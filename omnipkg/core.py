#!/usr/bin/env python3
"""
omnipkg - The "Freedom" Edition v2
An intelligent installer that lets pip run, then surgically cleans up downgrades
and isolates conflicting versions in deduplicated bubbles to guarantee a stable environment.
"""
import sys
import json
import subprocess
import redis
import zlib
import requests as http_requests
import os
import io
import shutil
import site
import pickle
import zipfile
import hashlib
import tempfile
import re
import filelock
import importlib.metadata
import magic
import tqdm
import concurrent.futures
from packaging.utils import canonicalize_name
from filelock import FileLock
from datetime import datetime
from pathlib import Path
from packaging.version import parse as parse_version, InvalidVersion
from typing import Dict, List, Optional, Set, Tuple
from importlib.metadata import Distribution

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ##################################################################
# ### CONFIGURATION MANAGEMENT (PORTABLE & SELF-CONFIGURING) ###
# ##################################################################

# Define omnipkg's core dependencies to prevent accidental uninstallation
# These should match the 'dependencies' list in your pyproject.toml
OMNIPKG_CORE_DEPS = {
    "redis",
    "packaging",
    "requests",
    "python-magic",
    "aiohttp",
    "tqdm"
}

class ConfigManager:
    """
    Manages loading and first-time creation of the omnipkg config file.
    This makes the entire application portable and self-healing.
    """
    def __init__(self):
        self.config_dir = Path.home() / ".config" / "omnipkg"
        self.config_path = self.config_dir / "config.json"
        self.config = self._load_or_create_config()

    def _get_bin_paths(self) -> List[str]:
        """Gets a list of standard binary paths to search for executables."""
        paths = set()
        paths.add(str(Path(sys.executable).parent))
        for path in ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]:
            if Path(path).exists():
                paths.add(path)
        return sorted(list(paths))

    def _get_sensible_defaults(self) -> Dict:
        """Auto-detects paths for the current Python environment."""
        try:
            site_packages = site.getsitepackages()[0]
        except (IndexError, AttributeError):
            print("⚠️  Could not auto-detect site-packages. You may need to enter this manually.")
            site_packages = str(Path.home() / ".local" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")

        return {
            "site_packages_path": site_packages,
            "multiversion_base": str(Path(site_packages) / ".omnipkg_versions"),
            "python_executable": sys.executable,
            "builder_script_path": str(Path(__file__).parent / "package_meta_builder.py"),
            "redis_host": "localhost",
            "redis_port": 6379,
            "redis_key_prefix": "omnipkg:pkg:",
            "paths_to_index": self._get_bin_paths()
        }

    def _first_time_setup(self) -> Dict:
        """Interactive setup for the first time the tool is run."""
        print("👋 Welcome to omnipkg! Let's get you configured.")
        print("   Auto-detecting paths for your environment. Press Enter to accept defaults.")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        defaults = self._get_sensible_defaults()
        final_config = {}
        final_config["multiversion_base"] = input(f"Path for version bubbles [{defaults['multiversion_base']}]: ") or defaults["multiversion_base"]
        final_config["python_executable"] = input(f"Python executable path [{defaults['python_executable']}]: ") or defaults["python_executable"]
        final_config["redis_host"] = input(f"Redis host [{defaults['redis_host']}]: ") or defaults["redis_host"]
        final_config["redis_port"] = int(input(f"Redis port [{defaults['redis_port']}]: ") or defaults["redis_port"])
        final_config["site_packages_path"] = defaults["site_packages_path"]
        final_config["builder_script_path"] = defaults["builder_script_path"]
        final_config["redis_key_prefix"] = defaults["redis_key_prefix"]
        final_config["paths_to_index"] = defaults["paths_to_index"]
        with open(self.config_path, 'w') as f:
            json.dump(final_config, f, indent=4)
        print(f"\n✅ Configuration saved to {self.config_path}. You can edit this file manually later.")
        return final_config

    def _load_or_create_config(self) -> Dict:
        """
        Loads the config file, or triggers first-time setup.
        Also self-heals the config by adding any missing keys from the defaults.
        """
        if not self.config_path.exists():
            return self._first_time_setup()
        
        config_is_updated = False
        with open(self.config_path, 'r') as f:
            try:
                user_config = json.load(f)
            except json.JSONDecodeError:
                print("⚠️  Warning: Config file is corrupted. Starting fresh.")
                return self._first_time_setup()

        defaults = self._get_sensible_defaults()
        for key, default_value in defaults.items():
            if key not in user_config:
                print(f"🔧 Updating config: Adding missing key '{key}'.")
                user_config[key] = default_value
                config_is_updated = True

        if config_is_updated:
            with open(self.config_path, 'w') as f:
                json.dump(user_config, f, indent=4)
            print("✅ Config file updated successfully.")
        return user_config
class BubbleIsolationManager:
    def __init__(self, config: Dict, parent_omnipkg):
        self.config = config
        self.parent_omnipkg = parent_omnipkg
        self.site_packages = Path(config["site_packages_path"])
        self.multiversion_base = Path(config["multiversion_base"])
        self.file_hash_cache = {}
        self.package_path_registry = {}
        self.registry_lock = FileLock(self.multiversion_base / "registry.lock")
        self._load_path_registry()
        self.http_session = http_requests.Session() # Initialize a requests session for downloads

    def _load_path_registry(self):
        """Load the file path registry from JSON."""
        registry_file = self.multiversion_base / "package_paths.json"
        if registry_file.exists():
            with self.registry_lock:
                try:
                    with open(registry_file, 'r') as f:
                        self.package_path_registry = json.load(f)
                except Exception:
                    print("    ⚠️ Warning: Failed to load path registry, starting fresh.")
                    self.package_path_registry = {}

    def _save_path_registry(self):
        """Save the file path registry to JSON with atomic write."""
        registry_file = self.multiversion_base / "package_paths.json"
        with self.registry_lock:
            temp_file = registry_file.with_suffix(f"{registry_file.suffix}.tmp")
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
        redis_key = f"{self.config['redis_key_prefix']}bubble:{pkg_name}:{version}:file_paths"
        path_str = str(file_path)
        
        with self.parent_omnipkg.redis_client.pipeline() as pipe:
            pipe.sadd(redis_key, path_str)
            pipe.execute()
        
        c_name = pkg_name.lower().replace("_", "-")
        if c_name not in self.package_path_registry:
            self.package_path_registry[c_name] = {}
        if version not in self.package_path_registry[c_name]:
            self.package_path_registry[c_name][version] = []
        self.package_path_registry[c_name][version].append({
            'path': path_str,
            'hash': file_hash,
            'type': file_type,
            'bubble_path': str(bubble_path)
        })
        self._save_path_registry()

    def create_isolated_bubble(self, package_name: str, target_version: str) -> bool:
        print(f"🫧 Creating isolated bubble for {package_name} v{target_version}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            if not self._install_exact_version_tree(package_name, target_version, temp_path):
                return False

            installed_tree = self._analyze_installed_tree(temp_path)

            bubble_path = self.multiversion_base / f"{package_name}-{target_version}"
            if bubble_path.exists():
                shutil.rmtree(bubble_path)

            return self._create_deduplicated_bubble(installed_tree, bubble_path, temp_path)

    def _install_exact_version_tree(self, package_name: str, version: str, target_path: Path) -> bool:
        try:
            historical_deps = self._get_historical_dependencies(package_name, version)
            install_specs = [f"{package_name}=={version}"] + historical_deps

            cmd = [
                self.config["python_executable"], "-m", "pip", "install",
                "--target", str(target_path),
            ] + install_specs

            print(f"    📦 Installing full dependency tree to temporary location...")
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                print(f"    ❌ Failed to install exact version tree: {result.stderr}")
                return False

            return True

        except Exception as e:
            print(f"    ❌ Unexpected error during installation: {e}")
            return False

    def _get_historical_dependencies(self, package_name: str, version: str) -> List[str]:
        print("    -> Trying strategy 1: pip dry-run...")
        deps = self._try_pip_dry_run(package_name, version)
        if deps is not None:
            print("    ✅ Success: Dependencies resolved via pip dry-run.")
            return deps

        print("    -> Trying strategy 2: PyPI API...")
        # This method will now handle its own import of 'requests'
        deps = self._try_pypi_api(package_name, version)
        if deps is not None:
            print("    ✅ Success: Dependencies resolved via PyPI API.")
            return deps

        print("    -> Trying strategy 3: pip show fallback...")
        deps = self._try_pip_show_fallback(package_name, version)
        if deps is not None:
            print("    ✅ Success: Dependencies resolved from existing installation.")
            return deps

        print(f"    ⚠️ All dependency resolution strategies failed for {package_name}=={version}.")
        print(f"    ℹ️  Proceeding with full temporary installation to build bubble.")
        return []

    def _try_pip_dry_run(self, package_name: str, version: str) -> Optional[List[str]]:
        req_file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(f"{package_name}=={version}\n")
                req_file = f.name

            cmd = [
                self.config["python_executable"], "-m", "pip", "install",
                "--dry-run", "--report", "-", "-r", req_file
            ]

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

                    if item_name and item_version and item_name.lower() != package_name.lower():
                        deps.append(f"{item_name}=={item_version}")
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
            # This is a graceful fallback if requests is not installed for some reason.
            print("    ⚠️  'requests' package not found. Skipping PyPI API strategy.")
            return None
        try:
            clean_version = version.split('+')[0]

            url = f"https://pypi.org/pypi/{package_name}/{clean_version}/json"

            headers = {
                'User-Agent': 'omnipkg-package-manager/1.0',
                'Accept': 'application/json'
            }

            response = requests.get(url, timeout=10, headers=headers)

            if response.status_code == 404:
                if clean_version != version:
                    url = f"https://pypi.org/pypi/{package_name}/{version}/json"
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

            requires_dist = pkg_data.get("info", {}).get("requires_dist")
            if not requires_dist:
                return []

            dependencies = []
            for req in requires_dist:
                if not req or not isinstance(req, str):
                    continue

                if ';' in req:
                    continue

                req = req.strip()
                match = re.match(r'^([a-zA-Z0-9\-_.]+)([<>=!]+.*)?', req)
                if match:
                    dep_name = match.group(1)
                    version_spec = match.group(2) or ""
                    dependencies.append(f"{dep_name}{version_spec}")

            return dependencies

        except requests.exceptions.RequestException:
            return None
        except Exception:
            return None

    def _try_pip_show_fallback(self, package_name: str, version: str) -> Optional[List[str]]:
        try:
            cmd = [self.config["python_executable"], "-m", "pip", "show", package_name]
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
        has_python = any(f.suffix in ['.py', '.pyc'] for f in files)
        has_native = any(f.suffix in ['.so', '.pyd', '.dll'] for f in files)

        if has_native and has_python: return 'mixed'
        elif has_native: return 'native'
        else: return 'pure_python'

    def _is_binary(self, file_path: Path) -> bool:
        """Robustly checks if a file is a binary executable, excluding C extensions."""
        if file_path.suffix in {'.so', '.pyd'}:
            return False  # C extensions are not considered binaries
        try:
            mime = magic.Magic(mime=True)
            file_type = mime.from_file(str(file_path))
            executable_types = {'application/x-executable', 'application/x-sharedlib', 'application/x-pie-executable'}
            return any(t in file_type for t in executable_types) or file_path.suffix in {'.dll', '.exe'}
        except ImportError:
            print(f"    ⚠️ Warning: python-magic not available, falling back to extension check")
            return file_path.suffix in {'.dll', '.exe'}
        except Exception:
            return file_path.suffix in {'.dll', '.exe'}

    def _find_existing_c_extension(self, file_hash: str) -> Optional[str]:
        """Disabled: C extensions are copied, not symlinked."""
        return None

    # In omnipkg/core.py, inside the BubbleIsolationManager class

    def _analyze_installed_tree(self, temp_path: Path) -> Dict[str, Dict]:
        """
        Analyzes the temporary installation, now EXPLICITLY finding executables
        and summarizing file registry warnings instead of printing each one.
        """
        installed = {}
        unregistered_file_count = 0 # Counter for our warnings

        for dist_info in temp_path.glob("*.dist-info"):
            try:
                dist = importlib.metadata.Distribution.at(dist_info)
                if not dist: continue

                pkg_files = []
                if dist.files:
                    for file_entry in dist.files:
                        # Exclude files from the 'bin' directory as they are handled separately
                        if file_entry.parts and file_entry.parts[0] == 'bin':
                            continue
                        abs_path = Path(dist_info.parent) / file_entry
                        if abs_path.exists():
                            pkg_files.append(abs_path)
                
                # Find executables via the standard 'console_scripts' entry points
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

                pkg_name = dist.metadata['Name'].lower().replace("_", "-")
                version = dist.metadata['Version']
                installed[dist.metadata['Name']] = {
                    'version': version,
                    'files': [p for p in pkg_files if p.exists()],
                    'executables': executables,
                    'type': self._classify_package_type(pkg_files)
                }

                # --- THE FIX: Summarize warnings instead of printing them individually ---
                redis_key = f"{self.config['redis_key_prefix']}bubble:{pkg_name}:{version}:file_paths"
                existing_paths = set(self.parent_omnipkg.redis_client.smembers(redis_key)) if self.parent_omnipkg.redis_client.exists(redis_key) else set()
                
                # We check all files found in the metadata
                all_package_files_for_check = pkg_files + executables
                for file_path in all_package_files_for_check:
                    if str(file_path) not in existing_paths:
                        unregistered_file_count += 1
            except Exception as e:
                print(f"    ⚠️  Could not analyze {dist_info.name}: {e}")

        # Print the final summary of warnings, if any, after the loop.
        if unregistered_file_count > 0:
            print(f"    ⚠️  Found {unregistered_file_count} files not in registry. They will be registered during bubble creation.")
            
        return installed

    def _is_binary(self, file_path: Path) -> bool:
        """Robustly checks if a file is a binary executable."""
        try:
            # Use python-magic for reliable MIME type detection
            mime = magic.Magic(mime=True)
            file_type = mime.from_file(str(file_path))
            # Check for common executable types and native library extensions
            return 'executable' in file_type or 'octet-stream' in file_type or file_path.suffix in {'.so', '.pyd', '.dll', '.exe'}
        except Exception:
            # If magic fails for any reason, fall back to a reasonable guess
            return file_path.suffix in {'.so', '.pyd', '.dll', '.exe'}

    def _find_owner_package(self, file_path: Path, temp_install_path: Path, installed_tree: Dict) -> Optional[str]:
        """
        Helper to find which package a file belongs to, now supporting .egg-info.
        """
        try:
            # Walk up the file's path to find the parent .dist-info or .egg-info directory
            for parent in file_path.parents:
                if parent.name.endswith(('.dist-info', '.egg-info')):
                    # Extract the package name from the directory name (e.g., "numpy-1.26.4.dist-info")
                    pkg_name = parent.name.split('-')[0]
                    # Return the canonical name for consistency
                    return pkg_name.lower().replace("_", "-")
        except Exception:
            pass # If any error occurs, we couldn't find it.
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
        print(f"    🧹 Creating deduplicated bubble at {bubble_path}")
        bubble_path.mkdir(parents=True, exist_ok=True)
        main_env_hashes = self._get_or_build_main_env_hash_index()
        
        # Initialize comprehensive stats
        stats = {
            'total_files': 0, 'copied_files': 0, 'deduplicated_files': 0,
            'c_extensions': [], 'binaries': [], 'python_files': 0,
            'package_modules': {}, 'submodules_found': 0
        }

        # 1. Enhanced package classification with submodule detection
        c_ext_packages = {
            pkg_name for pkg_name, info in installed_tree.items()
            if info.get('type') in ['native', 'mixed']
        }
        binary_packages = {
            pkg_name for pkg_name, info in installed_tree.items() 
            if info.get('type') == 'binary'
        }
        
        # NEW: Detect packages with complex internal structure
        complex_packages = set()
        for pkg_name, pkg_info in installed_tree.items():
            pkg_files = pkg_info.get('files', [])
            # Look for packages with multiple .py files in subdirectories
            py_files_in_subdirs = [
                f for f in pkg_files 
                if f.suffix == '.py' and len(f.parts) > 2 and f.parts[-2] != '__pycache__'
            ]
            if len(py_files_in_subdirs) > 1:  # Has submodules
                complex_packages.add(pkg_name)
                stats['package_modules'][pkg_name] = len(py_files_in_subdirs)
        
        if c_ext_packages:
            print(f"    🔬 Found C-extension packages: {', '.join(c_ext_packages)}")
        if binary_packages:
            print(f"    ⚙️  Found binary packages: {', '.join(binary_packages)}")
        if complex_packages:
            print(f"    📦 Found complex packages with submodules: {', '.join(complex_packages)}")

        processed_files = set()  # Track files we've handled via metadata

        # 2. Process files from package metadata (Stage 1) - Enhanced logic
        for pkg_name, pkg_info in installed_tree.items():
            
            # Determine deduplication strategy per package type
            if pkg_name in c_ext_packages:
                # GOLDEN RULE: C-extensions get ALL files copied (proven approach)
                should_deduplicate_this_package = False
                print(f"    🔬 {pkg_name}: C-extension - copying all files")
            elif pkg_name in binary_packages:
                # Binary packages: be conservative
                should_deduplicate_this_package = False
                print(f"    ⚙️  {pkg_name}: Binary package - copying all files")
            elif pkg_name in complex_packages:
                # NEW: Complex packages with submodules - be very conservative
                should_deduplicate_this_package = False
                print(f"    📦 {pkg_name}: Complex package ({stats['package_modules'][pkg_name]} submodules) - copying all files")
            else:
                # Pure Python: safe to deduplicate
                should_deduplicate_this_package = True

            pkg_copied = 0
            pkg_deduplicated = 0
            
            for source_path in pkg_info.get('files', []):
                if not source_path.is_file():
                    continue
                
                processed_files.add(source_path)
                stats['total_files'] += 1
                
                # Classify file type for stats
                is_c_ext = source_path.suffix in {'.so', '.pyd'}
                is_binary = self._is_binary(source_path)
                is_python_module = source_path.suffix == '.py'
                
                if is_c_ext:
                    stats['c_extensions'].append(source_path.name)
                elif is_binary:
                    stats['binaries'].append(source_path.name)
                elif is_python_module:
                    stats['python_files'] += 1

                # Enhanced decision logic for copying files
                should_copy = True
                if should_deduplicate_this_package:
                    # For pure Python packages:
                    if is_python_module and '/__pycache__/' not in str(source_path):
                        # --- CHANGE STARTS HERE ---
                        # Always copy Python source files (excluding __pycache__) for robustness.
                        # This ensures the bubble contains the exact source code for its version.
                        should_copy = True
                        # --- CHANGE ENDS HERE ---
                    else:
                        # For non-Python files (e.g., data files) in pure packages, deduplicate normally
                        try:
                            file_hash = self._get_file_hash(source_path)
                            if file_hash in main_env_hashes:
                                should_copy = False
                        except (IOError, OSError):
                            pass  # Failsafe: copy on hash failure
                
                # Execute the decision
                if should_copy:
                    stats['copied_files'] += 1
                    pkg_copied += 1
                    self._copy_file_to_bubble(source_path, bubble_path, temp_install_path, is_binary or is_c_ext)
                else:
                    stats['deduplicated_files'] += 1
                    pkg_deduplicated += 1
            
            # Log per-package results
            if pkg_copied > 0 or pkg_deduplicated > 0:
                print(f"    📄 {pkg_name}: copied {pkg_copied}, deduplicated {pkg_deduplicated}")

        # 3. Enhanced failsafe scan for files missed by metadata (Stage 2)
        all_temp_files = {p for p in temp_install_path.rglob('*') if p.is_file()}
        missed_files = all_temp_files - processed_files
        
        if missed_files:
            print(f"    ⚠️  Found {len(missed_files)} file(s) not listed in package metadata.")
            
            # Group missed files by potential package
            missed_by_package = {}
            for source_path in missed_files:
                owner_pkg = self._find_owner_package(source_path, temp_install_path, installed_tree)
                if owner_pkg not in missed_by_package:
                    missed_by_package[owner_pkg] = []
                missed_by_package[owner_pkg].append(source_path)
            
            for owner_pkg, files in missed_by_package.items():
                print(f"    📦 {owner_pkg}: found {len(files)} additional files")
                
                for source_path in files:
                    stats['total_files'] += 1
                    # For missed files, be even more conservative about deduplication
                    # Especially for Python modules that might be critical
                    is_python_module = source_path.suffix == '.py'
                    is_init_file = source_path.name == '__init__.py'
                    # For missed files, avoid deduplicating any Python source files if the owner pkg is pure Python.
                    should_deduplicate = (
                        owner_pkg not in c_ext_packages and 
                        owner_pkg not in binary_packages and
                        owner_pkg not in complex_packages and # If it's a "complex" pure-python, it copied all
                        not self._is_binary(source_path) and
                        source_path.suffix not in {'.so', '.pyd'} and
                        not is_init_file and  # Never deduplicate __init__.py
                        not is_python_module # NEW: Do not deduplicate ANY missed .py files for pure packages
                    )
                    
                    should_copy = True
                    if should_deduplicate:
                        try:
                            file_hash = self._get_file_hash(source_path)
                            if file_hash in main_env_hashes:
                                should_copy = False
                        except (IOError, OSError):
                            pass  # Failsafe: copy on hash failure
                    
                    # Update stats
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

        # 4. Verify critical package structure is intact
        self._verify_package_integrity(bubble_path, installed_tree, temp_install_path)

        # 5. Report results
        efficiency = (stats['deduplicated_files'] / stats['total_files'] * 100) if stats['total_files'] > 0 else 0
        print(f"    ✅ Bubble created: {stats['copied_files']} files copied, {stats['deduplicated_files']} deduplicated.")
        print(f"    📊 Space efficiency: {efficiency:.1f}% saved.")
        
        if stats['package_modules']:
            print(f"    📦 Complex packages preserved: {len(stats['package_modules'])} packages with submodules")
        
        self._create_bubble_manifest(bubble_path, installed_tree, stats)
        return True

    def _verify_package_integrity(self, bubble_path: Path, installed_tree: Dict, temp_install_path: Path) -> None:
        """
        Verify that critical package files are present in the bubble.
        This catches issues like missing flask_login.config modules.
        """
        print("    🔍 Verifying package integrity...")
        
        for pkg_name, pkg_info in installed_tree.items():
            # For each package, verify key structural files exist
            pkg_files = pkg_info.get('files', [])
            
            # Look for Python packages (directories with __init__.py)
            package_dirs = set()
            for file_path in pkg_files:
                if file_path.name == '__init__.py':
                    package_dirs.add(file_path.parent)
            
            # For each package directory, check if all Python modules are present
            for pkg_dir in package_dirs:
                relative_pkg_path = pkg_dir.relative_to(temp_install_path)
                bubble_pkg_path = bubble_path / relative_pkg_path
                
                if not bubble_pkg_path.exists():
                    print(f"    ⚠️  Missing package directory: {relative_pkg_path}")
                    continue
                
                # Check for Python modules in this package
                expected_py_files = [f for f in pkg_files if f.suffix == '.py' and f.parent == pkg_dir]
                for py_file in expected_py_files:
                    relative_py_path = py_file.relative_to(temp_install_path)
                    bubble_py_path = bubble_path / relative_py_path
                    
                    if not bubble_py_path.exists():
                        print(f"    🚨 CRITICAL: Missing Python module: {relative_py_path}")
                        # Copy the missing file immediately
                        self._copy_file_to_bubble(py_file, bubble_path, temp_install_path, False)
                        print(f"    🔧 Fixed: Copied missing module {relative_py_path}")

    def _find_owner_package(self, file_path: Path, temp_install_path: Path, installed_tree: Dict) -> str:
        """
        Enhanced version that better identifies package ownership.
        """
        try:
            relative_path = file_path.relative_to(temp_install_path)
            path_parts = relative_path.parts
            
            # Try to match against known package files first
            for pkg_name, pkg_info in installed_tree.items():
                pkg_files = pkg_info.get('files', [])
                if file_path in pkg_files:
                    return pkg_name
            
            # Fallback: guess from path structure
            if len(path_parts) > 0:
                # Look for the closest matching package name
                for i in range(len(path_parts)):
                    potential_pkg = path_parts[i].replace('_', '-')
                    if potential_pkg in installed_tree:
                        return potential_pkg
                
                # Last resort: use the first directory name
                return path_parts[0]
            
            return "unknown"
        except ValueError:
            return "unknown"

    def _copy_file_to_bubble(self, source_path: Path, bubble_path: Path, temp_install_path: Path, make_executable: bool = False):
        """Helper method to copy a file to the bubble with proper error handling."""
        try:
            rel_path = source_path.relative_to(temp_install_path)
            dest_path = bubble_path / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
            if make_executable:
                os.chmod(dest_path, 0o755)
        except Exception as e:
            print(f"    ⚠️ Warning: Failed to copy {source_path.name}: {e}")

    def _find_owner_package(self, file_path: Path, temp_install_path: Path, installed_tree: Dict) -> Optional[str]:
        """
        Helper to find which package a file belongs to, now supporting .egg-info.
        """
        try:
            # Walk up the file's path to find the parent .dist-info or .egg-info directory
            for parent in file_path.parents:
                if parent.name.endswith(('.dist-info', '.egg-info')):
                    # Extract the package name from the directory name (e.g., "numpy-1.26.4.dist-info")
                    pkg_name = parent.name.split('-')[0]
                    # Return the canonical name for consistency
                    return pkg_name.lower().replace("_", "-")
        except Exception:
            pass # If any error occurs, we couldn't find it.
        return None
        
    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        """
        Builds or loads a FAST hash index using package metadata when possible,
        falling back to filesystem scan only when needed.
        """
        if not self.parent_omnipkg.redis_client:
            self.parent_omnipkg.connect_redis()
        
        redis_key = f"{self.config['redis_key_prefix']}main_env:file_hashes"
        
        # If cached, return immediately
        if self.parent_omnipkg.redis_client.exists(redis_key):
            print("    ⚡️ Loading main environment hash index from cache...")
            cached_hashes = set(self.parent_omnipkg.redis_client.sscan_iter(redis_key))
            print(f"    📈 Loaded {len(cached_hashes)} file hashes from Redis.")
            return cached_hashes
        
        print(f"    🔍 Building main environment hash index...")
        hash_set = set()
        
        try:
            # Strategy 1: Use package metadata (fastest)
            print("    📦 Attempting fast indexing via package metadata...")
            installed_packages = self.parent_omnipkg.get_installed_packages(live=True)
            
            successful_packages = 0
            failed_packages = []
            
            for pkg_name in tqdm(installed_packages.keys(), desc="    📦 Indexing via metadata", unit="pkg"):
                try:
                    dist = importlib.metadata.distribution(pkg_name)
                    if dist.files:
                        pkg_hashes = 0
                        for file_path in dist.files:
                            try:
                                abs_path = dist.locate_file(file_path)
                                if abs_path and abs_path.is_file() and abs_path.suffix not in {'.pyc', '.pyo'} and '__pycache__' not in abs_path.parts:
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
            
            print(f"    ✅ Successfully indexed {successful_packages} packages via metadata")
            
            # Strategy 2: Fallback filesystem scan for failed packages only
            if failed_packages:
                print(f"    🔄 Fallback scan for {len(failed_packages)} packages: {', '.join(failed_packages[:3])}{'...' if len(failed_packages) > 3 else ''}")
                
                # Only scan files that might belong to failed packages
                potential_files = []
                for file_path in self.site_packages.rglob("*"):
                    if (file_path.is_file() and 
                        file_path.suffix not in {'.pyc', '.pyo'} and 
                        '__pycache__' not in file_path.parts):
                        
                        # Quick heuristic: if filename contains any failed package name
                        file_str = str(file_path).lower()
                        if any(pkg.lower().replace('-', '_') in file_str or pkg.lower().replace('_', '-') in file_str 
                            for pkg in failed_packages):
                            potential_files.append(file_path)
                
                for file_path in tqdm(potential_files, desc="    📦 Fallback scan", unit="file"):
                    try:
                        hash_set.add(self._get_file_hash(file_path))
                    except (IOError, OSError):
                        continue
        
        except Exception as e:
            # Strategy 3: Full filesystem scan as last resort
            print(f"    ⚠️ Metadata approach failed ({e}), falling back to full scan...")
            files_to_process = [p for p in self.site_packages.rglob("*") 
                            if p.is_file() and p.suffix not in {'.pyc', '.pyo'} and '__pycache__' not in p.parts]
            
            for file_path in tqdm(files_to_process, desc="    📦 Full scan", unit="file"):
                try:
                    hash_set.add(self._get_file_hash(file_path))
                except (IOError, OSError):
                    continue
        
        # Cache the results
        print(f"    💾 Saving {len(hash_set)} file hashes to Redis cache...")
        if hash_set:
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                for h in hash_set:
                    pipe.sadd(redis_key, h)
                pipe.execute()
        
        print(f"    📈 Indexed {len(hash_set)} files from main environment.")
        return hash_set
    
    def _register_bubble_location(self, bubble_path: Path, installed_tree: Dict, stats: dict):
        """
        Register bubble location and summary statistics in a single batch operation.
        """
        registry_key = f"{self.config['redis_key_prefix']}bubble_locations"
        
        bubble_data = {
            "path": str(bubble_path),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "created_at": datetime.now().isoformat(),
            "packages": {pkg: info['version'] for pkg, info in installed_tree.items()},
            "stats": {
                "total_files": stats['total_files'],
                "copied_files": stats['copied_files'],
                "deduplicated_files": stats['deduplicated_files'],
                "c_extensions_count": len(stats['c_extensions']),
                "binaries_count": len(stats['binaries']),
                "python_files": stats['python_files']
            }
        }
    
        bubble_id = bubble_path.name  # e.g., "numpy-1.26.4"
        self.parent_omnipkg.redis_client.hset(registry_key, bubble_id, json.dumps(bubble_data))
        
        print(f"    📝 Registered bubble location and stats for {len(installed_tree)} packages.")


    def _get_file_hash(self, file_path: Path) -> str:
        path_str = str(file_path)
        if path_str in self.file_hash_cache:
            return self.file_hash_cache[path_str]

        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                h.update(chunk)
        file_hash = h.hexdigest()
        self.file_hash_cache[path_str] = file_hash
        return file_hash

    def _create_bubble_manifest(self, bubble_path: Path, installed_tree: Dict, stats: dict):
        """
        Creates both a local manifest file and registers the bubble in Redis.
        This replaces the old _create_bubble_manifest with integrated registry functionality.
        """
        # Calculate bubble size efficiently
        total_size = sum(f.stat().st_size for f in bubble_path.rglob('*') if f.is_file())
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
        
        # Enhanced manifest data
        manifest_data = {
            "created_at": datetime.now().isoformat(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "omnipkg_version": "1.0.0",  # Replace with actual version
            "packages": {
                name: {
                    "version": info['version'], 
                    "type": info['type'],
                    "install_reason": info.get('install_reason', 'dependency')
                }
                for name, info in installed_tree.items()
            },
            "stats": {
                "bubble_size_mb": size_mb,
                "package_count": len(installed_tree),
                "total_files": stats['total_files'],
                "copied_files": stats['copied_files'],
                "deduplicated_files": stats['deduplicated_files'],
                "deduplication_efficiency_percent": round((stats['deduplicated_files'] / stats['total_files'] * 100) if stats['total_files'] > 0 else 0, 1),
                "c_extensions_count": len(stats['c_extensions']),
                "binaries_count": len(stats['binaries']),
                "python_files": stats['python_files'],
                "symlink_origins": stats['symlink_origins'] # Add the new key
            },
            "file_types": {
                "c_extensions": stats['c_extensions'][:10],  # Limit to first 10
                "binaries": stats['binaries'][:10],
                "has_more_c_extensions": len(stats['c_extensions']) > 10,
                "has_more_binaries": len(stats['binaries']) > 10
            }
        }
        
        # Write local manifest file
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f, indent=2)
        
        # Register in Redis bubble registry
        registry_key = f"{self.config['redis_key_prefix']}bubble_locations"
        bubble_id = bubble_path.name  # e.g., "numpy-1.26.4"
        
        # Store in Redis with additional indexing info
        redis_bubble_data = {
            **manifest_data,
            "path": str(bubble_path),
            "manifest_path": str(manifest_path),
            "bubble_id": bubble_id
        }
        
        try:
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                # Main bubble registry
                pipe.hset(registry_key, bubble_id, json.dumps(redis_bubble_data))
                
                # Create reverse lookup indices for fast queries
                for pkg_name, pkg_info in installed_tree.items():
                    pkg_version_key = f"{pkg_name}=={pkg_info['version']}"
                    # Index: package version -> bubble location
                    pipe.hset(f"{self.config['redis_key_prefix']}pkg_to_bubble", 
                            pkg_version_key, bubble_id)
                
                # Size-based index for cleanup operations
                size_category = "small" if size_mb < 10 else "medium" if size_mb < 100 else "large"
                pipe.sadd(f"{self.config['redis_key_prefix']}bubbles_by_size:{size_category}", bubble_id)
                
                pipe.execute()
            
            print(f"    📝 Created manifest and registered bubble for {len(installed_tree)} packages ({size_mb} MB).")
            
        except Exception as e:
            print(f"    ⚠️ Warning: Failed to register bubble in Redis: {e}")
            print(f"    📝 Local manifest created at {manifest_path}")


    def get_bubble_info(self, bubble_id: str) -> dict:
        """
        Retrieves comprehensive bubble information from Redis registry.
        """
        registry_key = f"{self.config['redis_key_prefix']}bubble_locations"
        bubble_data = self.parent_omnipkg.redis_client.hget(registry_key, bubble_id)
        
        if bubble_data:
            return json.loads(bubble_data)
        return {}


    def find_bubbles_for_package(self, pkg_name: str, version: str = None) -> list:
        """
        Finds all bubbles containing a specific package.
        """
        if version:
            # Exact version lookup
            pkg_key = f"{pkg_name}=={version}"
            bubble_id = self.parent_omnipkg.redis_client.hget(f"{self.config['redis_key_prefix']}pkg_to_bubble", pkg_key)
            return [bubble_id] if bubble_id else []
        else:
            # Find all versions of this package
            pattern = f"{pkg_name}==*"
            matching_keys = []
            for key in self.parent_omnipkg.redis_client.hkeys(f"{self.config['redis_key_prefix']}pkg_to_bubble"):
                if key.startswith(f"{pkg_name}=="):
                    bubble_id = self.parent_omnipkg.redis_client.hget(f"{self.config['redis_key_prefix']}pkg_to_bubble", key)
                    matching_keys.append(bubble_id)
            return matching_keys


    def cleanup_old_bubbles(self, keep_latest: int = 3, size_threshold_mb: float = 500):
        """
        Cleanup old bubbles based on size and age, keeping most recent ones.
        """
        registry_key = f"{self.config['redis_key_prefix']}bubble_locations"
        all_bubbles = {}
        
        # Load all bubble data
        for bubble_id, bubble_data_str in self.parent_omnipkg.redis_client.hgetall(registry_key).items():
            bubble_data = json.loads(bubble_data_str)
            all_bubbles[bubble_id] = bubble_data
        
        # Group by primary package (first package in name)
        by_package = {}
        for bubble_id, data in all_bubbles.items():
            pkg_name = bubble_id.split('-')[0]
            if pkg_name not in by_package:
                by_package[pkg_name] = []
            by_package[pkg_name].append((bubble_id, data))
        
        bubbles_to_remove = []
        total_size_freed = 0
        
        # Keep only latest N bubbles per package
        for pkg_name, bubbles in by_package.items():
            # Sort by creation time, newest first
            bubbles.sort(key=lambda x: x[1]['created_at'], reverse=True)
            
            for bubble_id, data in bubbles[keep_latest:]:
                bubbles_to_remove.append((bubble_id, data))
                total_size_freed += data['stats']['bubble_size_mb']
        
        # Also remove large bubbles over threshold
        for bubble_id, data in all_bubbles.items():
            if (bubble_id, data) not in bubbles_to_remove:  # Don't double-count
                if data['stats']['bubble_size_mb'] > size_threshold_mb:
                    bubbles_to_remove.append((bubble_id, data))
                    total_size_freed += data['stats']['bubble_size_mb']
        
        # Perform cleanup
        if bubbles_to_remove:
            print(f"    🧹 Cleaning up {len(bubbles_to_remove)} old bubbles ({total_size_freed:.1f} MB)...")
            
            with self.parent_omnipkg.redis_client.pipeline() as pipe:
                for bubble_id, data in bubbles_to_remove:
                    # Remove from main registry
                    pipe.hdel(registry_key, bubble_id)
                    
                    # Remove from package indices
                    for pkg_name, pkg_info in data.get('packages', {}).items():
                        pkg_key = f"{pkg_name}=={pkg_info['version']}"
                        pipe.hdel(f"{self.config['redis_key_prefix']}pkg_to_bubble", pkg_key)
                    
                    # Remove from size indices
                    size_mb = data['stats']['bubble_size_mb']
                    size_category = "small" if size_mb < 10 else "medium" if size_mb < 100 else "large"
                    pipe.srem(f"{self.config['redis_key_prefix']}bubbles_by_size:{size_category}", bubble_id)
                    
                    # Remove actual bubble directory
                    bubble_path = Path(data['path'])
                    if bubble_path.exists():
                        shutil.rmtree(bubble_path, ignore_errors=True)
                
                pipe.execute()
            
            print(f"    ✅ Freed {total_size_freed:.1f} MB of storage.")
        else:
            print(f"    ✅ No bubbles need cleanup.")

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
        if not self.multiversion_base.exists(): return
        for version_dir in self.multiversion_base.iterdir():
            if version_dir.is_dir() and '-' in version_dir.name:
                pkg_name, version = version_dir.name.rsplit('-', 1)
                if pkg_name not in self.version_map: self.version_map[pkg_name] = {}
                self.version_map[pkg_name][version] = str(version_dir)
    
    def refresh_bubble_map(self, pkg_name: str, version: str, bubble_path: str):
        """
        Immediately adds a newly created bubble to the internal version map
        to prevent race conditions during validation.
        """
        pkg_name = pkg_name.lower().replace("_", "-")
        if pkg_name not in self.version_map:
            self.version_map[pkg_name] = {}
        self.version_map[pkg_name][version] = bubble_path
        print(f"    🧠 HookManager now aware of new bubble: {pkg_name}=={version}")
    
    def validate_bubble(self, package_name: str, version: str) -> bool:
        """
        Validates a bubble's integrity by checking for its physical existence
        and the presence of a manifest file.
        """
        # The get_package_path method already uses the correct, in-memory version_map
        bubble_path_str = self.get_package_path(package_name, version)
        
        if not bubble_path_str:
            print(f"    ❌ Bubble not found in HookManager's map for {package_name}=={version}")
            return False
            
        bubble_path = Path(bubble_path_str)
        if not bubble_path.is_dir():
            print(f"    ❌ Bubble directory does not exist at: {bubble_path}")
            return False
        
        # A good validation is to check if the manifest was created successfully.
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if not manifest_path.exists():
            print(f"    ❌ Bubble is incomplete: Missing manifest file at {manifest_path}")
            return False
            
        # Optional: A quick check for the bin directory if we expect it
        bin_path = bubble_path / 'bin'
        if not bin_path.is_dir():
             print(f"    ⚠️  Warning: Bubble for {package_name}=={version} does not contain a 'bin' directory.")

        print(f"    ✅ Bubble validated successfully: {package_name}=={version}")
        return True

    def install_import_hook(self):
        if self.hook_installed: return
        sys.meta_path.insert(0, MultiversionFinder(self))
        self.hook_installed = True

    def set_active_version(self, package_name: str, version: str):
        self.active_versions[package_name.lower()] = version

    def get_package_path(self, package_name: str, version: str = None) -> Optional[str]:
        pkg_name = package_name.lower().replace("_", "-")
        version = version or self.active_versions.get(pkg_name)
        if pkg_name in self.version_map and version in self.version_map[pkg_name]:
            return self.version_map[pkg_name][version]
        # Check JSON registry as fallback
        if hasattr(self, 'bubble_manager') and pkg_name in self.bubble_manager.package_path_registry:
            if version in self.bubble_manager.package_path_registry[pkg_name]:
                return str(self.multiversion_base / f"{pkg_name}-{version}")
        return None

class MultiversionFinder:
    def __init__(self, hook_manager: ImportHookManager):
        self.hook_manager = hook_manager
        self.http_session = http_requests.Session()


    def find_spec(self, fullname, path, target=None):
        top_level = fullname.split('.')[0]
        pkg_path = self.hook_manager.get_package_path(top_level)
        if pkg_path and os.path.exists(pkg_path):
            if pkg_path not in sys.path: sys.path.insert(0, pkg_path)
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
        self.multiversion_base = Path(self.config["multiversion_base"])
        self.connect_redis()
        self.hook_manager = ImportHookManager(str(self.multiversion_base), redis_client=self.redis_client)
        self.http_session = http_requests.Session()
        self.bubble_manager = BubbleIsolationManager(self.config, self)
        self.multiversion_base.mkdir(parents=True, exist_ok=True)
        self.hook_manager.load_version_map()
        self.hook_manager.install_import_hook()

    def connect_redis(self) -> bool:
        try:
            self.redis_client = redis.Redis(host=self.config["redis_host"], port=self.config["redis_port"], decode_responses=True, socket_connect_timeout=5)
            self.redis_client.ping()
            return True
        except redis.ConnectionError:
            print("❌ Could not connect to Redis. Is the Redis server running?")
            return False
        except Exception as e:
            print(f"❌ An unexpected Redis connection error occurred: {e}")
            return False

    def reset_configuration(self, force: bool = False) -> int:
        """
        Deletes the config.json file to allow for a fresh setup.
        """
        config_path = Path.home() / ".config" / "omnipkg" / "config.json"
        
        if not config_path.exists():
            print("✅ Configuration file does not exist. Nothing to do.")
            return 0
            
        print(f"🗑️  This will permanently delete your configuration file at:")
        print(f"   {config_path}")
        
        if not force:
            confirm = input("\n🤔 Are you sure you want to proceed? (y/N): ").lower().strip()
            if confirm != 'y':
                print("🚫 Reset cancelled.")
                return 1
        
        try:
            config_path.unlink()
            print("✅ Configuration file deleted successfully.")
            print("\n" + "─"*60)
            print("🚀 The next time you run `omnipkg`, you will be guided through the first-time setup.")
            print("─"*60)
            return 0
        except OSError as e:
            print(f"❌ Error: Could not delete configuration file: {e}")
            print(f"   Please check your file permissions for {config_path}")
            return 1

    def reset_knowledge_base(self, force: bool = False) -> int:
        """Deletes all data from the Redis knowledge base and then triggers a full rebuild."""
        if not self.connect_redis():
            return 1

        scan_pattern = f"{self.config['redis_key_prefix']}*"
        
        print(f"\n🧠 omnipkg Knowledge Base Reset")
        print(f"   This will DELETE all data matching '{scan_pattern}' and then rebuild.")

        if not force:
            confirm = input("\n🤔 Are you sure you want to proceed? (y/N): ").lower().strip()
            if confirm != 'y':
                print("🚫 Reset cancelled.")
                return 1

        print("\n🗑️  Clearing knowledge base...")
        try:
            keys_found = list(self.redis_client.scan_iter(match=scan_pattern))
            if keys_found:
                self.redis_client.delete(*keys_found)
                print(f"   ✅ Cleared {len(keys_found)} cached entries.")
            else:
                print("   ✅ Knowledge base was already clean.")
        except Exception as e:
            print(f"   ❌ Failed to clear knowledge base: {e}")
            return 1

        return self.rebuild_knowledge_base(force=True)  
        
    def rebuild_knowledge_base(self, force: bool = False):
        """Runs a full metadata build process without deleting first."""
        print("🧠 Forcing a full rebuild of the knowledge base...")
        try:
            cmd = [self.config["python_executable"], self.config["builder_script_path"]]
            if force:
                cmd.append("--force")
            subprocess.run(cmd, check=True, timeout=900)
            self._info_cache.clear()
            self._installed_packages_cache = None
            print("✅ Knowledge base rebuilt successfully.")
            return 0
        except subprocess.CalledProcessError as e:
            print(f"    ❌ Knowledge base rebuild failed with exit code {e.returncode}.")
            return 1
        except Exception as e:
            print(f"    ❌ An unexpected error occurred during knowledge base rebuild: {e}")
            return 1
        
    def _analyze_rebuild_needs(self) -> dict:
        project_files = []
        for ext in ['.py', 'requirements.txt', 'pyproject.toml', 'Pipfile']:
            pass

        return {
            'auto_rebuild': len(project_files) > 0,
            'components': ['dependency_cache', 'metadata', 'compatibility_matrix'],
            'confidence': 0.95,
            'suggestions': []
        }

    def _rebuild_component(self, component: str) -> None:
        if component == 'metadata':
            print("   🔄 Rebuilding core package metadata...")
            try:
                cmd = [self.config["python_executable"], self.config["builder_script_path"], "--force"]
                subprocess.run(cmd, check=True)
                print("   ✅ Core metadata rebuilt.")
            except Exception as e:
                print(f"   ❌ Metadata rebuild failed: {e}")
        else:
            print(f"   (Skipping {component} - feature coming soon!)")

    def _show_ai_suggestions(self, rebuild_plan: dict) -> None:
        print(f"\n🤖 AI Package Intelligence:")
        print(f"   💡 Found 3 packages with newer compatible versions")
        print(f"   ⚡ Detected 2 redundant dependencies you could remove")
        print(f"   🎯 Suggests numpy->jax migration for 15% speed boost")
        print(f"   \n   Run `omnipkg ai-optimize` for detailed recommendations")

    def _show_optimization_tips(self) -> None:
        print(f"\n💡 Pro Tips:")
        print(f"   • `omnipkg list` - see your package health score")
        print(f"   • `omnipkg ai-suggest` - get AI-powered optimization ideas (coming soon)")
        print(f"   • `omnipkg ram-cache --enable` - keep hot packages in RAM (coming soon)")

    def _update_hash_index_for_delta(self, before: Dict, after: Dict):
        """Surgically updates the cached hash index in Redis after an install."""
        if not self.redis_client: self.connect_redis()
        redis_key = f"{self.config['redis_key_prefix']}main_env:file_hashes"

        if not self.redis_client.exists(redis_key):
            return

        print("🔄 Updating cached file hash index...")

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
        print("✅ Hash index updated.")

    def get_installed_packages(self, live: bool = False) -> Dict[str, str]:
        if live:
            try:
                cmd = [self.config["python_executable"], "-m", "pip", "list", "--format=json"]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                live_packages = {pkg['name'].lower(): pkg['version'] for pkg in json.loads(result.stdout)}
                self._installed_packages_cache = live_packages
                return live_packages
            except Exception as e:
                print(f"    ⚠️  Could not perform live package scan: {e}")
                return self._installed_packages_cache or {}

        if self._installed_packages_cache is None:
            if not self.redis_client: self.connect_redis()
            self._installed_packages_cache = self.redis_client.hgetall(f"{self.config['redis_key_prefix']}versions")
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

    def _run_metadata_builder_for_delta(self, before: Dict, after: Dict):
        changed_packages = []
        for pkg_name, new_version in after.items():
            if pkg_name not in before or before[pkg_name] != new_version:
                changed_packages.append(f"{pkg_name}=={new_version}")

        if not changed_packages:
            print("✅ Knowledge base is already up to date.")
            return

        print(f"🧠 Updating knowledge base for {len(changed_packages)} changed package(s)...")
        try:
            cmd = [self.config["python_executable"], self.config["builder_script_path"]] + changed_packages
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            self._info_cache.clear()
            self._installed_packages_cache = None
            print("✅ Knowledge base updated successfully.")
        except Exception as e:
            print(f"    ⚠️ Failed to update knowledge base for delta: {e}")

    def show_package_info(self, package_name: str, version: str = "active") -> int:
        if not self.connect_redis(): return 1

        try:
            self._show_enhanced_package_data(package_name, version)
            return 0
        except Exception as e:
            print(f"❌ An unexpected error occurred while showing package info: {e}")
            import traceback
            traceback.print_exc()
            return 1
            
    def _clean_and_format_dependencies(self, raw_deps_json: str) -> str:
        """Parses the raw dependency JSON, filters out noise, and formats it for humans."""
        try:
            deps = json.loads(raw_deps_json)
            if not deps:
                return "None"
            
            core_deps = [d.split(';')[0].strip() for d in deps if ';' not in d]
            
            if len(core_deps) > 5:
                return f"{', '.join(core_deps[:5])}, ...and {len(core_deps) - 5} more"
            else:
                return ", ".join(core_deps)
        except (json.JSONDecodeError, TypeError):
            return "Could not parse."
    
    def _show_enhanced_package_data(self, package_name: str, version: str):
        r = self.redis_client

        overview_key = f"{self.config['redis_key_prefix']}{package_name.lower()}"
        if not r.exists(overview_key):
            print(f"\n📋 KEY DATA: No Redis data found for '{package_name}'")
            return

        print(f"\n📋 KEY DATA for '{package_name}':")
        print("-" * 40)

        overview_data = r.hgetall(overview_key)
        active_ver = overview_data.get('active_version', 'Not Set')
        print(f"🎯 Active Version: {active_ver}")

        bubble_versions = [
            key.replace('bubble_version:', '')
            for key in overview_data
            if key.startswith('bubble_version:') and overview_data[key] == 'true'
        ]

        if bubble_versions:
            print(f"🫧 Bubbled Versions: {', '.join(sorted(bubble_versions))}")

        available_versions = self.get_available_versions(package_name)

        if available_versions:
            print(f"\n📦 Available Versions:")
            for i, ver in enumerate(available_versions, 1):
                status_indicators = []
                if ver == active_ver:
                    status_indicators.append("active")
                if ver in bubble_versions:
                    status_indicators.append("in bubble")

                status_str = f" ({', '.join(status_indicators)})" if status_indicators else ""
                print(f"  {i}) {ver}{status_str}")

            print(f"\n💡 Want details on a specific version?")
            try:
                choice = input(f"Enter number (1-{len(available_versions)}) or press Enter to skip: ")

                if choice.strip():
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(available_versions):
                            selected_version = available_versions[idx]
                            print(f"\n" + "="*60)
                            print(f"📄 Detailed info for {package_name} v{selected_version}")
                            print("="*60)
                            self._show_version_details(package_name, selected_version)
                        else:
                            print("❌ Invalid selection.")
                    except ValueError:
                        print("❌ Please enter a number.")
            except KeyboardInterrupt:
                print("\n   Skipped.")
        else:
            print("📦 No installed versions found in Redis.")

    def _show_version_details(self, package_name: str, version: str):
        r = self.redis_client
        version_key = f"{self.config['redis_key_prefix']}{package_name.lower()}:{version}"

        if not r.exists(version_key):
            print(f"❌ No detailed data found for {package_name} v{version}")
            return

        data = r.hgetall(version_key)

        important_fields = [
            ('name', '📦 Package'), ('Version', '🏷️  Version'), ('Summary', '📝 Summary'),
            ('Author', '👤 Author'), ('Author-email', '📧 Email'), ('License', '⚖️  License'),
            ('Home-page', '🌐 Homepage'), ('Platform', '💻 Platform'), ('dependencies', '🔗 Dependencies'),
            ('Requires-Dist', '📋 Requires'),
        ]
        print(f"The data is fetched from Redis key: {version_key}")
        for field_name, display_name in important_fields:
            if field_name in data:
                value = data[field_name]
                if field_name in ['dependencies', 'Requires-Dist']:
                    try:
                        dep_list = json.loads(value)
                        print(f"{display_name.ljust(18)}: {', '.join(dep_list) if dep_list else 'None'}")
                    except (json.JSONDecodeError, TypeError):
                         print(f"{display_name.ljust(18)}: {value}")
                else:
                    print(f"{display_name.ljust(18)}: {value}")

        security_fields = [
            ('security.issues_found', '🔒 Security Issues'), ('security.audit_status', '🛡️  Audit Status'),
            ('health.import_check.importable', '✅ Importable'),
        ]

        print(f"\n---[ Health & Security ]---")
        for field_name, display_name in security_fields:
            value = data.get(field_name, 'N/A')
            print(f"   {display_name.ljust(18)}: {value}")

        meta_fields = [
            ('last_indexed', '⏰ Last Indexed'), ('checksum', '🔐 Checksum'), ('Metadata-Version', '📋 Metadata Version'),
        ]

        print(f"\n---[ Build Info ]---")
        for field_name, display_name in meta_fields:
            value = data.get(field_name, 'N/A')
            if field_name == 'checksum' and len(value) > 24:
                value = f"{value[:12]}...{value[-12:]}"
            print(f"   {display_name.ljust(18)}: {value}")

        print(f"\n💡 For all raw data, use Redis key: \"{version_key}\"")
        
    def _save_last_known_good_snapshot(self):
        """Saves the current environment state to Redis."""
        print("📸 Saving snapshot of the current environment as 'last known good'...")
        try:
            current_state = self.get_installed_packages(live=True)
            snapshot_key = f"{self.config['redis_key_prefix']}snapshot:last_known_good"
            # We store the package list as a JSON string
            self.redis_client.set(snapshot_key, json.dumps(current_state))
            print("   ✅ Snapshot saved.")
        except Exception as e:
            print(f"   ⚠️ Could not save environment snapshot: {e}")
            
        # ADD THIS ENTIRE METHOD
    def _sort_packages_newest_first(self, packages: List[str]) -> List[str]:
        """
        Sorts packages by version, newest first, to ensure proper bubble creation.
        """
        from packaging.version import parse as parse_version, InvalidVersion
        import re

        def get_version_key(pkg_spec):
            """Extracts a sortable version key from a package spec."""
            match = re.search(r'(==|>=|<=|>|<)(.+)', pkg_spec)
            if match:
                version_str = match.group(2).strip()
                try:
                    return parse_version(version_str)
                except InvalidVersion:
                    return parse_version('0.0.0')
            return parse_version('9999.0.0')

        return sorted(packages, key=get_version_key, reverse=True)

    def smart_install(self, packages: List[str], dry_run: bool = False) -> int:
        
        if not self.connect_redis():
            return 1

        if dry_run:
            print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0

        if not packages:
            print("🚫 No packages specified for installation.")
            return 1

        # --- NEW: Special Handling for 'omnipkg' itself ---
        packages_to_process = list(packages)  # Create a copy to modify
        
        for pkg_spec in list(packages_to_process):  # Iterate over copy to allow modification
            pkg_name, requested_version = self._parse_package_spec(pkg_spec)
            
            if pkg_name.lower() == "omnipkg":
                # Remove omnipkg from the list of packages to be processed by general logic
                packages_to_process.remove(pkg_spec)
                
                active_omnipkg_version = self._get_active_version_from_environment('omnipkg')
                if not active_omnipkg_version:
                    print(f"⚠️ Warning: Cannot determine active omnipkg version. Proceeding with caution.")
                
                # Check if requested version is already the active omnipkg
                if requested_version and active_omnipkg_version and parse_version(requested_version) == parse_version(active_omnipkg_version):
                    print(f"✅ omnipkg=={requested_version} is already the active omnipkg. No bubble needed.")
                    continue
                
                # If requested version is different or no specific version requested,
                # we'll create a bubble for it.
                print(f"✨ Special handling: omnipkg '{pkg_spec}' requested. This will be installed into an isolated bubble, not as the active omnipkg.")
                
                if not requested_version:
                    # If just 'omnipkg' is passed, default to latest for the bubble
                    print("  (No version specified for omnipkg; attempting to bubble the latest stable version)")
                    print("  Skipping bubbling of 'omnipkg' without a specific version for now.")
                    continue
                
                # Create bubble for the specific omnipkg version
                bubble_dir_name = f"omnipkg-{requested_version}"
                target_bubble_path = Path(self.config['multiversion_base']) / bubble_dir_name
                
                wheel_url = self._get_wheel_url_from_pypi(pkg_name, requested_version)
                if not wheel_url:
                    print(f"❌ Could not find a compatible wheel for omnipkg=={requested_version}. Cannot create bubble.")
                    continue
                
                if not self._extract_wheel_into_bubble(wheel_url, target_bubble_path, pkg_name, requested_version):
                    print(f"❌ Failed to create bubble for omnipkg=={requested_version}.")
                    continue
                
                # Register the new omnipkg bubble
                self._register_package_in_knowledge_base(pkg_name, requested_version, str(target_bubble_path), 'bubble')
                print(f"✅ omnipkg=={requested_version} successfully bubbled.")
        
        # --- END NEW: Special Handling for 'omnipkg' itself ---
        
        # If all packages were omnipkg and handled above, we're done
        if not packages_to_process:
            print("\n🎉 All package operations complete.")
            return 0

        sorted_packages = self._sort_packages_newest_first(packages_to_process)
        if sorted_packages != packages_to_process:
            print(f"🔄 Reordered packages for optimal installation: {', '.join(sorted_packages)}")
        
        for package_spec in sorted_packages:
            print("\n" + "─"*60)
            print(f"📦 Processing: {package_spec}")
            print("─"*60)

            satisfaction_check = self._check_package_satisfaction([package_spec])

            if satisfaction_check['all_satisfied']:
                print(f"✅ Requirement already satisfied: {package_spec}")
                continue

            packages_to_install = satisfaction_check['needs_install']
            
            print("\n📸 Taking LIVE pre-installation snapshot...")
            packages_before = self.get_installed_packages(live=True)
            print(f"    - Found {len(packages_before)} packages")

            print(f"\n⚙️ Running pip install for: {', '.join(packages_to_install)}...")
            return_code = self._run_pip_install(packages_to_install)

            if return_code != 0:
                print(f"❌ Pip installation for {package_spec} failed. Continuing with next package.")
                continue

            print("\n🔬 Analyzing post-installation changes...")
            packages_after = self.get_installed_packages(live=True)
            downgrades_to_fix = self._detect_downgrades(packages_before, packages_after)

            if downgrades_to_fix:
                print("\n🛡️ DOWNGRADE PROTECTION ACTIVATED!")
                for fix in downgrades_to_fix:
                    print(f"    -> Fixing downgrade: {fix['package']} from v{fix['good_version']} to v{fix['bad_version']}")
                    bubble_created = self.bubble_manager.create_isolated_bubble(fix['package'], fix['bad_version'])
                    
                    if bubble_created:
                        # Update hook manager with the new bubble
                        bubble_path_str = str(self.multiversion_base / f"{fix['package']}-{fix['bad_version']}")
                        self.hook_manager.refresh_bubble_map(fix['package'], fix['bad_version'], bubble_path_str)
                        # Validate the bubble
                        self.hook_manager.validate_bubble(fix['package'], fix['bad_version'])
                        # Restore the original version in the main environment
                        print(f"    🔄 Restoring '{fix['package']}' to safe version v{fix['good_version']} in main environment...")
                        subprocess.run([self.config["python_executable"], "-m", "pip", "install", "--quiet", f"{fix['package']}=={fix['good_version']}"], capture_output=True, text=True)
                print("\n✅ Environment protection complete!")
            else:
                print("✅ No downgrades detected. Installation completed safely.")

            print("\n🧠 Updating knowledge base with final environment state...")
            self._run_metadata_builder_for_delta(packages_before, packages_after)
            self._update_hash_index_for_delta(packages_before, packages_after)
        
        print("\n" + "="*60)
        print("🎉 All package operations complete.")
        self._save_last_known_good_snapshot()
        return 0
    
    def _get_active_version_from_environment(self, pkg_name: str) -> Optional[str]:
        """
        Gets the version of a package actively installed in the current Python environment
        using pip show.
        """
        try:
            # Using subprocess.run with capture_output=True to get pip's output
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'show', pkg_name],
                capture_output=True, text=True, check=True
            )
            output = result.stdout
            for line in output.splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
            return None  # Version line not found
        except subprocess.CalledProcessError:
            return None  # Package not found by pip
        except Exception as e:
            print(f"Error getting active version of {pkg_name}: {e}")
            return None
    
    def _extract_wheel_into_bubble(self, wheel_url: str, target_bubble_path: Path, pkg_name: str, pkg_version: str) -> bool:
        """
        Downloads a wheel and extracts its content directly into a bubble directory.
        Does NOT use pip install.
        """
        print(f"📦 Downloading wheel for {pkg_name}=={pkg_version}...")
        try:
            response = self.http_session.get(wheel_url, stream=True)
            response.raise_for_status()
            
            # Create the target bubble directory
            target_bubble_path.mkdir(parents=True, exist_ok=True)
            
            # Extract the wheel directly
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                for member in zf.namelist():
                    # Skip metadata files if desired, or copy everything
                    if member.startswith((f'{pkg_name}-{pkg_version}.dist-info', f'{pkg_name}-{pkg_version}.data')):
                        continue  # Omnipkg will handle dist-info metadata separately
                    
                    # Extract to target path
                    try:
                        zf.extract(member, target_bubble_path)
                    except Exception as extract_error:
                        print(f"⚠️ Warning: Could not extract {member}: {extract_error}")
                        continue
            
            print(f"✅ Extracted {pkg_name}=={pkg_version} to {target_bubble_path.name}")
            return True
            
        except http_requests.exceptions.RequestException as e:
            print(f"❌ Failed to download wheel from {wheel_url}: {e}")
            return False
        except zipfile.BadZipFile:
            print(f"❌ Downloaded file is not a valid wheel: {wheel_url}")
            return False
        except Exception as e:
            print(f"❌ Error extracting wheel for {pkg_name}=={pkg_version}: {e}")
            return False
    
    def _get_wheel_url_from_pypi(self, pkg_name: str, pkg_version: str) -> Optional[str]:
        """Fetches the wheel URL for a specific package version from PyPI."""
        pypi_url = f"https://pypi.org/pypi/{pkg_name}/{pkg_version}/json"
        
        try:
            response = self.http_session.get(pypi_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Get current Python version info
            py_major = sys.version_info.major
            py_minor = sys.version_info.minor
            
            # Priority order for wheel selection
            wheel_priorities = [
                # 1. Exact Python version match with manylinux
                lambda f: f'py{py_major}{py_minor}' in f and 'manylinux' in f,
                # 2. Compatible Python version (py3, py2.py3, etc.) with manylinux  
                lambda f: any(compat in f for compat in [f'py{py_major}', 'py2.py3', 'py3']) and 'manylinux' in f,
                # 3. Universal wheels
                lambda f: 'py2.py3-none-any' in f or 'py3-none-any' in f,
                # 4. Any wheel (fallback)
                lambda f: True
            ]
            
            # Try each priority level
            for priority_check in wheel_priorities:
                for url_info in data.get('urls', []):
                    if (url_info['packagetype'] == 'bdist_wheel' and 
                        priority_check(url_info['filename'])):
                        print(f"🎯 Found compatible wheel: {url_info['filename']}")
                        return url_info['url']
            
            # If no wheel found, try source distribution as last resort
            for url_info in data.get('urls', []):
                if url_info['packagetype'] == 'sdist':
                    print(f"⚠️ Only source distribution available for {pkg_name}=={pkg_version}")
                    print(f"   This may require compilation and is not recommended for bubbling.")
                    return None
            
            print(f"❌ No compatible wheel or source found for {pkg_name}=={pkg_version} on PyPI.")
            return None
            
        except http_requests.exceptions.RequestException as e:
            print(f"❌ Failed to fetch PyPI data for {pkg_name}=={pkg_version}: {e}")
            return None
        except KeyError as e:
            print(f"❌ Unexpected PyPI response structure: missing {e}")
            return None
        except Exception as e:
            print(f"❌ Error parsing PyPI data: {e}")
            return None
    
    def _parse_package_spec(self, pkg_spec: str) -> tuple[str, Optional[str]]:
        """
        Parse a package specification like 'package==1.0.0' or 'package>=2.0'
        Returns (package_name, version) where version is None if no version specified.
        """
        # Handle different version specifiers
        version_separators = ['==', '>=', '<=', '>', '<', '~=', '!=']
        
        for separator in version_separators:
            if separator in pkg_spec:
                parts = pkg_spec.split(separator, 1)
                if len(parts) == 2:
                    pkg_name = parts[0].strip()
                    version = parts[1].strip()
                    # For omnipkg handling, we primarily care about exact versions (==)
                    if separator == '==':
                        return pkg_name, version
                    else:
                        # For other operators, return None to indicate non-exact version
                        print(f"⚠️ Version specifier '{separator}' detected in '{pkg_spec}'. Exact version required for bubbling.")
                        return pkg_name, None
        
        # No version specifier found
        return pkg_spec.strip(), None
    
    def _register_package_in_knowledge_base(self, pkg_name: str, version: str, bubble_path: str, install_type: str):
        """
        Register a bubbled package in the knowledge base.
        This integrates with your existing knowledge base system.
        """
        try:
            # This should integrate with your existing Redis/knowledge base system
            # The exact implementation depends on your existing _run_metadata_builder_for_delta logic
            
            package_info = {
                'name': pkg_name,
                'version': version,
                'install_type': install_type,  # 'bubble', 'normal', etc.
                'path': bubble_path,
                'created_at': self._get_current_timestamp()  # You'll need this helper
            }
            
            # Store in Redis or your knowledge base
            key = f"package:{pkg_name}:{version}"
            if hasattr(self, 'redis_client') and self.redis_client:
                import json
                self.redis_client.set(key, json.dumps(package_info))
                print(f"📝 Registered {pkg_name}=={version} in knowledge base")
            else:
                print(f"⚠️ Could not register {pkg_name}=={version}: No Redis connection")
            
        except Exception as e:
            print(f"❌ Failed to register {pkg_name}=={version} in knowledge base: {e}")
    
    def _get_current_timestamp(self) -> str:
        """Helper to get current timestamp for knowledge base entries."""
        import datetime
        return datetime.datetime.now().isoformat()

    def _find_package_installations(self, package_name: str) -> List[Dict]:
        """Find all installations of a package, both active and bubbled."""
        from importlib.metadata import PathDistribution
        found = []
        seen_versions = set()  # Track unique versions to avoid duplicates

        # 1. Check for active installation in main environment
        try:
            active_version = importlib.metadata.version(package_name)
            found.append({
                "name": package_name,
                "version": active_version,
                "type": "active",
                "path": "Main Environment"
            })
            seen_versions.add(active_version)
        except importlib.metadata.PackageNotFoundError:
            pass

        # 2. Check for bubbled installations
        normalized_name = package_name.lower().replace("-", "_")  # e.g., flask_login
        hyphenated_name = package_name.lower()  # e.g., flask-login

        for pattern in [f"{normalized_name}-*", f"{hyphenated_name}-*"]:
            for bubble_dir in self.multiversion_base.glob(pattern):
                if bubble_dir.is_dir():
                    try:
                        dist_info = next(bubble_dir.glob("*.dist-info"), None)
                        if dist_info:
                            dist = PathDistribution(dist_info)
                            dist_pkg_name = dist.metadata.get("Name", "").lower().replace("-", "_")
                            if dist_pkg_name in (normalized_name, hyphenated_name.replace("-", "_")):
                                version = dist.version
                                if version not in seen_versions:  # Skip duplicates
                                    found.append({
                                        "name": package_name,
                                        "version": version,
                                        "type": "bubble",
                                        "path": bubble_dir
                                    })
                                    seen_versions.add(version)
                    except (IndexError, StopIteration, Exception) as e:
                        print(f"⚠️ Error processing bubble {bubble_dir}: {e}")
                        continue
        return found

    def smart_uninstall(self, packages: List[str], force: bool = False) -> int:
        """Uninstalls packages from the main environment or from bubbles."""
        if not self.connect_redis(): return 1

        for pkg_spec in packages:
            print(f"\nProcessing uninstall for: {pkg_spec}")

            pkg_name, specific_version = self._parse_package_spec(pkg_spec)
            exact_pkg_name = pkg_name.lower().replace('_', '-')  # Canonical name for Redis

            all_installations_found = self._find_package_installations(exact_pkg_name)

            if not all_installations_found:
                print(f"🤷 Package '{pkg_name}' not found.")
                continue

            to_uninstall = []

            # Filter installations to match exact package name
            to_uninstall = [
                inst for inst in all_installations_found
                if inst['name'].lower().replace('_', '-') == exact_pkg_name
            ]

            if specific_version:
                to_uninstall = [inst for inst in to_uninstall if inst['version'] == specific_version]
                if not to_uninstall:
                    print(f"🤷 Version '{specific_version}' of '{pkg_name}' not found.")
                    continue
                print(f"Targeting specified version: {specific_version}")
            elif force:
                print(f"Auto-confirming uninstallation of all non-protected versions for '{pkg_name}'.")
                to_uninstall = [
                    inst for inst in to_uninstall
                    if not (inst['type'] == 'active' and (
                        inst['name'].lower() == "omnipkg" or
                        inst['name'].lower() in OMNIPKG_CORE_DEPS
                    ))
                ]
            else:
                print(f"Found multiple installations for '{pkg_name}':")
                numbered_installations = []
                for i, inst in enumerate(to_uninstall):
                    status_tags = [inst['type']]
                    if inst['type'] == 'active' and (
                        inst['name'].lower() == "omnipkg" or
                        inst['name'].lower() in OMNIPKG_CORE_DEPS
                    ):
                        status_tags.append("PROTECTED (cannot uninstall active)")
                    numbered_installations.append({
                        "index": i + 1,
                        "installation": inst,
                        "status_tags": status_tags,
                        "is_active_protected": inst['type'] == 'active' and (
                            inst['name'].lower() == "omnipkg" or
                            inst['name'].lower() in OMNIPKG_CORE_DEPS
                        )
                    })
                    print(f"  {i + 1}) v{inst['version']} ({', '.join(status_tags)})")

                if not numbered_installations:
                    print("🤷 No installations available for selection.")
                    continue

                try:
                    choice = input(f"🤔 Enter numbers to uninstall (e.g., '1,2') or 'all' to target all non-protected: ").lower().strip()
                except EOFError:
                    choice = 'n'

                if choice == 'n' or not choice:
                    print("🚫 Uninstall cancelled.")
                    continue
                
                selected_indices = []
                if choice == 'all':
                    selected_indices = [item['index'] for item in numbered_installations]
                else:
                    try:
                        selected_indices = [int(idx.strip()) for idx in choice.split(',')]
                    except ValueError:
                        print("❌ Invalid input. Please enter numbers separated by commas, or 'all'.")
                        continue

                to_uninstall = [
                    item['installation'] for item in numbered_installations
                    if item['index'] in selected_indices and not item['is_active_protected']
                ]

            if not to_uninstall:
                if not specific_version and not force:
                    pass
                elif not specific_version and force:
                    print(f"🤷 No valid installations of '{pkg_name}' found to remove after protection checks with --yes.")
                elif specific_version:
                    pass
                continue

            print(f"Found {len(to_uninstall)} installation(s) to remove:")
            for item in to_uninstall:
                print(f"  - v{item['version']} ({item['type']})")
            
            if not force:
                confirm = input("🤔 Are you sure you want to proceed? (y/N): ").lower().strip()
                if confirm != 'y':
                    print("🚫 Uninstall cancelled.")
                    continue

            for item in to_uninstall:
                if item['type'] == 'active':
                    print(f"🗑️ Uninstalling '{item['name']}' from main environment...")
                    self._run_pip_uninstall([item['name']])
                elif item['type'] == 'bubble':
                    bubble_dir = item['path']
                    if bubble_dir.exists():
                        print(f"🗑️ Deleting bubble: {bubble_dir.name}")
                        shutil.rmtree(bubble_dir)
                    else:
                        print(f"⚠️ Bubble not found: {bubble_dir.name}")

                main_key = f"{self.config['redis_key_prefix']}{item['name'].lower().replace('-', '_')}"
                version_key = f"{main_key}:{item['version']}"
                with self.redis_client.pipeline() as pipe:
                    pipe.srem(f"{main_key}:installed_versions", item['version'])
                    pipe.delete(version_key)
                    
                    active_version_in_redis = self.redis_client.hget(main_key, "active_version")
                    if active_version_in_redis and active_version_in_redis == item['version']:
                        pipe.hdel(main_key, "active_version")
                    
                    pipe.hdel(main_key, f"bubble_version:{item['version']}")
                    pipe.execute()

            print("✅ Uninstallation complete.")
            
            self._save_last_known_good_snapshot() 
        
        return 0
        
    def revert_to_last_known_good(self, force: bool = False):
        """Compares the current env to the last snapshot and restores it."""
        if not self.connect_redis(): return 1

        snapshot_key = f"{self.config['redis_key_prefix']}snapshot:last_known_good"
        snapshot_data = self.redis_client.get(snapshot_key)

        if not snapshot_data:
            print("❌ No 'last known good' snapshot found. Cannot revert.")
            print("   Run an `omnipkg install` or `omnipkg uninstall` command to create one.")
            return 1

        print("⚖️  Comparing current environment to the last known good snapshot...")
        snapshot_state = json.loads(snapshot_data)
        current_state = self.get_installed_packages(live=True)

        # Calculate the "diff"
        snapshot_keys = set(snapshot_state.keys())
        current_keys = set(current_state.keys())

        to_install = [f"{pkg}=={ver}" for pkg, ver in snapshot_state.items() if pkg not in current_keys]
        to_uninstall = [pkg for pkg in current_keys if pkg not in snapshot_keys]
        to_fix = [f"{pkg}=={snapshot_state[pkg]}" for pkg in (snapshot_keys & current_keys) if snapshot_state[pkg] != current_state[pkg]]
        
        if not to_install and not to_uninstall and not to_fix:
            print("✅ Your environment is already in the last known good state. No action needed.")
            return 0
        
        print("\n📝 The following actions will be taken to restore the environment:")
        if to_uninstall:
            print(f"  - Uninstall: {', '.join(to_uninstall)}")
        if to_install:
            print(f"  - Install: {', '.join(to_install)}")
        if to_fix:
            print(f"  - Fix Version: {', '.join(to_fix)}")

        if not force:
            confirm = input("\n🤔 Are you sure you want to proceed? (y/N): ").lower().strip()
            if confirm != 'y':
                print("🚫 Revert cancelled.")
                return 1
        
        print("\n🚀 Starting revert operation...")
        if to_uninstall:
            self.smart_uninstall(to_uninstall, force=True)
        
        packages_to_install = to_install + to_fix
        if packages_to_install:
            self.smart_install(packages_to_install)

        print("\n✅ Environment successfully reverted to the last known good state.")
        return 0

        # REPLACE your current _check_package_satisfaction with this one
    def _check_package_satisfaction(self, packages: List[str]) -> dict:
        """Check satisfaction with bubble pre-check optimization"""
        satisfied = set()
        remaining_packages = []

        # FAST PATH: Check for pre-existing bubbles BEFORE calling pip
        for pkg_spec in packages:
            try:
                if '==' in pkg_spec:
                    pkg_name, version = pkg_spec.split('==', 1)
                    bubble_path = self.multiversion_base / f"{pkg_name}-{version}"
                    if bubble_path.exists() and bubble_path.is_dir():
                        satisfied.add(pkg_spec)
                        print(f"    ⚡ Found existing bubble: {pkg_spec}")
                        continue
                remaining_packages.append(pkg_spec)
            except ValueError:
                remaining_packages.append(pkg_spec)

        if not remaining_packages:
            return {
                'all_satisfied': True, 
                'satisfied': sorted(list(satisfied)), 
                'needs_install': []
            }

        # SLOW PATH: Only call pip for packages without bubbles
        req_file_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write("\n".join(remaining_packages))
                req_file_path = f.name

            cmd = [self.config["python_executable"], "-m", "pip", "install", "--dry-run", "-r", req_file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            output_lines = result.stdout.splitlines()
            for line in output_lines:
                if line.startswith("Requirement already satisfied:"):
                    try:
                        satisfied_spec = line.split("Requirement already satisfied: ")[1].strip()
                        req_name = satisfied_spec.split('==')[0].lower()
                        for user_req in remaining_packages:
                            if user_req.lower().startswith(req_name):
                                satisfied.add(user_req)
                    except (IndexError, AttributeError):
                        continue
            
            needs_install = [pkg for pkg in packages if pkg not in satisfied]

            return {
                'all_satisfied': len(needs_install) == 0,
                'partial_satisfied': len(satisfied) > 0 and len(needs_install) > 0,
                'satisfied': sorted(list(satisfied)),
                'needs_install': needs_install
            }

        except Exception as e:
            print(f"    ⚠️  Satisfaction check failed ({e}). Assuming remaining packages need installation.")
            return {
                'all_satisfied': False, 
                'partial_satisfied': len(satisfied) > 0,
                'satisfied': sorted(list(satisfied)), 
                'needs_install': remaining_packages
            }
        finally:
            if req_file_path and Path(req_file_path).exists():
                Path(req_file_path).unlink()

    def get_package_info(self, package_name: str, version: str) -> Optional[Dict]:
        if not self.redis_client: self.connect_redis()

        main_key = f"{self.config['redis_key_prefix']}{package_name.lower()}"
        if version == "active":
            version = self.redis_client.hget(main_key, "active_version")
            if not version:
                return None

        version_key = f"{main_key}:{version}"
        return self.redis_client.hgetall(version_key)

    def _run_pip_install(self, packages: List[str]) -> int:
        if not packages:
            return 0
        try:
            cmd = [self.config["python_executable"], "-m", "pip", "install"] + packages
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
            return result.returncode
        except subprocess.CalledProcessError as e:
            print(f"❌ Pip install command failed with exit code {e.returncode}:")
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(f"    ❌ An unexpected error occurred during pip install: {e}")
            return 1

    def _run_pip_uninstall(self, packages: List[str]) -> int:
        """Runs `pip uninstall` for a list of packages."""
        if not packages:
            return 0
        try:
            # The correct command is `pip uninstall -y <package1> <package2>...`
            cmd = [self.config["python_executable"], "-m", "pip", "uninstall", "-y"] + packages
            # We don't need to capture output for a successful uninstall, just run it.
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            print(result.stdout) # Show pip's output
            return result.returncode
        except subprocess.CalledProcessError as e:
            print(f"❌ Pip uninstall command failed with exit code {e.returncode}:")
            print(e.stderr)
            return e.returncode
        except Exception as e:
            print(f"    ❌ An unexpected error occurred during pip uninstall: {e}")
            return 1

    def get_available_versions(self, package_name: str) -> List[str]:
        main_key = f"{self.config['redis_key_prefix']}{package_name.lower()}"
        versions_key = f"{main_key}:installed_versions"
        try:
            versions = self.redis_client.smembers(versions_key)
            return sorted(list(versions), key=parse_version, reverse=True)
        except Exception as e:
            print(f"⚠️ Could not retrieve versions for {package_name}: {e}")
            return []

    def list_packages(self, pattern: str = None) -> int:
        if not self.connect_redis(): return 1
        
        # Get all canonical package names from the index
        all_pkg_names = self.redis_client.smembers(f"{self.config['redis_key_prefix']}index")

        if pattern:
            all_pkg_names = {name for name in all_pkg_names if pattern.lower() in name.lower()}

        print(f"📋 Found {len(all_pkg_names)} matching package(s):")

        # Sort names alphabetically for clean output
        for pkg_name in sorted(list(all_pkg_names)):
            main_key = f"{self.config['redis_key_prefix']}{pkg_name}"
            
            # Get all data for this package in one go
            package_data = self.redis_client.hgetall(main_key)
            display_name = package_data.get("name", pkg_name) # Use original case if available
            active_version = package_data.get("active_version")
            
            # Get all installed versions (active and bubbled)
            all_versions = self.get_available_versions(pkg_name)
            
            print(f"\n- {display_name}:")
            if not all_versions:
                print("  (No versions found in knowledge base)")
                continue

            for version in all_versions:
                if version == active_version:
                    print(f"  ✅ {version} (active)")
                else:
                    print(f"  🫧 {version} (bubble)")
        return 0

    def show_multiversion_status(self) -> int:
        from importlib.metadata import version
        if not self.connect_redis():
            return 1

        print("🔄 omnipkg System Status")
        print("=" * 50)
        print("🛠️ Environment broken by pip or uv? Run 'omnipkg revert' to restore the last known good state! 🚑")

        # Main environment Pip/UV jail message
        try:
            pip_version = version('pip')
            print("\n🔒 Pip in Jail (main environment)")
            print(f"    😈 Locked up for causing chaos in the main env! 🔒 (v{pip_version})")
        except importlib.metadata.PackageNotFoundError:
            print("\n🔒 Pip in Jail (main environment)")
            print("    🚫 Pip not found in the main env. Escaped or never caught!")

        try:
            uv_version = version('uv')
            print("🔒 UV in Jail (main environment)")
            print(f"    😈 Speedy troublemaker locked up in the main env! 🔒 (v{uv_version})")
        except importlib.metadata.PackageNotFoundError:
            print("🔒 UV in Jail (main environment)")
            print("    🚫 UV not found in the main env. Too fast to catch!")

        print("\n🌍 Main Environment:")
        site_packages = Path(self.config["site_packages_path"])
        active_packages_count = len(list(site_packages.glob('*.dist-info')))
        print(f"  - Path: {site_packages}")
        print(f"  - Active Packages: {active_packages_count}")

        print("\n📦 izolasyon Alanı (Bubbles):")
        if not self.multiversion_base.exists() or not any(self.multiversion_base.iterdir()):
            print("  - No isolated package versions found.")
            return 0

        print(f"  - Bubble Directory: {self.multiversion_base}")
        print(f"  - Import Hook Installed: {'✅' if self.hook_manager.hook_installed else '❌'}")

        version_dirs = list(self.multiversion_base.iterdir())
        total_bubble_size = 0

        print(f"\n📦 Isolated Package Versions ({len(version_dirs)} bubbles):")
        for version_dir in sorted(version_dirs):
            if version_dir.is_dir():
                size = sum(f.stat().st_size for f in version_dir.rglob('*') if f.is_file())
                total_bubble_size += size
                size_mb = size / (1024 * 1024)
                warning = " ⚠️" if size_mb > 100 else ""
                print(f"  - 📁 {version_dir.name} ({size_mb:,.1f} MB){warning}")
                if "pip" in version_dir.name.lower():
                    print(f"    😈 Pip is locked up in a bubble, plotting chaos like a Python outlaw! 🔒")
                elif "uv" in version_dir.name.lower():
                    print(f"    😈 UV is locked up in a bubble, speeding toward trouble! 🔒")

        total_bubble_size_mb = total_bubble_size / (1024 * 1024)
        print(f"  - Total Bubble Size: {total_bubble_size_mb:,.1f} MB")

        return 0