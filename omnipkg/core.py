"""
omnipkg
An intelligent installer that lets pip run, then surgically cleans up downgrades
and isolates conflicting versions in deduplicated bubbles to guarantee a stable environment.
"""
import concurrent.futures
import hashlib
import importlib.metadata
import io
import json
import locale as sys_locale
import os
import packaging.version
import pickle
import textwrap
import platform
import re
import shutil
import site
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple, Any
import filelock
import redis
import requests as http_requests
import zlib
from filelock import FileLock
from importlib.metadata import Distribution, version, metadata, PackageNotFoundError
from packaging.utils import canonicalize_name
from packaging.version import parse as parse_version, InvalidVersion
from packaging.specifiers import SpecifierSet
from .i18n import _, LANG_INFO, SUPPORTED_LANGUAGES
from .package_meta_builder import omnipkgMetadataGatherer
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
try:
    import magic
    HAS_MAGIC = True
except ImportError:
    magic = None
    HAS_MAGIC = False

def _get_core_dependencies() -> set:
    """
    Correctly reads omnipkg's own production dependencies and returns them as a set.
    """
    try:
        pkg_meta = metadata('omnipkg')
        reqs = pkg_meta.get_all('Requires-Dist') or []
        return {canonicalize_name(re.match('^[a-zA-Z0-9\\-_.]+', req).group(0)) for req in reqs if re.match('^[a-zA-Z0-9\\-_.]+', req)}
    except PackageNotFoundError:
        try:
            pyproject_path = Path(__file__).parent.parent / 'pyproject.toml'
            if pyproject_path.exists():
                with pyproject_path.open('rb') as f:
                    pyproject_data = tomllib.load(f)
                return pyproject_data['project'].get('dependencies', [])
        except Exception as e:
            print(_('⚠️ Could not parse pyproject.toml, falling back to empty list: {}').format(e))
            return []
    except Exception as e:
        print(_('⚠️ Could not determine core dependencies, falling back to empty list: {}').format(e))
        return []

class ConfigManager:
    """
    Manages loading and first-time creation of the omnipkg config file.
    Now includes Python interpreter hotswapping capabilities and is environment-aware.
    """

    def __init__(self):
        """
        Initializes the ConfigManager with a robust, fail-safe sequence
        that now AUTOMATICALLY ADOPTS the native interpreter on first run.
        """
        self._python_cache = {}
        self._preferred_version = (3, 11)
        self.venv_path = self._get_venv_root()

        # --- CORE FIX: Prioritize the override ENV VAR to inherit the correct ID ---
        # This is the key to ensuring the subprocess uses the parent's environment identity.
        env_id_override = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE')
        if env_id_override:
            self.env_id = env_id_override
        else:
            self.env_id = hashlib.md5(str(self.venv_path.resolve()).encode()).hexdigest()[:8]
        # --- END CORE FIX ---

        self.config_dir = Path.home() / '.config' / 'omnipkg'
        self.config_path = self.config_dir / 'config.json'

        is_nested_interpreter = '.omnipkg/interpreters' in str(Path(sys.executable).resolve())
        setup_complete_flag = self.venv_path / '.omnipkg' / '.setup_complete'

        if not setup_complete_flag.exists() and not is_nested_interpreter:
            print('\n' + '=' * 60)
            print(_('  🚀 OMNIPKG ONE-TIME ENVIRONMENT SETUP'))
            print('=' * 60)
            self.config = self._get_sensible_defaults()
            try:
                print(_('   - Step 1: Registering the native Python interpreter...'))
                native_version_str = f'{sys.version_info.major}.{sys.version_info.minor}'
                self._register_and_link_existing_interpreter(Path(sys.executable), native_version_str)
                if sys.version_info[:2] != self._preferred_version:
                    print(_('\n   - Step 2: Setting up the required Python 3.11 control plane...'))
                    temp_omnipkg = omnipkg(config_manager=self)
                    result_code = temp_omnipkg._fallback_to_download('3.11')
                    if result_code != 0:
                        raise RuntimeError('Failed to set up the Python 3.11 control plane.')
                setup_complete_flag.parent.mkdir(parents=True, exist_ok=True)
                setup_complete_flag.touch()
                print('\n' + '=' * 60)
                print(_('  ✅ SETUP COMPLETE'))
                print('=' * 60)
                print(_('Your environment is now fully managed by omnipkg.'))
                print('=' * 60)
            except Exception as e:
                print(_('❌ A critical error occurred during one-time setup: {}').format(e))
                import traceback
                traceback.print_exc()
                if setup_complete_flag.exists():
                    setup_complete_flag.unlink(missing_ok=True)
                sys.exit(1)
                
        self.config = self._load_or_create_env_config()
        if self.config:
            self.multiversion_base = Path(self.config.get('multiversion_base', ''))
        else:
            print(_('⚠️ CRITICAL Warning: Config failed to load, omnipkg may not function.'))
            self.multiversion_base = Path('')

    def _set_rebuild_flag_for_version(self, version_str: str):
        """
        Sets a flag indicating that a new interpreter needs its knowledge base built.
        This is a stateful, safe way to trigger a one-time setup.
        """
        flag_file = self.venv_path / '.omnipkg' / '.needs_kb_rebuild'
        lock_file = self.venv_path / '.omnipkg' / '.needs_kb_rebuild.lock'
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        
        with FileLock(lock_file):
            versions_to_rebuild = []
            if flag_file.exists():
                try:
                    with open(flag_file, 'r') as f:
                        versions_to_rebuild = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass # Overwrite corrupted file
            
            if version_str not in versions_to_rebuild:
                versions_to_rebuild.append(version_str)
            
            with open(flag_file, 'w') as f:
                json.dump(versions_to_rebuild, f)
        print(f"   🚩 Flag set: Python {version_str} will build its knowledge base on first use.")


    def _peek_config_for_flag(self, flag_name: str) -> bool:
        """
        Safely checks the config file for a boolean flag for the current environment
        without fully loading the ConfigManager. Returns False if file doesn't exist.
        """
        if not self.config_path.exists():
            return False
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
            return data.get('environments', {}).get(self.env_id, {}).get(flag_name, False)
        except (json.JSONDecodeError, IOError):
            return False

    def _get_venv_root(self) -> Path:
        """
        Reliably finds the root of the virtual environment.
        PRIORITY 1: Check for an override environment variable set during relaunch.
        PRIORITY 2: Search upwards for the pyvenv.cfg file.
        """
        venv_root_override = os.environ.get('OMNIPKG_VENV_ROOT')
        if venv_root_override:
            return Path(venv_root_override)
        current_dir = Path(sys.executable).resolve().parent
        while current_dir != current_dir.parent:
            if (current_dir / 'pyvenv.cfg').exists():
                return current_dir
            current_dir = current_dir.parent
        return Path(sys.prefix)

    def _reset_setup_flag_on_disk(self):
        """Directly modifies the config file on disk to reset the setup flag."""
        try:
            full_config = {'environments': {}}
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    full_config = json.load(f)
            if self.env_id in full_config.get('environments', {}):
                full_config['environments'][self.env_id].pop('managed_python_setup_complete', None)
            with open(self.config_path, 'w') as f:
                json.dump(full_config, f, indent=4)
        except (IOError, json.JSONDecodeError) as e:
            print(_('   ⚠️  Could not reset setup flag in config file: {}').format(e))

    def _trigger_hotswap_relaunch(self):
        """
        Handles the user interaction and download process for an environment that needs to be upgraded.
        This function is self-contained and does not depend on self.config. It ends with an execv call.
        """
        print('\n' + '=' * 60)
        print(_('  🚀 Environment Hotswap to a Managed Python 3.11'))
        print('=' * 60)
        print(f'omnipkg works best with Python 3.11. Your version is {sys.version_info.major}.{sys.version_info.minor}.')
        print(_("\nTo ensure everything 'just works', omnipkg will now perform a one-time setup:"))
        print(_('  1. Download a self-contained Python 3.11 into your virtual environment.'))
        print('  2. Relaunch seamlessly to continue your command.')
        try:
            choice = input('\nDo you want to proceed with the automatic setup? (y/n): ')
            if choice.lower() == 'y':
                self._install_python311_in_venv()
            else:
                print('🛑 Setup cancelled. Aborting, as a managed Python 3.11 is required.')
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            print(_('\n🛑 Operation cancelled. Aborting.'))
            sys.exit(1)

    def _has_suitable_python311(self) -> bool:
        """
        Comprehensive check for existing suitable Python 3.11 installations.
        Returns True if we already have a usable Python 3.11 setup.
        """
        if sys.version_info[:2] == (3, 11) and sys.executable.startswith(str(self.venv_path)):
            return True
        registry_path = self.venv_path / '.omnipkg' / 'interpreters' / 'registry.json'
        if registry_path.exists():
            try:
                with open(registry_path, 'r') as f:
                    registry = json.load(f)
                python_311_path = registry.get('interpreters', {}).get('3.11')
                if python_311_path and Path(python_311_path).exists():
                    try:
                        result = subprocess.run([python_311_path, '-c', "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], capture_output=True, text=True, timeout=5)
                        if result.returncode == 0 and result.stdout.strip() == '3.11':
                            return True
                    except:
                        pass
            except:
                pass
        expected_exe_path = self._get_interpreter_dest_path(self.venv_path) / ('python.exe' if platform.system() == 'Windows' else 'bin/python3.11')
        if expected_exe_path.exists():
            try:
                result = subprocess.run([str(expected_exe_path), '--version'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and 'Python 3.11' in result.stdout:
                    return True
            except:
                pass
        bin_dir = self.venv_path / ('Scripts' if platform.system() == 'Windows' else 'bin')
        if bin_dir.exists():
            for possible_name in ['python3.11', 'python']:
                exe_path = bin_dir / (f'{possible_name}.exe' if platform.system() == 'Windows' else possible_name)
                if exe_path.exists():
                    try:
                        result = subprocess.run([str(exe_path), '-c', "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], capture_output=True, text=True, timeout=5)
                        if result.returncode == 0 and result.stdout.strip() == '3.11':
                            return True
                    except:
                        pass
        return False

    def _get_paths_for_interpreter(self, python_exe_path: str) -> Optional[Dict[str, str]]:
        """
            Runs an interpreter in a subprocess to ask for its version and calculates
            its site-packages path. This is the only reliable way to get paths for an
            interpreter that isn't the currently running one.
            """
        try:
            cmd = [python_exe_path, '-c', "import sys; import json; print(json.dumps({'version': f'{sys.version_info.major}.{sys.version_info.minor}', 'prefix': sys.prefix}))"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            interp_info = json.loads(result.stdout)
            version = interp_info['version']
            prefix = Path(interp_info['prefix'])
            site_packages = prefix / 'lib' / f'python{version}' / 'site-packages'
            return {'site_packages_path': str(site_packages), 'multiversion_base': str(site_packages / '.omnipkg_versions'), 'python_executable': python_exe_path}
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f'⚠️  Could not determine paths for interpreter {python_exe_path}: {e}')
            return None

    def _align_config_to_interpreter(self, python_exe_path_str: str):
        """
        Updates and saves config paths to match the specified Python executable
        by running it as a subprocess to get its true paths.
        """
        print(_('🔧 Aligning configuration to use Python interpreter: {}').format(python_exe_path_str))
        correct_paths = self._get_paths_for_interpreter(python_exe_path_str)
        if not correct_paths:
            print(f'❌ CRITICAL: Failed to determine paths for {python_exe_path_str}. Configuration not updated.')
            return
        print(_('   - New site-packages path: {}').format(correct_paths['site_packages_path']))
        print(_('   - New Python executable: {}').format(correct_paths['python_executable']))
        self.set('python_executable', correct_paths['python_executable'])
        self.set('site_packages_path', correct_paths['site_packages_path'])
        self.set('multiversion_base', correct_paths['multiversion_base'])
        self.config.update(correct_paths)
        self.multiversion_base = Path(self.config['multiversion_base'])
        print(_('   ✅ Configuration updated and saved successfully.'))

    def _get_venv_root(self) -> Path:
        """
        Reliably finds the root of the virtual environment.
        PRIORITY 1: Check for an override environment variable set during relaunch.
        PRIORITY 2: Search upwards for the pyvenv.cfg file.
        """
        venv_root_override = os.environ.get('OMNIPKG_VENV_ROOT')
        if venv_root_override:
            return Path(venv_root_override)
        current_dir = Path(sys.executable).resolve().parent
        while current_dir != current_dir.parent:
            if (current_dir / 'pyvenv.cfg').exists():
                return current_dir
            current_dir = current_dir.parent
        return Path(sys.prefix)

    def _setup_native_311_environment(self):
        """
        Performs the one-time setup for an environment that already has Python 3.11.
        This primarily involves symlinking and registering the interpreter.
        This function runs AFTER self.config is loaded.
        """
        print('\n' + '=' * 60)
        print('  🚀 Finalizing Environment Setup for Python 3.11')
        print('=' * 60)
        print(_('✅ Detected a suitable Python 3.11 within your virtual environment.'))
        print('   - Registering it with omnipkg for future operations...')
        self._register_and_link_existing_interpreter(Path(sys.executable), f'{sys.version_info.major}.{sys.version_info.minor}')
        registered_311_path = self.get_interpreter_for_version('3.11')
        if registered_311_path:
            self._align_config_to_interpreter(str(registered_311_path))
        else:
            print(_('⚠️ Warning: Could not find registered Python 3.11 path after setup. Config may be incorrect.'))
        self.set('managed_python_setup_complete', True)
        print(_('\n✅ Environment setup is complete!'))

    def _load_path_registry(self):
        """Load path registry (placeholder for your path management)."""
        pass

    def _ensure_proper_registration(self):
        """
        Ensures the current Python 3.11 is properly registered even if already detected.
        """
        if sys.version_info[:2] == (3, 11):
            current_path = Path(sys.executable).resolve()
            registry_path = self.venv_path / '.omnipkg' / 'interpreters' / 'registry.json'
            needs_registration = True
            if registry_path.exists():
                try:
                    with open(registry_path, 'r') as f:
                        registry = json.load(f)
                    registered_311 = registry.get('interpreters', {}).get('3.11')
                    if registered_311 and Path(registered_311).resolve() == current_path:
                        needs_registration = False
                except:
                    pass
            if needs_registration:
                print(_('   - Registering current Python 3.11...'))
                self._register_all_interpreters(self.venv_path)

    def _register_and_link_existing_interpreter(self, interpreter_path: Path, version: str):
        """
        "Adopts" the native venv interpreter by creating a symlink to it inside
        the managed .omnipkg/interpreters directory. It then ensures the registry
        points to this new, centralized symlink.
        """
        print(_('   - Centralizing native Python {} via symlink...').format(version))
        managed_interpreters_dir = self.venv_path / '.omnipkg' / 'interpreters'
        managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
        symlink_dir_name = f'cpython-{version}-venv-native'
        symlink_path = managed_interpreters_dir / symlink_dir_name
        target_for_symlink = interpreter_path.parent.parent
        if not symlink_path.exists():
            symlink_path.symlink_to(target_for_symlink, target_is_directory=True)
            print(_('   - ✅ Created symlink: {} -> {}').format(symlink_path, target_for_symlink))
        elif not (symlink_path.is_symlink() and os.readlink(str(symlink_path)) == str(target_for_symlink)):
            print(_('   - ⚠️  Correcting invalid symlink at {}...').format(symlink_path.name))
            if symlink_path.is_dir():
                shutil.rmtree(symlink_path)
            else:
                symlink_path.unlink()
            symlink_path.symlink_to(target_for_symlink, target_is_directory=True)
        else:
            print(_('   - ✅ Symlink already exists and is correct.'))
        self._register_all_interpreters(self.venv_path)

    def install_python311_in_venv(self):
        print(_('\n🚀 Upgrading environment to Python 3.11...'))
        venv_path = Path(sys.prefix)
        if venv_path == Path(sys.base_prefix):
            print(_('❌ Error: You must be in a virtual environment to use this feature.'))
            sys.exit(1)
        system = platform.system().lower()
        arch = platform.machine().lower()
        try:
            python311_exe = None
            try:
                python311_exe = self._install_managed_python(venv_path, '3.11.6')
            except (AttributeError, Exception) as e:
                print(_('Note: Falling back to platform-specific installation ({})').format(e))
                if system == 'linux':
                    python311_exe = self._install_python_platform(venv_path, arch, 'linux')
                elif system == 'darwin':
                    python311_exe = self._install_python_platform(venv_path, arch, 'macos')
                elif system == 'windows':
                    python311_exe = self._install_python_platform(venv_path, arch, 'windows')
                else:
                    raise OSError(_('Unsupported operating system: {}').format(system))
            if python311_exe and python311_exe.exists():
                self._update_venv_pyvenv_cfg(venv_path, python311_exe)
                print(_('✅ Python 3.11 downloaded and configured.'))
                self._finalize_environment_upgrade(venv_path, python311_exe)
                print(_('\n✅ Success! The environment is now fully upgraded to Python 3.11.'))
                print(' Your current command will now continue on the new version.')
                print('\n IMPORTANT: For the change to stick in your terminal for future commands, please run:')
                activate_script = venv_path / ('Scripts' if system == 'windows' else 'bin') / 'activate'
                print(_(' source "{}"').format(activate_script))
                print(_(' ...after this one finishes.'))
                args = [str(python311_exe), '-m', 'omnipkg.cli'] + sys.argv[1:]
                os.execv(str(python311_exe), args)
            else:
                raise Exception('Python 3.11 executable path was not determined after installation.')
        except Exception as e:
            print(_('❌ Failed to auto-upgrade to Python 3.11: {}').format(e))
            sys.exit(1)

    def _register_all_interpreters(self, venv_path: Path):
        """
        FIXED: Discovers and registers ONLY the Python interpreters that are explicitly
        managed within the .omnipkg/interpreters directory. This is the single
        source of truth for what is "swappable".
        """
        print(_('🔧 Registering all managed Python interpreters...'))
        managed_interpreters_dir = venv_path / '.omnipkg' / 'interpreters'
        managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
        registry_path = managed_interpreters_dir / 'registry.json'
        interpreters = {}
        if not managed_interpreters_dir.is_dir():
            print(_('   ⚠️  Managed interpreters directory not found.'))
            return
        for interp_dir in managed_interpreters_dir.iterdir():
            if not (interp_dir.is_dir() or interp_dir.is_symlink()):
                continue
            print(_('   -> Scanning directory: {}').format(interp_dir.name))
            found_exe_path = None
            search_locations = [interp_dir / 'bin', interp_dir / 'Scripts', interp_dir]
            possible_exe_names = ['python3.12', 'python3.11', 'python3.10', 'python3.9', 'python3', 'python', 'python.exe']
            for location in search_locations:
                if location.is_dir():
                    for exe_name in possible_exe_names:
                        exe_path = location / exe_name
                        if exe_path.is_file() and os.access(exe_path, os.X_OK):
                            version_tuple = self._verify_python_version(str(exe_path))
                            if version_tuple:
                                found_exe_path = exe_path
                                print(_('      ✅ Found valid executable: {}').format(found_exe_path))
                                break
                if found_exe_path:
                    break
            if found_exe_path:
                version_tuple = self._verify_python_version(str(found_exe_path))
                if version_tuple:
                    version_str = f'{version_tuple[0]}.{version_tuple[1]}'
                    interpreters[version_str] = str(found_exe_path.resolve())
        primary_version = '3.11' if '3.11' in interpreters else sorted(interpreters.keys(), reverse=True)[0] if interpreters else None
        registry_data = {'primary_version': primary_version, 'interpreters': {k: v for k, v in interpreters.items()}, 'last_updated': datetime.now().isoformat()}
        with open(registry_path, 'w') as f:
            json.dump(registry_data, f, indent=4)
        if interpreters:
            print(_('   ✅ Registered {} managed Python interpreters.').format(len(interpreters)))
            for version, path in sorted(interpreters.items()):
                print(_('      - Python {}: {}').format(version, path))
        else:
            print(_('   ⚠️  No managed Python interpreters were found or could be registered.'))

    def _find_existing_python311(self) -> Optional[Path]:
        """Checks if a managed Python 3.11 interpreter already exists."""
        venv_path = Path(sys.prefix)
        expected_exe_path = self._get_interpreter_dest_path(venv_path) / ('python.exe' if platform.system() == 'windows' else 'bin/python3.11')
        if expected_exe_path.exists() and expected_exe_path.is_file():
            print(_('✅ Found existing Python 3.11 interpreter.'))
            return expected_exe_path
        return None

    def get_interpreter_for_version(self, version: str) -> Optional[Path]:
        """Get the path to a specific Python interpreter version."""
        registry_path = Path(sys.prefix) / '.omnipkg' / 'interpreters' / 'registry.json'
        if not registry_path.exists():
            return None
        try:
            with open(registry_path, 'r') as f:
                registry = json.load(f)
            interpreter_path = registry.get('interpreters', {}).get(version)
            if interpreter_path and Path(interpreter_path).exists():
                return Path(interpreter_path)
        except:
            pass
        return None

    def _find_project_root(self):
        """
        Find the project root directory by looking for setup.py, pyproject.toml, or .git
        """
        import os
        from pathlib import Path
        current_dir = Path.cwd()
        module_dir = Path(__file__).parent.parent
        search_paths = [current_dir, module_dir]
        for start_path in search_paths:
            for path in [start_path] + list(start_path.parents):
                project_files = ['setup.py', 'pyproject.toml', 'setup.cfg', '.git', 'omnipkg.egg-info']
                for project_file in project_files:
                    if (path / project_file).exists():
                        print(_('     (Found project root: {})').format(path))
                        return path
        print(_('     (No project root found)'))
        return None

    def _install_essential_packages(self, python_exe: Path):
        """
        Installs essential packages for a new interpreter using a robust hybrid strategy.
        It installs dependencies first using the new interpreter's pip, then installs
        omnipkg itself without its dependencies to avoid resolver conflicts.
        """
        print('📦 Bootstrapping essential packages for new interpreter...')

        def run_verbose(cmd: List[str], error_msg: str):
            """Helper to run a command and show its output."""
            print(_('   🔩 Running: {}').format(' '.join(cmd)))
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
            except subprocess.CalledProcessError as e:
                print(_('   ❌ {}').format(error_msg))
                print('   --- Stderr ---'); print(e.stderr); print('   ----------------')
                raise

        try:
            print(_('   - Bootstrapping pip, setuptools, wheel...'))
            with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w', encoding='utf-8') as tmp_file:
                script_path = tmp_file.name
                with urllib.request.urlopen('https://bootstrap.pypa.io/get-pip.py') as response:
                    tmp_file.write(response.read().decode('utf-8'))
            pip_cmd = [str(python_exe), script_path, '--no-cache-dir', 'pip', 'setuptools', 'wheel']
            run_verbose(pip_cmd, 'Failed to bootstrap pip.')
            os.unlink(script_path)
            print(_('   ✅ Pip bootstrap complete.'))

            # --- HYBRID STRATEGY ---
            # Step 1: Install all of omnipkg's dependencies using the new interpreter's pip.
            # This allows pip to correctly resolve version-specific dependencies like 'tomli'.
            core_deps = _get_core_dependencies()
            if core_deps:
                print(_('   - Installing omnipkg core dependencies...'))
                # Let pip handle the markers correctly for the target python version
                deps_install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir'] + sorted(list(core_deps))
                run_verbose(deps_install_cmd, 'Failed to install omnipkg dependencies.')
                print(_('   ✅ Core dependencies installed.'))

            # Step 2: Now install omnipkg itself, but tell pip to ignore dependencies
            # since we just installed them. This avoids the complex resolver hell.
            print(_('   - Installing omnipkg application layer...'))
            project_root = self._find_project_root()
            if project_root:
                print(_('     (Developer mode detected: performing editable install)'))
                install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', '-e', str(project_root)]
            else:
                print('     (Standard mode detected: installing from PyPI)')
                install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', 'omnipkg']
            run_verbose(install_cmd, 'Failed to install omnipkg application.')
            print(_('   ✅ Omnipkg bootstrapped successfully!'))

        except Exception as e:
            print(_('❌ A critical error occurred during the bootstrap process: {}').format(e))
            raise

    def _create_omnipkg_executable(self, new_python_exe: Path, venv_path: Path):
        """
        Creates a proper shell script executable that forces the use of the new Python interpreter.
        """
        print(_('🔧 Creating new omnipkg executable...'))
        bin_dir = venv_path / ('Scripts' if platform.system() == 'Windows' else 'bin')
        omnipkg_exec_path = bin_dir / 'omnipkg'
        system = platform.system().lower()
        if system == 'windows':
            script_content = f'@echo off\nREM This script was auto-generated by omnipkg to ensure the correct Python is used.\n"{new_python_exe.resolve()}" -m omnipkg.cli %*\n'
            omnipkg_exec_path = bin_dir / 'omnipkg.bat'
        else:
            script_content = f'#!/bin/bash\n# This script was auto-generated by omnipkg to ensure the correct Python is used.\n\nexec "{new_python_exe.resolve()}" -m omnipkg.cli "$@"\n'
        with open(omnipkg_exec_path, 'w') as f:
            f.write(script_content)
        if system != 'windows':
            omnipkg_exec_path.chmod(493)
        print(_('   ✅ New omnipkg executable created.'))

    def _update_default_python_links(self, venv_path: Path, new_python_exe: Path):
        """Updates the default python/python3 symlinks to point to Python 3.11."""
        print(_('🔧 Updating default Python links...'))
        bin_dir = venv_path / ('Scripts' if platform.system() == 'Windows' else 'bin')
        if platform.system() == 'Windows':
            for name in ['python.exe', 'python3.exe']:
                target = bin_dir / name
                if target.exists():
                    target.unlink()
                shutil.copy2(new_python_exe, target)
        else:
            for name in ['python', 'python3']:
                target = bin_dir / name
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.symlink_to(new_python_exe)
        version_tuple = self._verify_python_version(str(new_python_exe))
        version_str = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else 'the new version'
        print(_('   ✅ Default Python links updated to use Python {}.').format(version_str))

    def _auto_register_original_python(self, venv_path: Path) -> None:
        """
        Automatically detects and registers the original Python interpreter that was
        used to create this environment, without moving or copying it.
        """
        print(_('🔍 Auto-detecting original Python interpreter...'))
        current_exe = Path(sys.executable).resolve()
        current_version = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
        major_minor = f'{sys.version_info.major}.{sys.version_info.minor}'
        print(_('   - Detected: Python {} at {}').format(current_version, current_exe))
        interpreters_dir = venv_path / '.omnipkg' / 'interpreters'
        registry_path = venv_path / '.omnipkg' / 'python_registry.json'
        registry = {}
        if registry_path.exists():
            try:
                with open(registry_path, 'r') as f:
                    registry = json.load(f)
            except Exception as e:
                print(f'   ⚠️  Warning: Could not load registry: {e}')
                registry = {}
        if major_minor in registry:
            print(_('   ✅ Python {} already registered at: {}').format(major_minor, registry[major_minor]['path']))
            return
        managed_name = f'original-{current_version}'
        managed_dir = interpreters_dir / managed_name
        managed_dir.mkdir(parents=True, exist_ok=True)
        bin_dir = managed_dir / 'bin'
        bin_dir.mkdir(exist_ok=True)
        original_links = [('python', current_exe), (f'python{sys.version_info.major}', current_exe), (f'python{major_minor}', current_exe)]
        print(_('   📝 Registering Python {} (original) without copying...').format(major_minor))
        for link_name, target in original_links:
            link_path = bin_dir / link_name
            if link_path.exists():
                link_path.unlink()
            try:
                link_path.symlink_to(target)
                print(_('      ✅ Created symlink: {} -> {}').format(link_name, target))
            except Exception as e:
                print(_('      ⚠️  Could not create symlink {}: {}').format(link_name, e))
        pip_candidates = [current_exe.parent / 'pip', current_exe.parent / f'pip{sys.version_info.major}', current_exe.parent / f'pip{major_minor}']
        for pip_path in pip_candidates:
            if pip_path.exists():
                pip_link = bin_dir / pip_path.name
                if not pip_link.exists():
                    try:
                        pip_link.symlink_to(pip_path)
                        print(_('      ✅ Created pip symlink: {}').format(pip_path.name))
                        break
                    except Exception as e:
                        print(_('      ⚠️  Could not create pip symlink: {}').format(e))
        registry[major_minor] = {'path': str(bin_dir / f'python{major_minor}'), 'version': current_version, 'type': 'original', 'source': str(current_exe), 'managed_dir': str(managed_dir), 'registered_at': datetime.now().isoformat()}
        try:
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            with open(registry_path, 'w') as f:
                json.dump(registry, f, indent=2)
            print(_('   ✅ Registered Python {} in registry').format(major_minor))
        except Exception as e:
            print(f'   ❌ Failed to save registry: {e}')
            return
        if hasattr(self, 'config') and self.config:
            managed_interpreters = self.config.get('managed_interpreters', {})
            managed_interpreters[major_minor] = str(bin_dir / f'python{major_minor}')
            self.set('managed_interpreters', managed_interpreters)
            print(f'   ✅ Updated main config with Python {major_minor}')

    def _should_auto_register_python(self, version: str) -> bool:
        """
        Determines if we should auto-register the original Python instead of downloading.
        """
        major_minor = '.'.join(version.split('.')[:2])
        current_major_minor = f'{sys.version_info.major}.{sys.version_info.minor}'
        return major_minor == current_major_minor

    def _enhanced_python_adopt(self, version: str) -> int:
        """
        Enhanced adoption logic that prioritizes registering the original interpreter
        when appropriate, falling back to download only when necessary.
        """
        print(_('🐍 Attempting to adopt Python {} into the environment...').format(version))
        if self._should_auto_register_python(version):
            print(_('   🎯 Requested version matches current Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
            print(_('   📋 Auto-registering current interpreter instead of downloading...'))
            try:
                self._auto_register_original_python(self.venv_path)
                print(_('🎉 Successfully registered Python {} (original interpreter)!').format(version))
                print(_("   You can now use 'omnipkg swap python {}'").format(version))
                return 0
            except Exception as e:
                print(_('   ❌ Auto-registration failed: {}').format(e))
                print(_('   🔄 Falling back to download strategy...'))
        return self._existing_adopt_logic(version)

    def _register_all_managed_interpreters(self) -> None:
        """
        Enhanced version that includes original interpreters in the scan.
        """
        print(_('🔧 Registering all managed Python interpreters...'))
        interpreters_dir = self.venv_path / '.omnipkg' / 'interpreters'
        if not interpreters_dir.exists():
            print(_('   ℹ️  No interpreters directory found.'))
            return
        registry_path = self.venv_path / '.omnipkg' / 'python_registry.json'
        registry = {}
        if registry_path.exists():
            try:
                with open(registry_path, 'r') as f:
                    registry = json.load(f)
            except Exception:
                registry = {}
        managed_interpreters = {}
        for interpreter_dir in interpreters_dir.iterdir():
            if not interpreter_dir.is_dir():
                continue
            print(_('   -> Scanning directory: {}').format(interpreter_dir.name))
            bin_dir = interpreter_dir / 'bin'
            if not bin_dir.exists():
                print(_('      ⚠️  No bin/ directory found in {}').format(interpreter_dir.name))
                continue
            python_exe = None
            for candidate in bin_dir.glob('python[0-9].[0-9]*'):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    python_exe = candidate
                    break
            if not python_exe:
                print(_('      ⚠️  No valid Python executable found in {}').format(interpreter_dir.name))
                continue
            try:
                result = subprocess.run([str(python_exe), '--version'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    version_match = re.search('Python (\\d+\\.\\d+)', result.stdout)
                    if version_match:
                        major_minor = version_match.group(1)
                        managed_interpreters[major_minor] = str(python_exe)
                        if major_minor not in registry:
                            registry[major_minor] = {'path': str(python_exe), 'type': 'downloaded' if 'cpython-' in interpreter_dir.name else 'original', 'managed_dir': str(interpreter_dir), 'registered_at': datetime.now().isoformat()}
                        interpreter_type = registry[major_minor].get('type', 'unknown')
                        print(_('      ✅ Found valid executable: {} ({})').format(python_exe, interpreter_type))
                    else:
                        print(_('      ⚠️  Could not parse version from: {}').format(result.stdout.strip()))
                else:
                    print(_('      ⚠️  Failed to get version: {}').format(result.stderr.strip()))
            except Exception as e:
                print(_('      ⚠️  Error testing executable: {}').format(e))
        try:
            with open(registry_path, 'w') as f:
                json.dump(registry, f, indent=2)
        except Exception as e:
            print(f'   ⚠️  Could not save registry: {e}')
        if managed_interpreters:
            self.set('managed_interpreters', managed_interpreters)
            print(_('   ✅ Registered {} managed Python interpreters.').format(len(managed_interpreters)))
            for version, path in managed_interpreters.items():
                interpreter_type = registry.get(version, {}).get('type', 'unknown')
                print(_('      - Python {}: {} ({})').format(version, path, interpreter_type))
        else:
            print(_('   ℹ️  No managed interpreters found.'))

    def _install_managed_python(self, venv_path: Path, full_version: str) -> Path:
        """
        Downloads and installs a specific, self-contained version of Python
        from the python-build-standalone project. Returns the path to the new executable.
        """
        print(_('\n🚀 Installing managed Python {}...').format(full_version))
        system = platform.system().lower()
        arch = platform.machine().lower()
        py_arch_map = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}
        py_arch = py_arch_map.get(arch)
        if not py_arch:
            raise OSError(_('Unsupported architecture: {}').format(arch))

        VERSION_TO_RELEASE_TAG_MAP = {'3.12.3': '20240415', '3.11.6': '20231002', '3.10.13': '20231002', '3.9.18': '20231002'}
        release_tag = VERSION_TO_RELEASE_TAG_MAP.get(full_version)
        if not release_tag:
            raise ValueError(f'No known standalone build for Python version {full_version}. Cannot download.')

        py_ver_plus_tag = f'{full_version}+{release_tag}'
        base_url = f'https://github.com/indygreg/python-build-standalone/releases/download/{release_tag}'
        archive_name_templates = {
            'linux': f'cpython-{py_ver_plus_tag}-{py_arch}-unknown-linux-gnu-install_only.tar.gz',
            'macos': f'cpython-{py_ver_plus_tag}-{py_arch}-apple-darwin-install_only.tar.gz',
            'windows': f'cpython-{py_ver_plus_tag}-{py_arch}-pc-windows-msvc-shared-install_only.tar.gz'
        }
        archive_name = archive_name_templates.get(system)
        if not archive_name:
            raise OSError(_('Unsupported operating system: {}').format(system))

        url = f'{base_url}/{archive_name}'
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / archive_name
            print(f'📥 Downloading Python {full_version} for {system.title()}...')
            print(_('   - URL: {}').format(url))
            try:
                urllib.request.urlretrieve(url, archive_path)
                if not archive_path.exists() or archive_path.stat().st_size < 1_000_000:
                    raise OSError(_('Failed to download or file is too small: {}').format(archive_path))
                print(_('✅ Downloaded {} bytes').format(archive_path.stat().st_size))

                with tarfile.open(archive_path, 'r:gz') as tar:
                    extract_path = Path(temp_dir) / 'extracted'
                    tar.extractall(extract_path)

                source_python_dir = extract_path / 'python'
                python_dest = venv_path / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'
                print(_('   - Installing to: {}').format(python_dest))
                shutil.copytree(source_python_dir, python_dest, dirs_exist_ok=True)

                python_exe = python_dest / ('python.exe' if system == 'windows' else 'bin/python3')
                if not python_exe.exists():
                    python_exe = python_dest / ('bin/python')
                if not python_exe.exists():
                    raise OSError(_('Python executable not found in expected location'))

                if system != 'windows':
                    python_exe.chmod(0o755)
                    major_minor = '.'.join(full_version.split('.')[:2])
                    versioned_symlink = python_exe.parent / f'python{major_minor}'
                    if not versioned_symlink.exists():
                        versioned_symlink.symlink_to(python_exe.name)

                self._install_essential_packages(python_exe)
                
                print(_('\n✨ New interpreter bootstrapped.'))
                
                try:
                    print('🔧 Forcing rescan to register the new interpreter...')
                    self._register_all_interpreters(self.venv_path)
                    print('   ✅ New interpreter registered successfully.')
                            
                except Exception as e:
                    print(_('   ⚠️  Interpreter registration failed: {}').format(e))
                    import traceback
                    traceback.print_exc()

                major_minor_version = '.'.join(full_version.split('.')[:2])
                self._set_rebuild_flag_for_version(major_minor_version)

                return python_exe
            except Exception as e:
                raise OSError(_('Failed to download or extract Python: {}').format(e))

    def _find_python_interpreters(self) -> Dict[Tuple[int, int], str]:
        """
        Discovers all available Python interpreters on the system.
        Returns a dict mapping (major, minor) version tuples to executable paths.
        """
        if self._python_cache:
            return self._python_cache
        interpreters = {}
        search_patterns = ['python{}.{}', 'python{}{}']
        search_paths = []
        if 'PATH' in os.environ:
            search_paths.extend(os.environ['PATH'].split(os.pathsep))
        common_paths = ['/usr/bin', '/usr/local/bin', '/opt/python*/bin', str(Path.home() / '.pyenv' / 'versions' / '*' / 'bin'), '/usr/local/opt/python@*/bin', 'C:\\Python*', 'C:\\Users\\*\\AppData\\Local\\Programs\\Python\\Python*']
        search_paths.extend(common_paths)
        current_python_dir = Path(sys.executable).parent
        search_paths.append(str(current_python_dir))
        for path_str in search_paths:
            try:
                if '*' in path_str:
                    from glob import glob
                    expanded_paths = glob(path_str)
                    for expanded_path in expanded_paths:
                        if Path(expanded_path).is_dir():
                            search_paths.append(expanded_path)
                    continue
                path = Path(path_str)
                if not path.exists() or not path.is_dir():
                    continue
                for major in range(3, 4):
                    for minor in range(6, 15):
                        for pattern in search_patterns:
                            exe_name = pattern.format(major, minor)
                            exe_path = path / exe_name
                            if platform.system() == 'Windows':
                                exe_path_win = path / f'{exe_name}.exe'
                                if exe_path_win.exists():
                                    exe_path = exe_path_win
                            if exe_path.exists() and exe_path.is_file():
                                version = self._verify_python_version(str(exe_path))
                                if version and version not in interpreters:
                                    interpreters[version] = str(exe_path)
                        for generic_name in ['python', 'python3']:
                            exe_path = path / generic_name
                            if platform.system() == 'Windows':
                                exe_path = path / f'{generic_name}.exe'
                            if exe_path.exists() and exe_path.is_file():
                                version = self._verify_python_version(str(exe_path))
                                if version and version not in interpreters:
                                    interpreters[version] = str(exe_path)
            except (OSError, PermissionError):
                continue
        current_version = sys.version_info[:2]
        interpreters[current_version] = sys.executable
        self._python_cache = interpreters
        return interpreters

    def find_true_venv_root(self) -> Path:
        """
        Helper to find the true venv root by looking for pyvenv.cfg,
        which is reliable across different Python interpreters within the same venv.
        """
        current_path = Path(sys.executable).resolve()
        while current_path != current_path.parent:
            if (current_path / 'pyvenv.cfg').exists():
                return current_path
        return Path(sys.prefix)

    def _verify_python_version(self, python_path: str) -> Optional[Tuple[int, int]]:
        """
        Verify that a Python executable works and get its version.
        Returns (major, minor) tuple or None if invalid.
        """
        try:
            result = subprocess.run([python_path, '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version_str = result.stdout.strip()
                major, minor = map(int, version_str.split('.'))
                return (major, minor)
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError, OSError):
            pass
        return None

    def get_best_python_for_version_range(self, min_version: Tuple[int, int]=None, max_version: Tuple[int, int]=None, preferred_version: Tuple[int, int]=None) -> Optional[str]:
        """Find the best Python interpreter for a given version range."""
        interpreters = self._find_python_interpreters()
        if not interpreters:
            return None
        candidates = {}
        for version, path in interpreters.items():
            if min_version and version < min_version:
                continue
            if max_version and version > max_version:
                continue
            candidates[version] = path
        if not candidates:
            return None
        if preferred_version and preferred_version in candidates:
            return candidates[preferred_version]
        if self._preferred_version in candidates:
            return candidates[self._preferred_version]
        best_version = max(candidates.keys())
        return candidates[best_version]

    def _get_bin_paths(self) -> List[str]:
        """Gets a list of standard binary paths to search for executables."""
        paths = set()
        paths.add(str(Path(sys.executable).parent))
        for path in ['/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']:
            if Path(path).exists():
                paths.add(path)
        return sorted(list(paths))

    def _get_system_lang_code(self):
        """Helper to get a valid system language code."""
        try:
            lang_code = sys_locale.getlocale()[0]
            if lang_code and '_' in lang_code:
                lang_code = lang_code.split('_')[0]
            return lang_code or 'en'
        except Exception:
            return 'en'

    def _get_sensible_defaults(self) -> Dict:
        """
        Generates sensible default configuration paths based STRICTLY on the
        currently active virtual environment to ensure safety and prevent permission errors.
        """
        print(_('💡 Grounding configuration in the current active environment...'))
        active_python_exe = sys.executable
        print(_('   ✅ Using: {} (Your active interpreter)').format(active_python_exe))
        calculated_paths = self._get_paths_for_interpreter(active_python_exe)
        if not calculated_paths:
            print(_('   ⚠️  Falling back to basic path detection within the current environment.'))
            site_packages = str(self._get_actual_current_site_packages())
            calculated_paths = {'site_packages_path': site_packages, 'multiversion_base': str(Path(site_packages) / '.omnipkg_versions'), 'python_executable': sys.executable}
        return {**calculated_paths, 'python_interpreters': self.list_available_pythons() or {}, 'preferred_python_version': f'{self._preferred_version[0]}.{self._preferred_version[1]}', 'builder_script_path': str(Path(__file__).parent / 'package_meta_builder.py'), 'redis_host': 'localhost', 'redis_port': 6379, 'redis_key_prefix': 'omnipkg:pkg:', 'install_strategy': 'stable-main', 'uv_executable': 'uv', 'paths_to_index': self._get_bin_paths(), 'language': self._get_system_lang_code(), 'enable_python_hotswap': True}

    def _get_actual_current_site_packages(self) -> Path:
        """
        Gets the ACTUAL site-packages directory for the currently running Python interpreter.
        This is more reliable than calculating it from sys.prefix when hotswapping is involved.
        """
        import site
        try:
            site_packages_list = site.getsitepackages()
            if site_packages_list:
                current_python_dir = Path(sys.executable).parent.parent
                for sp in site_packages_list:
                    sp_path = Path(sp)
                    try:
                        sp_path.relative_to(current_python_dir)
                        return sp_path
                    except ValueError:
                        continue
                return Path(site_packages_list[0])
        except:
            pass
        python_version = f'python{sys.version_info.major}.{sys.version_info.minor}'
        current_python_path = Path(sys.executable)
        if '.omnipkg/interpreters' in str(current_python_path):
            interpreter_root = current_python_path.parent.parent
            site_packages_path = interpreter_root / 'lib' / python_version / 'site-packages'
        else:
            venv_path = Path(sys.prefix)
            site_packages_path = venv_path / 'lib' / python_version / 'site-packages'
        return site_packages_path

    def list_available_pythons(self) -> Dict[str, str]:
        """
        List all available Python interpreters with their versions.
        FIXED: Prioritize actual interpreters over symlinks, show hotswapped paths correctly.
        """
        interpreters = self._find_python_interpreters()
        result = {}
        for (major, minor), path in sorted(interpreters.items()):
            version_key = f'{major}.{minor}'
            path_obj = Path(path)
            if version_key in result:
                existing_path = Path(result[version_key])
                current_is_hotswapped = '.omnipkg/interpreters' in str(path_obj)
                existing_is_hotswapped = '.omnipkg/interpreters' in str(existing_path)
                current_is_versioned = f'python{major}.{minor}' in path_obj.name
                existing_is_versioned = f'python{major}.{minor}' in existing_path.name
                if current_is_hotswapped and (not existing_is_hotswapped):
                    result[version_key] = str(path)
                elif existing_is_hotswapped and (not current_is_hotswapped):
                    continue
                elif current_is_versioned and (not existing_is_versioned):
                    result[version_key] = str(path)
                elif existing_is_versioned and (not current_is_versioned):
                    continue
                elif len(str(path)) > len(str(existing_path)):
                    result[version_key] = str(path)
            else:
                result[version_key] = str(path)
        return result

    def _first_time_setup(self, interactive=True) -> Dict:
        """Interactive setup for the first time the tool is run."""
        import os
        self.config_dir.mkdir(parents=True, exist_ok=True)
        defaults = self._get_sensible_defaults()
        final_config = defaults.copy()
        if interactive and (not os.environ.get('CI')):
            print(_("🌍 Welcome to omnipkg! Let's get you configured."))
            print('-' * 60)
            available_pythons = defaults['python_interpreters']
            if len(available_pythons) > 1:
                print(_('🐍 Discovered Python interpreters:'))
                for version, path in available_pythons.items():
                    marker = ' ⭐' if version == defaults['preferred_python_version'] else ''
                    print(_('   Python {}: {}{}').format(version, path, marker))
                print()
            print('Auto-detecting paths for your environment. Press Enter to accept defaults.\n')
            print(_('📦 Choose your default installation strategy:'))
            print(_('   1) stable-main:  Prioritize a stable main environment. (Recommended)'))
            print(_('   2) latest-active: Prioritize having the latest versions active.'))
            strategy = input(_('   Enter choice (1 or 2) [1]: ')).strip() or '1'
            final_config['install_strategy'] = 'stable-main' if strategy == '1' else 'latest-active'
            bubble_path = input(f"Path for version bubbles [{defaults['multiversion_base']}]: ").strip() or defaults['multiversion_base']
            final_config['multiversion_base'] = bubble_path
            python_path = input(_('Python executable path [{}]: ').format(defaults['python_executable'])).strip() or defaults['python_executable']
            final_config['python_executable'] = python_path
            while True:
                host_input = input(_('Redis host [{}]: ').format(defaults['redis_host'])) or defaults['redis_host']
                try:
                    import socket
                    socket.gethostbyname(host_input)
                    final_config['redis_host'] = host_input
                    break
                except socket.gaierror:
                    print(_("   ❌ Error: Invalid hostname '{}'. Please try again.").format(host_input))
            final_config['redis_port'] = int(input(_('Redis port [{}]: ').format(defaults['redis_port'])) or defaults['redis_port'])
            hotswap_choice = input(_('Enable Python interpreter hotswapping? (y/n) [y]: ')).strip().lower()
            final_config['enable_python_hotswap'] = hotswap_choice != 'n'
        try:
            with open(self.config_path, 'r') as f:
                full_config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            full_config = {'environments': {}}
        if 'environments' not in full_config:
            full_config['environments'] = {}
        full_config['environments'][self.env_id] = final_config
        with open(self.config_path, 'w') as f:
            json.dump(full_config, f, indent=4)
        if interactive and (not os.environ.get('CI')):
            print(_('\n✅ Configuration saved to {}.').format(self.config_path))
            print(_('   You can edit this file manually later.'))
            print(_('🧠 Initializing omnipkg knowledge base...'))
            print(_('   This may take a moment with large environments (like yours with {} packages).').format(len(defaults.get('installed_packages', []))))
            print(_('   💡 Future startups will be instant!'))
        rebuild_cmd = [str(final_config['python_executable']), '-m', 'omnipkg.cli', 'reset', '-y']
        try:
            if interactive and (not os.environ.get('CI')):
                process = subprocess.Popen(rebuild_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output and ('Processing' in output or 'Building' in output or 'Scanning' in output):
                        print(_('   {}').format(output.strip()))
                process.wait()
                if process.returncode != 0:
                    print(_('   ⚠️  Knowledge base initialization encountered issues but continuing...'))
            else:
                subprocess.run(rebuild_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            if interactive and (not os.environ.get('CI')):
                print(_('   ⚠️  Knowledge base will be built on first command usage instead.'))
            pass
        return final_config

    def _load_or_create_env_config(self) -> Dict:
        """
        Loads the config for the current environment from the global config file.
        If the environment is not registered, triggers the interactive first-time setup for it.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        full_config = {'environments': {}}
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    full_config = json.load(f)
                if 'environments' not in full_config:
                    full_config['environments'] = {}
            except json.JSONDecodeError:
                print(_('⚠️ Warning: Global config file is corrupted. Starting fresh.'))
        if self.env_id in full_config['environments']:
            return full_config['environments'][self.env_id]
        else:
            print(_('👋 New environment detected (ID: {}). Starting first-time setup.').format(self.env_id))
            return self._first_time_setup(interactive=True)

    def get(self, key, default=None):
        """Get a configuration value, with an optional default."""
        return self.config.get(key, default)

    def set(self, key, value):
        """Set a configuration value for the current environment and save."""
        self.config[key] = value
        try:
            with open(self.config_path, 'r') as f:
                full_config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            full_config = {'environments': {}}
        if 'environments' not in full_config:
            full_config['environments'] = {}
        full_config['environments'][self.env_id] = self.config
        with open(self.config_path, 'w') as f:
            json.dump(full_config, f, indent=4)

class InterpreterManager:
    """
    Manages multiple Python interpreters within the same environment.
    Provides methods to switch between interpreters and run commands with specific versions.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.venv_path = Path(sys.prefix)

    def list_available_interpreters(self) -> Dict[str, Path]:
        """Returns a dict of version -> path for all available interpreters."""
        registry_path = self.venv_path / '.omnipkg' / 'interpreters' / 'registry.json'
        if not registry_path.exists():
            return {}
        try:
            with open(registry_path, 'r') as f:
                registry = json.load(f)
            interpreters = {}
            for version, path_str in registry.get('interpreters', {}).items():
                path = Path(path_str)
                if path.exists():
                    interpreters[version] = path
            return interpreters
        except:
            return {}

    def run_with_interpreter(self, version: str, cmd: List[str]) -> subprocess.CompletedProcess:
        """Run a command with a specific Python interpreter version."""
        interpreter_path = self.config_manager.get_interpreter_for_version(version)
        if not interpreter_path:
            raise ValueError(_('Python {} interpreter not found').format(version))
        full_cmd = [str(interpreter_path)] + cmd
        return subprocess.run(full_cmd, capture_output=True, text=True)

    def install_package_with_version(self, package: str, python_version: str):
        """Install a package using a specific Python version."""
        interpreter_path = self.config_manager.get_interpreter_for_version(python_version)
        if not interpreter_path:
            raise ValueError(_('Python {} interpreter not found').format(python_version))
        cmd = [str(interpreter_path), '-m', 'pip', 'install', package]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f'Failed to install {package} with Python {python_version}: {result.stderr}')
        return result

class BubbleIsolationManager:

    def __init__(self, config: Dict, parent_omnipkg):
        self.config = config
        self.parent_omnipkg = parent_omnipkg
        self.site_packages = Path(config['site_packages_path'])
        self.multiversion_base = Path(config['multiversion_base'])
        self.file_hash_cache = {}
        self.package_path_registry = {}
        self.registry_lock = FileLock(self.multiversion_base / 'registry.lock')
        self._load_path_registry()
        self.http_session = http_requests.Session()

    def _load_path_registry(self):
        """Load the file path registry from JSON."""
        if not hasattr(self, 'multiversion_base'):
            return
        registry_file = self.multiversion_base / 'package_paths.json'
        if registry_file.exists():
            with self.registry_lock:
                try:
                    with open(registry_file, 'r') as f:
                        self.package_path_registry = json.load(f)
                except Exception:
                    print(_('    ⚠️ Warning: Failed to load path registry, starting fresh.'))
                    self.package_path_registry = {}

    def _save_path_registry(self):
        """Save the file path registry to JSON with atomic write."""
        registry_file = self.multiversion_base / 'package_paths.json'
        with self.registry_lock:
            temp_file = registry_file.with_suffix(f'{registry_file.suffix}.tmp')
            try:
                registry_file.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_file, 'w') as f:
                    json.dump(self.package_path_registry, f, indent=2)
                os.rename(temp_file, registry_file)
            finally:
                if temp_file.exists():
                    temp_file.unlink()

    def _register_file(self, file_path: Path, pkg_name: str, version: str, file_type: str, bubble_path: Path):
        """Register a file in the registry."""
        file_hash = self._get_file_hash(file_path)
        path_str = str(file_path)
        c_name = pkg_name.lower().replace('_', '-')
        if c_name not in self.package_path_registry:
            self.package_path_registry[c_name] = {}
        if version not in self.package_path_registry[c_name]:
            self.package_path_registry[c_name][version] = []
        self.package_path_registry[c_name][version].append({'path': path_str, 'hash': file_hash, 'type': file_type, 'bubble_path': str(bubble_path)})
        self._save_path_registry()

    def create_isolated_bubble(self, package_name: str, target_version: str) -> bool:
        print(_('🫧 Creating isolated bubble for {} v{}').format(package_name, target_version))
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            if not self._install_exact_version_tree(package_name, target_version, temp_path):
                return False
            installed_tree = self._analyze_installed_tree(temp_path)
            bubble_path = self.multiversion_base / f'{package_name}-{target_version}'
            if bubble_path.exists():
                shutil.rmtree(bubble_path)
            return self._create_deduplicated_bubble(installed_tree, bubble_path, temp_path)

    def _install_exact_version_tree(self, package_name: str, version: str, target_path: Path) -> bool:
        try:
            historical_deps = self._get_historical_dependencies(package_name, version)
            install_specs = ['{}=={}'.format(package_name, version)] + historical_deps
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--target', str(target_path)] + install_specs
            print(_('    📦 Installing full dependency tree to temporary location...'))
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(_('    ❌ Failed to install exact version tree: {}').format(result.stderr))
                return False
            return True
        except Exception as e:
            print(_('    ❌ Unexpected error during installation: {}').format(e))
            return False

    def _get_historical_dependencies(self, package_name: str, version: str) -> List[str]:
        print(_('    -> Trying strategy 1: pip dry-run...'))
        deps = self._try_pip_dry_run(package_name, version)
        if deps is not None:
            print(_('    ✅ Success: Dependencies resolved via pip dry-run.'))
            return deps
        print(_('    -> Trying strategy 2: PyPI API...'))
        deps = self._try_pypi_api(package_name, version)
        if deps is not None:
            print(_('    ✅ Success: Dependencies resolved via PyPI API.'))
            return deps
        print(_('    -> Trying strategy 3: pip show fallback...'))
        deps = self._try_pip_show_fallback(package_name, version)
        if deps is not None:
            print(_('    ✅ Success: Dependencies resolved from existing installation.'))
            return deps
        print(_('    ⚠️ All dependency resolution strategies failed for {}=={}.').format(package_name, version))
        print(_('    ℹ️  Proceeding with full temporary installation to build bubble.'))
        return []

    def _try_pip_dry_run(self, package_name: str, version: str) -> Optional[List[str]]:
        req_file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(_('{}=={}\n').format(package_name, version))
                req_file = f.name
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--dry-run', '--report', '-', '-r', req_file]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return None
            if not result.stdout or not result.stdout.strip():
                return None
            stdout_stripped = result.stdout.strip()
            if not (stdout_stripped.startswith('{') or stdout_stripped.startswith('[')):
                return None
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
            if not isinstance(report, dict) or 'install' not in report:
                return None
            deps = []
            for item in report.get('install', []):
                try:
                    if not isinstance(item, dict) or 'metadata' not in item:
                        continue
                    metadata = item['metadata']
                    item_name = metadata.get('name')
                    item_version = metadata.get('version')
                    if item_name and item_version and (item_name.lower() != package_name.lower()):
                        deps.append('{}=={}'.format(item_name, item_version))
                except Exception:
                    continue
            return deps
        except Exception:
            return None
        finally:
            if req_file and Path(req_file).exists():
                try:
                    Path(req_file).unlink()
                except Exception:
                    pass

    def _try_pypi_api(self, package_name: str, version: str) -> Optional[List[str]]:
        try:
            import requests
        except ImportError:
            print(_("    ⚠️  'requests' package not found. Skipping PyPI API strategy."))
            return None
        try:
            clean_version = version.split('+')[0]
            url = f'https://pypi.org/pypi/{package_name}/{clean_version}/json'
            headers = {'User-Agent': 'omnipkg-package-manager/1.0', 'Accept': 'application/json'}
            response = requests.get(url, timeout=10, headers=headers)
            if response.status_code == 404:
                if clean_version != version:
                    url = f'https://pypi.org/pypi/{package_name}/{version}/json'
                    response = requests.get(url, timeout=10, headers=headers)
            if response.status_code != 200:
                return None
            if not response.text.strip():
                return None
            try:
                pkg_data = response.json()
            except json.JSONDecodeError:
                return None
            if not isinstance(pkg_data, dict):
                return None
            requires_dist = pkg_data.get('info', {}).get('requires_dist')
            if not requires_dist:
                return []
            dependencies = []
            for req in requires_dist:
                if not req or not isinstance(req, str):
                    continue
                if ';' in req:
                    continue
                req = req.strip()
                match = re.match('^([a-zA-Z0-9\\-_.]+)([<>=!]+.*)?', req)
                if match:
                    dep_name = match.group(1)
                    version_spec = match.group(2) or ''
                    dependencies.append(_('{}{}').format(dep_name, version_spec))
            return dependencies
        except requests.exceptions.RequestException:
            return None
        except Exception:
            return None

    def _try_pip_show_fallback(self, package_name: str, version: str) -> Optional[List[str]]:
        try:
            cmd = [self.config['python_executable'], '-m', 'pip', 'show', package_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return None
            for line in result.stdout.split('\n'):
                if line.startswith('Requires:'):
                    requires = line.replace('Requires:', '').strip()
                    if requires and requires != '':
                        deps = [dep.strip() for dep in requires.split(',')]
                        return [dep for dep in deps if dep]
                    else:
                        return []
            return []
        except Exception:
            return None

    def _classify_package_type(self, files: List[Path]) -> str:
        has_python = any((f.suffix in ['.py', '.pyc'] for f in files))
        has_native = any((f.suffix in ['.so', '.pyd', '.dll'] for f in files))
        if has_native and has_python:
            return 'mixed'
        elif has_native:
            return 'native'
        else:
            return 'pure_python'

    def _find_existing_c_extension(self, file_hash: str) -> Optional[str]:
        """Disabled: C extensions are copied, not symlinked."""
        return None

    def _analyze_installed_tree(self, temp_path: Path) -> Dict[str, Dict]:
        """
        Analyzes the temporary installation, now EXPLICITLY finding executables
        and summarizing file registry warnings instead of printing each one.
        """
        installed = {}
        unregistered_file_count = 0
        for dist_info in temp_path.glob('*.dist-info'):
            try:
                dist = importlib.metadata.Distribution.at(dist_info)
                if not dist:
                    continue
                pkg_files = []
                if dist.files:
                    for file_entry in dist.files:
                        if file_entry.parts and file_entry.parts[0] == 'bin':
                            continue
                        abs_path = Path(dist_info.parent) / file_entry
                        if abs_path.exists():
                            pkg_files.append(abs_path)
                executables = []
                entry_points = dist.entry_points
                console_scripts = [ep for ep in entry_points if ep.group == 'console_scripts']
                if console_scripts:
                    temp_bin_path = temp_path / 'bin'
                    if temp_bin_path.is_dir():
                        for script in console_scripts:
                            exe_path = temp_bin_path / script.name
                            if exe_path.is_file():
                                executables.append(exe_path)
                pkg_name = dist.metadata['Name'].lower().replace('_', '-')
                version = dist.metadata['Version']
                installed[dist.metadata['Name']] = {'version': version, 'files': [p for p in pkg_files if p.exists()], 'executables': executables, 'type': self._classify_package_type(pkg_files)}
                redis_key = _('{}bubble:{}:{}:file_paths').format(self.parent_omnipkg.redis_key_prefix, pkg_name, version)
                existing_paths = set(self.parent_omnipkg.redis_client.smembers(redis_key)) if self.parent_omnipkg.redis_client.exists(redis_key) else set()
                all_package_files_for_check = pkg_files + executables
                for file_path in all_package_files_for_check:
                    if str(file_path) not in existing_paths:
                        unregistered_file_count += 1
            except Exception as e:
                print(_('    ⚠️  Could not analyze {}: {}').format(dist_info.name, e))
        if unregistered_file_count > 0:
            print(_('    ⚠️  Found {} files not in registry. They will be registered during bubble creation.').format(unregistered_file_count))
        return installed

    def _is_binary(self, file_path: Path) -> bool:
        """
        Robustly checks if a file is a binary executable, excluding C extensions.
        Gracefully falls back to a basic check if 'python-magic' is not installed.
        """
        if file_path.suffix in {'.so', '.pyd'}:
            return False
        if not HAS_MAGIC:
            if not getattr(self, '_magic_warning_shown', False):
                print(_("⚠️  Warning: 'python-magic' not installed. Using basic binary detection."))
                self._magic_warning_shown = True
            return file_path.suffix in {'.dll', '.exe'}
        try:
            mime = magic.Magic(mime=True)
            file_type = mime.from_file(str(file_path))
            executable_types = {'application/x-executable', 'application/x-sharedlib', 'application/x-pie-executable'}
            return any((t in file_type for t in executable_types)) or file_path.suffix in {'.dll', '.exe'}
        except Exception:
            return file_path.suffix in {'.dll', '.exe'}

    def _create_deduplicated_bubble(self, installed_tree: Dict, bubble_path: Path, temp_install_path: Path) -> bool:
        """
        Enhanced Version: Fixes flask-login and similar packages with missing submodules.
        
        Key improvements:
        1. Better detection of package internal structure
        2. Conservative approach for packages with submodules
        3. Enhanced failsafe scanning
        4. Special handling for namespace packages
        """
        print(_('    🧹 Creating deduplicated bubble at {}').format(bubble_path))
        bubble_path.mkdir(parents=True, exist_ok=True)
        main_env_hashes = self._get_or_build_main_env_hash_index()
        stats = {'total_files': 0, 'copied_files': 0, 'deduplicated_files': 0, 'c_extensions': [], 'binaries': [], 'python_files': 0, 'package_modules': {}, 'submodules_found': 0}
        c_ext_packages = {pkg_name for pkg_name, info in installed_tree.items() if info.get('type') in ['native', 'mixed']}
        binary_packages = {pkg_name for pkg_name, info in installed_tree.items() if info.get('type') == 'binary'}
        complex_packages = set()
        for pkg_name, pkg_info in installed_tree.items():
            pkg_files = pkg_info.get('files', [])
            py_files_in_subdirs = [f for f in pkg_files if f.suffix == '.py' and len(f.parts) > 2 and (f.parts[-2] != '__pycache__')]
            if len(py_files_in_subdirs) > 1:
                complex_packages.add(pkg_name)
                stats['package_modules'][pkg_name] = len(py_files_in_subdirs)
        if c_ext_packages:
            print(_('    🔬 Found C-extension packages: {}').format(', '.join(c_ext_packages)))
        if binary_packages:
            print(_('    ⚙️  Found binary packages: {}').format(', '.join(binary_packages)))
        if complex_packages:
            print(_('    📦 Found complex packages with submodules: {}').format(', '.join(complex_packages)))
        processed_files = set()
        for pkg_name, pkg_info in installed_tree.items():
            if pkg_name in c_ext_packages:
                should_deduplicate_this_package = False
                print(_('    🔬 {}: C-extension - copying all files').format(pkg_name))
            elif pkg_name in binary_packages:
                should_deduplicate_this_package = False
                print(_('    ⚙️  {}: Binary package - copying all files').format(pkg_name))
            elif pkg_name in complex_packages:
                should_deduplicate_this_package = False
                print(_('    📦 {}: Complex package ({} submodules) - copying all files').format(pkg_name, stats['package_modules'][pkg_name]))
            else:
                should_deduplicate_this_package = True
            pkg_copied = 0
            pkg_deduplicated = 0
            for source_path in pkg_info.get('files', []):
                if not source_path.is_file():
                    continue
                processed_files.add(source_path)
                stats['total_files'] += 1
                is_c_ext = source_path.suffix in {'.so', '.pyd'}
                is_binary = self._is_binary(source_path)
                is_python_module = source_path.suffix == '.py'
                if is_c_ext:
                    stats['c_extensions'].append(source_path.name)
                elif is_binary:
                    stats['binaries'].append(source_path.name)
                elif is_python_module:
                    stats['python_files'] += 1
                should_copy = True
                if should_deduplicate_this_package:
                    if is_python_module and '/__pycache__/' not in str(source_path):
                        should_copy = True
                    else:
                        try:
                            file_hash = self._get_file_hash(source_path)
                            if file_hash in main_env_hashes:
                                should_copy = False
                        except (IOError, OSError):
                            pass
                if should_copy:
                    stats['copied_files'] += 1
                    pkg_copied += 1
                    self._copy_file_to_bubble(source_path, bubble_path, temp_install_path, is_binary or is_c_ext)
                else:
                    stats['deduplicated_files'] += 1
                    pkg_deduplicated += 1
            if pkg_copied > 0 or pkg_deduplicated > 0:
                print(_('    📄 {}: copied {}, deduplicated {}').format(pkg_name, pkg_copied, pkg_deduplicated))
        all_temp_files = {p for p in temp_install_path.rglob('*') if p.is_file()}
        missed_files = all_temp_files - processed_files
        if missed_files:
            print(_('    ⚠️  Found {} file(s) not listed in package metadata.').format(len(missed_files)))
            missed_by_package = {}
            for source_path in missed_files:
                owner_pkg = self._find_owner_package(source_path, temp_install_path, installed_tree)
                if owner_pkg not in missed_by_package:
                    missed_by_package[owner_pkg] = []
                missed_by_package[owner_pkg].append(source_path)
            for owner_pkg, files in missed_by_package.items():
                print(_('    📦 {}: found {} additional files').format(owner_pkg, len(files)))
                for source_path in files:
                    stats['total_files'] += 1
                    is_python_module = source_path.suffix == '.py'
                    is_init_file = source_path.name == '__init__.py'
                    should_deduplicate = owner_pkg not in c_ext_packages and owner_pkg not in binary_packages and (owner_pkg not in complex_packages) and (not self._is_binary(source_path)) and (source_path.suffix not in {'.so', '.pyd'}) and (not is_init_file) and (not is_python_module)
                    should_copy = True
                    if should_deduplicate:
                        try:
                            file_hash = self._get_file_hash(source_path)
                            if file_hash in main_env_hashes:
                                should_copy = False
                        except (IOError, OSError):
                            pass
                    is_c_ext = source_path.suffix in {'.so', '.pyd'}
                    is_binary = self._is_binary(source_path)
                    if is_c_ext:
                        stats['c_extensions'].append(source_path.name)
                    elif is_binary:
                        stats['binaries'].append(source_path.name)
                    else:
                        stats['python_files'] += 1
                    if should_copy:
                        stats['copied_files'] += 1
                        self._copy_file_to_bubble(source_path, bubble_path, temp_install_path, is_binary or is_c_ext)
                    else:
                        stats['deduplicated_files'] += 1
        self._verify_package_integrity(bubble_path, installed_tree, temp_install_path)
        efficiency = stats['deduplicated_files'] / stats['total_files'] * 100 if stats['total_files'] > 0 else 0
        print(_('    ✅ Bubble created: {} files copied, {} deduplicated.').format(stats['copied_files'], stats['deduplicated_files']))
        print(_('    📊 Space efficiency: {}% saved.').format(efficiency))
        if stats['package_modules']:
            print(_('    📦 Complex packages preserved: {} packages with submodules').format(len(stats['package_modules'])))
        self._create_bubble_manifest(bubble_path, installed_tree, stats)
        return True

    def _verify_package_integrity(self, bubble_path: Path, installed_tree: Dict, temp_install_path: Path) -> None:
        """
        Verify that critical package files are present in the bubble.
        This catches issues like missing flask_login.config modules.
        """
        print(_('    🔍 Verifying package integrity...'))
        for pkg_name, pkg_info in installed_tree.items():
            pkg_files = pkg_info.get('files', [])
            package_dirs = set()
            for file_path in pkg_files:
                if file_path.name == '__init__.py':
                    package_dirs.add(file_path.parent)
            for pkg_dir in package_dirs:
                relative_pkg_path = pkg_dir.relative_to(temp_install_path)
                bubble_pkg_path = bubble_path / relative_pkg_path
                if not bubble_pkg_path.exists():
                    print(_('    ⚠️  Missing package directory: {}').format(relative_pkg_path))
                    continue
                expected_py_files = [f for f in pkg_files if f.suffix == '.py' and f.parent == pkg_dir]
                for py_file in expected_py_files:
                    relative_py_path = py_file.relative_to(temp_install_path)
                    bubble_py_path = bubble_path / relative_py_path
                    if not bubble_py_path.exists():
                        print(_('    🚨 CRITICAL: Missing Python module: {}').format(relative_py_path))
                        self._copy_file_to_bubble(py_file, bubble_path, temp_install_path, False)
                        print(_('    🔧 Fixed: Copied missing module {}').format(relative_py_path))

    def _find_owner_package(self, file_path: Path, temp_install_path: Path, installed_tree: Dict) -> Optional[str]:
        """
        Helper to find which package a file belongs to, now supporting .egg-info.
        """
        try:
            for parent in file_path.parents:
                if parent.name.endswith(('.dist-info', '.egg-info')):
                    pkg_name = parent.name.split('-')[0]
                    return pkg_name.lower().replace('_', '-')
        except Exception:
            pass
        return None

    def _copy_file_to_bubble(self, source_path: Path, bubble_path: Path, temp_install_path: Path, make_executable: bool=False):
        """Helper method to copy a file to the bubble with proper error handling."""
        try:
            rel_path = source_path.relative_to(temp_install_path)
            dest_path = bubble_path / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
            if make_executable:
                os.chmod(dest_path, 493)
        except Exception as e:
            print(_('    ⚠️ Warning: Failed to copy {}: {}').format(source_path.name, e))

    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        """
        Builds or loads a FAST hash index using package metadata when possible,
        falling back to filesystem scan only when needed.
        """
        if not self.parent_omnipkg.redis_client:
            self.parent_omnipkg.connect_redis()
        redis_key = _('{}main_env:file_hashes').format(self.parent_omnipkg.redis_key_prefix)
        if self.parent_omnipkg.redis_client.exists(redis_key):
            print(_('    ⚡️ Loading main environment hash index from cache...'))
            cached_hashes = set(self.parent_omnipkg.redis_client.sscan_iter(redis_key))
            print(_('    📈 Loaded {} file hashes from Redis.').format(len(cached_hashes)))
            return cached_hashes
        print(_('    🔍 Building main environment hash index...'))
        hash_set = set()
        try:
            print(_('    📦 Attempting fast indexing via package metadata...'))
            installed_packages = self.parent_omnipkg.get_installed_packages(live=True)
            successful_packages = 0
            failed_packages = []
            for pkg_name in tqdm(installed_packages.keys(), desc='    📦 Indexing via metadata', unit='pkg'):
                try:
                    dist = importlib.metadata.distribution(pkg_name)
                    if dist.files:
                        pkg_hashes = 0
                        for file_path in dist.files:
                            try:
                                abs_path = dist.locate_file(file_path)
                                if abs_path and abs_path.is_file() and (abs_path.suffix not in {'.pyc', '.pyo'}) and ('__pycache__' not in abs_path.parts):
                                    hash_set.add(self._get_file_hash(abs_path))
                                    pkg_hashes += 1
                            except (IOError, OSError, AttributeError):
                                continue
                        if pkg_hashes > 0:
                            successful_packages += 1
                        else:
                            failed_packages.append(pkg_name)
                    else:
                        failed_packages.append(pkg_name)
                except Exception:
                    failed_packages.append(pkg_name)
            print(_('    ✅ Successfully indexed {} packages via metadata').format(successful_packages))
            if failed_packages:
                print(_('    🔄 Fallback scan for {} packages: {}{}').format(len(failed_packages), ', '.join(failed_packages[:3]), '...' if len(failed_packages) > 3 else ''))
                potential_files = []
                for file_path in self.site_packages.rglob('*'):
                    if file_path.is_file() and file_path.suffix not in {'.pyc', '.pyo'} and ('__pycache__' not in file_path.parts):
                        file_str = str(file_path).lower()
                        if any((pkg.lower().replace('-', '_') in file_str or pkg.lower().replace('_', '-') in file_str for pkg in failed_packages)):
                            potential_files.append(file_path)
                for file_path in tqdm(potential_files, desc='    📦 Fallback scan', unit='file'):
                    try:
                        hash_set.add(self._get_file_hash(file_path))
                    except (IOError, OSError):
                        continue
        except Exception as e:
            print(_('    ⚠️ Metadata approach failed ({}), falling back to full scan...').format(e))
            files_to_process = [p for p in self.site_packages.rglob('*') if p.is_file() and p.suffix not in {'.pyc', '.pyo'} and ('__pycache__' not in p.parts)]
            for file_path in tqdm(files_to_process, desc='    📦 Full scan', unit='file'):
                try:
                    hash_set.add(self._get_file_hash(file_path))
                except (IOError, OSError):
                    continue
        print(_('    💾 Saving {} file hashes to Redis cache...').format(len(hash_set)))
        if hash_set:
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                for h in hash_set:
                    pipe.sadd(redis_key, h)
                pipe.execute()
        print(_('    📈 Indexed {} files from main environment.').format(len(hash_set)))
        return hash_set

    def _register_bubble_location(self, bubble_path: Path, installed_tree: Dict, stats: dict):
        """
        Register bubble location and summary statistics in a single batch operation.
        """
        registry_key = '{}bubble_locations'.format(self.parent_omnipkg.redis_key_prefix)
        bubble_data = {'path': str(bubble_path), 'python_version': '{}.{}'.format(sys.version_info.major, sys.version_info.minor), 'created_at': datetime.now().isoformat(), 'packages': {pkg: info['version'] for pkg, info in installed_tree.items()}, 'stats': {'total_files': stats['total_files'], 'copied_files': stats['copied_files'], 'deduplicated_files': stats['deduplicated_files'], 'c_extensions_count': len(stats['c_extensions']), 'binaries_count': len(stats['binaries']), 'python_files': stats['python_files']}}
        bubble_id = bubble_path.name
        self.parent_omnipkg.redis_client.hset(registry_key, bubble_id, json.dumps(bubble_data))
        print(_('    📝 Registered bubble location and stats for {} packages.').format(len(installed_tree)))

    def _get_file_hash(self, file_path: Path) -> str:
        path_str = str(file_path)
        if path_str in self.file_hash_cache:
            return self.file_hash_cache[path_str]
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while (chunk := f.read(8192)):
                h.update(chunk)
        file_hash = h.hexdigest()
        self.file_hash_cache[path_str] = file_hash
        return file_hash

    def _create_bubble_manifest(self, bubble_path: Path, installed_tree: Dict, stats: dict):
        """
        Creates both a local manifest file and registers the bubble in Redis.
        This replaces the old _create_bubble_manifest with integrated registry functionality.
        """
        total_size = sum((f.stat().st_size for f in bubble_path.rglob('*') if f.is_file()))
        size_mb = round(total_size / (1024 * 1024), 2)
        symlink_origins = set()
        for item in bubble_path.rglob('*.so'):
            if item.is_symlink():
                try:
                    real_path = item.resolve()
                    symlink_origins.add(str(real_path.parent))
                except Exception:
                    continue
        stats['symlink_origins'] = sorted(list(symlink_origins), key=len, reverse=True)
        manifest_data = {'created_at': datetime.now().isoformat(), 'python_version': _('{}.{}').format(sys.version_info.major, sys.version_info.minor), 'omnipkg_version': '1.0.0', 'packages': {name: {'version': info['version'], 'type': info['type'], 'install_reason': info.get('install_reason', 'dependency')} for name, info in installed_tree.items()}, 'stats': {'bubble_size_mb': size_mb, 'package_count': len(installed_tree), 'total_files': stats['total_files'], 'copied_files': stats['copied_files'], 'deduplicated_files': stats['deduplicated_files'], 'deduplication_efficiency_percent': round(stats['deduplicated_files'] / stats['total_files'] * 100 if stats['total_files'] > 0 else 0, 1), 'c_extensions_count': len(stats['c_extensions']), 'binaries_count': len(stats['binaries']), 'python_files': stats['python_files'], 'symlink_origins': stats['symlink_origins']}, 'file_types': {'c_extensions': stats['c_extensions'][:10], 'binaries': stats['binaries'][:10], 'has_more_c_extensions': len(stats['c_extensions']) > 10, 'has_more_binaries': len(stats['binaries']) > 10}}
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f, indent=2)
        registry_key = _('{}bubble_locations').format(self.parent_omnipkg.redis_key_prefix)
        bubble_id = bubble_path.name
        redis_bubble_data = {**manifest_data, 'path': str(bubble_path), 'manifest_path': str(manifest_path), 'bubble_id': bubble_id}
        try:
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                pipe.hset(registry_key, bubble_id, json.dumps(redis_bubble_data))
                for pkg_name, pkg_info in installed_tree.items():
                    canonical_pkg_name = canonicalize_name(pkg_name)
                    main_pkg_key = f'{self.parent_omnipkg.redis_key_prefix}{canonical_pkg_name}'
                    version_str = pkg_info['version']
                    
                    # --- THIS IS THE FIX ---
                    # The 'version_specific_key' variable was missing. It is now defined correctly.
                    version_specific_key = f'{main_pkg_key}:{version_str}'
                    # --- END FIX ---
                    
                    pipe.hset(main_pkg_key, f'bubble_version:{version_str}', 'true')
                    pipe.hset(version_specific_key, 'path', str(bubble_path))
                    pipe.sadd(_('{}:installed_versions').format(main_pkg_key), version_str)
                    
                    # --- FIX FOR INDEX KEY ---
                    # Ensure the 'index' key uses the correct environment prefix, not the full package prefix.
                    index_key = f"{self.parent_omnipkg.redis_env_prefix}index"
                    pipe.sadd(index_key, canonical_pkg_name)
                    # --- END FIX ---

                for pkg_name, pkg_info in installed_tree.items():
                    pkg_version_key = '{}=={}'.format(canonicalize_name(pkg_name), pkg_info['version'])
                    pipe.hset(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), pkg_version_key, bubble_id)
                size_category = 'small' if size_mb < 10 else 'medium' if size_mb < 100 else 'large'
                pipe.sadd(_('{}bubbles_by_size:{}').format(self.parent_omnipkg.redis_key_prefix, size_category), bubble_id)
                pipe.execute()
            print(_('    📝 Created manifest and registered bubble for {} packages ({} MB).').format(len(installed_tree), size_mb))
        except Exception as e:
            print(_('    ⚠️ Warning: Failed to register bubble in Redis: {}').format(e))
            import traceback
            traceback.print_exc()
            print(_('    📝 Local manifest created at {}').format(manifest_path))

    def get_bubble_info(self, bubble_id: str) -> dict:
        """
        Retrieves comprehensive bubble information from Redis registry.
        """
        registry_key = _('{}bubble_locations').format(self.parent_omnipkg.redis_key_prefix)
        bubble_data = self.parent_omnipkg.redis_client.hget(registry_key, bubble_id)
        if bubble_data:
            return json.loads(bubble_data)
        return {}

    def find_bubbles_for_package(self, pkg_name: str, version: str=None) -> list:
        """
        Finds all bubbles containing a specific package.
        """
        if version:
            pkg_key = '{}=={}'.format(pkg_name, version)
            bubble_id = self.parent_omnipkg.redis_client.hget(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), pkg_key)
            return [bubble_id] if bubble_id else []
        else:
            pattern = f'{pkg_name}==*'
            matching_keys = []
            for key in self.parent_omnipkg.redis_client.hkeys(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix)):
                if key.startswith(f'{pkg_name}=='):
                    bubble_id = self.parent_omnipkg.redis_client.hget(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), key)
                    matching_keys.append(bubble_id)
            return matching_keys

    def cleanup_old_bubbles(self, keep_latest: int=3, size_threshold_mb: float=500):
        """
        Cleanup old bubbles based on size and age, keeping most recent ones.
        """
        registry_key = _('{}bubble_locations').format(self.parent_omnipkg.redis_key_prefix)
        all_bubbles = {}
        for bubble_id, bubble_data_str in self.parent_omnipkg.redis_client.hgetall(registry_key).items():
            bubble_data = json.loads(bubble_data_str)
            all_bubbles[bubble_id] = bubble_data
        by_package = {}
        for bubble_id, data in all_bubbles.items():
            pkg_name = bubble_id.split('-')[0]
            if pkg_name not in by_package:
                by_package[pkg_name] = []
            by_package[pkg_name].append((bubble_id, data))
        bubbles_to_remove = []
        total_size_freed = 0
        for pkg_name, bubbles in by_package.items():
            bubbles.sort(key=lambda x: x[1]['created_at'], reverse=True)
            for bubble_id, data in bubbles[keep_latest:]:
                bubbles_to_remove.append((bubble_id, data))
                total_size_freed += data['stats']['bubble_size_mb']
        for bubble_id, data in all_bubbles.items():
            if (bubble_id, data) not in bubbles_to_remove:
                if data['stats']['bubble_size_mb'] > size_threshold_mb:
                    bubbles_to_remove.append((bubble_id, data))
                    total_size_freed += data['stats']['bubble_size_mb']
        if bubbles_to_remove:
            print(_('    🧹 Cleaning up {} old bubbles ({} MB)...').format(len(bubbles_to_remove), total_size_freed))
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                for bubble_id, data in bubbles_to_remove:
                    pipe.hdel(registry_key, bubble_id)
                    for pkg_name, pkg_info in data.get('packages', {}).items():
                        pkg_key = '{}=={}'.format(pkg_name, pkg_info['version'])
                        pipe.hdel(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), pkg_key)
                    size_mb = data['stats']['bubble_size_mb']
                    size_category = 'small' if size_mb < 10 else 'medium' if size_mb < 100 else 'large'
                    pipe.srem(_('{}bubbles_by_size:{}').format(self.parent_omnipkg.redis_key_prefix, size_category), bubble_id)
                    bubble_path = Path(data['path'])
                    if bubble_path.exists():
                        shutil.rmtree(bubble_path, ignore_errors=True)
                pipe.execute()
            print(_('    ✅ Freed {} MB of storage.').format(total_size_freed))
        else:
            print(_('    ✅ No bubbles need cleanup.'))

class ImportHookManager:

    def __init__(self, multiversion_base: str, config: Dict, redis_client=None):
        self.multiversion_base = Path(multiversion_base)
        self.version_map = {}
        self.active_versions = {}
        self.hook_installed = False
        self.redis_client = redis_client
        self.config = config
        self.http_session = http_requests.Session()

    def load_version_map(self):
        if not self.multiversion_base.exists():
            return
        for version_dir in self.multiversion_base.iterdir():
            if version_dir.is_dir() and '-' in version_dir.name:
                pkg_name, version = version_dir.name.rsplit('-', 1)
                if pkg_name not in self.version_map:
                    self.version_map[pkg_name] = {}
                self.version_map[pkg_name][version] = str(version_dir)

    def refresh_bubble_map(self, pkg_name: str, version: str, bubble_path: str):
        """
        Immediately adds a newly created bubble to the internal version map
        to prevent race conditions during validation.
        """
        pkg_name = pkg_name.lower().replace('_', '-')
        if pkg_name not in self.version_map:
            self.version_map[pkg_name] = {}
        self.version_map[pkg_name][version] = bubble_path
        print(_('    🧠 HookManager now aware of new bubble: {}=={}').format(pkg_name, version))

    def remove_bubble_from_tracking(self, package_name: str, version: str):
        """
        Removes a bubble from the internal version map tracking.
        Used when cleaning up redundant bubbles.
        """
        pkg_name = package_name.lower().replace('_', '-')
        if pkg_name in self.version_map and version in self.version_map[pkg_name]:
            del self.version_map[pkg_name][version]
            print(f'    ✅ Removed bubble tracking for {pkg_name}=={version}')
            if not self.version_map[pkg_name]:
                del self.version_map[pkg_name]
                print(f'    ✅ Removed package {pkg_name} from version map (no more bubbles)')
        if pkg_name in self.active_versions and self.active_versions[pkg_name] == version:
            del self.active_versions[pkg_name]
            print(f'    ✅ Removed active version tracking for {pkg_name}=={version}')

    def validate_bubble(self, package_name: str, version: str) -> bool:
        """
        Validates a bubble's integrity by checking for its physical existence
        and the presence of a manifest file.
        """
        bubble_path_str = self.get_package_path(package_name, version)
        if not bubble_path_str:
            print(_("    ❌ Bubble not found in HookManager's map for {}=={}").format(package_name, version))
            return False
        bubble_path = Path(bubble_path_str)
        if not bubble_path.is_dir():
            print(_('    ❌ Bubble directory does not exist at: {}').format(bubble_path))
            return False
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if not manifest_path.exists():
            print(_('    ❌ Bubble is incomplete: Missing manifest file at {}').format(manifest_path))
            return False
        bin_path = bubble_path / 'bin'
        if not bin_path.is_dir():
            print(_("    ⚠️  Warning: Bubble for {}=={} does not contain a 'bin' directory.").format(package_name, version))
        print(_('    ✅ Bubble validated successfully: {}=={}').format(package_name, version))
        return True

    def install_import_hook(self):
        if self.hook_installed:
            return
        sys.meta_path.insert(0, MultiversionFinder(self))
        self.hook_installed = True

    def set_active_version(self, package_name: str, version: str):
        self.active_versions[package_name.lower()] = version

    def get_package_path(self, package_name: str, version: str=None) -> Optional[str]:
        pkg_name = package_name.lower().replace('_', '-')
        version = version or self.active_versions.get(pkg_name)
        if pkg_name in self.version_map and version in self.version_map[pkg_name]:
            return self.version_map[pkg_name][version]
        if hasattr(self, 'bubble_manager') and pkg_name in self.bubble_manager.package_path_registry:
            if version in self.bubble_manager.package_path_registry[pkg_name]:
                return str(self.multiversion_base / '{}-{}'.format(pkg_name, version))
        return None

class MultiversionFinder:

    def __init__(self, hook_manager: ImportHookManager):
        self.hook_manager = hook_manager
        self.http_session = http_requests.Session()

    def find_spec(self, fullname, path, target=None):
        top_level = fullname.split('.')[0]
        pkg_path = self.hook_manager.get_package_path(top_level)
        if pkg_path and os.path.exists(pkg_path):
            if pkg_path not in sys.path:
                sys.path.insert(0, pkg_path)
        return None

class omnipkg:

    def __init__(self, config_manager: ConfigManager):
        """
        Initializes the Omnipkg core engine with a robust, fail-safe sequence.
        """
        self.config_manager = config_manager
        self.config = config_manager.config
        if not self.config:
            raise RuntimeError('OmnipkgCore cannot initialize: Configuration is missing or invalid.')
        self.env_id = self._get_env_id()
        self.multiversion_base = Path(self.config['multiversion_base'])
        self.redis_client = None
        self._info_cache = {}
        self._installed_packages_cache = None
        self.http_session = http_requests.Session()
        self.multiversion_base.mkdir(parents=True, exist_ok=True)
        if not self.connect_redis():
            sys.exit(1)
        self.interpreter_manager = InterpreterManager(self.config_manager)
        self.hook_manager = ImportHookManager(str(self.multiversion_base), config=self.config, redis_client=self.redis_client)
        self.bubble_manager = BubbleIsolationManager(self.config, self)
        migration_flag_key = f'omnipkg:env_{self.env_id}:migration_v2_env_aware_keys_complete'
        if not self.redis_client.get(migration_flag_key):
            old_keys_iterator = self.redis_client.scan_iter('omnipkg:pkg:*', count=1)
            if next(old_keys_iterator, None):
                self._perform_redis_key_migration(migration_flag_key)
            else:
                self.redis_client.set(migration_flag_key, 'true')
        self.hook_manager.load_version_map()
        self.hook_manager.install_import_hook()
        print(_('✅ Omnipkg core initialized successfully.'))

    def _perform_redis_key_migration(self, migration_flag_key: str):
        """
        Performs a one-time, automatic migration of Redis keys from the old
        global format to the new environment-and-python-specific format.
        """
        print('🔧 Performing one-time Knowledge Base upgrade for multi-environment support...')
        old_prefix = 'omnipkg:pkg:'
        all_old_keys = self.redis_client.keys(f'{old_prefix}*')
        if not all_old_keys:
            print('   ✅ No old-format data found to migrate. Marking as complete.')
            self.redis_client.set(migration_flag_key, 'true')
            return
        new_prefix_for_current_env = self.redis_key_prefix
        migrated_count = 0
        with self.redis_client.pipeline() as pipe:
            for old_key in all_old_keys:
                new_key = old_key.replace(old_prefix, new_prefix_for_current_env, 1)
                pipe.rename(old_key, new_key)
                migrated_count += 1
            pipe.set(migration_flag_key, 'true')
            pipe.execute()
        print(f'   ✅ Successfully upgraded {migrated_count} KB entries for this environment.')

    def _get_env_id(self) -> str:
        """Creates a short, stable hash from the venv path to uniquely identify it."""
        venv_path = str(Path(sys.prefix).resolve())
        return hashlib.md5(venv_path.encode()).hexdigest()[:8]

    @property
    def redis_env_prefix(self) -> str:
        """
        Gets the environment-and-python-specific part of the Redis key,
        e.g., 'omnipkg:env_12345678:py3.11:'.
        This is the correct base for keys like 'index' that are not package-specific.
        """
        # self.redis_key_prefix looks like 'omnipkg:env_xxxx:py3.y:pkg:'
        # We want to strip off the 'pkg:' part.
        return self.redis_key_prefix.rsplit('pkg:', 1)[0]

    @property
    def redis_key_prefix(self) -> str:
        python_exe_path = self.config.get('python_executable', sys.executable)
        py_ver_str = 'unknown'
        match = re.search('python(3\\.\\d+)', python_exe_path)
        if match:
            py_ver_str = f'py{match.group(1)}'
        else:
            try:
                result = subprocess.run([python_exe_path, '-c', "import sys; print(f'py{sys.version_info.major}.{sys.version_info.minor}')"], capture_output=True, text=True, check=True, timeout=2)
                py_ver_str = result.stdout.strip()
            except Exception:
                py_ver_str = f'py{sys.version_info.major}.{sys.version_info.minor}'
        return f'omnipkg:env_{self.config_manager.env_id}:{py_ver_str}:pkg:'

    def connect_redis(self) -> bool:
        try:
            self.redis_client = redis.Redis(host=self.config['redis_host'], port=self.config['redis_port'], decode_responses=True, socket_connect_timeout=5)
            self.redis_client.ping()
            return True
        except redis.ConnectionError:
            print(_('❌ Could not connect to Redis. Is the Redis server running?'))
            return False
        except Exception as e:
            print(_('❌ An unexpected Redis connection error occurred: {}').format(e))
            return False

    def reset_configuration(self, force: bool=False) -> int:
        """
        Deletes the config.json file to allow for a fresh setup.
        """
        config_path = Path.home() / '.config' / 'omnipkg' / 'config.json'
        if not config_path.exists():
            print(_('✅ Configuration file does not exist. Nothing to do.'))
            return 0
        print(_('🗑️  This will permanently delete your configuration file at:'))
        print(_('   {}').format(config_path))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                print(_('🚫 Reset cancelled.'))
                return 1
        try:
            config_path.unlink()
            print(_('✅ Configuration file deleted successfully.'))
            print('\n' + '─' * 60)
            print(_('🚀 The next time you run `omnipkg`, you will be guided through the first-time setup.'))
            print('─' * 60)
            return 0
        except OSError as e:
            print(_('❌ Error: Could not delete configuration file: {}').format(e))
            print(_('   Please check your file permissions for {}').format(config_path))
            return 1

    def reset_knowledge_base(self, force: bool=False) -> int:
        """
        Deletes ALL omnipkg data for the CURRENT environment from Redis,
        as well as any legacy global data. It then triggers a full rebuild.
        """
        if not self.connect_redis():
            return 1
        new_env_pattern = f'{self.redis_key_prefix}*'
        old_global_pattern = 'omnipkg:pkg:*'
        migration_flag_pattern = 'omnipkg:migration:*'
        snapshot_pattern = 'omnipkg:snapshot:*'
        print(_('\n🧠 omnipkg Knowledge Base Reset'))
        print('-' * 50)
        print(_("   This will DELETE all data for the current environment (matching '{}')").format(new_env_pattern))
        print(_('   It will ALSO delete any legacy global data from older omnipkg versions.'))
        print(_('   ⚠️  This command does NOT uninstall any Python packages.'))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                print(_('🚫 Reset cancelled.'))
                return 1
        print(_('\n🗑️  Clearing knowledge base...'))
        try:
            keys_new_env = self.redis_client.keys(new_env_pattern)
            keys_old_global = self.redis_client.keys(old_global_pattern)
            keys_migration = self.redis_client.keys(migration_flag_pattern)
            keys_snapshot = self.redis_client.keys(snapshot_pattern)
            all_keys_to_delete = set(keys_new_env + keys_old_global + keys_migration + keys_snapshot)
            if all_keys_to_delete:
                delete_command = self.redis_client.unlink if hasattr(self.redis_client, 'unlink') else self.redis_client.delete
                delete_command(*all_keys_to_delete)
                print(_('   ✅ Cleared {} cached entries from Redis.').format(len(all_keys_to_delete)))
            else:
                print(_('   ✅ Knowledge base was already clean.'))
        except Exception as e:
            print(_('   ❌ Failed to clear knowledge base: {}').format(e))
            return 1
        self._info_cache.clear()
        self._installed_packages_cache = None
        return self.rebuild_knowledge_base(force=True)

    def rebuild_knowledge_base(self, force: bool=False):
        """
        FIXED: Rebuilds the knowledge base by directly invoking the metadata gatherer
        in-process, avoiding subprocess argument limits and ensuring all discovered
        packages are processed correctly.
        """
        print(_('🧠 Forcing a full rebuild of the knowledge base...'))
        if not self.connect_redis():
            return 1
        try:
            from .package_meta_builder import omnipkgMetadataGatherer
            gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, force_refresh=force)
            gatherer.redis_client = self.redis_client
            gatherer.run()
            self._info_cache.clear()
            self._installed_packages_cache = None
            print(_('✅ Knowledge base rebuilt successfully.'))
            return 0
        except Exception as e:
            print(_('    ❌ An unexpected error occurred during knowledge base rebuild: {}').format(e))
            import traceback
            traceback.print_exc()
            return 1

    def _analyze_rebuild_needs(self) -> dict:
        project_files = []
        for ext in ['.py', 'requirements.txt', 'pyproject.toml', 'Pipfile']:
            pass
        return {'auto_rebuild': len(project_files) > 0, 'components': ['dependency_cache', 'metadata', 'compatibility_matrix'], 'confidence': 0.95, 'suggestions': []}

    def _rebuild_component(self, component: str) -> None:
        if component == 'metadata':
            print(_('   🔄 Rebuilding core package metadata...'))
            try:
                cmd = [self.config['python_executable'], self.config['builder_script_path'], '--force']
                subprocess.run(cmd, check=True)
                print(_('   ✅ Core metadata rebuilt.'))
            except Exception as e:
                print(_('   ❌ Metadata rebuild failed: {}').format(e))
        else:
            print(_('   (Skipping {} - feature coming soon!)').format(component))

    def prune_bubbled_versions(self, package_name: str, keep_latest: Optional[int]=None, force: bool=False):
        """
        Intelligently removes old bubbled versions of a package.
        """
        self._synchronize_knowledge_base_with_reality()
        c_name = canonicalize_name(package_name)
        all_installations = self._find_package_installations(c_name)
        active_version_info = next((p for p in all_installations if p['type'] == 'active'), None)
        bubbled_versions = [p for p in all_installations if p['type'] == 'bubble']
        if not bubbled_versions:
            print(_("✅ No bubbles found for '{}'. Nothing to prune.").format(c_name))
            return 0
        bubbled_versions.sort(key=lambda x: parse_version(x['version']), reverse=True)
        to_prune = []
        if keep_latest is not None:
            if keep_latest < 0:
                print(_("❌ 'keep-latest' must be a non-negative number."))
                return 1
            to_prune = bubbled_versions[keep_latest:]
            kept_count = len(bubbled_versions) - len(to_prune)
            print(_('🔎 Found {} bubbles. Keeping the latest {}, pruning {} older versions.').format(len(bubbled_versions), kept_count, len(to_prune)))
        else:
            to_prune = bubbled_versions
            print(_("🔎 Found {} bubbles to prune for '{}'.").format(len(to_prune), c_name))
        if not to_prune:
            print(_('✅ No bubbles match the pruning criteria.'))
            return 0
        print(_('\nThe following bubbled versions will be permanently deleted:'))
        for item in to_prune:
            print(_('  - v{} (bubble)').format(item['version']))
        if active_version_info:
            print(_('🛡️  The active version (v{}) will NOT be affected.').format(active_version_info['version']))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                print(_('🚫 Prune cancelled.'))
                return 1
        specs_to_uninstall = [f"{item['name']}=={item['version']}" for item in to_prune]
        for spec in specs_to_uninstall:
            print('-' * 20)
            self.smart_uninstall([spec], force=True)
        print(_("\n🎉 Pruning complete for '{}'.").format(c_name))
        return 0

    def _check_and_run_pending_rebuild(self) -> bool:
        """
        Checks for a flag file indicating a new interpreter needs its KB built.
        If the current context matches a version in the flag, it runs the build.
        Returns True if a rebuild was run, False otherwise.
        """
        flag_file = self.config_manager.venv_path / '.omnipkg' / '.needs_kb_rebuild'
        if not flag_file.exists():
            return False

        # Determine the current Python context's version
        configured_exe = self.config.get('python_executable')
        version_tuple = self.config_manager._verify_python_version(configured_exe)
        if not version_tuple:
            return False # Cannot determine current version
        current_version_str = f"{version_tuple[0]}.{version_tuple[1]}"
        
        lock_file = self.config_manager.venv_path / '.omnipkg' / '.needs_kb_rebuild.lock'
        with FileLock(lock_file):
            versions_to_rebuild = []
            try:
                with open(flag_file, 'r') as f:
                    versions_to_rebuild = json.load(f)
            except (json.JSONDecodeError, IOError):
                flag_file.unlink(missing_ok=True)
                return False

            if current_version_str in versions_to_rebuild:
                print(f"💡 First use of Python {current_version_str} detected.")
                print("   Building its knowledge base now...")
                
                # Perform the rebuild for the current context
                rebuild_status = self.rebuild_knowledge_base(force=True)

                if rebuild_status == 0: # Check for success
                    # Remove the current version from the list and save back
                    versions_to_rebuild.remove(current_version_str)
                    if not versions_to_rebuild:
                        flag_file.unlink(missing_ok=True) # Clean up empty file
                    else:
                        with open(flag_file, 'w') as f:
                            json.dump(versions_to_rebuild, f)
                    print(f"   ✅ Knowledge base for Python {current_version_str} is ready.")
                    return True
                else:
                    print(f"   ❌ Failed to build knowledge base. It will be re-attempted on the next run.")
                    return False # Rebuild failed, don't clear flag
        return False

    def _get_all_active_versions_live(self) -> Dict[str, str]:
        """
        Gets all active package versions in a single, fast subprocess call.
        This is much more efficient than checking one by one.
        """
        script = """
import json
import sys
import importlib.metadata
from packaging.utils import canonicalize_name

versions = {}
for dist in importlib.metadata.distributions():
    try:
        # Use the canonicalized name for consistency with omnipkg's keys
        name = canonicalize_name(dist.metadata['Name'])
        versions[name] = dist.version
    except (KeyError, TypeError):
        continue
print(json.dumps(versions))
"""
        try:
            result = subprocess.run(
                [self.config['python_executable'], '-c', script],
                capture_output=True, text=True, check=True, timeout=10
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            print(f"   ⚠️  Could not perform live bulk package scan: {e}")
            return {} # Return empty dict on failure

    def _synchronize_knowledge_base_with_reality(self):
        """
        Self-healing function. Uses a pending rebuild flag and a highly optimized
        bulk check for maximum speed and reliability.
        """
        rebuild_was_run = self._check_and_run_pending_rebuild()
        if rebuild_was_run:
            return

        print(_('🧠 Performing self-healing sync of knowledge base...'))
        
        if not self.redis_client:
            self.connect_redis()

        index_key = f'{self.redis_env_prefix}index'
        if not self.redis_client.exists(index_key):
             # This handles cases where the KB is genuinely empty and no flag was set
            if not any(Path(self.config['site_packages_path']).iterdir()):
                 print(_('   ✅ Knowledge base is empty or no packages found to sync.'))
                 return

        # --- PERFORMANCE OPTIMIZATION ---
        # Get all live versions in one fast command instead of in a loop.
        live_active_versions = self._get_all_active_versions_live()
        
        all_known_packages_in_redis = self.redis_client.smembers(index_key)
        
        # Combine keys from Redis and the live scan to check everything.
        packages_to_check = set(all_known_packages_in_redis) | set(live_active_versions.keys())
        
        if self.multiversion_base.exists():
            for bubble_dir in self.multiversion_base.iterdir():
                if bubble_dir.is_dir():
                    try:
                        dir_pkg_name, _version = bubble_dir.name.rsplit('-', 1)
                        packages_to_check.add(canonicalize_name(dir_pkg_name))
                    except ValueError:
                        continue
        
        if not packages_to_check:
            print(_('   ✅ Knowledge base is empty or no packages found to sync.'))
            return

        fixed_count = 0
        with self.redis_client.pipeline() as pipe:
            for pkg_name in packages_to_check:
                main_key = f'{self.redis_key_prefix}{pkg_name}'
                
                # Use the pre-fetched dictionary for an instant lookup
                real_active_version = live_active_versions.get(pkg_name)
                
                real_bubbled_versions = set()
                if self.multiversion_base.exists():
                    for bubble_dir in self.multiversion_base.iterdir():
                        if not bubble_dir.is_dir(): continue
                        try:
                            dir_pkg_name, version = bubble_dir.name.rsplit('-', 1)
                            if canonicalize_name(dir_pkg_name) == pkg_name:
                                real_bubbled_versions.add(version)
                        except ValueError:
                            continue
                
                cached_data = self.redis_client.hgetall(main_key)
                cached_active_version = cached_data.get('active_version')
                cached_bubbled_versions = {k.replace('bubble_version:', '') for k in cached_data if k.startswith('bubble_version:')}
                
                if real_active_version and real_active_version != cached_active_version:
                    pipe.hset(main_key, 'active_version', real_active_version)
                    fixed_count += 1
                elif not real_active_version and cached_active_version:
                    pipe.hdel(main_key, 'active_version')
                    fixed_count += 1
                
                stale_bubbles = cached_bubbled_versions - real_bubbled_versions
                for version in stale_bubbles:
                    pipe.hdel(main_key, f'bubble_version:{version}')
                    fixed_count += 1
                
                missing_bubbles = real_bubbled_versions - cached_bubbled_versions
                for version in missing_bubbles:
                    pipe.hset(main_key, f'bubble_version:{version}', 'true')
                    fixed_count += 1
            
            pipe.execute()
        
        if fixed_count > 0:
            print(_('   ✅ Sync complete. Reconciled {} discrepancies.').format(fixed_count))
        else:
            print(_('   ✅ Knowledge base is already in sync with the environment.'))

    def _update_hash_index_for_delta(self, before: Dict, after: Dict):
        """Surgically updates the cached hash index in Redis after an install."""
        if not self.redis_client:
            self.connect_redis()
        redis_key = _('{}main_env:file_hashes').format(self.redis_key_prefix)
        if not self.redis_client.exists(redis_key):
            return
        print(_('🔄 Updating cached file hash index...'))
        uninstalled_or_changed = {name: ver for name, ver in before.items() if name not in after or after[name] != ver}
        installed_or_changed = {name: ver for name, ver in after.items() if name not in before or before[name] != ver}
        with self.redis_client.pipeline() as pipe:
            for name, ver in uninstalled_or_changed.items():
                try:
                    dist = importlib.metadata.distribution(name)
                    if dist.files:
                        for file in dist.files:
                            pipe.srem(redis_key, self.bubble_manager._get_file_hash(dist.locate_file(file)))
                except (importlib.metadata.PackageNotFoundError, FileNotFoundError):
                    continue
            for name, ver in installed_or_changed.items():
                try:
                    dist = importlib.metadata.distribution(name)
                    if dist.files:
                        for file in dist.files:
                            pipe.sadd(redis_key, self.bubble_manager._get_file_hash(dist.locate_file(file)))
                except (importlib.metadata.PackageNotFoundError, FileNotFoundError):
                    continue
            pipe.execute()
        print(_('✅ Hash index updated.'))

    def get_installed_packages(self, live: bool=False) -> Dict[str, str]:
        if live:
            try:
                cmd = [self.config['python_executable'], '-m', 'pip', 'list', '--format=json']
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                live_packages = {pkg['name'].lower(): pkg['version'] for pkg in json.loads(result.stdout)}
                self._installed_packages_cache = live_packages
                return live_packages
            except Exception as e:
                print(_('    ⚠️  Could not perform live package scan: {}').format(e))
                return self._installed_packages_cache or {}
        if self._installed_packages_cache is None:
            if not self.redis_client:
                self.connect_redis()
            self._installed_packages_cache = self.redis_client.hgetall(_('{}versions').format(self.redis_key_prefix))
        return self._installed_packages_cache

    def _detect_downgrades(self, before: Dict[str, str], after: Dict[str, str]) -> List[Dict]:
        downgrades = []
        for pkg_name, old_version in before.items():
            if pkg_name in after:
                new_version = after[pkg_name]
                try:
                    if parse_version(new_version) < parse_version(old_version):
                        downgrades.append({'package': pkg_name, 'good_version': old_version, 'bad_version': new_version})
                except InvalidVersion:
                    continue
        return downgrades

    def _detect_upgrades(self, before: Dict[str, str], after: Dict[str, str]) -> List[Dict]:
        """Identifies packages that were upgraded."""
        upgrades = []
        for pkg_name, old_version in before.items():
            if pkg_name in after:
                new_version = after[pkg_name]
                try:
                    if parse_version(new_version) > parse_version(old_version):
                        upgrades.append({'package': pkg_name, 'old_version': old_version, 'new_version': new_version})
                except InvalidVersion:
                    continue
        return upgrades

    def _run_metadata_builder_for_delta(self, before: Dict, after: Dict):
        """
        FIXED: Atomically updates the knowledge base by directly invoking the metadata
        gatherer in-process for all targeted updates, mirroring the robust logic
        from the successful rebuild_knowledge_base function.
        """
        changed_specs = [f'{name}=={ver}' for name, ver in after.items() if name not in before or before[name] != ver]
        uninstalled = {name: ver for name, ver in before.items() if name not in after}
        if not changed_specs and (not uninstalled):
            print(_('✅ Knowledge base is already up to date.'))
            return
        print(_('🧠 Updating knowledge base for changes...'))
        try:
            if changed_specs:
                print(_('   -> Processing {} changed/new package(s)...').format(len(changed_specs)))
                gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, force_refresh=True)
                gatherer.redis_client = self.redis_client
                newly_active_packages = {canonicalize_name(spec.split('==')[0]): spec.split('==')[1] for spec in changed_specs if canonicalize_name(spec.split('==')[0]) in after}
                gatherer.run(targeted_packages=changed_specs, newly_active_packages=newly_active_packages)
            if uninstalled:
                print(_('   -> Cleaning up {} uninstalled package(s) from Redis...').format(len(uninstalled)))
                with self.redis_client.pipeline() as pipe:
                    for pkg_name, uninstalled_version in uninstalled.items():
                        c_name = canonicalize_name(pkg_name)
                        main_key = f'{self.redis_key_prefix}{c_name}'
                        version_key = f'{main_key}:{uninstalled_version}'
                        versions_set_key = f'{main_key}:installed_versions'
                        pipe.delete(version_key)
                        pipe.srem(versions_set_key, uninstalled_version)
                        if self.redis_client.hget(main_key, 'active_version') == uninstalled_version:
                            pipe.hdel(main_key, 'active_version')
                        pipe.hdel(main_key, f'bubble_version:{uninstalled_version}')
                    pipe.execute()
            self._info_cache.clear()
            self._installed_packages_cache = None
            print(_('✅ Knowledge base updated successfully.'))
        except Exception as e:
            print(_('    ⚠️ Failed to update knowledge base for delta: {}').format(e))
            import traceback
            traceback.print_exc()

    def show_package_info(self, package_spec: str) -> int:
        if not self.connect_redis():
            return 1
        self._synchronize_knowledge_base_with_reality()
        try:
            pkg_name, requested_version = self._parse_package_spec(package_spec)
            if requested_version:
                print(f'\n' + '=' * 60)
                print(_('📄 Detailed info for {} v{}').format(pkg_name, requested_version))
                print('=' * 60)
                self._show_version_details(pkg_name, requested_version)
            else:
                self._show_enhanced_package_data(pkg_name)
            return 0
        except Exception as e:
            print(_('❌ An unexpected error occurred while showing package info: {}').format(e))
            import traceback
            traceback.print_exc()
            return 1

    def _clean_and_format_dependencies(self, raw_deps_json: str) -> str:
        """Parses the raw dependency JSON, filters out noise, and formats it for humans."""
        try:
            deps = json.loads(raw_deps_json)
            if not deps:
                return 'None'
            core_deps = [d.split(';')[0].strip() for d in deps if ';' not in d]
            if len(core_deps) > 5:
                return _('{}, ...and {} more').format(', '.join(core_deps[:5]), len(core_deps) - 5)
            else:
                return ', '.join(core_deps)
        except (json.JSONDecodeError, TypeError):
            return 'Could not parse.'

    def _show_enhanced_package_data(self, package_name: str):
        r = self.redis_client
        overview_key = '{}{}'.format(self.redis_key_prefix, package_name.lower())
        if not r.exists(overview_key):
            print(_("\n📋 KEY DATA: No Redis data found for '{}'").format(package_name))
            return
        print(_("\n📋 KEY DATA for '{}':").format(package_name))
        print('-' * 40)
        overview_data = r.hgetall(overview_key)
        active_ver = overview_data.get('active_version', 'Not Set')
        print(_('🎯 Active Version: {}').format(active_ver))
        bubble_versions = [key.replace('bubble_version:', '') for key in overview_data if key.startswith('bubble_version:') and overview_data[key] == 'true']
        if bubble_versions:
            print(_('🫧 Bubbled Versions: {}').format(', '.join(sorted(bubble_versions))))
        available_versions = []
        if active_ver != 'Not Set':
            available_versions.append(active_ver)
        available_versions.extend(sorted(bubble_versions))
        if available_versions:
            print(_('\n📦 Available Versions:'))
            for i, ver in enumerate(available_versions, 1):
                status_indicators = []
                if ver == active_ver:
                    status_indicators.append('active')
                if ver in bubble_versions:
                    status_indicators.append('in bubble')
                status_str = f" ({', '.join(status_indicators)})" if status_indicators else ''
                print(_('  {}) {}{}').format(i, ver, status_str))
            print(_('\n💡 Want details on a specific version?'))
            try:
                choice = input(_('Enter number (1-{}) or press Enter to skip: ').format(len(available_versions)))
                if choice.strip():
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(available_versions):
                            selected_version = available_versions[idx]
                            print(f'\n' + '=' * 60)
                            print(_('📄 Detailed info for {} v{}').format(package_name, selected_version))
                            print('=' * 60)
                            self._show_version_details(package_name, selected_version)
                        else:
                            print(_('❌ Invalid selection.'))
                    except ValueError:
                        print(_('❌ Please enter a number.'))
            except KeyboardInterrupt:
                print(_('\n   Skipped.'))
        else:
            print(_('📦 No installed versions found in Redis.'))

    def get_all_versions(self, package_name: str) -> List[str]:
        """Get all versions (active + bubbled) for a package"""
        overview_key = f'{self.redis_key_prefix}{package_name.lower()}'
        overview_data = self.redis_client.hgetall(overview_key)
        active_ver = overview_data.get('active_version')
        bubble_versions = [key.replace('bubble_version:', '') for key in overview_data if key.startswith('bubble_version:') and overview_data[key] == 'true']
        versions = []
        if active_ver:
            versions.append(active_ver)
        versions.extend(bubble_versions)
        return sorted(versions, key=lambda v: v)

    def _show_version_details(self, package_name: str, version: str):
        r = self.redis_client
        version_key = f'{self.redis_key_prefix}{package_name.lower()}:{version}'
        if not r.exists(version_key):
            print(_('❌ No detailed data found for {} v{}').format(package_name, version))
            return
        data = r.hgetall(version_key)

        # --- MODIFIED LINE: Added 'path' to the list of fields ---
        important_fields = [
            ('name', '📦 Package'), ('Version', '🏷️  Version'), ('Summary', '📝 Summary'),
            ('Author', '👤 Author'), ('Author-email', '📧 Email'), ('License', '⚖️  License'),
            ('Home-page', '🌐 Homepage'), ('path', '📂 Path'), ('Platform', '💻 Platform'),
            ('dependencies', '🔗 Dependencies'), ('Requires-Dist', '📋 Requires')
        ]
        
        print(_('The data is fetched from Redis key: {}').format(version_key))
        for field_name, display_name in important_fields:
            if field_name in data:
                value = data[field_name]
                
                # Truncate long license text
                if field_name == 'License' and len(value) > 100:
                    value = value.split('\n')[0] + '... (truncated)'

                if field_name in ['dependencies', 'Requires-Dist']:
                    try:
                        dep_list = json.loads(value)
                        print(_('{}: {}').format(display_name.ljust(18), ', '.join(dep_list) if dep_list else 'None'))
                    except (json.JSONDecodeError, TypeError):
                        print(_('{}: {}').format(display_name.ljust(18), value))
                else:
                    print(_('{}: {}').format(display_name.ljust(18), value))
        security_fields = [('security.issues_found', '🔒 Security Issues'), ('security.audit_status', '🛡️  Audit Status'), ('health.import_check.importable', '✅ Importable')]
        print(_('\n---[ Health & Security ]---'))
        for field_name, display_name in security_fields:
            value = data.get(field_name, 'N/A')
            print(_('   {}: {}').format(display_name.ljust(18), value))
        meta_fields = [('last_indexed', '⏰ Last Indexed'), ('checksum', '🔐 Checksum'), ('Metadata-Version', '📋 Metadata Version')]
        print(_('\n---[ Build Info ]---'))
        for field_name, display_name in meta_fields:
            value = data.get(field_name, 'N/A')
            if field_name == 'checksum' and len(value) > 24:
                value = f'{value[:12]}...{value[-12:]}'
            print(_('   {}: {}').format(display_name.ljust(18), value))
        print(_('\n💡 For all raw data, use Redis key: "{}"').format(version_key))

    def _save_last_known_good_snapshot(self):
        """Saves the current environment state to Redis."""
        print(_("📸 Saving snapshot of the current environment as 'last known good'..."))
        try:
            current_state = self.get_installed_packages(live=True)
            snapshot_key = f'{self.redis_key_prefix}snapshot:last_known_good'
            self.redis_client.set(snapshot_key, json.dumps(current_state))
            print(_('   ✅ Snapshot saved.'))
        except Exception as e:
            print(_('   ⚠️ Could not save environment snapshot: {}').format(e))

    def _sort_packages_for_install(self, packages: List[str], strategy: str) -> List[str]:
        """
        Sorts packages for installation based on the chosen strategy.
        - 'latest-active': Sorts oldest to newest to ensure the last one installed is the latest.
        - 'stable-main': Sorts newest to oldest to minimize environmental changes.
        """
        from packaging.version import parse as parse_version, InvalidVersion
        import re

        def get_version_key(pkg_spec):
            """Extracts a sortable version key from a package spec."""
            match = re.search('(==|>=|<=|>|<|~=)(.+)', pkg_spec)
            if match:
                version_str = match.group(2).strip()
                try:
                    return parse_version(version_str)
                except InvalidVersion:
                    return parse_version('0.0.0')
            return parse_version('9999.0.0')
        should_reverse = strategy == 'stable-main'
        return sorted(packages, key=get_version_key, reverse=should_reverse)

    def adopt_interpreter(self, version: str) -> int:
        """
        Safely adopts a Python version by checking the registry, then trying to copy
        from the local system, and finally falling back to download.
        A rescan is forced after any successful filesystem change to ensure registration.
        """
        print(_('🐍 Attempting to adopt Python {} into the environment...').format(version))
        
        # First, check if it's already perfectly managed.
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        if version in managed_interpreters:
            print(_('   - ✅ Python {} is already adopted and managed.').format(version))
            return 0

        # Attempt to find it locally to copy.
        discovered_pythons = self.config_manager.list_available_pythons()
        source_path_str = discovered_pythons.get(version)
        
        if not source_path_str:
            print(_('   - No local Python {} found. Falling back to download strategy.').format(version))
            result = self._fallback_to_download(version)
            if result == 0:
                print(_('🔧 Forcing rescan to register the new interpreter...'))
                self.rescan_interpreters()
            return result
        
        source_exe_path = Path(source_path_str)
        try:
            cmd = [str(source_exe_path), '-c', 'import sys; print(sys.prefix)']
            cmd_result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            source_root = Path(os.path.realpath(cmd_result.stdout.strip()))
            current_venv_root = self.config_manager.venv_path.resolve()
            
            # Perform safety checks before attempting a copy
            if self._is_same_or_child_path(source_root, current_venv_root) or \
            not self._is_valid_python_installation(source_root, source_exe_path) or \
            self._estimate_directory_size(source_root) > 2 * 1024 * 1024 * 1024 or \
            self._is_system_critical_path(source_root):
                print(_('   - ⚠️  Safety checks failed for local copy. Falling back to download.'))
                result = self._fallback_to_download(version)
                if result == 0:
                    print(_('🔧 Forcing rescan to register the downloaded interpreter...'))
                    self.rescan_interpreters()
                return result
            
            dest_root = self.config_manager.venv_path / '.omnipkg' / 'interpreters' / f'cpython-{version}'
            if dest_root.exists():
                print(_('   - ✅ Adopted copy of Python {} already exists. Ensuring it is registered.').format(version))
                self.rescan_interpreters()
                return 0
            
            print(_('   - Starting safe copy operation...'))
            result = self._perform_safe_copy(source_root, dest_root, version)
            
            if result == 0:
                print(_('🔧 Forcing rescan to register the copied interpreter...'))
                self.rescan_interpreters()
            return result
            
        except Exception as e:
            print(_('   - ❌ An error occurred during the copy attempt: {}. Falling back to download.').format(e))
            result = self._fallback_to_download(version)
            if result == 0:
                print(_('🔧 Forcing rescan to register the downloaded interpreter...'))
                self.rescan_interpreters()
            return result

    def _is_interpreter_directory_valid(self, path: Path) -> bool:
        """
        Checks if a directory contains a valid, runnable Python interpreter structure.
        This is the core of the integrity check.
        """
        if not path.exists():
            return False
            
        # Check for Linux/macOS structure
        bin_dir = path / 'bin'
        if bin_dir.is_dir():
            # Check for any file that looks like a python executable
            for name in ['python', 'python3', 'python3.9', 'python3.10', 'python3.11', 'python3.12']:
                exe_path = bin_dir / name
                if exe_path.is_file() and os.access(exe_path, os.X_OK):
                    # Try to actually run it to verify it works
                    try:
                        result = subprocess.run([str(exe_path), '--version'], 
                                            capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            return True
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                        continue

        # Check for Windows structure
        scripts_dir = path / 'Scripts'
        if scripts_dir.is_dir():
            exe_path = scripts_dir / 'python.exe'
            if exe_path.is_file():
                try:
                    result = subprocess.run([str(exe_path), '--version'], 
                                        capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return True
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                    pass

        # Also check root directory for direct python executable
        for name in ['python', 'python.exe', 'python3', 'python3.exe']:
            exe_path = path / name
            if exe_path.is_file() and os.access(exe_path, os.X_OK):
                try:
                    result = subprocess.run([str(exe_path), '--version'], 
                                        capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return True
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                    continue

        return False # No valid structure or executable found

    def _fallback_to_download(self, version: str) -> int:
        """
        Fallback to downloading Python. This function now surgically detects an incomplete
        installation by checking for a valid executable, cleans it up if broken,
        and includes a safety stop to prevent deleting the active interpreter.
        """
        print(_('\n--- Running robust download strategy ---'))
        try:
            full_versions = {'3.12': '3.12.3', '3.10': '3.10.13', '3.9': '3.9.18', '3.11': '3.11.6'}
            full_version = full_versions.get(version)
            if not full_version:
                print(f'❌ Error: No known standalone build for Python {version}.')
                return 1

            dest_path = self.config_manager.venv_path / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'

            if dest_path.exists():
                print(_('   - Found existing directory for Python {}. Verifying integrity...').format(full_version))
                if self._is_interpreter_directory_valid(dest_path):
                    print(_('   - ✅ Integrity check passed. Installation is valid and complete.'))
                    # Even if valid, we ensure it's registered by forcing a rescan later.
                    return 0 # Success, no download needed.
                else:
                    print(_('   - ⚠️  Integrity check failed: Incomplete installation detected (missing or broken executable).'))

                    # --- CRITICAL SAFETY CHECK ---
                    # Never delete the interpreter that is currently running this script.
                    try:
                        active_interpreter_root = Path(sys.executable).resolve().parents[1]
                        if dest_path.resolve() == active_interpreter_root:
                            print(_('   - ❌ CRITICAL ERROR: The broken interpreter is the currently active one!'))
                            print(_('   - Aborting to prevent self-destruction. Please fix the environment manually.'))
                            return 1
                    except (IndexError, OSError):
                        pass # Path structure is unexpected, proceed with caution but don't block.
                    # --- END SAFETY CHECK ---

                    print(_('   - Preparing to clean up broken directory...'))
                    try:
                        shutil.rmtree(dest_path)
                        print(_('   - ✅ Removed broken directory successfully.'))
                    except Exception as e:
                        print(_("   - ❌ FATAL: Failed to remove existing broken directory: {}").format(e))
                        return 1

            # Proceed with a fresh installation into a guaranteed-to-be-clean directory.
            print(_('   - Starting fresh download and installation...'))
            
            # Call the correct method name - check your config_manager for the actual method
            if hasattr(self.config_manager, '_install_managed_python'):
                self.config_manager._install_managed_python(self.config_manager.venv_path, full_version)
            elif hasattr(self.config_manager, 'install_managed_python'):
                self.config_manager.install_managed_python(self.config_manager.venv_path, full_version)
            elif hasattr(self.config_manager, 'download_python'):
                self.config_manager.download_python(full_version)
            else:
                print(_('❌ Error: No download method found on config_manager'))
                return 1
                
            # Verify the installation worked
            if dest_path.exists() and self._is_interpreter_directory_valid(dest_path):
                print(_('   - ✅ Download and installation completed successfully.'))
                return 0
            else:
                print(_('   - ❌ Installation completed but integrity check still fails.'))
                return 1
                
        except Exception as e:
            print(_('❌ Download and installation process failed: {}').format(e))
            return 1 # Return 1 for failure

    def rescan_interpreters(self) -> int:
        """
        Forces a full, clean re-scan of the managed interpreters directory
        and rebuilds the registry from scratch. This is a repair utility.
        """
        print(_("Performing a full re-scan of managed interpreters..."))
        try:
            # We call the ConfigManager's registration function directly,
            # as it contains the ground-truth discovery logic.
            self.config_manager._register_all_interpreters(self.config_manager.venv_path)
            print(_("\n✅ Interpreter registry successfully rebuilt."))
            return 0
        except Exception as e:
            print(_("\n❌ An error occurred during the re-scan: {}").format(e))
            import traceback
            traceback.print_exc()
            return 1

    def _is_same_or_child_path(self, source: Path, target: Path) -> bool:
        """Check if source is the same as target or a child of target."""
        try:
            source = source.resolve()
            target = target.resolve()
            if source == target:
                return True
            try:
                source.relative_to(target)
                return True
            except ValueError:
                return False
        except (OSError, RuntimeError):
            return True

    def _is_valid_python_installation(self, root: Path, exe_path: Path) -> bool:
        """Validate that the source looks like a proper Python installation."""
        try:
            if not exe_path.exists():
                return False
            try:
                exe_path.resolve().relative_to(root.resolve())
            except ValueError:
                return False
            expected_dirs = ['lib', 'bin']
            if sys.platform == 'win32':
                expected_dirs = ['Lib', 'Scripts']
            has_expected_structure = any(((root / d).exists() for d in expected_dirs))
            test_cmd = [str(exe_path), '-c', 'import sys, os']
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=5)
            return has_expected_structure and test_result.returncode == 0
        except Exception:
            return False

    def _estimate_directory_size(self, path: Path, max_files_to_check: int=1000) -> int:
        """Estimate directory size with early termination for safety."""
        total_size = 0
        file_count = 0
        try:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(('.git', '__pycache__', '.mypy_cache', 'node_modules'))]
                for file in files:
                    if file_count >= max_files_to_check:
                        return total_size * 10
                    try:
                        file_path = os.path.join(root, file)
                        total_size += os.path.getsize(file_path)
                        file_count += 1
                    except (OSError, IOError):
                        continue
        except Exception:
            return float('inf')
        return total_size

    def _is_system_critical_path(self, path: Path) -> bool:
        """Check if path is a system-critical directory that shouldn't be copied."""
        critical_paths = [Path('/'), Path('/usr'), Path('/usr/local'), Path('/System'), Path('/Library'), Path('/opt'), Path('/bin'), Path('/sbin'), Path('/etc'), Path('/var'), Path('/tmp'), Path('/proc'), Path('/dev'), Path('/sys')]
        if sys.platform == 'win32':
            critical_paths.extend([Path('C:\\Windows'), Path('C:\\Program Files'), Path('C:\\Program Files (x86)'), Path('C:\\System32')])
        try:
            resolved_path = path.resolve()
            for critical in critical_paths:
                if resolved_path == critical.resolve():
                    return True
            return False
        except Exception:
            return True

    def _perform_safe_copy(self, source: Path, dest: Path, version: str) -> int:
        """Perform the actual copy operation with additional safety measures."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)

            def ignore_patterns(dir, files):
                ignored = []
                for file in files:
                    if file in {'.git', '__pycache__', '.mypy_cache', '.pytest_cache', '.tox', '.coverage', 'node_modules', '.DS_Store'}:
                        ignored.append(file)
                    try:
                        filepath = os.path.join(dir, file)
                        if os.path.isfile(filepath) and os.path.getsize(filepath) > 50 * 1024 * 1024:
                            ignored.append(file)
                    except OSError:
                        pass
                return ignored
            print(_('   - Copying {} -> {}').format(source, dest))
            shutil.copytree(source, dest, symlinks=True, ignore=ignore_patterns, dirs_exist_ok=False)
            copied_python = self._find_python_executable_in_dir(dest)
            if not copied_python or not copied_python.exists():
                print(_('   - ❌ Copy completed but Python executable not found in destination'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            test_cmd = [str(copied_python), '-c', 'import sys; print(sys.version)']
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode != 0:
                print(_('   - ❌ Copied Python executable failed basic test'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            print(_('   - ✅ Copy successful and verified!'))
            self.config_manager._register_all_interpreters(self.config_manager.venv_path)
            print(f'\n🎉 Successfully adopted Python {version} from local source!')
            print(_("   You can now use 'omnipkg swap python {}'").format(version))
            return 0
        except Exception as e:
            print(_('   - ❌ Copy operation failed: {}').format(e))
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            return self._fallback_to_download(version)

    def _find_python_executable_in_dir(self, directory: Path) -> Path:
        """Find the Python executable in a copied directory."""
        possible_names = ['python', 'python3', 'python.exe']
        possible_dirs = ['bin', 'Scripts', '.']
        for subdir in possible_dirs:
            for name in possible_names:
                candidate = directory / subdir / name
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None

    def _get_redis_key_prefix_for_version(self, version: str) -> str:
        """Generates the Redis key prefix for a specific Python version string."""
        py_ver_str = f"py{version}"
        # This logic correctly reconstructs the prefix for any given version
        base_prefix = self.config.get('redis_key_prefix', 'omnipkg:pkg:')
        base = base_prefix.split(':')[0]
        return f'{base}:env_{self.config_manager.env_id}:{py_ver_str}:pkg:'

    def remove_interpreter(self, version: str, force: bool = False) -> int:
        """
        Forcefully removes a managed Python interpreter directory, purges its
        knowledge base from Redis, and updates the registry.
        """
        print(f"🔥 Attempting to remove managed Python interpreter: {version}")
        
        # Safety check: prevent deleting the active interpreter
        active_python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        if version == active_python_version:
            print(f"❌ SAFETY LOCK: Cannot remove the currently active Python interpreter ({version}).")
            print("   Switch to a different Python version first using 'omnipkg swap python <other_version>'.")
            return 1

        # Find the interpreter's directory path
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        interpreter_path = managed_interpreters.get(version)
        if not interpreter_path:
            print(f"🤷 Error: Python version {version} is not a known managed interpreter.")
            return 1
        
        # The physical directory is two levels above the executable (e.g., .../cpython-3.9.18/bin/python)
        interpreter_root_dir = interpreter_path.parent.parent

        print(f"   Target directory for deletion: {interpreter_root_dir}")
        if not interpreter_root_dir.exists():
            print("   Directory does not exist. It may have already been cleaned up.")
            self.rescan_interpreters()
            return 0

        if not force:
            confirm = input(f"🤔 Are you sure you want to permanently delete this directory? (y/N): ").lower().strip()
            if confirm != 'y':
                print("🚫 Removal cancelled.")
                return 1

        # 1. Delete the directory from the filesystem
        try:
            print(f"🗑️ Deleting directory: {interpreter_root_dir}")
            shutil.rmtree(interpreter_root_dir)
            print("✅ Directory removed successfully.")
        except Exception as e:
            print(f"❌ Failed to remove directory: {e}")
            return 1
        
        # --- START NEW LOGIC: Purge Redis ---
        print(f"🧹 Cleaning up Knowledge Base for Python {version}...")
        try:
            # Get the specific key pattern for the version we just removed
            keys_to_delete_pattern = self._get_redis_key_prefix_for_version(version) + '*'
            
            keys = self.redis_client.keys(keys_to_delete_pattern)
            
            if keys:
                print(f"   -> Found {len(keys)} stale entries in Redis. Purging...")
                delete_command = self.redis_client.unlink if hasattr(self.redis_client, 'unlink') else self.redis_client.delete
                delete_command(*keys)
                print(f"   ✅ Knowledge Base for Python {version} has been purged.")
            else:
                print(f"   ✅ No Knowledge Base entries found for Python {version}. Nothing to clean.")

        except Exception as e:
            print(f"   ⚠️  Warning: Could not clean up Knowledge Base for Python {version}: {e}")
        # --- END NEW LOGIC ---
        
        # 2. Rescan the filesystem to update the registry
        print("🔧 Rescanning interpreters to update the registry...")
        self.rescan_interpreters()
        return 0

    def smart_install(self, packages: List[str], dry_run: bool=False) -> int:
        if not self.connect_redis():
            return 1
        if dry_run:
            print(_('🔬 Running in --dry-run mode. No changes will be made.'))
            return 0
        if not packages:
            print('🚫 No packages specified for installation.')
            return 1
        
        install_strategy = self.config.get('install_strategy', 'stable-main')
        packages_to_process = list(packages)
        
        # Handle omnipkg special case
        for pkg_spec in list(packages_to_process):
            pkg_name, requested_version = self._parse_package_spec(pkg_spec)
            self._synchronize_knowledge_base_with_reality()
            if pkg_name.lower() == 'omnipkg':
                packages_to_process.remove(pkg_spec)
                active_omnipkg_version = self._get_active_version_from_environment('omnipkg')
                if not active_omnipkg_version:
                    print('⚠️ Warning: Cannot determine active omnipkg version. Proceeding with caution.')
                if requested_version and active_omnipkg_version and (parse_version(requested_version) == parse_version(active_omnipkg_version)):
                    print('✅ omnipkg=={} is already the active omnipkg. No bubble needed.'.format(requested_version))
                    continue
                
                print("✨ Special handling: omnipkg '{}' requested.".format(pkg_spec))
                if not requested_version:
                    print("  Skipping bubbling of 'omnipkg' without a specific version for now.")
                    continue
                
                bubble_dir_name = 'omnipkg-{}'.format(requested_version)
                target_bubble_path = Path(self.config['multiversion_base']) / bubble_dir_name
                wheel_url = self.get_wheel_url_from_pypi(pkg_name, requested_version)
                if not wheel_url:
                    print('❌ Could not find a compatible wheel for omnipkg=={}. Cannot create bubble.'.format(requested_version))
                    continue
                if not self.extract_wheel_into_bubble(wheel_url, target_bubble_path, pkg_name, requested_version):
                    print('❌ Failed to create bubble for omnipkg=={}.'.format(requested_version))
                    continue
                
                self.register_package_in_knowledge_base(pkg_name, requested_version, str(target_bubble_path), 'bubble')
                print('✅ omnipkg=={} successfully bubbled.'.format(requested_version))
                
                # KB update for bubbled omnipkg
                fake_before = {}
                fake_after = {pkg_name: requested_version}
                self.run_metadata_builder_for_delta(fake_before, fake_after)
        
        if not packages_to_process:
            print(_('\n🎉 All package operations complete.'))
            return 0
        
        print("🚀 Starting install with policy: '{}'".format(install_strategy))
        resolved_packages = self._resolve_package_versions(packages_to_process)
        if not resolved_packages:
            print(_('❌ Could not resolve any packages to install. Aborting.'))
            return 1
        
        sorted_packages = self._sort_packages_for_install(resolved_packages, strategy=install_strategy)
        if sorted_packages != resolved_packages:
            print('🔄 Reordered packages for optimal installation: {}'.format(', '.join(sorted_packages)))
        
        user_requested_cnames = {canonicalize_name(self._parse_package_spec(p)[0]) for p in packages}
        any_installations_made = False
        
        # Batch tracking for consolidated KB updates
        main_env_kb_updates = {}  # {pkg_name: version}
        bubbled_kb_updates = {}   # {pkg_name: version}
        kb_deletions = set()      # package names to delete from KB first
        
        for package_spec in sorted_packages:
            print('\n' + '─' * 60)
            print('📦 Processing: {}'.format(package_spec))
            print('─' * 60)
            satisfaction_check = self._check_package_satisfaction([package_spec], strategy=install_strategy)
            if satisfaction_check['all_satisfied']:
                print('✅ Requirement already satisfied: {}'.format(package_spec))
                continue
            
            packages_to_install = satisfaction_check['needs_install']
            if not packages_to_install:
                continue
            
            packages_before = self.get_installed_packages(live=True)
            
            print('⚙️ Running pip install for: {}...'.format(', '.join(packages_to_install)))
            return_code = self._run_pip_install(packages_to_install)
            if return_code != 0:
                print('❌ Pip installation failed for {}. Continuing...'.format(package_spec))
                continue
            
            any_installations_made = True
            packages_after = self.get_installed_packages(live=True)
            
            # Handle version replacements - mark for KB deletion
            replacements = self._detect_version_replacements(packages_before, packages_after)
            if replacements:
                for rep in replacements:
                    kb_deletions.add(rep['package'])
                    self._cleanup_version_from_kb(rep['package'], rep['old_version'])
            
            if install_strategy == 'stable-main':
                downgrades_to_fix = self._detect_downgrades(packages_before, packages_after)
                upgrades_to_fix = self._detect_upgrades(packages_before, packages_after)
                all_changes_to_fix = []
                for fix in downgrades_to_fix:
                    all_changes_to_fix.append({'package': fix['package'], 'old_version': fix['good_version'], 'new_version': fix['bad_version'], 'change_type': 'downgraded'})
                for fix in upgrades_to_fix:
                    all_changes_to_fix.append({'package': fix['package'], 'old_version': fix['old_version'], 'new_version': fix['new_version'], 'change_type': 'upgraded'})
                
                if all_changes_to_fix:
                    print(_('🛡️ STABILITY PROTECTION ACTIVATED!'))
                    
                    # Summarize the initial cleanup
                    replaced_packages_count = len({fix['package'] for fix in all_changes_to_fix})
                    print(f"   -> Found {replaced_packages_count} package(s) downgraded by pip. Bubbling them to preserve stability...")

                    # Build the hash index only once for efficiency - REMOVED quiet=True
                    main_env_hashes = self.bubble_manager._get_or_build_main_env_hash_index()

                    for fix in all_changes_to_fix:
                        # Call bubble creation - REMOVED quiet=True
                        bubble_created = self.bubble_manager.create_isolated_bubble(
                            fix['package'], fix['new_version']
                        )
                        
                        if bubble_created:
                            bubbled_kb_updates[fix['package']] = fix['new_version']
                            bubble_path_str = str(self.multiversion_base / f"{fix['package']}-{fix['new_version']}")
                            self.hook_manager.refresh_bubble_map(fix['package'], fix['new_version'], bubble_path_str)
                            self.hook_manager.validate_bubble(fix['package'], fix['new_version'])
                            
                            # Restore to stable version
                            restore_result = subprocess.run([self.config['python_executable'], '-m', 'pip', 'install', '--quiet', f"{fix['package']}=={fix['old_version']}"], capture_output=True, text=True)
                            if restore_result.returncode == 0:
                                main_env_kb_updates[fix['package']] = fix['old_version']
                                # Print ONE summary line per action
                                print('   ✅ Bubbled {} v{}, restored stable v{}'.format(fix['package'], fix['new_version'], fix['old_version']))
                            else:
                                print('   ❌ Failed to restore {} v{}'.format(fix['package'], fix['old_version']))
                        else:
                            print('   ❌ Failed to create bubble for {} v{}'.format(fix['package'], fix['new_version']))
                    print("   -> Stability protection complete.")
                else:
                    # No changes to existing packages, just add new ones
                    for pkg_name, version in packages_after.items():
                        if pkg_name not in packages_before:
                            main_env_kb_updates[pkg_name] = version
            
            elif install_strategy == 'latest-active':
                versions_to_bubble = []
                for pkg_name in set(packages_before.keys()) | set(packages_after.keys()):
                    old_version = packages_before.get(pkg_name)
                    new_version = packages_after.get(pkg_name)
                    if old_version and new_version and (old_version != new_version):
                        change_type = 'upgraded' if parse_version(new_version) > parse_version(old_version) else 'downgraded'
                        versions_to_bubble.append({
                            'package': pkg_name, 
                            'version_to_bubble': old_version, 
                            'version_staying_active': new_version, 
                            'change_type': change_type, 
                            'user_requested': canonicalize_name(pkg_name) in user_requested_cnames
                        })
                    elif not old_version and new_version:
                        main_env_kb_updates[pkg_name] = new_version
                
                if versions_to_bubble:
                    print(_('🛡️ LATEST-ACTIVE STRATEGY: Preserving replaced versions'))
                    for item in versions_to_bubble:
                        bubble_created = self.bubble_manager.create_isolated_bubble(item['package'], item['version_to_bubble'])
                        if bubble_created:
                            bubbled_kb_updates[item['package']] = item['version_to_bubble']
                            bubble_path_str = str(self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}")
                            self.hook_manager.refresh_bubble_map(item['package'], item['version_to_bubble'], bubble_path_str)
                            self.hook_manager.validate_bubble(item['package'], item['version_to_bubble'])
                            main_env_kb_updates[item['package']] = item['version_staying_active']
                            print('    ✅ Bubbled {} v{}, keeping v{} active'.format(item['package'], item['version_to_bubble'], item['version_staying_active']))
                        else:
                            print('    ❌ Failed to bubble {} v{}'.format(item['package'], item['version_to_bubble']))
        
        if not any_installations_made:
            print(_('\n✅ All requirements were already satisfied.'))
            self._synchronize_knowledge_base_with_reality()
            return 0
        
        # Consolidated KB updates - single targeted operation
        print('\n🧠 Updating knowledge base (consolidated)...')
        
        # 1. Consolidate all packages that require a KB update
        all_changed_specs = set()
        
        # Get the final state of the environment after all pip operations
        final_main_state = self.get_installed_packages(live=True)
        
        # Find everything that's new or different compared to the start
        initial_packages_before = self.get_installed_packages(live=True) if not any_installations_made else packages_before
        for name, ver in final_main_state.items():
            if name not in initial_packages_before or initial_packages_before[name] != ver:
                all_changed_specs.add(f'{name}=={ver}')
        
        # Add all the newly bubbled packages to the list
        for pkg_name, version in bubbled_kb_updates.items():
            all_changed_specs.add(f"{pkg_name}=={version}")
        
        # 2. Run a single, consolidated metadata build for ALL changes
        if all_changed_specs:
            print('    Targeting {} package(s) for KB update...'.format(len(all_changed_specs)))
            try:
                from .package_meta_builder import omnipkgMetadataGatherer
                gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, force_refresh=True)
                gatherer.redis_client = self.redis_client
                
                # Run the builder on our consolidated list of specific package==version specs
                gatherer.run(targeted_packages=list(all_changed_specs))
                
                # Invalidate caches and update the hash index after the successful build
                self._info_cache.clear()
                self._installed_packages_cache = None
                self._update_hash_index_for_delta(initial_packages_before, final_main_state)
                print('    ✅ Knowledge base updated successfully.')
            except Exception as e:
                print('    ⚠️ Failed to run consolidated knowledge base update: {}'.format(e))
                import traceback
                traceback.print_exc()
        else:
            print('    ✅ Knowledge base is already up to date.')
        
        # Final cleanup of redundant bubbles
        print('\n🧹 Cleaning redundant bubbles...')
        final_active_packages = self.get_installed_packages(live=True)
        cleaned_count = 0
        for pkg_name, active_version in final_active_packages.items():
            bubble_path = self.multiversion_base / f'{pkg_name}-{active_version}'
            if bubble_path.exists() and bubble_path.is_dir():
                try:
                    import shutil
                    shutil.rmtree(bubble_path)
                    cleaned_count += 1
                    if hasattr(self, 'hook_manager'):
                        self.hook_manager.remove_bubble_from_tracking(pkg_name, active_version)
                except Exception as e:
                    print(_('    ❌ Failed to remove bubble directory: {}').format(e))
        
        if cleaned_count > 0:
            print('    ✅ Removed {} redundant bubbles'.format(cleaned_count))
        
        print('\n🎉 All package operations complete.')
        self._save_last_known_good_snapshot()
        self._synchronize_knowledge_base_with_reality()
        return 0

    def _get_active_version_from_environment(self, pkg_name: str) -> Optional[str]:
        """
        Gets the version of a package actively installed in the current Python environment
        using pip show.
        """
        try:
            result = subprocess.run([sys.executable, '-m', 'pip', 'show', pkg_name], capture_output=True, text=True, check=True)
            output = result.stdout
            for line in output.splitlines():
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
            return None
        except subprocess.CalledProcessError:
            return None
        except Exception as e:
            print(_('Error getting active version of {}: {}').format(pkg_name, e))
            return None

    def _detect_version_replacements(self, before: Dict, after: Dict) -> List[Dict]:
        """
        Identifies packages that were replaced (uninstalled and a new version installed).
        This is different from a simple upgrade/downgrade list.
        """
        replacements = []
        for pkg_name, old_version in before.items():
            if pkg_name in after and after[pkg_name] != old_version:
                replacements.append({'package': pkg_name, 'old_version': old_version, 'new_version': after[pkg_name]})
        return replacements

    def _cleanup_version_from_kb(self, package_name: str, version: str):
        """
        Surgically removes all traces of a single, specific version of a package
        from the Redis knowledge base.
        """
        print(_('   -> Cleaning up replaced version from knowledge base: {} v{}').format(package_name, version))
        c_name = canonicalize_name(package_name)
        main_key = f'{self.redis_key_prefix}{c_name}'
        version_key = f'{main_key}:{version}'
        versions_set_key = f'{main_key}:installed_versions'
        with self.redis_client.pipeline() as pipe:
            pipe.delete(version_key)
            pipe.srem(versions_set_key, version)
            pipe.hdel(main_key, f'bubble_version:{version}')
            if self.redis_client.hget(main_key, 'active_version') == version:
                pipe.hdel(main_key, 'active_version')
            pipe.execute()

    def _restore_from_snapshot(self, snapshot: Dict, current_state: Dict):
        """Restores the main environment to the exact state of a given snapshot."""
        print(_('🔄 Restoring main environment from snapshot...'))
        snapshot_keys = set(snapshot.keys())
        current_keys = set(current_state.keys())
        to_uninstall = [pkg for pkg in current_keys if pkg not in snapshot_keys]
        to_install_or_fix = ['{}=={}'.format(pkg, ver) for pkg, ver in snapshot.items() if pkg not in current_keys or current_state.get(pkg) != ver]
        if not to_uninstall and (not to_install_or_fix):
            print(_('   ✅ Environment is already in its original state.'))
            return
        if to_uninstall:
            print(_('   -> Uninstalling: {}').format(', '.join(to_uninstall)))
            self._run_pip_uninstall(to_uninstall)
        if to_install_or_fix:
            print(_('   -> Installing/Fixing: {}').format(', '.join(to_install_or_fix)))
            self._run_pip_install(to_install_or_fix + ['--no-deps'])
        print(_('   ✅ Environment restored.'))

    def _extract_wheel_into_bubble(self, wheel_url: str, target_bubble_path: Path, pkg_name: str, pkg_version: str) -> bool:
        """
        Downloads a wheel and extracts its content directly into a bubble directory.
        Does NOT use pip install.
        """
        print(_('📦 Downloading wheel for {}=={}...').format(pkg_name, pkg_version))
        try:
            response = self.http_session.get(wheel_url, stream=True)
            response.raise_for_status()
            target_bubble_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                for member in zf.namelist():
                    if member.startswith((_('{}-{}.dist-info').format(pkg_name, pkg_version), _('{}-{}.data').format(pkg_name, pkg_version))):
                        continue
                    try:
                        zf.extract(member, target_bubble_path)
                    except Exception as extract_error:
                        print(_('⚠️ Warning: Could not extract {}: {}').format(member, extract_error))
                        continue
            print(_('✅ Extracted {}=={} to {}').format(pkg_name, pkg_version, target_bubble_path.name))
            return True
        except http_requests.exceptions.RequestException as e:
            print(_('❌ Failed to download wheel from {}: {}').format(wheel_url, e))
            return False
        except zipfile.BadZipFile:
            print(_('❌ Downloaded file is not a valid wheel: {}').format(wheel_url))
            return False
        except Exception as e:
            print(_('❌ Error extracting wheel for {}=={}: {}').format(pkg_name, pkg_version, e))
            return False

    def _get_wheel_url_from_pypi(self, pkg_name: str, pkg_version: str) -> Optional[str]:
        """Fetches the wheel URL for a specific package version from PyPI."""
        pypi_url = f'https://pypi.org/pypi/{pkg_name}/{pkg_version}/json'
        try:
            response = self.http_session.get(pypi_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            py_major = sys.version_info.major
            py_minor = sys.version_info.minor
            wheel_priorities = [lambda f: f'py{py_major}{py_minor}' in f and 'manylinux' in f, lambda f: any((compat in f for compat in [f'py{py_major}', 'py2.py3', 'py3'])) and 'manylinux' in f, lambda f: 'py2.py3-none-any' in f or 'py3-none-any' in f, lambda f: True]
            for priority_check in wheel_priorities:
                for url_info in data.get('urls', []):
                    if url_info['packagetype'] == 'bdist_wheel' and priority_check(url_info['filename']):
                        print(_('🎯 Found compatible wheel: {}').format(url_info['filename']))
                        return url_info['url']
            for url_info in data.get('urls', []):
                if url_info['packagetype'] == 'sdist':
                    print(_('⚠️ Only source distribution available for {}=={}').format(pkg_name, pkg_version))
                    print(_('   This may require compilation and is not recommended for bubbling.'))
                    return None
            print(_('❌ No compatible wheel or source found for {}=={} on PyPI.').format(pkg_name, pkg_version))
            return None
        except http_requests.exceptions.RequestException as e:
            print(_('❌ Failed to fetch PyPI data for {}=={}: {}').format(pkg_name, pkg_version, e))
            return None
        except KeyError as e:
            print(_('❌ Unexpected PyPI response structure: missing {}').format(e))
            return None
        except Exception as e:
            print(_('❌ Error parsing PyPI data: {}').format(e))
            return None

    def _parse_package_spec(self, pkg_spec: str) -> tuple[str, Optional[str]]:
        """
        Parse a package specification like 'package==1.0.0' or 'package>=2.0'
        Returns (package_name, version) where version is None if no version specified.
        """
        version_separators = ['==', '>=', '<=', '>', '<', '~=', '!=']
        for separator in version_separators:
            if separator in pkg_spec:
                parts = pkg_spec.split(separator, 1)
                if len(parts) == 2:
                    pkg_name = parts[0].strip()
                    version = parts[1].strip()
                    if separator == '==':
                        return (pkg_name, version)
                    else:
                        print(_("⚠️ Version specifier '{}' detected in '{}'. Exact version required for bubbling.").format(separator, pkg_spec))
                        return (pkg_name, None)
        return (pkg_spec.strip(), None)

    def _register_package_in_knowledge_base(self, pkg_name: str, version: str, bubble_path: str, install_type: str):
        """
        Register a bubbled package in the knowledge base.
        This integrates with your existing knowledge base system.
        """
        try:
            package_info = {'name': pkg_name, 'version': version, 'install_type': install_type, 'path': bubble_path, 'created_at': self._get_current_timestamp()}
            key = 'package:{}:{}'.format(pkg_name, version)
            if hasattr(self, 'redis_client') and self.redis_client:
                import json
                self.redis_client.set(key, json.dumps(package_info))
                print(_('📝 Registered {}=={} in knowledge base').format(pkg_name, version))
            else:
                print(_('⚠️ Could not register {}=={}: No Redis connection').format(pkg_name, version))
        except Exception as e:
            print(_('❌ Failed to register {}=={} in knowledge base: {}').format(pkg_name, version, e))

    def _get_current_timestamp(self) -> str:
        """Helper to get current timestamp for knowledge base entries."""
        import datetime
        return datetime.datetime.now().isoformat()

    def _find_package_installations(self, package_name: str) -> List[Dict]:
        """
        Find all installations of a package by querying the Redis knowledge base.
        This is the single source of truth for omnipkg's state.
        """
        found = []
        c_name = canonicalize_name(package_name)
        main_key = f'{self.redis_key_prefix}{c_name}'
        package_data = self.redis_client.hgetall(main_key)
        if not package_data:
            return []
        for key, value in package_data.items():
            if key == 'active_version':
                found.append({'name': package_data.get('name', c_name), 'version': value, 'type': 'active', 'path': 'Main Environment'})
            elif key.startswith('bubble_version:') and value == 'true':
                version = key.replace('bubble_version:', '')
                bubble_path = self.multiversion_base / '{}-{}'.format(package_data.get('name', c_name), version)
                found.append({'name': package_data.get('name', c_name), 'version': version, 'type': 'bubble', 'path': str(bubble_path)})
        return found

    def smart_uninstall(self, packages: List[str], force: bool=False, install_type: Optional[str]=None) -> int:
        if not self.connect_redis():
            return 1
        self._synchronize_knowledge_base_with_reality()
        
        # ✅ Fetch dependencies dynamically at the start of the method
        core_deps = _get_core_dependencies()
        
        for pkg_spec in packages:
            print(_('\nProcessing uninstall for: {}').format(pkg_spec))
            pkg_name, specific_version = self._parse_package_spec(pkg_spec)
            exact_pkg_name = canonicalize_name(pkg_name)
            all_installations_found = self._find_package_installations(exact_pkg_name)
            if all_installations_found:
                all_installations_found.sort(key=lambda x: (x['type'] != 'active', parse_version(x.get('version', '0'))), reverse=False)
            if not all_installations_found:
                print(_("🤷 Package '{}' not found.").format(pkg_name))
                continue
            to_uninstall = all_installations_found
            if specific_version:
                to_uninstall = [inst for inst in to_uninstall if inst['version'] == specific_version]
                if not to_uninstall:
                    print(_("🤷 Version '{}' of '{}' not found.").format(specific_version, pkg_name))
                    continue
            if install_type:
                to_uninstall = [inst for inst in to_uninstall if inst['type'] == install_type]
                if not to_uninstall:
                    print(_('🤷 No installations match the specified criteria.').format(pkg_name))
                    continue
            elif not force and len(all_installations_found) > 1 and (not (specific_version or install_type)):
                print(_("Found multiple installations for '{}':").format(pkg_name))
                numbered_installations = []
                for i, inst in enumerate(to_uninstall):
                    # Using the local core_deps variable
                    is_protected = inst['type'] == 'active' and (canonicalize_name(inst['name']) == 'omnipkg' or canonicalize_name(inst['name']) in core_deps)
                    status_tags = [inst['type']]
                    if is_protected:
                        status_tags.append('PROTECTED')
                    numbered_installations.append({'index': i + 1, 'installation': inst, 'is_protected': is_protected})
                    print(_('  {}) v{} ({})').format(i + 1, inst['version'], ', '.join(status_tags)))
                if not numbered_installations:
                    print(_('🤷 No versions available for selection.'))
                    continue
                try:
                    choice = input(_("🤔 Enter numbers to uninstall (e.g., '1,2'), 'all', or press Enter to cancel: ")).lower().strip()
                    if not choice:
                        print(_('🚫 Uninstall cancelled.'))
                        continue
                    selected_indices = []
                    if choice == 'all':
                        selected_indices = [item['index'] for item in numbered_installations if not item['is_protected']]
                    else:
                        try:
                            selected_indices = {int(idx.strip()) for idx in choice.split(',')}
                        except ValueError:
                            print(_('❌ Invalid input.'))
                            continue
                    to_uninstall = [item['installation'] for item in numbered_installations if item['index'] in selected_indices]
                except (KeyboardInterrupt, EOFError):
                    print(_('\n🚫 Uninstall cancelled.'))
                    continue
            final_to_uninstall = []
            for item in to_uninstall:
                # Using the local core_deps variable
                is_protected = item['type'] == 'active' and (canonicalize_name(item['name']) == 'omnipkg' or canonicalize_name(item['name']) in core_deps)
                if is_protected:
                    print(_('⚠️  Skipping protected package: {} v{} (active)').format(item['name'], item['version']))
                else:
                    final_to_uninstall.append(item)
            if not final_to_uninstall:
                print(_('🤷 No versions selected for uninstallation after protection checks.'))
                continue
            print(_("\nPreparing to remove {} installation(s) for '{}':").format(len(final_to_uninstall), exact_pkg_name))
            for item in final_to_uninstall:
                print(_('  - v{} ({})').format(item['version'], item['type']))
            if not force:
                confirm = input(_('🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
                if confirm != 'y':
                    print(_('🚫 Uninstall cancelled.'))
                    continue
            for item in final_to_uninstall:
                if item['type'] == 'active':
                    print(_("🗑️ Uninstalling '{}=={}' from main environment via pip...").format(item['name'], item['version']))
                    self._run_pip_uninstall([f"{item['name']}=={item['version']}"])
                elif item['type'] == 'bubble':
                    bubble_dir = Path(item['path'])
                    if bubble_dir.exists():
                        print(_('🗑️  Deleting bubble directory: {}').format(bubble_dir.name))
                        shutil.rmtree(bubble_dir)
                print(_('🧹 Cleaning up knowledge base for {} v{}...').format(item['name'], item['version']))
                c_name = canonicalize_name(item['name'])
                main_key = f'{self.redis_key_prefix}{c_name}'
                version_key = f"{main_key}:{item['version']}"
                versions_set_key = _('{}:installed_versions').format(main_key)
                with self.redis_client.pipeline() as pipe:
                    pipe.delete(version_key)
                    pipe.srem(versions_set_key, item['version'])
                    if item['type'] == 'active':
                        pipe.hdel(main_key, 'active_version')
                    else:
                        pipe.hdel(main_key, f"bubble_version:{item['version']}")
                    pipe.execute()
                if self.redis_client.scard(versions_set_key) == 0:
                    print(_("    -> Last version of '{}' removed. Deleting all traces.").format(c_name))
                    self.redis_client.delete(main_key, versions_set_key)
                    self.redis_client.srem(f'{self.redis_key_prefix}index', c_name)
            print(_('✅ Uninstallation complete.'))
            self._save_last_known_good_snapshot()
        return 0



    def revert_to_last_known_good(self, force: bool=False):
        """Compares the current env to the last snapshot and restores it."""
        if not self.connect_redis():
            return 1
        snapshot_key = f'{self.redis_key_prefix}snapshot:last_known_good'
        snapshot_data = self.redis_client.get(snapshot_key)
        if not snapshot_data:
            print(_("❌ No 'last known good' snapshot found. Cannot revert."))
            print(_('   Run an `omnipkg install` or `omnipkg uninstall` command to create one.'))
            return 1
        print(_('⚖️  Comparing current environment to the last known good snapshot...'))
        snapshot_state = json.loads(snapshot_data)
        current_state = self.get_installed_packages(live=True)
        snapshot_keys = set(snapshot_state.keys())
        current_keys = set(current_state.keys())
        to_install = ['{}=={}'.format(pkg, ver) for pkg, ver in snapshot_state.items() if pkg not in current_keys]
        to_uninstall = [pkg for pkg in current_keys if pkg not in snapshot_keys]
        to_fix = [f'{pkg}=={snapshot_state[pkg]}' for pkg in snapshot_keys & current_keys if snapshot_state[pkg] != current_state[pkg]]
        if not to_install and (not to_uninstall) and (not to_fix):
            print(_('✅ Your environment is already in the last known good state. No action needed.'))
            return 0
        print(_('\n📝 The following actions will be taken to restore the environment:'))
        if to_uninstall:
            print(_('  - Uninstall: {}').format(', '.join(to_uninstall)))
        if to_install:
            print(_('  - Install: {}').format(', '.join(to_install)))
        if to_fix:
            print(_('  - Fix Version: {}').format(', '.join(to_fix)))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                print(_('🚫 Revert cancelled.'))
                return 1
        print(_('\n🚀 Starting revert operation...'))
        original_strategy = self.config.get('install_strategy', 'multiversion')
        strategy_changed = False
        try:
            if original_strategy != 'latest-active':
                print(_('   ⚙️  Temporarily setting install strategy to latest-active for revert...'))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', 'latest-active'], capture_output=True, text=True, check=True)
                    strategy_changed = True
                    print(_('   ✅ Install strategy temporarily set to latest-active'))
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                except Exception as e:
                    print(_('   ⚠️  Failed to set install strategy to latest-active: {}').format(e))
                    print(_('   ℹ️  Continuing with current strategy: {}').format(original_strategy))
            else:
                print(_('   ℹ️  Install strategy already set to latest-active'))
            if to_uninstall:
                self.smart_uninstall(to_uninstall, force=True)
            packages_to_install = to_install + to_fix
            if packages_to_install:
                self.smart_install(packages_to_install)
            print(_('\n✅ Environment successfully reverted to the last known good state.'))
            return 0
        finally:
            if strategy_changed and original_strategy != 'latest-active':
                print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', original_strategy], capture_output=True, text=True, check=True)
                    print(_('   ✅ Install strategy restored to: {}').format(original_strategy))
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                except Exception as e:
                    print(_('   ⚠️  Failed to restore install strategy to {}: {}').format(original_strategy, e))
                    print(_('   💡 You may need to manually restore it with: omnipkg config set install_strategy {}').format(original_strategy))
            elif not strategy_changed:
                print(_('   ℹ️  Install strategy unchanged: {}').format(original_strategy))

    def _check_package_satisfaction(self, packages: List[str], strategy: str) -> dict:
        """
        ### THE DEFINITIVE FIX ###
        Checks if a list of requirements is satisfied by querying the Redis Knowledge Base,
        which is the single source of truth for omnipkg.
        """
        satisfied_specs = set()
        needs_install_specs = []
        for package_spec in packages:
            is_satisfied = False
            try:
                pkg_name, requested_version = self._parse_package_spec(package_spec)
                if not requested_version:
                    needs_install_specs.append(package_spec)
                    continue
                c_name = canonicalize_name(pkg_name)
                main_key = f'{self.redis_key_prefix}{c_name}'
                version_key = f'{main_key}:{requested_version}'
                if not self.redis_client.exists(version_key):
                    needs_install_specs.append(package_spec)
                    continue
                package_data = self.redis_client.hgetall(main_key)
                if package_data.get('active_version') == requested_version:
                    is_satisfied = True
                if not is_satisfied and strategy == 'stable-main':
                    if package_data.get(f'bubble_version:{requested_version}') == 'true':
                        is_satisfied = True
                if is_satisfied:
                    satisfied_specs.add(package_spec)
                else:
                    needs_install_specs.append(package_spec)
            except Exception:
                needs_install_specs.append(package_spec)
        return {'all_satisfied': len(needs_install_specs) == 0, 'satisfied': sorted(list(satisfied_specs)), 'needs_install': needs_install_specs}

    def get_package_info(self, package_name: str, version: str) -> Optional[Dict]:
        if not self.redis_client:
            self.connect_redis()
        main_key = f'{self.redis_key_prefix}{package_name.lower()}'
        if version == 'active':
            version = self.redis_client.hget(main_key, 'active_version')
            if not version:
                return None
        version_key = f'{main_key}:{version}'
        return self.redis_client.hgetall(version_key)

    def switch_active_python(self, version: str) -> int:
        """
        Switches the active Python context for the entire environment.
        This updates the config file and the default `python` symlinks.
        """
        print(_('🐍 Switching active Python context to version {}...').format(version))
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        target_interpreter_path = managed_interpreters.get(version)
        if not target_interpreter_path:
            print(_('❌ Error: Python version {} is not managed by this environment.').format(version))
            print(_("   Run 'omnipkg list python' to see managed interpreters."))
            print(f"   If Python {version} is 'Discovered', first adopt it with: omnipkg python adopt {version}")
            return 1
        target_interpreter_str = str(target_interpreter_path)
        print(_('   - Found managed interpreter at: {}').format(target_interpreter_str))
        new_paths = self.config_manager._get_paths_for_interpreter(target_interpreter_str)
        if not new_paths:
            print(f'❌ Error: Could not determine paths for Python {version}. Aborting switch.')
            return 1
        print(_('   - Updating configuration to new context...'))
        self.config_manager.set('python_executable', new_paths['python_executable'])
        self.config_manager.set('site_packages_path', new_paths['site_packages_path'])
        self.config_manager.set('multiversion_base', new_paths['multiversion_base'])
        print(_('   - ✅ Configuration saved.'))
        print(_('   - Updating default `python` symlinks...'))
        venv_path = Path(sys.prefix)
        try:
            self.config_manager._update_default_python_links(venv_path, target_interpreter_path)
        except Exception as e:
            print(_('   - ❌ Failed to update symlinks: {}').format(e))
        print(_('\n🎉 Successfully switched omnipkg context to Python {}!').format(version))
        print('   The configuration has been updated. To activate the new interpreter')
        print(_('   in your shell, you MUST re-source your activate script:'))
        print(_('\n      source {}\n').format(venv_path / 'bin' / 'activate'))
        print(_('Just kidding, omnipkg handled it for you automatically!'))
        return 0

    def adopt_interpreter(self, version: str) -> int:
        """
        Safely adopts a Python version by checking the registry, then trying to copy
        from the local system, and finally falling back to download.
        A rescan is forced after any successful filesystem change to ensure registration.
        """
        print(_('🐍 Attempting to adopt Python {} into the environment...').format(version))
        
        # First, check if it's already perfectly managed.
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        if version in managed_interpreters:
            print(_('   - ✅ Python {} is already adopted and managed.').format(version))
            return 0

        # Attempt to find it locally to copy.
        discovered_pythons = self.config_manager.list_available_pythons()
        source_path_str = discovered_pythons.get(version)
        
        if not source_path_str:
            print(_('   - No local Python {} found. Falling back to download strategy.').format(version))
            result = self._fallback_to_download(version)
            if result == 0:
                print(_('🔧 Forcing rescan to register the new interpreter...'))
                self.rescan_interpreters()
            return result
        
        source_exe_path = Path(source_path_str)
        try:
            cmd = [str(source_exe_path), '-c', 'import sys; print(sys.prefix)']
            cmd_result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            source_root = Path(os.path.realpath(cmd_result.stdout.strip()))
            current_venv_root = self.config_manager.venv_path.resolve()
            
            # Perform safety checks before attempting a copy
            if self._is_same_or_child_path(source_root, current_venv_root) or \
            not self._is_valid_python_installation(source_root, source_exe_path) or \
            self._estimate_directory_size(source_root) > 2 * 1024 * 1024 * 1024 or \
            self._is_system_critical_path(source_root):
                print(_('   - ⚠️  Safety checks failed for local copy. Falling back to download.'))
                result = self._fallback_to_download(version)
                if result == 0:
                    print(_('🔧 Forcing rescan to register the downloaded interpreter...'))
                    self.rescan_interpreters()
                return result
            
            dest_root = self.config_manager.venv_path / '.omnipkg' / 'interpreters' / f'cpython-{version}'
            if dest_root.exists():
                print(_('   - ✅ Adopted copy of Python {} already exists. Ensuring it is registered.').format(version))
                self.rescan_interpreters()
                return 0
            
            print(_('   - Starting safe copy operation...'))
            result = self._perform_safe_copy(source_root, dest_root, version)
            
            if result == 0:
                print(_('🔧 Forcing rescan to register the copied interpreter...'))
                self.rescan_interpreters()
            return result
            
        except Exception as e:
            print(_('   - ❌ An error occurred during the copy attempt: {}. Falling back to download.').format(e))
            result = self._fallback_to_download(version)
            if result == 0:
                print(_('🔧 Forcing rescan to register the downloaded interpreter...'))
                self.rescan_interpreters()
            return result

    def _fallback_to_download(self, version: str) -> int:
        """
        Fallback to downloading Python. This function now surgically detects an incomplete
        installation by checking for a valid executable, cleans it up if broken,
        and includes a safety stop to prevent deleting the active interpreter.
        """
        print(_('\n--- Running robust download strategy ---'))
        try:
            full_versions = {'3.12': '3.12.3', '3.10': '3.10.13', '3.9': '3.9.18', '3.11': '3.11.6'}
            full_version = full_versions.get(version)
            if not full_version:
                print(f'❌ Error: No known standalone build for Python {version}.')
                return 1

            dest_path = self.config_manager.venv_path / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'

            if dest_path.exists():
                print(_('   - Found existing directory for Python {}. Verifying integrity...').format(full_version))
                if self._is_interpreter_directory_valid(dest_path):
                    print(_('   - ✅ Integrity check passed. Installation is valid and complete.'))
                    # Even if valid, we ensure it's registered by forcing a rescan later.
                    return 0 # Success, no download needed.
                else:
                    print(_('   - ⚠️  Integrity check failed: Incomplete installation detected (missing or broken executable).'))

                    # --- CRITICAL SAFETY CHECK ---
                    # Never delete the interpreter that is currently running this script.
                    try:
                        active_interpreter_root = Path(sys.executable).resolve().parents[1]
                        if dest_path.resolve() == active_interpreter_root:
                            print(_('   - ❌ CRITICAL ERROR: The broken interpreter is the currently active one!'))
                            print(_('   - Aborting to prevent self-destruction. Please fix the environment manually.'))
                            return 1
                    except (IndexError, OSError):
                        pass # Path structure is unexpected, proceed with caution but don't block.
                    # --- END SAFETY CHECK ---

                    print(_('   - Preparing to clean up broken directory...'))
                    try:
                        shutil.rmtree(dest_path)
                        print(_('   - ✅ Removed broken directory successfully.'))
                    except Exception as e:
                        print(_("   - ❌ FATAL: Failed to remove existing broken directory: {}").format(e))
                        return 1

            # Proceed with a fresh installation into a guaranteed-to-be-clean directory.
            print(_('   - Starting fresh download and installation...'))
            
            # Call the correct method name - check your config_manager for the actual method
            if hasattr(self.config_manager, '_install_managed_python'):
                self.config_manager._install_managed_python(self.config_manager.venv_path, full_version)
            elif hasattr(self.config_manager, 'install_managed_python'):
                self.config_manager.install_managed_python(self.config_manager.venv_path, full_version)
            elif hasattr(self.config_manager, 'download_python'):
                self.config_manager.download_python(full_version)
            else:
                print(_('❌ Error: No download method found on config_manager'))
                return 1
                
            # Verify the installation worked
            if dest_path.exists() and self._is_interpreter_directory_valid(dest_path):
                print(_('   - ✅ Download and installation completed successfully.'))
                return 0
            else:
                print(_('   - ❌ Installation completed but integrity check still fails.'))
                return 1
        except Exception as e:
            print(_('❌ Download and installation process failed: {}').format(e))
            return 1 # Return 1 for failure
           
    def _is_same_or_child_path(self, source: Path, target: Path) -> bool:
        """Check if source is the same as target or a child of target."""
        try:
            source = source.resolve()
            target = target.resolve()
            if source == target:
                return True
            try:
                source.relative_to(target)
                return True
            except ValueError:
                return False
        except (OSError, RuntimeError):
            return True
        
    def _is_valid_python_installation(self, root: Path, exe_path: Path) -> bool:
        """Validate that the source looks like a proper Python installation."""
        try:
            if not exe_path.exists():
                return False
            try:
                exe_path.resolve().relative_to(root.resolve())
            except ValueError:
                return False
            expected_dirs = ['lib', 'bin']
            if sys.platform == 'win32':
                expected_dirs = ['Lib', 'Scripts']
            has_expected_structure = any(((root / d).exists() for d in expected_dirs))
            test_cmd = [str(exe_path), '-c', 'import sys, os']
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=5)
            return has_expected_structure and test_result.returncode == 0
        except Exception:
            return False

    def _estimate_directory_size(self, path: Path, max_files_to_check: int=1000) -> int:
        """Estimate directory size with early termination for safety."""
        total_size = 0
        file_count = 0
        try:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(('.git', '__pycache__', '.mypy_cache', 'node_modules'))]
                for file in files:
                    if file_count >= max_files_to_check:
                        return total_size * 10
                    try:
                        file_path = os.path.join(root, file)
                        total_size += os.path.getsize(file_path)
                        file_count += 1
                    except (OSError, IOError):
                        continue
        except Exception:
            return float('inf')
        return total_size

    def _is_system_critical_path(self, path: Path) -> bool:
        """Check if path is a system-critical directory that shouldn't be copied."""
        critical_paths = [Path('/'), Path('/usr'), Path('/usr/local'), Path('/System'), Path('/Library'), Path('/opt'), Path('/bin'), Path('/sbin'), Path('/etc'), Path('/var'), Path('/tmp'), Path('/proc'), Path('/dev'), Path('/sys')]
        if sys.platform == 'win32':
            critical_paths.extend([Path('C:\\Windows'), Path('C:\\Program Files'), Path('C:\\Program Files (x86)'), Path('C:\\System32')])
        try:
            resolved_path = path.resolve()
            for critical in critical_paths:
                if resolved_path == critical.resolve():
                    return True
            return False
        except Exception:
            return True

    def _perform_safe_copy(self, source: Path, dest: Path, version: str) -> int:
        """Perform the actual copy operation with additional safety measures."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)

            def copy_function(src, dst, *, follow_symlinks=True):
                try:
                    if os.path.getsize(src) > 100 * 1024 * 1024:
                        print(_('   - ⚠️  Skipping large file: {}').format(src))
                        return dst
                except OSError:
                    pass
                return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

            def ignore_patterns(dir, files):
                ignored = []
                for file in files:
                    if file in {'.git', '__pycache__', '.mypy_cache', '.pytest_cache', '.tox', '.coverage', 'node_modules', '.DS_Store'}:
                        ignored.append(file)
                    try:
                        filepath = os.path.join(dir, file)
                        if os.path.isfile(filepath) and os.path.getsize(filepath) > 50 * 1024 * 1024:
                            ignored.append(file)
                    except OSError:
                        pass
                return ignored
            print(_('   - Copying {} -> {}').format(source, dest))
            shutil.copytree(source, dest, symlinks=True, ignore=ignore_patterns, dirs_exist_ok=False)
            copied_python = self._find_python_executable_in_dir(dest)
            if not copied_python or not copied_python.exists():
                print(_('   - ❌ Copy completed but Python executable not found in destination'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            test_cmd = [str(copied_python), '-c', 'import sys; print(sys.version)']
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode != 0:
                print(_('   - ❌ Copied Python executable failed basic test'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            print(_('   - ✅ Copy successful and verified!'))
            self.config_manager._register_all_interpreters(self.config_manager.venv_path)
            print(f'\n🎉 Successfully adopted Python {version} from local source!')
            print(_("   You can now use 'omnipkg swap python {}'").format(version))
            return 0
        except Exception as e:
            print(_('   - ❌ Copy operation failed: {}').format(e))
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            return self._fallback_to_download(version)

    def _find_python_executable_in_dir(self, directory: Path) -> Path:
        """Find the Python executable in a copied directory."""
        possible_names = ['python', 'python3', 'python.exe']
        possible_dirs = ['bin', 'Scripts', '.']
        for subdir in possible_dirs:
            for name in possible_names:
                candidate = directory / subdir / name
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None

    def _resolve_package_versions(self, packages: List[str]) -> List[str]:
        """
        Takes a list of packages and ensures every entry has an explicit version.
        Uses the PyPI API to find the latest version for packages specified without one.
        """
        print(_('🔎 Resolving package versions via PyPI API...'))
        resolved_packages = []
        for pkg_spec in packages:
            if '==' in pkg_spec:
                resolved_packages.append(pkg_spec)
                continue
            pkg_name = self._parse_package_spec(pkg_spec)[0]
            print(_("    -> Finding latest version for '{}'...").format(pkg_name))
            target_version = self._get_latest_version_from_pypi(pkg_name)
            if target_version:
                new_spec = f'{pkg_name}=={target_version}'
                print(_("    ✅ Resolved '{}' to '{}'").format(pkg_name, new_spec))
                resolved_packages.append(new_spec)
            else:
                print(_("    ⚠️  Could not resolve a version for '{}' via PyPI. Skipping.").format(pkg_name))
        return resolved_packages


    def _run_pip_install(self, packages: List[str]) -> int:
        if not packages:
            return 0
        try:
            cmd = [self.config['python_executable'], '-m', 'pip', 'install'] + packages
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
            return result.returncode
        except subprocess.CalledProcessError as e:
            print(_('❌ Pip install command failed with exit code {}:').format(e.returncode))
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(_('    ❌ An unexpected error occurred during pip install: {}').format(e))
            return 1

    def _run_pip_uninstall(self, packages: List[str]) -> int:
        """Runs `pip uninstall` for a list of packages."""
        if not packages:
            return 0
        try:
            cmd = [self.config['python_executable'], '-m', 'pip', 'uninstall', '-y'] + packages
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            print(result.stdout)
            return result.returncode
        except subprocess.CalledProcessError as e:
            print(_('❌ Pip uninstall command failed with exit code {}:').format(e.returncode))
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(_('    ❌ An unexpected error occurred during pip uninstall: {}').format(e))
            return 1

    def _run_uv_install(self, packages: List[str]) -> int:
        """Runs `uv install` for a list of packages."""
        if not packages:
            return 0
        try:
            cmd = [self.config['uv_executable'], 'install', '--quiet'] + packages
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
            return result.returncode
        except FileNotFoundError:
            print(_("❌ Error: 'uv' executable not found. Please ensure uv is installed and in your PATH."))
            return 1
        except subprocess.CalledProcessError as e:
            print(_('❌ uv install command failed with exit code {}:').format(e.returncode))
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(_('    ❌ An unexpected error toccurred during uv install: {}').format(e))
            return 1

    def _run_uv_uninstall(self, packages: List[str]) -> int:
        """Runs `uv pip uninstall` for a list of packages."""
        if not packages:
            return 0
        try:
            cmd = [self.config['uv_executable'], 'pip', 'uninstall'] + packages
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            print(result.stdout)
            return result.returncode
        except FileNotFoundError:
            print(_("❌ Error: 'uv' executable not found. Please ensure uv is installed and in your PATH."))
            return 1
        except subprocess.CalledProcessError as e:
            print(_('❌ uv uninstall command failed with exit code {}:').format(e.returncode))
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(_('    ❌ An unexpected error occurred during uv uninstall: {}').format(e))
            return 1

    def _get_latest_version_from_pypi(self, package_name: str) -> Optional[str]:
        """
        Gets the latest *compatible* version of a package by leveraging pip's own
        dependency resolver with a dry-run. This is the most reliable method.
        """
        print(f" -> Finding latest COMPATIBLE version for '{package_name}' using pip's resolver...")
        try:          
            # First try: Use pip install --dry-run with verbose output
            cmd = [
                self.config['python_executable'], '-m', 'pip', 'install', 
                '--dry-run', '--verbose', '--no-deps', f'{package_name}'
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=dict(os.environ, PYTHONIOENCODING='utf-8')
            )
            
            # Combine stdout and stderr to ensure we catch the output regardless of pip version
            output_to_search = result.stdout + result.stderr
            
            # --- SIMPLIFIED DIAGNOSTIC ---
            if result.returncode != 0 or not output_to_search.strip():
                print(f"    [pip debug] Exit code: {result.returncode}, investigating alternative methods...")
            
            # FIRST: Check if package is already installed and extract the version
            already_satisfied_patterns = [
                rf'Requirement already satisfied:\s+{re.escape(package_name)}\s+in\s+[^\s]+\s+\(([^)]+)\)',
                rf'Requirement already satisfied:\s+{re.escape(package_name)}==([^\s]+)',
                rf'Requirement already satisfied:\s+{re.escape(package_name)}-([^\s]+)',
            ]
            
            for pattern in already_satisfied_patterns:
                match = re.search(pattern, output_to_search, re.IGNORECASE | re.MULTILINE)
                if match:
                    version = match.group(1).strip()
                    print(f" ✅ Package already installed with version: {version}")
                    # Validate the version format
                    if re.match(r'^[0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?$', version):
                        return version
                    else:
                        print(f" ⚠️  Version '{version}' has invalid format, continuing search...")
                        continue
            
            # If the first approach didn't work, try alternative methods
            if not output_to_search.strip() or result.returncode != 0:
                print(" -> Trying alternative approach: pip index versions...")
                
                # Try pip index versions (available in newer pip versions)
                cmd_alt = [
                    self.config['python_executable'], '-m', 'pip', 'index', 'versions', package_name
                ]
                
                result_alt = subprocess.run(
                    cmd_alt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60
                )
                
                if result_alt.returncode == 0 and result_alt.stdout.strip():
                    # Parse output like: "package (1.0.0, 0.9.0, ...)"
                    version_match = re.search(rf'{re.escape(package_name)}\s*\(([^,)]+)', result_alt.stdout)
                    if version_match:
                        version = version_match.group(1).strip()
                        print(f" ✅ Found latest version via pip index: {version}")
                        return version
                
                # If that fails, try pip download approach
                print(" -> Trying pip download approach...")
                cmd_download = [
                    self.config['python_executable'], '-m', 'pip', 'download', 
                    '--dry-run', '--no-deps', package_name
                ]
                
                result_download = subprocess.run(
                    cmd_download,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60
                )
                
                output_to_search = result_download.stdout + result_download.stderr
                if result_download.returncode != 0:
                    print(f"    [pip download debug] Exit code: {result_download.returncode}")
            
            # Improved regex patterns to catch various pip output formats
            patterns = [
                # Pattern 1: "Would install package-version" or "Installing collected packages: package-version"
                rf'(?:Would install|Installing collected packages:|Collecting)\s+{re.escape(package_name)}-([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?)',
                
                # Pattern 2: "package==version" format
                rf'{re.escape(package_name)}==([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?)',
                
                # Pattern 3: "Downloading package-version-" format
                rf'Downloading\s+{re.escape(package_name)}-([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?)-',
                
                # Pattern 4: Generic "package version" format
                rf'{re.escape(package_name)}\s+([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?)',
                
                # Pattern 5: Requirements format "package>=version"
                rf'{re.escape(package_name)}>=([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?)'
            ]
            
            for i, pattern in enumerate(patterns, 1):
                match = re.search(pattern, output_to_search, re.IGNORECASE | re.MULTILINE)
                if match:
                    version = match.group(1)
                    print(f" ✅ Pip resolver identified latest compatible version: {version} (pattern {i})")
                    
                    # Validate the version format
                    if re.match(r'^[0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9\.-_]*)?$', version):
                        return version
                    else:
                        print(f" ⚠️  Version '{version}' has invalid format, continuing search...")
                        continue
            
            # Final fallback: try to get version from pip list if the package seems to be installed
            if "Requirement already satisfied" in output_to_search:
                print(" -> Package appears to be installed, checking with pip list...")
                cmd_list = [
                    self.config['python_executable'], '-m', 'pip', 'list', 
                    '--format=freeze', f'| grep -i "^{package_name}=="'
                ]
                
                try:
                    # Use shell=True for the grep to work
                    result_list = subprocess.run(
                        f"{self.config['python_executable']} -m pip list --format=freeze | grep -i '^{package_name}=='",
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result_list.returncode == 0 and result_list.stdout.strip():
                        # Parse "package==version" format
                        list_match = re.search(rf'^{re.escape(package_name)}==([^\s]+)', result_list.stdout, re.IGNORECASE | re.MULTILINE)
                        if list_match:
                            version = list_match.group(1).strip()
                            print(f" ✅ Found installed version via pip list: {version}")
                            return version
                except Exception as e:
                    print(f" -> pip list approach failed: {e}")
            
            print(f" ❌ CRITICAL: Could not parse the resolved version from pip's output for '{package_name}'.")
            print(f" ❌ This might indicate: 1) Package doesn't exist, 2) No compatible version, 3) Network issues, 4) Unexpected pip output format")
            return None
            
        except subprocess.TimeoutExpired:
            print(f" ❌ Pip resolver timed out while resolving '{package_name}'.")
            return None
        except Exception as e:
            print(f" ❌ An unexpected error occurred while running the pip resolver for '{package_name}': {e}")
            return None

    def get_available_versions(self, package_name: str) -> List[str]:
        """
        Correctly gets all available versions (active and bubbled) for a package
        by checking all relevant keys in the knowledge base.
        """
        c_name = canonicalize_name(package_name)
        main_key = f'{self.redis_key_prefix}{c_name}'
        versions = set()
        try:
            versions.update(self.redis_client.smembers(_('{}:installed_versions').format(main_key)))
            active_version = self.redis_client.hget(main_key, 'active_version')
            if active_version:
                versions.add(active_version)
            return sorted(list(versions), key=parse_version, reverse=True)
        except Exception as e:
            print(_('⚠️ Could not retrieve versions for {}: {}').format(package_name, e))
            return []

    def list_packages(self, pattern: str=None) -> int:
        if not self.connect_redis():
            return 1
        self._synchronize_knowledge_base_with_reality()
        all_pkg_names = self.redis_client.smembers(f'{self.redis_key_prefix}index')
        if pattern:
            all_pkg_names = {name for name in all_pkg_names if pattern.lower() in name.lower()}
        print(_('📋 Found {} matching package(s):').format(len(all_pkg_names)))
        for pkg_name in sorted(list(all_pkg_names)):
            main_key = f'{self.redis_key_prefix}{pkg_name}'
            package_data = self.redis_client.hgetall(main_key)
            display_name = package_data.get('name', pkg_name)
            active_version = package_data.get('active_version')
            all_versions = self.get_available_versions(pkg_name)
            print(_('\n- {}:').format(display_name))
            if not all_versions:
                print(_('  (No versions found in knowledge base)'))
                continue
            for version in all_versions:
                if version == active_version:
                    print(_('  ✅ {} (active)').format(version))
                else:
                    print(_('  🫧 {} (bubble)').format(version))
        return 0

    def show_multiversion_status(self) -> int:
        if not self.connect_redis():
            return 1
        self._synchronize_knowledge_base_with_reality()
        print(_('🔄 omnipkg System Status'))
        print('=' * 50)
        print(_("🛠️ Environment broken by pip or uv? Run 'omnipkg revert' to restore the last known good state! 🚑"))
        try:
            pip_version = version('pip')
            print(_('\n🔒 Pip in Jail (main environment)'))
            print(_('    😈 Locked up for causing chaos in the main env! 🔒 (v{})').format(pip_version))
        except importlib.metadata.PackageNotFoundError:
            print(_('\n🔒 Pip in Jail (main environment)'))
            print(_('    🚫 Pip not found in the main env. Escaped or never caught!'))
        try:
            uv_version = version('uv')
            print(_('🔒 UV in Jail (main environment)'))
            print(_('    😈 Speedy troublemaker locked up in the main env! 🔒 (v{})').format(uv_version))
        except importlib.metadata.PackageNotFoundError:
            print(_('🔒 UV in Jail (main environment)'))
            print(_('    🚫 UV not found in the main env. Too fast to catch!'))
        print(_('\n🌍 Main Environment:'))
        site_packages = Path(self.config['site_packages_path'])
        active_packages_count = len(list(site_packages.glob('*.dist-info')))
        print(_('  - Path: {}').format(site_packages))
        print(_('  - Active Packages: {}').format(active_packages_count))
        print(_('\n📦 izolasyon Alanı (Bubbles):'))
        if not self.multiversion_base.exists() or not any(self.multiversion_base.iterdir()):
            print(_('  - No isolated package versions found.'))
            return 0
        print(_('  - Bubble Directory: {}').format(self.multiversion_base))
        print(_('  - Import Hook Installed: {}').format('✅' if self.hook_manager.hook_installed else '❌'))
        version_dirs = list(self.multiversion_base.iterdir())
        total_bubble_size = 0
        print(_('\n📦 Isolated Package Versions ({} bubbles):').format(len(version_dirs)))
        for version_dir in sorted(version_dirs):
            if version_dir.is_dir():
                size = sum((f.stat().st_size for f in version_dir.rglob('*') if f.is_file()))
                total_bubble_size += size
                size_mb = size / (1024 * 1024)
                warning = ' ⚠️' if size_mb > 100 else ''
                formatted_size_str = '{:.1f}'.format(size_mb)
                print(_('  - 📁 {} ({} MB){}').format(version_dir.name, formatted_size_str, warning))
                if 'pip' in version_dir.name.lower():
                    print(_('    😈 Pip is locked up in a bubble, plotting chaos like a Python outlaw! 🔒'))
                elif 'uv' in version_dir.name.lower():
                    print(_('    😈 UV is locked up in a bubble, speeding toward trouble! 🔒'))
        total_bubble_size_mb = total_bubble_size / (1024 * 1024)
        formatted_total_size_str = '{:.1f}'.format(total_bubble_size_mb)
        print(_('  - Total Bubble Size: {} MB').format(formatted_total_size_str))
        return 0