"""
omnipkg - The "Freedom" Edition v2
An intelligent installer that lets pip run, then surgically cleans up downgrades
and isolates conflicting versions in deduplicated bubbles to guarantee a stable environment.
"""
import sys
import os
import io
import json
import subprocess
import shutil
import site
import pickle
import zipfile
import hashlib
import tempfile
import re
import importlib.metadata
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from importlib.metadata import Distribution, version
from .package_meta_builder import omnipkgMetadataGatherer
import redis
import zlib
import requests as http_requests
import filelock
from filelock import FileLock
from packaging.utils import canonicalize_name
from packaging.version import parse as parse_version, InvalidVersion
from .i18n import _, LANG_INFO, SUPPORTED_LANGUAGES
try:
    # On Python >= 3.11, this will use the built-in library.
    import tomllib
except ModuleNotFoundError:
    # On Python < 3.11, this will use the `tomli` package installed from pyproject.toml.
    # The `as tomllib` alias makes the rest of the code work seamlessly.
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
    Dynamically and RECURSIVELY loads all core dependencies for omnipkg itself,
    including transitive dependencies (dependencies of dependencies).
    """
    try:
        all_deps = set()
        to_check = {'omnipkg'}
        checked = set()
        while to_check:
            pkg_name = to_check.pop()
            if pkg_name in checked:
                continue
            checked.add(pkg_name)
            try:
                dist = importlib.metadata.distribution(pkg_name)
                if dist.requires:
                    for req_str in dist.requires:
                        match = re.match('^[a-zA-Z0-9\\-_.]+', req_str)
                        if match:
                            dep_name = canonicalize_name(match.group(0))
                            all_deps.add(dep_name)
                            if dep_name not in checked:
                                to_check.add(dep_name)
            except importlib.metadata.PackageNotFoundError:
                continue
        if all_deps:
            return all_deps
    except Exception as e:
        print(_('⚠️ Warning: Could not recursively read package metadata: {}').format(e))
    try:
        current_path = Path(__file__).resolve()
        for parent in current_path.parents:
            pyproject_path = parent / 'pyproject.toml'
            if pyproject_path.exists():
                with open(pyproject_path, 'rb') as f:
                    toml_data = tomli.load(f)
                    dep_list = toml_data.get('project', {}).get('dependencies', [])
                    for req_str in dep_list:
                        match = re.match('^[a-zA-Z0-9\\-_.]+', req_str)
                        if match:
                            dependencies.add(canonicalize_name(match.group(0)))
                return dependencies
    except Exception as e:
        pass
    return {'redis', 'packaging', 'requests', 'python-magic', 'aiohttp', 'tqdm', 'filelock', 'tomli', 'aiosignal', 'attrs', 'frozenlist', 'multidict', 'yarl', 'idna', 'charset-normalizer', 'uv', 'typing-extensions'}
OMNIPKG_CORE_DEPS = _get_core_dependencies()
OMNIPKG_CORE_DEPS.add('omnipkg')
import json
import sys
import os
import locale as sys_locale
from pathlib import Path
from typing import List, Dict

class ConfigManager:
    """
    Manages loading and first-time creation of the omnipkg config file.
    """

    def __init__(self):
        self.config_dir = Path.home() / '.config' / 'omnipkg'
        self.config_path = self.config_dir / 'config.json'
        self.config = self._load_or_create_config()

    def _get_bin_paths(self) -> List[str]:
        """Gets a list of standard binary paths to search for executables."""
        paths = set()
        paths.add(str(Path(sys.executable).parent))
        for path in ['/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']:
            if Path(path).exists():
                paths.add(path)
        return sorted(list(paths))

    def _get_sensible_defaults(self) -> Dict:
        """Auto-detects paths for the current Python environment."""
        try:
            site_packages = site.getsitepackages()[0]
        except (IndexError, AttributeError):
            site_packages = str(Path.home() / '.local' / _('python{}.{}').format(sys.version_info.major, sys.version_info.minor) / 'site-packages')
        return {'site_packages_path': site_packages, 'multiversion_base': str(Path(site_packages) / '.omnipkg_versions'), 'python_executable': sys.executable, 'builder_script_path': str(Path(__file__).parent / 'package_meta_builder.py'), 'redis_host': 'localhost', 'redis_port': 6379, 'redis_key_prefix': 'omnipkg:pkg:', 'install_strategy': 'stable-main', 'uv_executable': 'uv', 'paths_to_index': self._get_bin_paths(), 'language': self._get_system_lang_code()}

    def _get_system_lang_code(self):
        """Helper to get a valid system language code."""
        try:
            lang_code = sys_locale.getlocale()[0]
            if lang_code and '_' in lang_code:
                lang_code = lang_code.split('_')[0]
            return lang_code if lang_code in SUPPORTED_LANGUAGES else 'en'
        except Exception:
            return 'en'

    def _first_time_setup(self) -> Dict:
        """Interactive and user-friendly setup for the first time the tool is run."""
        defaults = self._get_sensible_defaults()
        _.set_language(defaults['language'])
        print('🌍 ' + _("Welcome to omnipkg! Let's get you configured."))
        print('-' * 60)
        detected_lang_code = defaults['language']
        detected_lang_info = LANG_INFO.get(detected_lang_code, LANG_INFO['en'])
        print(_("We've auto-detected your system language as: {}").format(detected_lang_info['native']))
        print('\n' + _('Please select your preferred language by typing the number:'))
        sorted_langs = sorted(LANG_INFO.items(), key=lambda item: item[1]['name'])
        lang_codes_by_index = [code for code, data in sorted_langs]
        for i, (code, data) in enumerate(sorted_langs):
            print(_(' {}) {} - {}').format(i + 1, data['native'], data['hello']))
        chosen_code = None
        while chosen_code is None:
            prompt = _("\nEnter choice (1-{}) or press Enter to accept '{}': ").format(len(sorted_langs), detected_lang_info['native'])
            try:
                response = input(prompt).strip()
                if not response:
                    chosen_code = detected_lang_code
                    break
                choice_idx = int(response) - 1
                if 0 <= choice_idx < len(lang_codes_by_index):
                    chosen_code = lang_codes_by_index[choice_idx]
                else:
                    print(_('❌ Invalid number. Please try again.'))
            except ValueError:
                print(_("❌ That's not a number. Please try again."))
            except (KeyboardInterrupt, EOFError):
                print('\n' + _('Setup cancelled.'))
                sys.exit(1)
        defaults['language'] = chosen_code
        _.set_language(chosen_code)
        chosen_lang_info = LANG_INFO.get(chosen_code, LANG_INFO['en'])
        print('\n' + _('✅ Language set to: {}').format(chosen_lang_info['native']))
        print('-' * 60)
        print(_('Auto-detecting paths for your environment. Press Enter to accept defaults.\n'))
        print(_('📦 Choose your default installation strategy:'))
        print(_('   1) stable-main:  Prioritize a stable main environment. (Recommended)'))
        print(_('   2) latest-active: Prioritize having the latest versions active.'))
        strategy = input(_('   Enter choice (1 or 2) [1]: ')).strip() or '1'
        defaults['install_strategy'] = 'stable-main' if strategy == '1' else 'latest-active'
        bubble_path = input(_(f"Path for version bubbles [{defaults['multiversion_base']}]: ")).strip() or defaults['multiversion_base']
        defaults['multiversion_base'] = bubble_path
        python_path = input(_(f"Python executable path [{defaults['python_executable']}]: ")).strip() or defaults['python_executable']
        defaults['python_executable'] = python_path
        uv_path = input(_(f"uv executable path [{defaults['uv_executable']}]: ")).strip() or defaults['uv_executable']
        defaults['uv_executable'] = uv_path
        redis_host = input(_(f"Redis host [{defaults['redis_host']}]: ")).strip() or defaults['redis_host']
        defaults['redis_host'] = redis_host
        redis_port = input(_(f"Redis port [{defaults['redis_port']}]: ")).strip() or str(defaults['redis_port'])
        defaults['redis_port'] = int(redis_port)
        self._save_config(defaults)
        print(f'\n✅ ' + _('Configuration saved to {path}.').format(path=str(self.config_path)))
        print(_('   You can edit this file manually later.'))
        return defaults

    def _load_or_create_config(self) -> Dict:
        """
        Loads the config file, or triggers first-time setup.
        """
        if not self.config_path.exists():
            return self._first_time_setup()
        config_is_updated = False
        with open(self.config_path, 'r') as f:
            try:
                user_config = json.load(f)
            except json.JSONDecodeError:
                print('⚠️  ' + _('Warning: Config file is corrupted. Starting fresh.'))
                return self._first_time_setup()
        defaults = self._get_sensible_defaults()
        for key, default_value in defaults.items():
            if key not in user_config:
                print(f'🔧 ' + _("Updating config: Adding missing key '{key}'.").format(key=key))
                user_config[key] = default_value
                config_is_updated = True
        if config_is_updated:
            self._save_config(user_config)
            print(_('✅ Config file updated successfully.'))
        return user_config

    def _save_config(self, config_dict):
        """Save a given configuration dictionary to file."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(config_dict, f, indent=4)

    def get(self, key, default=None):
        """Get a configuration value, with an optional default."""
        return self.config.get(key, default)

    def set(self, key, value):
        """Set a configuration value and save."""
        self.config[key] = value
        self._save_config(self.config)

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
        """Register a file in Redis and JSON registry without verbose logging."""
        file_hash = self._get_file_hash(file_path)
        redis_key = _('{}bubble:{}:{}:file_paths').format(self.config['redis_key_prefix'], pkg_name, version)
        path_str = str(file_path)
        with self.parent_omnipkg.redis_client.pipeline() as pipe:
            pipe.sadd(redis_key, path_str)
            pipe.execute()
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
                redis_key = _('{}bubble:{}:{}:file_paths').format(self.config['redis_key_prefix'], pkg_name, version)
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

    def _find_owner_package(self, file_path: Path, temp_install_path: Path, installed_tree: Dict) -> str:
        """
        Enhanced version that better identifies package ownership.
        """
        try:
            relative_path = file_path.relative_to(temp_install_path)
            path_parts = relative_path.parts
            for pkg_name, pkg_info in installed_tree.items():
                pkg_files = pkg_info.get('files', [])
                if file_path in pkg_files:
                    return pkg_name
            if len(path_parts) > 0:
                for i in range(len(path_parts)):
                    potential_pkg = path_parts[i].replace('_', '-')
                    if potential_pkg in installed_tree:
                        return potential_pkg
                return path_parts[0]
            return 'unknown'
        except ValueError:
            return 'unknown'

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

    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        """
        Builds or loads a FAST hash index using package metadata when possible,
        falling back to filesystem scan only when needed.
        """
        if not self.parent_omnipkg.redis_client:
            self.parent_omnipkg.connect_redis()
        redis_key = _('{}main_env:file_hashes').format(self.config['redis_key_prefix'])
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
        registry_key = '{}bubble_locations'.format(self.config['redis_key_prefix'])
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
        registry_key = _('{}bubble_locations').format(self.config['redis_key_prefix'])
        bubble_id = bubble_path.name
        redis_bubble_data = {**manifest_data, 'path': str(bubble_path), 'manifest_path': str(manifest_path), 'bubble_id': bubble_id}
        try:
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                pipe.hset(registry_key, bubble_id, json.dumps(redis_bubble_data))
                for pkg_name, pkg_info in installed_tree.items():
                    canonical_pkg_name = canonicalize_name(pkg_name)
                    main_pkg_key = f"{self.config['redis_key_prefix']}{canonical_pkg_name}"
                    version_str = pkg_info['version']
                    pipe.hset(main_pkg_key, f'bubble_version:{version_str}', 'true')
                    pipe.sadd(_('{}:installed_versions').format(main_pkg_key), version_str)
                    pipe.sadd(f"{self.config['redis_key_prefix']}index", canonical_pkg_name)
                for pkg_name, pkg_info in installed_tree.items():
                    pkg_version_key = '{}=={}'.format(canonicalize_name(pkg_name), pkg_info['version'])
                    pipe.hset(_('{}pkg_to_bubble').format(self.config['redis_key_prefix']), pkg_version_key, bubble_id)
                size_category = 'small' if size_mb < 10 else 'medium' if size_mb < 100 else 'large'
                pipe.sadd(_('{}bubbles_by_size:{}').format(self.config['redis_key_prefix'], size_category), bubble_id)
                pipe.execute()
            print(_('    📝 Created manifest and registered bubble for {} packages ({} MB).').format(len(installed_tree), size_mb))
        except Exception as e:
            print(_('    ⚠️ Warning: Failed to register bubble in Redis: {}').format(e))
            print(_('    📝 Local manifest created at {}').format(manifest_path))

    def get_bubble_info(self, bubble_id: str) -> dict:
        """
        Retrieves comprehensive bubble information from Redis registry.
        """
        registry_key = _('{}bubble_locations').format(self.config['redis_key_prefix'])
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
            bubble_id = self.parent_omnipkg.redis_client.hget(_('{}pkg_to_bubble').format(self.config['redis_key_prefix']), pkg_key)
            return [bubble_id] if bubble_id else []
        else:
            pattern = f'{pkg_name}==*'
            matching_keys = []
            for key in self.parent_omnipkg.redis_client.hkeys(_('{}pkg_to_bubble').format(self.config['redis_key_prefix'])):
                if key.startswith(f'{pkg_name}=='):
                    bubble_id = self.parent_omnipkg.redis_client.hget(_('{}pkg_to_bubble').format(self.config['redis_key_prefix']), key)
                    matching_keys.append(bubble_id)
            return matching_keys

    def cleanup_old_bubbles(self, keep_latest: int=3, size_threshold_mb: float=500):
        """
        Cleanup old bubbles based on size and age, keeping most recent ones.
        """
        registry_key = _('{}bubble_locations').format(self.config['redis_key_prefix'])
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
                        pipe.hdel(_('{}pkg_to_bubble').format(self.config['redis_key_prefix']), pkg_key)
                    size_mb = data['stats']['bubble_size_mb']
                    size_category = 'small' if size_mb < 10 else 'medium' if size_mb < 100 else 'large'
                    pipe.srem(_('{}bubbles_by_size:{}').format(self.config['redis_key_prefix'], size_category), bubble_id)
                    bubble_path = Path(data['path'])
                    if bubble_path.exists():
                        shutil.rmtree(bubble_path, ignore_errors=True)
                pipe.execute()
            print(_('    ✅ Freed {} MB of storage.').format(total_size_freed))
        else:
            print(_('    ✅ No bubbles need cleanup.'))

class ImportHookManager:

    def __init__(self, multiversion_base: str, redis_client=None):
        self.multiversion_base = Path(multiversion_base)
        self.version_map = {}
        self.active_versions = {}
        self.hook_installed = False
        self.redis_client = redis_client
        self.config = ConfigManager().config
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

    def __init__(self, config_data: Dict):
        """
        Initializes the Omnipkg core engine with a given configuration.
        """
        self.config = config_data
        self.redis_client = None
        self._info_cache = {}
        self._installed_packages_cache = None
        self.multiversion_base = Path(self.config['multiversion_base'])
        self.connect_redis()
        self.hook_manager = ImportHookManager(str(self.multiversion_base), redis_client=self.redis_client)
        self.http_session = http_requests.Session()
        self.bubble_manager = BubbleIsolationManager(self.config, self)
        self.multiversion_base.mkdir(parents=True, exist_ok=True)
        self.hook_manager.load_version_map()
        self.hook_manager.install_import_hook()
        print(_('✅ Core dependencies loaded.'))

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
        Deletes ALL omnipkg data from the Redis knowledge base and then triggers a full rebuild.
        This is a "hard reset" for omnipkg's brain, not for the Python environment itself.
        """
        if not self.connect_redis():
            return 1
        scan_pattern = f"{self.config['redis_key_prefix']}*"
        print(_('\n🧠 omnipkg Knowledge Base Reset'))
        print('-' * 50)
        print(_("   This will DELETE all data matching '{}' from Redis.").format(scan_pattern))
        print(_('   It will then rebuild the knowledge base from the CURRENT state of your file system.'))
        print(_('   ⚠️  This command does NOT uninstall any Python packages.'))
        if not force:
            confirm = input(_('\n🤔 Are you sure you want to proceed? (y/N): ')).lower().strip()
            if confirm != 'y':
                print(_('🚫 Reset cancelled.'))
                return 1
        print(_('\n🗑️  Clearing knowledge base...'))
        try:
            keys_found = list(self.redis_client.scan_iter(match=scan_pattern))
            if keys_found:
                delete_command = self.redis_client.unlink if hasattr(self.redis_client, 'unlink') else self.redis_client.delete
                delete_command(*keys_found)
                print(_('   ✅ Cleared {} cached entries from Redis.').format(len(keys_found)))
            else:
                print(_('   ✅ Knowledge base was already clean.'))
        except Exception as e:
            print(_('   ❌ Failed to clear knowledge base: {}').format(e))
            return 1
        self._info_cache.clear()
        self._installed_packages_cache = None
        return self.rebuild_knowledge_base(force=True)

    def rebuild_knowledge_base(self, force: bool=False):
        """Runs a full metadata build process without deleting first."""
        print(_('🧠 Forcing a full rebuild of the knowledge base...'))
        try:
            cmd = [self.config['python_executable'], self.config['builder_script_path']]
            if force:
                cmd.append('--force')
            subprocess.run(cmd, check=True, timeout=900)
            self._info_cache.clear()
            self._installed_packages_cache = None
            print(_('✅ Knowledge base rebuilt successfully.'))
            return 0
        except subprocess.CalledProcessError as e:
            print(_('    ❌ Knowledge base rebuild failed with exit code {}.').format(e.returncode))
            return 1
        except Exception as e:
            print(_('    ❌ An unexpected error occurred during knowledge base rebuild: {}').format(e))
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

    def _synchronize_knowledge_base_with_reality(self):
        """
        Self-healing function. Compares the file system (ground truth) with Redis (cache)
        and reconciles any differences. This function relies on the globally imported `_`
        for translations and should NOT assign any value to it locally.
        """
        print(_('🧠 Performing self-healing sync of knowledge base...'))
        if not self.redis_client:
            self.connect_redis()
        all_known_packages = self.redis_client.smembers('{}index'.format(self.config['redis_key_prefix']))
        packages_to_check = set(all_known_packages)
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
                main_key = f"{self.config['redis_key_prefix']}{pkg_name}"
                real_active_version = None
                try:
                    real_active_version = importlib.metadata.version(pkg_name)
                except importlib.metadata.PackageNotFoundError:
                    pass
                real_bubbled_versions = set()
                if self.multiversion_base.exists():
                    for bubble_dir in self.multiversion_base.iterdir():
                        if not bubble_dir.is_dir():
                            continue
                        try:
                            dir_pkg_name, version = bubble_dir.name.rsplit('-', 1)
                            if dir_pkg_name == pkg_name:
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
                    pipe.hdel(main_key, 'bubble_version:{}'.format(version))
                    fixed_count += 1
                missing_bubbles = real_bubbled_versions - cached_bubbled_versions
                for version in missing_bubbles:
                    pipe.hset(main_key, 'bubble_version:{}'.format(version), 'true')
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
        redis_key = _('{}main_env:file_hashes').format(self.config['redis_key_prefix'])
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
            self._installed_packages_cache = self.redis_client.hgetall(_('{}versions').format(self.config['redis_key_prefix']))
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
        Atomically updates the knowledge base for all changed packages. It runs the
        builder for new/upgraded packages and runs a precise cleanup for uninstalled ones.
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
                gatherer = omnipkgMetadataGatherer(config=self.config, force_refresh=True)
                gatherer.redis_client = self.redis_client
                gatherer.run(targeted_packages=changed_specs)
            if uninstalled:
                print(_('   -> Cleaning up {} uninstalled package(s) from Redis...').format(len(uninstalled)))
                with self.redis_client.pipeline() as pipe:
                    for pkg_name, uninstalled_version in uninstalled.items():
                        c_name = canonicalize_name(pkg_name)
                        main_key = f"{self.config['redis_key_prefix']}{c_name}"
                        version_key = _('{}:{}').format(main_key, uninstalled_version)
                        versions_set_key = f'{main_key}:installed_versions'
                        pipe.delete(version_key)
                        pipe.srem(versions_set_key, uninstalled_version)
                        if self.redis_client.hget(main_key, 'active_version') == uninstalled_version:
                            pipe.hdel(main_key, 'active_version')
                        pipe.hdel(main_key, _('bubble_version:{}').format(uninstalled_version))
                    pipe.execute()
            self._info_cache.clear()
            self._installed_packages_cache = None
            print(_('✅ Knowledge base updated successfully.'))
        except Exception as e:
            print(_('    ⚠️ Failed to update knowledge base for delta: {}').format(e))
            import traceback
            traceback.print_exc()

    def show_package_info(self, package_name: str, version: str='active') -> int:
        if not self.connect_redis():
            return 1
        self._synchronize_knowledge_base_with_reality()
        try:
            self._show_enhanced_package_data(package_name, version)
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

    def _show_enhanced_package_data(self, package_name: str, version: str):
        r = self.redis_client
        overview_key = '{}{}'.format(self.config['redis_key_prefix'], package_name.lower())
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
        available_versions = [active_ver] + bubble_versions if active_ver else bubble_versions
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
        overview_key = f"{self.config['redis_key_prefix']}{package_name.lower()}"
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
        version_key = f"{self.config['redis_key_prefix']}{package_name.lower()}:{version}"
        if not r.exists(version_key):
            print(_('❌ No detailed data found for {} v{}').format(package_name, version))
            return
        data = r.hgetall(version_key)
        important_fields = [('name', '📦 Package'), ('Version', '🏷️  Version'), ('Summary', '📝 Summary'), ('Author', '👤 Author'), ('Author-email', '📧 Email'), ('License', '⚖️  License'), ('Home-page', '🌐 Homepage'), ('Platform', '💻 Platform'), ('dependencies', '🔗 Dependencies'), ('Requires-Dist', '📋 Requires')]
        print(_('The data is fetched from Redis key: {}').format(version_key))
        for field_name, display_name in important_fields:
            if field_name in data:
                value = data[field_name]
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
            snapshot_key = f"{self.config['redis_key_prefix']}snapshot:last_known_good"
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

    def smart_install(self, packages: List[str], dry_run: bool=False) -> int:
        if not self.connect_redis():
            return 1
        if dry_run:
            print(_('🔬 Running in --dry-run mode. No changes will be made.'))
            return 0
        if not packages:
            print(_('🚫 No packages specified for installation.'))
            return 1
        install_strategy = self.config.get('install_strategy', 'stable-main')
        packages_to_process = list(packages)
        for pkg_spec in list(packages_to_process):
            pkg_name, requested_version = self._parse_package_spec(pkg_spec)
            if pkg_name.lower() == 'omnipkg':
                packages_to_process.remove(pkg_spec)
                active_omnipkg_version = self._get_active_version_from_environment('omnipkg')
                if not active_omnipkg_version:
                    print(_('⚠️ Warning: Cannot determine active omnipkg version. Proceeding with caution.'))
                if requested_version and active_omnipkg_version and (parse_version(requested_version) == parse_version(active_omnipkg_version)):
                    print(_('✅ omnipkg=={} is already the active omnipkg. No bubble needed.').format(requested_version))
                    continue
                print(_("✨ Special handling: omnipkg '{}' requested. This will be installed into an isolated bubble, not as the active omnipkg.").format(pkg_spec))
                if not requested_version:
                    print(_('  (No version specified for omnipkg; attempting to bubble the latest stable version)'))
                    print(_("  Skipping bubbling of 'omnipkg' without a specific version for now."))
                    continue
                bubble_dir_name = 'omnipkg-{}'.format(requested_version)
                target_bubble_path = Path(self.config['multiversion_base']) / bubble_dir_name
                wheel_url = self._get_wheel_url_from_pypi(pkg_name, requested_version)
                if not wheel_url:
                    print(_('❌ Could not find a compatible wheel for omnipkg=={}. Cannot create bubble.').format(requested_version))
                    continue
                if not self._extract_wheel_into_bubble(wheel_url, target_bubble_path, pkg_name, requested_version):
                    print(_('❌ Failed to create bubble for omnipkg=={}.').format(requested_version))
                    continue
                self._register_package_in_knowledge_base(pkg_name, requested_version, str(target_bubble_path), 'bubble')
                print(_('✅ omnipkg=={} successfully bubbled.').format(requested_version))
        if not packages_to_process:
            print(_('\n🎉 All package operations complete.'))
            return 0
        print(_("🚀 Starting install with policy: '{}'").format(install_strategy))
        resolved_packages = self._resolve_package_versions(packages_to_process)
        if not resolved_packages:
            print(_('❌ Could not resolve any packages to install. Aborting.'))
            return 1
        sorted_packages = self._sort_packages_for_install(resolved_packages, strategy=install_strategy)
        if sorted_packages != resolved_packages:
            print(_('🔄 Reordered packages for optimal installation: {}').format(', '.join(sorted_packages)))
        user_requested_cnames = {canonicalize_name(self._parse_package_spec(p)[0]) for p in packages}
        any_installations_made = False
        for package_spec in sorted_packages:
            print('\n' + '─' * 60)
            print(_('📦 Processing: {}').format(package_spec))
            print('─' * 60)
            satisfaction_check = self._check_package_satisfaction([package_spec], strategy=install_strategy)
            if satisfaction_check['all_satisfied']:
                print(_('✅ Requirement already satisfied: {}').format(package_spec))
                continue
            packages_to_install = satisfaction_check['needs_install']
            if not packages_to_install:
                continue
            print(_('\n📸 Taking LIVE pre-installation snapshot...'))
            packages_before = self.get_installed_packages(live=True)
            print(_('    - Found {} packages').format(len(packages_before)))
            print(_('\n⚙️ Running pip install for: {}...').format(', '.join(packages_to_install)))
            return_code = self._run_pip_install(packages_to_install)
            if return_code != 0:
                print(_('❌ Pip installation failed for {}. Continuing...').format(package_spec))
                continue
            any_installations_made = True
            print(_('✅ Installation completed for: {}').format(package_spec))
            print(_('\n🔬 Analyzing post-installation changes...'))
            packages_after = self.get_installed_packages(live=True)
            replacements = self._detect_version_replacements(packages_before, packages_after)
            if replacements:
                for rep in replacements:
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
                    print(_('\n🛡️ STABILITY PROTECTION ACTIVATED!'))
                    for fix in all_changes_to_fix:
                        print(_('    -> Protecting stable env. Bubbling {} version: {} v{}').format(fix['change_type'], fix['package'], fix['new_version']))
                        bubble_created = self.bubble_manager.create_isolated_bubble(fix['package'], fix['new_version'])
                        if bubble_created:
                            bubble_path_str = str(self.multiversion_base / f"{fix['package']}-{fix['new_version']}")
                            self.hook_manager.refresh_bubble_map(fix['package'], fix['new_version'], bubble_path_str)
                            self.hook_manager.validate_bubble(fix['package'], fix['new_version'])
                            print(_("    🔄 Restoring '{}' to stable version v{} in main environment...").format(fix['package'], fix['old_version']))
                            subprocess.run([self.config['python_executable'], '-m', 'pip', 'install', '--quiet', f"{fix['package']}=={fix['old_version']}"], capture_output=True, text=True)
                    print(_('\n✅ Stability protection complete!'))
                else:
                    print(_('✅ No changes to existing packages detected. Installation completed safely.'))
            elif install_strategy == 'latest-active':
                # For latest-active: bubble the OLD/REPLACED versions, keep NEW/REQUESTED versions active
                versions_to_bubble = []
                
                # Check for version changes from the before/after snapshots
                for pkg_name in set(packages_before.keys()) | set(packages_after.keys()):
                    old_version = packages_before.get(pkg_name)
                    new_version = packages_after.get(pkg_name)
                    
                    if old_version and new_version and old_version != new_version:
                        # Version changed - we need to bubble the OLD version that was replaced
                        change_type = "upgraded" if parse_version(new_version) > parse_version(old_version) else "downgraded"
                        versions_to_bubble.append({
                            'package': pkg_name,
                            'version_to_bubble': old_version,  # Bubble the OLD version
                            'version_staying_active': new_version,  # Keep NEW version in main
                            'change_type': change_type,
                            'user_requested': canonicalize_name(pkg_name) in user_requested_cnames
                        })
                    elif not old_version and new_version:
                        # New package installed - nothing to bubble, just note it
                        print(_('    ✅ New package installed: {} v{} (active in main environment)').format(pkg_name, new_version))
                
                if versions_to_bubble:
                    print(_('\n🛡️ LATEST-ACTIVE STRATEGY: Preserving replaced versions in bubbles'))
                    for item in versions_to_bubble:
                        request_type = "requested" if item['user_requested'] else "dependency"
                        print(_('    -> Bubbling replaced {} version: {} v{} (keeping v{} active)').format(
                            request_type, item['package'], item['version_to_bubble'], item['version_staying_active']))
                        
                        # Create bubble for the OLD/REPLACED version
                        bubble_created = self.bubble_manager.create_isolated_bubble(item['package'], item['version_to_bubble'])
                        if bubble_created:
                            bubble_path_str = str(self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}")
                            self.hook_manager.refresh_bubble_map(item['package'], item['version_to_bubble'], bubble_path_str)
                            self.hook_manager.validate_bubble(item['package'], item['version_to_bubble'])
                            print(_("    ✅ Successfully bubbled {} v{}").format(item['package'], item['version_to_bubble']))
                        else:
                            print(_("    ❌ Failed to bubble {} v{}").format(item['package'], item['version_to_bubble']))
                    
                    print(_('\n✅ Latest-active complete! Requested versions active, previous versions preserved.'))
                else:
                    print(_('✅ No version changes detected.'))
            print(_('\n🧠 Updating knowledge base for this package...'))
            final_state = self.get_installed_packages(live=True)
            self._run_metadata_builder_for_delta(packages_before, final_state)
            self._update_hash_index_for_delta(packages_before, final_state)
        if not any_installations_made:
            print(_('\n✅ All requirements were already satisfied.'))
            self._synchronize_knowledge_base_with_reality()
            return 0
        print(_('\n🧹 Performing final cleanup of redundant bubbles...'))
        final_active_packages = self.get_installed_packages(live=True)
        for pkg_name, active_version in final_active_packages.items():
            bubble_path = self.multiversion_base / f'{pkg_name}-{active_version}'
            if bubble_path.exists() and bubble_path.is_dir():
                print(_("    -> Found redundant bubble for active package '{0}=={1}'. Removing...").format(pkg_name, active_version))
                self.smart_uninstall([f'{pkg_name}=={active_version}'], force=True, install_type='bubble')
        print('\n' + '=' * 60)
        print(_('🎉 All package operations complete.'))
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
        main_key = f"{self.config['redis_key_prefix']}{c_name}"
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
        main_key = f"{self.config['redis_key_prefix']}{c_name}"
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
                    is_protected = inst['type'] == 'active' and (canonicalize_name(inst['name']) == 'omnipkg' or canonicalize_name(inst['name']) in OMNIPKG_CORE_DEPS)
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
                is_protected = item['type'] == 'active' and (canonicalize_name(item['name']) == 'omnipkg' or canonicalize_name(item['name']) in OMNIPKG_CORE_DEPS)
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
                main_key = f"{self.config['redis_key_prefix']}{c_name}"
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
                    self.redis_client.srem(f"{self.config['redis_key_prefix']}index", c_name)
            print(_('✅ Uninstallation complete.'))
            self._save_last_known_good_snapshot()
        return 0

    def revert_to_last_known_good(self, force: bool=False):
        """Compares the current env to the last snapshot and restores it."""
        if not self.connect_redis():
            return 1
        
        snapshot_key = f"{self.config['redis_key_prefix']}snapshot:last_known_good"
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
        
        # Store the current install strategy before changing it
        original_strategy = self.config.get('install_strategy', 'multiversion')
        strategy_changed = False
        
        try:
            # Set install strategy to latest-active for consistent revert behavior
            if original_strategy != 'latest-active':
                print(_('   ⚙️  Temporarily setting install strategy to latest-active for revert...'))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', 'latest-active'], 
                                          capture_output=True, text=True, check=True)
                    strategy_changed = True
                    print(_('   ✅ Install strategy temporarily set to latest-active'))
                    
                    # Refresh config to reflect the change
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                    
                except Exception as e:
                    print(_('   ⚠️  Failed to set install strategy to latest-active: {}').format(e))
                    print(_('   ℹ️  Continuing with current strategy: {}').format(original_strategy))
            else:
                print(_('   ℹ️  Install strategy already set to latest-active'))
            
            # Perform the revert operations
            if to_uninstall:
                self.smart_uninstall(to_uninstall, force=True)
            
            packages_to_install = to_install + to_fix
            if packages_to_install:
                self.smart_install(packages_to_install)
            
            print(_('\n✅ Environment successfully reverted to the last known good state.'))
            return 0
            
        finally:
            # Always attempt to restore the original install strategy
            if strategy_changed and original_strategy != 'latest-active':
                print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
                try:
                    result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', original_strategy], 
                                          capture_output=True, text=True, check=True)
                    print(_('   ✅ Install strategy restored to: {}').format(original_strategy))
                    
                    # Refresh config to reflect the restoration
                    from omnipkg.core import ConfigManager
                    self.config = ConfigManager().config
                    
                except Exception as e:
                    print(_('   ⚠️  Failed to restore install strategy to {}: {}').format(original_strategy, e))
                    print(_('   💡 You may need to manually restore it with: omnipkg config set install_strategy {}').format(original_strategy))
            elif not strategy_changed:
                print(_('   ℹ️  Install strategy unchanged: {}').format(original_strategy))

    def _check_package_satisfaction(self, packages: List[str], strategy: str) -> dict:
        """
        Checks if package requirements are satisfied based on the install strategy.
        - 'latest-active': Only satisfied if the EXACT version is already ACTIVE. Ignores bubbles.
        - 'stable-main': Satisfied if the version is active OR exists in a bubble.
        """
        satisfied = set()
        needs_install = list(packages)
        if strategy == 'latest-active':
            truly_satisfied = set()
            for pkg_spec in packages:
                try:
                    pkg_name, requested_version = self._parse_package_spec(pkg_spec)
                    if not requested_version:
                        continue
                    active_version = self._get_active_version_from_environment(pkg_name)
                    if active_version and parse_version(active_version) == parse_version(requested_version):
                        truly_satisfied.add(pkg_spec)
                except Exception:
                    continue
            satisfied.update(truly_satisfied)
            needs_install = [pkg for pkg in packages if pkg not in satisfied]
        elif strategy == 'stable-main':
            remaining_packages = []
            for pkg_spec in packages:
                try:
                    pkg_name, requested_version = self._parse_package_spec(pkg_spec)
                    if not requested_version:
                        remaining_packages.append(pkg_spec)
                        continue
                    active_version = self._get_active_version_from_environment(pkg_name)
                    if active_version and parse_version(active_version) == parse_version(requested_version):
                        satisfied.add(pkg_spec)
                        continue
                    bubble_path = self.multiversion_base / f'{pkg_name}-{requested_version}'
                    if bubble_path.exists() and bubble_path.is_dir():
                        satisfied.add(pkg_spec)
                        print(_('    ⚡ Found existing bubble: {}').format(pkg_spec))
                        continue
                    remaining_packages.append(pkg_spec)
                except Exception:
                    remaining_packages.append(pkg_spec)
            if remaining_packages:
                needs_install = remaining_packages
        return {'all_satisfied': len(needs_install) == 0, 'satisfied': sorted(list(satisfied)), 'needs_install': needs_install}

    def get_package_info(self, package_name: str, version: str) -> Optional[Dict]:
        if not self.redis_client:
            self.connect_redis()
        main_key = f"{self.config['redis_key_prefix']}{package_name.lower()}"
        if version == 'active':
            version = self.redis_client.hget(main_key, 'active_version')
            if not version:
                return None
        version_key = f'{main_key}:{version}'
        return self.redis_client.hgetall(version_key)

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
        Fetches the latest version of a package directly from the PyPI JSON API.
        This is more reliable than `pip dry-run` as it correctly handles pre-releases.
        """
        try:
            url = f'https://pypi.org/pypi/{canonicalize_name(package_name)}/json'
            response = self.http_session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'releases' in data and data['releases']:
                versions = [parse_version(v) for v in data['releases'].keys() if not parse_version(v).is_prerelease]
                if not versions:
                    versions = [parse_version(v) for v in data['releases'].keys()]
                if versions:
                    return str(max(versions))
            return data.get('info', {}).get('version')
        except Exception as e:
            print(_("    ⚠️  API Error while fetching version for '{}': {}").format(package_name, e))
            return None

    def get_available_versions(self, package_name: str) -> List[str]:
        """
        Correctly gets all available versions (active and bubbled) for a package
        by checking all relevant keys in the knowledge base.
        """
        c_name = canonicalize_name(package_name)
        main_key = f"{self.config['redis_key_prefix']}{c_name}"
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
        all_pkg_names = self.redis_client.smembers(f"{self.config['redis_key_prefix']}index")
        if pattern:
            all_pkg_names = {name for name in all_pkg_names if pattern.lower() in name.lower()}
        print(_('📋 Found {} matching package(s):').format(len(all_pkg_names)))
        for pkg_name in sorted(list(all_pkg_names)):
            main_key = f"{self.config['redis_key_prefix']}{pkg_name}"
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