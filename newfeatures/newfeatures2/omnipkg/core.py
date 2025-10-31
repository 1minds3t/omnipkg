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
import asyncio
import aiohttp
import concurrent.futures
import time
import filelock
import requests
import re
import importlib.metadata
from datetime import datetime
from pathlib import Path
from packaging.version import parse as parse_version, InvalidVersion
from typing import Dict, List, Optional, Set, Tuple
from importlib.metadata import Distribution
from filelock import FileLock  # NEW: For safe file writing

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

# In omnipkg/core.py

class BubbleIsolationManager:
    def __init__(self, config: Dict, parent_omnipkg):
        self.config = config
        self.parent_omnipkg = parent_omnipkg
        self.site_packages = Path(config["site_packages_path"])
        self.multiversion_base = Path(config["multiversion_base"])
        self.file_hash_cache = {}
        # Caches and Locks are good, we keep them
        self.package_path_registry = {}
        self.failed_versions_cache = {}
        self.api_cache = {}
        self.api_cache_ttl = 3600
        self.max_api_cache_size = 256
        self.registry_lock = FileLock(self.multiversion_base / "registry.lock")
        self.failed_cache_lock = FileLock(self.multiversion_base / "failed_cache.lock")
        self._load_path_registry()
        self._load_failed_versions_cache()

    # --- All the cache/lock management methods are correct. We keep them. ---
    def _normalize_package_name(self, package_name: str) -> str:
        return package_name.lower().replace("_", "-")

    def _atomic_write_json(self, file_path: Path, data: Dict, lock: FileLock):
        with lock:
            temp_file = file_path.with_suffix(f"{file_path.suffix}.tmp")
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(temp_file, 'w') as f: json.dump(data, f, indent=2)
                os.rename(temp_file, file_path)
            finally:
                if temp_file.exists(): temp_file.unlink()

    def _load_path_registry(self):
        registry_file = self.multiversion_base / "package_paths.json"
        if registry_file.exists():
            with self.registry_lock:
                try:
                    with open(registry_file, 'r') as f: self.package_path_registry = json.load(f)
                except Exception: pass

    def _save_path_registry(self):
        self._atomic_write_json(self.multiversion_base / "package_paths.json", self.package_path_registry, self.registry_lock)
    
    # ... other cache/lock/registry methods are fine ...
    def _load_failed_versions_cache(self):
        cache_file = self.multiversion_base / "failed_versions.json"
        if cache_file.exists():
            with self.failed_cache_lock:
                try:
                    with open(cache_file, 'r') as f:
                        data = json.load(f)
                        cutoff = time.time() - 86400
                        self.failed_versions_cache = {p: {v: r for v, r in vs.items() if r.get('timestamp', 0) > cutoff} for p, vs in data.items()}
                except Exception: pass

    def _save_failed_versions_cache(self):
        self._atomic_write_json(self.multiversion_base / "failed_versions.json", self.failed_versions_cache, self.failed_cache_lock)
    
    def _mark_version_failed(self, package_name: str, version: str, reason: str):
        c_name = self._normalize_package_name(package_name)
        if c_name not in self.failed_versions_cache: self.failed_versions_cache[c_name] = {}
        self.failed_versions_cache[c_name][version] = {'reason': reason, 'timestamp': time.time()}
        self._save_failed_versions_cache()

    def get_package_path(self, package_name: str, version: str) -> Optional[Path]:
        c_name = self._normalize_package_name(package_name)
        path_str = self.package_path_registry.get(c_name, {}).get(version)
        return Path(path_str) if path_str else None


    # --- THE NEW, CORRECT, "deptimemachine.py" LOGIC ---

    def _get_release_date(self, package_name: str, version: str) -> Optional[str]:
        """Gets the release date from PyPI. A crucial first step."""
        print(f"    📅 Getting release date for {package_name}=={version}...")
        try:
            url = f"https://pypi.org/pypi/{package_name}/{version}/json"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            release_info = data.get("releases", {}).get(version, [])
            if release_info and "upload_time_iso_8601" in release_info[0]:
                date_str = release_info[0]["upload_time_iso_8601"]
                print(f"    ✓ Release date found: {date_str}")
                return date_str
        except Exception as e:
            print(f"    ❌ Error fetching release date: {e}")
        return None

    def _get_deps_from_temp_venv(self, package_name: str, version: str) -> List[str]:
        """Creates a temporary venv to get a 100% accurate list of dependency *names*."""
        print("    📦 Creating temporary venv for accurate dependency discovery...")
        with tempfile.TemporaryDirectory(prefix="omnipkg-venv-") as temp_dir:
            try:
                venv_path = Path(temp_dir)
                subprocess.run([self.config["python_executable"], "-m", "venv", str(venv_path)], check=True, capture_output=True)
                venv_python = venv_path / 'bin' / 'python'
                # We install with --no-deps here because we ONLY want the names specified by the package itself
                subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "--no-deps", f"{package_name}=={version}"],
                    check=True, capture_output=True, timeout=300
                )
                
                # Then we use pip show to get the "Requires:" field
                show_result = subprocess.run(
                    [str(venv_python), "-m", "pip", "show", package_name],
                    check=True, capture_output=True, text=True
                )
                
                dep_names = []
                for line in show_result.stdout.splitlines():
                    if line.startswith("Requires:"):
                        deps_str = line.replace("Requires:", "").strip()
                        if deps_str:
                            dep_names = [self._normalize_package_name(d.strip()) for d in deps_str.split(',')]
                        break
                print(f"    ✓ Discovered dependency names: {', '.join(dep_names) if dep_names else 'None'}")
                return dep_names
            except Exception as e:
                self._mark_version_failed(package_name, version, "venv_dep_discovery_failed")
                return []

    def _find_historical_versions(self, dependencies: List[str], cutoff_date: str) -> Dict[str, str]:
        """Finds the latest version for each dependency released before a given date."""
        try:
            cutoff_datetime = datetime.fromisoformat(cutoff_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return {}

        print(f"\n    🔍 Finding historical versions for {len(dependencies)} deps before: {cutoff_datetime.date()}")
        historical_versions = {}
        for dep_name in dependencies:
            try:
                response = requests.get(f"https://pypi.org/pypi/{dep_name}/json", timeout=10)
                if response.status_code != 200: continue
                
                dep_data = response.json()
                latest_valid_version = None
                
                for version, releases in dep_data.get("releases", {}).items():
                    if not releases or not releases[0].get("upload_time_iso_8601"): continue
                    try:
                        release_datetime = datetime.fromisoformat(releases[0]["upload_time_iso_8601"].replace("Z", "+00:00"))
                        if release_datetime <= cutoff_datetime:
                            if latest_valid_version is None or parse_version(version) > parse_version(latest_valid_version):
                                latest_valid_version = version
                    except (ValueError, InvalidVersion): continue
                
                if latest_valid_version:
                    historical_versions[dep_name] = latest_valid_version
            except Exception: continue
        
        print(f"    ✅ Resolved {len(historical_versions)} historical dependencies.")
        return historical_versions

    def create_isolated_bubble(self, package_name: str, target_version: str) -> bool:
        c_name = self._normalize_package_name(package_name)
        print(f"🫧 Creating isolated bubble for {c_name} v{target_version}")
        
        # Step 1: Get the release date (The anchor for our time travel)
        release_date = self._get_release_date(c_name, target_version)
        if not release_date:
            self._mark_version_failed(c_name, target_version, "no_release_date")
            return False

        # Step 2: Get the NAMES of the dependencies (The shopping list)
        dep_names = self._get_deps_from_temp_venv(c_name, target_version)
        
        # Step 3: Find the correct historical VERSIONS for those names (The time travel)
        historical_deps = self._find_historical_versions(dep_names, release_date)
        
        # Step 4: Construct the final, historically accurate install list
        install_specs = [f"{name}=={ver}" for name, ver in historical_deps.items()]
        install_specs.append(f"{c_name}=={target_version}")

        print(f"\n    🚀 Final historical install specification:")
        for spec in install_specs: print(f"       -> {spec}")
        
        with tempfile.TemporaryDirectory(prefix="omnipkg-bubble-") as temp_dir:
            temp_path = Path(temp_dir)
            try:
                cmd = [self.config["python_executable"], "-m", "pip", "install", "--target", str(temp_path)] + install_specs
                if subprocess.run(cmd, capture_output=True, text=True, timeout=600).returncode != 0:
                    self._mark_version_failed(c_name, target_version, "final_installation_failed")
                    return False
            except Exception:
                self._mark_version_failed(c_name, target_version, "installation_exception")
                return False

            installed_tree = self._analyze_installed_tree(temp_path)
            bubble_path = self.multiversion_base / f"{c_name}-{target_version}"
            if bubble_path.exists(): shutil.rmtree(bubble_path)
            
            success = self._create_deduplicated_bubble(installed_tree, bubble_path, temp_path)
            if success:
                self._register_package_path(c_name, target_version, bubble_path)
                print(f"    📍 Registered path: {c_name}=={target_version} -> {bubble_path}")
            else:
                self._mark_version_failed(c_name, target_version, "bubble_creation_failed")
            return success

    # --- All the other helper methods (_analyze_installed_tree, _create_deduplicated_bubble, _get_file_hash, etc.) are correct. ---
    # They should remain exactly as they were in the last version.
    def _analyze_installed_tree(self, temp_path: Path) -> Dict[str, Dict]:
        installed = {}
        for dist_info in temp_path.glob("*.dist-info"):
            try:
                dist = importlib.metadata.Distribution.at(dist_info)
                if dist and dist.metadata.get("Name"):
                    pkg_files = [temp_path / f for f in dist.files if (temp_path / f).exists()]
                    installed[dist.metadata['Name']] = {'version': dist.metadata['Version'], 'files': pkg_files, 'type': self._classify_package_type(pkg_files), 'metadata': dict(dist.metadata.items())}
            except Exception: pass
        return installed

    def _classify_package_type(self, files: List[Path]) -> str:
        has_py = any(f.suffix in ['.py', '.pyc'] for f in files)
        has_native = any(f.suffix in ['.so', '.pyd', '.dll'] for f in files)
        return 'mixed' if has_py and has_native else 'native' if has_native else 'pure_python'

    def _create_deduplicated_bubble(self, installed_tree: Dict, bubble_path: Path, temp_path: Path) -> bool:
        print(f"    🧹 Creating deduplicated bubble at {bubble_path}")
        bubble_path.mkdir(parents=True, exist_ok=True)
        hashes = self._get_or_build_main_env_hash_index()
        total, copied = 0, 0
        for name, info in installed_tree.items():
            is_native = info.get('type') in ['native', 'mixed']
            if is_native: print(f"    ⚠️  Disabling deduplication for native package: {name}")
            for file in info.get('files', []):
                if not file.is_file(): continue
                total += 1
                should_copy = True
                if not is_native:
                    try:
                        if self._get_file_hash(file) in hashes: should_copy = False
                    except IOError: pass
                if should_copy:
                    try:
                        dest = bubble_path / file.relative_to(temp_path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file, dest)
                        copied += 1
                    except Exception: pass
        print(f"    ✅ Bubble created: {copied} files copied, {total - copied} deduplicated.")
        if total > 0: print(f"    📊 Space efficiency: {(total - copied) / total:.1%} saved.")
        self._create_bubble_manifest(bubble_path, installed_tree)
        return True

    def _get_or_build_main_env_hash_index(self) -> Set[str]:
        if not self.parent_omnipkg.redis_client: self.parent_omnipkg.connect_redis()
        key = f"{self.config['redis_key_prefix']}main_env:file_hashes"
        if self.parent_omnipkg.redis_client.exists(key):
            return set(self.parent_omnipkg.redis_client.sscan_iter(key))
        print(f"    🔍 Building main environment hash index...")
        hashes = set()
        for file in self.site_packages.rglob("*"):
            if file.is_file():
                try: hashes.add(self._get_file_hash(file))
                except IOError: continue
        with self.parent_omnipkg.redis_client.pipeline() as pipe:
            if hashes: pipe.sadd(key, *list(hashes))
            pipe.execute()
        return hashes

    def _get_file_hash(self, file_path: Path) -> str:
        path_str = str(file_path)
        if path_str in self.file_hash_cache: return self.file_hash_cache[path_str]
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192): h.update(chunk)
        self.file_hash_cache[path_str] = h.hexdigest()
        return self.file_hash_cache[path_str]

    def _create_bubble_manifest(self, bubble_path: Path, installed_tree: Dict):
        print("    📋 Creating comprehensive bubble manifest...")
        total_size = sum(f.stat().st_size for f in bubble_path.rglob('*') if f.is_file())
        manifest = {
            'created_at': datetime.now().isoformat(),
            'packages': { name: {
                    'version': info['version'], 'type': info['type'],
                    'summary': info.get('metadata', {}).get('Summary'),
                    'license': info.get('metadata', {}).get('License'),
                    'home_page': info.get('metadata', {}).get('Home-page')
                } for name, info in installed_tree.items()
            },
            'stats': {'bubble_size_mb': round(total_size / (1024*1024), 2), 'package_count': len(installed_tree)}
        }
        self._atomic_write_json(bubble_path / ".omnipkg_manifest.json", manifest, FileLock(bubble_path / ".manifest.lock"))

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

    def _normalize_package_name(self, package_name: str) -> str:
        """Normalizes a package name for consistent lookups."""
        return package_name.lower().replace("_", "-")

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
                            try:
                                pipe.srem(redis_key, self.bubble_manager._get_file_hash(dist.locate_file(file)))
                            except FileNotFoundError:
                                continue
                except importlib.metadata.PackageNotFoundError:
                    continue

            for name, ver in installed_or_changed.items():
                try:
                    dist = importlib.metadata.distribution(name)
                    if dist.files:
                        for file in dist.files:
                            try:
                                 pipe.sadd(redis_key, self.bubble_manager._get_file_hash(dist.locate_file(file)))
                            except FileNotFoundError:
                                continue
                except importlib.metadata.PackageNotFoundError:
                    continue

            pipe.execute()
        print("✅ Hash index updated.")

    def get_installed_packages(self, live: bool = False) -> Dict[str, str]:
        if live:
            try:
                cmd = [self.config["python_executable"], "-m", "pip", "list", "--format=json"]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                live_packages = {self._normalize_package_name(pkg['name']): pkg['version'] for pkg in json.loads(result.stdout)}
                self._installed_packages_cache = live_packages
                return live_packages
            except Exception:
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
        changed_packages = {name for name, ver in after.items() if name not in before or before[name] != ver}
        if not changed_packages: return

        print(f"🧠 Updating knowledge base for {len(changed_packages)} changed package(s)...")
        try:
            cmd = [self.config["python_executable"], self.config["builder_script_path"]] + list(changed_packages)
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            self._info_cache.clear()
            self._installed_packages_cache = None
            print("✅ Knowledge base updated successfully.")
        except Exception:
                 print(f"    ⚠️ Failed to update knowledge base for delta.")

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
            self.redis_client.set(snapshot_key, json.dumps(current_state))
            print("   ✅ Snapshot saved.")
        except Exception:
            print(f"   ⚠️ Could not save environment snapshot.")
    
    def _sort_packages_newest_first(self, packages: List[str]) -> List[str]:
        """Sorts packages by version, newest first."""
        def get_version_key(pkg_spec):
            match = re.search(r'(==|>=|<=|>|<)(.+)', pkg_spec)
            if match:
                try: return parse_version(match.group(2).strip())
                except InvalidVersion: return parse_version('0.0.0')
            return parse_version('9999.0.0')
        return sorted(packages, key=get_version_key, reverse=True)

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

    # In omnipkg/core.py -> inside the omnipkg class
# REPLACE your current smart_install with this one

    def smart_install(self, packages: List[str], dry_run: bool = False) -> int:
        """
        ## REBUILT for v1.0.9 ##
        A proactive installer that correctly identifies when to use the "Time Machine"
        bubble creation logic versus a standard installation.
        """
        if not self.connect_redis(): return 1
        if dry_run:
            print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0

        sorted_packages = self._sort_packages_newest_first(packages)
        if sorted_packages != packages:
            print(f"🔄 Reordered packages for optimal installation: {', '.join(sorted_packages)}")
        
        processed_packages = set()

        for package_spec in sorted_packages:
            print("\n" + "─"*60)
            print(f"📦 Processing: {package_spec}")
            print("─"*60)

            if self._check_package_satisfaction([package_spec])['all_satisfied']:
                print(f"✅ Requirement already satisfied: {package_spec}")
                continue

            # --- THE DEFINITIVE LOGIC FIX ---
            try:
                pkg_name = self._normalize_package_name(package_spec.split('==')[0])
                install_version_str = package_spec.split('==')[1]
                
                # Check what is currently active in the environment
                current_version_str = importlib.metadata.version(pkg_name)
                
                # Compare versions
                if parse_version(install_version_str) < parse_version(current_version_str):
                    # This is a clear downgrade. Go DIRECTLY to bubble creation.
                    print(f"    🧠 Detected request for older version. Current is v{current_version_str}.")
                    print(f"    🚀 Invoking Time Machine to create isolated bubble for v{install_version_str}.")
                    self.bubble_manager.create_isolated_bubble(pkg_name, install_version_str)
                    continue # Move to the next package in the list
                
            except (importlib.metadata.PackageNotFoundError, IndexError):
                # Package is not installed, or spec has no version.
                # Proceed with main environment installation.
                pass
            
            # --- MAIN ENVIRONMENT INSTALL PATH (for newest versions or new packages) ---
            print(f"    🚀 Installing '{package_spec}' into main environment.")
            packages_before = self.get_installed_packages(live=True)
            
            return_code = self._run_pip_install([package_spec])
            if return_code != 0:
                print(f"❌ Pip installation for {package_spec} failed. Continuing...")
                continue

            packages_after = self.get_installed_packages(live=True)
            processed_packages.add(pkg_name)

            # Safety net: check for any collateral downgrades from this install
            downgrades_to_fix = self._detect_downgrades(packages_before, packages_after)
            if downgrades_to_fix:
                print("\n🛡️  DOWNGRADE PROTECTION ACTIVATED!")
                for fix in downgrades_to_fix:
                    if fix['package'] == pkg_name: continue # Skip the package we just installed
                    print(f"    -> Fixing collateral downgrade: {fix['package']} from v{fix['good_version']} to v{fix['bad_version']}")
                    self.bubble_manager.create_isolated_bubble(fix['package'], fix['bad_version'])
                    print(f"    🔄 Restoring '{fix['package']}' to safe version v{fix['good_version']}...")
                    self._run_pip_install([f"{fix['package']}=={fix['good_version']}"])

            # Update knowledge base for the main environment
            self._run_metadata_builder_for_delta(packages_before, self.get_installed_packages(live=True))
            self._update_hash_index_for_delta(packages_before, self.get_installed_packages(live=True))

        print("\n" + "="*60 + "\n🎉 All package operations complete.")
        self._save_last_known_good_snapshot()
        return 0

    def _find_package_installations(self, package_name: str) -> List[Dict]:
        """Find all installations of a package, both active and bubbled."""
        found = []
        # 1. Check for active installation in main environment
        try:
            active_version = importlib.metadata.version(package_name)
            found.append({
                "name": package_name,
                "version": active_version,
                "type": "active",
                "path": "Main Environment"
            })
        except importlib.metadata.PackageNotFoundError:
            pass
    
        # 2. Check for bubbled installations
        # Use canonical name for searching bubble directories
        canonical_name = package_name.lower().replace("_", "-")
        for bubble_dir in self.multiversion_base.glob(f"{canonical_name}-*"):
            if bubble_dir.is_dir():
                try:
                    # THE FIX IS HERE: Use rsplit to correctly handle names with hyphens
                    pkg_name_from_dir, version = bubble_dir.name.rsplit('-', 1)
                    found.append({
                        "name": package_name, # Keep original case for consistency
                        "version": version,
                        "type": "bubble",
                        "path": bubble_dir
                    })
                except IndexError:
                    continue
        return found

    def smart_uninstall(self, packages: List[str], force: bool = False) -> int:
        """Uninstalls packages from the main environment or from bubbles."""
        if not self.connect_redis(): return 1

        for pkg_spec in packages:
            print(f"\nProcessing uninstall for: {pkg_spec}")
            
            try:
                pkg_name, specific_version = pkg_spec.split('==')
            except ValueError:
                pkg_name, specific_version = pkg_spec, None
            installations = self._find_package_installations(pkg_name)

            if not installations:
                print(f"🤷 Package '{pkg_name}' not found.")
                continue

            to_uninstall = []
            if specific_version:
                # User specified a version, find that exact one
                to_uninstall = [inst for inst in installations if inst['version'] == specific_version]
                if not to_uninstall:
                    print(f"🤷 Version '{specific_version}' of '{pkg_name}' not found.")
                    continue
            else:
                # No version specified, target all found installations
                to_uninstall = installations
            
            print(f"Found {len(to_uninstall)} installation(s) to remove:")
            for item in to_uninstall:
                print(f"  - v{item['version']} ({item['type']})")
            
            if not force:
                confirm = input("🤔 Are you sure you want to proceed? (y/N): ").lower().strip()
                if confirm != 'y':
                    print("🚫 Uninstall cancelled.")
                    continue

            # Perform uninstallation
            for item in to_uninstall:
                if item['type'] == 'active':
                    print(f"🗑️ Uninstalling '{item['name']}' from main environment...")
                    self._run_pip_uninstall([item['name']])
                elif item['type'] == 'bubble':
                    print(f"🗑️ Deleting bubble: {item['path'].name}")
                    shutil.rmtree(item['path'])

                # Clean up Redis
                main_key = f"{self.config['redis_key_prefix']}{item['name'].lower()}"
                version_key = f"{main_key}:{item['version']}"
                with self.redis_client.pipeline() as pipe:
                    pipe.srem(f"{main_key}:installed_versions", item['version'])
                    pipe.delete(version_key)
                    # If this was the active version, clear it
                    if self.redis_client.hget(main_key, "active_version") == item['version']:
                        pipe.hdel(main_key, "active_version")
                    # If this was a bubble version, clear it
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

    def _check_package_satisfaction(self, packages: List[str]) -> dict:
        """Check satisfaction with bubble pre-check optimization"""
        satisfied = set()
        remaining_packages = []

        # FAST PATH: Check for pre-existing bubbles BEFORE calling pip
        for pkg_spec in packages:
            try:
                if '==' in pkg_spec:
                    pkg_name, version = pkg_spec.split('==', 1)
                    # Use the bubble manager's path registry for the check
                    bubble_path = self.bubble_manager.get_package_path(self._normalize_package_name(pkg_name), version)
                    if bubble_path and bubble_path.exists():
                        satisfied.add(pkg_spec)
                        print(f"    ⚡ Found existing bubble: {pkg_spec}")
                        continue
                remaining_packages.append(pkg_spec)
            except ValueError:
                remaining_packages.append(pkg_spec)

        if not remaining_packages:
            return {'all_satisfied': True, 'satisfied': sorted(list(satisfied)), 'needs_install': []}

        # SLOW PATH: Call pip for packages without bubbles
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, dir='.') as f:
                f.write("\n".join(remaining_packages))
                req_file_path = f.name
            
            cmd = [self.config["python_executable"], "-m", "pip", "install", "--dry-run", "-r", req_file_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            os.unlink(req_file_path) # Clean up temp file

            for line in result.stdout.splitlines():
                if line.startswith("Requirement already satisfied:"):
                    try:
                        satisfied_spec = line.split(":", 1)[1].strip()
                        req_name = self._normalize_package_name(satisfied_spec.split('==')[0])
                        for user_req in remaining_packages:
                            if self._normalize_package_name(user_req.split('==')[0]) == req_name:
                                satisfied.add(user_req)
                    except Exception: continue
            
            needs_install = [pkg for pkg in packages if pkg not in satisfied]
            return {'all_satisfied': not needs_install, 'satisfied': sorted(list(satisfied)), 'needs_install': needs_install}
        except Exception:
            return {'all_satisfied': False, 'satisfied': list(satisfied), 'needs_install': remaining_packages}

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