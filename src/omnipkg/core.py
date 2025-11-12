"""
omnipkg
An intelligent installer that lets pip run, then surgically cleans up downgrades
and isolates conflicting versions in deduplicated bubbles to guarantee a stable environment.
"""
try:
    from .common_utils import safe_print, print_header
except ImportError:
    from omnipkg.common_utils import safe_print, print_header
import hashlib
import importlib.metadata
import io
import json
import locale as sys_locale
import os
import threading
import platform
import textwrap
import time
import re
import shutil
import traceback
import site
import subprocess
import sys
import collections
import tarfile
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False
import requests as http_requests
from filelock import FileLock
from importlib.metadata import version, metadata, PackageNotFoundError
from packaging.utils import canonicalize_name
from packaging.version import parse as parse_version, InvalidVersion
from packaging.version import parse as parse_version
from packaging.utils import canonicalize_name
from .i18n import _
from .package_meta_builder import omnipkgMetadataGatherer
from .cache import SQLiteCacheClient
from .loader import omnipkgLoader # <--- ADD THIS LINE

try:
    import tomllib
    HAS_TOMLLIB = True
except ModuleNotFoundError:
    try:
        import tomli as tomllib
        HAS_TOMLLIB = True
    except ImportError:
        tomllib = None
        HAS_TOMLLIB = False
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

def _get_dynamic_omnipkg_version():
    """
    Gets the omnipkg version, prioritizing pyproject.toml in developer mode.
    """
    if not HAS_TOMLLIB:
        # Can't read TOML, skip to metadata fallback
        try:
            return importlib.metadata.version('omnipkg')
        except importlib.metadata.PackageNotFoundError:
            return 'unknown'
    
    # Try pyproject.toml first (developer mode)
    try:
        pyproject_path = Path(__file__).parent.parent / 'pyproject.toml'
        if pyproject_path.exists():
            with pyproject_path.open('rb') as f:
                data = tomllib.load(f)
            version_from_toml = data.get('project', {}).get('version')
            if version_from_toml:
                return version_from_toml
    except Exception as e:
        safe_print(f"⚠️  Could not read version from pyproject.toml: {e}")
    
    # Fallback to installed metadata
    try:
        return importlib.metadata.version('omnipkg')
    except importlib.metadata.PackageNotFoundError:
        pass
    
    return 'unknown'

def _get_core_dependencies() -> set:
    """
    Reads omnipkg's DIRECT production dependencies from pyproject.toml.
    
    We ONLY return direct dependencies - pip will automatically handle
    transitive dependencies and filter out packages incompatible with 
    the target Python version.
    """
    try:
        # Try to find pyproject.toml relative to this file
        pyproject_path = Path(__file__).parent.parent / 'pyproject.toml'
        
        if not pyproject_path.exists():
            # Fallback: try current working directory
            pyproject_path = Path.cwd() / 'pyproject.toml'
        
        if pyproject_path.exists():
            # Read the toml file
            if sys.version_info >= (3, 11):
                import tomllib
                with pyproject_path.open('rb') as f:
                    pyproject_data = tomllib.load(f)
            else:
                try:
                    import tomli
                    with pyproject_path.open('rb') as f:
                        pyproject_data = tomli.load(f)
                except ImportError:
                    # If tomli not available, fall through to metadata fallback
                    raise
            
            # Get ONLY direct dependencies (no optional, no dev)
            deps = pyproject_data.get('project', {}).get('dependencies', [])
            
            # Extract just the package names (strip version specs)
            core_deps = set()
            for dep in deps:
                # Match package name before any version specifier
                match = re.match(r'^([a-zA-Z0-9\-_.]+)', dep)
                if match:
                    pkg_name = canonicalize_name(match.group(1))
                    core_deps.add(pkg_name)
            
            safe_print(f'   📋 Found {len(core_deps)} direct dependencies in pyproject.toml')
            return core_deps
        
        # If no pyproject.toml found, try to get from installed metadata
        safe_print('   ⚠️  No pyproject.toml found, trying installed metadata...')
        
    except Exception as e:
        safe_print(f'   ⚠️  Could not read pyproject.toml: {e}')
        safe_print('   📋 Falling back to installed metadata...')
    
    # Fallback: Get from installed metadata
    try:
        from importlib.metadata import metadata, PackageNotFoundError
        
        pkg_meta = metadata('omnipkg')
        reqs = pkg_meta.get_all('Requires-Dist') or []
        
        # Only get DIRECT dependencies (no extras, no conditionals)
        dependencies = set()
        for req in reqs:
            # Skip conditional dependencies (extras like [dev], [test], etc.)
            if 'extra ==' in req:
                continue
            
            match = re.match(r'^([a-zA-Z0-9\-_.]+)', req)
            if match:
                pkg_name = canonicalize_name(match.group(1))
                dependencies.add(pkg_name)
        
        # Add tomli for Python < 3.11 if not already present
        if sys.version_info < (3, 11):
            dependencies.add('tomli')
        
        safe_print(f'   📋 Found {len(dependencies)} direct dependencies from metadata')
        return dependencies
        
    except Exception as e:
        safe_print(f'   ⚠️  Could not determine dependencies from metadata: {e}')
        safe_print('   📦 Using minimal essential fallback set')
        
        # Absolute minimal fallback - just what omnipkg absolutely needs
        minimal_deps = {'redis', 'rich', 'requests'}
        
        # Add tomli for older Python versions
        if sys.version_info < (3, 11):
            minimal_deps.add('tomli')
        
        return minimal_deps

class ConfigManager:
    """
    Manages loading and first-time creation of the omnipkg config file.
    Now includes Python interpreter hotswapping capabilities and is environment-aware.
    """

    def __init__(self, suppress_init_messages=False):
        """
        Initializes the ConfigManager with a robust, fail-safe sequence.
        This new logic correctly establishes environment identity first, then loads
        or creates the configuration, and finally handles the one-time environment
        setup for interpreters.
        """
        env_id_override = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE')
        self.venv_path = self._get_venv_root()
        if env_id_override:
            self.env_id = env_id_override
        else:
            self.env_id = hashlib.md5(str(self.venv_path.resolve()).encode()).hexdigest()[:8]
        self._python_cache = {}
        self._preferred_version = (3, 11)
        self.config_dir = Path.home() / '.config' / 'omnipkg'
        self.config_path = self.config_dir / 'config.json'
        self.config = self._load_or_create_env_config(interactive=not suppress_init_messages)
        if self.config:
            self.multiversion_base = Path(self.config.get('multiversion_base', ''))
        else:
            if not suppress_init_messages:
                safe_print(_('⚠️ CRITICAL Warning: Config failed to load, omnipkg may not function.'))
            self.multiversion_base = Path('')
            return
        is_nested_interpreter = '.omnipkg/interpreters' in str(Path(sys.executable).resolve())
        setup_complete_flag = self.venv_path / '.omnipkg' / '.setup_complete'
        if not setup_complete_flag.exists() and (not is_nested_interpreter):
            if not suppress_init_messages:
                safe_print('\n' + '=' * 60)
                safe_print(_('  🚀 OMNIPKG ONE-TIME ENVIRONMENT SETUP'))
                safe_print('=' * 60)
            try:
                if not suppress_init_messages:
                    safe_print(_('   - Step 1: Registering the native Python interpreter...'))
                native_version_str = f'{sys.version_info.major}.{sys.version_info.minor}'
                self._register_and_link_existing_interpreter(Path(sys.executable), native_version_str)
                if sys.version_info[:2] != self._preferred_version:
                    if not suppress_init_messages:
                        safe_print(_('\n   - Step 2: Setting up the required Python 3.11 control plane...'))
                    temp_omnipkg = omnipkg(config_manager=self)
                    result_code = temp_omnipkg._fallback_to_download('3.11')
                    if result_code != 0:
                        raise RuntimeError('Failed to set up the Python 3.11 control plane.')
                setup_complete_flag.parent.mkdir(parents=True, exist_ok=True)
                setup_complete_flag.touch()
                if not suppress_init_messages:
                    safe_print('\n' + '=' * 60)
                    safe_print(_('  ✅ SETUP COMPLETE'))
                    safe_print('=' * 60)
                    safe_print(_('Your environment is now fully managed by omnipkg.'))
                    safe_print('=' * 60)
            except Exception as e:
                if not suppress_init_messages:
                    safe_print(_('❌ A critical error occurred during one-time setup: {}').format(e))
                    import traceback
                    traceback.print_exc()
                if setup_complete_flag.exists():
                    setup_complete_flag.unlink(missing_ok=True)
                sys.exit(1)

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
                    pass
            if version_str not in versions_to_rebuild:
                versions_to_rebuild.append(version_str)
            with open(flag_file, 'w') as f:
                json.dump(versions_to_rebuild, f)
        safe_print(_('   🚩 Flag set: Python {} will build its knowledge base on first use.').format(version_str))

    def _clear_rebuild_flag_for_version(self, version_str: str):
        """
        Surgically removes a specific version from the .needs_kb_rebuild flag file.
        """
        flag_file = self.venv_path / '.omnipkg' / '.needs_kb_rebuild'
        if not flag_file.exists():
            return
        
        lock_file = self.venv_path / '.omnipkg' / '.needs_kb_rebuild.lock'
        with FileLock(lock_file):
            try:
                with open(flag_file, 'r') as f:
                    versions_to_rebuild = json.load(f)
                
                if version_str in versions_to_rebuild:
                    versions_to_rebuild.remove(version_str)
                    safe_print(f"   -> Automatically clearing 'first use' flag for Python {version_str}...")
                    
                    if not versions_to_rebuild:
                        flag_file.unlink()
                    else:
                        with open(flag_file, 'w') as f:
                            json.dump(versions_to_rebuild, f)
            except (json.JSONDecodeError, IOError, Exception):
                flag_file.unlink(missing_ok=True)

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
        Finds the virtual environment root with enhanced validation to prevent
        environment cross-contamination from stale shell variables.
        
        CRITICAL: When running from a managed interpreter (e.g., inside .omnipkg/interpreters/),
        we must find the ORIGINAL venv root, not create nested structures.
        """
        override = os.environ.get('OMNIPKG_VENV_ROOT')
        if override:
            return Path(override)

        current_executable = Path(sys.executable).resolve()

        # Check common environment variables first, but validate them
        venv_path_str = os.environ.get('VIRTUAL_ENV')
        if venv_path_str:
            venv_path = Path(venv_path_str).resolve()
            # CRITICAL: Ensure the current running python is INSIDE this VIRTUAL_ENV
            if str(current_executable).startswith(str(venv_path)):
                return venv_path

        conda_prefix_str = os.environ.get('CONDA_PREFIX')
        if conda_prefix_str:
            conda_path = Path(conda_prefix_str).resolve()
            if str(current_executable).startswith(str(conda_path)):
                return conda_path

        # --- CRITICAL FIX: Detect if we're in a managed interpreter ---
        # If we're running from .omnipkg/interpreters/*, we need to find the REAL venv root
        # by going up past ALL .omnipkg directories (handles nested cases!)
        executable_str = str(current_executable)
        
        # AGGRESSIVE: Handle ANY level of nesting by finding the FIRST .omnipkg in the path
        if '.omnipkg' in executable_str:
            # Normalize path separators
            normalized_path = executable_str.replace('\\', '/')
            
            # Find the FIRST occurrence of .omnipkg (going from left/root)
            omnipkg_parts = normalized_path.split('/.omnipkg/')
            
            if len(omnipkg_parts) >= 2:
                # Everything BEFORE the first .omnipkg is the original venv
                original_venv = Path(omnipkg_parts[0])
                
                # Verify this is actually a venv by checking for pyvenv.cfg
                if (original_venv / 'pyvenv.cfg').exists():
                    return original_venv
                
                # If no pyvenv.cfg at that level, search upward from there
                search_dir = original_venv
                while search_dir != search_dir.parent:
                    if (search_dir / 'pyvenv.cfg').exists():
                        return search_dir
                    search_dir = search_dir.parent
                
                # Last resort: if we can't find pyvenv.cfg, just use the directory
                # before .omnipkg as it's definitely the venv root
                return original_venv
        
        # --- Standard upward search for non-managed interpreters ---
        # Search upwards from the current executable for pyvenv.cfg
        search_dir = current_executable.parent
        while search_dir != search_dir.parent:  # Stop at the filesystem root
            if (search_dir / 'pyvenv.cfg').exists():
                return search_dir
            search_dir = search_dir.parent

        # Only use sys.prefix as a last resort if all else fails.
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
            safe_print(_('   ⚠️  Could not reset setup flag in config file: {}').format(e))

    def _trigger_hotswap_relaunch(self):
        """
        Handles the user interaction and download process for an environment that needs to be upgraded.
        This function is self-contained and does not depend on self.config. It ends with an execv call.
        """
        safe_print('\n' + '=' * 60)
        safe_print(_('  🚀 Environment Hotswap to a Managed Python 3.11'))
        safe_print('=' * 60)
        safe_print(f'omnipkg works best with Python 3.11. Your version is {sys.version_info.major}.{sys.version_info.minor}.')
        safe_print(_("\nTo ensure everything 'just works', omnipkg will now perform a one-time setup:"))
        safe_print(_('  1. Download a self-contained Python 3.11 into your virtual environment.'))
        safe_print('  2. Relaunch seamlessly to continue your command.')
        try:
            choice = input('\nDo you want to proceed with the automatic setup? (y/n): ')
            if choice.lower() == 'y':
                self._install_python311_in_venv()
            else:
                safe_print('🛑 Setup cancelled. Aborting, as a managed Python 3.11 is required.')
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            safe_print(_('\n🛑 Operation cancelled. Aborting.'))
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

    def _align_config_to_interpreter(self, python_exe_path_str: str):
        """
        Updates and saves config paths to match the specified Python executable
        by running it as a subprocess to get its true paths.
        """
        safe_print(_('🔧 Aligning configuration to use Python interpreter: {}').format(python_exe_path_str))
        correct_paths = self._get_paths_for_interpreter(python_exe_path_str)
        if not correct_paths:
            safe_print(f'❌ CRITICAL: Failed to determine paths for {python_exe_path_str}. Configuration not updated.')
            return
        safe_print(_('   - New site-packages path: {}').format(correct_paths['site_packages_path']))
        safe_print(_('   - New Python executable: {}').format(correct_paths['python_executable']))
        self.set('python_executable', correct_paths['python_executable'])
        self.set('site_packages_path', correct_paths['site_packages_path'])
        self.set('multiversion_base', correct_paths['multiversion_base'])
        self.config.update(correct_paths)
        self.multiversion_base = Path(self.config['multiversion_base'])
        safe_print(_('   ✅ Configuration updated and saved successfully.'))

    def _copy_native_python_to_managed(self, venv_path: Path) -> Path:
        """
        Installs a MINIMAL copy of just the Python interpreter (not the entire venv).
        Uses the system Python as a base but copies only what's needed.
        """
        native_version = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
        major_minor = f'{sys.version_info.major}.{sys.version_info.minor}'
        
        safe_print(_('   - Setting up managed Python {} environment...').format(major_minor))
        
        # Destination
        managed_interpreters_dir = venv_path / '.omnipkg' / 'interpreters'
        managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
        
        dest_dir_name = f'cpython-{native_version}'  # No suffix needed
        dest_path = managed_interpreters_dir / dest_dir_name
        
        if dest_path.exists():
            safe_print(_('   - ✅ Managed environment already exists.'))
            python_exe = dest_path / 'bin' / f'python{major_minor}'
            if python_exe.exists():
                return python_exe
        
        safe_print(_('   - Creating minimal Python {} installation...').format(major_minor))
        
        try:
            # Create a new venv using the current Python
            # This creates a lightweight environment with just the essentials
            import venv
            
            safe_print(_('   - Building virtual environment...'))
            venv.create(dest_path, with_pip=True, symlinks=False, clear=True)
            
            # Find the executable in the new venv
            if platform.system() == 'Windows':
                python_exe = dest_path / 'Scripts' / 'python.exe'
            else:
                python_exe = dest_path / 'bin' / f'python{major_minor}'
                if not python_exe.exists():
                    python_exe = dest_path / 'bin' / 'python3'
                if not python_exe.exists():
                    python_exe = dest_path / 'bin' / 'python'
            
            if not python_exe.exists():
                raise OSError(_('Could not find Python executable in created environment'))
            
            # Verify it works
            result = subprocess.run([str(python_exe), '--version'], 
                                capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise OSError(_('Created Python failed verification: {}').format(result.stderr))
            
            safe_print(_('   - ✅ Successfully created Python {} environment').format(major_minor))
            return python_exe
            
        except Exception as e:
            safe_print(_('   - ❌ Failed to create managed Python: {}').format(e))
            if dest_path.exists():
                shutil.rmtree(dest_path)
            raise


    def _setup_native_311_environment(self):
        """
        Performs the one-time setup for an environment that already has Python 3.11.
        This primarily involves symlinking and registering the interpreter.
        This function runs AFTER self.config is loaded.
        """
        safe_print('\n' + '=' * 60)
        safe_print('  🚀 Finalizing Environment Setup for Python 3.11')
        safe_print('=' * 60)
        safe_print(_('✅ Detected a suitable Python 3.11 within your virtual environment.'))
        safe_print('   - Registering it with omnipkg for future operations...')
        self._register_and_link_existing_interpreter(Path(sys.executable), f'{sys.version_info.major}.{sys.version_info.minor}')
        registered_311_path = self.get_interpreter_for_version('3.11')
        if registered_311_path:
            self._align_config_to_interpreter(str(registered_311_path))
        else:
            safe_print(_('⚠️ Warning: Could not find registered Python 3.11 path after setup. Config may be incorrect.'))
        self.set('managed_python_setup_complete', True)
        safe_print(_('\n✅ Environment setup is complete!'))

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
                safe_print(_('   - Registering current Python 3.11...'))
                self._register_all_interpreters(self.venv_path)

    def _register_and_link_existing_interpreter(self, interpreter_path: Path, version: str):
        """
        Registers the native interpreter WITHOUT symlinking it.
        """
        managed_interpreters_dir = self.venv_path / '.omnipkg' / 'interpreters'
        managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
        
        # Platform-aware native detection
        interpreter_resolved = interpreter_path.resolve()
        
        # On Windows: check if it's in the main venv directory (not in .omnipkg)
        # On Unix: check if it's in venv_path/bin
        if platform.system() == 'Windows':
            # Windows: native if it's in the venv root, not in .omnipkg
            is_native = (
                str(interpreter_resolved).startswith(str(self.venv_path)) and
                '.omnipkg' not in str(interpreter_resolved)
            )
        else:
            # Unix: native if it's in venv_path/bin
            is_native = interpreter_resolved.parent == (self.venv_path / 'bin')
        
        if is_native:
            safe_print(_('   - Native Python {} - using directly (no symlink)').format(version))
            registry_path = managed_interpreters_dir / 'registry.json'
            
            # Load existing registry
            registry_data = {'interpreters': {}}
            if registry_path.exists():
                try:
                    with open(registry_path, 'r') as f:
                        registry_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass

            # Register the NATIVE path (not a symlink!)
            registry_data['interpreters'][version] = str(interpreter_resolved)

            with open(registry_path, 'w') as f:
                json.dump(registry_data, f, indent=4)

            safe_print(f"   - ✅ Registered native Python {version} in registry file.")
            return  # EXIT HERE - don't create symlink!
        
        # Only create symlink for NON-native interpreters
        # (This should rarely happen in normal usage)
        safe_print(_('   - WARNING: Non-native interpreter detected, creating symlink...'))
        
        # For non-native interpreters, create symlink
        safe_print(_('   - Centralizing Python {}...').format(version))
        symlink_dir_name = f'cpython-{version}-managed'
        symlink_path = managed_interpreters_dir / symlink_dir_name
        target_for_link = Path(sys.prefix)

        if symlink_path.exists():
            safe_print(_('   - ✅ Link already exists.'))
        else:
            try:
                safe_print(_('   - Attempting to create a symbolic link...'))
                symlink_path.symlink_to(target_for_link, target_is_directory=True)
                safe_print(_('   - ✅ Created symlink: {} -> {}').format(symlink_path, target_for_link))
            except (PermissionError, OSError) as e:
                if platform.system() == 'Windows':
                    safe_print(_('   - ⚠️ Symlink creation failed ({}). Falling back to creating a directory junction...').format(e))
                    try:
                        subprocess.run(['cmd', '/c', 'mklink', '/J', str(symlink_path), str(target_for_link)], check=True, capture_output=True)
                        safe_print(_('   - ✅ Created junction: {} -> {}').format(symlink_path, target_for_link))
                    except (subprocess.CalledProcessError, FileNotFoundError) as junction_error:
                        safe_print(_('   - ❌ Failed to create directory junction: {}').format(junction_error))
                        safe_print(_('   - ❌ Could not adopt the interpreter. Please try running with administrative privileges.'))
                else:
                    safe_print(_('   - ❌ Failed to create symlink: {}').format(e))
                    safe_print(_('   - ❌ Could not adopt the interpreter.'))

        self._register_all_interpreters(self.venv_path)

    def _register_all_interpreters(self, venv_path: Path):
        """
        THREAD-SAFE: Uses InterpreterManager's lock to prevent concurrent registry corruption.
        """
        safe_print(_('🔧 Registering all managed Python interpreters...'))
        managed_interpreters_dir = venv_path / '.omnipkg' / 'interpreters'
        managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
        registry_path = managed_interpreters_dir / 'registry.json'
        
        # === CRITICAL: Acquire the lock before ANY registry operations ===
        InterpreterManager = globals()['InterpreterManager']

        with InterpreterManager._registry_write_lock:
            safe_print("   🔒 Acquired registry write lock")
            
            # === STEP 1: Load existing registry and preserve native entries ===
            existing_registry = {}
            native_interpreters = {}
            
            if registry_path.exists():
                try:
                    with open(registry_path, 'r') as f:
                        existing_registry = json.load(f)
                        
                    # Extract and preserve native interpreter entries
                    for version, path_str in existing_registry.get('interpreters', {}).items():
                        path = Path(path_str)
                        # If the path is NOT inside .omnipkg/interpreters/, it's native
                        if not str(path).startswith(str(managed_interpreters_dir)):
                            if path.exists():  # Only preserve if still exists
                                native_interpreters[version] = path_str
                                safe_print(_('   ℹ️  Preserving native Python {}: {}').format(version, path_str))
                            else:
                                safe_print(_('   ⚠️  Native Python {} no longer exists, removing from registry').format(version))
                except (json.JSONDecodeError, IOError) as e:
                    safe_print(_('   ⚠️  Could not load existing registry: {}').format(e))
            
            # === STEP 2: Auto-preserve current Python if no native found ===
            if not native_interpreters:
                current_version = f'{sys.version_info.major}.{sys.version_info.minor}'
                current_exe = sys.executable
                # Only add if it's truly not in managed directory
                if not str(current_exe).startswith(str(managed_interpreters_dir)):
                    native_interpreters[current_version] = current_exe
                    safe_print(_('   ℹ️  Auto-preserving current Python {}: {}').format(current_version, current_exe))
            
            # === STEP 3: Scan for managed interpreters ===
            interpreters = {}

            if not managed_interpreters_dir.is_dir():
                safe_print(_('   ⚠️  Managed interpreters directory not found.'))
                # Still save native interpreters even if no managed ones exist
                if native_interpreters:
                    registry_data = {
                        'primary_version': list(native_interpreters.keys())[0] if native_interpreters else None,
                        'interpreters': native_interpreters,
                        'last_updated': datetime.now().isoformat()
                    }
                    with open(registry_path, 'w') as f:
                        json.dump(registry_data, f, indent=4)
                safe_print("   🔓 Released registry write lock")
                return

            # CLEANUP: Remove the bad native symlink if it exists
            current_version = f'{sys.version_info.major}.{sys.version_info.minor}'
            bad_symlink = managed_interpreters_dir / f'cpython-{current_version}-venv-native'
            if bad_symlink.exists():
                safe_print(_('   - Removing legacy native symlink...'))
                if bad_symlink.is_symlink():
                    bad_symlink.unlink()
                else:
                    import shutil
                    shutil.rmtree(bad_symlink, ignore_errors=True)
                safe_print(_('   - ✅ Legacy symlink removed'))

            # Scan for managed interpreters (downloaded ones)
            for interp_dir in managed_interpreters_dir.iterdir():
                if not (interp_dir.is_dir() or interp_dir.is_symlink()):
                    continue
                
                # Skip the bad native symlink if it somehow still exists
                if interp_dir.name == f'cpython-{current_version}-venv-native':
                    continue
                
                safe_print(_('   -> Scanning directory: {}').format(interp_dir.name))
                found_exe_path = None
                
                # ✅ FIXED: All this needs to be INSIDE the for loop (indented)
                # Pass 1: Fast, shallow search for standard layouts (Linux, macOS)
                search_locations = [interp_dir / 'bin', interp_dir / 'Scripts']
                possible_exe_names = ['python3.14', 'python3.13', 'python3.12', 'python3.11', 
                                    'python3.10', 'python3.9', 'python3.8', 'python3', 
                                    'python', 'python.exe']

                for location in search_locations:
                    if location.is_dir():
                        for exe_name in possible_exe_names:
                            exe_path = location / exe_name
                            if exe_path.is_file() and os.access(exe_path, os.X_OK):
                                version_tuple = self._verify_python_version(str(exe_path))
                                if version_tuple:
                                    found_exe_path = exe_path
                                    break
                    if found_exe_path:
                        break
                
                # Pass 2: If shallow search fails, perform a deeper, recursive search
                if not found_exe_path:
                    safe_print(_('      -> Standard search failed, trying deep scan for executables...'))
                    all_candidates = list(interp_dir.rglob('python.exe' if platform.system() == 'Windows' else 'python*'))
                    sorted_candidates = sorted(all_candidates, key=lambda p: len(p.parts))

                    for candidate in sorted_candidates:
                        if any(part in candidate.parts for part in ['Tools', 'Doc', 'include', 'tcl']):
                            continue
                        
                        if candidate.is_file() and os.access(candidate, os.X_OK):
                            version_tuple = self._verify_python_version(str(candidate))
                            if version_tuple:
                                found_exe_path = candidate
                                break

                if found_exe_path:
                    version_tuple = self._verify_python_version(str(found_exe_path))
                    if version_tuple:
                        version_str = f'{version_tuple[0]}.{version_tuple[1]}'
                        safe_print(_('      ✅ Found valid executable: {}').format(found_exe_path))
                        interpreters[version_str] = str(found_exe_path)
                else:
                    safe_print(_('      ⚠️  No valid Python executable found in this directory.'))
            # ✅ END of for loop - now process results

            # === STEP 4: Merge native and managed interpreters ===
            all_interpreters = {**native_interpreters, **interpreters}
            
            # Determine primary version (prefer native if it exists, otherwise latest managed)
            if native_interpreters:
                primary_version = sorted(native_interpreters.keys(), reverse=True)[0]
            elif interpreters:
                primary_version = sorted(interpreters.keys(), reverse=True)[0]
            else:
                primary_version = None
            
            # === STEP 5: Write the registry (ATOMICALLY) ===
            registry_data = {
                'primary_version': primary_version,
                'interpreters': all_interpreters,
                'last_updated': datetime.now().isoformat()
            }

            # Write to a temp file first, then atomic rename (prevents partial writes)
            import tempfile
            temp_fd, temp_path = tempfile.mkstemp(dir=registry_path.parent, suffix='.json')
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(registry_data, f, indent=4)
                
                # Atomic rename (ensures registry.json is never corrupt)
                os.replace(temp_path, registry_path)
                
                safe_print("   💾 Registry saved atomically")
            except Exception as e:
                safe_print(f"   ❌ Failed to write registry: {e}")
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise
            
            if all_interpreters:
                safe_print(_('   ✅ Registered {} Python interpreters (native + managed).').format(len(all_interpreters)))
                for version, path in sorted(all_interpreters.items()):
                    marker = ' (native)' if version in native_interpreters else ' (managed)'
                    safe_print(_('      - Python {}: {}{}').format(version, path, marker))
            else:
                safe_print(_('   ⚠️  No Python interpreters were found or could be registered.'))
            
            safe_print("   🔓 Released registry write lock")

    def _find_existing_python311(self) -> Optional[Path]:
        """Checks if a managed Python 3.11 interpreter already exists."""
        venv_path = Path(sys.prefix)
        expected_exe_path = self._get_interpreter_dest_path(venv_path) / ('python.exe' if platform.system() == 'windows' else 'bin/python3.11')
        if expected_exe_path.exists() and expected_exe_path.is_file():
            safe_print(_('✅ Found existing Python 3.11 interpreter.'))
            return expected_exe_path
        return None

    def get_interpreter_for_version(self, version: str) -> Optional[Path]:
        """
        Get the path to a specific Python interpreter version from the registry.
        """
        registry_path = self.venv_path / '.omnipkg' / 'interpreters' / 'registry.json'
        if not registry_path.exists():
            safe_print(_('   [DEBUG] Interpreter registry not found at: {}').format(registry_path))
            return None
        try:
            with open(registry_path, 'r') as f:
                registry = json.load(f)
            interpreter_path = registry.get('interpreters', {}).get(version)
            if interpreter_path and Path(interpreter_path).exists():
                return Path(interpreter_path)
        except (IOError, json.JSONDecodeError):
            pass
        return None

    def _find_project_root(self):
        """
        Find the project root directory by looking for setup.py, pyproject.toml, or .git
        """
        from pathlib import Path
        current_dir = Path.cwd()
        module_dir = Path(__file__).parent.parent
        search_paths = [current_dir, module_dir]
        
        for start_path in search_paths:
            for path in [start_path] + list(start_path.parents):
                # Check for pyproject.toml or setup.py FIRST (actual Python projects)
                if (path / 'pyproject.toml').exists() or (path / 'setup.py').exists():
                    # Verify it's actually an omnipkg project
                    if (path / 'pyproject.toml').exists():
                        if (path / 'pyproject.toml').exists():
                            try:
                                # Conditional import - handle both Python < 3.11 and >= 3.11
                                if sys.version_info < (3, 11):
                                    import tomli
                                else:
                                    import tomllib as tomli
                                
                                with open(path / 'pyproject.toml', 'rb') as f:
                                    data = tomli.load(f)
                                    if data.get('project', {}).get('name') == 'omnipkg':
                                        safe_print(_('     (Found project root: {})').format(path))
                                        return path
                            except (ImportError, ModuleNotFoundError):
                                # tomli/tomllib not available - just check if file exists
                                # Assume any pyproject.toml in the search path is omnipkg's
                                safe_print(_('     (Found project root: {})').format(path))
                                return path
                            except Exception:
                                # Any other error parsing - skip this candidate
                                continue
                    
                    # If we have setup.py, assume it's valid
                    elif (path / 'setup.py').exists():
                        # Quick heuristic: check if setup.py mentions omnipkg
                        try:
                            setup_content = (path / 'setup.py').read_text()
                            if 'omnipkg' in setup_content:
                                safe_print(_('     (Found project root: {})').format(path))
                                return path
                        except Exception:
                            continue
        
        safe_print(_('     (No project root found)'))
        return None

    def _install_essential_packages(self, python_exe: Path):
        """
        Installs essential packages for a new interpreter using a robust hybrid strategy.
        It installs dependencies first using the new interpreter's pip, then installs
        omnipkg itself without its dependencies to avoid resolver conflicts.
        """
        safe_print('📦 Bootstrapping essential packages for new interpreter...')

        def run_verbose(cmd: List[str], error_msg: str):
            """Helper to run a command and show its output."""
            safe_print(_('   🔩 Running: {}').format(' '.join(cmd)))
            try:
                result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
                return result
            except subprocess.CalledProcessError as e:
                safe_print(_('   ❌ {}').format(error_msg))
                safe_print('   --- Stdout ---')
                safe_print(e.stdout if e.stdout else '(empty)')
                safe_print('   --- Stderr ---')
                safe_print(e.stderr if e.stderr else '(empty)')
                safe_print('   ----------------')
                raise
        
        # Get Python version of the target interpreter
        version_result = subprocess.run(
            [str(python_exe), '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
            capture_output=True, text=True, timeout=30
        )
        target_py_version = version_result.stdout.strip()
        safe_print(f'   📌 Target interpreter: Python {target_py_version}')
        
        try:
            safe_print('   - Attempting bootstrap with built-in ensurepip (most reliable)...')
            ensurepip_cmd = [str(python_exe), '-m', 'ensurepip', '--upgrade']
            run_verbose(ensurepip_cmd, "ensurepip bootstrap failed.")
            safe_print('   ✅ Pip bootstrap complete via ensurepip.')
            
            core_deps = _get_core_dependencies()
            if core_deps:
                # Filter dependencies based on target Python version
                filtered_deps = []
                for dep in core_deps:
                    dep_lower = dep.lower()
                    # Skip uv for Python 3.7
                    if 'uv' in dep_lower and target_py_version.startswith('3.7'):
                        safe_print(f'   ⏭️  Skipping {dep} (not compatible with Python {target_py_version})')
                        continue
                    # Skip pip-audit for Python < 3.14
                    if 'pip-audit' in dep_lower and not target_py_version.startswith('3.14'):
                        safe_print(f'   ⏭️  Skipping {dep} (requires Python 3.14+, have {target_py_version})')
                        continue
                    # Skip safety 3.x for Python < 3.10
                    if 'safety' in dep_lower:
                        major, minor = map(int, target_py_version.split('.'))
                        if major == 3 and minor < 10:
                            # Use older safety for Python 3.7-3.9
                            filtered_deps.append('safety>=2.0.0,<3.0')
                            continue
                    filtered_deps.append(dep)
                
                if filtered_deps:
                    safe_print(_('   - Installing {} omnipkg core dependencies...').format(len(filtered_deps)))
                    deps_install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir'] + sorted(list(filtered_deps))
                    try:
                        run_verbose(deps_install_cmd, 'Failed to install omnipkg dependencies.')
                        safe_print(_('   ✅ Core dependencies installed.'))
                    except subprocess.CalledProcessError:
                        safe_print(_('   ⚠️  Some dependencies failed to install. Continuing anyway...'))
                
            safe_print(_('   - Installing omnipkg application layer...'))
            project_root = self._find_project_root()
            if project_root:
                safe_print(_('     (Developer mode detected: performing editable install)'))
                install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', '-e', str(project_root)]
            else:
                safe_print('     (Standard mode detected: installing from PyPI)')
                install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', 'omnipkg']
            run_verbose(install_cmd, 'Failed to install omnipkg application.')
            safe_print(_('   ✅ Omnipkg bootstrapped successfully!'))
            
        except Exception as e:
            safe_print(_('   ⚠️  Primary bootstrap failed: {}').format(e))
            safe_print(_('   - Falling back to get-pip.py bootstrap...'))
            
            try:
                with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w', encoding='utf-8') as tmp_file:
                    script_path = tmp_file.name
                    safe_print(_('   - Downloading get-pip.py...'))
                    # Use version-specific get-pip.py for Python 3.7
                    if target_py_version.startswith('3.7'):
                        pip_url = 'https://bootstrap.pypa.io/pip/3.7/get-pip.py'
                        safe_print(_('   - Using archived get-pip.py for Python 3.7'))
                    else:
                        pip_url = 'https://bootstrap.pypa.io/get-pip.py'
                    with urllib.request.urlopen(pip_url) as response:
                        tmp_file.write(response.read().decode('utf-8'))
                
                safe_print(_('   - Running get-pip.py...'))
                pip_cmd = [str(python_exe), script_path, '--no-cache-dir']
                result = run_verbose(pip_cmd, 'Failed to bootstrap pip.')
                os.unlink(script_path)
                safe_print(_('   ✅ Pip bootstrap complete via get-pip.py'))
                
                # Now try installing dependencies again
                core_deps = _get_core_dependencies()
                if core_deps:
                    # Filter dependencies based on target Python version (same logic as above)
                    filtered_deps = []
                    for dep in core_deps:
                        dep_lower = dep.lower()
                        if 'uv' in dep_lower and target_py_version.startswith('3.7'):
                            safe_print(f'   ⏭️  Skipping {dep} (not compatible with Python {target_py_version})')
                            continue
                        if 'pip-audit' in dep_lower and not target_py_version.startswith('3.14'):
                            safe_print(f'   ⏭️  Skipping {dep} (requires Python 3.14+, have {target_py_version})')
                            continue
                        if 'safety' in dep_lower:
                            major, minor = map(int, target_py_version.split('.'))
                            if major == 3 and minor < 10:
                                filtered_deps.append('safety>=2.0.0,<3.0')
                                continue
                        filtered_deps.append(dep)
                    
                    if filtered_deps:
                        safe_print(_('   - Installing {} omnipkg core dependencies...').format(len(filtered_deps)))
                        deps_install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir'] + sorted(list(filtered_deps))
                        try:
                            run_verbose(deps_install_cmd, 'Failed to install omnipkg dependencies.')
                            safe_print(_('   ✅ Core dependencies installed.'))
                        except subprocess.CalledProcessError:
                            safe_print(_('   ⚠️  Some dependencies failed to install. Continuing anyway...'))
                
                safe_print(_('   - Installing omnipkg application layer...'))
                project_root = self._find_project_root()
                if project_root:
                    safe_print(_('     (Developer mode detected: performing editable install)'))
                    install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', '-e', str(project_root)]
                else:
                    safe_print('     (Standard mode detected: installing from PyPI)')
                    install_cmd = [str(python_exe), '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', 'omnipkg']
                run_verbose(install_cmd, 'Failed to install omnipkg application.')
                safe_print(_('   ✅ Omnipkg bootstrapped successfully!'))
                
            except Exception as fallback_error:
                safe_print(_('❌ Both bootstrap methods failed!'))
                safe_print(_('   Primary error: {}').format(e))
                safe_print(_('   Fallback error: {}').format(fallback_error))
                raise

    def _create_omnipkg_executable(self, new_python_exe: Path, venv_path: Path):
        """
        Creates a proper shell script executable that forces the use of the new Python interpreter.
        """
        safe_print(_('🔧 Creating new omnipkg executable...'))
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
        safe_print(_('   ✅ New omnipkg executable created.'))

    def _update_default_python_links(self, venv_path: Path, new_python_exe: Path):
        """Updates the default python/python3 symlinks to point to Python 3.11."""
        safe_print(_('🔧 Updating default Python links...'))
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
        safe_print(_('   ✅ Default Python links updated to use Python {}.').format(version_str))

    def _auto_register_original_python(self, venv_path: Path) -> None:
        """
        Automatically detects and registers the original Python interpreter that was
        used to create this environment, without moving or copying it.
        """
        safe_print(_('🔍 Auto-detecting original Python interpreter...'))
        current_exe = Path(sys.executable).resolve()
        current_version = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
        major_minor = f'{sys.version_info.major}.{sys.version_info.minor}'
        safe_print(_('   - Detected: Python {} at {}').format(current_version, current_exe))
        interpreters_dir = venv_path / '.omnipkg' / 'interpreters'
        registry_path = venv_path / '.omnipkg' / 'python_registry.json'
        registry = {}
        if registry_path.exists():
            try:
                with open(registry_path, 'r') as f:
                    registry = json.load(f)
            except Exception as e:
                safe_print(f'   ⚠️  Warning: Could not load registry: {e}')
                registry = {}
        if major_minor in registry:
            safe_print(_('   ✅ Python {} already registered at: {}').format(major_minor, registry[major_minor]['path']))
            return
        managed_name = f'original-{current_version}'
        managed_dir = interpreters_dir / managed_name
        managed_dir.mkdir(parents=True, exist_ok=True)
        bin_dir = managed_dir / 'bin'
        bin_dir.mkdir(exist_ok=True)
        original_links = [('python', current_exe), (f'python{sys.version_info.major}', current_exe), (f'python{major_minor}', current_exe)]
        safe_print(_('   📝 Registering Python {} (original) without copying...').format(major_minor))
        for link_name, target in original_links:
            link_path = bin_dir / link_name
            if link_path.exists():
                link_path.unlink()
            try:
                link_path.symlink_to(target)
                safe_print(_('      ✅ Created symlink: {} -> {}').format(link_name, target))
            except Exception as e:
                safe_print(_('      ⚠️  Could not create symlink {}: {}').format(link_name, e))
        pip_candidates = [current_exe.parent / 'pip', current_exe.parent / f'pip{sys.version_info.major}', current_exe.parent / f'pip{major_minor}']
        for pip_path in pip_candidates:
            if pip_path.exists():
                pip_link = bin_dir / pip_path.name
                if not pip_link.exists():
                    try:
                        pip_link.symlink_to(pip_path)
                        safe_print(_('      ✅ Created pip symlink: {}').format(pip_path.name))
                        break
                    except Exception as e:
                        safe_print(_('      ⚠️  Could not create pip symlink: {}').format(e))
        registry[major_minor] = {'path': str(bin_dir / f'python{major_minor}'), 'version': current_version, 'type': 'original', 'source': str(current_exe), 'managed_dir': str(managed_dir), 'registered_at': datetime.now().isoformat()}
        try:
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            with open(registry_path, 'w') as f:
                json.dump(registry, f, indent=2)
            safe_print(_('   ✅ Registered Python {} in registry').format(major_minor))
        except Exception as e:
            safe_print(f'   ❌ Failed to save registry: {e}')
            return
        if hasattr(self, 'config') and self.config:
            managed_interpreters = self.config.get('managed_interpreters', {})
            managed_interpreters[major_minor] = str(bin_dir / f'python{major_minor}')
            self.set('managed_interpreters', managed_interpreters)
            safe_print(f'   ✅ Updated main config with Python {major_minor}')

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
        safe_print(_('🐍 Attempting to adopt Python {} into the environment...').format(version))
        if self._should_auto_register_python(version):
            safe_print(_('   🎯 Requested version matches current Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
            safe_print(_('   📋 Auto-registering current interpreter instead of downloading...'))
            try:
                self._auto_register_original_python(self.venv_path)
                safe_print(_('🎉 Successfully registered Python {} (original interpreter)!').format(version))
                safe_print(_("   You can now use 'omnipkg swap python {}'").format(version))
                return 0
            except Exception as e:
                safe_print(_('   ❌ Auto-registration failed: {}').format(e))
                safe_print(_('   🔄 Falling back to download strategy...'))
        return self._existing_adopt_logic(version)

    def _register_all_managed_interpreters(self) -> None:
        """
        Enhanced version that includes original interpreters in the scan.
        """
        safe_print(_('🔧 Registering all managed Python interpreters...'))
        interpreters_dir = self.venv_path / '.omnipkg' / 'interpreters'
        if not interpreters_dir.exists():
            safe_print(_('   ℹ️  No interpreters directory found.'))
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
            safe_print(_('   -> Scanning directory: {}').format(interpreter_dir.name))
            bin_dir = interpreter_dir / 'bin'
            if not bin_dir.exists():
                safe_print(_('      ⚠️  No bin/ directory found in {}').format(interpreter_dir.name))
                continue
            python_exe = None
            for candidate in bin_dir.glob('python[0-9].[0-9]*'):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    python_exe = candidate
                    break
            if not python_exe:
                safe_print(_('      ⚠️  No valid Python executable found in {}').format(interpreter_dir.name))
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
                        safe_print(_('      ✅ Found valid executable: {} ({})').format(python_exe, interpreter_type))
                    else:
                        safe_print(_('      ⚠️  Could not parse version from: {}').format(result.stdout.strip()))
                else:
                    safe_print(_('      ⚠️  Failed to get version: {}').format(result.stderr.strip()))
            except Exception as e:
                safe_print(_('      ⚠️  Error testing executable: {}').format(e))
        try:
            with open(registry_path, 'w') as f:
                json.dump(registry, f, indent=2)
        except Exception as e:
            safe_print(f'   ⚠️  Could not save registry: {e}')
        if managed_interpreters:
            self.set('managed_interpreters', managed_interpreters)
            safe_print(_('   ✅ Registered {} managed Python interpreters.').format(len(managed_interpreters)))
            for version, path in managed_interpreters.items():
                interpreter_type = registry.get(version, {}).get('type', 'unknown')
                safe_print(_('      - Python {}: {} ({})').format(version, path, interpreter_type))
        else:
            safe_print(_('   ℹ️  No managed interpreters found.'))

    
    def _install_managed_python(self, venv_path: Path, full_version: str, omnipkg_instance: 'omnipkg') -> Path:


        """
        Downloads and installs a specific, self-contained version of Python
        from the python-build-standalone project. Returns the path to the new executable.
        """
        safe_print(_('\n🚀 Installing managed Python {}...').format(full_version))
        system = platform.system().lower()
        arch = platform.machine().lower()
        py_arch_map = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}
        py_arch = py_arch_map.get(arch)
        if not py_arch:
            raise OSError(_('Unsupported architecture: {}').format(arch))
        
        # Base version to release tag mapping
        VERSION_TO_RELEASE_TAG_MAP = {
            '3.8.20': '20241002',
            '3.14.0': '20251014',
            '3.13.7': '20250818',
            '3.13.6': '20250807',
            '3.13.1': '20241211',
            '3.13.0': '20241016',
            '3.12.11': '20250818',
            '3.12.8': '20241211',
            '3.12.7': '20241008',
            '3.12.6': '20240814',
            '3.12.5': '20240726',
            '3.12.4': '20240726',
            '3.12.3': '20240415',
            '3.11.13': '20250603',
            '3.11.12': '20241211',
            '3.11.10': '20241008',
            '3.11.9': '20240726',
            '3.11.6': '20231002',
            '3.10.18': '20250818',
            '3.10.15': '20241008',
            '3.10.14': '20240726',
            '3.10.13': '20231002',
            '3.9.23': '20250818',
            '3.9.21': '20241211',
            '3.9.20': '20241008',
            '3.9.19': '20240726',
            '3.9.18': '20231002'
        }
        
        # OS-specific overrides for versions with platform-specific fixes
        # macOS uses 20200823 for 3.7.9 to avoid libintl.dylib dependency issue
        OS_SPECIFIC_RELEASE_TAGS = {
            'darwin': {
                '3.7.9': '20200823',  # macOS-specific fix for libintl.dylib issue
            },
            'linux': {
                '3.7.9': '20200822',
            },
            'windows': {
                '3.7.9': '20200822',
            }
        }
        
        # Normalize system name
        if system == 'macos':
            system = 'darwin'
        
        # Try OS-specific release tag first, then fall back to general mapping
        release_tag = None
        if system in OS_SPECIFIC_RELEASE_TAGS:
            release_tag = OS_SPECIFIC_RELEASE_TAGS[system].get(full_version)
        
        if not release_tag:
            release_tag = VERSION_TO_RELEASE_TAG_MAP.get(full_version)
        
        if not release_tag:
            available_versions = list(VERSION_TO_RELEASE_TAG_MAP.keys())
            safe_print(_('❌ No known standalone build for Python version {}.').format(full_version))
            safe_print(_('   Available versions: {}').format(', '.join(sorted(available_versions))))
            raise ValueError(f'No known standalone build for Python version {full_version}. Cannot download.')
        
        py_ver_plus_tag = f'{full_version}+{release_tag}'
        base_url = f'https://github.com/astral-sh/python-build-standalone/releases/download/{release_tag}'
        
        # Older releases (pre-2021) used different naming: -pgo instead of -install_only
        # The 20200822/20200823 releases use .tar.zst format with -pgo suffix
        # NOTE: Release 20200822 has files dated 20200823 in their filenames!
        use_legacy_naming = release_tag in ['20200822', '20200823']
        
        if use_legacy_naming:
            # Legacy format: cpython-X.Y.Z-platform-pgo-YYYYMMDDTHHMM.tar.zst
            # The 20200822 release has files with 20200823 timestamps
            file_date = '20200823' if release_tag == '20200822' else release_tag
            
            archive_name_templates = {
                'linux': f'cpython-{full_version}-{py_arch}-unknown-linux-gnu-pgo-{file_date}T0036.tar.zst',
                'darwin': f'cpython-{full_version}-{py_arch}-apple-darwin-pgo-{file_date}T2228.tar.zst',
                'windows': f'cpython-{full_version}-{py_arch}-pc-windows-msvc-shared-pgo-{file_date}T0118.tar.zst'
            }
        else:
            # Modern format: cpython-X.Y.Z+TAG-platform-install_only.tar.gz
            archive_name_templates = {
                'linux': f'cpython-{py_ver_plus_tag}-{py_arch}-unknown-linux-gnu-install_only.tar.gz',
                'darwin': f'cpython-{py_ver_plus_tag}-{py_arch}-apple-darwin-install_only.tar.gz',
                'windows': f'cpython-{py_ver_plus_tag}-{py_arch}-pc-windows-msvc-install_only.tar.gz'
            }
        
        archive_name = archive_name_templates.get(system)
        if not archive_name:
            raise OSError(_('Unsupported operating system: {}').format(system))
        
        url = f'{base_url}/{archive_name}'
        
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / archive_name
            safe_print(f'📥 Downloading Python {full_version} for {system.title()}...')
            safe_print(_('   - URL: {}').format(url))
            try:
                safe_print(_('   - Attempting download...'))
                urllib.request.urlretrieve(url, archive_path)
                if not archive_path.exists():
                    raise OSError(_('Download failed: file does not exist'))
                file_size = archive_path.stat().st_size
                if file_size < 1000000:
                    raise OSError(_('Downloaded file is too small ({} bytes), likely incomplete or invalid').format(file_size))
                safe_print(_('✅ Downloaded {} bytes').format(file_size))
                safe_print(_('   - Extracting archive...'))
                
                extract_path = Path(temp_dir) / 'extracted'
                
                # Legacy releases use .tar.zst, modern ones use .tar.gz
                if archive_name.endswith('.tar.zst'):
                    # For .tar.zst files, we need zstandard support
                    try:
                        import zstandard as zstd
                    except ImportError:
                        # --- INTERNAL QUANTUM HEALING ---
                        print_header("Dependency Healing Required")
                        safe_print("   - Diagnosis: The 'zstandard' package is needed for this operation.")
                        
                        original_context = f"{sys.version_info.major}.{sys.version_info.minor}"
                        safe_print(f"   - Current Context: Python {original_context}")

                        # We need to install zstandard for the Python that is RUNNING this script.
                        target_context = original_context
                        
                        safe_print(f"   - Action: Installing 'zstandard' for Python {target_context} context.")

                        # Temporarily switch to the correct context if needed. This is defensive.
                        # In this specific case, we are already in the right context, but this
                        # makes the logic reusable for other scenarios.
                        if omnipkg_instance.current_python_context != f"py{target_context}":
                            safe_print(f"   - Swapping to Python {target_context} to install dependency...")
                            if omnipkg_instance.switch_active_python(target_context) != 0:
                                raise OSError(f"Failed to switch to Python {target_context} to install dependency.")
                        
                        # Use the omnipkg instance to install the dependency
                        install_result = omnipkg_instance.smart_install(['zstandard'])
                        
                        if install_result != 0:
                            safe_print("   - ❌ Failed to install 'zstandard'. Aborting download.")
                            # Attempt to swap back if we switched
                            if omnipkg_instance.current_python_context.replace('py','') != original_context:
                                omnipkg_instance.switch_active_python(original_context)
                            raise OSError("Failed to install required dependency 'zstandard'.")
                        
                        safe_print("   - ✅ 'zstandard' installed successfully.")

                        # Now that the dependency is installed, we must re-launch the process
                        # so the new package is available on sys.path.
                        safe_print("\n   - 🚀 Restarting process to load the new package...")
                        
                        # Re-run the original command the user typed.
                        args_for_exec = [sys.executable] + sys.argv
                        
                        try:
                            # This replaces the current process with a new one.
                            os.execv(sys.executable, args_for_exec)
                        except Exception as e:
                            # If the handoff fails, instruct the user.
                            safe_print(f"   - ⚠️  Automatic restart failed: {e}")
                            safe_print("   - Please re-run your command to continue.")
                            # Exit gracefully.
                            sys.exit(1)
                        safe_print("─" * 60)
                        # --- END OF SELF-HEALING LOGIC ---
                    
                    safe_print(_('   - Decompressing zstd archive...'))
                    with open(archive_path, 'rb') as ifh:
                        dctx = zstd.ZstdDecompressor()
                        with tempfile.NamedTemporaryFile(suffix='.tar', delete=False) as ofh:
                            decompressed_path = Path(ofh.name)
                            dctx.copy_stream(ifh, ofh)
                    
                    safe_print(_('   - Extracting tar archive...'))
                    with tarfile.open(decompressed_path, 'r:') as tar:
                        tar.extractall(extract_path)
                    
                    # Clean up decompressed tar file
                    decompressed_path.unlink()
                else:
                    # Modern .tar.gz format
                    with tarfile.open(archive_path, 'r:gz') as tar:
                        tar.extractall(extract_path)
                
                # Legacy releases have a different directory structure
                # Modern: extract_path/python/...
                # Legacy: extract_path/python/install/... (the actual Python is in an 'install' subdirectory)
                if use_legacy_naming:
                    # Legacy structure: check for install subdirectory
                    possible_install_dirs = [
                        extract_path / 'python' / 'install',
                        extract_path / 'install',
                        extract_path / 'python'
                    ]
                    source_python_dir = None
                    for possible_dir in possible_install_dirs:
                        if possible_dir.exists() and (possible_dir / 'bin').exists():
                            source_python_dir = possible_dir
                            break
                    
                    if not source_python_dir:
                        # Debug: show what we actually extracted
                        extracted_contents = list(extract_path.rglob('*'))[:20]  # First 20 items
                        safe_print(_('   - Debug: Extracted contents: {}').format([str(p.relative_to(extract_path)) for p in extracted_contents]))
                        raise OSError(_('Could not find valid python installation directory in legacy archive'))
                else:
                    # Modern structure
                    source_python_dir = extract_path / 'python'
                    if not source_python_dir.exists():
                        possible_dirs = list(extract_path.glob('**/python'))
                        if possible_dirs:
                            source_python_dir = possible_dirs[0]
                        else:
                            raise OSError(_('Could not find python directory in extracted archive'))
                
                python_dest = venv_path / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'
                safe_print(_('   - Installing to: {}').format(python_dest))
                python_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_python_dir, python_dest, dirs_exist_ok=True)
                
                python_exe_candidates = []
                if system == 'windows':
                    python_exe_candidates = [python_dest / 'python.exe', python_dest / 'Scripts/python.exe']
                else:
                    python_exe_candidates = [python_dest / 'bin/python3', python_dest / 'bin/python', python_dest / f"bin/python{full_version.split('.')[0]}.{full_version.split('.')[1]}"]
                
                python_exe = None
                for candidate in python_exe_candidates:
                    if candidate.exists():
                        python_exe = candidate
                        break
                
                if not python_exe:
                    raise OSError(_('Python executable not found in expected locations: {}').format([str(c) for c in python_exe_candidates]))
                
                if system != 'windows':
                    python_exe.chmod(493)
                    major_minor = '.'.join(full_version.split('.')[:2])
                    versioned_symlink = python_exe.parent / f'python{major_minor}'
                    if not versioned_symlink.exists():
                        try:
                            versioned_symlink.symlink_to(python_exe.name)
                        except OSError as e:
                            safe_print(_('   - Warning: Could not create versioned symlink: {}').format(e))
                
                safe_print(_('   - Testing installation...'))
                result = subprocess.run([str(python_exe), '--version'], capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    raise OSError(_('Python executable test failed: {}').format(result.stderr))
                safe_print(_('   - ✅ Python version: {}').format(result.stdout.strip()))
                
                self._install_essential_packages(python_exe)
                
                safe_print(_('\n✨ New interpreter bootstrapped.'))
                try:
                    safe_print(_('🔧 Forcing rescan to register the new interpreter...'))
                    self._register_all_interpreters(self.venv_path)
                    safe_print(_('   ✅ New interpreter registered successfully.'))
                except Exception as e:
                    safe_print(_('   ⚠️  Interpreter registration failed: {}').format(e))
                    import traceback
                    traceback.print_exc()
                
                major_minor_version = '.'.join(full_version.split('.')[:2])
                self._set_rebuild_flag_for_version(major_minor_version)
                return python_exe
                
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    safe_print(_('❌ Python {} not found in python-build-standalone releases.').format(full_version))
                    safe_print(_('   This might be a very new version. Check https://github.com/indygreg/python-build-standalone/releases'))
                    safe_print(_('   for available versions.'))
                raise OSError(_('HTTP error downloading Python: {} - {}').format(e.code, e.reason))
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
        from .common_utils import safe_print
        try:
            # --- THIS IS THE FIX ---
            # The subprocess is an isolated environment and only knows built-in functions.
            # We must use 'print', not 'safe_print', inside the command string.
            command_string = 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
            
            result = subprocess.run(
                [python_path, '-c', command_string],
                capture_output=True, text=True, timeout=10
            )
            # --- END FIX ---

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

    
    def _get_sensible_defaults(self, python_exe_override: str = None) -> Dict:
        """
        (CORRECTED) Generates sensible default configuration paths based STRICTLY on the
        currently active virtual environment to ensure safety and prevent discovery conflicts.
        """
        active_python_exe = sys.executable

        # --- THIS IS THE CRITICAL FIX ---
        # First, get the paths for the currently running, active interpreter. This is ground truth.
        calculated_paths = self._get_paths_for_interpreter(active_python_exe)
        
        if not calculated_paths:
            # If the primary method fails, fall back to a safer detection within the current env.
            safe_print(_(' ⚠️ Falling back to basic path detection within the current environment.'))
            site_packages = str(self.get_actual_current_site_packages())
            calculated_paths = {
                'site_packages_path': site_packages,
                'multiversion_base': str(Path(site_packages) / '.omnipkg_versions'),
                'python_executable': sys.executable
            }
    
        # Now, find other interpreters, but ensure our active one takes precedence.
        all_pythons = self.list_available_pythons() or {}
        active_version_str = f'{sys.version_info.major}.{sys.version_info.minor}'
        all_pythons[active_version_str] = active_python_exe # Force override
    
        return {
            **calculated_paths,
            'python_interpreters': all_pythons,
            'preferred_python_version': f'{self._preferred_version[0]}.{self._preferred_version[1]}',
            'builder_script_path': str(Path(__file__).parent / 'package_meta_builder.py'),
            'redis_host': 'localhost',
            'redis_port': 6379,
            'redis_key_prefix': 'omnipkg:pkg:',
            'redis_enabled': True,
            'install_strategy': 'stable-main',
            'uv_executable': 'uv',
            'paths_to_index': self._get_bin_paths(),
            'language': self._get_system_lang_code(),
            'enable_python_hotswap': True
        }


    def get_actual_current_site_packages(self) -> Path:
        """
        Gets the ACTUAL site-packages directory for the currently running Python interpreter.
        This is more reliable than calculating it from sys.prefix when hotswapping is involved.
        Cross-platform compatible with special handling for Windows.
        """
        import platform
        is_windows = platform.system() == 'Windows'
        
        try:
            # Method 1: Use site.getsitepackages() - most reliable method
            site_packages_list = site.getsitepackages()
            if site_packages_list:
                current_python_dir = Path(sys.executable).parent
                
                # Find the site-packages that belongs to our current Python
                for sp in site_packages_list:
                    sp_path = Path(sp)
                    try:
                        # Check if this site-packages is under our Python installation
                        sp_path.relative_to(current_python_dir)
                        # Additional validation: check if it actually contains packages
                        if sp_path.exists():
                            return sp_path
                    except ValueError:
                        continue
                
                # If relative path matching fails, prefer paths that actually exist
                # and sort by specificity (longer paths first)
                existing_paths = [Path(sp) for sp in site_packages_list if Path(sp).exists()]
                if existing_paths:
                    # For Windows, prefer 'lib' over 'Lib' when both exist (lowercase is more standard)
                    if is_windows and len(existing_paths) > 1:
                        lib_paths = [p for p in existing_paths if 'lib' in str(p).lower()]
                        lowercase_lib = [p for p in lib_paths if '/lib/' in str(p) or '\\lib\\' in str(p)]
                        if lowercase_lib:
                            return sorted(lowercase_lib, key=len, reverse=True)[0]
                    
                    return sorted(existing_paths, key=len, reverse=True)[0]
                
                # Fallback to first path (even if it doesn't exist yet)
                return Path(site_packages_list[0])
                
        except Exception:
            # Continue with fallback logic
            pass
        
        # Method 2: Try to find an existing package and derive site-packages from it
        try:
            # Look for a common package that should exist
            common_packages = ['pip', 'setuptools', 'packaging']
            for pkg_name in common_packages:
                try:
                    pkg = __import__(pkg_name)
                    if hasattr(pkg, '__file__') and pkg.__file__:
                        pkg_path = Path(pkg.__file__).parent
                        # Navigate up to find site-packages
                        current = pkg_path
                        while current.parent != current:
                            if current.name == 'site-packages':
                                return current
                            current = current.parent
                except ImportError:
                    continue
        except Exception:
            pass
        
        # Method 3: Check sys.path for site-packages directories
        try:
            for path_str in sys.path:
                if path_str and 'site-packages' in path_str:
                    path_obj = Path(path_str)
                    if path_obj.exists() and path_obj.name == 'site-packages':
                        return path_obj
        except Exception:
            pass
        
        # Method 4: Manual construction based on OS (fallback)
        python_version = f'python{sys.version_info.major}.{sys.version_info.minor}'
        current_python_path = Path(sys.executable)
        
        # Handle omnipkg's own interpreter management
        if '.omnipkg/interpreters' in str(current_python_path):
            interpreter_root = current_python_path.parent.parent
            if is_windows:
                # Try both case variations for Windows
                candidates = [
                    interpreter_root / 'lib' / 'site-packages',  # Prefer lowercase
                    interpreter_root / 'Lib' / 'site-packages'   # Windows standard
                ]
                for candidate in candidates:
                    if candidate.exists():
                        return candidate
                # Default to lowercase if neither exists
                return interpreter_root / 'lib' / 'site-packages'
            else:
                return interpreter_root / 'lib' / python_version / 'site-packages'
        else:
            # Standard environment detection
            venv_path = Path(sys.prefix)
            
            if is_windows:
                # Windows has multiple possible locations, try in order of preference
                # Based on the debug output, both 'lib' and 'Lib' exist, prefer 'lib' (lowercase)
                candidates = [
                    venv_path / 'lib' / 'site-packages',  # Prefer lowercase (more standard)
                    venv_path / 'Lib' / 'site-packages',  # Windows default
                    venv_path / 'lib' / python_version / 'site-packages'  # Version-specific
                ]
                
                for candidate in candidates:
                    if candidate.exists():
                        return candidate
                
                # If none exist, default to lowercase (more portable)
                return venv_path / 'lib' / 'site-packages'
            else:
                # Unix-like systems (Linux, macOS)
                return venv_path / 'lib' / python_version / 'site-packages'
    
    def _get_paths_for_interpreter(self, python_exe_path: str) -> Optional[Dict[str, str]]:
        """
        Runs an interpreter in a subprocess to ask for its version and calculates its site-packages path.
        This is the only reliable way to get paths for an interpreter that isn't the currently running one.
        """
        from .common_utils import safe_print
        import platform
        
        try:
            # Step 1: Get version and prefix (this part works fine)
            cmd = [python_exe_path, '-I', '-c', 
                   "import sys, json; print(json.dumps({'version': f'{sys.version_info.major}.{sys.version_info.minor}', 'prefix': sys.prefix}))"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            interp_info = json.loads(result.stdout)
    
            # Step 2: Ask the interpreter for its site-packages path authoritatively.
            site_packages_cmd = [
                python_exe_path, '-I', '-c',
                "import site, sys, json; print(json.dumps(site.getsitepackages() or [sp for sp in sys.path if 'site-packages' in sp or 'dist-packages' in sp]))"
            ]
            sp_result = subprocess.run(site_packages_cmd, capture_output=True, text=True, check=True, timeout=10)
            sp_list = json.loads(sp_result.stdout)
    
            if not sp_list:
                raise RuntimeError("Subprocess could not determine site-packages location.")
    
            # Platform-aware path selection
            is_windows = platform.system() == 'Windows'
            site_packages_path = None
            
            if is_windows:
                # Windows: Must contain 'site-packages' and prefer lib/site-packages over Lib/site-packages
                # This logic is CRITICAL for Windows CI - do not modify
                candidates = [p for p in sp_list if 'site-packages' in p and Path(p).exists()]
                
                # Prefer lowercase 'lib' path on Windows as it's more standard
                for path in candidates:
                    if '\\lib\\site-packages' in path:
                        site_packages_path = Path(path)
                        break
                
                # Fallback to any valid site-packages path
                if not site_packages_path and candidates:
                    site_packages_path = Path(candidates[0])
            else:
                # Linux/Mac: Accept site-packages OR dist-packages (Debian/Ubuntu use dist-packages)
                for path in sp_list:
                    path_lower = path.lower()
                    if ('site-packages' in path_lower or 'dist-packages' in path_lower) and Path(path).exists():
                        site_packages_path = Path(path)
                        break
            
            if not site_packages_path:
                raise RuntimeError(f"No valid site-packages directory found in {sp_list} (platform: {platform.system()})")
    
            return {
                'site_packages_path': str(site_packages_path),
                'multiversion_base': str(site_packages_path / '.omnipkg_versions'),
                'python_executable': python_exe_path
            }
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, KeyError, RuntimeError) as e:
            error_details = f'Error: {e}'
            if isinstance(e, subprocess.CalledProcessError):
                error_details += f'\nSTDERR:\n{e.stderr}'
            safe_print(f'⚠️  Could not determine paths for interpreter {python_exe_path}: {error_details}')
            return None

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
        """
        Interactive setup that keeps the native Python in its original location
        and only manages additional Python versions.
        """
        safe_print(_('💡 Grounding configuration in the current active environment...'))
        
        # Use the ACTUAL active Python executable, not a managed copy
        native_python_exe = Path(sys.executable).resolve()
        
        # Find the venv's actual Python (not system Python)
        venv_python = self.venv_path / 'bin' / f'python{sys.version_info.major}.{sys.version_info.minor}'
        if not venv_python.exists():
            venv_python = self.venv_path / 'bin' / 'python3'
        if not venv_python.exists():
            venv_python = self.venv_path / 'bin' / 'python'
        
        if venv_python.exists():
            native_python_exe = venv_python
            safe_print(_(' ✅ Using: {} (Your active interpreter)').format(native_python_exe))
        else:
            safe_print(_(' ⚠️  Using: {} (System interpreter)').format(native_python_exe))
        
        # --- PLATFORM-SPECIFIC INTERPRETER ADOPTION ---
        native_version = f'{sys.version_info.major}.{sys.version_info.minor}'
        major_minor_micro = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
        
        if platform.system() == 'Windows':
            # --- WINDOWS-SPECIFIC LOGIC ---
            
            # Check if the current Python is already in a managed/isolated location
            # (like GitHub Actions hosted runners, or already managed by omnipkg)
            is_already_managed = (
                'hostedtoolcache' in str(native_python_exe).lower() or  # GitHub Actions
                'omnipkg' in str(native_python_exe).lower() or          # Already managed
                'AppData\\Local\\Programs' in str(native_python_exe) or # User installation
                str(native_python_exe).startswith(str(self.venv_path))  # Already in venv
            )
            
            if is_already_managed:
                safe_print(_('   - Python is already in a managed location, using directly'))
                safe_print(_('   - ✅ Using: {}').format(native_python_exe))
                
                # Just register it without copying
                managed_interpreters_dir = self.venv_path / '.omnipkg' / 'interpreters'
                managed_interpreters_dir.mkdir(parents=True, exist_ok=True)
                
                registry_path = managed_interpreters_dir / 'registry.json'
                registry_data = {'interpreters': {}}
                if registry_path.exists():
                    try:
                        with open(registry_path, 'r') as f:
                            registry_data = json.load(f)
                    except (json.JSONDecodeError, IOError):
                        pass
                
                # Register the native path
                registry_data['interpreters'][native_version] = str(native_python_exe)
                
                with open(registry_path, 'w') as f:
                    json.dump(registry_data, f, indent=4)
                
                safe_print(_('   - ✅ Registered Python {} without copying').format(native_version))
                managed_python_exe_str = str(native_python_exe)
                
            else:
                # Only create managed copy for system Python in problematic locations
                safe_print(_('   - Setting up managed Python interpreter for Windows...'))
                
                # Use a short, stable path to avoid MAX_PATH issues
                managed_base_dir = Path.home() / 'AppData' / 'Local' / 'omnipkg' / 'interpreters'
                managed_base_dir.mkdir(parents=True, exist_ok=True)
                
                dest_path = managed_base_dir / f'cpython-{major_minor_micro}'
                managed_python_exe = dest_path / 'Scripts' / 'python.exe'
                
                # Check if a managed copy already exists
                if not managed_python_exe.exists():
                    safe_print(_('   - Creating managed copy at: {}').format(dest_path))
                    try:
                        import venv
                        # Create a full, standalone copy (no symlinks, no admin rights needed)
                        venv.create(dest_path, with_pip=True, symlinks=False, clear=True)
                        
                        # Verify the copy works
                        if not managed_python_exe.exists():
                            raise OSError("venv.create succeeded but python.exe not found")
                        
                        result = subprocess.run(
                            [str(managed_python_exe), '--version'],
                            check=True,
                            capture_output=True,
                            text=True
                        )
                        safe_print(_('   - ✅ Managed Python copy created: {}').format(result.stdout.strip()))
                        
                    except Exception as e:
                        safe_print(_('   - ❌ FATAL: Failed to create managed Python copy: {}').format(e))
                        if dest_path.exists():
                            import shutil
                            shutil.rmtree(dest_path, ignore_errors=True)
                        raise RuntimeError(f"Could not create managed Python environment on Windows: {e}") from e
                else:
                    safe_print(_('   - ✅ Found existing managed Python copy'))
                
                # Register this interpreter for version swapping
                try:
                    self._register_all_interpreters(managed_base_dir)
                except Exception as e:
                    safe_print(_('   - ⚠️  Warning: Could not register interpreter: {}').format(e))
                
                # Use the managed copy for configuration
                managed_python_exe_str = str(managed_python_exe)
                           
        else:
            # --- MACOS/LINUX LOGIC ---
            safe_print(_('   - Registering native Python for future version swapping...'))
            
            try:
                # This will now correctly write the native interpreter's path to the registry.json file.
                self._register_and_link_existing_interpreter(Path(sys.executable), native_version)
            except Exception as e:
                safe_print(_('   - ⚠️ Failed to adopt Python {}: {}').format(native_version, e))
                # If registration fails, we can't proceed.
                raise RuntimeError("FATAL: First-time setup failed during interpreter registration.") from e

            # Now, when we look up the interpreter, it will be found in the registry.
            registered_path = self.get_interpreter_for_version(native_version)
            
            if not registered_path:
                # This error should no longer happen, but it's a critical safety check.
                raise RuntimeError("FATAL: First-time setup failed. Could not find the adopted Python interpreter after registration.")
            
            managed_python_exe_str = str(registered_path)
        
        # --- CREATE THE CONFIGURATION ---
        self.config_dir.mkdir(parents=True, exist_ok=True)
        defaults = self._get_sensible_defaults(managed_python_exe_str)
        final_config = defaults.copy()
        
        # Interactive prompts (if not in CI)
        if interactive and (not os.environ.get('CI')):
            safe_print(_("🌍 Welcome to omnipkg! Let's get you configured."))
            safe_print('-' * 60)
            
            available_pythons = defaults['python_interpreters']
            if len(available_pythons) > 1:
                safe_print(_('🐍 Discovered Python interpreters:'))
                for version, path in available_pythons.items():
                    marker = ' ⭐' if version == defaults['preferred_python_version'] else ''
                    safe_print(_('   Python {}: {}{}').format(version, path, marker))
                safe_print()
            
            safe_print('Auto-detecting paths for your environment. Press Enter to accept defaults.\n')
            
            safe_print(_('📦 Choose your default installation strategy:'))
            safe_print(_('   1) stable-main:  Prioritize a stable main environment. (Recommended)'))
            safe_print(_('   2) latest-active: Prioritize having the latest versions active.'))
            strategy = input(_('   Enter choice (1 or 2) [1]: ')).strip() or '1'
            final_config['install_strategy'] = 'stable-main' if strategy == '1' else 'latest-active'
            
            bubble_path = input(f"Path for version bubbles [{defaults['multiversion_base']}]: ").strip() or defaults['multiversion_base']
            final_config['multiversion_base'] = bubble_path
            
            python_path = input(_('Python executable path [{}]: ').format(defaults['python_executable'])).strip() or defaults['python_executable']
            final_config['python_executable'] = python_path
            
            redis_choice = input(_('⚡️ Attempt to use Redis for high-performance caching? (y/n) [y]: ')).strip().lower()
            final_config['redis_enabled'] = redis_choice != 'n'
            
            if final_config['redis_enabled']:
                while True:
                    host_input = input(_('   -> Redis host [{}]: ').format(defaults['redis_host'])) or defaults['redis_host']
                    try:
                        import socket
                        socket.gethostbyname(host_input)
                        final_config['redis_host'] = host_input
                        break
                    except socket.gaierror:
                        safe_print(_("      ❌ Error: Invalid hostname '{}'. Please try again.").format(host_input))
                
                final_config['redis_port'] = int(input(_('   -> Redis port [{}]: ').format(defaults['redis_port'])) or defaults['redis_port'])
            
            hotswap_choice = input(_('Enable Python interpreter hotswapping? (y/n) [y]: ')).strip().lower()
            final_config['enable_python_hotswap'] = hotswap_choice != 'n'
        
        # Save configuration
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
            safe_print(_('\n✅ Configuration saved to {}.').format(self.config_path))
            safe_print(_('   You can edit this file manually later.'))
            safe_print(_('🧠 Initializing omnipkg knowledge base...'))
            safe_print(_('   This may take a moment with large environments.'))
            safe_print(_('   💡 Future startups will be instant!'))
        
        # Initialize knowledge base
        rebuild_cmd = [str(final_config['python_executable']), '-m', 'omnipkg.cli', 'reset', '-y']
        try:
            if interactive and (not os.environ.get('CI')):
                process = subprocess.Popen(rebuild_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output and ('Processing' in output or 'Building' in output or 'Scanning' in output):
                        safe_print(_('   {}').format(output.strip()))
                process.wait()
                if process.returncode != 0:
                    safe_print(_('   ⚠️  Knowledge base initialization encountered issues but continuing...'))
            else:
                subprocess.run(rebuild_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            if interactive and (not os.environ.get('CI')):
                safe_print(_('   ⚠️  Knowledge base will be built on first command usage instead.'))
            pass
        
        return final_config

    def _load_or_create_env_config(self, interactive: bool=True) -> Dict:
        """
        Loads the config for the current environment from the global config file.
        If the environment is not registered, triggers the first-time setup for it.
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
                safe_print(_('⚠️ Warning: Global config file is corrupted. Starting fresh.'))
        if self.env_id in full_config.get('environments', {}):
            return full_config['environments'][self.env_id]
        else:
            if interactive:
                safe_print(_('👋 New environment detected (ID: {}). Starting first-time setup.').format(self.env_id))
            return self._first_time_setup(interactive=interactive)

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

    # CLASS-LEVEL LOCK (shared across all instances)
    _registry_write_lock = threading.Lock()

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.venv_path = config_manager.venv_path
        self._registry_cache: Optional[Dict[str, Path]] = None


    def refresh_registry(self):
        """
        FIX: Clears the in-memory cache of interpreters, forcing a re-read from disk on the next call.
        """
        self._registry_cache = None
        safe_print("   - 🔄 Interpreter registry cache invalidated.")

    def list_available_interpreters(self) -> Dict[str, Path]:
        """
        Returns a dict of version -> path for all available interpreters.
        Now uses an in-memory cache that can be refreshed.
        THREAD-SAFE for reads (cache prevents unnecessary disk I/O).
        """
        # Return from cache if it's already loaded
        if self._registry_cache is not None:
            return self._registry_cache

        registry_path = self.venv_path / '.omnipkg' / 'interpreters' / 'registry.json'
        if not registry_path.exists():
            self._registry_cache = {}
            return {}
        
        try:
            # ACQUIRE LOCK BEFORE READING (to prevent reading during a write)
            with self._registry_write_lock:
                with open(registry_path, 'r') as f:
                    registry = json.load(f)
            
            interpreters = {}
            for version, path_str in registry.get('interpreters', {}).items():
                path = Path(path_str)
                if path.exists():
                    interpreters[version] = path
            
            # Load into cache
            self._registry_cache = interpreters
            return interpreters
        except Exception as e:
            safe_print(f"   ⚠️ Failed to read registry: {e}")
            self._registry_cache = {}
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
                    safe_print(_('    ⚠️ Warning: Failed to load path registry, starting fresh.'))
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

    def create_isolated_bubble(self, package_name: str, target_version: str, python_context_version: str) -> bool:
        """
        --- [REWRITTEN] ---
        Creates an isolated bubble by using the new unified, safe installation workflow.
        """
        safe_print(_('🫧 Creating isolated bubble for {} v{} (Python {} context)').format(package_name, target_version, python_context_version))
        
        bubble_path = self.multiversion_base / f'{package_name}-{target_version}'
        
        # The new unified function handles everything: temp dirs, modern install, Time Machine fallback, and creation.
        success = self.install_and_verify(
            package_name,
            target_version,
            python_context_version,
            destination_path=bubble_path
        )
        
        return success

    def _install_exact_version_tree(self, package_name: str, version: str, target_path: Path) -> bool:
        try:
            historical_deps = self._get_historical_dependencies(package_name, version)
            install_specs = ['{}=={}'.format(package_name, version)] + historical_deps
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--target', str(target_path)] + install_specs
            safe_print(_('    📦 Installing full dependency tree to temporary location...'))
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                safe_print(_('    ❌ Failed to install exact version tree: {}').format(result.stderr))
                return False
            return True
        except Exception as e:
            safe_print(_('    ❌ Unexpected error during installation: {}').format(e))
            return False
        
    def create_bubble_from_main_env(self, package_name: str, version: str, python_context_version: str) -> bool:
        """
        Creates a bubble by copying the package and its co-located dependencies
        from the current main environment. Used after Time Machine installs to main env.
        """
        site_packages = Path(self.parent_omnipkg.config['python_executable']).parent.parent / 'lib' / f'python{python_context_version}' / 'site-packages'
        
        final_bubble_path = self.multiversion_base / f'{package_name}-{version}'
        
        if final_bubble_path.exists():
            shutil.rmtree(final_bubble_path)
        
        final_bubble_path.mkdir(parents=True, exist_ok=True)
        
        # Get all packages currently in main env
        current_packages = self.parent_omnipkg.get_installed_packages(live=True)
        
        # Copy EVERYTHING from site-packages to the bubble
        # BUT SKIP OTHER BUBBLES AND OMNIPKG INTERNALS
        safe_print(f"   - Copying complete universe from main environment...")
        
        # CRITICAL: Items to skip
        skip_items = {
            '.omnipkg_versions',  # Don't copy other bubbles!
            '.omnipkg',           # Don't copy omnipkg internals
            '__pycache__',        # Don't need cache
        }
        
        files_copied = 0
        for item in site_packages.iterdir():
            # FILTER OUT DANGEROUS ITEMS
            if item.name in skip_items:
                safe_print(f"   - Skipping: {item.name}")
                continue
            
            try:
                if item.is_dir():
                    shutil.copytree(item, final_bubble_path / item.name, dirs_exist_ok=True)
                    files_copied += 1
                elif item.is_file() and (item.suffix == '.pth' or item.suffix == '.py'):
                    shutil.copy2(item, final_bubble_path / item.name)
                    files_copied += 1
            except Exception as e:
                safe_print(f"   - Warning: Could not copy {item.name}: {e}")
                continue
        
        safe_print(f"   - Copied {files_copied} items to bubble")
        
        # Create manifest
        self._create_bubble_manifest(
            final_bubble_path,
            package_name,
            version,
            python_context_version
        )
        
        # Register with hook manager
        self.parent_omnipkg.hook_manager.refresh_bubble_map(
            package_name, 
            version, 
            str(final_bubble_path)
        )
        self.parent_omnipkg.hook_manager.validate_bubble(package_name, version)
        
        return True

    def install_and_verify(self, package_name: str, version: str, python_context_version: str, destination_path: Path):
        """
        The one true installation function. Installs to a temp directory, verifies with an
        import test, and then moves the result. Contains the Time Machine fallback.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # --- STAGE 1: Attempt a modern, but potentially broken, installation ---
            safe_print(f"   - Attempting modern install for {package_name}=={version} into temp directory...")
            return_code, captured_output = self.parent_omnipkg._run_pip_install(
                [f"{package_name}=={version}"],
                target_directory=temp_path
            )

            install_ok = (return_code == 0)
            import_test_passed = False
            if install_ok:
                # --- NEW VERIFICATION STEP ---
                import_test_passed = self.parent_omnipkg._run_post_install_import_test(
                    package_name, install_dir_override=temp_path
                )
                if not import_test_passed:
                    safe_print(f"❌ Post-install import test failed. The package is likely broken.")

            # --- STAGE 2: If install failed OR import test failed, engage the Time Machine ---
            if not install_ok or not import_test_passed:
                error_output = captured_output.get("stderr", "").lower()
                build_failure_indicators = ['metadata-generation-failed', 'failed building wheel', 'setup.py egg_info']
                
                # Trigger on explicit build failure OR a failed import test
                if any(indicator in error_output for indicator in build_failure_indicators):
                    print_header(f"TIME MACHINE FALLBACK: Detected legacy build failure for {package_name}=={version}")
                elif not import_test_passed:
                    print_header(f"TIME MACHINE FALLBACK: Detected broken install for {package_name}=={version}")

                # Call the Time Machine, telling it to install into our SAFE temp directory
                time_machine_succeeded = self.parent_omnipkg._run_historical_install_fallback(
                    package_name, version, target_directory_override=temp_path
                )
                
                if not time_machine_succeeded:
                    safe_print(f"❌ Time Machine failed. The installation for {package_name}=={version} is unrecoverable.")
                    return False
                else:
                    safe_print(f"✅ Time Machine successfully healed the installation of {package_name}=={version}.")
            
            # --- STAGE 3: Analyze and create the final installation/bubble ---
            # At this point, temp_path contains either a working modern install or a healed historical one.
            safe_print("   - Analyzing final installation...")
            installed_tree = self._analyze_installed_tree(temp_path)
            
            safe_print(f"   - Moving verified package to final destination: {destination_path}")
            if destination_path.exists():
                shutil.rmtree(destination_path)

            return self._create_deduplicated_bubble(
                installed_tree, 
                destination_path,
                temp_path, 
                python_context_version=python_context_version
            )

    def create_bubble_for_package(self, package_name: str, version: str, python_context_version: str) -> bool:
        """
        Creates a complete, isolated bubble for a package, including all its dependencies.
        For editable/dev installs, copies directly from the source directory.
        """
        safe_print(f"🫧 Creating complete bubble for {package_name} v{version}...")
        
        install_source = None
        is_editable = False
        
        # --- CHECK IF THIS IS A DEV ENVIRONMENT (even if not currently installed) ---
        if package_name == 'omnipkg':  # Special handling for omnipkg itself
            project_root = self.parent_omnipkg.config_manager._find_project_root()
            if project_root and (project_root / 'pyproject.toml').exists():
                # We're in a dev environment - use the source
                install_source = str(project_root)
                is_editable = True
                safe_print(f"   - Detected development environment: {install_source}")
        
        # --- FALLBACK: Check if currently installed version is editable ---
        if not is_editable:
            try:
                dist = importlib.metadata.distribution(package_name)
                direct_url_json = dist.read_text('direct_url.json')
                if direct_url_json:
                    project_root = self.parent_omnipkg.config_manager._find_project_root()
                    if project_root and (project_root / 'pyproject.toml').exists():
                        install_source = str(project_root)
                        is_editable = True
                        safe_print(f"   - Detected local development source: {install_source}")
            except (importlib.metadata.PackageNotFoundError, TypeError, FileNotFoundError):
                pass

        if not install_source:
            install_source = f"{package_name}=={version}"
            safe_print(f"   - Using PyPI as source: {install_source}")

        # --- SPECIAL HANDLING FOR EDITABLE INSTALLS ---
        if is_editable:
            return self._create_bubble_from_editable_install(
                package_name, version, install_source, python_context_version
            )
        
        # --- NORMAL PYPI BUBBLE CREATION ---
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            safe_print(f"   - Installing full dependency tree to temporary location...")
            cmd = [
                self.parent_omnipkg.config['python_executable'], '-m', 'pip', 'install',
                '--target', str(temp_path),
                install_source
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                safe_print(f'   - ❌ Failed to create temporary installation for "{install_source}".')
                safe_print("--- Pip Error ---")
                safe_print(result.stderr)
                safe_print("-----------------")
                if "No matching distribution found" in result.stderr:
                    safe_print("   - 💡 This package version is not available on PyPI.")
                return False

            installed_tree = self._analyze_installed_tree(temp_path)
            bubble_path = self.multiversion_base / f'{package_name}-{version}'
            if bubble_path.exists():
                shutil.rmtree(bubble_path)
            
            return self._create_deduplicated_bubble(installed_tree, bubble_path, temp_path, python_context_version=python_context_version)

    def _create_bubble_from_editable_install(self, package_name: str, version: str, 
                                        source_path: str, python_context_version: str) -> bool:
        """
        Creates a bubble from an editable install by copying files from the live source.
        This preserves the current dev version WITH ALL DEPENDENCIES as a fallback.
        """
        safe_print(f"   - Creating bubble from editable source (copy, not move)...")
        
        try:
            source_root = Path(source_path)
            
            # Find the actual package directory in the source
            package_dir = source_root / package_name
            if not package_dir.exists():
                package_dir = source_root
            
            # Get current site-packages
            site_packages = None
            for key in ['site_packages', 'main_site_packages', 'site_packages_path']:
                if key in self.parent_omnipkg.config:
                    site_packages = Path(self.parent_omnipkg.config[key])
                    break
            
            if not site_packages:
                import site as site_module
                site_packages = Path(site_module.getsitepackages()[0])
            
            safe_print(f"   - Using site-packages: {site_packages}")
            
            # --- GET DEPENDENCIES FROM PYPROJECT.TOML ---
            dependencies = []
            pyproject_path = source_root / 'pyproject.toml'
            if pyproject_path.exists():
                try:
                    if sys.version_info >= (3, 11):
                        import tomllib
                    else:
                        import tomli as tomllib
                    
                    with open(pyproject_path, 'rb') as f:
                        pyproject_data = tomllib.load(f)
                    
                    # Get dependencies from project.dependencies
                    deps = pyproject_data.get('project', {}).get('dependencies', [])
                    
                    # Parse dependency specs (e.g., "requests>=2.20" -> "requests")
                    for dep_spec in deps:
                        # Remove version constraints, extras, and markers
                        dep_name = dep_spec.split('[')[0].split('>')[0].split('<')[0].split('=')[0].split(';')[0].strip()
                        if dep_name:
                            dependencies.append(dep_name)
                    
                    safe_print(f"   - Found {len(dependencies)} dependencies in pyproject.toml")
                except Exception as e:
                    safe_print(f"   - ⚠️  Could not parse pyproject.toml: {e}")
            
            # Create a temp directory with the package AND its dependencies
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # 1. Copy the main package source
                temp_pkg_dir = temp_path / package_name
                safe_print(f"   - Copying source files from: {package_dir}")
                shutil.copytree(package_dir, temp_pkg_dir, symlinks=False, 
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git*', 
                                                        '.pytest_cache', '*.egg-info'))
                
                # 2. Copy the package's dist-info
                dist_info_found = False
                for dist_info_pattern in [f'{package_name}-*.dist-info', 
                                        f'{package_name}-*.egg-info',
                                        f'__editable__*.dist-info']:
                    for dist_info in site_packages.glob(dist_info_pattern):
                        if package_name in dist_info.name or 'editable' in dist_info.name:
                            dest = temp_path / dist_info.name
                            if dist_info.is_dir():
                                shutil.copytree(dist_info, dest, symlinks=False)
                            else:
                                shutil.copy2(dist_info, dest)
                            safe_print(f"   - Copied metadata: {dist_info.name}")
                            dist_info_found = True
                
                if not dist_info_found:
                    safe_print(f"   - ⚠️  No dist-info found, creating minimal metadata...")
                    dist_info_dir = temp_path / f'{package_name}-{version}.dist-info'
                    dist_info_dir.mkdir(exist_ok=True)
                    metadata_file = dist_info_dir / 'METADATA'
                    metadata_file.write_text(f"Metadata-Version: 2.1\nName: {package_name}\nVersion: {version}\n")
                
                # 3. Copy ALL dependencies from site-packages
                safe_print(f"   - Copying {len(dependencies)} dependencies...")
                for dep_name in dependencies:
                    dep_canonical = canonicalize_name(dep_name)
                    
                    # Find and copy the dependency package directory
                    dep_dir = None
                    for potential_name in [dep_name, dep_name.replace('-', '_'), dep_canonical]:
                        potential_dir = site_packages / potential_name
                        if potential_dir.exists() and potential_dir.is_dir():
                            dep_dir = potential_dir
                            break
                    
                    if dep_dir:
                        dest_dir = temp_path / dep_dir.name
                        if not dest_dir.exists():
                            shutil.copytree(dep_dir, dest_dir, symlinks=False,
                                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                            safe_print(f"      ✓ {dep_name}")
                    
                    # Copy dependency's dist-info
                    for dist_info in site_packages.glob(f'{dep_canonical}*.dist-info'):
                        dest = temp_path / dist_info.name
                        if not dest.exists():
                            shutil.copytree(dist_info, dest, symlinks=False)
                
                safe_print(f"   - Analyzing complete dependency tree...")
                
                # Now analyze what we copied (package + all deps)
                installed_tree = self._analyze_installed_tree(temp_path)
                
                # Create the final bubble
                bubble_path = self.multiversion_base / f'{package_name}-{version}'
                if bubble_path.exists():
                    shutil.rmtree(bubble_path)
                
                return self._create_deduplicated_bubble(
                    installed_tree, bubble_path, temp_path, 
                    python_context_version=python_context_version
                )
        
        except Exception as e:
            safe_print(f"   - ❌ Failed to create bubble from editable install: {e}")
            import traceback
            traceback.print_exc()
            return False
        
    

    def _get_historical_dependencies(self, package_name: str, version: str) -> List[str]:
        safe_print(_('    -> Trying strategy 1: pip dry-run...'))
        deps = self._try_pip_dry_run(package_name, version)
        if deps is not None:
            safe_print(_('    ✅ Success: Dependencies resolved via pip dry-run.'))
            return deps
        safe_print(_('    -> Trying strategy 2: PyPI API...'))
        deps = self._try_pypi_api(package_name, version)
        if deps is not None:
            safe_print(_('    ✅ Success: Dependencies resolved via PyPI API.'))
            return deps
        safe_print(_('    -> Trying strategy 3: pip show fallback...'))
        deps = self._try_pip_show_fallback(package_name, version)
        if deps is not None:
            safe_print(_('    ✅ Success: Dependencies resolved from existing installation.'))
            return deps
        safe_print(_('    ⚠️ All dependency resolution strategies failed for {}=={}.').format(package_name, version))
        safe_print(_('    ℹ️  Proceeding with full temporary installation to build bubble.'))
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
            safe_print(_("    ⚠️  'requests' package not found. Skipping PyPI API strategy."))
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
                existing_paths = set(self.parent_omnipkg.cache_client.smembers(redis_key)) if self.parent_omnipkg.cache_client.exists(redis_key) else set()
                all_package_files_for_check = pkg_files + executables
                for file_path in all_package_files_for_check:
                    if str(file_path) not in existing_paths:
                        unregistered_file_count += 1
            except Exception as e:
                safe_print(_('    ⚠️  Could not analyze {}: {}').format(dist_info.name, e))
        if unregistered_file_count > 0:
            safe_print(_('    ⚠️  Found {} files not in registry. They will be registered during bubble creation.').format(unregistered_file_count))
        return installed

    def _is_binary(self, file_path: Path) -> bool:
        """
        Robustly checks if a file is a binary executable, excluding C extensions.
        Uses multiple detection strategies with intelligent fallbacks.
        """
        if file_path.suffix in {'.so', '.pyd', '.dylib'}:
            return False
        if HAS_MAGIC:
            try:
                mime = magic.Magic(mime=True)
                file_type = mime.from_file(str(file_path))
                executable_types = {'application/x-executable', 'application/x-sharedlib', 'application/x-pie-executable', 'application/x-mach-binary', 'application/x-ms-dos-executable'}
                return any((t in file_type for t in executable_types)) or file_path.suffix in {'.dll', '.exe'}
            except Exception:
                pass
        if not getattr(self, '_magic_warning_shown', False):
            safe_print(_("⚠️  Warning: 'python-magic' not installed. Using enhanced binary detection."))
            self._magic_warning_shown = True
        try:
            if file_path.stat().st_mode & 73:
                if file_path.is_file() and file_path.stat().st_size > 0:
                    result = self._detect_binary_by_header(file_path)
                    if result:
                        return True
        except (OSError, PermissionError):
            pass
        if file_path.suffix.lower() in {'.exe', '.dll', '.bat', '.cmd', '.ps1'}:
            return True
        return self._is_likely_executable_name(file_path)

    def _detect_binary_by_header(self, file_path: Path) -> bool:
        """
        Detect binary executables by reading file headers/magic numbers.
        """
        try:
            with open(file_path, 'rb') as f:
                header = f.read(16)
            if len(header) < 4:
                return False
            if header.startswith(b'\x7fELF'):
                return True
            if header.startswith(b'MZ'):
                return True
            magic_numbers = [b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe']
            for magic in magic_numbers:
                if header.startswith(magic):
                    return True
            return False
        except (OSError, IOError, PermissionError):
            return False

    def _is_likely_executable_name(self, file_path: Path) -> bool:
        """
        Additional heuristic: check if filename suggests it's an executable.
        Used as a final fallback for edge cases.
        """
        name = file_path.name.lower()
        common_executables = {'python', 'python3', 'pip', 'pip3', 'node', 'npm', 'yarn', 'git', 'docker', 'kubectl', 'terraform', 'ansible', 'uv', 'poetry', 'pipenv', 'black', 'flake8', 'mypy', 'gcc', 'clang', 'make', 'cmake', 'ninja', 'curl', 'wget', 'ssh', 'scp', 'rsync'}
        if name in common_executables:
            return True
        import re
        if re.match('^[a-z][a-z0-9]*[0-9]+(?:\\.[0-9]+)*$', name):
            base_name = re.sub('[0-9]+(?:\\.[0-9]+)*$', '', name)
            return base_name in common_executables
        return False

    def _create_deduplicated_bubble(self, installed_tree: Dict, bubble_path: Path, temp_install_path: Path, python_context_version: str) -> bool:
        """
        Enhanced Version: Fixes flask-login and similar packages with missing submodules.
        
        Key improvements:
        1. Better detection of package internal structure
        2. Conservative approach for packages with submodules
        3. Enhanced failsafe scanning
        4. Special handling for namespace packages
        """
        safe_print(_('    🧹 Creating deduplicated bubble at {}').format(bubble_path))
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
            safe_print(_('    🔬 Found C-extension packages: {}').format(', '.join(c_ext_packages)))
        if binary_packages:
            safe_print(_('    ⚙️  Found binary packages: {}').format(', '.join(binary_packages)))
        if complex_packages:
            safe_print(_('    📦 Found complex packages with submodules: {}').format(', '.join(complex_packages)))
        processed_files = set()
        for pkg_name, pkg_info in installed_tree.items():
            if pkg_name in c_ext_packages:
                should_deduplicate_this_package = False
                safe_print(_('    🔬 {}: C-extension - copying all files').format(pkg_name))
            elif pkg_name in binary_packages:
                should_deduplicate_this_package = False
                safe_print(_('    ⚙️  {}: Binary package - copying all files').format(pkg_name))
            elif pkg_name in complex_packages:
                should_deduplicate_this_package = False
                safe_print(_('    📦 {}: Complex package ({} submodules) - copying all files').format(pkg_name, stats['package_modules'][pkg_name]))
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
                safe_print(_('    📄 {}: copied {}, deduplicated {}').format(pkg_name, pkg_copied, pkg_deduplicated))
        all_temp_files = {p for p in temp_install_path.rglob('*') if p.is_file()}
        missed_files = all_temp_files - processed_files
        if missed_files:
            safe_print(_('    ⚠️  Found {} file(s) not listed in package metadata.').format(len(missed_files)))
            missed_by_package = {}
            for source_path in missed_files:
                owner_pkg = self._find_owner_package(source_path, temp_install_path, installed_tree)
                if owner_pkg not in missed_by_package:
                    missed_by_package[owner_pkg] = []
                missed_by_package[owner_pkg].append(source_path)
            for owner_pkg, files in missed_by_package.items():
                safe_print(_('    📦 {}: found {} additional files').format(owner_pkg, len(files)))
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
        safe_print(_('    ✅ Bubble created: {} files copied, {} deduplicated.').format(stats['copied_files'], stats['deduplicated_files']))
        safe_print(_('    📊 Space efficiency: {}% saved.').format(efficiency))
        if stats['package_modules']:
            safe_print(_('    📦 Complex packages preserved: {} packages with submodules').format(len(stats['package_modules'])))
        self._create_bubble_manifest(bubble_path, installed_tree, stats, python_context_version=python_context_version)
        return True

    def _verify_package_integrity(self, bubble_path: Path, installed_tree: Dict, temp_install_path: Path) -> None:
        """
        ENHANCED VERSION: Now uses the same robust import verification as the main installer.
        This catches issues like missing flask_login.config modules by actually testing imports.
        """
        safe_print(_('    🔍 Verifying package integrity with import tests...'))
        
        # First, do the basic file existence checks
        self._verify_basic_file_integrity(bubble_path, installed_tree, temp_install_path)
        
        # Now do the critical part: actual import verification for each package in the bubble
        import_failures = []
        
        for pkg_name, pkg_info in installed_tree.items():
            safe_print(_('    🧪 Testing imports for: {}').format(pkg_name))
            
            # Create a temporary distribution-like object for this package
            # We need to test imports against the bubble, not the temp install
            import_success = self._test_bubble_imports(pkg_name, bubble_path, pkg_info, temp_install_path)
            
            if not import_success['importable']:
                import_failures.append({
                    'package': pkg_name,
                    'error': import_success.get('error', 'Unknown import failure'),
                    'attempted_modules': import_success.get('attempted_modules', [])
                })
                safe_print(_('    ❌ Import test failed for {}: {}').format(
                    pkg_name, import_success.get('error', 'Unknown error')
                ))
            else:
                safe_print(_('    ✅ Import test passed for {} (modules: {})').format(
                    pkg_name, ', '.join(import_success.get('successful_modules', []))
                ))
        
        # If we have import failures, try to fix them
        if import_failures:
            safe_print(_('    🔧 Attempting to fix {} import failure(s)...').format(len(import_failures)))
            self._fix_bubble_import_failures(bubble_path, installed_tree, temp_install_path, import_failures)
        else:
            safe_print(_('    ✅ All package imports verified successfully'))

    def _verify_basic_file_integrity(self, bubble_path: Path, installed_tree: Dict, temp_install_path: Path) -> None:
        """
        Basic file existence checks (your original logic, kept for completeness)
        """
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
                    safe_print(_('    ⚠️ Missing package directory: {}').format(relative_pkg_path))
                    continue
                
                expected_py_files = [f for f in pkg_files if f.suffix == '.py' and f.parent == pkg_dir]
                for py_file in expected_py_files:
                    relative_py_path = py_file.relative_to(temp_install_path)
                    bubble_py_path = bubble_path / relative_py_path
                    
                    if not bubble_py_path.exists():
                        safe_print(_('    🚨 CRITICAL: Missing Python module: {}').format(relative_py_path))
                        self._copy_file_to_bubble(py_file, bubble_path, temp_install_path, False)
                        safe_print(_('    🔧 Fixed: Copied missing module {}').format(relative_py_path))

    def _test_bubble_imports(self, pkg_name: str, bubble_path: Path, pkg_info: Dict, temp_install_path: Path) -> Dict:
        """
        Test imports for a specific package in the bubble using the same logic as _verify_installation.
        This is adapted from your existing robust import verification code.
        """        
        # Get import candidates using the same logic as your main installer
        import_candidates = self._get_import_candidates_for_bubble(pkg_name, pkg_info, temp_install_path)
        
        if not import_candidates:
            # Fallback to standard name transformations
            import_candidates = [pkg_name.replace('-', '_')]
        
        # Build the test script (adapted from your _verify_installation method)
        script_lines = [
            'import sys',
            'import importlib', 
            'import traceback',
            'results = []'
        ]
        
        # Add the bubble path to Python path for testing
        script_lines.append(f"sys.path.insert(0, r'{bubble_path}')")
        
        # Test each import candidate
        for candidate in import_candidates:
            script_lines.extend([
                f'# Testing import: {candidate}',
                'try:',
                f"    mod = importlib.import_module('{candidate}')",
                f"    version = getattr(mod, '__version__', None)",
                f"    results.append(('{candidate}', True, version))",
                'except Exception as e:',
                f"    results.append(('{candidate}', False, str(e)))"
            ])
        
        script_lines.extend([
            'import json',
            'print(json.dumps(results))'
        ])
        
        script = '\n'.join(script_lines)
        
        # Execute the test script
        try:
            python_exe = self.config.get('python_executable', sys.executable)
            result = subprocess.run(
                [python_exe, '-c', script], 
                capture_output=True, 
                text=True, 
                check=True, 
                timeout=10
            )
            
            test_results = json.loads(result.stdout.strip())
            successful_imports = [(name, version) for name, success, version in test_results if success]
            failed_imports = [(name, error) for name, success, error in test_results if not success]
            
            if successful_imports:
                return {
                    'importable': True,
                    'successful_modules': [name for name, _version in successful_imports],
                    'failed_modules': [name for name, _error in failed_imports] if failed_imports else []
                }
            else:
                return {
                    'importable': False,
                    'error': f'All import attempts failed: {dict(failed_imports)}',
                    'attempted_modules': import_candidates
                }
                
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            error_msg = e.stderr.strip() if hasattr(e, 'stderr') and e.stderr else str(e)
            return {
                'importable': False,
                'error': f'Subprocess failed: {error_msg}',
                'attempted_modules': import_candidates
            }

    # Replace your _get_import_candidates_for_bubble with this improved version:

    def _get_import_candidates_for_bubble(self, pkg_name: str, pkg_info: Dict, temp_install_path: Path) -> List[str]:
        """
        (Authoritative) Finds import candidates by reading top_level.txt from the 
        package's .dist-info directory. Handles PyPI's inconsistent naming conventions.
        """
        from packaging.utils import canonicalize_name
        
        # Strategy 1: Find ANY .dist-info directory that might match this package
        version = pkg_info['version']
        canonical = canonicalize_name(pkg_name)
        
        # Generate all possible .dist-info directory name variations
        possible_names = {
            pkg_name,
            pkg_name.lower(),
            pkg_name.replace('-', '_'),
            pkg_name.replace('_', '-'),
            pkg_name.replace('-', '_').lower(),
            canonical,
            canonical.replace('-', '_')
        }
        
        dist_info_dir = None
        for name_variant in possible_names:
            # Use glob to be safe against case variations on some filesystems
            found = list(temp_install_path.glob(f"{name_variant}-{version}.dist-info"))
            if found:
                dist_info_dir = found[0]
                break
        
        # If still not found, do a case-insensitive wildcard search
        if not dist_info_dir:
            for candidate in temp_install_path.glob(f"*-{version}.dist-info"):
                candidate_name = candidate.name.rsplit('-', 1)[0]
                if canonicalize_name(candidate_name) == canonical:
                    dist_info_dir = candidate
                    break
        
        # Strategy 2: Read top_level.txt if we found the directory
        if dist_info_dir and dist_info_dir.is_dir():
            top_level_file = dist_info_dir / 'top_level.txt'
            if top_level_file.exists():
                try:
                    content = top_level_file.read_text(encoding='utf-8').strip()
                    if content:
                        # This is the ONLY authoritative source
                        return [line.strip() for line in content.split('\n') if line.strip()]
                except Exception:
                    pass
        
        # Strategy 3: Emergency fallback with special cases
        candidates = set()
        special_cases = {
            'markdown-it-py': 'markdown_it',
            'beautifulsoup4': 'bs4',
            'pillow': 'PIL',
            'pyyaml': 'yaml',
            'python-dateutil': 'dateutil',
            'msgpack-python': 'msgpack',
            'protobuf': 'google.protobuf',
        }
        
        if canonical in special_cases:
            candidates.add(special_cases[canonical])
        
        # Add standard transformations as a final guess
        candidates.add(pkg_name.replace('-', '_'))
        candidates.add(pkg_name.lower().replace('-', '_'))
        
        return sorted(list(candidates))
    
    def _fix_bubble_import_failures(self, bubble_path: Path, installed_tree: Dict, temp_install_path: Path, import_failures: List[Dict]) -> None:
        """
        Attempt to fix import failures by copying missing files or entire package structures.
        """
        safe_print(_('    🔧 Analyzing and fixing import failures...'))
        
        for failure in import_failures:
            pkg_name = failure['package']
            error_msg = failure['error']
            
            safe_print(_('    🔍 Fixing {}: {}').format(pkg_name, error_msg))
            
            # Strategy 1: If it's a "No module named" error, copy the entire package structure
            if 'No module named' in error_msg or 'ModuleNotFoundError' in error_msg:
                missing_module = self._extract_missing_module_name(error_msg)
                if missing_module:
                    self._copy_missing_module_structure(
                        missing_module, pkg_name, bubble_path, temp_install_path, installed_tree
                    )
            
            # Strategy 2: For complex packages, copy ALL Python files (conservative approach)
            elif 'ImportError' in error_msg:
                self._copy_all_python_files_for_package(
                    pkg_name, bubble_path, temp_install_path, installed_tree
                )
        
        # Re-test imports after fixes
        safe_print(_('    🧪 Re-testing imports after fixes...'))
        remaining_failures = []
        
        for failure in import_failures:
            pkg_name = failure['package']
            import_success = self._test_bubble_imports(pkg_name, bubble_path, installed_tree[pkg_name], temp_install_path)
            
            if not import_success['importable']:
                remaining_failures.append(failure)
                safe_print(_('    ❌ Still failing after fix: {}').format(pkg_name))
            else:
                safe_print(_('    ✅ Fixed successfully: {}').format(pkg_name))
        
        if remaining_failures:
            safe_print(_('    ⚠️ {} package(s) still have import issues after repair attempts').format(
                len(remaining_failures)
            ))

    def _extract_missing_module_name(self, error_msg: str) -> str:
        """Extract the specific missing module name from error messages."""
        import re
        
        # Try different patterns for extracting module names
        patterns = [
            r"No module named '([^']+)'",
            r"No module named ([^\s]+)",
            r"ModuleNotFoundError.*?'([^']+)'"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, error_msg)
            if match:
                return match.group(1)
        
        return None

    def _copy_missing_module_structure(self, missing_module: str, pkg_name: str, bubble_path: Path, temp_install_path: Path, installed_tree: Dict) -> None:
        """Copy missing module structure from temp install to bubble."""
        safe_print(_('    📁 Copying missing module structure: {}').format(missing_module))
        if not missing_module or missing_module.startswith('/') or '..' in missing_module:
            safe_print(f"   ⚠️ Invalid module name '{missing_module}' provided for healing. Skipping.")
            return
        # Look for the missing module in the temp install
        module_parts = missing_module.split('.')
        
        # Try to find the module file or directory in temp install
        for root_part in module_parts:
            potential_paths = [
                temp_install_path / f"{root_part}.py",
                temp_install_path / root_part,
                temp_install_path / pkg_name.replace('-', '_') / f"{root_part}.py",
                temp_install_path / pkg_name.replace('-', '_') / root_part
            ]
            
            for potential_path in potential_paths:
                if potential_path.exists():
                    relative_path = potential_path.relative_to(temp_install_path)
                    target_path = bubble_path / relative_path
                    
                    if potential_path.is_file():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        self._copy_file_to_bubble(potential_path, bubble_path, temp_install_path, False)
                        safe_print(_('    ✅ Copied missing file: {}').format(relative_path))
                    elif potential_path.is_dir():
                        self._copy_directory_to_bubble(potential_path, bubble_path, temp_install_path)
                        safe_print(_('    ✅ Copied missing directory: {}').format(relative_path))
                    
                    break

    def _copy_all_python_files_for_package(self, pkg_name: str, bubble_path: Path, temp_install_path: Path, installed_tree: Dict) -> None:
        """Conservative approach: copy ALL Python files for a failing package."""
        safe_print(_('    📦 Copying all Python files for package: {}').format(pkg_name))
        
        pkg_info = installed_tree.get(pkg_name, {})
        pkg_files = pkg_info.get('files', [])
        
        python_files = [f for f in pkg_files if f.suffix in {'.py', '.pyx', '.pxd'}]
        
        for py_file in python_files:
            if py_file.is_file():
                self._copy_file_to_bubble(py_file, bubble_path, temp_install_path, False)
        
        safe_print(_('    ✅ Copied {} Python files for {}').format(len(python_files), pkg_name))

    def _copy_directory_to_bubble(self, source_dir: Path, bubble_path: Path, temp_install_path: Path) -> None:
        """Copy an entire directory structure to the bubble."""
        relative_dir = source_dir.relative_to(temp_install_path)
        target_dir = bubble_path / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for item in source_dir.rglob('*'):
            if item.is_file():
                relative_item = item.relative_to(temp_install_path)
                target_item = bubble_path / relative_item
                target_item.parent.mkdir(parents=True, exist_ok=True)
                
                is_binary = self._is_binary(item) or item.suffix in {'.so', '.pyd'}
                self._copy_file_to_bubble(item, bubble_path, temp_install_path, is_binary)

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
            safe_print(_('    ⚠️ Warning: Failed to copy {}: {}').format(source_path.name, e))

    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        """
        Builds or loads a FAST hash index using multiple strategies:
        1. Isolated subprocess for authoritative file lists (preferred)
        2. Package metadata approach (fallback)
        3. Full filesystem scan (last resort)
        
        This method prevents cross-version contamination and provides the most
        accurate representation of the main environment state.
        """
        if not self.parent_omnipkg.cache_client:
            self.parent_omnipkg._connect_cache()
            if not self.parent_omnipkg.cache_client:
                return set()
        redis_key = f'{self.parent_omnipkg.redis_key_prefix}main_env:file_hashes'
        if self.parent_omnipkg.cache_client.exists(redis_key):
            safe_print(_('    ⚡️ Loading main environment hash index from cache...'))
            cached_hashes = set(self.parent_omnipkg.cache_client.sscan_iter(redis_key))
            safe_print(_('    📈 Loaded {} file hashes from Redis.').format(len(cached_hashes)))
            return cached_hashes
        safe_print(_('    🔍 Building main environment hash index...'))
        hash_set = set()
        try:
            safe_print(_('    📦 Attempting fast indexing via isolated subprocess...'))
            installed_packages = self.parent_omnipkg.get_installed_packages(live=True)
            package_names = list(installed_packages.keys())
            if not package_names:
                safe_print(_('    ✅ No packages found in the main environment to index.'))
                return hash_set
            safe_print(f"    -> Querying {self.parent_omnipkg.config.get('python_executable')} for file lists of {len(package_names)} packages...")
            package_files_map = self.parent_omnipkg._get_file_list_for_packages_live(package_names)
            files_to_hash = [Path(p) for file_list in package_files_map.values() for p in file_list]
            files_iterator = tqdm(files_to_hash, desc='    📦 Hashing files', unit='file') if HAS_TQDM else files_to_hash
            for abs_path in files_iterator:
                try:
                    if abs_path.is_file() and abs_path.suffix not in {'.pyc', '.pyo'} and ('__pycache__' not in abs_path.parts):
                        hash_set.add(self._get_file_hash(abs_path))
                except (IOError, OSError):
                    continue
            safe_print(_('    ✅ Successfully indexed {} files from {} packages via subprocess.').format(len(files_to_hash), len(package_names)))
        except Exception as e:
            safe_print(_('    ⚠️ Isolated subprocess indexing failed ({}), trying metadata approach...').format(e))
            try:
                safe_print(_('    📦 Attempting indexing via package metadata...'))
                successful_packages = 0
                failed_packages = []
                package_iterator = tqdm(installed_packages.keys(), desc='    📦 Indexing via metadata', unit='pkg') if HAS_TQDM else installed_packages.keys()
                for pkg_name in package_iterator:
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
                safe_print(_('    ✅ Successfully indexed {} packages via metadata').format(successful_packages))
                if failed_packages:
                    safe_print(_('    🔄 Fallback scan for {} packages: {}{}').format(len(failed_packages), ', '.join(failed_packages[:3]), '...' if len(failed_packages) > 3 else ''))
                    potential_files = []
                    for file_path in self.site_packages.rglob('*'):
                        if file_path.is_file() and file_path.suffix not in {'.pyc', '.pyo'} and ('__pycache__' not in file_path.parts):
                            file_str = str(file_path).lower()
                            if any((pkg.lower().replace('-', '_') in file_str or pkg.lower().replace('_', '-') in file_str for pkg in failed_packages)):
                                potential_files.append(file_path)
                    files_iterator = tqdm(potential_files, desc='    📦 Fallback scan', unit='file') if HAS_TQDM else potential_files
                    for file_path in files_iterator:
                        try:
                            hash_set.add(self._get_file_hash(file_path))
                        except (IOError, OSError):
                            continue
            except Exception as e2:
                safe_print(_('    ⚠️ Metadata approach also failed ({}), falling back to full filesystem scan...').format(e2))
                files_to_process = [p for p in self.site_packages.rglob('*') if p.is_file() and p.suffix not in {'.pyc', '.pyo'} and ('__pycache__' not in p.parts)]
                files_to_process_iterator = tqdm(files_to_process, desc='    📦 Full scan', unit='file') if HAS_TQDM else files_to_process
                for file_path in files_to_process_iterator:
                    try:
                        hash_set.add(self._get_file_hash(file_path))
                    except (IOError, OSError):
                        continue
        safe_print(_('    💾 Saving {} file hashes to Redis cache...').format(len(hash_set)))
        if hash_set:
            with self.parent_omnipkg.cache_client.pipeline() as pipe:
                chunk_size = 5000
                hash_list = list(hash_set)
                for i in range(0, len(hash_list), chunk_size):
                    chunk = hash_list[i:i + chunk_size]
                    pipe.sadd(redis_key, *chunk)
                pipe.execute()
        safe_print(_('    📈 Indexed {} files from main environment.').format(len(hash_set)))
        return hash_set

    def _register_bubble_location(self, bubble_path: Path, installed_tree: Dict, stats: dict):
        """
        Register bubble location and summary statistics in a single batch operation.
        """
        registry_key = '{}bubble_locations'.format(self.parent_omnipkg.redis_key_prefix)
        bubble_data = {'path': str(bubble_path), 'python_version': '{}.{}'.format(sys.version_info.major, sys.version_info.minor), 'created_at': datetime.now().isoformat(), 'packages': {pkg: info['version'] for pkg, info in installed_tree.items()}, 'stats': {'total_files': stats['total_files'], 'copied_files': stats['copied_files'], 'deduplicated_files': stats['deduplicated_files'], 'c_extensions_count': len(stats['c_extensions']), 'binaries_count': len(stats['binaries']), 'python_files': stats['python_files']}}
        bubble_id = bubble_path.name
        self.parent_omnipkg.cache_client.hset(registry_key, bubble_id, json.dumps(bubble_data))
        safe_print(_('    📝 Registered bubble location and stats for {} packages.').format(len(installed_tree)))

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

    def _create_bubble_manifest(self, bubble_path: Path, installed_tree: Dict, stats: dict, python_context_version: str):
        """
        Creates a robust, dynamic manifest file and registers the bubble in Redis.
        Now correctly stamps the manifest with the provided python_context_version and
        a dynamic omnipkg version.
        """
        # --- FIX: Get omnipkg version dynamically ---
        omnipkg_version = _get_dynamic_omnipkg_version()

        # Identify the primary package the bubble was created for from its directory name
        primary_package_name = bubble_path.name.rsplit('-', 1)[0]
        primary_package_cname = canonicalize_name(primary_package_name)

        # Enhance package metadata to distinguish the primary package from dependencies
        packages_metadata = {
            name: {
                'version': info['version'],
                'type': info['type'],
                'install_reason': 'primary' if canonicalize_name(name) == primary_package_cname else 'dependency'
            } for name, info in installed_tree.items()
        }

        # Calculate bubble size and other stats
        total_size = sum((f.stat().st_size for f in bubble_path.rglob('*') if f.is_file()))
        size_mb = round(total_size / (1024 * 1024), 2)
        
        # --- ENHANCED MANIFEST ---
        manifest_data = {
            'manifest_schema_version': '1.1',
            'created_at': datetime.now().isoformat(),
            'python_version': python_context_version, # Correctly uses the active context version
            'omnipkg_version': omnipkg_version,      # Uses the dynamic version
            'primary_package': primary_package_name,
            'packages': packages_metadata,
            'stats': {
                'bubble_size_mb': size_mb,
                'package_count': len(installed_tree),
                'total_files': stats.get('total_files', 0),
                'copied_files': stats.get('copied_files', 0),
                'deduplicated_files': stats.get('deduplicated_files', 0),
                'deduplication_efficiency_percent': round(
                    stats.get('deduplicated_files', 0) / stats.get('total_files', 1) * 100, 1
                ) if stats.get('total_files') else 0,
                'c_extensions_count': len(stats.get('c_extensions', [])),
                'binaries_count': len(stats.get('binaries', [])),
            }
        }

        # Write the local manifest file inside the bubble
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f, indent=2)

        # Register the bubble's existence in Redis. The metadata builder will handle the deep scan.
        try:
            registry_key = f'{self.parent_omnipkg.redis_key_prefix}bubble_locations'
            bubble_id = bubble_path.name
            
            # Store a summary in the bubble registry
            redis_summary = {
                'path': str(bubble_path),
                'primary_package': primary_package_name,
                'python_version': python_context_version,
                'omnipkg_version': omnipkg_version,
                'created_at': manifest_data['created_at'],
                'size_mb': size_mb,
                'package_count': len(installed_tree),
            }
            
            self.parent_omnipkg.cache_client.hset(registry_key, bubble_id, json.dumps(redis_summary))
            safe_print(_('    📝 Created manifest and registered bubble "{}" existence.').format(bubble_id))
        except Exception as e:
            safe_print(_('    ⚠️ Warning: Failed to register bubble existence in Redis: {}').format(e))
            import traceback
            traceback.print_exc()
            safe_print(_('    📝 Local manifest was still created at {}').format(manifest_path))

    def get_bubble_info(self, bubble_id: str) -> dict:
        """
        Retrieves comprehensive bubble information from Redis registry.
        """
        registry_key = _('{}bubble_locations').format(self.parent_omnipkg.redis_key_prefix)
        bubble_data = self.parent_omnipkg.cache_client.hget(registry_key, bubble_id)
        if bubble_data:
            return json.loads(bubble_data)
        return {}

    def find_bubbles_for_package(self, pkg_name: str, version: str=None) -> list:
        """
        Finds all bubbles containing a specific package.
        """
        if version:
            pkg_key = '{}=={}'.format(pkg_name, version)
            bubble_id = self.parent_omnipkg.cache_client.hget(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), pkg_key)
            return [bubble_id] if bubble_id else []
        else:
            pattern = f'{pkg_name}==*'
            matching_keys = []
            for key in self.parent_omnipkg.cache_client.hkeys(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix)):
                if key.startswith(f'{pkg_name}=='):
                    bubble_id = self.parent_omnipkg.cache_client.hget(_('{}pkg_to_bubble').format(self.parent_omnipkg.redis_key_prefix), key)
                    matching_keys.append(bubble_id)
            return matching_keys

    def cleanup_old_bubbles(self, keep_latest: int=3, size_threshold_mb: float=500):
        """
        Cleanup old bubbles based on size and age, keeping most recent ones.
        """
        registry_key = _('{}bubble_locations').format(self.parent_omnipkg.redis_key_prefix)
        all_bubbles = {}
        for bubble_id, bubble_data_str in self.parent_omnipkg.cache_client.hgetall(registry_key).items():
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
            safe_print(_('    🧹 Cleaning up {} old bubbles ({} MB)...').format(len(bubbles_to_remove), total_size_freed))
            with self.parent_omnipkg.cache_client.pipeline() as pipe:
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
            safe_print(_('    ✅ Freed {} MB of storage.').format(total_size_freed))
        else:
            safe_print(_('    ✅ No bubbles need cleanup.'))

class ImportHookManager:

    def __init__(self, multiversion_base: str, config: Dict, cache_client=None):
        self.multiversion_base = Path(multiversion_base)
        self.version_map = {}
        self.active_versions = {}
        self.hook_installed = False
        self.cache_client = cache_client
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
        safe_print(_('    🧠 HookManager now aware of new bubble: {}=={}').format(pkg_name, version))

    def remove_bubble_from_tracking(self, package_name: str, version: str):
        """
        Removes a bubble from the internal version map tracking.
        Used when cleaning up redundant bubbles.
        """
        pkg_name = package_name.lower().replace('_', '-')
        if pkg_name in self.version_map and version in self.version_map[pkg_name]:
            del self.version_map[pkg_name][version]
            safe_print(f'    ✅ Removed bubble tracking for {pkg_name}=={version}')
            if not self.version_map[pkg_name]:
                del self.version_map[pkg_name]
                safe_print(f'    ✅ Removed package {pkg_name} from version map (no more bubbles)')
        if pkg_name in self.active_versions and self.active_versions[pkg_name] == version:
            del self.active_versions[pkg_name]
            safe_print(f'    ✅ Removed active version tracking for {pkg_name}=={version}')

    def validate_bubble(self, package_name: str, version: str) -> bool:
        """
        (SMARTER VALIDATION) Validates a bubble's integrity. It now intelligently
        checks for a 'bin' directory ONLY if the bubble's manifest indicates it
        should contain executables.
        """
        bubble_path_str = self.get_package_path(package_name, version)
        if not bubble_path_str:
            safe_print(_("    ❌ Bubble not found in HookManager's map for {}=={}").format(package_name, version))
            return False
            
        bubble_path = Path(bubble_path_str)
        if not bubble_path.is_dir():
            safe_print(_('    ❌ Bubble directory does not exist at: {}').format(bubble_path))
            return False
            
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if not manifest_path.exists():
            safe_print(_('    ❌ Bubble is incomplete: Missing manifest file at {}').format(manifest_path))
            return False

        # --- THIS IS THE NEW, SMARTER LOGIC ---
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Check if any package in the bubble is expected to have executables.
            # We look for packages that aren't 'pure_python' or 'mixed'.
            # A more direct check could be to see if the manifest stores executable info.
            # For now, let's assume packages with native code might have executables.
            has_executables = any(
                info.get('type') not in ['pure_python', 'mixed'] 
                for info in manifest.get('packages', {}).values()
            )
            
            # The manifest might also have a direct count of binaries
            if 'binaries_count' in manifest.get('stats', {}):
                if manifest['stats']['binaries_count'] > 0:
                    has_executables = True

            bin_path = bubble_path / 'bin'
            if has_executables and not bin_path.is_dir():
                # Only warn if we expect a bin directory and it's not there.
                safe_print(_("    ⚠️  Warning: Bubble for {}=={} should contain executables, but 'bin' directory is missing.").format(package_name, version))

        except (json.JSONDecodeError, KeyError):
            # If manifest is broken, fall back to the old check for safety.
            bin_path = bubble_path / 'bin'
            if not bin_path.is_dir():
                safe_print(_("    ⚠️  Warning: Bubble for {}=={} does not contain a 'bin' directory (manifest unreadable).").format(package_name, version))
        
        # --- END OF NEW LOGIC ---

        safe_print(_('    ✅ Bubble validated successfully: {}=={}').format(package_name, version))
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

class NoCompatiblePythonError(Exception):
    """
    Custom exception raised when a package EXISTS on PyPI but is incompatible 
    with the current Python version. This exception carries the necessary context 
    to perform 'Quantum Healing'.
    
    NOTE: This should NOT be raised for packages that don't exist or have invalid 
    version specifications - those should return None instead.
    """
    def __init__(
        self, 
        package_name: str, 
        package_version: Optional[str] = None, 
        current_python: Optional[str] = None, 
        compatible_python: Optional[str] = None, 
        message: Optional[str] = None
    ):
        self.package_name = package_name
        self.package_version = package_version
        self.current_python = current_python
        self.compatible_python = compatible_python
        
        # Build a helpful message
        if message:
            self.message = message
        else:
            msg_parts = [f"Package '{package_name}'"]
            
            if package_version:
                msg_parts.append(f"v{package_version}")
            
            if current_python:
                msg_parts.append(f"is not compatible with Python {current_python}")
            else:
                msg_parts.append("is not compatible with your current Python version")
            
            if compatible_python and compatible_python != "unknown":
                msg_parts.append(f"It requires Python {compatible_python}")
            elif compatible_python == "unknown":
                msg_parts.append("Compatible Python version could not be determined")
            
            self.message = ". ".join(msg_parts) + "."
        
        super().__init__(self.message)
    
    def should_attempt_quantum_healing(self) -> bool:
        """
        Determines if quantum healing should be attempted based on available information.
        Returns False if we don't have enough info to attempt healing.
        """
        # Need at least a package name and some indication of what Python version works
        return (
            self.package_name is not None 
            and self.compatible_python is not None 
            and self.compatible_python != "unknown"
        )
    
    def get_quantum_healing_context(self) -> dict:
        """
        Returns a dictionary with all the context needed for quantum healing.
        """
        return {
            'package_name': self.package_name,
            'package_version': self.package_version,
            'current_python': self.current_python,
            'compatible_python': self.compatible_python,
            'can_heal': self.should_attempt_quantum_healing()
        }

class omnipkg:

    def __init__(self, config_manager: ConfigManager, minimal_mode: bool = False):
        """
        Initializes Omnipkg with optional minimal mode for lightweight commands.
        
        Args:
            minimal_mode: If True, skips database/cache initialization for commands
                         that only need config access (swap, version, etc.)
        """
        self.config_manager = config_manager
        self.config = config_manager.config
        
        if not self.config:
            if len(sys.argv) > 1 and sys.argv[1] in ['reset-config', 'doctor']:
                pass
            else:
                raise RuntimeError('OmnipkgCore cannot initialize: Configuration is missing or invalid.')
        
        # Minimal initialization for lightweight commands
        if minimal_mode:
            self.env_id = self._get_env_id()
            self.multiversion_base = Path(self.config['multiversion_base'])
            self.interpreter_manager = InterpreterManager(self.config_manager)
            safe_print(_('✅ Omnipkg core initialized (minimal mode).'))
            return  # EXIT EARLY - no cache, no migrations, no hooks
        self._self_heal_omnipkg_installation()
        self.env_id = self._get_env_id()
        self.multiversion_base = Path(self.config['multiversion_base'])
        self.cache_client = None
        self._cache_connection_status = None
        self.initialize_pypi_cache()
        self._info_cache = {}
        self._prime_loader_cache()
        self._installed_packages_cache = None
        self.http_session = http_requests.Session()
        self.multiversion_base.mkdir(parents=True, exist_ok=True)
        if not self._connect_cache():
            sys.exit(1)
        # --- ADD THE MIGRATION LOGIC HERE ---
        # V2 MIGRATION: Automatically upgrade legacy global keys to be environment-aware.
        migration_flag_key = f'omnipkg:env_{self.env_id}:migration_v2_env_aware_keys_complete'
        
        # We check the flag *before* the expensive scan.
        if not self.cache_client.get(migration_flag_key):
            # Check if there are any old keys that *need* migrating.
            # We use scan_iter for performance, only needing to find one key.
            old_keys_iterator = self.cache_client.scan_iter('omnipkg:pkg:*', count=1)
            if next(old_keys_iterator, None):
                 self._perform_redis_key_migration(migration_flag_key)
            else:
                 # No old keys found, so we can just set the flag.
                 self.cache_client.set(migration_flag_key, 'true')
        # --- END MIGRATION LOGIC ---
        self.interpreter_manager = InterpreterManager(self.config_manager)
        self.hook_manager = ImportHookManager(str(self.config.get('multiversion_base')), config=self.config, cache_client=None)
        self.bubble_manager = BubbleIsolationManager(self.config, self)

        migration_flag_key = f'omnipkg:env_{self.env_id}:migration_v2_env_aware_keys_complete'
        if not self.cache_client.get(migration_flag_key):
            old_keys_iterator = self.cache_client.scan_iter('omnipkg:pkg:*', count=1)
            if next(old_keys_iterator, None):
                self._perform_redis_key_migration(migration_flag_key)
            else:
                self.cache_client.set(migration_flag_key, 'true')
        migration_v3_flag_key = f'{self.redis_env_prefix}migration_v3_install_type_complete'
        if not self.cache_client.get(migration_v3_flag_key):
            self._perform_v3_metadata_migration(migration_v3_flag_key)

        self.hook_manager.load_version_map()
        self.hook_manager.install_import_hook()
        safe_print(_('✅ Omnipkg core initialized successfully.'))

    def _get_omnipkg_version_from_site_packages(self, site_packages_path: str) -> str:
        """
        Gets omnipkg version directly from dist-info in a specific site-packages.
        Handles both regular and editable installs.
        """
        try:
            site_pkg = Path(site_packages_path)
            
            # Check for regular install
            dist_info_dirs = list(site_pkg.glob('omnipkg-*.dist-info'))
            
            # Check for editable install (PEP 660 style)
            if not dist_info_dirs:
                dist_info_dirs = list(site_pkg.glob('__editable___omnipkg*.dist-info'))
            
            # Check for old-style editable install via .pth files
            if not dist_info_dirs:
                pth_files = list(site_pkg.glob('*.pth'))
                for pth_file in pth_files:
                    content = pth_file.read_text()
                    if 'omnipkg' in content and '/omnipkg' in content:
                        # Found an editable install - read version from the source pyproject.toml
                        for line in content.split('\n'):
                            if 'omnipkg' in line and line.strip().startswith('/'):
                                project_path = Path(line.strip())
                                if project_path.exists():
                                    pyproject_path = project_path / 'pyproject.toml'
                                    if pyproject_path.exists():
                                        try:
                                            if sys.version_info >= (3, 11):
                                                import tomllib
                                            else:
                                                import tomli as tomllib
                                            with open(pyproject_path, 'rb') as f:
                                                data = tomllib.load(f)
                                            return data.get('project', {}).get('version', 'unknown')
                                        except Exception:
                                            pass
            
            if not dist_info_dirs:
                return 'not-installed'
            
            # Find the highest version if multiple exist
            dist_info_dirs.sort(key=lambda p: parse_version(p.name.split('-')[1].replace('__editable___omnipkg', '').split('.dist')[0]), reverse=True)
            metadata_file = dist_info_dirs[0] / 'METADATA'
            
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    for line in f:
                        if line.lower().startswith('version:'):
                            return line.split(':', 1)[1].strip()
            
            # Fallback if METADATA is weird
            version_str = dist_info_dirs[0].name.split('-')[1].replace('__editable___omnipkg', '').split('.dist')[0]
            return version_str
        except Exception:
            return 'unknown'

    def _prime_loader_cache(self):
        """
        (NEW) Proactively builds the omnipkgLoader dependency cache if it doesn't
        exist. This ensures the first auto-healing run is as fast as possible.
        """
        try:
            # Determine the correct cache file path using the loader's own logic
            # to ensure consistency.
            python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
            multiversion_base = Path(self.config['multiversion_base'])
            cache_file = multiversion_base / '.cache' / f'loader_deps_{python_version}.json'

            # If the cache already exists, our work is done. Exit immediately.
            if cache_file.exists():
                return

            # If the cache is missing, we build it now so the loader doesn't have to.
            from omnipkg.loader import omnipkgLoader
            
            # Create a temporary, "quiet" loader instance just to run its
            # dependency detection logic.
            # We pass `quiet=True` to prevent any output during this background task.
            temp_loader = omnipkgLoader(config=self.config, quiet=True)
            
            # The _get_omnipkg_dependencies method now contains the logic to
            # compute and save the cache file. Calling it is enough.
            temp_loader._get_omnipkg_dependencies()

        except Exception:
            # This is a non-critical optimization. If it fails for any reason
            # (e.g., permissions), we silently ignore it. The loader will
            # simply build the cache on its first run as before.
            pass

    def _self_heal_omnipkg_installation(self):
        """
        (V25 - FILE-BASED CACHE) Ultra-fast version checking with persistent cache.
        Uses a small JSON file to cache sync status across CLI invocations.
        """
        import time
        
        overall_start = time.perf_counter_ns()
        
        try:
            # === TIER 0: FILE-BASED CACHE CHECK (MICROSECONDS) ===
            cache_data = self._read_heal_cache()
            cached_master_version = cache_data.get('master_version')
            cached_timestamp = cache_data.get('timestamp', 0)
            
            # === TIER 1: FAST MASTER VERSION DETECTION ===
            master_version_str, install_spec = self._get_master_version_ultra_fast()
            if master_version_str in ['unknown', 'not-installed', None]:
                return
            
            # Check if cache is valid (version matches and less than 60 seconds old)
            cache_valid = (
                cached_master_version == master_version_str and
                (time.time() - cached_timestamp) < 60
            )
            
            if cache_valid:
                if '--verbose' in sys.argv or '-V' in sys.argv:
                    elapsed_ns = time.perf_counter_ns() - overall_start
                    elapsed_ms = elapsed_ns / 1_000_000
                    safe_print(f"   ⚡ All interpreters in sync (cached: {elapsed_ms:.3f}ms)")
                return
            
            # === TIER 2: PATH-BASED SYNC CHECK (1-2ms) ===
            sync_needed = self._check_sync_status_ultra_fast(master_version_str)
            
            if not sync_needed:
                # Write cache for next time
                self._write_heal_cache({
                    'master_version': master_version_str,
                    'timestamp': time.time()
                })
                
                if '--verbose' in sys.argv or '-V' in sys.argv:
                    elapsed_ns = time.perf_counter_ns() - overall_start
                    elapsed_ms = elapsed_ns / 1_000_000
                    safe_print(f"   ⚡ All interpreters in sync (checked in {elapsed_ms:.3f}ms, {elapsed_ns:,} ns)")
                return
            
            # === TIER 3: HEALING REQUIRED (ONLY WHEN NECESSARY) ===
            self._perform_concurrent_healing(master_version_str, install_spec, sync_needed)
            
            # Write cache after successful healing
            self._write_heal_cache({
                'master_version': master_version_str,
                'timestamp': time.time()
            })
            
        except Exception as e:
            if '--verbose' in sys.argv or '-V' in sys.argv:
                import traceback
                safe_print(f"\n⚠️ Self-heal encountered an error: {e}")
                traceback.print_exc()


    def _get_heal_cache_path(self) -> Path:
        """Returns the path to the self-heal cache file."""
        return self.config_manager.config_dir / f'heal_cache_{self.config_manager.env_id}.json'


    def _read_heal_cache(self) -> dict:
        """Reads the self-heal cache from a JSON file."""
        cache_path = self._get_heal_cache_path()
        if cache_path.exists():
            try:
                import json
                with open(cache_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}


    def _write_heal_cache(self, data: dict):
        """Writes data to the self-heal cache file."""
        cache_path = self._get_heal_cache_path()
        try:
            import json
            with open(cache_path, 'w') as f:
                json.dump(data, f)
        except IOError:
            pass  # Fail silently if we can't write the cache


    def _get_master_version_ultra_fast(self) -> Tuple[str, List[str]]:
        """
        Ultra-fast master version detection using direct file I/O.
        Returns: (version_string, install_spec)
        """
        # Fast path: Check if we're in a project (editable install)
        project_root = self.config_manager._find_project_root()
        if project_root:
            toml_path = Path(project_root) / 'pyproject.toml'
            if toml_path.exists():
                try:
                    # Read only the first 2KB - version is always near the top
                    content = toml_path.read_text(encoding='utf-8')[:2048]
                    
                    # Ultra-fast string search (no regex, no TOML parsing)
                    for line in content.split('\n'):
                        stripped = line.strip()
                        if stripped.startswith('version'):
                            # Extract version: version = "1.5.16" or version="1.5.16"
                            version_str = stripped.split('=', 1)[1].strip().strip('"\'')
                            return version_str, ['-e', str(project_root)]
                except Exception:
                    pass
        
        # Fallback: Get version from native interpreter's site-packages
        # This is fast because we're already running IN the native interpreter
        try:
            native_site_packages = site.getsitepackages()[0]
            version_str = self._get_omnipkg_version_from_site_packages(native_site_packages)
            if version_str not in ['unknown', 'not-installed']:
                return version_str, [f'omnipkg=={version_str}']
        except Exception:
            pass
        
        return 'unknown', []


    def _check_sync_status_ultra_fast(self, master_version: str) -> List[Tuple[str, str]]:
        """
        CORRECTED path-based sync checking - derives site-packages from interpreter location.
        This handles standalone Python builds correctly!
        Excludes the native interpreter from the sync list (it's handled separately).
        """
        sync_needed = []
        
        # Get all managed interpreters
        interpreter_manager = InterpreterManager(self.config_manager)
        all_interpreters = interpreter_manager.list_available_interpreters()
        
        # Determine native interpreter to exclude it
        if platform.system() == 'Windows':
            native_exe = self.config_manager.venv_path / 'Scripts' / 'python.exe'
        else:
            native_exe = self.config_manager.venv_path / 'bin' / 'python'
        native_exe = native_exe.resolve()
        
        # Pre-compute expected dist-info names
        expected_dist_info = f"omnipkg-{master_version}.dist-info"
        expected_editable_dist_info = f"__editable___omnipkg-{master_version}.dist-info"
        
        for py_ver, exe_path in all_interpreters.items():
            try:
                # === SKIP THE NATIVE INTERPRETER ===
                exe_path_obj = Path(exe_path).resolve()
                if exe_path_obj == native_exe:
                    continue  # Native is handled separately
                
                # === DERIVE site-packages FROM INTERPRETER PATH ===
                # Pattern: /path/to/interpreter/bin/python -> /path/to/interpreter/lib/pythonX.Y/site-packages
                interpreter_root = exe_path_obj.parent.parent
                
                if platform.system() == 'Windows':
                    # Windows: interpreter_root\Lib\site-packages
                    site_packages = interpreter_root / 'Lib' / 'site-packages'
                else:
                    # Unix: interpreter_root/lib/pythonX.Y/site-packages
                    site_packages = interpreter_root / 'lib' / f'python{py_ver}' / 'site-packages'
                
                # Verify the site-packages directory actually exists
                if not site_packages.exists():
                    sync_needed.append((py_ver, str(exe_path)))
                    continue
                
                # Fast check: does the expected dist-info exist?
                has_regular_install = (site_packages / expected_dist_info).exists()
                has_editable_install = (site_packages / expected_editable_dist_info).exists()
                
                # Additional check for old-style editable (.pth files)
                has_pth_install = False
                if not has_regular_install and not has_editable_install:
                    # Quick check for .pth files containing omnipkg
                    pth_files = list(site_packages.glob('*.pth'))
                    for pth_file in pth_files:
                        try:
                            # Only read first 512 bytes - .pth files are tiny
                            content = pth_file.read_text(encoding='utf-8')[:512]
                            if 'omnipkg' in content:
                                # For editable installs via .pth, we need to verify the version
                                # by checking the actual source pyproject.toml
                                for line in content.split('\n'):
                                    line = line.strip()
                                    if line and line.startswith('/') and 'omnipkg' in line:
                                        project_path = Path(line)
                                        if project_path.exists():
                                            pyproject_path = project_path / 'pyproject.toml'
                                            if pyproject_path.exists():
                                                # Read version from source
                                                try:
                                                    toml_content = pyproject_path.read_text(encoding='utf-8')[:2048]
                                                    for toml_line in toml_content.split('\n'):
                                                        stripped = toml_line.strip()
                                                        if stripped.startswith('version'):
                                                            source_version = stripped.split('=', 1)[1].strip().strip('"\'')
                                                            if source_version == master_version:
                                                                has_pth_install = True
                                                            break
                                                except:
                                                    pass
                                        break
                        except:
                            pass
                
                # If none of the expected patterns exist, sync is needed
                if not (has_regular_install or has_editable_install or has_pth_install):
                    sync_needed.append((py_ver, str(exe_path)))
                    
            except Exception as e:
                # If anything fails, assume sync is needed (conservative)
                if '--verbose' in sys.argv or '-V' in sys.argv:
                    safe_print(f"   ⚠️ Path derivation failed for Python {py_ver}: {e}")
                sync_needed.append((py_ver, str(exe_path)))
        
        return sync_needed

    
    def _check_sync_status_ultra_fast(self, master_version: str) -> List[Tuple[str, str]]:
        """
        CORRECTED path-based sync checking - derives site-packages from interpreter location.
        This handles standalone Python builds correctly!
        
        CRITICAL: The native interpreter is ALWAYS at venv_path/bin/python (or Scripts/python.exe).
        It is NEVER in .omnipkg/interpreters/. We must exclude it from the sync list.
        """
        sync_needed = []
        
        # Get all managed interpreters
        interpreter_manager = InterpreterManager(self.config_manager)
        all_interpreters = interpreter_manager.list_available_interpreters()
        
        # Determine THE NATIVE interpreter - ALWAYS at venv_path/bin or Scripts
        # This is the ORIGINAL Python that came with the venv, NOT whatever is currently active
        # CRITICAL: We need to find the actual native Python executable (e.g., python3.11)
        if platform.system() == 'Windows':
            native_exe = self.config_manager.venv_path / 'Scripts' / 'python.exe'
        else:
            # The native Python is in bin/ and NOT a symlink to .omnipkg
            bin_dir = self.config_manager.venv_path / 'bin'
            native_exe = None
            
            if bin_dir.exists():
                # Collect all valid python3.XX files (where XX is a valid version)
                candidates = []
                for py_file in bin_dir.glob('python3.*'):
                    # Must be a file
                    if not py_file.is_file():
                        continue
                    
                    # Parse version from filename (e.g., python3.11 -> 3.11)
                    name = py_file.name
                    try:
                        # Extract the version part after "python"
                        version_part = name.replace('python', '')
                        # Should be like "3.11" or "3.9"
                        major, minor = version_part.split('.')
                        # Validate it's actually a number
                        if int(major) == 3 and int(minor) >= 0:
                            # Check if it's a symlink to .omnipkg (managed interpreter)
                            if py_file.is_symlink():
                                target = py_file.resolve()
                                if '.omnipkg' in str(target):
                                    continue  # Skip managed interpreters
                            
                            candidates.append((int(minor), py_file))
                    except (ValueError, IndexError):
                        # Not a valid version format, skip
                        continue
                
                # Use the highest version number (most recent Python)
                # This handles cases where multiple Pythons exist
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    native_exe = candidates[0][1]
            
            # Fallback if we couldn't find it
            if native_exe is None:
                native_exe = bin_dir / 'python'
        
        if '--verbose' in sys.argv or '-V' in sys.argv:
            safe_print(f"   🔍 [Sync Check] Native interpreter: {native_exe}")
        
        # Pre-compute expected dist-info names
        expected_dist_info = f"omnipkg-{master_version}.dist-info"
        expected_editable_dist_info = f"__editable___omnipkg-{master_version}.dist-info"
        
        for py_ver, exe_path in all_interpreters.items():
            try:
                exe_path_obj = Path(exe_path).resolve()
                
                # === CRITICAL: SKIP THE NATIVE INTERPRETER ===
                if exe_path_obj == native_exe:
                    if '--verbose' in sys.argv or '-V' in sys.argv:
                        safe_print(f"   🔍 [Sync Check] Skipping native interpreter: Python {py_ver} at {exe_path}")
                    continue  # Native is handled separately in _perform_concurrent_healing
                
                # Also skip if this is a symlink pointing to native (belt-and-suspenders)
                if not '.omnipkg' in str(exe_path_obj):
                    if '--verbose' in sys.argv or '-V' in sys.argv:
                        safe_print(f"   🔍 [Sync Check] Skipping (not in .omnipkg): Python {py_ver} at {exe_path}")
                    continue
                
                # === DERIVE site-packages FROM INTERPRETER PATH ===
                interpreter_root = exe_path_obj.parent.parent
                
                if platform.system() == 'Windows':
                    site_packages = interpreter_root / 'Lib' / 'site-packages'
                else:
                    site_packages = interpreter_root / 'lib' / f'python{py_ver}' / 'site-packages'
                
                # Verify the site-packages directory actually exists
                if not site_packages.exists():
                    if '--verbose' in sys.argv or '-V' in sys.argv:
                        safe_print(f"   🔍 [Sync Check] site-packages not found for Python {py_ver}, adding to sync list")
                    sync_needed.append((py_ver, str(exe_path)))
                    continue
                
                # Fast check: does the expected dist-info exist?
                has_regular_install = (site_packages / expected_dist_info).exists()
                has_editable_install = (site_packages / expected_editable_dist_info).exists()
                
                # Additional check for old-style editable (.pth files)
                has_pth_install = False
                if not has_regular_install and not has_editable_install:
                    pth_files = list(site_packages.glob('*.pth'))
                    for pth_file in pth_files:
                        try:
                            content = pth_file.read_text(encoding='utf-8')[:512]
                            if 'omnipkg' in content:
                                for line in content.split('\n'):
                                    line = line.strip()
                                    if line and line.startswith('/') and 'omnipkg' in line:
                                        project_path = Path(line)
                                        if project_path.exists():
                                            pyproject_path = project_path / 'pyproject.toml'
                                            if pyproject_path.exists():
                                                try:
                                                    toml_content = pyproject_path.read_text(encoding='utf-8')[:2048]
                                                    for toml_line in toml_content.split('\n'):
                                                        stripped = toml_line.strip()
                                                        if stripped.startswith('version'):
                                                            source_version = stripped.split('=', 1)[1].strip().strip('"\'')
                                                            if source_version == master_version:
                                                                has_pth_install = True
                                                            break
                                                except:
                                                    pass
                                        break
                        except:
                            pass
                
                # If none of the expected patterns exist, sync is needed
                if not (has_regular_install or has_editable_install or has_pth_install):
                    if '--verbose' in sys.argv or '-V' in sys.argv:
                        safe_print(f"   🔍 [Sync Check] Python {py_ver} needs sync (no valid install found)")
                    sync_needed.append((py_ver, str(exe_path)))
                    
            except Exception as e:
                # If anything fails, assume sync is needed (conservative)
                if '--verbose' in sys.argv or '-V' in sys.argv:
                    safe_print(f"   ⚠️ Path derivation failed for Python {py_ver}: {e}")
                sync_needed.append((py_ver, str(exe_path)))
        
        return sync_needed


    def _perform_concurrent_healing(self, master_version: str, install_spec: List[str], 
                                    sync_needed: List[Tuple[str, str]]):
        """
        Performs concurrent healing only when necessary.
        This is the slow path - only hit when versions are actually out of sync.
        
        CRITICAL SAFETY RULE: The native interpreter is ALWAYS at venv_path/bin/python.
        It is NEVER in .omnipkg/interpreters/. Only the native interpreter can update itself.
        """
        import textwrap
        import json
        import concurrent.futures
        
        # === DETERMINE THE NATIVE INTERPRETER PATH ===
        # Native is ALWAYS at venv_path/bin (or Scripts on Windows)
        # It is NEVER in .omnipkg/interpreters/ - those are managed interpreters
        # CRITICAL: Find the actual native Python, not the symlink that might point to managed
        if platform.system() == 'Windows':
            native_exe = self.config_manager.venv_path / 'Scripts' / 'python.exe'
        else:
            # Find the actual versioned Python in bin/ (e.g., python3.11)
            bin_dir = self.config_manager.venv_path / 'bin'
            native_candidates = []
            if bin_dir.exists():
                for py_file in bin_dir.glob('python3.*'):
                    if py_file.is_file() and not py_file.is_symlink():
                        native_candidates.append(py_file)
                    elif py_file.is_symlink():
                        # Check if symlink points outside .omnipkg (to system Python)
                        target = py_file.resolve()
                        if '.omnipkg' not in str(target):
                            native_candidates.append(py_file)
            
            # Use the first valid candidate, or fall back to 'python'
            if native_candidates:
                native_exe = native_candidates[0]
            else:
                native_exe = bin_dir / 'python'
        
        # === CHECK IF WE ARE THE NATIVE INTERPRETER ===
        current_exe = Path(sys.executable).resolve()
        
        # Direct path comparison - this is the ONLY reliable check
        is_current_native = (current_exe == native_exe)
        
        # Get current version
        current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        
        if '--verbose' in sys.argv or '-V' in sys.argv:
            safe_print(f"   🔍 Current exe: {current_exe}")
            safe_print(f"   🔍 Native exe:  {native_exe}")
            safe_print(f"   🔍 Is native:   {is_current_native}")
            safe_print(f"   🔍 Current version: {current_version}")
        
        # === CRITICAL SAFETY CHECK ===
        # ONLY the native interpreter can update itself
        native_needs_sync = False
        native_was_synced = False
        
        if is_current_native:
            # We ARE the native interpreter - check if we need sync
            try:
                native_site_packages = site.getsitepackages()[0]
                current_version_str = self._get_omnipkg_version_from_site_packages(native_site_packages)
                if current_version_str != master_version:
                    native_needs_sync = True
                    if '--verbose' in sys.argv or '-V' in sys.argv:
                        safe_print(f"   🔍 Native needs sync: {current_version_str} -> {master_version}")
            except Exception as e:
                native_needs_sync = True
                if '--verbose' in sys.argv or '-V' in sys.argv:
                    safe_print(f"   🔍 Native sync check failed: {e}")
        else:
            # We're on a non-native interpreter - NEVER touch native
            if '--verbose' in sys.argv or '-V' in sys.argv:
                safe_print(f"   🔒 Running from non-native interpreter (Python {current_version}) - skipping native sync")
        
        # === SYNC NATIVE FIRST (ONLY IF WE ARE THE NATIVE INTERPRETER) ===
        if native_needs_sync and is_current_native:
            import tempfile
            native_sync_start = time.perf_counter()
            safe_print(f"🔄 Syncing native Python {current_version} to v{master_version}...")
            
            sync_script = textwrap.dedent(f"""
    import subprocess
    import sys
    try:
        from .common_utils import safe_print
    except ImportError:
        from omnipkg.common_utils import safe_print                                  
                                          

    install_spec = {json.dumps(install_spec)}
    cmd = [
        sys.executable, '-m', 'pip', 'install',
        '--force-reinstall', '--no-deps', '--no-cache-dir', '-q'
    ] + install_spec

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        safe_print("   ✅ Synced: {current_version}")
    else:
        safe_print(f"   ❌ Failed to sync: {{result.stderr}}")
    sys.exit(result.returncode)
            """)
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as f:
                f.write(sync_script)
                sync_script_path = f.name
            
            try:
                result = subprocess.run(
                    [str(native_exe), sync_script_path],
                    capture_output=True, text=True, timeout=60
                )
                print(result.stdout, end='')
                
                if result.returncode == 0:
                    native_was_synced = True
                    native_sync_duration = time.perf_counter() - native_sync_start
                    safe_print(f"   ⏱️  Native sync completed in {native_sync_duration:.2f}s")
            finally:
                Path(sync_script_path).unlink(missing_ok=True)
        
        # === SYNC OTHER INTERPRETERS CONCURRENTLY ===
        if sync_needed:
            concurrent_sync_start = time.perf_counter()
            versions_to_sync = ', '.join([f"Python {ver}" for ver, _ in sync_needed])
            safe_print(f"🔄 Syncing {versions_to_sync} to v{master_version}...")
            
            def sync_interpreter(py_ver, target_exe):
                heal_cmd = [
                    target_exe, '-m', 'pip', 'install',
                    '--force-reinstall', '--no-deps', '--no-cache-dir', '-q'
                ] + install_spec
                result = subprocess.run(heal_cmd, capture_output=True, text=True, timeout=60)
                return (py_ver, result.returncode == 0)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(sync_interpreter, ver, exe) for ver, exe in sync_needed]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            
            concurrent_sync_duration = time.perf_counter() - concurrent_sync_start
            
            success = [ver for ver, ok in results if ok]
            failed = [ver for ver, ok in results if not ok]
            
            if success:
                safe_print(f"   ✅ Synced: {', '.join(success)}")
            if failed:
                safe_print(f"   ❌ Failed: {', '.join(failed)}")
            
            safe_print(f"   ⏱️  Concurrent sync completed in {concurrent_sync_duration:.2f}s")
        
        # === AUTO-RELAUNCH IF NATIVE WAS SYNCED ===
        if native_was_synced and is_current_native:
            safe_print(f"\n🔄 Restarting command with updated omnipkg v{master_version}...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
    
    def _perform_v3_metadata_migration(self, flag_key: str):
        """
        (CORRECTED) Scans all existing package entries and adds the 'install_type' and
        'owner_package' fields based on their path.
        """
        safe_print('🔧 Performing one-time KB upgrade to add installation context (v3.1)...')
        index_key = f'{self.redis_env_prefix}index'
        all_packages = self.cache_client.smembers(index_key)
        if not all_packages:
            safe_print('   ✅ No packages to migrate.')
            self.cache_client.set(flag_key, 'true')
            return

        migrated_count = 0
        multiversion_base = Path(self.config['multiversion_base'])
        site_packages = Path(self.config['site_packages_path'])

        with self.cache_client.pipeline() as pipe:
            for pkg_name in all_packages:
                main_key = f'{self.redis_key_prefix}{pkg_name}'
                versions = self.cache_client.smembers(f'{main_key}:installed_versions')
                for version in versions:
                    version_key = f'{main_key}:{version}'
                    path_str = self.cache_client.hget(version_key, 'path')
                    if not path_str:
                        continue
                    
                    path_obj = Path(path_str)
                    install_type = 'unknown'
                    owner_package = None

                    try:
                        relative_to_bubbles = path_obj.relative_to(multiversion_base)
                        bubble_dir_name = relative_to_bubbles.parts[0]
                        
                        expected_bubble_name = f"{canonicalize_name(pkg_name)}-{version}"

                        if bubble_dir_name == expected_bubble_name:
                            install_type = 'bubble'
                        else:
                            install_type = 'nested'
                            owner_package = bubble_dir_name
                    
                    except ValueError:
                        try:
                            path_obj.relative_to(site_packages)
                            install_type = 'active'
                        except ValueError:
                            install_type = 'unknown'

                    pipe.hset(version_key, 'install_type', install_type)
                    if owner_package:
                        pipe.hset(version_key, 'owner_package', owner_package)
                    else:
                        # Ensure old incorrect owner_package fields are removed
                        pipe.hdel(version_key, 'owner_package')
                        
                    migrated_count += 1
            
            pipe.set(flag_key, 'true')
            pipe.execute()

        safe_print(f'   ✅ Successfully upgraded {migrated_count} KB entries with correct context.')

    def _perform_redis_key_migration(self, migration_flag_key: str):
        """
        Performs a one-time, automatic migration of Redis keys from the old
        global format to the new environment-and-python-specific format.
        """
        safe_print('🔧 Performing one-time Knowledge Base upgrade for multi-environment support...')
        old_prefix = 'omnipkg:pkg:'
        all_old_keys = self.cache_client.keys(f'{old_prefix}*')
        if not all_old_keys:
            safe_print('   ✅ No old-format data found to migrate. Marking as complete.')
            self.cache_client.set(migration_flag_key, 'true')
            return
        new_prefix_for_current_env = self.redis_key_prefix
        migrated_count = 0
        with self.cache_client.pipeline() as pipe:
            for old_key in all_old_keys:
                new_key = old_key.replace(old_prefix, new_prefix_for_current_env, 1)
                pipe.rename(old_key, new_key)
                migrated_count += 1
            pipe.set(migration_flag_key, 'true')
            pipe.execute()
        safe_print(f'   ✅ Successfully upgraded {migrated_count} KB entries for this environment.')

    def _get_env_id(self) -> str:
        """Creates a short, stable hash from the venv path to uniquely identify it."""
        venv_path = str(Path(sys.prefix).resolve())
        return hashlib.md5(venv_path.encode()).hexdigest()[:8]

    @property
    def current_python_context(self) -> str:
        """
        (NEW) Helper property to get the current Python context string (e.g., 'py3.9').
        This is the single source of truth for the active context.
        """
        try:
            # This logic is derived from your redis_key_prefix property
            python_exe_path = self.config.get('python_executable', sys.executable)
            result = subprocess.run(
                [python_exe_path, '-c', "import sys; print(f'py{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True, text=True, check=True, timeout=2
            )
            return result.stdout.strip()
        except Exception:
            # Fallback for safety
            return f'py{sys.version_info.major}.{sys.version_info.minor}'

    def initialize_pypi_cache(self):
        """(MODIFIED & FIXED) Initialize PyPI version cache system."""

        # Default to no Redis client
        redis_instance = None

        # This is the defensive check:
        # 1. First, check if the 'redis' module was successfully imported (is not None).
        # 2. Then, check if a cache_client has even been configured on the instance.
        if redis and self.cache_client:
            # ONLY if both of the above are true, is it safe to check the instance type.
            if isinstance(self.cache_client, redis.Redis):
                redis_instance = self.cache_client

        # Now, instantiate the cache. redis_instance is guaranteed to be either a
        # valid Redis client or None, which prevents the crash.
        self.pypi_cache = PyPIVersionCache(
            redis_client=redis_instance
        )

        self.pypi_cache.clear_expired_cache()
        stats = self.pypi_cache.get_cache_stats()
        safe_print(f"📊 PyPI Contextual Cache initialized: {stats['valid_entries']} valid entries.")

    @property
    def redis_env_prefix(self) -> str:
        """
        Gets the environment-and-python-specific part of the Redis key,
        e.g., 'omnipkg:env_12345678:py3.11:'.
        This is the correct base for keys like 'index' that are not package-specific.
        """
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

    def _connect_cache(self) -> bool:
        """
        Establishes a cache connection. Auto-corrects misconfigurations gracefully.
        """
        # Fast Path: If a connection is already established, we're done.
        if self._cache_connection_status in ['redis_ok', 'sqlite_ok']:
            return True

        # Check if the user wants to use Redis.
        if self.config.get('redis_enabled', True):
            # The user wants Redis. Now, we check if we CAN use it.
            
            # CRITICAL CHECK 1: Is the 'redis' Python package actually installed?
            if not REDIS_AVAILABLE:
                safe_print('\n' + '⚠️ ' * 35)
                safe_print("CONFIGURATION AUTO-CORRECTION: Redis package not found")
                safe_print("\n   - Your config has `redis_enabled: true`")
                safe_print("   - However, the `redis` Python package is not installed")
                safe_print("   - Auto-switching to SQLite for this session")
                safe_print("\n   💡 To use Redis: 8pkg install redis")
                safe_print('⚠️ ' * 35 + '\n')
                
                # Auto-fix the config
                self._disable_redis_in_config(reason='package_not_installed')
                # Fall through to SQLite initialization below
                
            else:
                # Package is installed, try to connect
                try:
                    redis_host = self.config.get('redis_host', 'localhost')
                    redis_port = self.config.get('redis_port', 6379)
                    
                    cache_client = redis.Redis(
                        host=redis_host,
                        port=redis_port,
                        decode_responses=True,
                        socket_connect_timeout=2,
                        socket_timeout=2
                    )
                    cache_client.ping()
                    
                    self.cache_client = cache_client
                    self._cache_connection_status = 'redis_ok'
                    safe_print('⚡️ Connected to Redis successfully (High-performance mode).')
                    return True
                    
                except Exception as e:
                    safe_print('\n' + '⚠️ ' * 35)
                    safe_print("CONFIGURATION AUTO-CORRECTION: Redis server unreachable")
                    safe_print("\n   - Your config has `redis_enabled: true`")
                    safe_print("   - However, cannot connect to Redis server")
                    safe_print(f"   - Error: {type(e).__name__}: {e}")
                    safe_print("   - Auto-switching to SQLite for this session")
                    safe_print("\n   💡 To use Redis: Start redis-server or check connection")
                    safe_print('⚠️ ' * 35 + '\n')
                    
                    # Auto-fix the config
                    self._disable_redis_in_config(reason='server_unreachable')
                    # Fall through to SQLite initialization below

        # SQLite initialization (runs if Redis disabled or failed)
        safe_print("✅ SQLite cache initialized.")
        try:
            sqlite_db_path = self.config_manager.config_dir / f'cache_{self.env_id}.sqlite'
            self.cache_client = SQLiteCacheClient(db_path=sqlite_db_path)
            if not self.cache_client.ping():
                raise RuntimeError("SQLite connection failed.")
            
            self._cache_connection_status = 'sqlite_ok'
            return True
            
        except Exception as e:
            safe_print(f'❌ FATAL: Could not initialize SQLite cache: {e}')
            import traceback
            traceback.print_exc()
            self._cache_connection_status = 'failed_all'
            return False


    def _disable_redis_in_config(self, reason: str):
        """
        Updates the config file to disable Redis and record why.
        Does not raise exceptions - gracefully handles any failures.
        """
        try:
            import json
            from datetime import datetime
            
            config_path = self.config_manager.config_path
            
            # Read current config
            with open(config_path, 'r') as f:
                full_config = json.load(f)
            
            # Update the environment's config
            if 'environments' in full_config and self.env_id in full_config['environments']:
                env_config = full_config['environments'][self.env_id]
                
                # Only update if Redis was actually enabled
                if env_config.get('redis_enabled', True):
                    env_config['redis_enabled'] = False
                    env_config['redis_auto_disabled'] = True
                    env_config['redis_disabled_reason'] = reason
                    env_config['redis_disabled_timestamp'] = datetime.now().isoformat()
                    
                    # Write back
                    with open(config_path, 'w') as f:
                        json.dump(full_config, f, indent=4)
                    
                    safe_print('   ✅ Config updated: redis_enabled → false')
                    safe_print('   📝 Edit ~/.config/omnipkg/config.json to re-enable Redis\n')
            
            # Update in-memory config immediately to prevent retry loops
            self.config['redis_enabled'] = False
            
        except Exception as e:
            # If config update fails, just update in-memory and continue
            safe_print(f'   ⚠️  Could not persist config change: {e}')
            self.config['redis_enabled'] = False

    def _handle_redis_fallback(self):
        """
        Called when Redis is configured but unavailable. Updates config to prevent
        repeated connection attempts and notifies the user.
        """
        try:
            # Update the config to disable Redis for this environment
            config_path = self.config_manager.config_path
            
            with open(config_path, 'r') as f:
                full_config = json.load(f)
            
            if 'environments' in full_config and self.env_id in full_config['environments']:
                env_config = full_config['environments'][self.env_id]
                
                # Only update if Redis was actually enabled
                if env_config.get('redis_enabled', True) is True:
                    env_config['redis_enabled'] = False
                    env_config['redis_fallback_reason'] = 'server_unavailable'
                    env_config['redis_fallback_timestamp'] = str(datetime.now())
                    
                    with open(config_path, 'w') as f:
                        json.dump(full_config, f, indent=4)
                    
                    safe_print('   ℹ️  Updated config: redis_enabled → false')
                    safe_print('   💡 To re-enable Redis later, edit your config or run: 8pkg config set redis_enabled true')
            
            # Also update the in-memory config to prevent repeated attempts this session
            self.config['redis_enabled'] = False
            
        except Exception as e:
            # If we can't update the config, just log it and continue
            # The in-memory flag is more important
            safe_print(f'   ⚠️  Could not persist Redis fallback to config: {e}')
            self.config['redis_enabled'] = False

    def reset_configuration(self, force: bool=False) -> int:
        """
        Deletes the config.json file to allow for a fresh setup.
        """
        config_path = Path.home() / '.config' / 'omnipkg' / 'config.json'
        if not config_path.exists():
            safe_print(_('✅ Configuration file does not exist. Nothing to do.'))
            return 0
        safe_print(_('🗑️  This will permanently delete your configuration file at:'))
        safe_print(_('   {}').format(config_path))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                safe_print(_('🚫 Reset cancelled.'))
                return 1
        try:
            config_path.unlink()
            safe_print(_('✅ Configuration file deleted successfully.'))
            safe_print('\n' + '─' * 60)
            safe_print(_('🚀 The next time you run `omnipkg`, you will be guided through the first-time setup.'))
            safe_print('─' * 60)
            return 0
        except OSError as e:
            safe_print(_('❌ Error: Could not delete configuration file: {}').format(e))
            safe_print(_('   Please check your file permissions for {}').format(config_path))
            return 1

    def reset_knowledge_base(self, force: bool=False) -> int:
        """
        Deletes ALL omnipkg data for the CURRENT environment from Redis,
        and then triggers a full rebuild.
        """
        if not self._connect_cache():
            return 1
        env_context_prefix = self.redis_key_prefix.rsplit('pkg:', 1)[0]
        new_env_pattern = f'{env_context_prefix}*'
        old_global_pattern = 'omnipkg:pkg:*'
        migration_flag_pattern = 'omnipkg:migration:*'
        snapshot_pattern = 'omnipkg:snapshot:*'
        safe_print(_('\n🧠 omnipkg Knowledge Base Reset'))
        safe_print('-' * 50)
        safe_print(_("   This will DELETE all data for the current environment (matching '{}')").format(new_env_pattern))
        safe_print(_('   It will ALSO delete any legacy global data from older omnipkg versions.'))
        safe_print(_('   ⚠️  This command does NOT uninstall any Python packages.'))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                safe_print(_('🚫 Reset cancelled.'))
                return 1
        safe_print(_('\n🗑️  Clearing knowledge base...'))
        try:
            keys_new_env = self.cache_client.keys(new_env_pattern)
            keys_old_global = self.cache_client.keys(old_global_pattern)
            keys_migration = self.cache_client.keys(migration_flag_pattern)
            keys_snapshot = self.cache_client.keys(snapshot_pattern)
            all_keys_to_delete = set(keys_new_env + keys_old_global + keys_migration + keys_snapshot)
            if all_keys_to_delete:
                delete_command = self.cache_client.unlink if hasattr(self.cache_client, 'unlink') else self.cache_client.delete
                delete_command(*all_keys_to_delete)
                safe_print(_('   ✅ Cleared {} cached entries from Redis.').format(len(all_keys_to_delete)))
            else:
                safe_print(_('   ✅ Knowledge base was already clean.'))
        except Exception as e:
            safe_print(_('   ❌ Failed to clear knowledge base: {}').format(e))
            return 1
        self._info_cache.clear()
        self._installed_packages_cache = None

        # --- START OF THE CORRECTED LOGIC ---
        # 1. Run the rebuild and capture its success/failure status.
        rebuild_status = self.rebuild_knowledge_base(force=True)

        # 2. ONLY if the rebuild was successful (status 0) AND it was a forced
        #    run (like in CI), do we clear the "first use" flag.
        if rebuild_status == 0 and force:
            try:
                configured_exe = self.config.get('python_executable')
                version_tuple = self.config_manager._verify_python_version(configured_exe)
                if version_tuple:
                    current_version_str = f'{version_tuple[0]}.{version_tuple[1]}'
                    # NOTE: You will need to add the _clear_rebuild_flag_for_version
                    # helper method to your ConfigManager class for this to work.
                    self.config_manager._clear_rebuild_flag_for_version(current_version_str)
            except Exception as e:
                # This is a non-critical cleanup; log a warning but don't fail the command.
                safe_print(f"   - ⚠️  Warning: Could not automatically clear first-use flag: {e}")
            
        # 3. Return the original status of the rebuild operation.
        return rebuild_status

    def rebuild_knowledge_base(self, force: bool=False):
        """
        FIXED: Rebuilds the knowledge base by directly invoking the metadata gatherer
        in-process, now passing the correct target Python context to ensure
        metadata is stamped with the correct version.
        """
        safe_print(_('🧠 Forcing a full rebuild of the knowledge base...'))
        if not self._connect_cache():
            return 1
        try:
            configured_exe = self.config.get('python_executable')
            version_tuple = self.config_manager._verify_python_version(configured_exe)
            current_python_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else None
            if not current_python_version:
                safe_print(_('   ❌ CRITICAL: Could not determine configured Python version. Aborting rebuild.'))
                return 1
            safe_print(f'   🐍 Rebuilding knowledge base for Python {current_python_version} context...')
            gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, force_refresh=force, omnipkg_instance=self, target_context_version=current_python_version)
            gatherer.cache_client = self.cache_client
            gatherer.run()
            self._info_cache.clear()
            self._installed_packages_cache = None
            safe_print(_('✅ Knowledge base rebuilt successfully.'))
            return 0
        except Exception as e:
            safe_print(_('    ❌ An unexpected error occurred during knowledge base rebuild: {}').format(e))
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
            safe_print(_('   🔄 Rebuilding core package metadata...'))
            try:
                cmd = [self.config['python_executable'], self.config['builder_script_path'], '--force']
                subprocess.run(cmd, check=True)
                safe_print(_('   ✅ Core metadata rebuilt.'))
            except Exception as e:
                safe_print(_('   ❌ Metadata rebuild failed: {}').format(e))
        else:
            safe_print(_('   (Skipping {} - feature coming soon!)').format(component))

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
            safe_print(_("✅ No bubbles found for '{}'. Nothing to prune.").format(c_name))
            return 0
        bubbled_versions.sort(key=lambda x: parse_version(x['version']), reverse=True)
        to_prune = []
        if keep_latest is not None:
            if keep_latest < 0:
                safe_print(_("❌ 'keep-latest' must be a non-negative number."))
                return 1
            to_prune = bubbled_versions[keep_latest:]
            kept_count = len(bubbled_versions) - len(to_prune)
            safe_print(_('🔎 Found {} bubbles. Keeping the latest {}, pruning {} older versions.').format(len(bubbled_versions), kept_count, len(to_prune)))
        else:
            to_prune = bubbled_versions
            safe_print(_("🔎 Found {} bubbles to prune for '{}'.").format(len(to_prune), c_name))
        if not to_prune:
            safe_print(_('✅ No bubbles match the pruning criteria.'))
            return 0
        safe_print(_('\nThe following bubbled versions will be permanently deleted:'))
        for item in to_prune:
            safe_print(_('  - v{} (bubble)').format(item['version']))
        if active_version_info:
            safe_print(_('🛡️  The active version (v{}) will NOT be affected.').format(active_version_info['version']))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                safe_print(_('🚫 Prune cancelled.'))
                return 1
        specs_to_uninstall = [f"{item['name']}=={item['version']}" for item in to_prune]
        for spec in specs_to_uninstall:
            safe_print('-' * 20)
            self.smart_uninstall([spec], force=True)
        safe_print(_("\n🎉 Pruning complete for '{}'.").format(c_name))
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
        
        configured_exe = self.config.get('python_executable')
        version_tuple = self.config_manager._verify_python_version(configured_exe)
        if not version_tuple:
            return False
        
        current_version_str = f'{version_tuple[0]}.{version_tuple[1]}'
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
                safe_print(_('💡 First use of Python {} detected.').format(current_version_str))
                safe_print(_(' Building its knowledge base now...'))
                
                rebuild_status = self.rebuild_knowledge_base(force=True)
                
                if rebuild_status == 0:
                    # After a successful rebuild, update the flag file directly
                    # since we already hold the lock.
                    versions_to_rebuild.remove(current_version_str)
                    if not versions_to_rebuild:
                        flag_file.unlink(missing_ok=True)
                    else:
                        with open(flag_file, 'w') as f:
                            json.dump(versions_to_rebuild, f)
                    
                    safe_print(f' ✅ Knowledge base for Python {current_version_str} is ready.')
                    return True
                else:
                    safe_print(_(' ❌ Failed to build knowledge base. It will be re-attempted on the next run.'))
                    return False
        
        return False

    def _repair_manifest_context_mismatch(self, dist: importlib.metadata.Distribution, current_python_version: str) -> bool:
        """
        Surgically repairs a bubble's manifest file if its `python_version` does not
        match the Python context of its current location.
        """
        try:
            multiversion_base_path = Path(self.config.get('multiversion_base', '/dev/null'))
            
            # Robustly find the bubble's root directory and its manifest file
            relative_to_base = dist._path.relative_to(multiversion_base_path)
            bubble_root_name = relative_to_base.parts[0]
            bubble_root_path = multiversion_base_path / bubble_root_name
            manifest_file = bubble_root_path / '.omnipkg_manifest.json'

            if not manifest_file.exists():
                return False # Nothing to repair

            with open(manifest_file, 'r') as f:
                manifest_data = json.load(f)
            
            manifest_version = manifest_data.get('python_version')

            # The core detection logic
            if manifest_version and manifest_version != current_python_version:
                safe_print(f"   ⚠️  FIXING: Detected manifest mismatch in '{bubble_root_name}'.")
                safe_print(f"      - Manifest claims Python {manifest_version}, but location is for Python {current_python_version}.")
                
                # The repair action
                manifest_data['python_version'] = current_python_version
                
                # Atomically write the corrected file back to prevent corruption
                temp_file = manifest_file.with_suffix(f'{manifest_file.suffix}.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(manifest_data, f, indent=2)
                os.rename(temp_file, manifest_file)
                
                safe_print(f"      - ✅ Repaired manifest to claim Python {current_python_version}.")
                return True # Signal that a repair was made
                
        except (ValueError, IndexError, FileNotFoundError, json.JSONDecodeError, OSError):
            # If anything goes wrong during the check/repair, just assume no repair was made.
            return False
            
        return False
    
    def _fast_register_cloned_instances(self, instances_to_clone: List[importlib.metadata.Distribution], all_checksums_map: Dict[str, str]):
        """
        Efficiently registers new package instances by cloning metadata from existing
        identical instances already in the Knowledge Base.

        Args:
            instances_to_clone: A list of new Distribution objects to register.
            all_checksums_map: A pre-fetched dict mapping {checksum: existing_redis_key}.
        """
        if not instances_to_clone:
            return

        safe_print(f"   -> ⚡ Fast-registering {len(instances_to_clone)} new instance(s) with existing content...")
        
        from .package_meta_builder import omnipkgMetadataGatherer
        gatherer = omnipkgMetadataGatherer(
            config=self.config, env_id=self.env_id, omnipkg_instance=self
        )

        with self.cache_client.pipeline() as pipe:
            for dist in instances_to_clone:
                try:
                    # 1. Generate the checksum for the new instance.
                    temp_metadata = gatherer._build_comprehensive_metadata(dist)
                    checksum = temp_metadata.get('checksum')

                    if not checksum or checksum not in all_checksums_map:
                        continue # Should not happen, but a safeguard

                    # 2. Find the Redis key of an existing twin package.
                    twin_key = all_checksums_map[checksum]
                    cloned_data = self.cache_client.hgetall(twin_key)

                    if not cloned_data:
                        continue

                    # 3. Get the new instance's context (path, owner, etc.).
                    context_info = gatherer._get_install_context(dist)
                    is_active = (context_info.get('install_type') == 'active')

                    # 4. Update the cloned data with the new instance's specific info.
                    cloned_data.update(context_info)
                    cloned_data['path'] = str(dist._path.resolve())
                    cloned_data['last_indexed'] = datetime.now().isoformat()

                    # 5. Generate the NEW, UNIQUE Redis key for this new instance.
                    c_name = canonicalize_name(dist.metadata['Name'])
                    dist_info_path = Path(dist._path).resolve()
                    resolved_path_str = str(dist_info_path)
                    unique_instance_identifier = f"{resolved_path_str}::{dist.version}"
                    instance_hash = hashlib.sha256(unique_instance_identifier.encode()).hexdigest()[:12]
                    new_instance_key = f"{self.redis_key_prefix.replace(':pkg:', ':inst:')}{c_name}:{dist.version}:{instance_hash}"
                    cloned_data['installation_hash'] = instance_hash

                    # 6. Queue the commands to store the new instance in Redis.
                    flattened_data = self._flatten_dict(cloned_data)
                    pipe.delete(new_instance_key) # Ensure clean write
                    pipe.hset(new_instance_key, mapping=flattened_data)

                    # Also update the main package records
                    main_key = f'{self.redis_key_prefix}{c_name}'
                    pipe.sadd(f"{main_key}:installed_versions", dist.version)
                    pipe.sadd(f"{main_key}:{dist.version}:instances", instance_hash)

                except Exception as e:
                    safe_print(f"      - ⚠️  Failed to fast-clone {dist.metadata['Name']}: {e}")

            # 7. Execute all registrations in a single transaction.
            pipe.execute()

     
    def _synchronize_knowledge_base_with_reality(self, verbose: bool = False) -> List[importlib.metadata.Distribution]:
        """
        (UPGRADED - THE REPAIR BOT v7) Now uses resolved paths consistently
        for hash generation to prevent ghost/rebuild loops.
        """
        from .package_meta_builder import omnipkgMetadataGatherer
        if self._check_and_run_pending_rebuild():
            # If a rebuild was just run, the KB is perfectly in sync.
            # We can return early to avoid redundant work.
            safe_print("   ✅ First-use KB build complete. Synchronization is guaranteed.")
            gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, omnipkg_instance=self)
            return gatherer._discover_distributions(None, verbose=False)
        # --- END OF "FIRST USE" LOGIC ---
    
        self._clean_corrupted_installs()
        if self._check_and_run_pending_rebuild():
            pass

        safe_print(_('🧠 Checking knowledge base synchronization...'))

        configured_python_exe = self.config.get('python_executable', sys.executable)
        version_tuple = self.config_manager._verify_python_version(configured_python_exe)
        current_python_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else 'unknown'

        if not self.cache_client: self._connect_cache()
        if not self.cache_client: return []

        from .package_meta_builder import omnipkgMetadataGatherer
        gatherer = omnipkgMetadataGatherer(
            config=self.config,
            env_id=self.env_id,
            omnipkg_instance=self,
            target_context_version=current_python_version
        )
        
        # Initial discovery without filtering (to know what's on disk)
        all_discovered_dists = gatherer._discover_distributions(targeted_packages=None, verbose=False)
        
        active_dists_on_disk = {
            canonicalize_name(dist.metadata['Name']): dist.version
            for dist in all_discovered_dists
            if gatherer._get_install_context(dist)['install_type'] == 'active'
        }
        
        # ========================================================================
        # AUTHORITATIVE PATH-BASED HEALING (v9)
        # ========================================================================

        import os
        disk_path_map = {os.path.realpath(str(dist._path)): dist for dist in all_discovered_dists}

        # Step 2: Get all instance keys and their stored paths from the Knowledge Base.
        kb_instance_keys = self.cache_client.keys(self.redis_key_prefix.replace(':pkg:', ':inst:') + '*')
        kb_path_map = {}  # Maps a stored path back to its Redis key
        
        if kb_instance_keys:
            with self.cache_client.pipeline() as pipe:
                for key in kb_instance_keys:
                    pipe.hget(key, 'path')
                stored_paths_results = pipe.execute()

            for key, path_result in zip(kb_instance_keys, stored_paths_results):
                # --- THIS IS THE CRITICAL FIX ---
                # We MUST check if the result is a valid string before using it.
                if isinstance(path_result, str) and path_result.strip():
                    try:
                        resolved_path = os.path.realpath(path_result)
                        kb_path_map[resolved_path] = key
                    except Exception:
                        # Ignore paths that cause errors during realpath conversion.
                        pass
                # --- END OF CRITICAL FIX ---

        # Step 3: Compare the sets to find discrepancies.
        disk_paths = set(disk_path_map.keys())
        kb_paths = set(kb_path_map.keys())

        ghost_paths = kb_paths - disk_paths
        new_paths = disk_paths - kb_paths
        
        instances_to_rebuild = [disk_path_map[path] for path in new_paths]

        # --- Reporting and Debugging ---
        if instances_to_rebuild:
            safe_print(f"   -> 🔍 Found {len(instances_to_rebuild)} new/changed instance(s) that need to be registered.")
        if ghost_paths:
            safe_print(f"   -> 👻 Found {len(ghost_paths)} ghost instance(s) in the KB that no longer exist on disk.")
            #safe_print("    -> DEBUG: Analyzing first 5 ghosts to be deleted...")
            for i, path in enumerate(list(ghost_paths)[:5]):
                key = kb_path_map.get(path, '(unknown key)')
               #safe_print(f"      - GHOST {i+1}:")
                #safe_print(f"          Path in KB: {path}")
                #safe_print(f"          Key in KB:  {key}")

        # --- Healing Actions ---
        if instances_to_rebuild:
            safe_print(f"   -> 🧠 Rebuilding KB for {len(instances_to_rebuild)} instance(s)...")
            gatherer.run(pre_discovered_distributions=instances_to_rebuild)
        
        if ghost_paths:
            keys_to_delete = [kb_path_map[path] for path in ghost_paths]
            if keys_to_delete:
                safe_print(f"   -> 🗑️  Deleting {len(keys_to_delete)} ghost keys from the KB...")
                self.cache_client.delete(*keys_to_delete)
        
        if not instances_to_rebuild and not ghost_paths:
            pass
        else:
            safe_print("   -> ✅ Instance-level healing complete.")
            self._installed_packages_cache = None
        
        # ========================================================================
        # END INSTALLATION-HASH-BASED INSTANCE HEALING
        # ========================================================================

        # --- Repair active version mismatches (this logic stays the same) ---
        repairs_made = 0
        kb_active_versions = {}
        index_key = f"{self.redis_env_prefix}index"
        all_kb_packages = self.cache_client.smembers(index_key)
        for pkg_name in all_kb_packages:
            main_key = f'{self.redis_key_prefix}{pkg_name}'
            active_ver = self.cache_client.hget(main_key, 'active_version')
            if active_ver:
                kb_active_versions[pkg_name] = active_ver

        all_package_names = set(active_dists_on_disk.keys()) | set(kb_active_versions.keys())

        for pkg_name in sorted(list(all_package_names)):
            disk_version = active_dists_on_disk.get(pkg_name)
            kb_version = kb_active_versions.get(pkg_name)

            if disk_version != kb_version:
                if verbose:
                    safe_print(f"\n   - 💥 Inconsistency found for '{pkg_name}':")
                    safe_print(f"     - On Disk (Ground Truth): v{disk_version or 'Not Found'}")
                    safe_print(f"     - In Knowledge Base     : v{kb_version or 'Not Set'}")

                main_key = f'{self.redis_key_prefix}{pkg_name}'
                if disk_version:
                    safe_print(f"   - 🔧 REPAIRING KB: Setting '{pkg_name}' active version to v{disk_version}.")
                    self.cache_client.hset(main_key, 'active_version', disk_version)
                else:
                    safe_print(f"   - 🔧 REPAIRING KB: Removing stale active version for '{pkg_name}'.")
                    self.cache_client.hdel(main_key, 'active_version')
                
                repairs_made += 1
        
        if repairs_made > 0:
            safe_print(_('   - ✅ Repaired {} inconsistent "active" statuses in the knowledge base.').format(repairs_made))
            self._installed_packages_cache = None

        # Final verification: Check for any remaining ghost entries at the package level
        disk_specs = {f"{canonicalize_name(dist.metadata['Name'])}=={dist.version}" for dist in all_discovered_dists}
        kb_specs = set()
        for pkg_name in all_kb_packages:
            versions = self.cache_client.smembers(f"{self.redis_key_prefix}{pkg_name}:installed_versions")
            for version in versions:
                kb_specs.add(f"{pkg_name}=={version}")

        ghosts_in_kb = kb_specs - disk_specs
        if ghosts_in_kb:
            safe_print(f"   -> 👻 Found {len(ghosts_in_kb)} ghost package(s) to exorcise...")
            for spec in ghosts_in_kb:
                self._exorcise_ghost_entry(spec)

        # Check if we're fully synchronized now
        if disk_specs == kb_specs and repairs_made == 0 and not (instances_to_rebuild or ghost_paths or ghosts_in_kb):

            safe_print(_('   ✅ Knowledge base is in sync.'))
        else:
            safe_print(_('   ✅ Sync and repair complete.'))
        
        return all_discovered_dists
    
    def _get_disk_specs_for_context(self, python_version: str) -> set:
        """
        (V3 - ROBUST PATH FIX) A lightweight, READ-ONLY function to get the ground truth
        of package specs, now using robust logic to find the bubble root and check context.
        """
        from .package_meta_builder import omnipkgMetadataGatherer
        
        gatherer = omnipkgMetadataGatherer(
            config=self.config, 
            env_id=self.env_id, 
            omnipkg_instance=self, 
            target_context_version=python_version
        )
        
        all_discovered_dists = gatherer._discover_distributions(targeted_packages=None, verbose=False)
        
        disk_specs = set()
        multiversion_base_path = Path(self.config.get('multiversion_base', '/dev/null'))

        for dist in all_discovered_dists:
            try:
                context_info = gatherer._get_install_context(dist)
                
                if context_info['install_type'] == 'active':
                    disk_specs.add(f"{canonicalize_name(dist.metadata['Name'])}=={dist.version}")
                    continue

                if context_info['install_type'] in ['bubble', 'nested']:
                    is_compatible = False
                    
                    # --- THIS IS THE ROBUST FIX (MIRRORS THE BUILDER) ---
                    try:
                        relative_to_base = dist._path.relative_to(multiversion_base_path)
                        bubble_root_name = relative_to_base.parts[0]
                        bubble_root_path = multiversion_base_path / bubble_root_name
                        manifest_file = bubble_root_path / '.omnipkg_manifest.json'

                        if manifest_file.exists():
                            try:
                                with open(manifest_file, 'r') as f:
                                    manifest = json.load(f)
                                if manifest.get('python_version') == python_version:
                                    is_compatible = True
                            except Exception: 
                                is_compatible = True
                        else: 
                            is_compatible = True
                    except ValueError:
                        is_compatible = True
                    # --- END FIX ---

                    if is_compatible:
                        disk_specs.add(f"{canonicalize_name(dist.metadata['Name'])}=={dist.version}")
            except Exception:
                continue

        return disk_specs

    def _get_all_disk_instances_for_context(self, python_version: str, verbose: bool = False) -> Dict[str, importlib.metadata.Distribution]:
        """
        (V7.2 - CORRECTED) Discovers all physical package installations and returns them as a
        dictionary mapping a unique instance ID to the distribution object.
        Fixes the AttributeError by correctly using `self` and creating a new
        gatherer instance to access its discovery methods.
        """
        if verbose:
            safe_print("   -> Discovering all physical package installations on disk...")
        
        # --- THIS IS THE FIX ---
        # 1. We need to import the gatherer class to create an instance of it.
        from .package_meta_builder import omnipkgMetadataGatherer
        
        # 2. Create a new gatherer instance, passing the current omnipkg instance (`self`) to it.
        gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, omnipkg_instance=self, target_context_version=python_version)
        
        # 3. Call the _discover_distributions method on this new gatherer instance.
        all_distributions = gatherer._discover_distributions(targeted_packages=None, verbose=verbose)
        # --- END FIX ---
        
        disk_instances = {}
        for dist in all_distributions:
            try:
                # Use the same gatherer instance to get context information
                context_info = gatherer._get_install_context(dist._path)
                
                is_compatible = True
                if context_info['install_type'] in ['bubble', 'nested']:
                    # A simple check: if a manifest exists, check the python_version field
                    manifest_path = dist._path.parent / '.omnipkg_manifest.json'
                    if manifest_path.exists():
                        try:
                            with open(manifest_path, 'r') as f:
                                manifest = json.load(f)
                            bubble_py_ver = manifest.get('python_version')
                            if bubble_py_ver and bubble_py_ver != python_version:
                                is_compatible = False
                        except Exception:
                            pass # If manifest is broken, assume compatibility for now
                
                if is_compatible:
                    pkg_name = canonicalize_name(dist.metadata['Name'])
                    version = dist.version
                    path_str = str(dist._path)
                    
                    unique_instance_identifier = f"{path_str}::{version}"
                    instance_hash = hashlib.sha256(unique_instance_identifier.encode()).hexdigest()[:12]
                    
                    instance_id = f"{pkg_name}=={version}::{instance_hash}"
                    disk_instances[instance_id] = dist
            except Exception:
                continue # Skip corrupted distributions
        
        if verbose:
            safe_print(f"   -> Found {len(disk_instances)} physical instances for Python {python_version}.")
        return disk_instances

    def _get_kb_instances_by_package_for_context(self, python_version: str, verbose: bool = False) -> Dict[str, Dict[str, Dict[str, Dict]]]:
        """
        Gets all instance data from KB, organized by package name and version,
        but ONLY for instances indexed by the specified Python version.
        """
        # This function is now correctly scoped and can be simplified.
        # We query based on the key prefix which is already Python-version-specific.
        instance_key_prefix = self.redis_key_prefix.replace(':pkg:', ':inst:')
        context_key_pattern = f"{instance_key_prefix}*"
        all_instance_keys = self.cache_client.keys(context_key_pattern)

        if not all_instance_keys:
            return {}

        with self.cache_client.pipeline() as pipe:
            for key in all_instance_keys:
                pipe.hgetall(key)
        all_instance_data = pipe.execute()

        instances_by_package = defaultdict(lambda: defaultdict(dict))
        for key, instance_data in zip(all_instance_keys, all_instance_data):
            if not instance_data or instance_data.get('indexed_by_python') != python_version:
                continue

            key_parts = key.split(':')
            if len(key_parts) >= 5:
                # Key format: omnipkg:env_...:pyX.Y:inst:pkg-name:version:hash
                pkg_name = key_parts[4]
                version = key_parts[5]
                instance_hash = key_parts[6]
                instances_by_package[pkg_name][version][instance_hash] = instance_data

        if verbose:
            safe_print(f"   -> Found {len(all_instance_keys)} KB instances for Python {python_version} context.")
        return dict(instances_by_package)

    def _validate_instance_integrity(self, instance_data: Dict, pkg_name: str, version: str) -> List[str]:
        """ Simplified validation, as path context is handled by the main sync logic. """
        issues = []
        if not instance_data.get('Name'):
            issues.append('Name is empty')
        if instance_data.get('Version') != version:
            issues.append(f"Version mismatch: KB says {instance_data.get('Version')}, expected {version}")
        if not instance_data.get('path') or not Path(instance_data['path']).exists():
            issues.append('Path is missing or does not exist')
        if not instance_data.get('installation_hash'):
            issues.append('Missing installation_hash')
        return issues

    def _exorcise_ghost_package_instances(self, package_name: str, version: str, python_version: str):
        """
        Remove all ghost instances for a specific package version from the current Python context.
        """
        instance_key_prefix = self.redis_key_prefix.replace(':pkg:', ':inst:')
        pattern = f"{instance_key_prefix}{package_name}:{version}:*"
        
        ghost_keys = self.cache_client.keys(pattern)
        if not ghost_keys:
            return
        
        # Filter to only remove instances from current Python context
        keys_to_remove = []
        with self.cache_client.pipeline() as pipe:
            for key in ghost_keys:
                pipe.hget(key, 'indexed_by_python')
            python_versions = pipe.execute()
        
        for key, indexed_python in zip(ghost_keys, python_versions):
            if indexed_python == python_version:
                keys_to_remove.append(key)
        
        if keys_to_remove:
            safe_print(f'   -> Removing {len(keys_to_remove)} ghost instances for {package_name}=={version}')
            self.cache_client.delete(*keys_to_remove)
            
            # Also clean up any legacy package-version keys if they exist
            legacy_key = f"{self.redis_key_prefix}{package_name}:{version}"
            if self.cache_client.exists(legacy_key):
                self.cache_client.delete(legacy_key)
                
            # Update the installed_versions set
            main_key = f"{self.redis_key_prefix}{package_name}"
            installed_versions_key = f"{main_key}:installed_versions"
            
            # Only remove from installed_versions if no instances remain for this version
            remaining_pattern = f"{instance_key_prefix}{package_name}:{version}:*"
            if not self.cache_client.keys(remaining_pattern):
                self.cache_client.srem(installed_versions_key, version)

    def _get_all_active_versions_live_for_context(self, site_packages_path, verbose: bool=False):
        """
        Get active versions only from the specified site-packages directory.
        This prevents cross-interpreter contamination.
        """
        start_time = time.time()
        active_versions = {}
        if not site_packages_path or not site_packages_path.exists():
            if verbose:
                safe_print(_(' ⚠️ Site-packages path does not exist: {}').format(site_packages_path))
            return active_versions
        if verbose:
            safe_print(f' 🔍 Scanning for packages in: {site_packages_path}')
        package_categories = defaultdict(list)
        failed_packages = []
        try:
            for dist_info_path in site_packages_path.glob('*.dist-info'):
                if dist_info_path.is_dir():
                    try:
                        dist = importlib.metadata.Distribution.at(dist_info_path)
                        pkg_name = canonicalize_name(dist.metadata['Name'])
                        active_versions[pkg_name] = dist.version
                        if pkg_name in ['flask', 'django', 'fastapi', 'tornado']:
                            package_categories['web_frameworks'].append(pkg_name)
                        elif pkg_name in ['requests', 'urllib3', 'httpx', 'aiohttp']:
                            package_categories['http_clients'].append(pkg_name)
                        elif pkg_name in ['numpy', 'pandas', 'scipy', 'matplotlib', 'seaborn']:
                            package_categories['data_science'].append(pkg_name)
                        elif pkg_name in ['pytest', 'unittest2', 'nose', 'tox']:
                            package_categories['testing'].append(pkg_name)
                        elif pkg_name in ['click', 'argparse', 'fire', 'typer']:
                            package_categories['cli_tools'].append(pkg_name)
                        else:
                            package_categories['other'].append(pkg_name)
                    except Exception as e:
                        failed_packages.append((dist_info_path.name, str(e)))
                        continue
        except Exception as e:
            if verbose:
                safe_print(_(' ❌ Error scanning site-packages: {}').format(e))
        scan_time = time.time() - start_time
        safe_print(f'    ⏱️  Scan completed in {scan_time:.2f}s')
        safe_print(_('    ✅ Found {} packages total').format(len(active_versions)))
        if verbose:
            safe_print(_(' 📊 Package Scan Summary:'))
            for category, packages in package_categories.items():
                if packages and category != 'other':
                    count = len(packages)
                    sample = packages[:3]
                    sample_str = ', '.join(sample)
                    if count > 3:
                        sample_str += f' (+{count - 3} more)'
                    safe_print(_('    📦 {}: {} ({})').format(category.replace('_', ' ').title(), count, sample_str))
            if package_categories['other']:
                other_count = len(package_categories['other'])
                safe_print(_('    📦 Other packages: {}').format(other_count))
            if failed_packages:
                safe_print(_('    ⚠️  Failed to process {} packages').format(len(failed_packages)))
        return active_versions

    def _get_packages_in_bubbles_for_context(self, python_version, verbose: bool=False):
        """
        Get packages in bubbles, but only those created for the current Python version.
        """
        start_time = time.time()
        packages_in_bubbles = {}
        if not self.multiversion_base.exists():
            if verbose:
                safe_print(_(' ⚠️ Multiversion base does not exist: {}').format(self.multiversion_base))
            return packages_in_bubbles
        safe_print(f' 🫧 Scanning bubble packages for Python {python_version}...')
        package_categories = defaultdict(list)
        failed_bubbles = []
        skipped_version_count = 0
        total_bubbles_found = 0
        version_mismatches = defaultdict(int)
        python_version_key = f"python_{python_version.replace('.', '_')}"
        for dist_info_path in self.multiversion_base.rglob('*.dist-info'):
            if dist_info_path.is_dir():
                total_bubbles_found += 1
                try:
                    bubble_root = dist_info_path.parent
                    bubble_info_file = bubble_root / '.omnipkg_bubble_info'
                    bubble_python_version = None
                    if bubble_info_file.exists():
                        try:
                            with open(bubble_info_file, 'r') as f:
                                bubble_info = json.load(f)
                                bubble_python_version = bubble_info.get('python_version')
                        except:
                            pass
                    if bubble_python_version and bubble_python_version != python_version:
                        skipped_version_count += 1
                        version_mismatches[bubble_python_version] += 1
                        continue
                    dist = importlib.metadata.Distribution.at(dist_info_path)
                    pkg_name = canonicalize_name(dist.metadata['Name'])
                    if pkg_name not in packages_in_bubbles:
                        packages_in_bubbles[pkg_name] = set()
                    packages_in_bubbles[pkg_name].add(dist.version)
                    if pkg_name in ['flask', 'django', 'fastapi', 'tornado', 'bottle']:
                        package_categories['web_frameworks'].append(pkg_name)
                    elif pkg_name in ['requests', 'urllib3', 'httpx', 'aiohttp']:
                        package_categories['http_clients'].append(pkg_name)
                    elif pkg_name in ['numpy', 'pandas', 'scipy', 'matplotlib', 'seaborn', 'plotly']:
                        package_categories['data_science'].append(pkg_name)
                    elif pkg_name in ['pytest', 'unittest2', 'nose', 'tox', 'coverage']:
                        package_categories['testing'].append(pkg_name)
                    elif pkg_name in ['click', 'argparse', 'fire', 'typer']:
                        package_categories['cli_tools'].append(pkg_name)
                    elif pkg_name in ['sqlalchemy', 'psycopg2', 'pymongo', 'redis']:
                        package_categories['databases'].append(pkg_name)
                    elif pkg_name in ['jinja2', 'markupsafe', 'pyyaml', 'toml', 'configparser']:
                        package_categories['templating_config'].append(pkg_name)
                    elif pkg_name in ['cryptography', 'pycryptodome', 'bcrypt', 'passlib']:
                        package_categories['security'].append(pkg_name)
                    else:
                        package_categories['other'].append(pkg_name)
                except Exception as e:
                    failed_bubbles.append((dist_info_path.name, str(e)))
                    continue
        scan_time = time.time() - start_time
        safe_print(f'    ⏱️  Scan completed in {scan_time:.2f}s')
        safe_print(f'    📊 Total .dist-info directories found: {total_bubbles_found}')
        safe_print(_('    ✅ Matching Python {} packages: {}').format(python_version, len(packages_in_bubbles)))
        if verbose:
            safe_print(_(' 🫧 Bubble Package Scan Summary:'))
            if skipped_version_count > 0:
                safe_print(f'    ⏭️  Skipped {skipped_version_count} packages from other Python versions:')
                for version, count in sorted(version_mismatches.items()):
                    safe_print(_('        • Python {}: {} packages').format(version, count))
            for category, packages in package_categories.items():
                if packages and category != 'other':
                    count = len(packages)
                    unique_packages = list(set(packages))
                    sample = unique_packages[:3]
                    sample_str = ', '.join(sample)
                    if len(unique_packages) > 3:
                        sample_str += f' (+{len(unique_packages) - 3} more)'
                    safe_print(_('    📦 {}: {} instances ({})').format(category.replace('_', ' ').title(), count, sample_str))
            if package_categories['other']:
                other_count = len(set(package_categories['other']))
                safe_print(_('    📦 Other packages: {} unique types').format(other_count))
            if failed_bubbles:
                safe_print(_('    ⚠️  Failed to process {} bubbles').format(len(failed_bubbles)))
                if len(failed_bubbles) <= 3:
                    for name, error in failed_bubbles:
                        safe_print(_('        • {}: {}').format(name, error))
            multi_version_packages = {k: v for k, v in packages_in_bubbles.items() if len(v) > 1}
            if multi_version_packages:
                safe_print(f'    🔄 Packages with multiple bubble versions: {len(multi_version_packages)}')
                for pkg, versions in sorted(multi_version_packages.items()):
                    if len(multi_version_packages) <= 5:
                        version_list = ', '.join(sorted(versions))
                        safe_print(_('        • {}: {}').format(pkg, version_list))
        return packages_in_bubbles

    def _exorcise_ghost_entry(self, package_spec: str, filesystem_cleanup: bool = True):
        """
        Surgically removes a non-existent package entry from both KB and filesystem.
        If it's the last version of the package, it removes all traces,
        including the main package key and the index entry.
        
        Args:
            package_spec: Package specification like "package==version"
            filesystem_cleanup: Whether to remove orphaned .dist-info dirs (default True)
        """
        try:
            pkg_name, version = self._parse_package_spec(package_spec)
            if not pkg_name or not version:
                return
                
            c_name = canonicalize_name(pkg_name)
            safe_print(f'   -> 👻 Exorcising ghost entry: {c_name}=={version}')
            
            # 1. Clean up Redis KB first
            main_key = f'{self.redis_key_prefix}{c_name}'
            version_key = f'{main_key}:{version}'
            versions_set_key = f'{main_key}:installed_versions'
            index_key = f'{self.redis_env_prefix}index'
            
            with self.cache_client.pipeline() as pipe:
                pipe.delete(version_key)
                pipe.srem(versions_set_key, version)
                if self.cache_client.hget(main_key, 'active_version') == version:
                    pipe.hdel(main_key, 'active_version')
                pipe.hdel(main_key, f'bubble_version:{version}')
                pipe.execute()
                
            # Check if this was the last version
            versions_remaining = self.cache_client.scard(versions_set_key)
            if versions_remaining == 0:
                safe_print(f"    -> Last version of '{c_name}' removed. Deleting all traces from KB.")
                with self.cache_client.pipeline() as pipe:
                    pipe.delete(main_key)
                    pipe.delete(versions_set_key)
                    pipe.srem(index_key, c_name)
                    pipe.execute()
            
            # 2. Clean up filesystem ghosts if requested
            if filesystem_cleanup:
                self._remove_ghost_dist_info(pkg_name, version, c_name)
                
        except Exception as e:
            safe_print(f'   ⚠️  Warning: Could not exorcise ghost {package_spec}: {e}')

    def _remove_ghost_dist_info(self, pkg_name: str, version: str, c_name: str, site_packages_path=None):
        """
        Remove orphaned .dist-info directories that have no corresponding package.
        
        Args:
            pkg_name: Original package name
            version: Package version
            c_name: Canonicalized package name  
            site_packages_path: Path to site-packages (optional)
        """
        # Use provided path or fall back to configured one
        if site_packages_path:
            site_packages = Path(site_packages_path)
        elif hasattr(self, 'site_packages') and self.site_packages:
            site_packages = Path(self.site_packages)
        else:
            return
        if not site_packages.exists():
            return
        
        # Generate possible .dist-info directory names
        possible_names = [
            f"{pkg_name}-{version}.dist-info",
            f"{c_name}-{version}.dist-info",
            f"{pkg_name.replace('-', '_')}-{version}.dist-info",
            f"{c_name.replace('-', '_')}-{version}.dist-info",
        ]
        
        for dist_info_name in possible_names:
            dist_info_path = site_packages / dist_info_name
            if dist_info_path.exists() and dist_info_path.is_dir():
                # Verify it's actually a ghost by checking if package exists
                if self._is_ghost_dist_info(dist_info_path, pkg_name, c_name):
                    try:
                        safe_print(f"    -> 🗑️  Removing ghost .dist-info: {dist_info_name}")
                        shutil.rmtree(dist_info_path)
                    except Exception as e:
                        safe_print(f"    -> ⚠️  Failed to remove {dist_info_name}: {e}")

    def _is_ghost_dist_info(self, dist_info_path: Path) -> bool:
        """
        (QUIET VERSION) Determines if a .dist-info directory is a "ghost" by
        using the standard library and ignoring unreliable cache files. This version
        produces no output.
        """
        try:
            dist = importlib.metadata.Distribution.at(dist_info_path)
            if not dist.files:
                return False

            for file_path_obj in dist.files:
                file_path_str = str(file_path_obj)
                
                # Ignore metadata, pycache, and compiled bytecode as they are not
                # reliable indicators of a package's presence.
                if (dist_info_path.name in file_path_str or 
                    '__pycache__' in file_path_str or 
                    file_path_str.endswith(('.pyc', '.pyo'))):
                    continue

                absolute_path = dist.locate_file(file_path_obj)

                if absolute_path and absolute_path.exists():
                    # Found a live file, so it is NOT a ghost.
                    return False

            # If we checked all real files and found none, it IS a ghost.
            return True

        except Exception:
            # On any error, play it safe and assume it's not a ghost.
            return False

    def _clean_corrupted_installs(self):
        """
        (ULTRA-OPTIMIZED) Fast, multi-context cleanup of corrupted pip installations
        (directories starting with ~). It uses a fast-path check to avoid work on
        clean environments and minimizes filesystem calls.
        """
        # --- Step 1: Gather all unique site-packages paths ---
        # This is fast as it primarily reads from memory/config.
        paths_to_scan = set()
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        for version, exe_path in managed_interpreters.items():
            paths = self.config_manager._get_paths_for_interpreter(str(exe_path))
            if paths and paths.get('site_packages_path'):
                paths_to_scan.add(Path(paths['site_packages_path']))

        # --- Step 2: The Fast-Path Exit ---
        # Perform a single, quick generator-based scan. This is the key optimization.
        # We check all paths for ANY sign of trouble before proceeding.
        first_corrupted_item = None
        for sp_path in paths_to_scan:
            if sp_path.is_dir():
                # next() stops the glob the moment the first match is found.
                first_corrupted_item = next(sp_path.glob('~*'), None)
                if first_corrupted_item:
                    break # Found one, no need to scan further.

        # If we finished the loop and found nothing, exit immediately.
        # This is the fast path for 99% of runs.
        if not first_corrupted_item:
            return

        # --- Step 3: Full Cleanup (only if the fast-path failed) ---
        # If we are here, we know there's at least one corrupted file, so now we
        # do the full scan and cleanup.
        safe_print('\n' + '─' * 60)
        safe_print("🛡️  AUTO-HEAL: Cleaning corrupted installations...")
        cleanup_count = 0
        
        # We already found one item, so process it and then find the rest.
        all_corrupted_items = [first_corrupted_item]
        for sp_path in paths_to_scan:
             if sp_path.is_dir():
                # Add any other corrupted items from all paths
                all_corrupted_items.extend(p for p in sp_path.glob('~*') if p != first_corrupted_item)
        
        # De-duplicate the list
        all_corrupted_items = list(dict.fromkeys(all_corrupted_items))

        for item in all_corrupted_items:
            try:
                safe_print(f"    -> 💀 Removing corrupted: {item.name}")
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                safe_print(f"       -> 🗑️  Successfully deleted")
                cleanup_count += 1
            except Exception:
                # Fallback for stubborn items, using sudo if available
                try:
                    cmd = ['rm', '-rf', str(item)]
                    if shutil.which('sudo'):
                        cmd.insert(0, 'sudo')
                    subprocess.run(cmd, check=True, capture_output=True)
                    safe_print(f"       -> 🗑️  Force-deleted stubborn item")
                    cleanup_count += 1
                except Exception as e:
                    safe_print(f"    -> ⚠️  Could not remove {item.name}: {e}")

        if cleanup_count > 0:
            safe_print(f"🎉 Cleaned {cleanup_count} corrupted installations")
            # A rebuild is necessary after filesystem surgery.
            self.rebuild_knowledge_base(force=True)
        safe_print('─' * 60)

    def _hunt_and_exorcise_all_ghosts(self, site_packages_path=None, live_active_versions=None):
        """
        (QUIET VERSION) Scans for and removes ALL ghost .dist-info directories,
        only printing output if it finds and deletes something.
        """
        # Use provided path or fall back to configured one
        if site_packages_path:
            site_packages = Path(site_packages_path)
        elif hasattr(self, 'site_packages') and self.site_packages:
            site_packages = Path(self.site_packages)
        else:
            return
        if not site_packages.exists():
            return

        ghosts_found = []
        for dist_info_path in site_packages.glob("*.dist-info"):
            if dist_info_path.is_dir() and self._is_ghost_dist_info(dist_info_path):
                ghosts_found.append(dist_info_path)

        if not ghosts_found:
            # If the environment is clean, we print nothing.
            return

        # Only if we find ghosts do we print the "Hunting" message.
        safe_print("🔍 Hunting for ghost .dist-info directories...")
        ghost_count = 0
        for dist_info_path in ghosts_found:
            try:
                safe_print(f"    -> 👻 Found ghost: {dist_info_path.name}")
                shutil.rmtree(dist_info_path)
                safe_print(f"       -> 🗑️  Successfully deleted ghost directory.")
                ghost_count += 1
                
                name_version = dist_info_path.name[:-10]
                parts = name_version.rsplit('-', 1)
                if len(parts) == 2:
                    pkg_name, version = parts
                    self._exorcise_ghost_entry(f"{pkg_name}=={version}", filesystem_cleanup=False)

            except Exception as e:
                safe_print(f"    -> ⚠️  Failed to remove ghost {dist_info_path.name}: {e}")

        if ghost_count > 0:
            safe_print(f"🎉 Exorcised {ghost_count} ghost .dist-info directories.")

    def doctor(self, dry_run: bool = False, force: bool = False) -> int:
        """
        Diagnoses and repairs a corrupted environment by removing orphaned
        package metadata ("ghosts").
        """
        safe_print('\n' + '=' * 60)
        safe_print("🩺 OMNIPKG ENVIRONMENT DOCTOR")
        safe_print('=' * 60)
        safe_print(f"🔬 Performing forensic scan of: {self.config['site_packages_path']}")

        site_packages = Path(self.config['site_packages_path'])
        all_dist_infos = list(site_packages.glob('*.dist-info'))
        
        # Step 1: Group metadata by package name to find conflicts
        packages = defaultdict(list)
        for path in all_dist_infos:
            try:
                # Extract name like 'rich' from 'rich-14.1.0.dist-info'
                package_name = path.name.split('-')[0].lower().replace('_', '-')
                packages[package_name].append(path)
            except IndexError:
                continue

        conflicted_packages = {name: paths for name, paths in packages.items() if len(paths) > 1}

        if not conflicted_packages:
            safe_print("\n✅ Environment is healthy. No conflicts found.")
            return 0

        safe_print(f"\n🚨 DIAGNOSIS: Found {len(conflicted_packages)} packages with conflicting metadata!")
        
        ghosts_to_exorcise = []
        
        # Step 2 & 3: Perform the autopsy and identify ghosts for each conflict
        for name, paths in conflicted_packages.items():
            safe_print(f"\n--- Autopsy for: '{name}' ---")
            found_versions = sorted([p.name.split('-')[1] for p in paths])
            safe_print(f"  - Found Metadata Versions: {', '.join(found_versions)}")

            canonical_version = None
            python_exe = self.config['python_executable']
            import_name = name.replace('-', '_')

            # Step 1: Try actual import for __version__
            try:
                cmd = [
                    python_exe, '-c',
                    f"import {import_name}; print(getattr({import_name}, '__version__', None))"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
                version_output = result.stdout.strip()
                
                if version_output and version_output != 'None':
                    canonical_version = version_output
                    safe_print(f"  - Live Code Version (Ground Truth): {canonical_version}  ✅")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Import failed, that's ok - we'll try metadata
                pass

            # Step 2: If import didn't work or had no __version__, try importlib.metadata
            if not canonical_version:
                try:
                    safe_print(f"  - ⚠️  Direct import unavailable. Falling back to metadata...")
                    cmd = [
                        python_exe, '-c',
                        f"try: import importlib.metadata as meta\n"
                        f"except ImportError: import importlib_metadata as meta\n"
                        f"print(meta.version('{name}'))"
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
                    canonical_version = result.stdout.strip()
                    safe_print(f"  - Metadata Version: {canonical_version}  📦")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    safe_print(f"  - ⚠️  Could not determine version for '{name}'. Skipping.")
                    continue

            if not canonical_version:
                safe_print(f"  - ⚠️  No version found for '{name}'. Skipping.")
                continue
            
            # Identify the keeper and the ghosts
            keeper_dist_info = site_packages / f"{name}-{canonical_version}.dist-info"
            
            try:
                # Use the packaging library to create a comparable Version object
                parsed_canonical_version = parse_version(canonical_version)
            except Exception as e:
                safe_print(f"  - ⚠️  Could not parse live version '{canonical_version}' for '{name}'. Skipping. Error: {e}")
                continue

            # Identify the keeper and the ghosts
            for path in paths:
                is_keeper = False
                try:
                    # 'rich-14.1.0.dist-info' -> 'rich-14.1.0'
                    base_name = path.name.removesuffix('.dist-info')
                    
                    # Reliably split name and version by splitting from the right
                    package_part, version_part = base_name.rsplit('-', 1)

                    # Compare using normalized names and parsed versions
                    parsed_path_version = parse_version(version_part)
                    
                    if (canonicalize_name(package_part) == canonicalize_name(name) and 
                        parsed_path_version == parsed_canonical_version):
                        # This is the keeper, do not delete
                        is_keeper = True
                
                except Exception:
                    # If parsing the directory name fails, it's non-standard.
                    # Treat it as a ghost.
                    pass
                
                if not is_keeper:
                    # If it's not the keeper, it's a ghost.
                    ghosts_to_exorcise.append(path)

        if not ghosts_to_exorcise:
            safe_print("\n✅ All conflicts resolved without action (e.g., could not determine canonical version).")
            return 0

        # Step 4: Present the healing plan
        safe_print("\n" + "─" * 60)
        safe_print("💔 HEALING PLAN: The following orphaned metadata ('ghosts') will be deleted:")
        for ghost in ghosts_to_exorcise:
            safe_print(f"  - 👻 {ghost.name}")
        safe_print("─" * 60)

        if dry_run:
            safe_print("\n🔬 Dry run complete. No changes were made.")
            return 0

        if not force:
            confirm = input("\n🤔 Proceed with the exorcism? (y/N): ").lower().strip()
            if confirm != 'y':
                safe_print("🚫 Healing cancelled by user.")
                return 1

        # Step 5: Execute the healing
        safe_print("\n🔥 Starting exorcism...")
        healed_count = 0
        for ghost in ghosts_to_exorcise:
            try:
                safe_print(f"  - 🗑️  Deleting {ghost.name}...")
                shutil.rmtree(ghost)
                healed_count += 1
            except OSError as e:
                safe_print(f"  - ❌ FAILED to delete {ghost.name}: {e}")

        safe_print(f"\n✨ Healing complete. {healed_count} ghosts exorcised.")

        # Step 6: Finalize and resync
        safe_print("🧠 The environment has changed. Forcing a full knowledge base rebuild...")
        self.rebuild_knowledge_base(force=True)
        
        safe_print("\n🎉 Your environment is now clean and healthy!")
        return 0
    
    def heal(self, dry_run: bool = False, force: bool = False) -> int:
        """
        (UPGRADED) Audits, reconciles conflicting requirements, and attempts
        to fix dependency conflicts by installing a single, consistent set of packages.
        """
        safe_print('\n' + '=' * 60)
        safe_print("❤️‍🩹 OMNIPKG ENVIRONMENT HEALER")
        safe_print('=' * 60)
        safe_print("🔬 Auditing package dependencies...")

        try:
            pip_exe = Path(self.config['python_executable']).parent / 'pip'
            result = subprocess.run([str(pip_exe), 'check'], capture_output=True, text=True, encoding='utf-8')
        except Exception as e:
            safe_print(f"❌ An unexpected error occurred during the audit: {e}")
            return 1

        if result.returncode == 0:
            safe_print("\n✅ Your environment is healthy. No dependency conflicts found!")
            return 0
        
        # Step 2a: Group all requirements by package name
        conflict_output = result.stdout
        safe_print("\n🚨 DIAGNOSIS: Found dependency conflicts!")
        
        conflict_regex = re.compile(r"(\S+) \S+ (?:has requirement|requires) ([^,]+),")
        
        grouped_reqs = defaultdict(list)
        for line in conflict_output.splitlines():
            # A more robust regex to capture the full specifier
            match = re.search(r"has requirement (.+?), but you have", line)
            if not match:
                match = re.search(r"requires (.+?), which is not installed", line)

            if match:
                spec = match.group(1).strip()
                pkg_name_match = re.match(r"([a-zA-Z0-9_.-]+)", spec)
                if pkg_name_match:
                    pkg_name = canonicalize_name(pkg_name_match.group(1))
                    grouped_reqs[pkg_name].append(spec)

        if not grouped_reqs:
            safe_print("\n🤔 Could not parse any specific actions from the audit.")
            return 1
            
        # Step 2b: Reconcile conflicts by electing one "winner" version for each package
        reconciled_plan = []
        safe_print("\n" + "─" * 60)
        safe_print("🤝 Reconciling conflicting requirements...")
        
        reconciled_plan = []
        safe_print("\n" + "─" * 60)
        safe_print("🤝 Reconciling conflicting requirements...")

        for pkg_name, specs in grouped_reqs.items():
            safe_print(f"   - Reconciling '{pkg_name}':")
            
            # --- START: NEW INTERSECTION LOGIC ---
            
            # 1. Collect ONLY the version specifier parts from each requirement string.
            version_constraints = []
            for spec_str in specs:
                # Use regex to robustly strip the package name and any markers
                match = re.match(r"^\s*([a-zA-Z0-9_.-]+)\s*(.*)", spec_str)
                if match:
                    # part will be like '>=0.24.0; python_version > "3.8"'
                    part = match.group(2).strip()
                    # remove the marker part
                    if ';' in part:
                        part = part.split(';', 1)[0].strip()
                    
                    if part:
                        version_constraints.append(part)
                        safe_print(f"     - Found constraint: {part}")

            if not version_constraints:
                safe_print(f"   - ⚠️  Could not extract any version constraints for '{pkg_name}'. Skipping.")
                continue

            # 2. Combine all constraints into a single, comma-separated string.
            # This represents the intersection of all requirements.
            # Example: ">=0.24.0,<0.18,>=0.16.4,>=0.25.0"
            combined_spec = f"{pkg_name}{','.join(version_constraints)}"
            safe_print(f"     - Combined spec: '{combined_spec}'")

            # 3. Use our powerful resolver on this SINGLE combined spec.
            # _find_best_version_for_spec is smart enough to handle this.
            resolved_spec_str = self._find_best_version_for_spec(combined_spec)

            if resolved_spec_str:
                safe_print(f"   - ✅ Elected Winner via intersection: {resolved_spec_str}")
                reconciled_plan.append(resolved_spec_str)
            else:
                # This is CRITICAL. It now correctly identifies truly impossible situations.
                safe_print(f"   - ❌ Unresolvable conflict for '{pkg_name}'. The combined requirements are impossible to satisfy.")
                safe_print(f"     - Failed to resolve: '{combined_spec}'")
                # In a future version, you could add logic here to suggest upgrading the
                # packages that are causing the constraint (e.g., 'tokenizers').
                # For now, we notify the user and cannot proceed with this package.

        # --- END: NEW INTERSECTION LOGIC ---
        
        if not reconciled_plan:
            safe_print("\n" + "─" * 60)
            safe_print("" "✅ No actions needed after reconciliation.")
            return 0

        # Step 3: Present the final, possible healing plan
        safe_print("\n" + "─" * 60)
        safe_print("💊 FINAL HEALING PLAN:")
        for pkg in sorted(reconciled_plan):
            safe_print(f"  - 🎯 {pkg}")
        safe_print("─" * 60)

        if dry_run:
            safe_print("\n🔬 Dry run complete. No changes were made.")
            return 0

        if not force:
            confirm = input("\n🤔 Proceed with healing? (y/N): ").lower().strip()
            if confirm != 'y':
                safe_print("🚫 Healing cancelled by user.")
                return 1

        # Step 4: Execute the reconciled plan
        safe_print("\n🔥 Applying treatment...")
        return self.smart_install(reconciled_plan)

    def _update_hash_index_for_delta(self, before: Dict, after: Dict):
        """Surgically updates the cached hash index in Redis after an install."""
        if not self.cache_client:
            self._connect_cache()
        redis_key = _('{}main_env:file_hashes').format(self.redis_key_prefix)
        if not self.cache_client.exists(redis_key):
            return
        safe_print(_('🔄 Updating cached file hash index...'))
        uninstalled_or_changed = {name: ver for name, ver in before.items() if name not in after or after[name] != ver}
        installed_or_changed = {name: ver for name, ver in after.items() if name not in before or before[name] != ver}
        with self.cache_client.pipeline() as pipe:
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
        safe_print(_('✅ Hash index updated.'))

    def get_installed_packages(self, live: bool=False) -> Dict[str, str]:
        if live:
            try:
                cmd = [self.config['python_executable'], '-I', '-m', 'pip', 'list', '--format=json']
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                live_packages = {pkg['name'].lower(): pkg['version'] for pkg in json.loads(result.stdout)}
                self._installed_packages_cache = live_packages
                return live_packages
            except Exception as e:
                safe_print(_('    ⚠️  Could not perform live package scan: {}').format(e))
                return self._installed_packages_cache or {}
        if self._installed_packages_cache is None:
            if not self.cache_client:
                self._connect_cache()
            self._installed_packages_cache = self.cache_client.hgetall(_('{}versions').format(self.redis_key_prefix))
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
        (V2 - CONTEXT-AWARE FIX) Atomically updates the knowledge base by directly
        invoking the metadata gatherer in-process, now correctly passing the
        current Python context to ensure metadata is processed correctly.
        """
        changed_specs = [f'{name}=={ver}' for name, ver in after.items() if name not in before or before[name] != ver]
        uninstalled = {name: ver for name, ver in before.items() if name not in after}
        if not changed_specs and (not uninstalled):
            safe_print(_('✅ Knowledge base is already up to date.'))
            return
        safe_print(_('🧠 Updating knowledge base for changes...'))
        try:
            # --- THIS IS THE FIX ---
            # Determine the current, correct Python context.
            configured_exe = self.config.get('python_executable', sys.executable)
            version_tuple = self.config_manager._verify_python_version(configured_exe)
            current_python_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else None
            # --- END FIX ---

            if changed_specs:
                safe_print(_('   -> Processing {} changed/new package(s) for Python {} context...').format(len(changed_specs), current_python_version))
                # --- PASS THE CONTEXT TO THE GATHERER ---
                gatherer = omnipkgMetadataGatherer(
                    config=self.config, env_id=self.env_id, force_refresh=True, 
                    omnipkg_instance=self, target_context_version=current_python_version
                )
                gatherer.cache_client = self.cache_client
                gatherer.run(targeted_packages=changed_specs)

            if uninstalled:
                safe_print(_('   -> Cleaning up {} uninstalled package(s) from Redis...').format(len(uninstalled)))
                with self.cache_client.pipeline() as pipe:
                    for pkg_name, uninstalled_version in uninstalled.items():
                        # ... (rest of the cleanup logic is correct)
                        c_name = canonicalize_name(pkg_name)
                        main_key = f'{self.redis_key_prefix}{c_name}'
                        version_key = f'{main_key}:{uninstalled_version}'
                        versions_set_key = f'{main_key}:installed_versions'
                        pipe.delete(version_key)
                        pipe.srem(versions_set_key, uninstalled_version)
                        if self.cache_client.hget(main_key, 'active_version') == uninstalled_version:
                            pipe.hdel(main_key, 'active_version')
                        pipe.hdel(main_key, f'bubble_version:{uninstalled_version}')
                    pipe.execute()

            self._info_cache.clear()
            self._installed_packages_cache = None
            safe_print(_('✅ Knowledge base updated successfully.'))
        except Exception as e:
            safe_print(_('    ⚠️ Failed to update knowledge base for delta: {}').format(e))
            import traceback
            traceback.print_exc()

    def show_package_info(self, package_spec: str) -> int:
        if not self._connect_cache():
            return 1
            
        # --- FIX: Capture the results of the sync scan ---
        all_discovered_distributions = self._synchronize_knowledge_base_with_reality()
        
        try:
            pkg_name, requested_version = self._parse_package_spec(package_spec)
            if requested_version:
                safe_print('\n' + '=' * 60)
                safe_print(_('📄 Detailed info for {} v{}').format(pkg_name, requested_version))
                safe_print('=' * 60)
                
                # FIX: Find the installation and pass the dict, not the string
                installations = self._find_package_installations(pkg_name)
                
                # Look for the specific version
                target_install = None
                for install in installations:
                    if install.get('Version') == requested_version:
                        target_install = install
                        break
                
                if not target_install:
                    safe_print(_('❌ Version {} not found for package {}').format(requested_version, pkg_name))
                    safe_print(_('💡 Available versions:'))
                    for install in installations:
                        safe_print(f"   - {install.get('Version')} ({install.get('install_type', 'unknown')})")
                    return 1
                
                # Pass the dict to _show_version_details
                self._show_version_details(target_install)
            else:
                # --- FIX: Pass the discovered distributions down to the display function ---
                self._show_enhanced_package_data(pkg_name, pre_discovered_dists=all_discovered_distributions)
            return 0
        except Exception as e:
            safe_print(_('❌ An unexpected error occurred while showing package info: {}').format(e))
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

    def _show_enhanced_package_data(self, package_name: str, pre_discovered_dists: Optional[List[importlib.metadata.Distribution]] = None):
        """
        (REWRITTEN for INSTANCE-AWARE data) Displays a clear summary of all
        package installations, correctly distinguishing between all unique instances.
        Now accepts pre-discovered distributions to avoid re-scanning the filesystem.
        """
        c_name = canonicalize_name(package_name)
        
        # --- FIX: Pass the pre-discovered distributions to avoid a rescan ---
        all_installations = self._find_package_installations(c_name, pre_discovered_dists=pre_discovered_dists)

        if not all_installations:
            safe_print(_("\n📋 KEY DATA: No installations found for '{}'").format(package_name))
            return

        # Sort for predictable display
        all_installations.sort(key=lambda x: (not x.get('is_active', False), x.get('type', 'z'), parse_version(x.get('Version', '0'))), reverse=True)

        # Present the KEY DATA summary
        safe_print(_("\n📋 KEY DATA for '{}':").format(package_name))
        print('-' * 40)
        
        active_detail = next((inst for inst in all_installations if inst.get('is_active')), None)
        if active_detail:
            safe_print(_('🎯 Active Version: {} ({})').format(active_detail['Version'], active_detail['install_type']))
        else:
            safe_print(_('🎯 Active Version: Not Set'))

        bubbled_versions = sorted(list(set(inst['Version'] for inst in all_installations if inst.get('install_type') == 'bubble')))
        if bubbled_versions:
            safe_print(_('🫧 Bubbled Versions: {}').format(', '.join(bubbled_versions)))

        nested_count = sum(1 for inst in all_installations if inst.get('install_type') == 'nested')
        if nested_count > 0:
            safe_print(_('📦 Nested Versions: {} found inside other bubbles').format(nested_count))

        # Build and display the interactive list
        safe_print(_('\n📦 Available Installations:'))
        for i, detail in enumerate(all_installations, 1):
            status_parts = []
            if detail.get('is_active'):
                status_parts.append('active')
            
            install_type = detail.get('install_type', 'unknown')
            if install_type == 'bubble':
                status_parts.append('bubble')
            elif install_type == 'nested':
                status_parts.append(f"nested in {detail.get('owner_package', 'Unknown')}")
            
            status_str = f" ({', '.join(status_parts)})" if status_parts else ''
            safe_print(_('  {}) v{}{}'.format(i, detail.get('Version', '?'), status_str)))
        
        # Handle user interaction
        safe_print(_('\n💡 Want details on a specific version?'))
        try:
            choice = input(_('Enter number (1-{}) or press Enter to skip: ').format(len(all_installations))).strip()
            if choice:
                idx = int(choice) - 1
                if 0 <= idx < len(all_installations):
                    selected_inst = all_installations[idx]
                    print('\n' + '=' * 60)
                    safe_print(_('📄 Detailed info for {} v{} ({})').format(c_name, selected_inst['Version'], selected_inst['install_type']))
                    print('=' * 60)
                    self._show_version_details(selected_inst)
                else:
                    safe_print(_('❌ Invalid selection.'))
        except (ValueError, KeyboardInterrupt, EOFError):
            safe_print(_('\n   Skipped.'))

    def get_all_versions(self, package_name: str) -> List[str]:
        """Get all versions (active + bubbled) for a package"""
        overview_key = f'{self.redis_key_prefix}{package_name.lower()}'
        overview_data = self.cache_client.hgetall(overview_key)
        active_ver = overview_data.get('active_version')
        bubble_versions = [key.replace('bubble_version:', '') for key in overview_data if key.startswith('bubble_version:') and overview_data[key] == 'true']
        versions = []
        if active_ver:
            versions.append(active_ver)
        versions.extend(bubble_versions)
        return sorted(versions, key=lambda v: v)

    def _show_version_details(self, data: Dict):
        """
        (FIXED) Displays detailed information from a pre-loaded dictionary of package
        instance data, with correct lookup instructions for both Redis and SQLite.
        """
        package_name = data.get('Name')
        version = data.get('Version')
        cache_key = data.get('redis_key', '(unknown key)')

        if not package_name or not version:
            safe_print(_('❌ Cannot display details: package name or version not found in the provided data.'))
            return

        # Determine which cache backend is being used
        is_using_redis = self._cache_connection_status == 'redis_ok'
        
        if is_using_redis:
            safe_print(_('The data is from Redis key: {}').format(cache_key))
        else:
            safe_print(_('The data is from SQLite cache (key: {})').format(cache_key))

        # --- CORRECTED DICTIONARY KEYS (NO STRAY SPACES) ---
        important_fields = [
            ('Name', '📦 Package'), ('Version', '🏷️  Version'), ('install_type', 'Status'),
            ('owner_package', 'Owner'), ('Summary', '📝 Summary'), ('Author', '👤 Author'),
            ('Author-email', '📧 Email'), ('License', '⚖️  License'), ('Home-page', '🌐 Homepage'),
            ('path', '📂 Path'), ('Platform', '💻 Platform'), ('dependencies', '🔗 Dependencies'),
            ('Requires-Dist', '📋 Requires')
        ]

        for field_name, display_name in important_fields:
            if field_name in data:
                value = data.get(field_name)
                if field_name == 'License' and value and len(value) > 100:
                    value = value.split('\n')[0] + '... (truncated)'
                # --- ADD THIS ELIF BLOCK ---
                elif field_name == 'Description' and value and len(value) > 200:
                    value = value[:200].replace('\n', ' ') + '... (truncated)'
                # --- END OF CHANGE ---
                elif field_name in ['dependencies', 'Requires-Dist'] and value:
                    try:
                        dep_list = json.loads(value)
                        safe_print(_('{}: {}').format(display_name.ljust(18), ', '.join(dep_list) if dep_list else 'None'))
                    except (json.JSONDecodeError, TypeError):
                        safe_print(_('{}: {}').format(display_name.ljust(18), value))
                else:
                    safe_print(_('{}: {}').format(display_name.ljust(18), value or 'N/A'))

        security_fields = [
            ('security.issues_found', '🔒 Security Issues'), ('security.audit_status', '🛡️  Audit Status'),
            ('health.import_check.importable', '✅ Importable')
        ]
        safe_print(_('\n---[ Health & Security ]---'))
        for field_name, display_name in security_fields:
            value = data.get(field_name, 'N/A')
            safe_print(_('   {}: {}').format(display_name.ljust(18), value))

        meta_fields = [
            ('last_indexed', '⏰ Last Indexed'), ('installation_hash', '🔐 Checksum'),
            ('Metadata-Version', '📋 Metadata Version')
        ]
        safe_print(_('\n---[ Build Info ]---'))
        for field_name, display_name in meta_fields:
            value = data.get(field_name, 'N/A')
            if field_name == 'installation_hash' and value and len(value) > 24:
                value = f'{value[:12]}...{value[-12:]}'
            safe_print(_('   {}: {}').format(display_name.ljust(18), value))

        # Show how to query the raw data based on cache backend
        safe_print(_('\n💡 For all raw data:'))
        if is_using_redis:
            safe_print(_('   # Option 1: Raw key-value pairs'))
            safe_print(f'   redis-cli HGETALL "{cache_key}"')
            safe_print(_('\n   # Option 2: Formatted table view (copy-paste this command)'))
            
            # This Lua script fetches the data, sorts it, finds the max key width, and prints a padded table.
            lua_script = (
                "local data = redis.call('HGETALL', KEYS[1]); "
                "local result = {}; "
                "local max_key_len = 0; "
                "for i = 1, #data, 2 do "
                "  if string.len(data[i]) > max_key_len then max_key_len = string.len(data[i]); end; "
                "  table.insert(result, {key=data[i], value=data[i+1]}); "
                "end; "
                "table.sort(result, function(a,b) return a.key < b.key end); "
                "local output = ''; "
                "for _, pair in ipairs(result) do "
                "  local val = pair.value; "
                "  if string.len(val) > 100 then val = string.sub(val, 1, 100) .. '...'; end; "
                "  output = output .. string.format('%-' .. max_key_len + 4 .. 's%s\\n', pair.key, val); "
                "end; "
                "return output"
            )
            
            # We escape the script for safe inclusion in a shell command.
            escaped_lua_script = lua_script.replace('"', '\\"').replace("'", "\\'")
            
            redis_table_command = f"redis-cli --no-raw EVAL \"{escaped_lua_script}\" 1 \"{cache_key}\""
            safe_print(f"   {redis_table_command}")
            
        else: # This is the SQLite block
            db_path = self.config_manager.config_dir / f'cache_{self.env_id}.sqlite'
            safe_print(_('   # View field names and previews (recommended):'))
            safe_print(_('   sqlite3 -column -header {} "SELECT field, SUBSTR(value, 1, 80) || CASE WHEN LENGTH(value) > 80 THEN \'...\' ELSE \'\' END as value FROM hash_store WHERE key = \'{}\' ORDER BY field;"').format(db_path, cache_key))
            safe_print(_('   # View specific field (e.g., Summary):'))
            safe_print(_('   sqlite3 {} "SELECT value FROM hash_store WHERE key = \'{}\' AND field = \'Summary\';"').format(db_path, cache_key))

    def _save_last_known_good_snapshot(self):
        """Saves the current environment state to Redis."""
        safe_print(_("📸 Saving snapshot of the current environment as 'last known good'..."))
        try:
            current_state = self.get_installed_packages(live=True)
            snapshot_key = f'{self.redis_key_prefix}snapshot:last_known_good'
            self.cache_client.set(snapshot_key, json.dumps(current_state))
            safe_print(_('   ✅ Snapshot saved.'))
        except Exception as e:
            safe_print(_('   ⚠️ Could not save environment snapshot: {}').format(e))

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
        
        CRITICAL FIX: Always refreshes the interpreter registry cache after adoption.
        """
        safe_print(_('🐍 Attempting to adopt Python {} into the environment...').format(version))
        
        # Check if already adopted
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        
        if version in managed_interpreters:
            safe_print(_('   - ✅ Python {} is already adopted and managed.').format(version))
            return 0
        
        discovered_pythons = self.config_manager.list_available_pythons()
        source_path_str = discovered_pythons.get(version)
        
        if not source_path_str:
            safe_print(_('   - No local Python {} found. Falling back to download strategy.').format(version))
            # Download will handle registration internally
            result = self._fallback_to_download(version)
            
            # CRITICAL: Force registry refresh after download
            if result == 0:
                self.interpreter_manager.refresh_registry()
                safe_print(_('   - ✅ Successfully adopted Python {}.').format(version))
            
            return result
        
        source_exe_path = Path(source_path_str)
        
        try:
            cmd = [str(source_exe_path), '-c', 'import sys; print(sys.prefix)']
            cmd_result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            source_root = Path(os.path.realpath(cmd_result.stdout.strip()))
            current_venv_root = self.config_manager.venv_path.resolve()
            
            if (self._is_same_or_child_path(source_root, current_venv_root) or 
                not self._is_valid_python_installation(source_root, source_exe_path) or 
                self._estimate_directory_size(source_root) > 2 * 1024 * 1024 * 1024 or 
                self._is_system_critical_path(source_root)):
                
                safe_print(_('   - ⚠️  Safety checks failed for local copy. Falling back to download.'))
                result = self._fallback_to_download(version)
                
                # CRITICAL: Force registry refresh after download
                if result == 0:
                    self.interpreter_manager.refresh_registry()
                    safe_print(_('   - ✅ Successfully adopted Python {}.').format(version))
                
                return result
            
            dest_root = self.config_manager.venv_path / '.omnipkg' / 'interpreters' / f'cpython-{version}'
            
            if dest_root.exists():
                safe_print(_('   - ✅ Adopted copy of Python {} already exists.').format(version))
                return 0
            
            safe_print(_('   - Starting safe copy operation...'))
            result = self._perform_safe_copy(source_root, dest_root, version)
            
            if result == 0:
                # Copy doesn't auto-register, so we need to rescan
                safe_print(_('🔧 Forcing rescan to register the copied interpreter...'))
                self.rescan_interpreters()
                
                # CRITICAL: Force registry refresh after rescan
                self.interpreter_manager.refresh_registry()
                safe_print(_('   - ✅ Successfully adopted Python {}.').format(version))
            
            return result
            
        except Exception as e:
            safe_print(_('   - ❌ An error occurred during the copy attempt: {}. Falling back to download.').format(e))
            result = self._fallback_to_download(version)
            
            # CRITICAL: Force registry refresh after download
            if result == 0:
                self.interpreter_manager.refresh_registry()
                safe_print(_('   - ✅ Successfully adopted Python {}.').format(version))
            
            return result

    def _is_interpreter_directory_valid(self, path: Path) -> bool:
        """
        Checks if a directory contains a valid, runnable Python interpreter structure.
        This is the core of the integrity check.
        """
        if not path.exists():
            return False
        bin_dir = path / 'bin'
        if bin_dir.is_dir():
            for name in ['python3.14', 'python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3.9', 'python3.8', 'python3', 'python', 'python.exe']:
                exe_path = bin_dir / name
                if exe_path.is_file() and os.access(exe_path, os.X_OK):
                    try:
                        result = subprocess.run([str(exe_path), '--version'], capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            return True
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                        continue
        scripts_dir = path / 'Scripts'
        if scripts_dir.is_dir():
            exe_path = scripts_dir / 'python.exe'
            if exe_path.is_file():
                try:
                    result = subprocess.run([str(exe_path), '--version'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return True
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                    pass
        for name in ['python', 'python.exe', 'python3', 'python3.exe']:
            exe_path = path / name
            if exe_path.is_file() and os.access(exe_path, os.X_OK):
                try:
                    result = subprocess.run([str(exe_path), '--version'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return True
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                    continue
        return False

    def _fallback_to_download(self, version: str) -> int:
        """
        Fallback to downloading Python. This function now surgically detects an incomplete
        installation by checking for a valid executable, cleans it up if broken,
        and includes a safety stop to prevent deleting the active interpreter.
        
        CRITICAL FIX: Always uses the REAL venv root, not nested paths.
        """
        safe_print(_('\n--- Running robust download strategy ---'))
        try:
            full_versions = {
                '3.7': '3.7.9',
                '3.8': '3.8.20',
                '3.9': '3.9.23',
                '3.10': '3.10.18',
                '3.11': '3.11.9',
                '3.12': '3.12.11',
                '3.13': '3.13.7',
                '3.14': '3.14.0'
            }
            
            full_version = full_versions.get(version)
            if not full_version:
                safe_print(f'❌ Error: No known standalone build for Python {version}.')
                safe_print(_('   Available versions: {}').format(', '.join(full_versions.keys())))
                return 1
            
            # CRITICAL: Force recalculation of venv_path to ensure we're not using a nested path
            # This prevents installing Python 3.13 inside Python 3.14's directory
            real_venv_path = self.config_manager._get_venv_root()
            
            # Double-check: if the venv_path contains .omnipkg, something is wrong
            if '.omnipkg' in str(real_venv_path):
                safe_print(_('   - ⚠️  WARNING: Detected nested path in venv_path!'))
                safe_print(_('   - Current venv_path: {}').format(real_venv_path))
                
                # Extract the real root by taking everything before first .omnipkg
                path_str = str(real_venv_path).replace('\\', '/')
                parts = path_str.split('/.omnipkg/')
                if len(parts) >= 2:
                    real_venv_path = Path(parts[0])
                    safe_print(_('   - Corrected to: {}').format(real_venv_path))
            
            dest_path = real_venv_path / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'
            
            safe_print(_('   - Target installation path: {}').format(dest_path))
            
            if dest_path.exists():
                safe_print(_('   - Found existing directory for Python {}. Verifying integrity...').format(full_version))
                if self._is_interpreter_directory_valid(dest_path):
                    safe_print(_('   - ✅ Integrity check passed. Installation is valid and complete.'))
                    return 0
                else:
                    safe_print(_('   - ⚠️  Integrity check failed: Incomplete installation detected (missing or broken executable).'))
                    
                    # Safety check: don't delete the currently active interpreter
                    try:
                        active_interpreter_root = Path(sys.executable).resolve().parents[1]
                        if dest_path.resolve() == active_interpreter_root:
                            safe_print(_('   - ❌ CRITICAL ERROR: The broken interpreter is the currently active one!'))
                            safe_print(_('   - Aborting to prevent self-destruction. Please fix the environment manually.'))
                            return 1
                    except (IndexError, OSError):
                        pass
                    
                    safe_print(_('   - Preparing to clean up broken directory...'))
                    try:
                        shutil.rmtree(dest_path)
                        safe_print(_('   - ✅ Removed broken directory successfully.'))
                    except Exception as e:
                        safe_print(_('   - ❌ FATAL: Failed to remove existing broken directory: {}').format(e))
                        return 1
            
            safe_print(_('   - Starting fresh download and installation...'))
            download_success = False
            
            # Try Python 3.13 alternative download method
            if version == '3.13':
                safe_print(_('   - Using python-build-standalone for Python 3.13...'))
                download_success = self._download_python_313_alternative(dest_path, full_version)
            
            # Fallback to other download methods
            if not download_success:
                if hasattr(self.config_manager, '_install_managed_python'):
                    try:
                        self.config_manager._install_managed_python(real_venv_path, full_version, omnipkg_instance=self)
                        download_success = True
                    except Exception as e:
                        safe_print(_('   - Warning: _install_managed_python failed: {}').format(e))
                elif hasattr(self.config_manager, 'install_managed_python'):
                    try:
                        self.config_manager.install_managed_python(real_venv_path, full_version)
                        download_success = True
                    except Exception as e:
                        safe_print(_('   - Warning: install_managed_python failed: {}').format(e))
                elif hasattr(self.config_manager, 'download_python'):
                    try:
                        self.config_manager.download_python(full_version)
                        download_success = True
                    except Exception as e:
                        safe_print(_('   - Warning: download_python failed: {}').format(e))
            
            if not download_success:
                safe_print(_('❌ Error: All download methods failed for Python {}').format(full_version))
                return 1
            
            # Verify installation
            if dest_path.exists() and self._is_interpreter_directory_valid(dest_path):
                safe_print(_('   - ✅ Download and installation completed successfully.'))
                self.config_manager._set_rebuild_flag_for_version(version)
                return 0
            else:
                safe_print(_('   - ❌ Installation completed but integrity check still fails.'))
                return 1
                
        except Exception as e:
            safe_print(_('❌ Download and installation process failed: {}').format(e))
            import traceback
            traceback.print_exc()
            return 1

    
# Add this at the START of _download_python_313_alternative, 
# right after the function signature:

    def _download_python_313_alternative(self, dest_path: Path, full_version: str) -> bool:
        """
        Alternative download method specifically for Python 3.13 using python-build-standalone releases.
        Downloads from the December 5, 2024 release builds.
        """
        import urllib.request
        import tarfile
        import platform
        import tempfile
        import shutil
        
        # CRITICAL FIX: Verify dest_path doesn't contain nested .omnipkg
        dest_path_str = str(dest_path).replace('\\', '/')
        if dest_path_str.count('/.omnipkg/') > 1:
            safe_print(_('   - ❌ CRITICAL: Detected nested .omnipkg path!'))
            safe_print(_('   - Bad path: {}').format(dest_path))
            
            # Extract the real root
            parts = dest_path_str.split('/.omnipkg/')
            if len(parts) >= 2:
                # Reconstruct path with only ONE .omnipkg
                real_venv = Path(parts[0])
                dest_path = real_venv / '.omnipkg' / 'interpreters' / f'cpython-{full_version}'
                safe_print(_('   - Corrected to: {}').format(dest_path))
        
        try:
            safe_print(_('   - Attempting Python 3.13 download from python-build-standalone...'))
            system = platform.system().lower()
            machine = platform.machine().lower()
            base_url = 'https://github.com/indygreg/python-build-standalone/releases/download/20241205/'
            build_filename = None
            if system == 'windows':
                if '64' in machine or machine == 'amd64' or machine == 'x86_64':
                    build_filename = 'cpython-3.13.1+20241205-x86_64-pc-windows-msvc-install_only.tar.gz'
                else:
                    build_filename = 'cpython-3.13.1+20241205-i686-pc-windows-msvc-install_only.tar.gz'
            elif system == 'darwin':
                if 'arm' in machine or 'm1' in machine.lower() or 'arm64' in machine:
                    build_filename = 'cpython-3.13.1+20241205-aarch64-apple-darwin-install_only.tar.gz'
                else:
                    build_filename = 'cpython-3.13.1+20241205-x86_64-apple-darwin-install_only.tar.gz'
            elif system == 'linux':
                if 'aarch64' in machine or 'arm64' in machine:
                    build_filename = 'cpython-3.13.1+20241205-aarch64-unknown-linux-gnu-install_only.tar.gz'
                elif 'arm' in machine:
                    if 'hf' in machine or platform.processor().find('hard') != -1:
                        build_filename = 'cpython-3.13.1+20241205-armv7-unknown-linux-gnueabihf-install_only.tar.gz'
                    else:
                        build_filename = 'cpython-3.13.1+20241205-armv7-unknown-linux-gnueabi-install_only.tar.gz'
                elif 'ppc64le' in machine:
                    build_filename = 'cpython-3.13.1+20241205-ppc64le-unknown-linux-gnu-install_only.tar.gz'
                elif 's390x' in machine:
                    build_filename = 'cpython-3.13.1+20241205-s390x-unknown-linux-gnu-install_only.tar.gz'
                elif 'x86_64' in machine or 'amd64' in machine:
                    try:
                        import subprocess
                        result = subprocess.run(['ldd', '--version'], capture_output=True, text=True, timeout=5)
                        if 'musl' in result.stderr.lower():
                            build_filename = 'cpython-3.13.1+20241205-x86_64-unknown-linux-musl-install_only.tar.gz'
                        else:
                            build_filename = 'cpython-3.13.1+20241205-x86_64-unknown-linux-gnu-install_only.tar.gz'
                    except:
                        build_filename = 'cpython-3.13.1+20241205-x86_64-unknown-linux-gnu-install_only.tar.gz'
                elif 'i686' in machine or 'i386' in machine:
                    build_filename = 'cpython-3.13.1+20241205-i686-unknown-linux-gnu-install_only.tar.gz'
                else:
                    build_filename = 'cpython-3.13.1+20241205-x86_64-unknown-linux-gnu-install_only.tar.gz'
            if not build_filename:
                safe_print(_('   - ❌ Could not determine appropriate build for platform: {} {}').format(system, machine))
                return False
            download_url = base_url + build_filename
            safe_print(_('   - Selected build: {}').format(build_filename))
            safe_print(_('   - Downloading from: {}').format(download_url))
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz') as temp_file:
                temp_path = Path(temp_file.name)
            try:

                def show_progress(block_num, block_size, total_size):
                    if total_size > 0:
                        percent = min(100, block_num * block_size * 100 // total_size)
                        if block_num % 100 == 0 or percent >= 100:
                            safe_print(_('   - Download progress: {}%').format(percent), end='\r')
                urllib.request.urlretrieve(download_url, temp_path, reporthook=show_progress)
                safe_print(_('\n   - Download completed, extracting...'))
                with tarfile.open(temp_path, 'r:gz') as tar_ref:
                    with tempfile.TemporaryDirectory() as temp_extract_dir:
                        tar_ref.extractall(temp_extract_dir)
                        extracted_items = list(Path(temp_extract_dir).iterdir())
                        if len(extracted_items) == 1 and extracted_items[0].is_dir():
                            extracted_dir = extracted_items[0]
                            if dest_path.exists():
                                shutil.rmtree(dest_path)
                            shutil.move(str(extracted_dir), str(dest_path))
                        else:
                            dest_path.mkdir(parents=True, exist_ok=True)
                            for item in extracted_items:
                                dest_item = dest_path / item.name
                                if dest_item.exists():
                                    if dest_item.is_dir():
                                        shutil.rmtree(dest_item)
                                    else:
                                        dest_item.unlink()
                                shutil.move(str(item), str(dest_item))
                safe_print(_('   - Extraction completed'))
                if system in ['linux', 'darwin']:
                    python_exe = dest_path / 'bin' / 'python3'
                    if python_exe.exists():
                        python_exe.chmod(493)
                        python_versioned = dest_path / 'bin' / 'python3.13'
                        if python_versioned.exists():
                            python_versioned.chmod(493)
                safe_print(_('   - ✅ Python 3.13.1 installation completed successfully'))
                safe_print(_('   - Bootstrapping the new Python 3.13 environment...'))
                python_exe = self._find_python_executable_in_dir(dest_path)
                if not python_exe:
                    safe_print(_('   - ❌ CRITICAL: Could not find Python executable in {} after extraction.').format(dest_path))
                    return False
                self._install_essential_packages(python_exe)
        
                safe_print(_('   - ✅ Alternative Python 3.13 download and bootstrap completed'))
                return True
            finally:
                temp_path.unlink(missing_ok=True)
        except Exception as e:
            safe_print(_('   - ❌ Python 3.13 download failed: {}').format(e))
            import traceback
            safe_print(_('   - Error details: {}').format(traceback.format_exc()))
            return False

    def rescan_interpreters(self) -> int:
        """
        Forces a full, clean re-scan of the managed interpreters directory
        and rebuilds the registry from scratch. This is a repair utility.
        """
        safe_print(_('Performing a full re-scan of managed interpreters...'))
        try:
            # This line rewrites the registry.json file on disk.
            self.config_manager._register_all_interpreters(self.config_manager.venv_path)

            # *** THE FIX: After rewriting the file, invalidate the current process's cache. ***
            self.interpreter_manager.refresh_registry()

            safe_print(_('\n✅ Interpreter registry successfully rebuilt.'))
            return 0
        except Exception as e:
            safe_print(_('\n❌ An error occurred during the re-scan: {}').format(e))
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
            
            safe_print(_('   - Copying {} -> {}').format(source, dest))
            shutil.copytree(source, dest, symlinks=True, ignore=ignore_patterns, dirs_exist_ok=False)
            
            copied_python = self._find_python_executable_in_dir(dest)
            if not copied_python or not copied_python.exists():
                safe_print(_('   - ❌ Copy completed but Python executable not found in destination'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            
            test_cmd = [str(copied_python), '-c', 'import sys; print(sys.version)']
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=10)
            if test_result.returncode != 0:
                safe_print(_('   - ❌ Copied Python executable failed basic test'))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            
            safe_print(_('   - ✅ Copy successful and verified!'))
            
            # 🔥 CRITICAL FIX: Bootstrap omnipkg into the copied interpreter
            safe_print(_('   - Bootstrapping omnipkg into copied interpreter...'))
            try:
                self._install_essential_packages(copied_python)
            except Exception as e:
                safe_print(_('   - ⚠️ Bootstrap failed: {}. Trying download fallback...').format(e))
                shutil.rmtree(dest, ignore_errors=True)
                return self._fallback_to_download(version)
            
            self.config_manager._register_all_interpreters(self.config_manager.venv_path)
            
            safe_print(f'\n🎉 Successfully adopted Python {version} from local source!')
            safe_print(_("   You can now use 'omnipkg swap python {}'").format(version))
            return 0
            
        except Exception as e:
            safe_print(_('   - ❌ Copy operation failed: {}').format(e))
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
        py_ver_str = f'py{version}'
        base_prefix = self.config.get('redis_key_prefix', 'omnipkg:pkg:')
        base = base_prefix.split(':')[0]
        return f'{base}:env_{self.config_manager.env_id}:{py_ver_str}:pkg:'

    def remove_interpreter(self, version: str, force: bool=False) -> int:
        """
        Forcefully removes a managed Python interpreter directory, purges its
        knowledge base from Redis, and updates the registry.
        
        SAFETY: This will NEVER remove native interpreters (conda/system Python).
        """
        safe_print(_('🔥 Attempting to remove managed Python interpreter: {}').format(version))
        
        # SAFETY CHECK 1: Cannot remove currently active interpreter
        configured_python = self.config_manager.get('python_executable')
        if configured_python:
            import re
            version_match = re.search(r'python(\d+\.\d+)', str(configured_python))
            if version_match:
                active_python_version = version_match.group(1)
            else:
                active_python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
        else:
            active_python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
        
        if version == active_python_version:
            safe_print(_('❌ SAFETY LOCK: Cannot remove the currently active Python interpreter ({}).').format(version))
            safe_print(_("   Switch to a different Python version first using 'omnipkg swap python <other_version>'."))
            return 1
        
        # Get interpreter info from registry
        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        interpreter_path = managed_interpreters.get(version)
        
        if not interpreter_path:
            safe_print(_('🤷 Error: Python version {} is not a known managed interpreter.').format(version))
            return 1
        
        # === CRITICAL SAFETY CHECK 2: Check if this is a native interpreter ===
        interpreter_path_obj = Path(interpreter_path)
        managed_interpreters_dir = self.config_manager.venv_path / '.omnipkg' / 'interpreters'
        
        is_native = not str(interpreter_path_obj).startswith(str(managed_interpreters_dir))
        
        if is_native:
            safe_print(_('❌ SAFETY LOCK: Cannot remove native Python interpreter ({}).').format(version))
            safe_print(_('   This is your original conda/system Python installation.'))
            safe_print(_('   Only downloaded/managed interpreters can be removed.'))
            safe_print(_('   Native interpreter path: {}').format(interpreter_path))
            return 1
        # === END SAFETY CHECK ===
        
        interpreter_root_dir = interpreter_path_obj.parent.parent
        safe_print(f'   Target directory for deletion: {interpreter_root_dir}')
        
        # SAFETY CHECK 3: Verify directory is inside managed interpreters directory
        if not str(interpreter_root_dir).startswith(str(managed_interpreters_dir)):
            safe_print(_('❌ SAFETY LOCK: Refusing to delete directory outside managed interpreters area.'))
            safe_print(_('   Expected location: {}').format(managed_interpreters_dir))
            safe_print(_('   Attempted location: {}').format(interpreter_root_dir))
            return 1
        
        if not interpreter_root_dir.exists():
            safe_print(_('   Directory does not exist. It may have already been cleaned up.'))
            self.rescan_interpreters()
            return 0
        
        # Confirmation prompt
        if not force:
            safe_print(_('⚠️  This will permanently delete the managed interpreter at:'))
            safe_print(f'   {interpreter_root_dir}')
            confirm = input(_('🤔 Are you sure you want to continue? (y/N): ')).lower().strip()
            if confirm != 'y':
                safe_print(_('🚫 Removal cancelled.'))
                return 1
        
        # Perform deletion
        try:
            safe_print(_('🗑️ Deleting directory: {}').format(interpreter_root_dir))
            shutil.rmtree(interpreter_root_dir)
            safe_print(_('✅ Directory removed successfully.'))
        except Exception as e:
            safe_print(_('❌ Failed to remove directory: {}').format(e))
            return 1
        
        # Clean up knowledge base (ONLY if cache is available)
        # ✅ FIX: Check if we have a cache_client before trying to use it
        if hasattr(self, 'cache_client') and self.cache_client is not None:
            safe_print(f'🧹 Cleaning up Knowledge Base for Python {version}...')
            try:
                keys_to_delete_pattern = self._get_redis_key_prefix_for_version(version) + '*'
                keys = self.cache_client.keys(keys_to_delete_pattern)
                if keys:
                    safe_print(_('   -> Found {} stale entries in cache. Purging...').format(len(keys)))
                    delete_command = self.cache_client.unlink if hasattr(self.cache_client, 'unlink') else self.cache_client.delete
                    delete_command(*keys)
                    safe_print(f'   ✅ Knowledge Base for Python {version} has been purged.')
                else:
                    safe_print(f'   ✅ No Knowledge Base entries found for Python {version}. Nothing to clean.')
            except Exception as e:
                safe_print(f'   ⚠️  Warning: Could not clean up Knowledge Base for Python {version}: {e}')
        else:
            safe_print(f'   ℹ️  Skipping Knowledge Base cleanup (cache not initialized in minimal mode)')
        
        # Update registry
        safe_print(_('🔧 Rescanning interpreters to update the registry...'))
        self.rescan_interpreters()
        
        safe_print(_('✨ Python {} has been successfully removed from the environment.').format(version))
        return 0
    
    def check_package_installed_fast(self, python_exe: str, package: str, version: str) -> Tuple[Optional[str], float]:
        """
        (REWRITTEN & ROBUST) Checks if a specific package version is already installed.
        Uses DIRECT filesystem checks for maximum reliability and speed.

        Returns:
            A tuple of (status, duration_ms) where status is one of:
            - 'active': The exact version is installed in the main environment.
            - 'bubble': The exact version exists as a bubble.
            - None: The package is not satisfied.
        """
        start_time = time.perf_counter()
        
        # --- Phase 1: Check main environment via direct dist-info inspection ---
        try:
            # Get site-packages directory from the target python_exe
            cmd = [python_exe, '-c', 'import site; print(site.getsitepackages())']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True)
            
            import ast
            site_packages_paths = [Path(p) for p in ast.literal_eval(result.stdout.strip())]
            
            pkg_name_normalized = package.replace('-', '_').lower()

            for site_packages in site_packages_paths:
                if not site_packages.is_dir():
                    continue

                # Use glob for case-insensitivity and name variations
                for dist_info in site_packages.glob(f'{pkg_name_normalized}-{version}.dist-info'):
                    if dist_info.is_dir():
                        # We found a potential match. Verify version from METADATA.
                        metadata_file = dist_info / 'METADATA'
                        if metadata_file.exists():
                            try:
                                with open(metadata_file, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        if line.lower().startswith('version:'):
                                            found_version = line.split(':', 1)[1].strip()
                                            if found_version == version:
                                                duration_ms = (time.perf_counter() - start_time) * 1000
                                                return 'active', duration_ms # ✅ IT'S ACTIVE
                            except Exception:
                                pass # Corrupted metadata, treat as not found.
        except Exception:
            # If the subprocess fails for any reason, we can't confirm it's active.
            pass

        # --- Phase 2: If not active, check if a valid bubble exists ---
        bubble_path = self.multiversion_base / f'{package}-{version}'
        if bubble_path.is_dir():
            # Check for the existence of the manifest file for a quick, reliable check.
            if (bubble_path / '.omnipkg_manifest.json').exists():
                duration_ms = (time.perf_counter() - start_time) * 1000
                return 'bubble', duration_ms # ✅ IT'S IN A BUBBLE

        # --- Phase 3: Not satisfied ---
        duration_ms = (time.perf_counter() - start_time) * 1000
        return None, duration_ms
    
    def _check_package_exists_on_pypi(self, package_name: str) -> bool:
        """
        Performs a fast, lightweight check to see if a package exists on PyPI.
        Returns True if it exists, False otherwise.
        """
        try:
            import requests
            response = requests.head(f"https://pypi.org/pypi/{package_name}/json", timeout=10)
            return response.status_code == 200
        except Exception:
            return False
    
    def _resolve_spec_with_pip(self, package_spec: str) -> Tuple[Optional[str], str]:
        """
        (V5 - UNIVERSAL PIP COMPATIBILITY) Works with pip from the Stone Age to modern times.
        Detects ancient pip and falls back to `pip download` which has existed forever.
        """
        try:
            # First try modern approach with --dry-run --report
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--dry-run', '--ignore-installed', '--no-deps', '--report', '-', package_spec]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            
            pip_report_str = result.stdout.strip()
            pip_user_error_output = result.stderr.strip()

            # Check if this failed because of ancient pip
            is_ancient_pip = "no such option: --dry-run" in pip_user_error_output
            
            if is_ancient_pip:
                safe_print(f"   ⏳ Ancient pip detected - falling back to `pip download` method...")
                # This call can now RAISE the error, which will be caught below
                return self._resolve_spec_with_ancient_pip(package_spec)
            
            if result.returncode == 0 and pip_report_str:
                json_start = pip_report_str.find('{')
                if json_start == -1: return None, pip_user_error_output
                json_end = pip_report_str.rfind('}')
                if json_end == -1 or json_end < json_start: return None, pip_user_error_output
                json_str = pip_report_str[json_start:json_end + 1]
                
                try:
                    report = json.loads(json_str)
                    install_plan = report.get('install', [])
                    if not install_plan: return None, pip_user_error_output
                    req_name = self._parse_package_spec(package_spec)[0]
                    
                    for item in install_plan:
                        metadata = item.get('metadata', {})
                        if metadata:
                            pkg_name = metadata.get('name')
                            version = metadata.get('version')
                            if pkg_name and version and canonicalize_name(pkg_name) == canonicalize_name(req_name):
                                return f"{pkg_name}=={version}", pip_user_error_output
                    return None, pip_user_error_output
                except json.JSONDecodeError as e:
                    safe_print(f"   ⚠️  Failed to parse pip JSON: {e}")
                    safe_print(f"   ⚠️  Extracted JSON (first 500 chars): {json_str[:500]}")
                    return None, pip_user_error_output

            is_no_match_error = "Could not find a version" in pip_user_error_output or "No matching distribution" in pip_user_error_output
        
            if is_no_match_error:
                pkg_name_to_check, requested_version = self._parse_package_spec(package_spec)
                try:
                    response = http_requests.get(f'https://pypi.org/pypi/{pkg_name_to_check}/json', timeout=10)
                    if response.status_code == 200:
                        pypi_data = response.json()
                        version_to_check = requested_version or pypi_data['info']['version']
                        compatible_py = self._find_compatible_python_version(pkg_name_to_check, version_to_check)
                        safe_print(f"   💡 Pip validation failed for '{pkg_name_to_check}', but package exists on PyPI.")
                        safe_print(f"      - Target Version: {version_to_check}, Requires Python: {compatible_py or 'unknown'}")
                        
                        raise NoCompatiblePythonError(
                            package_name=pkg_name_to_check,
                            package_version=version_to_check,
                            current_python=self.current_python_context.replace('py', ''),
                            compatible_python=compatible_py
                        )
                except Exception:
                    pass
            
            return None, pip_user_error_output

        except NoCompatiblePythonError:
            # CATCH AND RE-RAISE the specific error to prevent it from being swallowed.
            # This is the crucial fix.
            raise
        except Exception as e:
            return None, f"An unexpected error occurred during pip resolution: {e}"

    def _resolve_spec_with_ancient_pip(self, package_spec: str) -> Tuple[Optional[str], str]:
        """
        (AUTHORITATIVE FIX) Fallback for ancient pip. When `pip download` fails,
        it intelligently parses the error and RAISES NoCompatiblePythonError if
        the package exists but is incompatible.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            cmd = [
                self.config['python_executable'], '-m', 'pip', 'download',
                '--no-deps', '--no-cache-dir', '--dest', temp_dir, package_spec
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)

            # --- Success Path ---
            if result.returncode == 0:
                downloaded_files = list(Path(temp_dir).glob('*'))
                if downloaded_files:
                    version = self._extract_version_from_filename(downloaded_files[0].name, package_spec)
                    if version:
                        return f"{self._parse_package_spec(package_spec)[0]}=={version}", ""

            # --- FAILURE PATH: THIS IS THE FIX ---
            pip_output = result.stderr.strip()
            is_no_match_error = "could not find a version" in pip_output.lower() or \
                                "no matching distribution" in pip_output.lower()

            if is_no_match_error:
                pkg_name, requested_version = self._parse_package_spec(package_spec)
                
                # Check if the package *exists* on PyPI. If so, it's an incompatibility.
                if self._check_package_exists_on_pypi(pkg_name):
                    # It exists. Find out what Python it needs.
                    compatible_py = self._find_compatible_python_version(pkg_name, target_package_version=requested_version)
                    
                    # RAISE THE EXCEPTION to be caught by smart_install.
                    raise NoCompatiblePythonError(
                        package_name=pkg_name,
                        package_version=requested_version,
                        current_python=self.current_python_context.replace('py',''),
                        compatible_python=compatible_py
                    )

            # If we get here, it was a real failure (e.g., package doesn't exist), not an incompatibility.
            return None, pip_output
            
        except NoCompatiblePythonError:
            raise # Let smart_install catch this.
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            
    def _extract_version_from_filename(self, filename: str, package_spec: str) -> Optional[str]:
        """
        Extract version from various pip download filename formats across pip eras.
        """
        pkg_name = self._parse_package_spec(package_spec)[0]
        
        # Pattern 1: Modern wheels - rich-13.8.1-py3-none-any.whl
        wheel_pattern = rf"{re.escape(pkg_name)}-([\d\.]+(?:[a-z]+)?[\d]*)-\w+-\w+-\w+\.whl"
        match = re.match(wheel_pattern, filename, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Pattern 2: Source distributions - rich-13.8.1.tar.gz
        sdist_pattern = rf"{re.escape(pkg_name)}-([\d\.]+(?:[a-z]+)?[\d]*)\.(?:tar\.gz|zip)"
        match = re.match(sdist_pattern, filename, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Pattern 3: Ancient packages - Django-1.0-final.tar.gz
        ancient_pattern = rf"{re.escape(pkg_name)}-([\d\.]+(?:-\w+)?)\.(?:tar\.gz|zip)"
        match = re.match(ancient_pattern, filename, re.IGNORECASE)
        if match:
            version = match.group(1)
            # Normalize "1.0-final" to "1.0"
            return re.sub(r'-\w+$', '', version)
        
        return None

    def smart_install(self, packages: List[str], dry_run: bool = False, force_reinstall: bool = False, 
                  override_strategy: Optional[str] = None, target_directory: Optional[Path] = None, 
                  preflight_compatibility_cache: Optional[Dict] = None) -> int:
    
        # ====================================================================
        # ULTRA-FAST PREFLIGHT CHECK (Before any heavy initialization)
        # ====================================================================
        if not force_reinstall and packages:
            safe_print('⚡ Running ultra-fast preflight check...')
        
            preflight_start = time.perf_counter()
            configured_exe = self.config.get('python_executable', sys.executable)
            install_strategy = self.config.get('install_strategy', 'stable-main')
            
            fully_resolved_specs = []
            needs_installation = []
            
            # Define complex specifier characters
            complex_spec_chars = ['<', '>', '~', '!', ',']
            
            # --- Phase 1: Resolve versions and check if already satisfied ---
            for pkg_spec in packages:
                pkg_name, version = self._parse_package_spec(pkg_spec)
                
                # Step 1: Get the version (either from spec or from cache/PyPI)
                if version:
                    # Already has explicit version (e.g., numpy==1.26.4)
                    resolved_spec = pkg_spec
                else:
                    # Check if it's a complex specifier BEFORE calling _get_latest_version_from_pypi
                    is_complex_spec = any(op in pkg_spec for op in complex_spec_chars)
                    
                    if is_complex_spec:
                        safe_print(f"   🔍 Detected complex specifier: '{pkg_spec}'")
                        safe_print(f"   → Resolving to exact version for Python {self.current_python_context}...")
                        
                        try:
                            # Use _find_best_version_for_spec to respect the constraint
                            resolved_spec = self._find_best_version_for_spec(pkg_spec)
                            
                            if resolved_spec:
                                # Successfully resolved (e.g., "numpy<2" -> "numpy==1.26.4")
                                pkg_name, version = self._parse_package_spec(resolved_spec)
                                safe_print(f"   ✅ Resolved '{pkg_spec}' to '{resolved_spec}'")
                            else:
                                # Could not resolve - mark for pip validation
                                safe_print(f"   ⚠️  Could not resolve '{pkg_spec}'. Marked for pip fallback.")
                                needs_installation.append(pkg_spec)
                                continue
                                
                        except NoCompatiblePythonError as e:
                            # Complex specifier resolution triggered quantum healing
                            safe_print("\n" + "="*60)
                            safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected During Preflight")
                            safe_print("="*60)
                            safe_print(f"   - Diagnosis: Cannot resolve '{e.package_name}' v{e.package_version} on Python {e.current_python}.")
                            safe_print(f"   - Prescription: This package requires Python {e.compatible_python}.")
                            
                            from .cli import handle_python_requirement
                            if not e.compatible_python or e.compatible_python == "unknown":
                                safe_print(f"❌ Healing failed: Could not determine compatible Python version.")
                                return 1
            
                            if not handle_python_requirement(e.compatible_python, self, "omnipkg"):
                                safe_print(f"❌ Healing failed: Could not switch to Python {e.compatible_python}.")
                                return 1
            
                            safe_print(f"\n🚀 Retrying in new Python {e.compatible_python} context...")
                            new_config_manager = ConfigManager()
                            new_omnipkg_instance = self.__class__(new_config_manager)
            
                            # Recursively retry with the ORIGINAL packages list
                            return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, 
                                                                    override_strategy, target_directory)
                    else:
                        # Simple package name without version - get latest compatible
                        try:
                            latest_version = self._get_latest_version_from_pypi(pkg_name)
                        except NoCompatiblePythonError as e:
                            # 🔥 IMMEDIATE QUANTUM HEALING - NO CACHING!
                            safe_print("\n" + "="*60)
                            safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected During Preflight")
                            safe_print("="*60)
                            safe_print(f"   - Diagnosis: Cannot resolve '{e.package_name}' v{e.package_version} on Python {e.current_python}.")
                            safe_print(f"   - Prescription: This package requires Python {e.compatible_python}.")
                            
                            from .cli import handle_python_requirement
                            if not e.compatible_python or e.compatible_python == "unknown":
                                safe_print(f"❌ Healing failed: Could not determine compatible Python version.")
                                return 1
            
                            if not handle_python_requirement(e.compatible_python, self, "omnipkg"):
                                safe_print(f"❌ Healing failed: Could not switch to Python {e.compatible_python}.")
                                return 1
            
                            safe_print(f"\n🚀 Retrying in new Python {e.compatible_python} context...")
                            new_config_manager = ConfigManager()
                            new_omnipkg_instance = self.__class__(new_config_manager)
            
                            # Recursively retry with the ORIGINAL packages list
                            return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, 
                                                                    override_strategy, target_directory)
            
                        if latest_version:
                            resolved_spec = f"{pkg_name}=={latest_version}"
                            version = latest_version
                        else:
                            safe_print(f"   ⚠️  Fast checks could not resolve '{pkg_name}'. Marked for pip fallback.")
                            needs_installation.append(pkg_spec)
                            continue
            
                # Step 2: Check if this version is already installed
                is_installed, duration_ms = self.check_package_installed_fast(configured_exe, pkg_name, version)
                
                if is_installed:
                    bubble_path = self.multiversion_base / f'{pkg_name}-{version}'
                    is_in_bubble = bubble_path.exists() and bubble_path.is_dir()
                    
                    if not is_in_bubble:
                        safe_print(f'   ✓ {resolved_spec} [satisfied: {duration_ms:.1f}ms - active in main env]')
                        fully_resolved_specs.append(resolved_spec)
                        continue
                    elif install_strategy == 'stable-main':
                        safe_print(f'   ✓ {resolved_spec} [satisfied: {duration_ms:.1f}ms - bubble]')
                        fully_resolved_specs.append(resolved_spec)
                        continue
                    else:
                        safe_print(f'   ⚠️  {resolved_spec} found in bubble but needs to be active')
                        needs_installation.append(resolved_spec)
                        continue
                else:
                    needs_installation.append(resolved_spec)
            
            # --- Phase 2: If everything satisfied, we're done! ---
            if not needs_installation:
                total_check_time = (time.perf_counter() - preflight_start) * 1000
                safe_print(f'⚡ PREFLIGHT SUCCESS: All {len(packages)} package(s) already satisfied! ({total_check_time:.1f}ms)')
                return 0
            
            # --- Phase 3: Validate unresolved packages with pip (ONLY if needed) ---
            if needs_installation:
                safe_print(f"\n📦 {len(needs_installation)} package(s) need installation/validation")
                
                validated_specs = []
                for spec in needs_installation:
                    try:
                        pkg_name, version = self._parse_package_spec(spec)
                        
                        if not version:
                            safe_print(f"   🔍 Resolving version for '{pkg_name}' with pip...")
                            resolved_spec, pip_output = self._resolve_spec_with_pip(spec)
                        else:
                            safe_print(f"   ⚙️  Validating '{spec}' with pip...")
                            resolved_spec, pip_output = self._resolve_spec_with_pip(spec)
                        
                        if resolved_spec:
                            safe_print(f"   ✓ Pip validated '{spec}' -> '{resolved_spec}'")
                            validated_specs.append(resolved_spec)
                        else:
                            safe_print(f"\n❌ Could not find the specified version for '{pkg_name}'.")
                            safe_print("   Pip provided the following information and available versions:")
                            for line in pip_output.splitlines():
                                safe_print(f"   | {line}")
                            return 1
                            
                    except NoCompatiblePythonError as e:
                        # Quantum healing during preflight!
                        safe_print("\n" + "="*60)
                        safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected During Preflight")
                        safe_print("="*60)
                        safe_print(f"   - Diagnosis: Cannot resolve '{e.package_name}' v{e.package_version} on Python {e.current_python}.")
                        safe_print(f"   - Prescription: This package requires Python {e.compatible_python}.")
                        from .cli import handle_python_requirement
                        if not e.compatible_python or e.compatible_python == "unknown":
                            safe_print(f"❌ Healing failed: Could not determine compatible Python version.")
                            return 1

                        if not handle_python_requirement(e.compatible_python, self, "omnipkg"):
                            safe_print(f"❌ Healing failed: Could not switch to Python {e.compatible_python}.")
                            return 1

                        safe_print(f"\n🚀 Retrying in new Python {e.compatible_python} context...")
                        new_config_manager = ConfigManager()
                        new_omnipkg_instance = self.__class__(new_config_manager)

                        # Recursively retry with the ORIGINAL packages list
                        return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, 
                                                                override_strategy, target_directory)
                
                packages = validated_specs
    

        # ====================================================================
        # NORMAL INITIALIZATION (Only runs if packages need work)
        # ====================================================================
        original_strategy = None
        if override_strategy:
            original_strategy = self.config.get('install_strategy', 'stable-main')
            if original_strategy != override_strategy:
                safe_print(f'   - 🔄 Using override strategy: {override_strategy}')
                self.config['install_strategy'] = override_strategy
        install_strategy = self.config.get('install_strategy', 'stable-main')
    
        if not self._connect_cache():
            return 1
        
        # ... rest of your existing smart_install code continues here ...
        if dry_run:
            safe_print('🔬 Running in --dry-run mode. No changes will be made.')
            return 0
        if not packages:
            safe_print('🚫 No packages specified for installation.')
            return 1
        from .i18n import _  # Add this line at the top

        install_strategy = None  # ✅ Initialize at the top so it's always defined
        
        self.doctor(dry_run=False, force=True)
        self._heal_conda_environment()
        if dry_run:
            safe_print('🔬 Running in --dry-run mode. No changes will be made.')
            return 0
        if not packages:
            safe_print('🚫 No packages specified for installation.')
            return 1

        # --- UNIFIED SMART PREFLIGHT CHECK ---
        if not force_reinstall:
            safe_print('⚡ Running preflight satisfaction check...')
            preflight_start = time.perf_counter()
            configured_exe = self.config.get('python_executable', sys.executable)
            
            is_satisfied = True
            for pkg_spec in packages:
                pkg_name, version = self._parse_package_spec(pkg_spec)
                if not version: # If no version specified, we must resolve it
                    is_satisfied = False
                    break
                
                # Use the new ultra-fast check here!
                install_status, unused_duration = self.check_package_installed_fast(configured_exe, pkg_name, version)
                
                if install_status == 'active':
                    safe_print(f'✅ {pkg_spec} already satisfied (active in main env)')
                    continue
                elif install_status == 'bubble' and install_strategy == 'stable-main':
                    safe_print(f'✅ {pkg_spec} already satisfied (found as bubble)')
                    continue
                elif install_status == 'bubble' and install_strategy == 'latest-active':
                    # Bubble exists but we need it in main env - NOT satisfied
                    is_satisfied = False
                    break
                else:
                    # Not found anywhere
                    is_satisfied = False
                    break

            preflight_time = (time.perf_counter() - preflight_start) * 1000
            if is_satisfied:
                safe_print(f'✅ PREFLIGHT SUCCESS: All {len(packages)} package(s) already satisfied! ({preflight_time:.1f}ms)')
                return 0

            # --- UNIFIED SMART PREFLIGHT CHECK ---
            resolved_package_cache = {}  # Cache resolved versions to avoid duplicate PyPI calls
            main_env_kb_updates = {}
            bubbled_kb_updates = {}
            kb_deletions = set()
            any_installations_made = False
            
            all_packages_satisfied = True
            processed_packages = []
            needs_resolution = []  # Packages without version that need PyPI lookup
            needs_kb_check = []    # Packages that need full KB verification (for nested/complex cases)
            
            # Phase 1: Ultra-fast checks for packages with explicit versions
            for pkg_spec in packages:
                if '==' in pkg_spec:
                    # Package has version specified - try fast check first
                    pkg_name, version = self._parse_package_spec(pkg_spec)
                    resolved_package_cache[pkg_spec] = pkg_spec  # Cache the already-resolved spec
                    
                    
                    # Call the MODIFIED helper function
                    install_status, check_time = self.check_package_installed_fast(configured_exe, pkg_name, version)
                    
                    if install_status == 'active':
                        # ALWAYS satisfied if it's the active package in the main environment.
                        safe_print(f'✅ {pkg_spec} already satisfied (active in main env)')
                        processed_packages.append(pkg_spec)
                        continue
                        
                    elif install_status == 'bubble':
                        # If found in a bubble, satisfaction DEPENDS on the install strategy.
                        if install_strategy == 'stable-main':
                            # For stable-main, a bubble is good enough.
                            safe_print(f'✅ {pkg_spec} already satisfied (found as bubble)')
                            processed_packages.append(pkg_spec)
                            continue
                        else:
                            # For 'latest-active', a bubble is NOT good enough. We need to install.
                            # Mark as not satisfied and stop checking. The main installer will handle it.
                            all_packages_satisfied = False
                            break # Exit the loop immediately
                            
                    elif install_status is None:
                        # Not found in main env or as a bubble. Might be nested, so check the KB.
                        needs_kb_check.append(pkg_spec)
                        
                else:
                    # Package needs version resolution from PyPI
                    needs_resolution.append(pkg_spec)

            # This break is crucial. If the loop was broken, we must exit Phase 1
            
            # Phase 2: Resolve versions for packages without explicit versions
            resolved_specs = []
            if needs_resolution:
                try:
                    for pkg_spec in needs_resolution:
                        safe_print(f'  🔍 Resolving version for {pkg_spec}...')
                        try:
                            resolved = self._resolve_package_versions([pkg_spec])
                            if not resolved:
                                all_packages_satisfied = False
                                break
                        except ValueError as e:
                            safe_print(f"❌ Failed to resolve '{pkg_spec}': {e}")
                            all_packages_satisfied = False
                            break
                        
                        resolved_spec = resolved[0]
                        resolved_specs.append(resolved_spec)
                        resolved_package_cache[pkg_spec] = resolved_spec
                        
                        # Now check if this resolved version is satisfied via fast check
                        pkg_name, version = self._parse_package_spec(resolved_spec)
                        install_status, unused_duration = self.check_package_installed_fast(configured_exe, pkg_name, version) 
                        if install_status == 'active':
                            safe_print(f'✅ {resolved_spec} already satisfied (active in main env)')
                            processed_packages.append(resolved_spec)
                        elif install_status == 'bubble' and install_strategy == 'stable-main':
                            safe_print(f'✅ {resolved_spec} already satisfied (found as bubble)')
                            processed_packages.append(resolved_spec)
                        elif install_status == 'bubble' and install_strategy != 'stable-main':
                            # 'latest-active' needs this to be installed in main env. Not satisfied.
                            all_packages_satisfied = False
                            break # Exit the loop immediately
                        else:
                            # Not found or requires KB check for complex strategies
                            needs_kb_check.append(resolved_spec)
                    
                    # *** ADD THIS CHECK RIGHT HERE ***
                    # After resolution loop, check if everything was satisfied
                    if all_packages_satisfied and not needs_kb_check:
                        preflight_time = (time.perf_counter() - preflight_start) * 1000
                        safe_print(f'✅ PREFLIGHT SUCCESS: All {len(processed_packages)} package(s) already satisfied! ({preflight_time:.1f}ms)')
                        return 0
                
                except NoCompatiblePythonError as e:
                    # Quantum healing during preflight!
                    safe_print("\n" + "="*60)
                    safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected During Preflight")
                    safe_print("="*60)
                    safe_print(f"   - Diagnosis: Cannot resolve '{e.package_name}' v{e.package_version} on Python {e.current_python}.")
                    safe_print(f"   - Prescription: This package requires Python {e.compatible_python}.")
                    from .cli import handle_python_requirement
                    if not e.compatible_python or e.compatible_python == "unknown":
                        safe_print(f"❌ Healing failed: Could not determine compatible Python version.")
                        return 1

                    if not handle_python_requirement(e.compatible_python, self, "omnipkg"):
                        safe_print(f"❌ Healing failed: Could not switch to Python {e.compatible_python}.")
                        return 1

                    safe_print(f"\n🚀 Retrying in new Python {e.compatible_python} context...")
                    new_config_manager = ConfigManager()
                    new_omnipkg_instance = self.__class__(new_config_manager)

                    return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, target_directory)
                #
                # Instead, just log and continue:
                if not all_packages_satisfied:
                    preflight_time = (time.perf_counter() - preflight_start) * 1000
                    
                    # Continue to main installation logic below...
            
            # Phase 3: KB check only for complex cases (nested packages, complex strategies)
            if needs_kb_check and all_packages_satisfied:
                safe_print(f'🔍 Checking {len(needs_kb_check)} package(s) requiring deeper verification...')
                # Only sync KB once if we actually need to check nested/vendored packages
                self._synchronize_knowledge_base_with_reality(verbose=False)
                
                # Now use the already-synced KB data for nested/vendored package detection
                kb_satisfied = True
                for pkg_spec in needs_kb_check:
                    pkg_name, version = self._parse_package_spec(pkg_spec)
                    
                    # The fast check already covered main env and bubbles,
                    # so if we're here, we need to check for nested/vendored installations
                    # using the full KB data
                    
                    # Check if it exists as nested (inside other bubbles)
                    # This requires KB lookup since nested packages aren't in standard locations
                    nested_found = False
                    # ... implement your nested package detection logic here using KB data ...
                    
                    if not nested_found:
                        # If we get here, package is truly not satisfied anywhere
                        kb_satisfied = False
                        break  # ✅ ADD THIS BREAK
                    else:
                        safe_print(f'✅ {pkg_spec} already satisfied (nested)')
                        processed_packages.append(pkg_spec)
                
                all_packages_satisfied = kb_satisfied
            
            # ✅ ADD THIS CHECK AFTER PHASE 3
            if not all_packages_satisfied:
                safe_print(f'📦 Preflight detected packages need installation ({preflight_time:.1f}ms)')
                # Continue to main installation...

        # --- MAIN INSTALLATION LOGIC STARTS HERE ---
        # Continue with the rest of your installation logic...
        configured_exe = self.config.get('python_executable', sys.executable)
        version_tuple = self.config_manager._verify_python_version(configured_exe)
        python_context_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else 'unknown'
        
        if python_context_version == 'unknown':
            safe_print("⚠️ CRITICAL: Could not determine Python context. Manifests may be stamped incorrectly.")
        install_strategy = self.config.get('install_strategy', 'stable-main')
        packages_to_process = list(packages)
        
        # Handle omnipkg special case
        
        # --- ENHANCED OMNIPKG SPECIAL CASE HANDLING ---
        for pkg_spec in list(packages_to_process):
            pkg_name, requested_version = self._parse_package_spec(pkg_spec)
            if pkg_name.lower() == 'omnipkg':
                packages_to_process.remove(pkg_spec)
                safe_print("✨ Special handling: omnipkg '{}' requested.".format(pkg_spec))

                # If no version specified, resolve it
                if not requested_version:
                    resolved_spec = resolved_package_cache.get(pkg_spec)
                    if not resolved_spec:
                        safe_print(f"  ❌ CRITICAL: Could not find pre-resolved version for '{pkg_spec}'. Skipping.")
                        continue
                    pkg_name, requested_version = self._parse_package_spec(resolved_spec)
                    safe_print(f"  -> Using pre-flight resolved version: {resolved_spec}")

                active_omnipkg_version = self._get_active_version_from_environment('omnipkg')
                
                # Check if upgrade is needed or if force_reinstall is set
                if not force_reinstall and active_omnipkg_version and (parse_version(requested_version) == parse_version(active_omnipkg_version)):
                    safe_print('✅ omnipkg=={} is already the active version. No action needed.'.format(requested_version))
                    continue

                # For omnipkg upgrades, we need to actually replace the main installation
                # rather than bubble it, since we want to use the new version immediately
                is_upgrade = active_omnipkg_version and (parse_version(requested_version) > parse_version(active_omnipkg_version))
                is_downgrade = active_omnipkg_version and (parse_version(requested_version) < parse_version(active_omnipkg_version))
                
                if is_upgrade or is_downgrade:
                    action = "Upgrading" if is_upgrade else "Downgrading"
                    safe_print(f"🔄 {action} omnipkg from v{active_omnipkg_version} to v{requested_version}...")
                    
                    # Bubble the OLD version before upgrading (to preserve it)
                    if active_omnipkg_version:
                        bubble_path = self.multiversion_base / f'omnipkg-{active_omnipkg_version}'
                        if not bubble_path.exists():
                            safe_print(f"🫧 Creating bubble for current version (v{active_omnipkg_version})...")
                            # This new method correctly handles dependencies for local dev installs
                            bubble_created = self.bubble_manager.create_bubble_for_package(
                                'omnipkg', active_omnipkg_version, python_context_version=python_context_version
                            )
                            if bubble_created:
                                safe_print(f'✅ Bubbled omnipkg v{active_omnipkg_version}')
                            else:
                                safe_print(f'⚠️  Failed to bubble current version v{active_omnipkg_version}')
                    
                    # Now perform the actual upgrade/downgrade in main environment
                    safe_print(f"📦 Installing omnipkg=={requested_version} to main environment...")
                    packages_before = self.get_installed_packages(live=True)
                    return_code, install_result = self._run_pip_install(
                        [f'omnipkg=={requested_version}'],
                        target_directory=None,
                        force_reinstall=force_reinstall
                    )
                    
                    if return_code != 0:
                        safe_print(f'❌ Failed to install omnipkg=={requested_version}.')
                        continue
                    
                    packages_after = self.get_installed_packages(live=True)
                    any_installations_made = True
                    
                    # Update KB for the new omnipkg version
                    main_env_kb_updates['omnipkg'] = requested_version
                    
                    safe_print(f'✅ omnipkg successfully {action.lower()}d to v{requested_version}!')
                else:
                    # Not currently installed, or same version with force_reinstall
                    bubble_path = self.multiversion_base / f'omnipkg-{requested_version}'
                    if bubble_path.exists() and not force_reinstall:
                        safe_print(f'✅ Bubble for omnipkg=={requested_version} already exists.')
                        continue

                    safe_print(f"🫧 Creating isolated bubble for omnipkg v{requested_version}...")
                    bubble_created = self.bubble_manager.create_isolated_bubble(
                        'omnipkg', requested_version, python_context_version=python_context_version
                    )

                    if bubble_created:
                        safe_print('✅ omnipkg=={} successfully bubbled and registered.'.format(requested_version))
                        self._synchronize_knowledge_base_with_reality()
                    else:
                        safe_print(f'❌ Failed to create bubble for omnipkg=={requested_version}.')
        
        if not packages_to_process:
            safe_print(_('\n🎉 All package operations complete.'))
            return 0

        safe_print("🚀 Starting install with policy: '{}'".format(install_strategy))
        try:
            for pkg_spec in packages_to_process:
                pkg_name, _version = self._parse_package_spec(pkg_spec)
        # *** KEY OPTIMIZATION: Use cached resolved packages instead of re-resolving ***
            if not force_reinstall and resolved_package_cache:
                # Use cached resolutions from preflight check - no duplicate PyPI calls
                resolved_packages = []
                for orig_pkg in packages_to_process:
                    if orig_pkg in resolved_package_cache:
                        resolved_packages.append(resolved_package_cache[orig_pkg])
                        # Silent optimization - users already saw the resolution process in preflight
                    else:
                        # Fallback to resolution if not cached (shouldn't happen in normal flow)
                        # This will show the full PyPI resolution logging since it's a fresh lookup
                        resolved = self._resolve_package_versions([orig_pkg])
                        if resolved:
                            resolved_packages.extend(resolved)
            else:
                # Force reinstall case or no cache - resolve normally with full logging
                resolved_packages = self._resolve_package_versions(packages_to_process)


            if not resolved_packages:
                safe_print(_('❌ Could not resolve any packages to install. Aborting.'))
                return 1

            sorted_packages = self._sort_packages_for_install(resolved_packages, strategy=install_strategy)

        except ValueError as e:  # ADD THIS CATCH BLOCK
            safe_print(f"\n❌ Resolution failed: {e}")
            return 1

        except NoCompatiblePythonError as e:
            # --- THIS IS THE "QUANTUM HEALING" CATCH BLOCK ---
            safe_print("\n" + "="*60)
            safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected")
            safe_print("="*60)
            safe_print(f"   - Diagnosis: Cannot install '{e.package_name}' on your current Python ({e.current_python}).")
            safe_print(f"   - Prescription: This package requires Python {e.compatible_python}.")
            from .cli import handle_python_requirement
            if not e.compatible_python or e.compatible_python == "unknown":
                safe_print(f"❌ Healing failed: Could not determine a compatible Python version for '{e.package_name}'.")
                return 1

            # Use your existing CLI logic (handle_python_requirement) to perform the switch.
            if not handle_python_requirement(e.compatible_python, self, "omnipkg"):
                safe_print(f"❌ Healing failed: Could not automatically switch to Python {e.compatible_python}.")
                return 1

            # THE RECURSIVE CALL: Re-run the *original* command in the new context.
            safe_print(f"\n🚀 Retrying original `install` command in the new Python {e.compatible_python} context...")
            
            # We must create a NEW OmnipkgCore instance because the underlying configuration
            # on disk has changed after the Python swap.
            new_config_manager = ConfigManager()
            new_omnipkg_instance = self.__class__(new_config_manager)

            
            # Re-run the entire smart_install process with the original package list.
            return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, target_directory)
        

        if sorted_packages != resolved_packages:
            safe_print('🔄 Reordered packages for optimal installation: {}'.format(', '.join(sorted_packages)))

        # Rest of the installation logic remains the same...
        user_requested_cnames = {canonicalize_name(self._parse_package_spec(p)[0]) for p in packages}
        main_env_kb_updates = {}
        any_failures = False 
        bubbled_kb_updates = {}
        any_installations_made = False
        kb_deletions = set()
        
        for package_spec in sorted_packages:
            try:   
                safe_print('\n' + '─' * 60)
                safe_print('📦 Processing: {}'.format(package_spec))
                if force_reinstall:
                    safe_print(_('   - 🛡️  Force reinstall triggered by auto-repair.'))
                safe_print('─' * 60)
                
                if not force_reinstall:
                    satisfaction_check = self._check_package_satisfaction([package_spec], strategy=install_strategy)
                    if satisfaction_check['all_satisfied']:
                        safe_print('✅ Requirement already satisfied: {}'.format(package_spec))
                        continue
                
                packages_to_install = [package_spec]
                packages_before = self.get_installed_packages(live=True)
                safe_print('⚙️ Running pip install for: {}...'.format(', '.join(packages_to_install)))
                return_code, pkg_install_output = self._run_pip_install(
                    packages_to_install, target_directory=target_directory, force_reinstall=force_reinstall)
                
                if return_code != 0:
                    safe_print(f'❌ Pip installation failed for {package_spec}.')
                    pkg_name, requested_version = self._parse_package_spec(package_spec)

                    # Check if the failure was due to a cached version
                    py_context = self.current_python_context
                    cached_version = self.pypi_cache.get_cached_version(pkg_name, py_context)

                    if requested_version and cached_version == requested_version:
                        # The failed version came from our cache! Invalidate and retry.
                        self.pypi_cache.invalidate_cache_entry(pkg_name, py_context)
                        safe_print(f"🔄 Retrying {pkg_name} by re-resolving the latest compatible version...")

                        # Re-run the full resolver to get the truly latest compatible version
                        new_compatible_version = self._get_latest_version_from_pypi(pkg_name)

                        if new_compatible_version and new_compatible_version != requested_version:
                            new_spec = f'{pkg_name}=={new_compatible_version}'
                            safe_print(f"   ✅ Found new compatible version: {new_spec}. Retrying install...")
                            retry_code, retry_output = self._run_pip_install(
                                [new_spec], target_directory=target_directory, force_reinstall=force_reinstall)
                            if retry_code == 0:
                                safe_print(f"   🎉 Successfully installed {new_spec} on retry!")
                                return_code = 0 # Mark as successful
                            else:
                                safe_print(f"   ❌ Retry also failed for {new_spec}.")
                        elif new_compatible_version:
                            safe_print(f"   🤷 Re-resolver found the same version ({new_compatible_version}). Cannot auto-correct further.")
                        else:
                            safe_print(f"   ❌ Could not find any compatible version for {pkg_name} on retry.")
                    else:
                        safe_print("   -> Failure was not due to a cached version. Continuing...")

                if return_code != 0:
                    safe_print('❌ Unrecoverable installation failure for {}. Continuing...'.format(package_spec))
                    any_failures = True  # <--- ADD THIS LINE
                    continue
                    
                any_installations_made = True
                packages_after = self.get_installed_packages(live=True)
                replacements = self._detect_version_replacements(packages_before, packages_after)
                
                if replacements:
                    for rep in replacements:
                        kb_deletions.add(rep['package'])
                        self._cleanup_version_from_kb(rep['package'], rep['old_version'])
                
                # Handle stability protection based on install strategy
                if install_strategy == 'stable-main':
                    downgrades_to_fix = self._detect_downgrades(packages_before, packages_after)
                    upgrades_to_fix = self._detect_upgrades(packages_before, packages_after)
                    all_changes_to_fix = []
                    
                    for fix in downgrades_to_fix:
                        all_changes_to_fix.append({'package': fix['package'], 'old_version': fix['good_version'], 'new_version': fix['bad_version'], 'change_type': 'downgraded'})
                    for fix in upgrades_to_fix:
                        all_changes_to_fix.append({'package': fix['package'], 'old_version': fix['old_version'], 'new_version': fix['new_version'], 'change_type': 'upgraded'})
                    
                    if all_changes_to_fix:
                        safe_print(_('🛡️ STABILITY PROTECTION ACTIVATED!'))
                        replaced_packages_count = len({fix['package'] for fix in all_changes_to_fix})
                        safe_print(_('   -> Found {} package(s) downgraded by pip. Bubbling them to preserve stability...').format(replaced_packages_count))
                        main_env_hashes = self.bubble_manager._get_or_build_main_env_hash_index()
                        
                        for fix in all_changes_to_fix:
                            bubble_created = self.bubble_manager.create_isolated_bubble(
                                fix['package'], fix['new_version'], python_context_version=python_context_version
                            )
                            if bubble_created:
                                bubbled_kb_updates[fix['package']] = fix['new_version']
                                bubble_path_str = str(self.multiversion_base / f"{fix['package']}-{fix['new_version']}")
                                self.hook_manager.refresh_bubble_map(fix['package'], fix['new_version'], bubble_path_str)
                                self.hook_manager.validate_bubble(fix['package'], fix['new_version'])
                                restore_result = subprocess.run([self.config['python_executable'], '-m', 'pip', 'install', '--quiet', f"{fix['package']}=={fix['old_version']}"], capture_output=True, text=True)
                                if restore_result.returncode == 0:
                                    main_env_kb_updates[fix['package']] = fix['old_version']
                                    safe_print('   ✅ Bubbled {} v{}, restored stable v{}'.format(fix['package'], fix['new_version'], fix['old_version']))
                                else:
                                    safe_print('   ❌ Failed to restore {} v{}'.format(fix['package'], fix['old_version']))
                            else:
                                safe_print('   ❌ Failed to create bubble for {} v{}'.format(fix['package'], fix['new_version']))
                        safe_print(_('   -> Stability protection complete.'))
                    else:
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
                            versions_to_bubble.append({'package': pkg_name, 'version_to_bubble': old_version, 'version_staying_active': new_version, 'change_type': change_type, 'user_requested': canonicalize_name(pkg_name) in user_requested_cnames})
                        elif not old_version and new_version:
                            main_env_kb_updates[pkg_name] = new_version
                    
                    if versions_to_bubble:
                        safe_print(_('🛡️ LATEST-ACTIVE STRATEGY: Preserving replaced versions'))
                        for item in versions_to_bubble:
                            bubble_created = self.bubble_manager.create_isolated_bubble(
                                item['package'], item['version_to_bubble'], python_context_version=python_context_version
                            )
                            if bubble_created:
                                bubbled_kb_updates[item['package']] = item['version_to_bubble']
                                bubble_path_str = str(self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}")
                                self.hook_manager.refresh_bubble_map(item['package'], item['version_to_bubble'], bubble_path_str)
                                self.hook_manager.validate_bubble(item['package'], item['version_to_bubble'])
                                main_env_kb_updates[item['package']] = item['version_staying_active']
                                safe_print('    ✅ Bubbled {} v{}, keeping v{} active'.format(item['package'], item['version_to_bubble'], item['version_staying_active']))
                            else:
                                safe_print('    ❌ Failed to bubble {} v{}'.format(item['package'], item['version_to_bubble']))

            except NoCompatiblePythonError as e:
                # --- THIS IS THE "QUANTUM HEALING" CATCH BLOCK ---
                safe_print("\n" + "="*60)
                safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected")
                safe_print("="*60)
                safe_print(f"   - Diagnosis: Cannot install '{e.package_name}' on current Python {python_context_version}.")
                from .cli import handle_python_requirement
                compatible_py_ver = self._find_compatible_python_version(e.package_name, self._parse_package_spec(package_spec)[1])

                if not compatible_py_ver:
                    safe_print(f"❌ Healing failed: Could not find any compatible Python version for '{e.package_name}' on PyPI.")
                    return 1

                # Use your existing CLI logic to handle the adopt/swap
                if not handle_python_requirement(compatible_py_ver, self, "omnipkg"):
                    safe_print(f"❌ Healing failed: Could not automatically switch to Python {compatible_py_ver}.")
                    return 1

                # THE RECURSIVE CALL: Re-run the *original* command in the new context
                safe_print(f"\n🚀 Retrying original command in the new Python {compatible_py_ver} context...")
                
                # We must create a NEW instance because the config on disk has changed
                new_config_manager = ConfigManager()
                new_omnipkg_instance = self.__class__(new_config_manager)

                
                # Re-run the entire smart_install with the original package list
                return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, target_directory)

            except ValueError as e:
                safe_print(f"\n❌ Aborting installation: {e}")
                return 1
        if not force_reinstall:
           self._cleanup_redundant_bubbles()
        # Knowledge base update and cleanup logic remains the same...
        safe_print(_('\n🧠 Updating knowledge base (consolidated)...'))
        all_changed_specs = set()
        final_main_state = self.get_installed_packages(live=True)
        initial_packages_before = self.get_installed_packages(live=True) if not any_installations_made else packages_before
        
        for name, ver in final_main_state.items():
            if name not in initial_packages_before or initial_packages_before[name] != ver:
                all_changed_specs.add(f'{name}=={ver}')
        for pkg_name, version in bubbled_kb_updates.items():
            all_changed_specs.add(f'{pkg_name}=={version}')
        for pkg_name, version in main_env_kb_updates.items():
            all_changed_specs.add(f'{pkg_name}=={version}')

        if all_changed_specs:
            safe_print('    Targeting {} package(s) for KB update...'.format(len(all_changed_specs)))
            try:
                from .package_meta_builder import omnipkgMetadataGatherer
                gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, target_context_version=python_context_version, force_refresh=True, omnipkg_instance=self)
                gatherer.cache_client = self.cache_client
                gatherer.run(targeted_packages=list(all_changed_specs))
                self._info_cache.clear()
                self._installed_packages_cache = None
                self._update_hash_index_for_delta(initial_packages_before, final_main_state)
                safe_print(_('    ✅ Knowledge base updated successfully.'))
            except Exception as e:
                safe_print('    ⚠️ Failed to run consolidated knowledge base update: {}'.format(e))
                import traceback
                traceback.print_exc()
        else:
            safe_print(_('    ✅ Knowledge base is already up to date.'))


        safe_print(_('\n🎉 All package operations complete.'))
        self._save_last_known_good_snapshot()
        self._synchronize_knowledge_base_with_reality()
        return 0

    def smart_upgrade(self, version: Optional[str] = None, force: bool = False, skip_dev_check: bool = False) -> int:
        """
        (V23 - The Minimalist Finalizer) The definitive self-upgrade. Trusts the
        core library's self-healing capabilities and focuses solely on correctly
        updating the filesystem installation via a standalone process.
        """
        print_header("omnipkg Self-Upgrade (Minimalist Finalizer)")

        current_version_str = self._get_active_version_from_environment('omnipkg')
        if not current_version_str:
            safe_print("   - ❌ Could not detect currently installed omnipkg version. Aborting.")
            return 1
        safe_print(f"   - Current installed version: {current_version_str}")

        project_root = self.config_manager._find_project_root()
        if project_root and not skip_dev_check:
            safe_print("\n" + "🛡️" * 30)
            safe_print("   DEV MODE DETECTED: Self-upgrade is disabled.")
            safe_print("   To upgrade: `git pull` and then `pip install -e .`")
            safe_print("   (Use --force-dev to override and test user-mode upgrade)")
            safe_print("🛡️" * 30)
            return 0

        target_version_str = version or self._fetch_latest_pypi_version_only('omnipkg')
        if not target_version_str:
            safe_print("   - ❌ Could not determine target version from PyPI. Aborting.")
            return 1
        safe_print(f"   - Target PyPI version: {target_version_str}")

        if current_version_str == target_version_str and not force:
            safe_print(f"✅ Already on version {target_version_str}. No upgrade needed.")
            return 0

        if not force:
            if input("\n🤔 WARNING: This will irrevocably replace the current omnipkg installation. Proceed? (y/N): ").lower().strip() != 'y':
                safe_print("🚫 Upgrade cancelled.")
                return 1

        python_context = self.current_python_context.replace('py', '')

        safe_print(f"\n   - Step 1: Preserving current version (v{current_version_str}) in a bubble...")
        if not self.bubble_manager.create_bubble_for_package('omnipkg', current_version_str, python_context_version=python_context):
            safe_print(f"   - ❌ Failed to bubble current version v{current_version_str}. Aborting.")
            return 1
        
        # --- ADD THIS BLOCK TO FIX THE MISSING METADATA ---

        # --- END OF FIX ---

        safe_print(f"   - ✅ Successfully bubbled and indexed omnipkg v{current_version_str}.")

        # --- The upgrader script that will be executed by a new, clean process ---
        # It has one job: replace the files on disk. Nothing more.
        upgrader_script_content = textwrap.dedent(f"""
            import sys, os, subprocess, time, textwrap
            try:
                from .common_utils import safe_print
            except ImportError:
                from omnipkg.common_utils import safe_print
            def run_cmd(cmd, description):
                print(f"--- [Upgrader] Executing: {{description}} ---")
                print(f"    $ {{' '.join(cmd)}}")
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
                for line in iter(process.stdout.readline, ''):
                    print(f"    | {{line.strip()}}")
                return_code = process.wait()
                if return_code != 0:
                    safe_print(f"--- ❌ [Upgrader] FAILED: {{description}} (exit code {{return_code}}) ---")
                    sys.exit(return_code)
                safe_print(f"--- ✅ [Upgrader] SUCCESS: {{description}} ---")

            try:
                safe_print("--- 🚀 [Upgrader] Standalone upgrader process started. ---")
                time.sleep(1)

                run_cmd(
                    [sys.executable, "-m", "pip", "uninstall", "-y", "omnipkg"],
                    "Clean uninstall of old omnipkg version"
                )

                run_cmd(
                    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "omnipkg=={target_version_str}"],
                    "Install new omnipkg version"
                )

                print("\\n" + "="*70)
                safe_print("--- ✅ [Upgrader] FILESYSTEM UPGRADE COMPLETE ---")
                print(f"--- omnipkg has been successfully upgraded to version {target_version_str}. ---")
                print("--- The Knowledge Base will sync automatically on your next command. ---")
                print("="*70)
                sys.exit(0)

            except Exception as e:
                safe_print(f"--- ❌ [Upgrader] A fatal error occurred: {{e}} ---")
                import traceback
                traceback.print_exc()
                sys.exit(1)
        """)

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py', prefix='omnipkg_upgrader_') as f:
            f.write(upgrader_script_content)
            upgrader_script_path = f.name

        # --- THE HANDOFF ---
        safe_print("\n   - Step 2: Handing over to the standalone upgrader process...")
        main_python_exe = self.config['python_executable']
        args_for_exec = [main_python_exe, upgrader_script_path]

        try:
            if os.name == 'nt':
                subprocess.Popen(args_for_exec, creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
                sys.exit(0)
            else:
                os.execv(main_python_exe, args_for_exec)
        except Exception as e:
            safe_print(f"\n   - ❌ CRITICAL: Handover failed: {e}")
            return 1

        return 1

    def _create_pypi_only_bubble(self, package_name: str, version: str, python_context: str) -> bool:
        """
        Creates a bubble by downloading ONLY from PyPI, never from local source.
        This ensures upgrade bubbles contain the actual PyPI version.
        """
        safe_print(f"🫧 Creating PyPI-sourced bubble for {package_name} v{version}...")
        
        install_source = f"{package_name}=={version}"
        safe_print(f"   - Using PyPI source: {install_source}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Install from PyPI explicitly
            safe_print(f"   - Installing full dependency tree from PyPI to temporary location...")
            cmd = [
                self.config['python_executable'], '-m', 'pip', 'install',
                '--target', str(temp_path),
                '--no-cache-dir',  # Force fresh download
                install_source
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                safe_print(f'   - ❌ Failed to install "{install_source}" from PyPI.')
                safe_print("--- Pip Error ---")
                safe_print(result.stderr)
                safe_print("-----------------")
                return False

            # Analyze the installed tree
            installed_tree = self.bubble_manager._analyze_installed_tree(temp_path)
            
            # Create the deduplicated bubble
            bubble_path = self.multiversion_base / f'{package_name}-{version}'
            if bubble_path.exists():
                shutil.rmtree(bubble_path)
            
            return self.bubble_manager._create_deduplicated_bubble(
                installed_tree, bubble_path, temp_path, 
                python_context_version=python_context
            )
    
    def _find_compatible_python_version(self, package_name: str, target_package_version: Optional[str] = None) -> Optional[str]:
        """
        (REWRITTEN) Queries PyPI to find a compatible Python version for a package.
        Handles both UPGRADE and DOWNGRADE scenarios by analyzing wheel files first.

        Returns: A recommended Python version string (e.g., "3.9") if the current
                 version is incompatible, otherwise returns None.
        """
        safe_print(f"   - 🐍 Searching PyPI for Python requirements for '{package_name}'...")
        try:
            from packaging.specifiers import SpecifierSet
            from packaging.version import parse as parse_version

            response = http_requests.get(f'https://pypi.org/pypi/{package_name}/json', timeout=15)
            response.raise_for_status()
            data = response.json()

            current_py_str = self.current_python_context.replace('py', '')  # "py3.7" -> "3.7"
            current_py_tuple = tuple(map(int, current_py_str.split('.')))
            safe_print(f"   - 📍 Current Python: {current_py_str}")

            # Determine which package version to check
            if target_package_version:
                if target_package_version not in data['releases']:
                    safe_print(f"   - ❌ Version {target_package_version} not found on PyPI for '{package_name}'")
                    return "unknown"
                release_info = data['releases'][target_package_version]
                safe_print(f"   - 🎯 Checking compatibility for: {package_name}=={target_package_version}")
            else:
                target_package_version = data['info']['version']
                release_info = data['releases'].get(target_package_version, [])
                safe_print(f"   - 🎯 Checking compatibility for latest version: {package_name}=={target_package_version}")

            # ============================================================
            # STRATEGY 1: Parse wheel filenames (MOST ACCURATE)
            # ============================================================
            supported_py_versions = set()
            for file_info in release_info:
                filename = file_info.get('filename', '')
                if file_info.get('packagetype') == 'bdist_wheel' and filename.endswith('.whl'):
                    # Handles tags like cp38, py3, cp310, py2.py3
                    matches = re.findall(r'-(?:cp|py)(\d)(\d{1,2})?-', filename)
                    for match in matches:
                        major, minor = match
                        if minor:
                            supported_py_versions.add(f"{major}.{minor}")

            if supported_py_versions:
                safe_print(f"   - Found Python versions supported by wheels: {', '.join(sorted(supported_py_versions))}")
                
                supported_tuples = sorted([tuple(map(int, v.split('.'))) for v in supported_py_versions])

                if any(current_py_tuple == v_tuple for v_tuple in supported_tuples):
                    safe_print(f"   - ✅ Current Python {current_py_str} is compatible according to wheel data.")
                    return None  # It's compatible!

                # If not directly compatible, find the best path.
                # Prioritize UPGRADE path
                future_versions = [v for v in supported_tuples if v > current_py_tuple]
                if future_versions:
                    recommended = min(future_versions)
                    rec_str = f"{recommended[0]}.{recommended[1]}"
                    safe_print(f"   - 💡 Recommendation: Upgrade to Python {rec_str} for this package.")
                    return rec_str

                # If no future versions, it's a DOWNGRADE path
                past_versions = [v for v in supported_tuples if v < current_py_tuple]
                if past_versions:
                    recommended = max(past_versions)
                    rec_str = f"{recommended[0]}.{recommended[1]}"
                    safe_print(f"   - 💡 Recommendation: Downgrade to Python {rec_str} for this package (e.g., tensorflow).")
                    return rec_str

            # ============================================================
            # STRATEGY 2: FALLBACK to requires_python metadata
            # ============================================================
            safe_print("   - ⚠️  Could not determine compatibility from wheels, falling back to metadata.")
            requires_python = data['info'].get('requires_python')

            if not requires_python:
                safe_print(f"   - ⚠️  No Python requirements specified in metadata.")
                return "unknown"

            safe_print(f"   - 📋 requires_python metadata: '{requires_python}'")
            spec = SpecifierSet(requires_python)

            if not spec.contains(current_py_str):
                # The spec does NOT contain our version, so it's incompatible.
                # Try to find the closest valid version we support.
                our_supported_versions = ["3.13", "3.12", "3.11", "3.10", "3.9", "3.8", "3.7"]
                
                # Find best upgrade path
                for py_ver in reversed(our_supported_versions):
                     if tuple(map(int, py_ver.split('.'))) > current_py_tuple and spec.contains(py_ver):
                         safe_print(f"   - 💡 Recommendation (from metadata): Upgrade to Python {py_ver}.")
                         return py_ver
                
                # Find best downgrade path
                for py_ver in our_supported_versions:
                    if tuple(map(int, py_ver.split('.'))) < current_py_tuple and spec.contains(py_ver):
                        safe_print(f"   - 💡 Recommendation (from metadata): Downgrade to Python {py_ver}.")
                        return py_ver
                
                return "unknown" # Can't find a path

            safe_print(f"   - ✅ Current Python {current_py_str} seems compatible based on metadata.")
            return None # Compatible based on metadata

        except Exception as e:
            safe_print(f"   - ⚠️  Error during compatibility check: {e}")
            return "unknown"

    def _cleanup_redundant_bubbles(self):
        """
        Scans for and REMOVES any bubbles from the filesystem that are identical
        to the currently active version of a package. Now ignores protected tool bubbles.
        """
        safe_print(_('\n🧹 Cleaning redundant bubbles...'))
        try:
            # --- START OF FIX ---
            # Define a set of package names that should never be cleaned up.
            # These are internal tools managed by omnipkg itself.
            PROTECTED_TOOL_PACKAGES = {'safety'}
            # --- END OF FIX ---

            final_active_packages = self.get_installed_packages(live=True)
            cleaned_count = 0
            for pkg_name, active_version in final_active_packages.items():
                # --- ADD THIS CHECK ---
                if canonicalize_name(pkg_name) in PROTECTED_TOOL_PACKAGES:
                    safe_print(f"   - 🛡️  Skipping cleanup for protected tool: {pkg_name}")
                    continue
                # --- END OF CHECK ---

                # Construct the path to a potentially redundant bubble
                bubble_path = self.multiversion_base / f'{pkg_name}-{active_version}'
                
                if bubble_path.exists() and bubble_path.is_dir():
                    safe_print(f"   - Found redundant bubble for active package: {pkg_name}=={active_version}")
                    try:
                        shutil.rmtree(bubble_path)
                        cleaned_count += 1
                        safe_print(f"   - ✅ Removed redundant bubble directory.")
                    except Exception as e:
                        safe_print(_('    ❌ Failed to remove bubble directory: {}').format(e))
            
            if cleaned_count > 0:
                safe_print('    ✅ Removed {} redundant bubble directory(s).'.format(cleaned_count))
            else:
                safe_print('    ✅ No redundant bubbles found.')
        except Exception as e:
            safe_print(f"   - ⚠️  An error occurred during bubble cleanup: {e}")

    def _detect_conda_corruption_from_error(self, stderr_output: str) -> Optional[Tuple[str, str]]:
        """
        Detect corruption patterns in conda command stderr output.
        
        Args:
            stderr_output: Standard error from failed conda command
            
        Returns:
            Tuple of (corrupted_file_path, environment_path) if detected, None otherwise
        """
        if "CorruptedEnvironmentError" not in stderr_output:
            return None
        
        # Patterns to match different corruption error formats
        patterns = [
            # Full error with environment location and corrupted file
            r"environment location:\s*(.+?)\s*corrupted file:\s*(.+?)(?:\n|$)",
            # Just corrupted file mentioned
            r"corrupted file:\s*(.+?)(?:\n|$)",
            # Alternative format
            r"CorruptedEnvironmentError.*?(\/.+?\.json)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, stderr_output, re.MULTILINE | re.DOTALL)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    # Full match with env location and file
                    return groups[1].strip(), groups[0].strip()
                elif len(groups) == 1:
                    # Just file path, derive env location
                    file_path = groups[0].strip()
                    if "/conda-meta/" in file_path:
                        env_path = file_path.split("/conda-meta/")[0]
                        return file_path, env_path
        
        return None


    def _backup_corrupted_file(self, file_path: str, backup_base_dir: Optional[str] = None) -> bool:
        """
        Create a backup of a corrupted file before removal.
        
        Args:
            file_path: Path to the corrupted file
            backup_base_dir: Base directory for backups (default: ~/.omnipkg/conda-backups)
            
        Returns:
            True if backup successful, False otherwise
        """
        try:
            if not os.path.exists(file_path):
                return True  # File already gone, no backup needed
            
            if backup_base_dir is None:
                backup_base_dir = os.path.join(Path.home(), ".omnipkg", "conda-backups")
            
            timestamp_dir = os.path.join(backup_base_dir, str(int(time.time())))
            os.makedirs(timestamp_dir, exist_ok=True)
            
            backup_path = os.path.join(timestamp_dir, os.path.basename(file_path))
            shutil.copy2(file_path, backup_path)
            
            safe_print(f"   - 💾 Backed up corrupted file to: {backup_path}")
            return True
            
        except Exception as e:
            safe_print(f"   - ⚠️  Failed to backup {file_path}: {e}")
            return False
            
    def _run_conda_with_healing(self, cmd_args: List[str], max_attempts: int = 3) -> subprocess.CompletedProcess:
        """
        Run a conda command with automatic corruption healing.
        
        Args:
            cmd_args: Conda command arguments (e.g., ['install', 'package'])
            max_attempts: Maximum number of repair attempts
            
        Returns:
            CompletedProcess result
        """
        full_cmd = ["conda"] + cmd_args
        
        for attempt in range(max_attempts):
            try:
                proc = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                # If successful, return immediately
                if proc.returncode == 0:
                    return proc
                
                # Check for corruption in the error output
                corruption_info = self._detect_conda_corruption_from_error(proc.stderr)
                
                if not corruption_info:
                    # Not a corruption error, return the failed result
                    return proc
                
                corrupted_file, env_location = corruption_info
                
                if attempt < max_attempts - 1:  # Don't heal on the last attempt
                    safe_print(f"\n🛡️  AUTO-HEAL: Detected corruption (attempt {attempt + 1}/{max_attempts})")
                    safe_print(f"   - 💀 Corrupted file: {os.path.basename(corrupted_file)}")
                    
                    # Backup and remove the corrupted file
                    if self._backup_corrupted_file(corrupted_file):
                        try:
                            if os.path.exists(corrupted_file):
                                os.unlink(corrupted_file)
                                safe_print(f"   - 🗑️  Removed corrupted file")
                            
                            # Also clean up any related .pyc files
                            corrupted_dir = os.path.dirname(corrupted_file)
                            if os.path.exists(corrupted_dir):
                                for pyc_file in Path(corrupted_dir).glob("*.pyc"):
                                    pyc_file.unlink()
                            
                            safe_print(f"   - 🔄 Retrying conda command...")
                            
                        except Exception as e:
                            safe_print(f"   - ❌ Failed to remove corrupted file: {e}")
                            return proc
                else:
                    safe_print(f"❌ Max repair attempts ({max_attempts}) reached. Manual intervention needed.")
                    return proc
                    
            except subprocess.TimeoutExpired:
                safe_print("❌ Conda command timed out")
                raise
            except Exception as e:
                safe_print(f"❌ Error running conda command: {e}")
                raise
        
        return proc  # Should never reach here, but just in case
        
    def _heal_conda_environment(self, also_run_clean: bool = True):
        """
        Enhanced conda environment healing that combines proactive scanning 
        with reactive error-based healing.
        
        This function:
        1. Proactively scans for corrupted JSON files in conda-meta
        2. Can also reactively heal based on conda command errors
        3. Optionally runs 'conda clean' after repairs
        
        Args:
            self: Optional self reference if called as method
            also_run_clean: Whether to run 'conda clean --all' after healing
        """
        conda_prefix_str = os.environ.get('CONDA_PREFIX')
        if not conda_prefix_str:
            return  # Not in a conda environment
        
        conda_meta_path = Path(conda_prefix_str) / 'conda-meta'
        if not conda_meta_path.is_dir():
            return  # No metadata directory
        
        safe_print("🛡️  AUTO-HEAL: Scanning conda environment for corruption...")
        
        # Proactive scan for corrupted files
        corrupted_files_found = []
        total_files = 0
        
        for meta_file in conda_meta_path.glob('*.json'):
            total_files += 1
            try:
                # Check 1: Empty file
                if meta_file.stat().st_size == 0:
                    corrupted_files_found.append(str(meta_file))
                    continue
                
                # Check 2: Invalid JSON
                with open(meta_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # Check 3: Missing required fields (basic validation)
                    required_fields = ['name', 'version']
                    if not all(field in data for field in required_fields):
                        corrupted_files_found.append(str(meta_file))
                        continue
                        
            except (json.JSONDecodeError, UnicodeDecodeError):
                corrupted_files_found.append(str(meta_file))
            except Exception:
                # Other errors ignored, only care about JSON corruption
                continue
        
        safe_print(f"   - 📊 Scanned {total_files} metadata files")
        
        if not corrupted_files_found:
            safe_print("   - ✅ No corruption detected in conda metadata")
            return
        
        # Healing process
        safe_print(f"   - 💀 Found {len(corrupted_files_found)} corrupted file(s)")
        backup_dir = Path.home() / ".omnipkg" / "conda-backups" / str(int(time.time()))
        
        cleaned_count = 0
        for corrupted_file in corrupted_files_found:
            file_name = os.path.basename(corrupted_file)
            safe_print(f"      -> Processing: {file_name}")
            
            if self._backup_corrupted_file(corrupted_file, str(backup_dir.parent)):
                try:
                    if os.path.exists(corrupted_file):
                        os.unlink(corrupted_file)
                        cleaned_count += 1
                        safe_print(f"         ✅ Removed corrupted file")
                except Exception as e:
                    safe_print(f"         ❌ Failed to remove: {e}")
        
        if cleaned_count > 0:
            safe_print(f"   - 🧹 Successfully cleaned {cleaned_count} corrupted file(s)")
            
            # Optionally run conda clean to clear caches
            if also_run_clean:
                safe_print("   - 🧽 Running conda clean to clear caches...")
                try:
                    clean_proc = subprocess.run(
                        ["conda", "clean", "--all", "--yes"],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    if clean_proc.returncode == 0:
                        safe_print("      ✅ Conda clean completed successfully")
                    else:
                        safe_print(f"      ⚠️  Conda clean had issues: {clean_proc.stderr}")
                except Exception as e:
                    safe_print(f"      ❌ Error running conda clean: {e}")
            
            safe_print("   - 💡 Conda environment should now be stable")
        
        safe_print('─' * 60)
        
    def safe_conda_command(self, cmd_args: List[str], max_heal_attempts: int = 2) -> bool:
        """
        Wrapper function to run conda commands with automatic healing.
        
        Args:
            cmd_args: Conda command arguments (e.g., ['install', '-y', 'package'])
            max_heal_attempts: Maximum healing attempts on corruption
            
        Returns:
            True if command succeeded, False otherwise
        """
        try:
            # First, do a proactive heal
            self._heal_conda_environment(also_run_clean=False)
            
            # Then run the command with reactive healing
            proc = self._run_conda_with_healing(cmd_args, max_heal_attempts)
            
            if proc.returncode == 0:
                return True
            else:
                safe_print(f"❌ Conda command failed: {proc.stderr}")
                return False
                
        except Exception as e:
            safe_print(f"❌ Exception during conda command: {e}")
            return False

    
    def _auto_heal_invalid_distributions(self, pip_output: str, site_packages_path: Path):
        """
        Parses pip's output for 'Ignoring invalid distribution' warnings and
        surgically removes the corresponding broken directories.
        """
        invalid_dist_pattern = r"WARNING: Ignoring invalid distribution ~([\w-]+) \((.*)\)"
        found_corrupted = re.findall(invalid_dist_pattern, pip_output)

        if not found_corrupted:
            return

        safe_print('\n' + '─' * 60)
        safe_print(_("🛡️  AUTO-HEAL: Detected corrupted package installations. Cleaning up..."))
        
        cleaned_count = 0
        for name, path_str in found_corrupted:
            # The path pip gives is the parent dir, we need to find the specific broken folder
            parent_dir = Path(path_str)
            if parent_dir.resolve() != site_packages_path.resolve():
                safe_print(f"   - ⚠️  Skipping cleanup for '{name}' as it's not in the active site-packages.")
                continue

            # The broken directory is usually named with a tilde and the name pip found
            broken_dist_info = parent_dir / f"~{name.lower()}"
            
            # It could also have version info, so we use a glob for robustness
            found_paths = list(parent_dir.glob(f"~{name.lower()}*"))

            if not found_paths:
                safe_print(f"   - ❔ Could not locate directory for corrupted package '{name}'. It may have been removed already.")
                continue

            for broken_path in found_paths:
                if broken_path.is_dir():
                    try:
                        safe_print(_("   - 🗑️  Removing corrupted directory: {}").format(broken_path))
                        shutil.rmtree(broken_path)
                        cleaned_count += 1
                    except OSError as e:
                        safe_print(_("   - ❌ Failed to remove {}: {}").format(broken_path, e))
        
        if cleaned_count > 0:
            safe_print(_("   - ✅ Successfully cleaned up {} corrupted package installation(s).").format(cleaned_count))
        safe_print('─' * 60)

    def _brute_force_package_cleanup(self, pkg_name: str, site_packages: Path):
        """
        Performs a manual, brute-force deletion of a corrupted package's files
        in a specific site-packages directory.
        """
        safe_print(_("🧹 Performing brute-force cleanup of corrupted package '{}' in {}...").format(pkg_name, site_packages))
        try:
            c_name_dash = canonicalize_name(pkg_name)
            c_name_under = c_name_dash.replace('-', '_')
            for name_variant in {c_name_dash, c_name_under}:
                for path in site_packages.glob(f'{name_variant}'):
                    if path.is_dir():
                        safe_print(_('   - Deleting library directory: {}').format(path))
                        shutil.rmtree(path, ignore_errors=True)
            for path in site_packages.glob(f'{c_name_dash}-*.dist-info'):
                if path.is_dir():
                    safe_print(_('   - Deleting metadata: {}').format(path))
                    shutil.rmtree(path, ignore_errors=True)
            safe_print(_('   - ✅ Brute-force cleanup complete.'))
            return True
        except Exception as e:
            safe_print(_('   - ❌ Brute-force cleanup FAILED: {}').format(e))
            return False
        
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
            safe_print(_('Error getting active version of {}: {}').format(pkg_name, e))
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
        safe_print(_('   -> Cleaning up replaced version from knowledge base: {} v{}').format(package_name, version))
        c_name = canonicalize_name(package_name)
        main_key = f'{self.redis_key_prefix}{c_name}'
        version_key = f'{main_key}:{version}'
        versions_set_key = f'{main_key}:installed_versions'
        with self.cache_client.pipeline() as pipe:
            pipe.delete(version_key)
            pipe.srem(versions_set_key, version)
            pipe.hdel(main_key, f'bubble_version:{version}')
            if self.cache_client.hget(main_key, 'active_version') == version:
                pipe.hdel(main_key, 'active_version')
            pipe.execute()

    def _restore_from_snapshot(self, snapshot: Dict, current_state: Dict):
        """Restores the main environment to the exact state of a given snapshot."""
        safe_print(_('🔄 Restoring main environment from snapshot...'))
        snapshot_keys = set(snapshot.keys())
        current_keys = set(current_state.keys())
        to_uninstall = [pkg for pkg in current_keys if pkg not in snapshot_keys]
        to_install_or_fix = ['{}=={}'.format(pkg, ver) for pkg, ver in snapshot.items() if pkg not in current_keys or current_state.get(pkg) != ver]
        if not to_uninstall and (not to_install_or_fix):
            safe_print(_('   ✅ Environment is already in its original state.'))
            return
        if to_uninstall:
            safe_print(_('   -> Uninstalling: {}').format(', '.join(to_uninstall)))
            self._run_pip_uninstall(to_uninstall)
        if to_install_or_fix:
            safe_print(_('   -> Installing/Fixing: {}').format(', '.join(to_install_or_fix)))
            install_code, fix_output = self._run_pip_install(to_install_or_fix + ['--no-deps'])
        safe_print(_('   ✅ Environment restored.'))

    def _extract_wheel_into_bubble(self, wheel_url: str, target_bubble_path: Path, pkg_name: str, pkg_version: str) -> bool:
        """
        Downloads a wheel and extracts its content directly into a bubble directory.
        Does NOT use pip install.
        """
        safe_print(_('📦 Downloading wheel for {}=={}...').format(pkg_name, pkg_version))
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
                        safe_print(_('⚠️ Warning: Could not extract {}: {}').format(member, extract_error))
                        continue
            safe_print(_('✅ Extracted {}=={} to {}').format(pkg_name, pkg_version, target_bubble_path.name))
            return True
        except http_requests.exceptions.RequestException as e:
            safe_print(_('❌ Failed to download wheel from {}: {}').format(wheel_url, e))
            return False
        except zipfile.BadZipFile:
            safe_print(_('❌ Downloaded file is not a valid wheel: {}').format(wheel_url))
            return False
        except Exception as e:
            safe_print(_('❌ Error extracting wheel for {}=={}: {}').format(pkg_name, pkg_version, e))
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
                        safe_print(_('🎯 Found compatible wheel: {}').format(url_info['filename']))
                        return url_info['url']
            for url_info in data.get('urls', []):
                if url_info['packagetype'] == 'sdist':
                    safe_print(_('⚠️ Only source distribution available for {}=={}').format(pkg_name, pkg_version))
                    safe_print(_('   This may require compilation and is not recommended for bubbling.'))
                    return None
            safe_print(_('❌ No compatible wheel or source found for {}=={} on PyPI.').format(pkg_name, pkg_version))
            return None
        except http_requests.exceptions.RequestException as e:
            safe_print(_('❌ Failed to fetch PyPI data for {}=={}: {}').format(pkg_name, pkg_version, e))
            return None
        except KeyError as e:
            safe_print(_('❌ Unexpected PyPI response structure: missing {}').format(e))
            return None
        except Exception as e:
            safe_print(_('❌ Error parsing PyPI data: {}').format(e))
            return None

    def _parse_package_spec(self, pkg_spec: str) -> Tuple[str, Optional[str]]:
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
                        safe_print(_("⚠️ Version specifier '{}' detected in '{}'. Exact version required for bubbling.").format(separator, pkg_spec))
                        return (pkg_name, None)
        return (pkg_spec.strip(), None)

    def rebuild_package_kb(self, packages: List[str], force: bool=True, target_python_version: Optional[str]=None, search_path_override: Optional[str]=None) -> int:
        """
        (CORRECTED) Forces a targeted KB rebuild, now correctly determining and
        passing the Python context to prevent accidental ghost exorcisms.
        """
        if not packages:
            return 0
        safe_print(_('🧠 Forcing targeted KB rebuild for: {}...').format(', '.join(packages)))
        if not self.cache_client:
            return 1
        try:
            # --- THIS IS THE CRITICAL FIX ---
            # If a target version isn't explicitly passed, we MUST determine it from the
            # currently configured Python executable. We can no longer allow it to be None.
            final_target_version = target_python_version
            if not final_target_version:
                configured_exe = self.config.get('python_executable', sys.executable)
                version_tuple = self.config_manager._verify_python_version(configured_exe)
                if version_tuple:
                    final_target_version = f'{version_tuple[0]}.{version_tuple[1]}'
                else:
                    # Fallback if verification fails for some reason
                    final_target_version = f'{sys.version_info.major}.{sys.version_info.minor}'
            # --- END OF FIX ---
            
            search_path = search_path_override
            if not search_path and any(self.multiversion_base / f'{self._parse_package_spec(p)[0]}-{self._parse_package_spec(p)[1]}' for p in packages if '==' in p):
                 search_path = str(self.multiversion_base)
                 safe_print(f"   -> Targeting KB build to bubble directory: {search_path}")

            gatherer = omnipkgMetadataGatherer(
                config=self.config, 
                env_id=self.env_id, 
                force_refresh=force, 
                omnipkg_instance=self, 
                target_context_version=final_target_version # Use the guaranteed version
            )
            
            found_distributions = gatherer.run(
                targeted_packages=packages, 
                search_path_override=search_path
            )
            
            if found_distributions is None:
                found_distributions = []

            # Ghost hunting logic remains the same, but will now work correctly
            requested_specs_canonical = {f'{canonicalize_name(self._parse_package_spec(s)[0])}=={self._parse_package_spec(s)[1]}' for s in packages if '==' in s}
            found_specs_canonical = {f"{canonicalize_name(dist.metadata['Name'])}=={dist.version}" for dist in found_distributions}
            ghost_specs_canonical = requested_specs_canonical - found_specs_canonical

            if ghost_specs_canonical:
                original_spec_map = {f'{canonicalize_name(self._parse_package_spec(s)[0])}=={self._parse_package_spec(s)[1]}': s for s in packages if '==' in s}
                for canonical_spec in ghost_specs_canonical:
                    original_spec = original_spec_map.get(canonical_spec, canonical_spec)
                    self._exorcise_ghost_entry(original_spec)

            self._info_cache.clear()
            self._installed_packages_cache = None
            safe_print(f'   ✅ Knowledge base for {len(found_specs_canonical)} package(s) successfully rebuilt.')
            if ghost_specs_canonical:
                safe_print(_('   ✅ Exorcised {} ghost entries.').format(len(ghost_specs_canonical)))
            return 0
        except Exception as e:
            safe_print(_('    ❌ An unexpected error occurred during targeted KB rebuild: {}').format(e))
            traceback.print_exc()
            return 1

    def _register_package_in_knowledge_base(self, pkg_name: str, version: str, bubble_path: str, install_type: str):
        """
        Register a bubbled package in the knowledge base.
        This integrates with your existing knowledge base system.
        """
        try:
            package_info = {'name': pkg_name, 'version': version, 'install_type': install_type, 'path': bubble_path, 'created_at': self._get_current_timestamp()}
            key = 'package:{}:{}'.format(pkg_name, version)
            if hasattr(self, 'cache_client') and self.cache_client:
                import json
                self.cache_client.set(key, json.dumps(package_info))
                safe_print(_('📝 Registered {}=={} in knowledge base').format(pkg_name, version))
            else:
                safe_print(_('⚠️ Could not register {}=={}: No Redis connection').format(pkg_name, version))
        except Exception as e:
            safe_print(_('❌ Failed to register {}=={} in knowledge base: {}').format(pkg_name, version, e))

    def _get_current_timestamp(self) -> str:
        """Helper to get current timestamp for knowledge base entries."""
        import datetime
        return datetime.datetime.now().isoformat()

    def _find_package_installations(self, package_name: str, pre_discovered_dists: Optional[List[importlib.metadata.Distribution]] = None) -> List[Dict]:
        """
        (V5.0 - THE REDIS-AWARE FIX) Finds all distinct installations by trusting the filesystem
        first, then enriching with data from Redis using the CORRECT key generation logic.
        """
        c_name = canonicalize_name(package_name)

        # Step 1: Discover physical distributions on disk (the ground truth).
        if pre_discovered_dists is not None:
            all_dists = pre_discovered_dists
        else:
            # --- FIX STARTS HERE ---
            # The original code had a faulty fallback that tried to use the `dist`
            # variable before it was defined. The correct fallback is simply to
            # perform the filesystem scan, which is what the subsequent code does.
            # We remove the erroneous lines and proceed directly to the scan.
            configured_exe = self.config.get('python_executable', sys.executable)
            version_tuple = self.config_manager._verify_python_version(configured_exe)
            current_python_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else None
            from .package_meta_builder import omnipkgMetadataGatherer
            gatherer = omnipkgMetadataGatherer(
                config=self.config, env_id=self.env_id, omnipkg_instance=self,
                target_context_version=current_python_version
            )
            all_dists = gatherer._discover_distributions(None, verbose=False)
            # --- FIX ENDS HERE ---

        target_dists = [
            dist for dist in all_dists
            if canonicalize_name(dist.metadata.get("Name", "")) == c_name
        ]

        if not target_dists:
            return []

        # De-duplicate based on the unique metadata path.
        unique_dists = {dist._path.resolve(): dist for dist in target_dists}.values()

        # Step 2: For each physical distribution, generate the CORRECT key and fetch from Redis.
        keys_to_fetch = []
        dist_map = {}  # Maps a key back to its dist object for fallback.
        for dist in unique_dists:
            resolved_path_str = str(dist._path.resolve())
            unique_instance_identifier = f"{resolved_path_str}::{dist.version}"
            instance_hash = hashlib.sha256(unique_instance_identifier.encode()).hexdigest()[:12]
            instance_key = f"{self.redis_key_prefix.replace(':pkg:', ':inst:')}{c_name}:{dist.version}:{instance_hash}"

            keys_to_fetch.append(instance_key)
            dist_map[instance_key] = dist

        # Use a pipeline for efficient fetching.
        with self.cache_client.pipeline() as pipe:
            for key in keys_to_fetch:
                pipe.hgetall(key)
            redis_results = pipe.execute()

        # Step 3: Build the final list, falling back to basic data only if Redis lookup fails.
        found_installations = []
        active_version_str = self.cache_client.hget(f'{self.redis_key_prefix}{c_name}', 'active_version')

        for key, redis_data in zip(keys_to_fetch, redis_results):
            dist = dist_map[key]
            if redis_data:
                # SUCCESS: We found the rich data in Redis.
                redis_data['is_active'] = (redis_data.get('Version') == active_version_str and redis_data.get('install_type') == 'active')
                redis_data['redis_key'] = key
                found_installations.append(redis_data)
            else:
                # FALLBACK: The KB is out of sync. Build a basic entry from the dist object.
                # This is better than recursively rebuilding here; we just report what we found.
                # The main sync logic will handle the full KB repair.
                safe_print(f"   ⚠️  KB is out of sync for {c_name}=={dist.version}. (key: {key})")
                from .package_meta_builder import omnipkgMetadataGatherer
                gatherer = omnipkgMetadataGatherer(config=self.config, env_id=self.env_id, omnipkg_instance=self)
                context_info = gatherer._get_install_context(dist)

                basic_info = {
                    'Name': dist.metadata['Name'],
                    'Version': dist.version,
                    'path': str(dist._path.resolve()),
                    'install_type': context_info.get('install_type', 'unknown'),
                    'owner_package': context_info.get('owner_package'),
                    'redis_key': f"(not found in KB: {key})",
                }
                basic_info['is_active'] = (basic_info['Version'] == active_version_str and basic_info['install_type'] == 'active')
                found_installations.append(basic_info)

        return found_installations
        
    def smart_uninstall(self, packages: List[str], force: bool=False, install_type: Optional[str]=None) -> int:
        if not self._connect_cache():
            return 1
        self._heal_conda_environment()
        self._synchronize_knowledge_base_with_reality()
        core_deps = _get_core_dependencies()

        for pkg_spec in packages:
            safe_print(_('\nProcessing uninstall for: {}').format(pkg_spec))
            pkg_name, specific_version = self._parse_package_spec(pkg_spec)
            c_name = canonicalize_name(pkg_name)
            all_installations_found = self._find_package_installations(c_name)

            to_uninstall_options = []
            for inst in all_installations_found:
                if inst.get('install_type') in ['active', 'bubble']:
                    to_uninstall_options.append(inst)
                else:
                    owner = inst.get('owner_package', 'another package')
                    safe_print(f"   - 🛡️  Skipping {inst.get('Name')} v{inst.get('Version')} ({inst.get('install_type')}): It is a protected dependency of '{owner}'.")

            if not to_uninstall_options:
                safe_print(_("✅ No user-managed installations of '{}' found to uninstall.").format(pkg_name))
                continue

            to_uninstall = to_uninstall_options

            if specific_version:
                to_uninstall = [inst for inst in to_uninstall if inst.get('Version') == specific_version]
            elif not force and len(to_uninstall_options) > 1:
                safe_print(_("Found multiple uninstallable versions for '{}':").format(pkg_name))
                numbered = [{'index': i + 1, 'installation': inst} for i, inst in enumerate(to_uninstall_options)]
                for item in numbered:
                    safe_print(_('  {}) v{} ({})').format(item['index'], item['installation'].get('Version'), item['installation'].get('install_type')))
                try:
                    choice = input(_("🤔 Enter numbers to uninstall (e.g., '1,2'), 'all', or press Enter to cancel: ")).lower().strip()
                    if not choice:
                        safe_print(_('🚫 Uninstall cancelled.'))
                        continue
                    indices = {int(idx.strip()) for idx in choice.split(',')} if choice != 'all' else {item['index'] for item in numbered}
                    to_uninstall = [item['installation'] for item in numbered if item['index'] in indices]
                except (ValueError, KeyboardInterrupt, EOFError):
                    safe_print(_('\n🚫 Uninstall cancelled.'))
                    continue

            if not to_uninstall:
                safe_print(_('🤷 No versions selected for uninstallation.'))
                continue

            final_to_uninstall = [
                item for item in to_uninstall
                if not (item.get('install_type') == 'active' and (canonicalize_name(item.get('Name')) in core_deps or canonicalize_name(item.get('Name')) == 'omnipkg'))
            ]

            if len(final_to_uninstall) != len(to_uninstall):
                safe_print(_('⚠️  Skipped one or more protected core packages.'))

            if not final_to_uninstall:
                safe_print(_('🤷 No versions remaining to uninstall after protection checks.'))
                continue

            safe_print(_("\nPreparing to remove {} installation(s) for '{}':").format(len(final_to_uninstall), c_name))
            for item in final_to_uninstall:
                safe_print(_('  - v{} ({})').format(item.get('Version', '?'), item.get('install_type', 'unknown')))

            proceed = force or input(_('🤔 Are you sure you want to proceed? (y/N): ')).lower().strip() == 'y'
            if not proceed:
                safe_print(_('🚫 Uninstall cancelled.'))
                continue

            # --- YOUR SUPERIOR LOGIC STARTS HERE ---

            # 1. Perform all physical uninstalls first.
            redis_keys_of_deleted_items = []
            for item in final_to_uninstall:
                item_type = item.get('install_type')
                item_name = item.get('Name')
                item_version = item.get('Version')
                # --- THIS IS THE DEFENSIVE FIX ---
                item_path_str = item.get('path') # Get the path safely

                if item_type == 'active':
                    safe_print(_("🗑️ Uninstalling '{}' from main environment via pip...").format(item_name))
                    self._run_pip_uninstall([item_name])
                # Check if the path is a valid string before trying to use it
                elif item_type == 'bubble' and item_path_str and isinstance(item_path_str, str):
                    # The path from the KB is to the .dist-info directory.
                    # The directory we need to delete is its PARENT.
                    dist_info_path = Path(item_path_str)
                    bubble_dir = dist_info_path.parent

                    # A critical sanity check to ensure we're deleting the correct directory.
                    expected_bubble_name = f"{canonicalize_name(item_name)}-{item_version}"
                    
                    if bubble_dir.name == expected_bubble_name and bubble_dir.is_dir():
                        safe_print(_('🗑️  Deleting bubble directory: {}').format(bubble_dir))
                        shutil.rmtree(bubble_dir, ignore_errors=True)
                    else:
                        # This else block is for logging a failure, it's good to keep.
                        safe_print(f"   ⚠️ Path mismatch or directory not found, skipping filesystem deletion for {item_name}=={item_version}.")
                        safe_print(f"      - Expected Bubble Name: {expected_bubble_name}")
                        safe_print(f"      - Found Bubble Name:    {bubble_dir.name}")
                        safe_print(f"      - Path Being Checked:   {bubble_dir}")
                # --- END OF FIX ---
                else:
                    # This branch handles cases where the path is missing from a broken KB entry
                    safe_print(f"   ⚠️ Could not determine path for {item_name}=={item_version} from KB. Skipping filesystem deletion.")

                redis_key = item.get('redis_key')
                if redis_key and 'unknown' not in redis_key:
                    redis_keys_of_deleted_items.append(item)

            # 2. Rescan the filesystem to get the new ground truth.
            safe_print(_('🧹 Verifying state and cleaning knowledge base...'))
            post_deletion_installations = self._find_package_installations(c_name)
            redis_keys_still_on_disk = {inst.get('redis_key') for inst in post_deletion_installations if inst.get('redis_key')}

            # 3. Surgically remove only the KB entries for items that are truly gone.
            for item in redis_keys_of_deleted_items:
                redis_key = item.get('redis_key')
                if redis_key not in redis_keys_still_on_disk:
                    if self.cache_client.delete(redis_key):
                        instance_id = redis_key.split(':')[-1]
                        safe_print(f"   -> ✅ Removed KB entry for physically deleted instance: {instance_id}")

            # 4. Update package-level info based on the final ground truth.
            final_versions_on_disk = {inst.get('Version') for inst in post_deletion_installations}
            versions_to_check = {item.get('Version') for item in final_to_uninstall}

            for version in versions_to_check:
                if version not in final_versions_on_disk:
                    safe_print(f"   -> Last instance of v{version} removed. Updating package version list.")
                    main_key = f'{self.redis_key_prefix}{c_name}'
                    versions_set_key = f'{main_key}:installed_versions'
                    self.cache_client.srem(versions_set_key, version)

            if not post_deletion_installations:
                safe_print(f"   -> No installations of '{c_name}' remain. Removing package from KB index.")
                main_key = f'{self.redis_key_prefix}{c_name}'
                self.cache_client.delete(main_key, f'{main_key}:installed_versions')
                self.cache_client.srem(f'{self.redis_env_prefix}index', c_name)
            
            safe_print(_('✅ Uninstallation complete.'))
            self._save_last_known_good_snapshot()

        return 0

    def revert_to_last_known_good(self, force: bool=False):
        """Compares the current env to the last snapshot and restores it."""
        if not self._connect_cache():
            return 1
        snapshot_key = f'{self.redis_key_prefix}snapshot:last_known_good'
        snapshot_data = self.cache_client.get(snapshot_key)
        if not snapshot_data:
            safe_print(_("❌ No 'last known good' snapshot found. Cannot revert."))
            safe_print(_('   Run an `omnipkg install` or `omnipkg uninstall` command to create one.'))
            return 1
        safe_print(_('⚖️  Comparing current environment to the last known good snapshot...'))
        snapshot_state = json.loads(snapshot_data)
        current_state = self.get_installed_packages(live=True)
        snapshot_keys = set(snapshot_state.keys())
        current_keys = set(current_state.keys())
        to_install = ['{}=={}'.format(pkg, ver) for pkg, ver in snapshot_state.items() if pkg not in current_keys]
        to_uninstall = [pkg for pkg in current_keys if pkg not in snapshot_keys]
        to_fix = [f'{pkg}=={snapshot_state[pkg]}' for pkg in snapshot_keys & current_keys if snapshot_state[pkg] != current_state[pkg]]
        if not to_install and (not to_uninstall) and (not to_fix):
            safe_print(_('✅ Your environment is already in the last known good state. No action needed.'))
            return 0
        safe_print(_('\n📝 The following actions will be taken to restore the environment:'))
        if to_uninstall:
            safe_print(_('  - Uninstall: {}').format(', '.join(to_uninstall)))
        if to_install:
            safe_print(_('  - Install: {}').format(', '.join(to_install)))
        if to_fix:
            safe_print(_('  - Fix Version: {}').format(', '.join(to_fix)))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                safe_print(_('🚫 Revert cancelled.'))
                return 1
        safe_print(_('\n🚀 Starting revert operation...'))
        original_strategy = self.config.get('install_strategy', 'multiversion')
        strategy_changed = False
        try:
            if original_strategy != 'latest-active':
                safe_print(_('   ⚙️  Temporarily setting install strategy to latest-active for revert...'))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', 'latest-active'], capture_output=True, text=True, check=True)
                    strategy_changed = True
                    safe_print(_('   ✅ Install strategy temporarily set to latest-active'))
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                except Exception as e:
                    safe_print(_('   ⚠️  Failed to set install strategy to latest-active: {}').format(e))
                    safe_print(_('   ℹ️  Continuing with current strategy: {}').format(original_strategy))
            else:
                safe_print(_('   ℹ️  Install strategy already set to latest-active'))
            if to_uninstall:
                self.smart_uninstall(to_uninstall, force=True)
            packages_to_install = to_install + to_fix
            if packages_to_install:
                self.smart_install(packages_to_install)
            safe_print(_('\n✅ Environment successfully reverted to the last known good state.'))
            return 0
        finally:
            if strategy_changed and original_strategy != 'latest-active':
                safe_print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', original_strategy], capture_output=True, text=True, check=True)
                    safe_print(_('   ✅ Install strategy restored to: {}').format(original_strategy))
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                except Exception as e:
                    safe_print(_('   ⚠️  Failed to restore install strategy to {}: {}').format(original_strategy, e))
                    safe_print(_('   💡 You may need to manually restore it with: omnipkg config set install_strategy {}').format(original_strategy))
            elif not strategy_changed:
                safe_print(_('   ℹ️  Install strategy unchanged: {}').format(original_strategy))

    def _check_package_satisfaction(self, packages: List[str], strategy: str) -> dict:
        """
        ### THE DEFINITIVE INSTANCE-AWARE FIX ###
        Checks if a list of requirements is satisfied by searching for ANY valid,
        non-nested instance in the Redis Knowledge Base.
        """
        satisfied_specs = set()
        needs_install_specs = []

        for package_spec in packages:
            is_satisfied = False
            try:
                pkg_name, requested_version = self._parse_package_spec(package_spec)
                if not requested_version:
                    # If no version is specified, we must assume it needs installation
                    # to resolve the latest compatible version.
                    needs_install_specs.append(package_spec)
                    continue

                c_name = canonicalize_name(pkg_name)
                
                # --- THIS IS THE NEW, CORRECT LOGIC ---
                # Instead of checking one key, we now find ALL instances for this version.
                all_installations = self._find_package_installations(c_name)
                
                # Filter for the specific version we're looking for.
                matching_version_installations = [
                    inst for inst in all_installations if inst.get('Version') == requested_version
                ]

                if not matching_version_installations:
                    needs_install_specs.append(package_spec)
                    continue

                # Now, check if ANY of these instances satisfy the requirement.
                for instance in matching_version_installations:
                    install_type = instance.get('install_type')
                    
                    # A requirement is satisfied if it's 'active'.
                    if install_type == 'active':
                        is_satisfied = True
                        break # Found the best case, no need to check others.
                        
                    # For stable-main, a 'bubble' is also considered satisfied.
                    if strategy == 'stable-main' and install_type == 'bubble':
                        is_satisfied = True
                        # Don't break here, an 'active' one might still be found.
                # --- END OF NEW LOGIC ---

                if is_satisfied:
                    satisfied_specs.add(package_spec)
                else:
                    needs_install_specs.append(package_spec)

            except Exception:
                needs_install_specs.append(package_spec)
        
        return {
            'all_satisfied': len(needs_install_specs) == 0,
            'satisfied': sorted(list(satisfied_specs)),
            'needs_install': needs_install_specs
        }

    def get_package_info(self, package_name: str, version: str) -> Optional[Dict]:
        if not self.cache_client:
            self._connect_cache()
        main_key = f'{self.redis_key_prefix}{package_name.lower()}'
        if version == 'active':
            version = self.cache_client.hget(main_key, 'active_version')
            if not version:
                return None
        version_key = f'{main_key}:{version}'
        return self.cache_client.hgetall(version_key)

    import time
    import platform
    import sys
    import time
    from pathlib import Path

    def switch_active_python(self, version: str) -> int:
        """
        Switch the active Python context to the specified version.
        
        Windows-specific behavior:
        - Always performs full swap (no fast path) due to DLL caching issues
        - Includes additional cleanup steps to prevent import errors
        
        Linux/macOS behavior:
        - Uses fast path optimization when already in correct context
        """
        start_time = time.perf_counter_ns()
        is_windows = platform.system() == 'Windows'

        # === TIER 1: THE NANOSECOND FAST PATH (In-Memory Check) ===
        # SKIP THIS ON WINDOWS - Windows DLL caching makes fast path unreliable
        if not is_windows:
            configured_python = self.config_manager.get('python_executable')
            if configured_python:
                path_str = str(configured_python)
                # Direct string checks - faster than regex!
                if f'cpython-{version}' in path_str or path_str.endswith(f'python{version}'):
                    elapsed_ns = time.perf_counter_ns() - start_time
                    elapsed_ms = elapsed_ns / 1_000_000
                    safe_print(_('⚡ Already in Python {} context. No swap needed!').format(version))
                    safe_print(f'   ⏱️  Detection time: {elapsed_ms:.3f}ms ({elapsed_ns:,} ns)')
                    return 0

        # === TIER 2: THE MILLISECOND FAST PATH (Authoritative Check) ===
        # SKIP THIS ON WINDOWS - Same reason as above
        if not is_windows:
            target_context = f'py{version}'
            if self.current_python_context == target_context:
                elapsed_ns = time.perf_counter_ns() - start_time
                elapsed_ms = elapsed_ns / 1_000_000
                safe_print(_('⚡ Already in Python {} context. No swap needed!').format(version))
                safe_print(f'   ⏱️  Detection time: {elapsed_ms:.3f}ms ({elapsed_ns:,} ns)')
                return 0

        # === THE SWAP PATH (Always executed on Windows, conditional on Linux/macOS) ===
        safe_print(_('🐍 Switching active Python context to version {}...').format(version))

        # Windows-specific: Force refresh interpreter registry
        if is_windows:
            safe_print(_('   🪟 Windows detected: Forcing interpreter registry refresh...'))
            self.interpreter_manager.refresh_registry()

        managed_interpreters = self.interpreter_manager.list_available_interpreters()
        target_interpreter_path = managed_interpreters.get(version)
        
        # CRITICAL FIX: If not found, try refreshing the registry once (for non-Windows)
        if not target_interpreter_path and not is_windows:
            safe_print(_('   - Python {} not found in cache, refreshing registry...').format(version))
            self.interpreter_manager.refresh_registry()
            managed_interpreters = self.interpreter_manager.list_available_interpreters()
            target_interpreter_path = managed_interpreters.get(version)
        
        if not target_interpreter_path:
            safe_print(f"   ❌ Python {version} not found in the registry.")
            safe_print(f"   - Available versions: {', '.join(managed_interpreters.keys())}")
            return 1

        safe_print(_('   ✅ Managed interpreter: {}').format(target_interpreter_path))
        new_paths = self.config_manager._get_paths_for_interpreter(str(target_interpreter_path))
        if not new_paths:
            safe_print(f'❌ Could not determine paths for Python {version}.')
            return 1
        
        safe_print(_('   🔧 Updating configuration...'))
        self.config_manager.set('python_executable', new_paths['python_executable'])
        self.config_manager.set('site_packages_path', new_paths['site_packages_path'])
        self.config_manager.set('multiversion_base', new_paths['multiversion_base'])
        
        # Windows-specific: Clear module cache to prevent stale imports
        if is_windows:
            safe_print(_('   🧹 Windows: Clearing module cache...'))
            self._clear_windows_module_cache()
        
        safe_print(_('   🔗 Updating symlinks...'))
        venv_path = self.config_manager.venv_path
        try:
            self.config_manager._update_default_python_links(venv_path, Path(target_interpreter_path))
        except Exception as e:
            safe_print(_('   ❌ Symlink update failed: {}').format(e))

        elapsed_ns = time.perf_counter_ns() - start_time
        elapsed_ms = elapsed_ns / 1_000_000
        
        safe_print(_('\n🎉 Switched to Python {}!').format(version))
        safe_print(_('   To activate in your shell, run: source {}/bin/activate').format(venv_path))
        safe_print(_('\n   Just kidding, omnipkg handled it for you automatically!'))
        safe_print(f'   ⏱️  Swap completed in: {elapsed_ms:.3f}ms')
        
        return 0

    def _clear_windows_module_cache(self):
        """
        Windows-specific: Clear cached modules that might cause import conflicts.
        
        This helps prevent "ImportError: cannot import name 'text_encoding' from 'io'"
        and similar errors caused by Windows DLL caching across Python version swaps.
        """
        import gc
        
        # List of critical modules to remove from sys.modules if present
        # These are the ones most likely to cause issues with stale DLL references
        critical_modules = [
            'io', '_io', 
            'encodings', '_codecs',
            'sys', '_sys',
            # Add other problematic modules as discovered
        ]
        
        modules_cleared = []
        for module_name in critical_modules:
            if module_name in sys.modules:
                try:
                    del sys.modules[module_name]
                    modules_cleared.append(module_name)
                except Exception:
                    pass  # Some modules can't be removed, that's OK
        
        # Force garbage collection to clean up any remaining references
        gc.collect()
        
        if modules_cleared:
            safe_print(f'   📦 Cleared {len(modules_cleared)} cached modules: {", ".join(modules_cleared)}')
    
    def _find_best_version_for_spec(self, package_spec: str) -> Optional[str]:
        """
        Resolves a complex package specifier (e.g., 'numpy>=1.20,<1.22') to the
        latest compliant version by querying all available versions from PyPI.
        NOW RESPECTS CURRENT PYTHON CONTEXT!
        SUPPORTS PEP 508 environment markers (e.g., 'package>=1.0; python_version >= "3.8"')
        """
        from packaging.specifiers import SpecifierSet
        from packaging.version import parse as parse_version
        from packaging.markers import Marker

        safe_print(f"    -> Resolving complex specifier: '{package_spec}'")
        try:
            # 0. Split off environment markers if present (PEP 508)
            # Example: "aiohttp>=3.13.1; python_version >= '3.8'" -> "aiohttp>=3.13.1"
            if ';' in package_spec:
                spec_part, marker_part = package_spec.split(';', 1)
                package_spec = spec_part.strip()
                marker_str = marker_part.strip()
                safe_print(f"    -> Detected environment marker: '{marker_str}'")
                
                # Evaluate the marker for current Python version
                try:
                    marker = Marker(marker_str)
                    # This evaluates the marker against the current environment
                    if not marker.evaluate():
                        safe_print(f"    ⚠️  Environment marker not satisfied for current Python context")
                        safe_print(f"    -> Marker: {marker_str}")
                        safe_print(f"    -> Current Python: {self.current_python_context}")
                        # Could return None here, or continue anyway - your choice
                        # For now, let's continue and try to resolve anyway
                except Exception as e:
                    safe_print(f"    ⚠️  Could not evaluate environment marker: {e}")
            
            # 1. Parse package name and constraints
            match = re.match(r"^\s*([a-zA-Z0-9_.-]+)\s*(.*)", package_spec)
            if not match:
                safe_print(f"    ❌ Could not parse package name from '{package_spec}'")
                return None
            
            pkg_name = match.group(1).strip()
            spec_str = match.group(2).strip()
            
            if not spec_str:
                return self._get_latest_version_from_pypi(pkg_name)

            # 2. Fetch ALL versions from PyPI
            # 2. Let pip tell us what's actually compatible in the current Python context
            safe_print(f"    -> Using pip to find compatible versions for '{pkg_name}' in Python {self.current_python_context}...")
            safe_print(f"    -> Package spec with constraint: '{package_spec}'")

            # Use the test installation approach to get pip's answer
            test_result = self._test_install_to_get_compatible_version(package_spec)  # Pass the FULL spec!


            if not test_result:
                safe_print(f"    ❌ Pip could not find ANY compatible version of '{pkg_name}' for {self.current_python_context}")
                
                # Try to determine what Python version IS compatible
                safe_print(f"    🔍 Checking PyPI metadata to find compatible Python version...")
                try:
                    response = http_requests.get(f'https://pypi.org/pypi/{pkg_name}/json', timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        latest_version = data['info']['version']
                        requires_python = data['info'].get('requires_python')
                        
                        if requires_python:
                            from packaging.specifiers import SpecifierSet
                            spec = SpecifierSet(requires_python)
                            
                            # Find minimum Python version
                            min_py = self._get_package_minimum_python_from_spec(spec)
                            if min_py:
                                safe_print(f"    💡 {pkg_name} v{latest_version} requires Python {min_py}+")
                                raise NoCompatiblePythonError(
                                    package_name=pkg_name,
                                    package_version=latest_version,
                                    current_python=self.current_python_context,
                                    compatible_python=min_py,
                                    message=f"Package '{pkg_name}' requires Python {min_py}, current is {self.current_python_context}"
                                )
                except NoCompatiblePythonError:
                    raise
                except Exception as e:
                    safe_print(f"    ⚠️ Could not determine compatible Python version: {e}")
                
                return None

            # 3. Now check if this version satisfies the original constraint
            safe_print(f"    ✅ Pip found compatible version: {test_result}")
            specifier = SpecifierSet(spec_str)

            if not specifier.contains(test_result):
                safe_print(f"    ⚠️ Compatible version {test_result} does NOT satisfy constraint '{spec_str}'")
                safe_print(f"    → This means NO version matching '{spec_str}' exists for Python {self.current_python_context}")
                
                # Trigger quantum healing - need different Python version
                safe_print(f"    🔍 Finding what Python version is needed...")
                try:
                    response = http_requests.get(f'https://pypi.org/pypi/{pkg_name}/json', timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        all_releases = data['releases']
                        
                        # Find the latest version that matches the spec
                        valid_versions = list(specifier.filter(all_releases.keys()))
                        if valid_versions:
                            latest_matching = sorted(valid_versions, key=parse_version, reverse=True)[0]
                            safe_print(f"    💡 Latest version matching '{spec_str}': {latest_matching}")
                            
                            # Find what Python it needs
                            version_files = all_releases.get(latest_matching, [])
                            min_python_from_wheels = None
                            
                            for file_info in version_files:
                                if file_info.get('packagetype') == 'bdist_wheel':
                                    filename = file_info.get('filename', '')
                                    wheel_match = re.search(r'-cp(\d)(\d+)-', filename)
                                    if wheel_match:
                                        wheel_py_major = int(wheel_match.group(1))
                                        wheel_py_minor = int(wheel_match.group(2))
                                        if not min_python_from_wheels or (wheel_py_major, wheel_py_minor) < min_python_from_wheels:
                                            min_python_from_wheels = (wheel_py_major, wheel_py_minor)
                            
                            if min_python_from_wheels:
                                required_python = f"{min_python_from_wheels[0]}.{min_python_from_wheels[1]}"
                                compatible_python_clean = min_py.replace('py', '') if min_py.startswith('py') else min_py

                                raise NoCompatiblePythonError(
                                    package_name=pkg_name,
                                    package_version=latest_version,
                                    current_python=self.current_python_context,
                                    compatible_python=compatible_python_clean,  # Now "3.11" not "py3.11"
                                    message=f"Package '{pkg_name}' requires Python {compatible_python_clean}, current is {self.current_python_context}"
                                )
                except NoCompatiblePythonError:
                    raise
                except Exception as e:
                    safe_print(f"    ⚠️ Could not determine required Python version: {e}")
                
                return None

            # 4. Success! We have a compatible version that satisfies the constraint
            resolved_spec = f"{pkg_name}=={test_result}"
            safe_print(f"    ✅ Resolved '{package_spec}' to '{resolved_spec}'")
            return resolved_spec

        except NoCompatiblePythonError:
            raise  # Re-raise to trigger quantum healing
        except http_requests.RequestException as e:
            safe_print(f"    ❌ Network error while resolving '{pkg_name}': {e}")
            return None
        except Exception as e:
            safe_print(f"    ❌ Failed to resolve complex specifier '{package_spec}': {e}")
            return None

    def _resolve_package_versions(self, packages: List[str]) -> List[str]:
        """
        (UPGRADED) Takes a list of packages and ensures every entry has an
        explicit '==' version. It now intelligently dispatches to the correct
        resolver based on the specifier complexity.
        """
        safe_print(_('🔎 Resolving package versions via PyPI API...'))
        resolved_packages = []
        
        # Define characters that indicate a complex specifier
        complex_spec_chars = ['<', '>', '~', '!', ',']

        for pkg_spec in packages:
            safe_print(f"🔍 DEBUG: Processing '{pkg_spec}'")
            
            # Case 1: Already has an exact version. Keep it.
            if '==' in pkg_spec:
                safe_print(f"    → Case 1: Already pinned")
                resolved_packages.append(pkg_spec)
                continue

            # Case 2: Has a complex specifier
            safe_print(f"    → Checking for complex spec chars: {complex_spec_chars}")
            if any(op in pkg_spec for op in complex_spec_chars):
                safe_print(f"    → Case 2: Complex specifier detected! Calling _find_best_version_for_spec")
                resolved = self._find_best_version_for_spec(pkg_spec)
                if resolved:
                    resolved_packages.append(resolved)
                else:
                    safe_print(f"    ⚠️  Could not resolve '{pkg_spec}'. Skipping.")
                continue
            
            safe_print(f"    → Case 3: Simple package name")
            safe_print(f"    → Case 3: Simple package name")
            pkg_name, _version = self._parse_package_spec(pkg_spec)  # Unpack the tuple directly


            safe_print(_("    -> Finding latest version for '{}'...").format(pkg_name))
            target_version = self._get_latest_version_from_pypi(pkg_name)
            if target_version:
                new_spec = f'{pkg_name}=={target_version}'
                safe_print(_("    ✅ Resolved '{}' to '{}'").format(pkg_name, new_spec))
                resolved_packages.append(new_spec)
            else:
                safe_print(_("    ❌ CRITICAL: Could not resolve a version for '{}' via PyPI.").format(pkg_name))
                raise ValueError(f"Package '{pkg_name}' not found or could not be resolved.")
        
        return resolved_packages

    def _find_python_executable_in_dir(self, directory: Path) -> Optional[Path]:
        """Find the Python executable in a directory, checking common locations."""
        # Check standard locations first
        if platform.system() == "Windows":
            search_paths = [
                directory / 'python.exe',
                directory / 'Scripts' / 'python.exe',
            ]
        else:
            search_paths = [
                directory / 'bin' / 'python3',
                directory / 'bin' / 'python',
            ]

        for path in search_paths:
            if path.is_file() and os.access(path, os.X_OK):
                return path

        # If not found, do a broader search
        for exe in directory.rglob('python.exe' if platform.system() == "Windows" else 'python3'):
            if exe.is_file() and os.access(exe, os.X_OK):
                return exe
        
        return None

    def _get_file_list_for_packages_live(self, package_names: List[str]) -> Dict[str, List[str]]:
        """
        (ROBUST VERSION) Runs a subprocess to get the authoritative file list.
        The script is now hardened to handle corrupted metadata and provides
        detailed error reporting if it fails on a specific package.
        """
        if not package_names:
            return {}
        
        python_exe = self.config.get('python_executable', sys.executable)
        
        # This script is now much more robust.
        script = f'''
import sys, json, traceback

try:
    import importlib.metadata as metadata
except ImportError:
    import importlib_metadata as metadata

results = {{}}
current_pkg = "None"
try:
    for pkg_name in {package_names!r}:
        current_pkg = pkg_name
        try:
            dist = metadata.distribution(pkg_name)
            if dist.files:
                file_list = []
                for file_path_obj in dist.files:
                    # Process each file individually to prevent one bad
                    # entry from failing the entire package.
                    try:
                        abs_path = dist.locate_file(file_path_obj)
                        if abs_path and abs_path.is_file():
                            file_list.append(str(abs_path))
                    except Exception:
                        continue # Silently skip broken file entries
                results[pkg_name] = file_list
            else:
                results[pkg_name] = []
        except metadata.PackageNotFoundError:
            results[pkg_name] = [] # Package not found is not an error
        except Exception:
            # Any other exception for a single package is also not fatal.
            # We just record that we couldn't get its files.
            results[pkg_name] = []

except Exception as e:
    # THIS IS THE CRITICAL PART: If the loop itself crashes,
    # we now know exactly which package was being processed.
    error_info = {{
        "error": "Omnipkg subprocess failed while processing package files.",
        "failing_package": current_pkg,
        "exception_type": type(e).__name__,
        "exception_message": str(e),
        "traceback": traceback.format_exc()
    }}
    # Print the diagnostic data to stderr so it can be captured.
    print(json.dumps(error_info), file=sys.stderr)
    sys.exit(1) # Exit with an error code

# If we get here, everything was successful.
print(json.dumps(results))
'''
        
        try:
            cmd = [python_exe, '-I', '-c', script]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            # The subprocess failed. Now we check stderr for our detailed JSON error.
            try:
                error_data = json.loads(e.stderr)
                safe_print(f"   - ⚠️  Subprocess failed while processing package '{error_data.get('failing_package')}'.")
                safe_print(f"   -   Error: {error_data.get('exception_type')}: {error_data.get('exception_message')}")
            except (json.JSONDecodeError, KeyError):
                # If stderr wasn't our JSON, print the raw error.
                safe_print('   - ⚠️  Could not get file list from live environment. Subprocess failed with a non-JSON error:')
                safe_print(f"   -   STDERR: {e.stderr.strip()}")
            return {name: [] for name in package_names}
        except Exception as e:
            safe_print(f'   - ⚠️  An unexpected error occurred while running the file-list subprocess: {e}')
            return {name: [] for name in package_names}
        
    def _get_import_candidates(self, package_name: str) -> List[str]:
        """
        Authoritatively finds import candidates by reading top_level.txt from the
        installed package's .dist-info directory.
        """
        try:
            dist = importlib.metadata.distribution(package_name)
            
            # This is the most reliable way to find the importable names.
            if dist.read_text('top_level.txt'):
                return [line.strip() for line in dist.read_text('top_level.txt').split('\n') if line.strip()]

        except importlib.metadata.PackageNotFoundError:
            pass # Fall through to heuristics
        except Exception as e:
            safe_print(f"   - Warning: Could not read top_level.txt for {package_name}: {e}")

        # Fallback to smart name mangling if top_level.txt is missing or fails.
        candidates = {package_name.replace('-', '_'), package_name.lower()}
        return sorted(list(candidates))

    # In omnipkg/core.py, inside the omnipkg class

    def _run_post_install_import_test(self, package_name: str, install_dir_override: Optional[Path] = None) -> bool:
        """
        Runs a simple 'import' test in a subprocess to verify an installation.
        Can now target a specific installation directory.
        """
        target_desc = f"in sandbox '{install_dir_override}'" if install_dir_override else "in main environment"
        safe_print(f"   🧪 Verifying installation with import test for: {package_name} {target_desc}")
        
        try:
            import_names = self._get_import_candidates(package_name)
            python_exe = self.config.get('python_executable', sys.executable)
            
            for import_name in import_names:
                script_lines = ["import sys"]
                if install_dir_override:
                    script_lines.append(f"sys.path.insert(0, r'{str(install_dir_override)}')")
                script_lines.append(f"import {import_name}")

                script = "; ".join(script_lines)
                cmd = [python_exe, "-c", script]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    safe_print(f"      ✅ Import test passed for '{import_name}'")
                    return True

            safe_print(f"      ❌ All import attempts failed for package '{package_name}'. The package is likely broken.")
            return False
        except Exception as e:
            safe_print(f"      ❌ An unexpected error occurred during the import test: {e}")
            return False

    def _run_historical_install_fallback(self, target_pkg, target_ver, target_directory_override: Optional[Path] = None):
        """
        --- [MODIFIED] ---
        Orchestrator for the Dependency Time Machine logic, run as an automatic fallback.
        This method now accepts a target_directory_override to ensure installations are isolated.
        """
        safe_print(f"   - Attempting to rebuild {target_pkg}=={target_ver} from the past...")
        
        release_date = self._get_historical_release_date(target_pkg, target_ver)
        if not release_date:
            safe_print('   - ❌ Failed to get release date. Time Machine cannot proceed.')
            return False

        dep_names = self._get_historical_dependency_names(target_pkg, target_ver)
        if dep_names is None: # Explicitly check for None in case of error
            safe_print('   - ❌ Failed to determine dependencies. Time Machine cannot proceed.')
            return False

        if not dep_names:
            safe_print('   - ℹ️ No dependencies found. Attempting a direct simple install.')
            # --- [FIX] Pass the override to the execution function ---
            return self._execute_historical_install(target_pkg, target_ver, {}, target_directory_override=target_directory_override)

        historical_versions = self._find_historical_versions(dep_names, release_date)
        
        if historical_versions is not None:
            return self._execute_historical_install(
                target_pkg, target_ver, historical_versions, target_directory_override=target_directory_override
            )
        else:
            safe_print('   - ❌ Could not resolve any historical dependencies. Time Machine failed.')
            return False

    # Add these three functions right BEFORE _execute_historical_install (around line 8588)
# They should be methods of the omnipkg class

    def _get_platform_tags(self) -> Dict[str, any]:
        """Get current platform information for wheel matching."""
        system = platform.system().lower()
        machine = platform.machine().lower()
        py_version = f"{sys.version_info.major}{sys.version_info.minor}"
        
        # Python implementation and ABI
        py_impl = 'cp' if platform.python_implementation() == 'CPython' else 'py'
        abi_tag = f"cp{py_version}" if py_impl == 'cp' else 'none'
        
        # Platform tag mapping
        if system == 'linux':
            # Check for musl vs glibc
            try:
                import subprocess
                ldd_output = subprocess.check_output(['ldd', '--version'], stderr=subprocess.STDOUT, text=True)
                is_musl = 'musl' in ldd_output.lower()
            except:
                is_musl = False
            
            if machine in ('x86_64', 'amd64'):
                platform_tags = ['manylinux2014_x86_64', 'manylinux2010_x86_64', 'manylinux1_x86_64', 'linux_x86_64']
            elif machine in ('aarch64', 'arm64'):
                platform_tags = ['manylinux2014_aarch64', 'manylinux_2_17_aarch64', 'linux_aarch64']
            elif machine.startswith('arm'):
                platform_tags = ['linux_armv7l', 'linux_armv6l']
            else:
                platform_tags = [f'linux_{machine}']
                
            if is_musl:
                platform_tags = [f'musllinux_1_1_{machine}'] + platform_tags
                
        elif system == 'darwin':
            if machine == 'arm64':
                platform_tags = ['macosx_11_0_arm64', 'macosx_10_9_universal2']
            else:
                platform_tags = ['macosx_10_9_x86_64', 'macosx_10_6_intel']
                
        elif system == 'windows':
            if machine in ('amd64', 'x86_64'):
                platform_tags = ['win_amd64']
            else:
                platform_tags = ['win32']
        else:
            platform_tags = ['any']
        
        return {
            'py_version': py_version,
            'py_impl': py_impl,
            'abi_tag': abi_tag,
            'platform_tags': platform_tags,
            'system': system
        }

    def _score_wheel_compatibility(self, filename: str, platform_info: Dict) -> int:
        """
        Score a wheel filename for compatibility with current platform.
        Higher score = better match. Returns 0 if incompatible.
        """
        # Parse wheel filename: {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
        parts = filename.replace('.whl', '').split('-')
        if len(parts) < 5:
            return 0
        
        # Extract tags (handle optional build tag)
        if len(parts) == 5:
            _, _, python_tag, abi_tag, platform_tag = parts
        else:
            # Has build tag
            _, _, _, python_tag, abi_tag, platform_tag = parts
        
        score = 0
        py_version = platform_info['py_version']
        py_impl = platform_info['py_impl']
        current_abi = platform_info['abi_tag']
        
        # Check Python version compatibility
        if python_tag == f"{py_impl}{py_version}":
            score += 1000  # Exact match
        elif python_tag == f"py{sys.version_info.major}":
            score += 500   # Major version match (py3)
        elif python_tag == 'py2.py3':
            score += 250   # Universal Python 2/3
        elif python_tag == 'py3':
            score += 200   # Python 3 generic
        elif re.match(rf"{py_impl}\d+", python_tag):
            # Check if it's compatible with our version
            tag_version = int(python_tag.replace(py_impl, ''))
            if tag_version <= int(py_version):
                score += 100 - (int(py_version) - tag_version) * 10
            else:
                return 0  # Too new
        else:
            return 0  # Incompatible Python version
        
        # Check ABI compatibility
        if abi_tag == current_abi:
            score += 100  # Exact ABI match
        elif abi_tag == 'none':
            score += 50   # Pure Python wheel
        elif abi_tag.startswith('abi3'):
            score += 75   # Stable ABI
        else:
            # ABI mismatch for binary wheels
            if platform_info['system'] != 'any':
                return 0
        
        # Check platform compatibility
        platform_score = 0
        if platform_tag == 'any':
            platform_score = 25  # Universal platform (pure Python)
        else:
            for idx, compatible_platform in enumerate(platform_info['platform_tags']):
                if platform_tag == compatible_platform:
                    # Earlier in list = more specific/preferred
                    platform_score = 200 - (idx * 20)
                    break
        
        if platform_score == 0:
            return 0  # Incompatible platform
        
        score += platform_score
        
        return score
    
    def _select_best_file(self, files: List[Dict], platform_info: Dict) -> Optional[Dict]:
        """
        Select the best file (wheel or sdist) for the current platform.
        Returns the file dict or None if no compatible file found.
        """
        wheels = [f for f in files if f['packagetype'] == 'bdist_wheel']
        sdists = [f for f in files if f['packagetype'] == 'sdist']
        
        if wheels:
            # Score all wheels
            scored_wheels = []
            for wheel in wheels:
                score = self._score_wheel_compatibility(wheel['filename'], platform_info)
                if score > 0:
                    scored_wheels.append((score, wheel))
            
            if scored_wheels:
                # Return highest scoring wheel
                scored_wheels.sort(reverse=True, key=lambda x: x[0])
                return scored_wheels[0][1]
        
        # Fallback to source distribution
        if sdists:
            return sdists[0]
        
        return None

    def _execute_historical_install(self, target_pkg, target_ver, historical_versions, target_directory_override: Optional[Path] = None):
        install_target_desc = f"temporary sandbox ('{target_directory_override}')" if target_directory_override else "main environment"
        # Get platform information once
        platform_info = self._get_platform_tags()
        safe_print(f"      - 🖥️  Platform: Python {platform_info['py_version']} on {platform_info['system']} ({', '.join(platform_info['platform_tags'][:2])})")
        safe_print(f"      - 🎯 Install Target: {install_target_desc}")

        # Stage 1: Ancient setuptools
        safe_print('\n      - ⚙️ Stage 1: Installing ANCIENT build system (setuptools with Feature support)...')
        build_system_fix = ["setuptools==40.8.0", "wheel"]
        
        return_code, _output = self._run_pip_install(
            build_system_fix, 
            force_reinstall=True,
            target_directory=None
        )
        
        if return_code != 0:
            safe_print("      - ❌ Stage 1 failed.")
            return False
        safe_print("      - ✅ Stage 1 complete: Ancient build system (setuptools 40.x) is now active.")

        # Stage 2: Download and install - BUT DO ITSDANGEROUS LAST
        safe_print('\n      - ⚙️ Stage 2: Downloading and installing historical package files...')
        
        itsdangerous_file = None  # Save this for last
        
        for pkg_name, pkg_ver in historical_versions.items():
            # Skip itsdangerous for now
            if pkg_name == 'itsdangerous' and pkg_ver == '0.24':
                url = f"https://pypi.org/pypi/{pkg_name}/{pkg_ver}/json"
                response = http_requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                files = data.get('urls', [])
                sdist_file = next((f for f in files if f['packagetype'] == 'sdist'), None)
                if sdist_file:
                    itsdangerous_file = (sdist_file['url'], sdist_file['filename'])
                continue
            
            url = f"https://pypi.org/pypi/{pkg_name}/{pkg_ver}/json"
            try:
                response = http_requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                files = data.get('urls', [])
                if not files:
                    safe_print(f"      - ⚠️  No files found for {pkg_name}=={pkg_ver}")
                    continue
                    
                # Use smart selection
                file_to_download = self._select_best_file(files, platform_info)
                
                if not file_to_download:
                    safe_print(f"      - ⚠️  No suitable file found for {pkg_name}=={pkg_ver}")
                    continue
                
                download_url = file_to_download['url']
                filename = file_to_download['filename']
                
                safe_print(f"      - 📥 Downloading {filename}...")
                
                temp_file = Path(tempfile.gettempdir()) / filename
                file_response = http_requests.get(download_url, timeout=30)
                file_response.raise_for_status()
                temp_file.write_bytes(file_response.content)
                
                return_code, _output = self._run_pip_install(
                    [str(temp_file)],
                    force_reinstall=True,
                    target_directory=target_directory_override,
                    extra_flags=['--no-build-isolation', '--no-deps']
                )
                
                temp_file.unlink()
                
                if return_code != 0:
                    safe_print(f"      - ❌ Failed to install {pkg_name}=={pkg_ver}")
                    self._restore_modern_setuptools()
                    return False
                    
            except Exception as e:
                safe_print(f"      - ❌ Error processing {pkg_name}=={pkg_ver}: {e}")
                self._restore_modern_setuptools()
                return False
        
        # NOW install itsdangerous LAST to overwrite any modern version
        if itsdangerous_file:
            download_url, filename = itsdangerous_file
            safe_print(f"      - 📥 Downloading {filename} (installing LAST to ensure single-file version)...")
            
            temp_file = Path(tempfile.gettempdir()) / filename
            file_response = http_requests.get(download_url, timeout=30)
            file_response.raise_for_status()
            temp_file.write_bytes(file_response.content)
            
            safe_print(f"      - 🔧 Special handling for single-file itsdangerous 0.24...")
            import tarfile
            import shutil
            
            # Extract the tarball
            extract_dir = Path(tempfile.mkdtemp())
            with tarfile.open(temp_file, 'r:gz') as tar:
                tar.extractall(extract_dir)
            
            # Find and copy itsdangerous.py
            itsdangerous_py = list(extract_dir.rglob('itsdangerous.py'))
            if itsdangerous_py:
                target_path = Path(target_directory_override) if target_directory_override else Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
                
                # CRITICAL: Remove any modern itsdangerous package directory first
                itsdangerous_dir = target_path / 'itsdangerous'
                if itsdangerous_dir.exists() and itsdangerous_dir.is_dir():
                    safe_print(f"      - 🗑️  Removing modern itsdangerous package directory...")
                    shutil.rmtree(itsdangerous_dir)
                
                shutil.copy2(itsdangerous_py[0], target_path / 'itsdangerous.py')
                safe_print(f"      - ✅ Installed single-file itsdangerous.py (overwrote modern version)")
                
                # Create .dist-info
                dist_info_dir = target_path / 'itsdangerous-0.24.dist-info'
                dist_info_dir.mkdir(exist_ok=True)
                (dist_info_dir / 'METADATA').write_text(f"Name: itsdangerous\nVersion: 0.24\n")
                (dist_info_dir / 'top_level.txt').write_text('itsdangerous\n')
            
            shutil.rmtree(extract_dir)
            temp_file.unlink()
        
        safe_print("      - ✅ Stage 2 complete.")

        # Stage 3
        safe_print(f'\n      - ⚙️ Stage 3: Building {target_pkg}=={target_ver}...')
        target_spec = [f"{target_pkg}=={target_ver}"]
        
        return_code, _output = self._run_pip_install(
            target_spec,
            force_reinstall=True,
            target_directory=target_directory_override,
            extra_flags=['--no-build-isolation', '--no-deps']
        )
        
        self._restore_modern_setuptools()

        if return_code != 0:
            safe_print("      - ❌ Stage 3 failed.")
            return False
        safe_print("      - ✅ Stage 3 complete.")
        
        return True

    def _restore_modern_setuptools(self):
        """Restore modern setuptools"""
        safe_print("      - 🔄 Restoring modern setuptools...")
        self._run_pip_install(
            ["setuptools>=65.5.1"],
            force_reinstall=True,
            target_directory=None
        )

    def _get_historical_release_date(self, package_name, version):
        """Uses PyPI JSON API to get the release date for a specific version."""
        safe_print(f"      - Getting release date for {package_name}=={version}...")
        try:
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
            response = http_requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            # Get the upload time of the first file in the release, which is a reliable indicator
            upload_time = data['urls'][0]['upload_time_iso_8601']
            safe_print(f"      - ✓ Found release date: {upload_time}")
            return upload_time
        except (requests.exceptions.RequestException, KeyError, IndexError) as e:
            safe_print(f'      - ❌ Error fetching release date from PyPI: {e}')
            return None

    def _get_historical_dependency_names(self, package_name, version):
        """
        Creates a temp venv USING THE CORRECT PYTHON CONTEXT to discover dependencies.
        """
        safe_print('      - Getting actual dependencies via temporary real install...')
        temp_dir = tempfile.mkdtemp()
        venv_path = os.path.join(temp_dir, 'venv')
        
        # --- THIS IS THE CRITICAL FIX ---
        # Use the configured python executable, not the one running the script.
        # This prevents the Python 3.11 context from leaking into our 3.8 operation.
        configured_python_exe = self.config['python_executable']
        venv_python = os.path.join(venv_path, 'bin', 'python')
        
        try:
            # Create the venv using the correct Python interpreter
            subprocess.run([configured_python_exe, '-m', 'venv', venv_path], check=True, capture_output=True)
            
            # Now, all subsequent commands use the python from this correctly-versioned venv
            subprocess.run([venv_python, '-m', 'pip', 'install', f'{package_name}=={version}'], check=True, capture_output=True)
            freeze_result = subprocess.run([venv_python, '-m', 'pip', 'freeze'], check=True, capture_output=True, text=True)
            
            # Exclude the package itself from its list of dependencies
            dep_names = [
                line.split('==')[0].strip() 
                for line in freeze_result.stdout.splitlines() 
                if line.split('==')[0].strip().lower() != package_name.lower()
            ]
            
            safe_print(f'      - ✓ Discovered dependencies: {dep_names}')
            return dep_names
        except subprocess.CalledProcessError as e:
            # Provide more detailed error logging
            safe_print(f'      - ❌ Error during temp install to discover dependencies:')
            # The error from pip is often in stderr, so we decode and print it
            error_output = e.stderr.decode('utf-8', 'replace') if isinstance(e.stderr, bytes) else e.stderr
            safe_print(textwrap.indent(error_output, '         | '))
            return None # Return None to signal a hard failure
        finally:
            shutil.rmtree(temp_dir)

    def _find_historical_versions(self, dependencies, cutoff_date):
        """Finds the latest version for each dependency before a given date."""
        safe_print('      - Finding historical versions of dependencies...')
        historical_versions = {}
        cutoff_datetime = datetime.fromisoformat(cutoff_date.replace('Z', '+00:00'))
        
        for dep_name in dependencies:
            try:
                url = f'https://pypi.org/pypi/{dep_name}/json'
                response = http_requests.get(url, timeout=10)
                response.raise_for_status()
                dep_data = response.json()
                latest_valid_version = None
                
                for version, releases in dep_data.get('releases', {}).items():
                    if not releases: continue
                    release_date_str = releases[0].get('upload_time_iso_8601')
                    if not release_date_str: continue
                    
                    release_date = datetime.fromisoformat(release_date_str.replace('Z', '+00:00'))
                    if release_date <= cutoff_datetime:
                        if latest_valid_version is None or parse_version(version) > parse_version(latest_valid_version):
                            latest_valid_version = version
                
                if latest_valid_version:
                    historical_versions[dep_name] = latest_valid_version
            except Exception:
                pass # Ignore failures for individual dependencies
        
        safe_print(f"      - ✓ Resolved historical versions: {historical_versions}")
        return historical_versions

    def _run_pip_install(self, packages: List[str], force_reinstall: bool=False, target_directory: Optional[Path]=None, extra_flags: Optional[List[str]]=None) -> Tuple[int, Dict[str, str]]:
        """
        Runs `pip install` with LIVE, STREAMING output and automatic recovery
        from corrupted 'no RECORD file' errors. Can now target a specific directory.
        Returns: (return_code, captured_output_dict)
        """
        if not packages:
            return 0, {"stdout": "", "stderr": ""}
        
        cmd = [self.config['python_executable'], '-u', '-m', 'pip', 'install']
        if extra_flags:
            cmd.extend(extra_flags)
        if force_reinstall:
            cmd.append('--upgrade')
        if target_directory:
            safe_print(_('   - Targeting installation to: {}').format(target_directory))
            cmd.extend(['--target', str(target_directory)])
        cmd.extend(packages)
        
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1, universal_newlines=True)
            
            stdout_lines, stderr_lines = [], []
            
            # This logic streams output live while capturing it
            for line in process.stdout:
                safe_print(line, end='')
                stdout_lines.append(line)
            for line in process.stderr:
                safe_print(line, end='', file=sys.stderr)
                stderr_lines.append(line)
            
            return_code = process.wait()
            
            full_stdout = ''.join(stdout_lines)
            full_stderr = ''.join(stderr_lines)
            captured_output = {"stdout": full_stdout, "stderr": full_stderr}
            
            full_output = full_stdout + full_stderr
            cleanup_path = target_directory if target_directory else Path(self.config.get('site_packages_path'))

            # Heal 'invalid distribution' warnings first
            self._auto_heal_invalid_distributions(full_output, cleanup_path)
            
            if return_code != 0:
                # Check for the specific "no compatible version" error
                no_dist_found = "no matching distribution found" in full_output.lower() or \
                                "could not find a version that satisfies" in full_output.lower()

                if no_dist_found:
                    # Extract the actual package name, not the error message
                    package_spec = packages[0]  # e.g., "rich==13.6.9"
                    package_name = package_spec.split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
                    
                    # Check if this is a version that doesn't exist vs package incompatibility
                    if '==' in package_spec:
                        # User specified exact version that doesn't exist
                        safe_print(f"\n❌ The specified version does not exist on PyPI")
                        safe_print(f"💡 Package: {package_name}")
                        safe_print(f"💡 Requested version: {package_spec}")
                        safe_print(f"💡 Check pip output above for available versions")
                        return 1, captured_output  # FIXED: Return tuple
                    else:
                        # No version works - might be Python incompatibility
                        raise NoCompatiblePythonError(
                            package_name=package_name,
                            current_python=self.current_python_context,
                            message=f"No compatible version of '{package_name}' found for Python {self.current_python_context}"
                        )
                
                # Check for 'no RECORD file' corruption
                record_file_pattern = 'no RECORD file was found for ([\\w\\-]+)'
                match = re.search(record_file_pattern, full_output)
                if match:
                    package_name = match.group(1)
                    safe_print('\n' + '=' * 60)
                    safe_print(_("🛡️  AUTO-RECOVERY: Detected corrupted package '{}'.").format(package_name))
                    if self._brute_force_package_cleanup(package_name, cleanup_path):
                        safe_print(_('   - Retrying installation on clean environment...'))
                        retry_process = subprocess.run(cmd, capture_output=True, text=True)
                        if retry_process.returncode == 0:
                            safe_print(retry_process.stdout)
                            safe_print(_('   - ✅ Recovery successful!'))
                            return 0, {"stdout": retry_process.stdout, "stderr": retry_process.stderr}  # FIXED: Return tuple
                        else:
                            safe_print(_('   - ❌ Recovery failed. Pip error after cleanup:'))
                            safe_print(retry_process.stderr)
                            return 1, {"stdout": retry_process.stdout, "stderr": retry_process.stderr}  # FIXED: Return tuple
                    else:
                        return 1, captured_output  # FIXED: Return tuple
                
                return return_code, captured_output  # FIXED: Return tuple
                
            return 0, captured_output  # FIXED: Return tuple
            
        except NoCompatiblePythonError:
            raise  # Re-raise for smart_install to handle
        except Exception as e:
            safe_print(_('    ❌ An unexpected error occurred during pip install: {}').format(e))
            return 1, {"stdout": "", "stderr": str(e)}  # FIXED: Return tuple

    def _run_pip_uninstall(self, packages: List[str]) -> int:
        """Runs `pip uninstall` with LIVE, STREAMING output."""
        if not packages:
            return 0
        try:
            cmd = [self.config['python_executable'], '-u', '-m', 'pip', 'uninstall', '-y'] + packages
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, universal_newlines=True)
            safe_print()
            for line in iter(process.stdout.readline, ''):
                safe_print(line, end='')
            process.stdout.close()
            return_code = process.wait()
            return return_code
        except Exception as e:
            safe_print(_('    ❌ An unexpected error occurred during pip uninstall: {}').format(e))
            return 1

    def _run_uv_install(self, packages: List[str]) -> int:
        """Runs `uv install` for a list of packages."""
        if not packages:
            return 0
        try:
            cmd = [self.config['uv_executable'], 'install', '--quiet'] + packages
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            safe_print(result.stdout)
            return result.returncode
        except FileNotFoundError:
            safe_print(_("❌ Error: 'uv' executable not found. Please ensure uv is installed and in your PATH."))
            return 1
        except subprocess.CalledProcessError as e:
            safe_print(_('❌ uv install command failed with exit code {}:').format(e.returncode))
            safe_print(e.stderr)
            return e.returncode
        except Exception as e:
            safe_print(_('    ❌ An unexpected error toccurred during uv install: {}').format(e))
            return 1

    def _run_uv_uninstall(self, packages: List[str]) -> int:
        """Runs `uv pip uninstall` for a list of packages."""
        if not packages:
            return 0
        try:
            cmd = [self.config['uv_executable'], 'pip', 'uninstall'] + packages
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            safe_print(result.stdout)
            return result.returncode
        except FileNotFoundError:
            safe_print(_("❌ Error: 'uv' executable not found. Please ensure uv is installed and in your PATH."))
            return 1
        except subprocess.CalledProcessError as e:
            safe_print(_('❌ uv uninstall command failed with exit code {}:').format(e.returncode))
            safe_print(e.stderr)
            return e.returncode
        except Exception as e:
            safe_print(_('    ❌ An unexpected error occurred during uv uninstall: {}').format(e))
            return 1

    def _test_install_to_get_compatible_version(self, package_name: str) -> Optional[str]:
        """
        Test-installs a package to a temporary directory to get pip's actual compatibility
        error messages, then parses them to find the latest truly compatible version.
        
        OPTIMIZED: If installation starts succeeding, we IMMEDIATELY detect it and cancel
        to avoid wasting time, then return the version info for the main smart installer.
        """
        safe_print(_(" -> Test-installing '{}' to discover latest compatible version...").format(package_name))
        temp_dir = None
        process = None
        try:
            safe_name = re.sub(r'[<>=!,\s]+', '_', package_name)
            temp_dir = tempfile.mkdtemp(prefix=f'omnipkg_test_{safe_name}_')
            temp_path = Path(temp_dir)
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--target', str(temp_path), '--no-deps', '--no-cache-dir', package_name]
            safe_print(_('    Running: {}').format(' '.join(cmd)))
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            stdout_lines = []
            stderr_lines = []
            success_detected = False
            detected_version = None

            def read_stdout():
                nonlocal stdout_lines, success_detected, detected_version
                for line in iter(process.stdout.readline, ''):
                    if line:
                        stdout_lines.append(line)
                        safe_print(_('    [STDOUT] {}').format(line.strip()))
                        early_success_patterns = [f'Collecting\\s+{re.escape(package_name)}==([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)', f'Downloading\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)-', f'Successfully downloaded\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)']
                        for pattern in early_success_patterns:
                            match = re.search(pattern, line, re.IGNORECASE)
                            if match and (not success_detected):
                                detected_version = match.group(1)
                                safe_print(_('    🚀 EARLY SUCCESS DETECTED! Version {} is compatible!').format(detected_version))
                                safe_print(_('    ⚡ Canceling temp install to save time - will use smart installer'))
                                success_detected = True
                                break
                        if success_detected:
                            break
                process.stdout.close()

            def read_stderr():
                nonlocal stderr_lines
                for line in iter(process.stderr.readline, ''):
                    if line:
                        stderr_lines.append(line)
                process.stderr.close()
            stdout_thread = threading.Thread(target=read_stdout)
            stderr_thread = threading.Thread(target=read_stderr)
            stdout_thread.daemon = True
            stderr_thread.daemon = True
            stdout_thread.start()
            stderr_thread.start()
            start_time = time.time()
            timeout = 180
            while process.poll() is None and time.time() - start_time < timeout:
                if success_detected:
                    safe_print(_('    ⚡ Terminating test install process (PID: {})').format(process.pid))
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                time.sleep(0.1)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            if success_detected and detected_version:
                safe_print(_('    ✅ Early success! Latest compatible version: {}').format(detected_version))
                safe_print('    🎯 This version will be passed to smart installer for main installation')
                return detected_version
            if process.poll() is None:
                safe_print(_('    ⏰ Test installation timed out, terminating...'))
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return None
            return_code = process.returncode
            full_stdout = ''.join(stdout_lines)
            full_stderr = ''.join(stderr_lines)
            full_output = full_stdout + full_stderr
            if return_code == 0:
                safe_print(_('    ✅ Test installation completed successfully'))
                install_patterns = [_('Installing collected packages:\\s+{}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)').format(re.escape(package_name)), f'Successfully installed\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)', f'Collecting\\s+{re.escape(package_name)}==([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)']
                for pattern in install_patterns:
                    match = re.search(pattern, full_output, re.IGNORECASE | re.MULTILINE)
                    if match:
                        version = match.group(1)
                        safe_print(_('    ✅ Successfully installed latest compatible version: {}').format(version))
                        return version
                try:
                    base_pkg_name = re.match(r'^([a-zA-Z0-9_.-]+)', package_name)
                    if base_pkg_name:
                        search_name = base_pkg_name.group(1).replace('-', '_')
                    else:
                        search_name = package_name.replace('-', '_')

                    for item in temp_path.glob(f"{search_name}-*.dist-info"):
                        try:
                            dist_info_name = item.name
                            version_match = re.search(f"^{re.escape(search_name)}-([0-9a-zA-Z.+-]+)\\.dist-info", dist_info_name)
                            if version_match:
                                version = version_match.group(1)
                                safe_print(f'    ✅ Found installed version from dist-info: {version}')
                                return version
                        except Exception as e:
                            safe_print(_('    Warning: Could not check dist-info: {}').format(e))
                except Exception as e:
                    safe_print(_('    Warning: Could not check dist-info: {}').format(e))
                safe_print(_("    ⚠️ Installation succeeded but couldn't determine version"))
                return None
            else:
                version_list_patterns = ['from versions:\\s*([^)]+)\\)', 'available versions:\\s*([^\\n\\r]+)', '\\(from versions:\\s*([^)]+)\\)']
                compatible_versions = []
                for pattern in version_list_patterns:
                    match = re.search(pattern, full_output, re.IGNORECASE | re.DOTALL)
                    if match:
                        versions_text = match.group(1).strip()
                        raw_versions = [v.strip() for v in versions_text.split(',')]
                        for raw_version in raw_versions:
                            clean_version = raw_version.strip(' \'"')
                            if re.match('^[0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?$', clean_version):
                                compatible_versions.append(clean_version)
                        break
                if compatible_versions:
                    try:
                        from packaging.version import parse as parse_version
                        stable_versions = [v for v in compatible_versions if not re.search('[a-zA-Z]', v)]
                        versions_to_sort = stable_versions if stable_versions else compatible_versions
                        sorted_versions = sorted(versions_to_sort, key=parse_version, reverse=True)
                        latest_compatible = sorted_versions[0]
                        safe_print(_('    ✅ Found {} compatible versions').format(len(compatible_versions)))
                        safe_print(_('    ✅ Latest compatible version: {}').format(latest_compatible))
                        return latest_compatible
                    except Exception as e:
                        safe_print(_('    ❌ Error sorting versions: {}').format(e))
                        if compatible_versions:
                            fallback_version = compatible_versions[-1]
                            safe_print(_('    ⚠️ Using fallback version: {}').format(fallback_version))
                            return fallback_version
                python_req_pattern = 'Requires-Python\\s*>=([0-9]+\\.[0-9]+)'
                python_req_matches = re.findall(python_req_pattern, full_output)
                if python_req_matches:
                    safe_print(_('    📋 Found Python version requirements: {}').format(', '.join(set(python_req_matches))))
                return None
        except Exception as e:
            safe_print(_('    ❌ Unexpected error during test installation: {}').format(e))
            return None
        finally:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except:
                    try:
                        process.kill()
                        process.wait()
                    except:
                        pass
            if temp_dir and Path(temp_dir).exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    safe_print(_('    ⚠️ Warning: Could not clean up temp directory {}: {}').format(temp_dir, e))

    def _quick_compatibility_check(self, package_name: str, version_to_test: str=None) -> Optional[str]:
        """
        Quickly test if a specific version (or latest) is compatible by attempting
        a pip install and parsing any compatibility errors for available versions.
        
        Returns the latest compatible version found, or None if can't determine.
        """
        safe_print(f'   💫 Quick compatibility check for {package_name}' + (f'=={version_to_test}' if version_to_test else '')) 
        try:
            package_spec = f'{package_name}=={version_to_test}' if version_to_test else package_name
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--dry-run', '--no-deps', package_spec]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            
            full_output = result.stdout + result.stderr
            
            # ========== DETECT ANCIENT PIP FIRST ==========
            if "no such option: --dry-run" in full_output.lower():
                safe_print('    ⏳ Ancient pip detected - quick check not available')
                safe_print('    ' + '─' * 58)
                return None  # Bail early, let caller use fallback
            # ===============================================

            if result.returncode == 0:
                install_patterns = [
                    f'Would install\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)', 
                    f'Collecting\\s+{re.escape(package_name)}==([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)'
                ]
                for pattern in install_patterns:
                    match = re.search(pattern, full_output, re.IGNORECASE)
                    if match:
                        compatible_version = match.group(1)
                        safe_print(f'    ✅ Latest version {compatible_version} is compatible!')
                        return compatible_version
                return version_to_test if version_to_test else None

            else:
                # Failure case - try to extract version list
                safe_print('    ' + '─' * 58)
                
                version_list_patterns = [
                    'from versions:\\s*([^)]+)\\)', 
                    'available versions:\\s*([^\\n\\r]+)', 
                    '\\(from versions:\\s*([^)]+)\\)'
                ]
                
                for pattern in version_list_patterns:
                    match = re.search(pattern, full_output, re.IGNORECASE | re.DOTALL)
                    if match:
                        versions_text = match.group(1).strip()
                        compatible_versions = []
                        raw_versions = [v.strip(' \'"') for v in versions_text.split(',')]
                        
                        for raw_version in raw_versions:
                            if re.match('^[0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?$', raw_version):
                                compatible_versions.append(raw_version)
                        
                        if compatible_versions:
                            try:
                                from packaging.version import parse as parse_version
                                stable_versions = [v for v in compatible_versions if not re.search('[a-zA-Z]', v)]
                                versions_to_sort = stable_versions if stable_versions else compatible_versions
                                latest_compatible = sorted(versions_to_sort, key=parse_version, reverse=True)[0]
                                safe_print(f'    🎯 Latest compatible version: {latest_compatible}')
                                return latest_compatible
                            except Exception as e:
                                safe_print(f'    ⚠️ Error sorting versions: {e}')
                                return compatible_versions[-1] if compatible_versions else None
                
                return None

        except Exception as e:
            safe_print(f'    ❌ Quick compatibility check failed: {e}')
            return None
                    
    def _get_package_minimum_python_from_spec(self, spec) -> Optional[str]:
        """Extract minimum Python version from a SpecifierSet (e.g., '>=3.8' -> 'py3.8')"""
        try:
            for specifier in spec:
                if specifier.operator in ('>=', '>'):
                    version_str = str(specifier.version)
                    major_minor = '.'.join(version_str.split('.')[:2])  # "3.8.0" -> "3.8"
                    return f"py{major_minor}"
            return None
        except:
            return None

    def _find_newest_version_for_python(self, package_name: str, target_python: str, pypi_data: dict) -> Optional[str]:
        """Find newest package version with wheels for target Python version"""
        try:
            from packaging.version import parse as parse_version
            releases = pypi_data.get('releases', {})
            
            target_py_tag = target_python.replace('py', '').replace('.', '')  # "py3.8" -> "38"
            
            # Sort versions newest first
            sorted_versions = sorted(releases.keys(), key=lambda v: parse_version(v), reverse=True)
            
            for version in sorted_versions:
                files = releases[version]
                for file_info in files:
                    if file_info.get('packagetype') == 'bdist_wheel':
                        filename = file_info.get('filename', '')
                        # Check for cp38, py38, py3, etc.
                        if re.search(rf'-(?:cp|py){target_py_tag}', filename) or '-py3-' in filename:
                            safe_print(f'    🎯 Found compatible version: {version} (has wheels for {target_python})')
                            return version
            
            return None
        except Exception as e:
            safe_print(f'    ⚠️  Error finding compatible version: {e}')
            return None

    def _get_latest_version_from_pypi(self, package_name: str, python_context_version: Optional[str] = None) -> Optional[str]:
        """
        (ENHANCED CACHING) Gets the latest compatible version of a package.
        Returns None if package doesn't exist.
        Raises NoCompatiblePythonError if package exists but no compatible Python version.
        """
        safe_print(f" -> Finding latest COMPATIBLE version for '{package_name}' using background caching...")
        py_context = python_context_version or self.current_python_context
    
        if not hasattr(self, 'pypi_cache'):
            self.initialize_pypi_cache()

        cached_version = self.pypi_cache.get_cached_version(package_name, py_context)
        if cached_version:
            safe_print(f"    💾 Using cached version for {py_context}: {cached_version}")
            self._start_background_cache_refresh(package_name, py_context)
            return cached_version
        
        # USE THE ROBUST TEST INSTALLATION APPROACH FIRST
        safe_print(f"    🧪 Using pip to find latest compatible version for Python {py_context}...")
        try:
            test_result = self._test_install_to_get_compatible_version(package_name)
            if test_result:
                safe_print(f'    🎯 Found compatible version: {test_result}')
                self.pypi_cache.cache_version(package_name, test_result, py_context)
                return test_result
        except Exception as e:
            safe_print(f'    ⚠️ Test installation approach failed: {e}')
        
        # If test installation didn't work, check PyPI to see if package exists
        # and determine if we need quantum healing
        package_exists_on_pypi = False
        latest_pypi_version = None
        
        try:
            safe_print(f"    🌐 Fetching latest version from PyPI for '{package_name}'...")
            response = http_requests.get(f'https://pypi.org/pypi/{package_name}/json', timeout=10)
            if response.status_code == 200:
                pypi_data = response.json()
                latest_pypi_version = pypi_data['info']['version']
                package_exists_on_pypi = True
                safe_print(f'    📦 Latest PyPI version: {latest_pypi_version}')

                # NEW: For legacy Python contexts, preemptively check compatibility
                py_parts = tuple(map(int, py_context.replace('py', '').split('.')))
                is_legacy = py_parts < (3, 10)

                if is_legacy:
                    requires_python = pypi_data['info'].get('requires_python')
                    if requires_python:
                        from packaging.specifiers import SpecifierSet
                        spec = SpecifierSet(requires_python)
                        current_py_str = py_context.replace('py', '')
                        
                        if not spec.contains(current_py_str):
                            # INCOMPATIBLE - find target Python and raise immediately
                            pkg_min_python = self._get_package_minimum_python_from_spec(spec)
                            if pkg_min_python:
                                baseline_version = self._find_newest_version_for_python(package_name, pkg_min_python, pypi_data)
                                target_version = baseline_version or latest_pypi_version
                                
                                safe_print(f'    ⚠️  Incompatible: {package_name} v{target_version} requires {pkg_min_python}')
                                
                                # RAISE IMMEDIATELY - don't waste time on test installations
                                raise NoCompatiblePythonError(
                                    package_name=package_name,
                                    package_version=target_version,
                                    current_python=py_context,
                                    compatible_python=pkg_min_python.replace('py', ''),
                                    message=f"Package '{package_name}' v{target_version} requires Python {pkg_min_python}, current is {py_context}"
                                )
            
                # Check if this version is already installed and compatible
                safe_print(f'    🔍 Checking if version {latest_pypi_version} is already installed...')
                cmd_check = [self.config['python_executable'], '-m', 'pip', 'show', package_name]
                result_check = subprocess.run(cmd_check, capture_output=True, text=True, check=False, timeout=30)
                if result_check.returncode == 0:
                    version_match = re.search('^Version:\\s*([^\\s\\n\\r]+)', result_check.stdout, re.MULTILINE | re.IGNORECASE)
                    if version_match:
                        installed_version = version_match.group(1).strip()
                        safe_print(f'    📋 Currently installed version: {installed_version}')
                        if installed_version == latest_pypi_version:
                            safe_print(f'    🚀 JACKPOT! Latest PyPI version {latest_pypi_version} is already installed!')
                            safe_print('    ⚡ Skipping all test installations - using installed version')
                            # Cache the result and return - FIX: Include python_context
                            self.pypi_cache.cache_version(package_name, latest_pypi_version, py_context)
                            return latest_pypi_version
                        else:
                            safe_print(f'    📋 Installed version ({installed_version}) differs from latest PyPI ({latest_pypi_version})')
                            safe_print('    🧪 Will test if latest PyPI version is compatible...')
                    else:
                        safe_print('    ⚠️ Could not parse installed version from pip show output')
                else:
                    safe_print(f"    📋 Package '{package_name}' is not currently installed")
                    safe_print('    🧪 Will test if latest PyPI version is compatible...')
                    
            elif response.status_code == 404:
                safe_print(f"    ❌ Package '{package_name}' not found on PyPI (404 error)")
                safe_print("    💡 This usually means the package name doesn't exist or contains invalid characters")
                safe_print('    📝 Please check the package name spelling and format')
                return None
            else:
                safe_print(f'    ❌ Could not fetch PyPI data (status: {response.status_code})')
                safe_print('    🧪 Falling back to test installation approach...')
        except NoCompatiblePythonError:
            raise  # Re-raise immediately, don't catch it
        except http_requests.exceptions.RequestException as e:
            safe_print(f'    ❌ Network error checking PyPI: {e}')
            safe_print('    🧪 Falling back to test installation approach...')
        except Exception as e:
            safe_print(f'    ❌ Error checking PyPI: {e}')
            safe_print('    🧪 Falling back to test installation approach...')

        # Test compatibility if we have a latest version from PyPI
        if latest_pypi_version:
            safe_print('    🧪 Testing latest PyPI version compatibility with quick install attempt...')
            try:
                compatible_version = self._quick_compatibility_check(package_name, latest_pypi_version)
                if compatible_version:
                    safe_print(f'    🎯 Found compatible version {compatible_version} - caching and returning!')
                    # Cache the result and return - FIX: Include python_context
                    self.pypi_cache.cache_version(package_name, compatible_version, py_context)
                    return compatible_version
            except Exception as e:
                safe_print(f'    ⚠️ Quick compatibility check failed: {e}')
                compatible_version = None

        # If quick check didn't work, try the full test installation approach
        if not compatible_version:
            safe_print('    🧪 Starting optimized test installation with early success detection...')
            try:
                test_result = self._test_install_to_get_compatible_version(package_name)
                if test_result:
                    safe_print(f'    🎯 Test approach successful! Version {test_result} ready for smart installer')
                    # Cache the result and return - FIX: Include python_context
                    self.pypi_cache.cache_version(package_name, test_result, py_context)
                    return test_result
            except Exception as e:
                safe_print(f'    ⚠️ Test installation approach failed: {e}')

        # Final fallback: dry-run method
        safe_print(" -> Optimized test installation didn't work, falling back to dry-run method...")
        try:
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--dry-run', '--verbose', '--no-deps', f'{package_name}']
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            output_to_search = result.stdout + result.stderr
            
            if result.returncode != 0:
                error_patterns = ['No matching distribution found', 'Could not find a version', 'ERROR: No matching distribution found', 'Package .* not found', 'Invalid requirement']
                for pattern in error_patterns:
                    if re.search(pattern, output_to_search, re.IGNORECASE):
                        safe_print(f"    ❌ Package '{package_name}' appears to not exist or be accessible")
                        safe_print(f'    💡 Pip error suggests no compatible version was found')
                        
                        # HERE'S THE KEY DECISION:
                        if package_exists_on_pypi and latest_pypi_version:
                            # We KNOW the package exists - find what Python version it needs!
                            safe_print(f'    🔬 Package exists on PyPI (v{latest_pypi_version}) but incompatible with Python {py_context}')
                            safe_print(f'    🔍 Looking up compatible Python versions for {package_name} v{latest_pypi_version}...')
                            
                            # Find what Python version this package actually supports
                            compatible_py_version = self._find_compatible_python_version(package_name, latest_pypi_version)
                            
                            if compatible_py_version:
                                safe_print(f'    ✅ Found compatible Python version: {compatible_py_version}')
                                safe_print(f'    💡 {package_name} v{latest_pypi_version} requires Python {compatible_py_version}')
                            else:
                                safe_print(f'    ⚠️  Could not determine compatible Python version')
                                compatible_py_version = "unknown"
                            
                            raise NoCompatiblePythonError(
                                package_name=package_name,
                                package_version=latest_pypi_version,
                                current_python=py_context,
                                compatible_python=compatible_py_version,
                                message=f"Package '{package_name}' v{latest_pypi_version} requires Python {compatible_py_version}, but current Python is {py_context}"
                            )
                        else:
                            # We never confirmed it exists on PyPI - probably a typo
                            safe_print(f'    💡 Package may not exist or name may be incorrect')
                            return None

            # Check for already satisfied patterns
            already_satisfied_patterns = [
                f'Requirement already satisfied:\\s+{re.escape(package_name)}\\s+in\\s+[^\\s]+\\s+\\(([^)]+)\\)',
                f'Requirement already satisfied:\\s+{re.escape(package_name)}==([^\\s]+)',
                f'Requirement already satisfied:\\s+{re.escape(package_name)}-([^\\s]+)'
            ]
            
            for pattern in already_satisfied_patterns:
                match = re.search(pattern, output_to_search, re.IGNORECASE | re.MULTILINE)
                if match:
                    version = match.group(1).strip()
                    safe_print(f' ✅ Package already installed with version: {version}')
                    if re.match('^[0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?$', version):
                        self.pypi_cache.cache_version(package_name, version, py_context)
                        return version
                    else:
                        safe_print(f" ⚠️  Version '{version}' has invalid format, continuing search...")
                        continue

            # Try alternative approaches if dry-run didn't work
            if not output_to_search.strip() or result.returncode != 0:
                safe_print(' -> Trying alternative approach: pip index versions...')
                cmd_alt = [self.config['python_executable'], '-m', 'pip', 'index', 'versions', package_name]
                result_alt = subprocess.run(cmd_alt, capture_output=True, text=True, check=False, timeout=60)
                if result_alt.returncode == 0 and result_alt.stdout.strip():
                    version_match = re.search(f'{re.escape(package_name)}\\s*\\(([^,)]+)', result_alt.stdout)
                    if version_match:
                        version = version_match.group(1).strip()
                        safe_print(f' ✅ Found latest version via pip index: {version}')
                        self.pypi_cache.cache_version(package_name, version, py_context)
                        return version

            # Parse output for version patterns
            patterns = [
                f'(?:Would install|Installing collected packages:|Collecting)\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)',
                f'{re.escape(package_name)}==([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)',
                f'Downloading\\s+{re.escape(package_name)}-([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)-',
                f'{re.escape(package_name)}\\s+([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)',
                f'{re.escape(package_name)}>=([0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?)'
            ]
            
            for i, pattern in enumerate(patterns, 1):
                match = re.search(pattern, output_to_search, re.IGNORECASE | re.MULTILINE)
                if match:
                    version = match.group(1)
                    safe_print(f' ✅ Pip resolver identified latest compatible version: {version} (pattern {i})')
                    if re.match('^[0-9]+(?:\\.[0-9]+)*(?:[a-zA-Z0-9\\.-_]*)?$', version):
                        self.pypi_cache.cache_version(package_name, version, py_context)
                        return version
                    else:
                        safe_print(f" ⚠️  Version '{version}' has invalid format, continuing search...")
                        continue

            # Final attempt with pip list
            if 'Requirement already satisfied' in output_to_search:
                safe_print(' -> Package appears to be installed, checking with pip list...')
                try:
                    result_list = subprocess.run(f"{self.config['python_executable']} -m pip list --format=freeze | grep -i '^{package_name}=='", 
                                            shell=True, capture_output=True, text=True, timeout=30)
                    if result_list.returncode == 0 and result_list.stdout.strip():
                        list_match = re.search(f'^{re.escape(package_name)}==([^\\s]+)', result_list.stdout, re.IGNORECASE | re.MULTILINE)
                        if list_match:
                            version = list_match.group(1).strip()
                            safe_print(f' ✅ Found installed version via pip list: {version}')
                            self.pypi_cache.cache_version(package_name, version, py_context)
                            return version
                except Exception as e:
                    safe_print(f' -> pip list approach failed: {e}')

            # If we get here, nothing worked - check if package exists but is incompatible
            if package_exists_on_pypi and latest_pypi_version:
                safe_print(f' ❌ Package {package_name} v{latest_pypi_version} exists but incompatible with Python {py_context}')
                safe_print(f' 🔍 Looking up compatible Python versions...')
                
                compatible_py_version = self._find_compatible_python_version(package_name, latest_pypi_version)
                
                if compatible_py_version:
                    safe_print(f' ✅ {package_name} v{latest_pypi_version} requires Python {compatible_py_version}')
                else:
                    safe_print(f' ⚠️  Could not determine compatible Python version')
                    compatible_py_version = "unknown"
                
                raise NoCompatiblePythonError(
                    package_name=package_name,
                    package_version=latest_pypi_version,
                    current_python=py_context,
                    compatible_python=compatible_py_version,
                    message=f"Package '{package_name}' v{latest_pypi_version} requires Python {compatible_py_version}, current is {py_context}"
                )
            else:
                # Could not find package at all
                safe_print(f" ❌ Could not find or resolve a compatible version for package '{package_name}'.")
                safe_print(' ❌ This might indicate:')
                safe_print("   1) Package doesn't exist on PyPI")
                safe_print('   2) Package name is misspelled or contains invalid characters')
                safe_print('   3) No compatible version exists for your Python environment')
                safe_print('   4) Network connectivity issues')
                safe_print('   5) Package requires different installation method')
                return None

        except subprocess.TimeoutExpired:
            safe_print(f" ❌ Pip resolver timed out while resolving '{package_name}'.")
            return None
        except NoCompatiblePythonError:
            raise  # Re-raise compatibility errors
        except Exception as e:
            safe_print(f" ❌ An unexpected error occurred: {e}")
            return None
        
    def _start_background_cache_refresh(self, package_name: str, python_context: str):
        """
        (FIXED) The background refresh now ALSO uses the robust `pip`-based check
        to prevent it from ever polluting the cache with incompatible versions.
        """
        def background_refresh():
            try:
                # Use the fast but robust dry-run check to get a fresh, COMPATIBLE version.
                fresh_compatible_version = self._quick_compatibility_check(package_name)
                
                if fresh_compatible_version:
                    current_cached = self.pypi_cache.get_cached_version(package_name, python_context)
                    
                    if fresh_compatible_version != current_cached:
                        self.pypi_cache.cache_version(package_name, fresh_compatible_version, python_context)
                        safe_print(f"    🆕 [Background] Cache updated for {python_context}: {package_name} {current_cached or 'N/A'} → {fresh_compatible_version}")
                    else:
                        # Re-set the value to refresh the TTL.
                        self.pypi_cache.cache_version(package_name, fresh_compatible_version, python_context)
            except Exception:
                # Silently fail in the background.
                pass
        
        refresh_thread = threading.Thread(target=background_refresh, daemon=True)
        refresh_thread.start()

    def _fetch_latest_pypi_version_only(self, package_name: str) -> Optional[str]:
        """
        Lightweight PyPI fetch - just gets the latest version number.
        Used for background cache refreshes.
        """
        try:
            response = http_requests.get(f'https://pypi.org/pypi/{package_name}/json', timeout=5)
            if response.status_code == 200:
                pypi_data = response.json()
                return pypi_data['info']['version']
            return None
        except Exception:
            return None

    def _perform_full_pypi_resolution(self, package_name: str) -> Optional[str]:
        """
        Full network-based resolution when cache miss occurs.
        This is your existing logic, extracted for clarity.
        """
        latest_pypi_version = None
        compatible_version = None

        try:
            safe_print(f"    🌐 Fetching latest version from PyPI for '{package_name}'...")
            response = http_requests.get(f'https://pypi.org/pypi/{package_name}/json', timeout=10)
            if response.status_code == 200:
                pypi_data = response.json()
                latest_pypi_version = pypi_data['info']['version']
                safe_print(f'    📦 Latest PyPI version: {latest_pypi_version}')

                # Check if this version is already installed and compatible
                safe_print(f'    🔍 Checking if version {latest_pypi_version} is already installed...')
                cmd_check = [self.config['python_executable'], '-m', 'pip', 'show', package_name]
                result_check = subprocess.run(cmd_check, capture_output=True, text=True, check=False, timeout=30)
                if result_check.returncode == 0:
                    version_match = re.search('^Version:\\s*([^\\s\\n\\r]+)', result_check.stdout, re.MULTILINE | re.IGNORECASE)
                    if version_match:
                        installed_version = version_match.group(1).strip()
                        safe_print(f'    📋 Currently installed version: {installed_version}')
                        if installed_version == latest_pypi_version:
                            safe_print(f'    🚀 JACKPOT! Latest PyPI version {latest_pypi_version} is already installed!')
                            safe_print('    ⚡ Skipping all test installations - using installed version')
                            # Cache the result and return
                            self.pypi_cache.cache_version(package_name, latest_pypi_version, self.python_context)
                            return latest_pypi_version
                        else:
                            safe_print(f'    📋 Installed version ({installed_version}) differs from latest PyPI ({latest_pypi_version})')
                            safe_print('    🧪 Will test if latest PyPI version is compatible...')
                    else:
                        safe_print('    ⚠️ Could not parse installed version from pip show output')
                else:
                    safe_print(f"    📋 Package '{package_name}' is not currently installed")
                    safe_print('    🧪 Will test if latest PyPI version is compatible...')

            elif response.status_code == 404:
                safe_print(f"    ❌ Package '{package_name}' not found on PyPI (404 error)")
                safe_print("    💡 This usually means the package name doesn't exist or contains invalid characters")
                safe_print('    📝 Please check the package name spelling and format')
                return None
            else:
                safe_print(f'    ❌ Could not fetch PyPI data (status: {response.status_code})')
                safe_print('    🧪 Falling back to test installation approach...')

        except http_requests.exceptions.RequestException as e:
            safe_print(f'    ❌ Network error checking PyPI: {e}')
            safe_print('    🧪 Falling back to test installation approach...')
        except Exception as e:
            safe_print(f'    ❌ Error checking PyPI: {e}')
            safe_print('    🧪 Falling back to test installation approach...')

        # Test compatibility if we have a latest version from PyPI
        if latest_pypi_version:
            safe_print('    🧪 Testing latest PyPI version compatibility with quick install attempt...')
            try:
                compatible_version = self._quick_compatibility_check(package_name, latest_pypi_version)
                if compatible_version:
                    safe_print(f'    🎯 Found compatible version {compatible_version} - caching and returning!')
                    # Cache the result and return
                    self.pypi_cache.cache_version(package_name, compatible_version)
                    return compatible_version
            except Exception as e:
                safe_print(f'    ⚠️ Quick compatibility check failed: {e}')
                compatible_version = None

        # If quick check didn't work, try the full test installation approach
        if not compatible_version:
            safe_print('    🧪 Starting optimized test installation with early success detection...')
            try:
                test_result = self._test_install_to_get_compatible_version(package_name)
                if test_result:
                    safe_print(f'    🎯 Test approach successful! Version {test_result} ready for smart installer')
                    # Cache the result and return
                    self.pypi_cache.cache_version(package_name, test_result)
                    return test_result
            except Exception as e:
                safe_print(f'    ⚠️ Test installation approach failed: {e}')

        # Final fallback: dry-run method
        safe_print(" -> Optimized test installation didn't work, falling back to dry-run method...")
        try:
            cmd = [self.config['python_executable'], '-m', 'pip', 'install', '--dry-run', '--verbose', '--no-deps', f'{package_name}']
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
            output_to_search = result.stdout + result.stderr

            if result.returncode != 0:
                error_patterns = ['No matching distribution found', 'Could not find a version', 'ERROR: No matching distribution found', 'Package .* not found', 'Invalid requirement']
                for pattern in error_patterns:
                    if re.search(pattern, output_to_search, re.IGNORECASE):
                        safe_print(f"    ❌ Package '{package_name}' appears to not exist or be accessible")
                        safe_print(f'    💡 Pip error suggests no compatible version was found')
                        return None
            
            # This is a fallback and doesn't need to be cached as the primary methods handle caching.
            # Just find the version and return it.
            # (Your existing pattern matching logic for the dry-run is good here)
            # ...
            return None # Fallback return

        except Exception as e:
            safe_print(f" ❌ An unexpected error occurred while running the pip resolver for '{package_name}': {e}")
            return None

    def get_available_versions(self, package_name: str) -> List[str]:
        """
        (FIXED) Correctly gets all available versions (active and bubbled) for a package
        by using the correct Redis command (SMEMBERS) for sets.
        """
        c_name = canonicalize_name(package_name)
        main_key = f'{self.redis_key_prefix}{c_name}'
        versions = set()
        try:
            # --- THIS IS THE FIX ---
            # Use SMEMBERS to read from a Set, not HGETALL or other hash commands.
            installed_versions = self.cache_client.smembers(f'{main_key}:installed_versions')
            if installed_versions:
                versions.update(installed_versions)
            # --- END FIX ---

            # The active version is still in the main hash, which is correct.
            active_version = self.cache_client.hget(main_key, 'active_version')
            if active_version:
                versions.add(active_version)
                
            return sorted(list(versions), key=parse_version, reverse=True)
        except Exception as e:
            # Catch potential Redis errors, including WRONGTYPE if the schema changes again.
            safe_print(_('⚠️ Could not retrieve versions for {}: {}').format(package_name, e))
            return []

    def list_packages(self, pattern: str=None) -> int:
        if not self._connect_cache():
            return 1
        self._synchronize_knowledge_base_with_reality()
        
        all_pkg_names = self.cache_client.smembers(f'{self.redis_env_prefix}index')

        if pattern:
            try:
                prog = re.compile(pattern, re.IGNORECASE)
                all_pkg_names = {name for name in all_pkg_names if prog.search(name)}
            except re.error:
                all_pkg_names = {name for name in all_pkg_names if pattern.lower() in name.lower()}

        safe_print(_('📋 Found {} matching package(s) for Python context {}:').format(len(all_pkg_names), self.current_python_context))

        if not all_pkg_names:
            return 0

        # Step 1: Gather all data before printing
        packages_data = []
        for pkg_name in sorted(list(all_pkg_names)):
            main_key = f'{self.redis_key_prefix}{pkg_name}'
            package_data = self.cache_client.hgetall(main_key)
            display_name = package_data.get('name', pkg_name)
            active_version = package_data.get('active_version')
            all_versions = self.get_available_versions(pkg_name)
            
            bubbled_versions = sorted([v for v in all_versions if v != active_version], key=parse_version, reverse=True)
            
            packages_data.append({
                "name": display_name,
                "active": active_version,
                "bubbled": bubbled_versions
            })

        if not packages_data:
            return 0

        # Step 2: Calculate column widths for clean alignment
        max_name_len = max(len(p["name"]) for p in packages_data)
        # Ensure a minimum width for the header
        max_name_len = max(max_name_len, len("Package")) + 4

        # Step 3: Print the header
        header = f"{'Package':<{max_name_len}}{'Active Version':<20}Bubbled Versions"
        safe_print("\n" + header)
        safe_print('─' * (len(header) + 20)) # Make separator long enough

        # Step 4: Print each package as a single, formatted row
        for pkg in packages_data:
            name_str = pkg["name"].ljust(max_name_len)
            
            active_str = f'✅ {pkg["active"]}'.ljust(20) if pkg["active"] else '(none)'.ljust(20)
            
            bubbled_str = ""
            if pkg["bubbled"]:
                # To keep it compact, we'll show a max of 3 bubbled versions
                display_bubbles = pkg["bubbled"][:3]
                bubbled_str = '🫧 ' + ', '.join(display_bubbles)
                if len(pkg["bubbled"]) > 3:
                    bubbled_str += f", ... ({len(pkg['bubbled']) - 3} more)"
            
            safe_print(f'{name_str}{active_str}{bubbled_str}')
        
        return 0

    def show_multiversion_status(self) -> int:
        if not self._connect_cache():
            return 1
        self._synchronize_knowledge_base_with_reality(verbose=True)
        safe_print(_('🔄 omnipkg System Status'))
        safe_print('=' * 50)
        safe_print(_("🛠️ Environment broken by pip or uv? Run 'omnipkg revert' to restore the last known good state! 🚑"))
        try:
            pip_version = version('pip')
            safe_print(_('\n🔒 Pip in Jail (main environment)'))
            safe_print(_('    😈 Locked up for causing chaos in the main env! 🔒 (v{})').format(pip_version))
        except importlib.metadata.PackageNotFoundError:
            safe_print(_('\n🔒 Pip in Jail (main environment)'))
            safe_print(_('    🚫 Pip not found in the main env. Escaped or never caught!'))
        try:
            uv_version = version('uv')
            safe_print(_('🔒 UV in Jail (main environment)'))
            safe_print(_('    😈 Speedy troublemaker locked up in the main env! 🔒 (v{})').format(uv_version))
        except importlib.metadata.PackageNotFoundError:
            safe_print(_('🔒 UV in Jail (main environment)'))
            safe_print(_('    🚫 UV not found in the main env. Too fast to catch!'))
        safe_print(_('\n🌍 Main Environment:'))
        site_packages = Path(self.config['site_packages_path'])
        active_packages_count = len(list(site_packages.glob('*.dist-info')))
        safe_print(_('  - Path: {}').format(site_packages))
        safe_print(_('  - Active Packages: {}').format(active_packages_count))
        safe_print(_('\n📦 izolasyon Alanı (Bubbles):'))
        if not self.multiversion_base.exists() or not any(self.multiversion_base.iterdir()):
            safe_print(_('  - No isolated package versions found.'))
            return 0
        safe_print(_('  - Bubble Directory: {}').format(self.multiversion_base))
        safe_print(_('  - Import Hook Installed: {}').format('✅' if self.hook_manager.hook_installed else '❌'))
        version_dirs = list(self.multiversion_base.iterdir())
        total_bubble_size = 0
        safe_print(_('\n📦 Isolated Package Versions ({} bubbles):').format(len(version_dirs)))
        for version_dir in sorted(version_dirs):
            if version_dir.is_dir():
                size = sum((f.stat().st_size for f in version_dir.rglob('*') if f.is_file()))
                total_bubble_size += size
                size_mb = size / (1024 * 1024)
                warning = ' ⚠️' if size_mb > 100 else ''
                formatted_size_str = '{:.1f}'.format(size_mb)
                safe_print(_('  - 📁 {} ({} MB){}').format(version_dir.name, formatted_size_str, warning))
                if 'pip' in version_dir.name.lower():
                    safe_print(_('    😈 Pip is locked up in a bubble, plotting chaos like a Python outlaw! 🔒'))
                elif 'uv' in version_dir.name.lower():
                    safe_print(_('    😈 UV is locked up in a bubble, speeding toward trouble! 🔒'))
        total_bubble_size_mb = total_bubble_size / (1024 * 1024)
        formatted_total_size_str = '{:.1f}'.format(total_bubble_size_mb)
        safe_print(_('  - Total Bubble Size: {} MB').format(formatted_total_size_str))
        return 0
    
class PyPIVersionCache:
    """
    Manages 24-hour caching of PyPI versions AND compatibility information.
    Now stores: version, compatible_python, exists_on_pypi, error_state
    """

    def __init__(self, redis_client=None, cache_dir: str = "~/.omnipkg/cache"):
        self.redis_client = redis_client
        self.cache_dir = os.path.expanduser(cache_dir)
        self.cache_file = os.path.join(self.cache_dir, "pypi_versions_contextual.json")
        self.cache_ttl = 24 * 60 * 60  # 24 hours in seconds

        os.makedirs(self.cache_dir, exist_ok=True)

        if not self.redis_client:
            self._load_file_cache()
    
    def _get_cache_key(self, package_name: str, python_context: str) -> str:
        """Generate a context-aware cache key."""
        return f"pypi_version:{python_context}:{package_name.lower()}"
    
    def _load_file_cache(self):
        """Load cache from local file."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    self._file_cache = json.load(f)
            else:
                self._file_cache = {}
        except Exception:
            self._file_cache = {}
    
    def _save_file_cache(self):
        """Save cache to local file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self._file_cache, f, indent=2)
        except Exception as e:
            safe_print(f"⚠️ Warning: Could not save cache to file: {e}")
    
    def get_cached_version(self, package_name: str, python_context: str) -> Optional[str]:
        """
        Get cached version for a specific python_context.
        Returns None if not cached, version string if compatible, or raises NoCompatiblePythonError.
        """
        cache_key = self._get_cache_key(package_name, python_context)

        cached_data = None
        
        # Try Redis first
        if self.redis_client:
            try:
                redis_data = self.redis_client.get(cache_key)
                if redis_data:
                    cached_data = json.loads(redis_data)
                    safe_print(f"    🚀 CACHE HIT: {package_name} (for Python {python_context}) (Redis)")
            except Exception as e:
                safe_print(f"    ⚠️ Redis cache read error: {e}")

        # Try file cache if Redis didn't have it
        if not cached_data and hasattr(self, '_file_cache'):
            cached_entry = self._file_cache.get(cache_key)
            if cached_entry:
                cached_time = cached_entry.get('timestamp', 0)
                if time.time() - cached_time < self.cache_ttl:
                    cached_data = cached_entry
                    safe_print(f"    🚀 CACHE HIT: {package_name} (for Python {python_context}) (file)")
                else:
                    # Cache expired
                    del self._file_cache[cache_key]
                    self._save_file_cache()

        # Process cached data
        if cached_data:
            # Check if this is an incompatibility error state
            if cached_data.get('incompatible'):
                safe_print(f"    ⚠️  Cached incompatibility: {package_name} requires Python {cached_data.get('compatible_python')}")
                raise NoCompatiblePythonError(
                    package_name=package_name,
                    package_version=cached_data.get('version'),
                    current_python=python_context,
                    compatible_python=cached_data.get('compatible_python'),
                    message=f"Cached: Package '{package_name}' requires Python {cached_data.get('compatible_python')}"
                )
            
            # Normal case - return the version
            version = cached_data.get('version')
            if version:
                safe_print(f"    -> v{version}")
                return version

        return None
    
    def get_cache_stats(self) -> Dict[str, any]:
        """Get cache statistics."""
        stats = {
            'total_entries': 0,
            'expired_entries': 0,
            'valid_entries': 0,
            'cache_file_exists': os.path.exists(self.cache_file),
            'redis_available': self.redis_client is not None
        }
        
        current_time = time.time()
        
        # Count file cache entries
        if hasattr(self, '_file_cache'):
            stats['total_entries'] = len(self._file_cache)
            for data in self._file_cache.values():
                if current_time - data.get('timestamp', 0) >= self.cache_ttl:
                    stats['expired_entries'] += 1
                else:
                    stats['valid_entries'] += 1
        
        return stats 
    
    def cache_version(self, package_name: str, version: str, python_context: str, 
                     compatible: bool = True, compatible_python: Optional[str] = None):
        """
        Cache version information with compatibility data.
        
        Args:
            package_name: Name of the package
            version: Version string
            python_context: Python version context (e.g., 'py3.14')
            compatible: Whether this version is compatible with python_context
            compatible_python: If incompatible, which Python version IS compatible
        """
        cache_key = self._get_cache_key(package_name, python_context)
        cache_data = {
            'version': version,
            'timestamp': time.time(),
            'cached_at': datetime.now().isoformat(),
            'incompatible': not compatible,
            'compatible_python': compatible_python if not compatible else None
        }

        # Save to Redis
        if self.redis_client:
            try:
                self.redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(cache_data)
                )
                if compatible:
                    safe_print(f"    💾 Cached {package_name}=={version} for Python {python_context} in Redis.")
                else:
                    safe_print(f"    💾 Cached incompatibility: {package_name} needs Python {compatible_python} (Redis)")
            except Exception as e:
                safe_print(f"    ⚠️ Redis cache write error: {e}")

        # Save to file cache
        if hasattr(self, '_file_cache'):
            self._file_cache[cache_key] = cache_data
            self._save_file_cache()
            if compatible:
                safe_print(f"    💾 Cached {package_name}=={version} for Python {python_context} in file cache.")
            else:
                safe_print(f"    💾 Cached incompatibility: {package_name} needs Python {compatible_python} (file)")

    def invalidate_cache_entry(self, package_name: str, python_context: str):
        """(NEW) Explicitly remove a cache entry, e.g., after an install failure."""
        cache_key = self._get_cache_key(package_name, python_context)
        safe_print(f"    🔥 Invalidating cache for {package_name} on Python {python_context} due to install error.")

        if self.redis_client:
            try:
                self.redis_client.delete(cache_key)
            except Exception:
                pass # Ignore errors during invalidation

        if hasattr(self, '_file_cache') and cache_key in self._file_cache:
            del self._file_cache[cache_key]
            self._save_file_cache()
    
    def clear_expired_cache(self):
        """Remove all expired entries from cache."""
        current_time = time.time()
        
        # Clear file cache
        if hasattr(self, '_file_cache'):
            expired_keys = []
            for key, data in self._file_cache.items():
                if current_time - data.get('timestamp', 0) >= self.cache_ttl:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._file_cache[key]
            
            if expired_keys:
                self._save_file_cache()
                safe_print(f"    🧹 Cleared {len(expired_keys)} expired entries from file cache")
        
        
        # Redis entries expire automatically due to TTL

