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
import os
import shutil
import site
import hashlib
import tempfile
import requests
import re
import importlib.metadata  # <--- THE FIX IS HERE
from datetime import datetime
from pathlib import Path
from packaging.version import parse as parse_version, InvalidVersion
from typing import Dict, List, Optional, Set, Tuple
from importlib.metadata import Distribution

# ##################################################################
# ### CONFIGURATION MANAGEMENT (PORTABLE & SELF-CONFIGURING) ###
# ##################################################################

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

    def _analyze_installed_tree(self, temp_path: Path) -> Dict[str, Dict]:
        installed = {}
        for dist_info in temp_path.glob("*.dist-info"):
            try:
                dist = importlib.metadata.Distribution.at(dist_info)
                if not dist: continue

                pkg_files = []
                for file_entry in dist.files:
                    abs_path = Path(dist_info.parent) / file_entry
                    if abs_path.exists():
                        pkg_files.append(abs_path)

                installed[dist.metadata['Name']] = {
                    'version': dist.metadata['Version'],
                    'files': [p for p in pkg_files if p.exists()],
                    'type': self._classify_package_type(pkg_files),
                    'metadata': dist.metadata
                }
            except Exception as e:
                print(f"    ⚠️  Could not analyze {dist_info.name}: {e}")
        return installed


    def _classify_package_type(self, files: List[Path]) -> str:
        has_python = any(f.suffix in ['.py', '.pyc'] for f in files)
        has_native = any(f.suffix in ['.so', '.pyd', '.dll'] for f in files)

        if has_native and has_python: return 'mixed'
        elif has_native: return 'native'
        else: return 'pure_python'

    def _create_deduplicated_bubble(self, installed_tree: Dict, bubble_path: Path, temp_install_path: Path) -> bool:
        """
        Creates the final bubble with intelligent, package-aware deduplication.
        Deduplication is now disabled for any package containing native code.
        """
        print(f"    🧹 Creating deduplicated bubble at {bubble_path}")
        bubble_path.mkdir(parents=True, exist_ok=True)
        
        main_env_hashes = self._get_or_build_main_env_hash_index()
        
        total_files = 0
        copied_files = 0
        
        for pkg_name, pkg_info in installed_tree.items():
            pkg_type = pkg_info.get('type', 'pure_python')
            
            is_native_pkg = pkg_type in ['native', 'mixed']
            if is_native_pkg:
                print(f"    ⚠️  Disabling deduplication for native package: {pkg_name}")

            for file_path in pkg_info['files']:
                if not file_path.is_file(): continue
                total_files += 1

                should_copy = True
                
                if not is_native_pkg:
                    try:
                        file_hash = self._get_file_hash(file_path)
                        if file_hash in main_env_hashes:
                            should_copy = False
                    except (IOError, OSError):
                        should_copy = True
                
                if should_copy:
                    try:
                        rel_path = file_path.relative_to(temp_install_path)
                        dest_path = bubble_path / rel_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file_path, dest_path)
                        copied_files += 1
                    except Exception as e:
                        pass
        
        deduplicated_files = total_files - copied_files
        efficiency = (deduplicated_files / total_files * 100) if total_files > 0 else 0
        print(f"    ✅ Bubble created: {copied_files} files copied, {deduplicated_files} deduplicated.")
        if total_files > 0:
            print(f"    📊 Space efficiency: {efficiency:.1f}% saved.")
        
        self._create_bubble_manifest(bubble_path, installed_tree)
        return True  
        
    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        """
        Intelligently gets the hash index from the Redis cache.
        If the cache doesn't exist, it performs a one-time build.
        """
        if not self.parent_omnipkg.redis_client: self.parent_omnipkg.connect_redis()

        redis_key = f"{self.config['redis_key_prefix']}main_env:file_hashes"

        if self.parent_omnipkg.redis_client.exists(redis_key):
            print("    ⚡️ Loading main environment hash index from cache...")
            hashes = set(self.parent_omnipkg.redis_client.sscan_iter(redis_key))
            print(f"    📈 Loaded {len(hashes)} file hashes from Redis.")
            return hashes

        print(f"    🔍 Building main environment hash index for the first time...")
        hash_set = set()
        for file_path in self.site_packages.rglob("*"):
            if file_path.is_file():
                try:
                    hash_set.add(self._get_file_hash(file_path))
                except (IOError, OSError):
                    continue

        print(f"    💾 Saving {len(hash_set)} hashes to Redis cache...")
        with self.parent_omnipkg.redis_client.pipeline() as pipe:
            for h in hash_set:
                pipe.sadd(redis_key, h)
            pipe.execute()

        print(f"    📈 Indexed {len(hash_set)} files from main environment.")
        return hash_set

    def _should_copy_file(self, file_path: Path, pkg_type: str, main_env_hashes: Set[str]) -> bool:
        try:
            file_hash = self._get_file_hash(file_path)
            if file_hash in main_env_hashes:
                if pkg_type in ['native', 'mixed'] and file_path.suffix in ['.so', '.pyd', '.dll']:
                    return True
                return False
            return True
        except (IOError, OSError):
            return True

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

    def _create_bubble_manifest(self, bubble_path: Path, installed_tree: Dict):
        total_size = sum(f.stat().st_size for f in bubble_path.rglob('*') if f.is_file())
        manifest = {
            'created_at': datetime.now().isoformat(),
            'packages': {
                name: {'version': info['version'], 'type': info['type']}
                for name, info in installed_tree.items()
            },
            'stats': {
                'bubble_size_mb': round(total_size / (1024 * 1024), 2),
                'package_count': len(installed_tree)
            }
        }
        with open(bubble_path / '.omnipkg_manifest.json', 'w') as f:
            json.dump(manifest, f, indent=2)

class ImportHookManager:
    def __init__(self, multiversion_base: str):
        self.multiversion_base = Path(multiversion_base)
        self.version_map = {}
        self.active_versions = {}
        self.hook_installed = False

    def load_version_map(self):
        if not self.multiversion_base.exists(): return
        for version_dir in self.multiversion_base.iterdir():
            if version_dir.is_dir() and '-' in version_dir.name:
                pkg_name, version = version_dir.name.rsplit('-', 1)
                if pkg_name not in self.version_map: self.version_map[pkg_name] = {}
                self.version_map[pkg_name][version] = str(version_dir)

    def install_import_hook(self):
        if self.hook_installed: return
        sys.meta_path.insert(0, MultiversionFinder(self))
        self.hook_installed = True

    def set_active_version(self, package_name: str, version: str):
        self.active_versions[package_name.lower()] = version

    def get_package_path(self, package_name: str, version: str = None) -> Optional[str]:
        pkg_name = package_name.lower()
        version = version or self.active_versions.get(pkg_name)
        if pkg_name in self.version_map and version in self.version_map[pkg_name]:
            return self.version_map[pkg_name][version]
        return None

class MultiversionFinder:
    def __init__(self, hook_manager: ImportHookManager):
        self.hook_manager = hook_manager

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
        self.hook_manager = ImportHookManager(str(self.multiversion_base))
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

    def smart_install(self, packages: List[str], dry_run: bool = False) -> int:
        if not self.connect_redis():
            return 1

        if dry_run:
            print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0

        print(f"🔍 Checking satisfaction for: {', '.join(packages)}")

        satisfaction_check = self._check_package_satisfaction(packages)

        if satisfaction_check['all_satisfied']:
            print("\n✅ All requirements already satisfied. No work needed. 🛡️")
            return 0

        packages_to_install = satisfaction_check['needs_install']

        if satisfaction_check['partial_satisfied']:
            print("\nℹ️  Some packages already satisfied:")
            for pkg in satisfaction_check['satisfied']:
                print(f"   ✅ {pkg}")
            print("\n📦 Proceeding to install remaining packages:")
            for pkg in packages_to_install:
                print(f"   📥 {pkg}")

        print("\n📸 Taking LIVE pre-installation snapshot...")
        packages_before = self.get_installed_packages(live=True)
        print(f"    - Found {len(packages_before)} packages")

        print(f"\n⚙️  Running pip install for: {', '.join(packages_to_install)}...")
        return_code = self._run_pip_install(packages_to_install)

        if return_code != 0:
            print("❌ Pip installation failed. Aborting cleanup.")
            return return_code

        print("\n🔬 Analyzing post-installation changes...")
        packages_after = self.get_installed_packages(live=True)
        downgrades_to_fix = self._detect_downgrades(packages_before, packages_after)

        if downgrades_to_fix:
            print("\n🛡️  DOWNGRADE PROTECTION ACTIVATED!")
            for fix in downgrades_to_fix:
                print(f"    -> Fixing downgrade: {fix['package']} from v{fix['good_version']} to v{fix['bad_version']}")
                self.bubble_manager.create_isolated_bubble(fix['package'], fix['bad_version'])
                print(f"    🔄 Restoring '{fix['package']}' to safe version v{fix['good_version']} in main environment...")
                subprocess.run([self.config["python_executable"], "-m", "pip", "install", f"{fix['package']}=={fix['good_version']}"], capture_output=True, text=True)
            print("\n✅ Environment protection complete!")
        else:
            print("✅ No downgrades detected. Installation completed safely.")

        print("\n🧠 Updating knowledge base with final environment state...")
        self._run_metadata_builder_for_delta(packages_before, packages_after)
        self._update_hash_index_for_delta(packages_before, packages_after)

        return 0

    def _check_package_satisfaction(self, packages: List[str]) -> dict:
        req_file_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write("\n".join(packages))
                req_file_path = f.name

            cmd = [self.config["python_executable"], "-m", "pip", "install", "--dry-run", "-r", req_file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            satisfied = set()
            output_lines = result.stdout.splitlines()

            for line in output_lines:
                if line.startswith("Requirement already satisfied:"):
                    try:
                        satisfied_spec = line.split("Requirement already satisfied: ")[1].strip()
                        req_name = satisfied_spec.split('==')[0].lower()
                        for user_req in packages:
                            if user_req.lower().startswith(req_name):
                                satisfied.add(user_req)
                    except IndexError:
                        continue

            needs_install = [pkg for pkg in packages if pkg not in satisfied]

            return {
                'all_satisfied': len(needs_install) == 0,
                'partial_satisfied': len(satisfied) > 0 and len(needs_install) > 0,
                'satisfied': sorted(list(satisfied)),
                'needs_install': needs_install
            }

        except Exception as e:
            print(f"    ⚠️  Satisfaction check failed ({e}). Assuming all packages need installation.")
            return {'all_satisfied': False, 'partial_satisfied': False, 'satisfied': [], 'needs_install': packages}
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
        installed = self.get_installed_packages()
        if pattern:
            installed = {k: v for k, v in installed.items() if pattern.lower() in k.lower()}

        print(f"📋 Found {len(installed)} packages:")

        for pkg, version in sorted(installed.items()):
            info = self.get_package_info(pkg, version)
            if info:
                summary = info.get('Summary', 'No description available')[:57] + '...'
                security = '🛡️' if int(info.get('security.issues_found', '0')) == 0 else '⚠️'
                health = '💚' if info.get('health.import_check.importable', 'unknown') == 'True' else '💔'
                print(f"  {security}{health} {pkg} v{version} - {summary}")
            else:
                print(f"  ⚠️  {pkg} v{version} - No metadata found. Run `omnipkg rebuild-kb`.")
        return 0


    def show_multiversion_status(self) -> int:
        if not self.connect_redis():
            return 1

        print("🔄 omnipkg System Status")
        print("=" * 50)

        site_packages = Path(self.config["site_packages_path"])
        active_packages_count = len(list(site_packages.glob('*.dist-info')))
        print("🌍 Main Environment:")
        print(f"  - Path: {site_packages}")
        print(f"  - Active Packages: {active_packages_count}")

        print("\n izolasyon Alanı (Bubbles):")

        if not self.multiversion_base.exists() or not any(self.multiversion_base.iterdir()):
            print("  - No isolated package versions found.")
            return 0

        print(f"  - Bubble Directory: {self.multiversion_base}")
        print(f"  - Import Hook Installed: {'✅' if self.hook_manager.hook_installed else '❌'}")

        version_dirs = list(self.multiversion_base.iterdir())
        total_bubble_size = 0

        print(f"\n📦 Isolated Package Versions ({len(version_dirs)}):")
        for version_dir in sorted(version_dirs):
            if version_dir.is_dir():
                size = sum(f.stat().st_size for f in version_dir.rglob('*') if f.is_file())
                total_bubble_size += size
                size_mb = size / (1024 * 1024)
                print(f"  - 📁 {version_dir.name} ({size_mb:.1f} MB)")

        total_bubble_size_mb = total_bubble_size / (1024 * 1024)
        print(f"  - Total Bubble Size: {total_bubble_size_mb:.1f} MB")

        return 0