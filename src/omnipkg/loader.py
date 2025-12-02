from __future__ import annotations  # Python 3.6+ compatibility

import sys
import importlib
import shutil
import time
import gc
from pathlib import Path
import os
import subprocess
import re
import filelock
import textwrap
import warnings
import tempfile
import threading
from typing import Optional, Dict, Any, List, Tuple
import json
import site
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version as get_version, PackageNotFoundError
import signal
from contextlib import contextmanager
import io  # <-- ADD THIS, needed for execute() method

# Import safe_print and custom exceptions
try:
    from .common_utils import safe_print, UVFailureDetector, ProcessCorruptedException
except ImportError:
    from omnipkg.common_utils import safe_print, UVFailureDetector, ProcessCorruptedException

# Import i18n
from omnipkg.i18n import _

# ═══════════════════════════════════════════════════════════
# 🧠 INSTALL TENSORFLOW PATCHER AT MODULE LOAD (ONCE ONLY)
# ═══════════════════════════════════════════════════════════
try:
    from omnipkg.tf_patcher import smart_tf_patcher
    smart_tf_patcher()
except ImportError:
    pass  # Patcher not available

# ═══════════════════════════════════════════════════════════
# Import Daemon Components (NEW)
# ═══════════════════════════════════════════════════════════
try:
    from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy
    DAEMON_AVAILABLE = True
except ImportError:
    DAEMON_AVAILABLE = False
    class DaemonClient:
        pass
    class DaemonProxy:
        pass

# ═══════════════════════════════════════════════════════════
# Legacy Worker Support (DEPRECATED - use daemon instead)
# ═══════════════════════════════════════════════════════════
try:
    from omnipkg.isolation.workers import PersistentWorker
    WORKER_AVAILABLE = True
except ImportError:
    WORKER_AVAILABLE = False
    class PersistentWorker:
        def __init__(self, *args, **kwargs):
            raise ImportError("PersistentWorker not available")  # <-- FIXED: Added closing parenthesis


class omnipkgLoader:
    """
    Activates isolated package environments with optional persistent worker pool.
    """
    
    # ═══════════════════════════════════════════════════════════
    # CLASS-LEVEL WORKER POOL (Shared across all loader instances)
    # ═══════════════════════════════════════════════════════════
    _worker_pool = {}  # {package_spec: PersistentWorker}
    _worker_pool_lock = threading.RLock()
    _worker_pool_enabled = True  # Global toggle
    _cloak_locks: Dict[str, filelock.FileLock] = {}
    _install_locks: Dict[str, filelock.FileLock] = {} # <-- NEW: Add install locks
    _locks_dir: Optional[Path] = None
    _numpy_version_history: List[str] = []
    _global_cloaking_lock = threading.RLock()  # Re-entrant lock
    _numpy_lock = threading.Lock() # Protects the history list
    _active_main_env_packages = set()  # Packages currently active from main env
    _dependency_cache: Optional[Dict[str, Path]] = None
    # -------------------------------------------------------------------------
    # 🛡️ IMMORTAL PACKAGES: These must never be cloaked/deleted
    # -------------------------------------------------------------------------
    _CRITICAL_DEPS = {
        # Core omnipkg
        'omnipkg', 'click', 'rich', 'toml', 'packaging', 'filelock', 'colorama',
        'tabulate', 'psutil', 'distro', 'pydantic', 'pydantic_core', 'ruamel.yaml',
        'safety_schemas', 'typing_extensions', 'mypy_extensions',
        
        # Networking (Requests) - CRITICAL for simple fetches
        'requests', 'urllib3', 'charset_normalizer', 'idna', 'certifi',
        
        # Async Networking (Aiohttp) - CRITICAL for OmniPkg background tasks
        'aiohttp', 'aiosignal', 'aiohappyeyeballs', 'attrs', 'frozenlist', 
        'multidict', 'yarl',
        
        # Cache
        'redis',
    }

    def __init__(self, package_spec: str=None, config: dict=None, quiet: bool=False, 
                 force_activation: bool=False, use_worker_pool: bool = True, 
                 worker_fallback: bool = True, isolation_mode: str='strict'):
        """
        Initializes the loader with enhanced Python version awareness.
        """  
        self._true_site_packages = None
        
        # Try to find the real site-packages via the omnipkg module location
        try:
            import omnipkg
            # Usually .../site-packages/omnipkg
            omnipkg_loc = Path(omnipkg.__file__).parent.parent
            if omnipkg_loc.name == 'site-packages':
                self._true_site_packages = omnipkg_loc
        except ImportError:
            pass
      
        if config is None:
            # If no config is passed, become self-sufficient and load it.
            # Lazy import to prevent circular dependencies.
            from omnipkg.core import ConfigManager
            try:
                # Suppress messages because this is a background load.
                cm = ConfigManager(suppress_init_messages=True)
                self.config = cm.config
            except Exception:
                # If config fails to load for any reason, proceed with None.
                # The auto-detection logic will still serve as a fallback.
                self.config = {}
        else:
            self.config = config
        if os.environ.get("OMNIPKG_IS_DAEMON_WORKER"):
            self.quiet = True
        else:
            self.quiet = quiet

        self.python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
        self.python_version_nodot = f'{sys.version_info.major}{sys.version_info.minor}'
        self.force_activation = force_activation
        
        if not self.quiet:
            safe_print(_('🐍 [omnipkg loader] Running in Python {} context').format(self.python_version))
        self._initialize_version_aware_paths()
        self._store_clean_original_state()
        self._current_package_spec = package_spec
        self._activated_bubble_path = None
        self._cloaked_main_modules = []
        self.isolation_mode = isolation_mode
        self._activation_successful = False
        self._activation_start_time = None
        self._activation_end_time = None
        self._deactivation_start_time = None
        self._worker_from_pool = False   
        self._worker_fallback_enabled = worker_fallback
        self._active_worker = None
        self._worker_mode = False
        self._packages_we_cloaked = set()  # Only packages WE cloaked
        self._using_main_env = False  # Track if we're using main env directly
        self._my_main_env_package = None 
        self._use_worker_pool = use_worker_pool
        self._cloaked_main_modules = []
        self._deactivation_end_time = None
        self._total_activation_time_ns = None
        self._total_deactivation_time_ns = None
        self._omnipkg_dependencies = self._get_omnipkg_dependencies()
        self._activated_bubble_dependencies = [] # To track everything we need to exorcise

        if omnipkgLoader._locks_dir is None:
                omnipkgLoader._locks_dir = self.multiversion_base / '.locks'
                omnipkgLoader._locks_dir.mkdir(parents=True, exist_ok=True)
    
    # ═══════════════════════════════════════════════════════════
    # WORKER POOL MANAGEMENT
    # ═══════════════════════════════════════════════════════════
    
    @classmethod
    def _get_or_create_worker(cls, package_spec: str, verbose: bool = False):
        """
        Get a worker from the pool, or create one if it doesn't exist.
        
        This is the KEY to performance - workers stay alive between activations!
        """
        with cls._worker_pool_lock:
            # Check if worker already exists and is healthy
            if package_spec in cls._worker_pool:
                worker = cls._worker_pool[package_spec]
                
                # Health check
                if worker.process and worker.process.poll() is None:
                    return worker, True  # (worker, from_pool)
                else:
                    # Worker died, remove it
                    if verbose:
                        safe_print(f"   ♻️  Restarting dead worker for {package_spec}")
                    try:
                        worker.shutdown()
                    except:
                        pass
                    del cls._worker_pool[package_spec]
            
            # Create new worker
            try:
                if verbose:
                    safe_print(f"   🔄 Creating new worker for {package_spec}...")
                
                worker = PersistentWorker(
                    package_spec=package_spec,
                    verbose=verbose
                )
                
                cls._worker_pool[package_spec] = worker
                
                if verbose:
                    safe_print(f"   ✅ Worker created and added to pool")
                
                return worker, False  # (worker, from_pool)
            except Exception as e:
                if verbose:
                    safe_print(f"   ❌ Worker creation failed: {e}")
                return None, False
    
    @classmethod
    def shutdown_worker_pool(cls, verbose: bool = True):
        """
        Shutdown ALL workers in the pool.
        
        Call this at program exit or when you're done with version swapping.
        """
        with cls._worker_pool_lock:
            if not cls._worker_pool:
                if verbose:
                    safe_print("   ℹ️  Worker pool is already empty")
                return
            
            if verbose:
                safe_print(f"   🛑 Shutting down worker pool ({len(cls._worker_pool)} workers)...")
            
            for spec, worker in list(cls._worker_pool.items()):
                try:
                    worker.shutdown()
                    if verbose:
                        safe_print(f"      ✅ Shutdown: {spec}")
                except Exception as e:
                    if verbose:
                        safe_print(f"      ⚠️  Failed to shutdown {spec}: {e}")
            
            cls._worker_pool.clear()
            
            if verbose:
                safe_print(f"   ✅ Worker pool shutdown complete")
    
    @classmethod
    def get_worker_pool_stats(cls) -> dict:
        """Get statistics about the current worker pool."""
        with cls._worker_pool_lock:
            active_workers = []
            dead_workers = []
            
            for spec, worker in cls._worker_pool.items():
                if worker.process and worker.process.poll() is None:
                    active_workers.append(spec)
                else:
                    dead_workers.append(spec)
            
            return {
                'total': len(cls._worker_pool),
                'active': len(active_workers),
                'dead': len(dead_workers),
                'active_specs': active_workers,
                'dead_specs': dead_workers
            }

    def _create_worker_for_spec(self, package_spec: str):
        """
        Connects to the daemon to handle this package spec.
        """
        if not self._use_worker_pool:
            return None
    
        # Don't use daemon if we ARE the daemon worker (prevent recursion)
        if os.environ.get("OMNIPKG_IS_DAEMON_WORKER"):
            return None
    
        try:
            # Get the client (auto-starts if needed)
            client = self._get_daemon_client()
            
            # Return proxy that looks like a worker but talks to daemon
            proxy = DaemonProxy(client, package_spec)
            
            if not self.quiet:
                safe_print(f"   ⚡ Connected to Daemon for {package_spec}")
                
            return proxy
            
        except Exception as e:
            if not self.quiet:
                safe_print(f"   ⚠️  Daemon connection failed: {e}. Falling back to local.")
            return None
            
    def _get_cloak_lock(self, pkg_name: str) -> filelock.FileLock:
        """
        Get or create a file lock for a specific package's cloak operations.
        This ensures only ONE loader can cloak/uncloak a package at a time.
        """
        canonical_name = pkg_name.lower().replace('-', '_')
        
        if canonical_name not in omnipkgLoader._cloak_locks:
            lock_file = omnipkgLoader._locks_dir / f"{canonical_name}.lock"
            omnipkgLoader._cloak_locks[canonical_name] = filelock.FileLock(
                str(lock_file),
                timeout=10  # Wait up to 10 seconds for lock
            )
        
        return omnipkgLoader._cloak_locks[canonical_name]
    
    def _get_install_lock(self, spec_str: str) -> filelock.FileLock:
        """
        Gets or creates a file lock for a specific package INSTALLATION.
        This prevents race conditions when multiple threads try to install
        the same missing bubble.
        """
        # Normalize the name for the lock file
        lock_name = spec_str.replace('==', '-').replace('.', '_')
        
        if lock_name not in omnipkgLoader._install_locks:
            lock_file = omnipkgLoader._locks_dir / f"install-{lock_name}.lock"
            omnipkgLoader._install_locks[lock_name] = filelock.FileLock(
                str(lock_file),
                timeout=300  # Wait up to 5 minutes for an install to finish
            )
        
        return omnipkgLoader._install_locks[lock_name]

    def _initialize_version_aware_paths(self):
        """
        Initialize paths with strict Python version isolation.
        Ensures we only work with version-compatible directories.
        """
        if self.config and 'multiversion_base' in self.config and ('site_packages_path' in self.config):
            self.multiversion_base = Path(self.config['multiversion_base'])
            configured_site_packages = Path(self.config['site_packages_path'])
            if self._is_version_compatible_path(configured_site_packages):
                self.site_packages_root = configured_site_packages
                if not self.quiet:
                    safe_print(_('✅ [omnipkg loader] Using configured site-packages: {}').format(self.site_packages_root))
            else:
                if not self.quiet:
                    safe_print(_('⚠️ [omnipkg loader] Configured site-packages path is not compatible with Python {}. Auto-detecting...').format(self.python_version))
                self.site_packages_root = self._auto_detect_compatible_site_packages()
        else:
            if not self.quiet:
                safe_print(_('⚠️ [omnipkg loader] Config not provided or incomplete. Auto-detecting Python {}-compatible paths.').format(self.python_version))
            self.site_packages_root = self._auto_detect_compatible_site_packages()
            self.multiversion_base = self.site_packages_root / '.omnipkg_versions'
        if not self.multiversion_base.exists():
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                if not self.quiet:
                    safe_print(_('✅ [omnipkg loader] Created bubble directory: {}').format(self.multiversion_base))
            except Exception as e:
                raise RuntimeError(_('Failed to create bubble directory at {}: {}').format(self.multiversion_base, e))

    def _is_version_compatible_path(self, path: Path) -> bool:
        """
        Performs a robust check to see if a given path belongs to the
        currently running Python interpreter's version, preventing
        cross-version contamination.
        """
        path_str = str(path).lower()
        match = re.search('python(\\d+\\.\\d+)', path_str)
        if not match:
            return True
        path_version = match.group(1)
        if path_version == self.python_version:
            return True
        else:
            if not self.quiet:
                safe_print(_('🚫 [omnipkg loader] Rejecting incompatible path (contains python{}) for context python{}: {}').format(path_version, self.python_version, path))
            return False

    def _auto_detect_compatible_site_packages(self) -> Path:
        """
        Auto-detect site-packages path that's compatible with current Python version.
        """
        try:
            for site_path in site.getsitepackages():
                candidate = Path(site_path)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    if not self.quiet:
                        safe_print(_('✅ [omnipkg loader] Auto-detected compatible site-packages: {}').format(candidate))
                    return candidate
        except (AttributeError, IndexError):
            pass
        python_version_path = f'python{self.python_version}'
        candidate = Path(sys.prefix) / 'lib' / python_version_path / 'site-packages'
        if candidate.exists():
            if not self.quiet:
                safe_print(_('✅ [omnipkg loader] Using sys.prefix-based site-packages: {}').format(candidate))
            return candidate
        for path_str in sys.path:
            if 'site-packages' in path_str:
                candidate = Path(path_str)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    if not self.quiet:
                        safe_print(_('✅ [omnipkg loader] Using sys.path-derived site-packages: {}').format(candidate))
                    return candidate
        raise RuntimeError(_('Could not auto-detect Python {}-compatible site-packages directory').format(self.python_version))

    def _store_clean_original_state(self):
        """
        Store original state with contamination filtering to prevent cross-version issues.
        """
        self.original_sys_path = []
        contaminated_paths = []
        for path_str in sys.path:
            path_obj = Path(path_str)
            if self._is_version_compatible_path(path_obj):
                self.original_sys_path.append(path_str)
            else:
                contaminated_paths.append(path_str)
        if contaminated_paths and not self.quiet:
            safe_print(_('🧹 [omnipkg loader] Filtered out {} incompatible paths from sys.path').format(len(contaminated_paths)))
        self.original_sys_modules_keys = set(sys.modules.keys())
        self.original_path_env = os.environ.get('PATH', '')
        self.original_pythonpath_env = os.environ.get('PYTHONPATH', '')
        if not self.quiet:
            safe_print(_('✅ [omnipkg loader] Stored clean original state with {} compatible paths').format(len(self.original_sys_path)))

    def _filter_environment_paths(self, env_var: str) -> str:
        """
        Filter environment variable paths to remove incompatible Python versions.
        """
        if env_var not in os.environ:
            return ''
        original_paths = os.environ[env_var].split(os.pathsep)
        filtered_paths = []
        for path_str in original_paths:
            if self._is_version_compatible_path(Path(path_str)):
                filtered_paths.append(path_str)
        return os.pathsep.join(filtered_paths)
    
    def _get_omnipkg_dependencies(self) -> Dict[str, Path]:
        """
        Gets dependency paths with cache validation.
        """
        # Tier 1: Memory Cache
        if omnipkgLoader._dependency_cache is not None:
            return omnipkgLoader._dependency_cache

        # Tier 2: File Cache
        cache_file = self.multiversion_base / '.cache' / f'loader_deps_{self.python_version}.json'
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                
                # Convert to Path objects
                dependencies = {name: Path(path) for name, path in cached_data.items()}
                
                # 🔍 VALIDATION: Check if cache covers our current critical list
                # If we updated the code to add 'aiohttp', but cache is old, we MUST invalidate.
                cached_keys = set(dependencies.keys())
                # Normalize critical deps to canonical names for comparison
                required_keys = {d.replace('-', '_') for d in self._CRITICAL_DEPS}
                
                missing_criticals = required_keys - cached_keys
                
                # Ignore packages that genuinely aren't installed, but if cache is EMPTY for them...
                # Actually, simpler heuristic: If cache lacks aiohttp/requests, it's definitely stale.
                if 'aiohttp' in self._CRITICAL_DEPS and 'aiohttp' not in cached_keys:
                     if not self.quiet:
                        safe_print("   ♻️  Cache stale (missing aiohttp). Re-scanning dependencies...")
                else:
                    omnipkgLoader._dependency_cache = dependencies
                    return dependencies

            except (json.JSONDecodeError, IOError, Exception):
                pass # Cache corrupt or invalid, proceed to detection

        # Tier 3: Detection & Save
        dependencies = self._detect_omnipkg_dependencies()
        omnipkgLoader._dependency_cache = dependencies
        
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            paths_to_save = {name: str(path) for name, path in dependencies.items()}
            with open(cache_file, 'w') as f:
                json.dump(paths_to_save, f)
        except IOError:
            pass

        return dependencies
    
    def _compute_omnipkg_dependencies(self) -> Dict[str, Path]:
        """
        (CORRECTED) Gets omnipkg's dependency paths, using a class-level
        cache to ensure the expensive detection runs only once per session.
        """
        # --- Check the cache first ---
        if omnipkgLoader._dependency_cache is not None:
            return omnipkgLoader._dependency_cache

        # --- If cache is empty, run the original detection logic ---
        # FIXED: Call the actual implementation instead of recursing
        dependencies = self._detect_omnipkg_dependencies()
        
        # --- Store the result in the cache for next time ---
        omnipkgLoader._dependency_cache = dependencies
        return dependencies

    def _detect_omnipkg_dependencies(self):
        """
        Detects critical dependency paths.
        🛡️ AUTO-HEALING: If a critical dep is missing but a cloak exists,
        it will RESTORE (Un-Cloak) it immediately.
        """
        found_deps = {}
        
        for dep in self._CRITICAL_DEPS:
            # Try variations: 'typing_extensions', 'typing-extensions'
            dep_variants = [dep, dep.replace('-', '_'), dep.replace('_', '-')]
            
            # Special case for 'attr' package which is installed as 'attrs'
            if dep == 'attrs': 
                dep_variants.append('attr')
            
            for dep_variant in dep_variants:
                try:
                    # Attempt Import
                    dep_module = importlib.import_module(dep_variant)
                    
                except ImportError:
                    # 🚑 HEALING PROTOCOL: Module missing? Check if we cloaked it!
                    canonical = dep.replace('-', '_')
                    # Look for ANY cloak of this package
                    # We use the raw site_packages_root to bypass sys.path mess
                    cloaks = list(self.site_packages_root.glob(f"{canonical}*_omnipkg_cloaked*"))
                    
                    if cloaks:
                        if not self.quiet:
                             safe_print(f"   🚑 RESURRECTING critical package: {canonical} (Found {len(cloaks)} cloaks)")
                        
                        # Sort by timestamp (newest first) and restore
                        try:
                            # Simple cleanup of the name to find the target
                            # e.g., aiohttp.123_omnipkg_cloaked -> aiohttp
                            newest_cloak = sorted(cloaks, key=lambda p: str(p), reverse=True)[0]
                            original_name = re.sub(r'\.\d+_omnipkg_cloaked.*$', '', newest_cloak.name)
                            target_path = newest_cloak.parent / original_name
                            
                            # Nuke any empty directory blocking us
                            if target_path.exists():
                                if target_path.is_dir(): shutil.rmtree(target_path)
                                else: target_path.unlink()
                                
                            shutil.move(str(newest_cloak), str(target_path))
                            
                            # 🔄 RETRY IMPORT after healing
                            importlib.invalidate_caches()
                            try:
                                dep_module = importlib.import_module(dep_variant)
                                if not self.quiet: safe_print(f"      ✅ Resurrected and loaded: {original_name}")
                            except ImportError:
                                continue # Still broken, give up on this variant
                        except Exception as e:
                            if not self.quiet: safe_print(f"      ❌ Failed to resurrect {canonical}: {e}")
                            continue
                    else:
                        continue # No cloak found, genuinely missing

                # If we have the module (naturally or resurrected), record it
                if hasattr(dep_module, '__file__') and dep_module.__file__:
                    dep_path = Path(dep_module.__file__).parent
                    
                    if self._is_version_compatible_path(dep_path) and (
                        self.site_packages_root in dep_path.parents or 
                        dep_path == self.site_packages_root / dep_variant
                    ):
                        canonical_name = dep.replace('-', '_')
                        found_deps[canonical_name] = dep_path
                        break  # Found it, stop trying variants
        
        return found_deps

    def _ensure_main_site_packages_in_path(self):
            """
            If we decide to use a package from the main environment, we must ensure
            the main site-packages directory is actually in sys.path. 
            This is critical when running inside nested isolated workers that 
            may have stripped it out.
            """
            main_path = str(self.site_packages_root)
            if main_path not in sys.path:
                if not self.quiet:
                    safe_print(f"   🔌 Re-connecting main site-packages for {self._current_package_spec}")
                # Append to end to keep bubble isolation priority, 
                # but ensure visibility for this package.
                sys.path.append(main_path)

    def _ensure_omnipkg_access_in_bubble(self, bubble_path_str: str):
        """
        Ensure omnipkg's version-compatible dependencies remain accessible when bubble is active.
        """
        bubble_path = Path(bubble_path_str)
        linked_count = 0
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            bubble_dep_path = bubble_path / dep_name
            if bubble_dep_path.exists():
                continue
            if not self._is_version_compatible_path(dep_path):
                continue
            try:
                if dep_path.is_dir():
                    bubble_dep_path.symlink_to(dep_path, target_is_directory=True)
                else:
                    bubble_dep_path.symlink_to(dep_path)
                linked_count += 1
            except Exception as e:
                site_packages_str = str(self.site_packages_root)
                if site_packages_str not in sys.path:
                    insertion_point = 1 if len(sys.path) > 1 else len(sys.path)
                    sys.path.insert(insertion_point, site_packages_str)
        if linked_count > 0 and not self.quiet:
            safe_print(_('🔗 [omnipkg loader] Linked {} compatible dependencies to bubble').format(linked_count))

    def _get_bubble_dependencies(self, bubble_path: Path) -> dict:
        """
        (CORRECTED) Gets all packages from a bubble.
        Prioritizes reading the manifest, falls back to a fast scan for small
        bubbles, and uses a thorough scan for large bubbles.
        """
        # Strategy 1: Read the manifest (ultra-fast, always preferred)
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                return {
                    name.lower().replace('-', '_'): info.get('version')
                    for name, info in manifest.get('packages', {}).items()
                }
            except Exception:
                pass # Fall through to scanning if manifest is corrupt

        # If no manifest, proceed with scanning
        dependencies = {}
        dist_infos = list(bubble_path.rglob('*.dist-info'))

        # THE FIX: This is the actual implementation that was missing.
        for dist_info in dist_infos:
            if dist_info.is_dir():
                try:
                    from importlib.metadata import PathDistribution
                    dist = PathDistribution(dist_info)
                    pkg_name = dist.metadata['Name'].lower().replace('-', '_')
                    dependencies[pkg_name] = dist.version
                except Exception:
                    continue
        
        return dependencies

    def _get_bubble_package_version(self, bubble_path: Path, pkg_name: str) -> str:
        """Get version of a package from bubble manifest."""
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                packages = manifest.get('packages', {})
                return packages.get(pkg_name, {}).get('version')
        return None

    def _batch_cloak_packages(self, package_names: list):
        """
        Cloak multiple packages with PROCESS-WIDE SAFETY.
        """
        with omnipkgLoader._global_cloaking_lock:  
            loader_id = id(self)
            timestamp = int(time.time() * 1000000)
            cloak_suffix = f"{timestamp}_{loader_id}_omnipkg_cloaked"
            
            cloak_operations = []

            # CRITICAL: Build comprehensive protection set
            omnipkg_dep_names = set(self._omnipkg_dependencies.keys())
            
            # CRITICAL FIX: Add both naming conventions for typing_extensions
            omnipkg_dep_names.add('typing_extensions')
            omnipkg_dep_names.add('typing-extensions')
            
            # Add globally protected packages from other active loaders
            protected_packages = omnipkg_dep_names | omnipkgLoader._active_main_env_packages
            
            # Filter out ALL protected packages (check both naming conventions)
            packages_to_cloak = []
            for pkg in package_names:
                pkg_canonical = pkg.replace('-', '_')
                pkg_dashed = pkg.replace('_', '-')
                
                # Check if either naming convention is protected
                if pkg_canonical not in protected_packages and pkg_dashed not in protected_packages:
                    packages_to_cloak.append(pkg)
            
            if not self.quiet:
                total_protected = len(package_names) - len(packages_to_cloak)
                if total_protected > 0:
                    safe_print(f"   - 🛡️ Protected {total_protected} critical packages from cloaking")
                    protected_list = [p for p in package_names if p not in packages_to_cloak]
                    safe_print(f"      Protected: {', '.join(protected_list)}")
                
                if packages_to_cloak:
                    safe_print(f"   - 🔍 Will cloak CODE for: {', '.join(packages_to_cloak)}")
            
            # Prepare all operations first
            successful_cloaks = []
            for original_path, cloak_path in cloak_operations:
                pkg_name = original_path.stem  # Get package name from path
                lock = self._get_cloak_lock(pkg_name)
                
                try:
                    with lock.acquire(timeout=5):  # 5 second timeout per package
                        # Double-check it still exists (another thread might have cloaked it)
                        if not original_path.exists():
                            if not self.quiet:
                                safe_print(f"      ⏭️  Skipping {original_path.name} (already cloaked by another loader)")
                            continue
                        
                        shutil.move(str(original_path), str(cloak_path))
                        successful_cloaks.append((original_path, cloak_path, True))
                        if not self.quiet:
                            safe_print(f"      ✅ Cloaked: {original_path.name}")
                            
                except filelock.Timeout:
                    if not self.quiet:
                        safe_print(f"      ⏱️  Timeout waiting for lock on {pkg_name}, skipping...")
                    successful_cloaks.append((original_path, cloak_path, False))
                except Exception as e:
                    if not self.quiet:
                        safe_print(f"      ❌ Failed to cloak {original_path.name}: {e}")
                    successful_cloaks.append((original_path, cloak_path, False))
            
            self._cloaked_main_modules.extend(successful_cloaks)
            return len([c for c in successful_cloaks if c[2]])
        
        def nuke_all_cloaks_for_package(self, pkg_name: str):
            """
            Nuclear option: Find and destroy ALL cloaked versions of a package.
            This is a recovery tool for when cloaking gets out of control.
            """
            canonical_name = pkg_name.lower().replace('-', '_')
            
            # Find ALL cloaks - any file/dir with _omnipkg_cloaked in the name
            all_cloaks = []
            
            patterns = [
                f"{canonical_name}*_omnipkg_cloaked*",  # numpy.123_omnipkg_cloaked
                f"{canonical_name}-*_omnipkg_cloaked*",  # numpy-2.3.5.dist-info.123_omnipkg_cloaked
            ]
            
            safe_print(f"\n🔍 Scanning for ALL {pkg_name} cloaks...")
            
            for pattern in patterns:
                for cloaked_path in self.site_packages_root.glob(pattern):
                    all_cloaks.append(cloaked_path)
                    safe_print(f"   📦 Found cloak: {cloaked_path.name}")
            
            if not all_cloaks:
                safe_print(f"   ✅ No cloaks found for {pkg_name}")
                return 0
            
            safe_print(f"\n💥 NUKING {len(all_cloaks)} cloak(s)...")
            destroyed_count = 0
            
            for cloak_path in all_cloaks:
                try:
                    if cloak_path.is_dir():
                        shutil.rmtree(cloak_path)
                    else:
                        cloak_path.unlink()
                    destroyed_count += 1
                    safe_print(f"   ☠️  Destroyed: {cloak_path.name}")
                except Exception as e:
                    safe_print(f"   ❌ Failed to destroy {cloak_path.name}: {e}")
            
            safe_print(f"\n✅ Nuked {destroyed_count}/{len(all_cloaks)} cloaks for {pkg_name}\n")
            return destroyed_count

    def _is_main_site_packages(self, path: str) -> bool:
        """Check if a path points to the main site-packages directory."""
        try:
            path_obj = Path(path).resolve()
            main_site_packages = self.site_packages_root.resolve()
            return path_obj == main_site_packages
        except:
            return False

    def _bubble_needs_fallback(self, bubble_path: Path) -> bool:
        """Determine if bubble needs access to main site-packages for dependencies."""
        # Check if bubble has all critical dependencies
        critical_deps = ['setuptools', 'pip', 'wheel']
        
        for dep in critical_deps:
            dep_path = bubble_path / dep
            dist_info_path = next(bubble_path.glob(f"{dep}-*.dist-info"), None)
            
            if not (dep_path.exists() or dist_info_path):
                return True
        
        return False

    def _add_selective_fallbacks(self, bubble_path: Path):
        """Add only specific non-conflicting packages from main environment."""
        bubble_packages = set(self._get_bubble_dependencies(bubble_path))
        
        # Only allow these safe packages from main environment
        safe_packages = {'setuptools', 'pip', 'wheel', 'certifi', 'urllib3'}
        
        # Create a restricted view of main site-packages
        main_site_packages = str(self.site_packages_root)
        
        # Only add main site-packages if we need safe packages
        needed_safe_packages = safe_packages - bubble_packages
        if needed_safe_packages and main_site_packages not in sys.path:
            sys.path.append(main_site_packages)

    def _scan_for_cloaked_versions(self, pkg_name: str) -> list:
        """
        Scan for ALL cloaked versions, now recognizing loader-specific suffixes.
        Returns list of (cloaked_path, original_name, timestamp, loader_id) tuples.
        """
        canonical_name = pkg_name.lower().replace('-', '_')
        cloaked_versions = []
        
        patterns = [
            f"{canonical_name}.*_omnipkg_cloaked*",
            f"{canonical_name}-*.dist-info.*_omnipkg_cloaked*",
            f"{canonical_name}-*.egg-info.*_omnipkg_cloaked*",
            f"{canonical_name}.py.*_omnipkg_cloaked*"
        ]
        
        for pattern in patterns:
            for cloaked_path in self.site_packages_root.glob(pattern):
                # NEW: Extract timestamp AND loader_id
                match = re.search(r'\.(\d+)_(\d+)_omnipkg_cloaked', str(cloaked_path))
                if match:
                    timestamp = int(match.group(1))
                    loader_id = int(match.group(2))
                    original_name = re.sub(r'\.\d+_\d+_omnipkg_cloaked.*$', '', cloaked_path.name)
                    cloaked_versions.append((cloaked_path, original_name, timestamp, loader_id))
                else:
                    # OLD format fallback (legacy cloaks without loader_id)
                    match_old = re.search(r'\.(\d+)_omnipkg_cloaked', str(cloaked_path))
                    if match_old:
                        timestamp = int(match_old.group(1))
                        original_name = re.sub(r'\.\d+_omnipkg_cloaked.*$', '', cloaked_path.name)
                        cloaked_versions.append((cloaked_path, original_name, timestamp, None))

        return cloaked_versions

    def _cleanup_all_cloaks_for_package(self, pkg_name: str):
        """
        Emergency cleanup with loader-awareness.
        """
        cloaked_versions = self._scan_for_cloaked_versions(pkg_name)
        
        if not cloaked_versions:
            return
        
        if not self.quiet:
            safe_print(f"   🧹 EMERGENCY CLEANUP: Found {len(cloaked_versions)} orphaned cloaks for {pkg_name}")
        
        # NEW: Separate our cloaks from others
        my_loader_id = id(self)
        my_cloaks = [c for c in cloaked_versions if len(c) > 3 and c[3] == my_loader_id]
        other_cloaks = [c for c in cloaked_versions if c not in my_cloaks]
        
        # Strategy: Restore OUR cloak first (if we have one), otherwise newest
        cloaks_to_try = my_cloaks if my_cloaks else other_cloaks
        
        if not cloaks_to_try:
            cloaks_to_try = cloaked_versions
        
        cloaks_to_try.sort(key=lambda x: x[2], reverse=True)
        
        # Try to restore the best candidate
        for cloak_info in cloaks_to_try:
            cloak_path = cloak_info[0]
            original_name = cloak_info[1]
            
            if not cloak_path.exists():
                continue
            
            target_path = cloak_path.parent / original_name
            
            try:
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path)
                    else:
                        target_path.unlink()
                
                shutil.move(str(cloak_path), str(target_path))
                if not self.quiet:
                    safe_print(f"   ✅ Restored: {original_name}")
                
                # Success! Now delete all other cloaks
                for other_cloak_info in cloaked_versions:
                    other_path = other_cloak_info[0]
                    if other_path != cloak_path and other_path.exists():
                        try:
                            if other_path.is_dir():
                                shutil.rmtree(other_path)
                            else:
                                other_path.unlink()
                            if not self.quiet:
                                safe_print(f"   🗑️  Removed old cloak: {other_path.name}")
                        except Exception:
                            pass
                
                return  # Successfully cleaned up!
                
            except Exception as e:
                if not self.quiet:
                    safe_print(f"   ⚠️  Failed to restore {cloak_path.name}: {e}")
                continue
        
        if not self.quiet:
            safe_print(f"   ❌ All restoration attempts failed for {pkg_name}")

    def _get_version_from_original_env(self, package_name: str, requested_version: str) -> tuple:
        """
        Enhanced detection that ALWAYS checks for cloaked versions first.
        CRITICAL FIX: Strictly checks self.site_packages_root to avoid confusion
        from parent loaders' bubbles in sys.path.
        """
        from packaging.utils import canonicalize_name
        
        canonical_target = canonicalize_name(package_name)
        filesystem_name = package_name.replace('-', '_')
        
        # FIX: Do not rely on self.original_sys_path which might be polluted by parent loaders
        site_packages = self.site_packages_root
        
        if not self.quiet:
            safe_print(f"      🔍 Searching for {package_name}=={requested_version}...")
        
        # ═══════════════════════════════════════════════════════════
        # STRATEGY 0: CHECK FOR CLOAKED VERSIONS FIRST (CRITICAL!)
        # ═══════════════════════════════════════════════════════════
        cloaked_versions = self._scan_for_cloaked_versions(package_name)
        
        for cloaked_path, original_name, *_ in cloaked_versions:
            if requested_version in original_name:
                if not self.quiet:
                    safe_print(f"      [Strategy 0/6] Found CLOAKED version: {cloaked_path.name}")
                return (requested_version, cloaked_path)
        
        # ═══════════════════════════════════════════════════════════
        # STRATEGY 1: Direct path check (exact dist-info match)
        # ═══════════════════════════════════════════════════════════
        exact_dist_info_path = site_packages / f"{filesystem_name}-{requested_version}.dist-info"
        if exact_dist_info_path.exists() and exact_dist_info_path.is_dir():
            if not self.quiet:
                safe_print(f"      ✅ [Strategy 1/6] Found at exact path: {exact_dist_info_path}")
            return (requested_version, None)
        
        # ═══════════════════════════════════════════════════════════
        # STRATEGY 2: importlib.metadata (Strictly scoped to main site-packages)
        # ═══════════════════════════════════════════════════════════
        try:
            # FIX: Only pass the main site-packages path
            for dist in importlib.metadata.distributions(path=[str(site_packages)]):
                if canonicalize_name(dist.name) == canonical_target:
                    if dist.version == requested_version:
                        if not self.quiet:
                            safe_print(f"      ✅ [Strategy 2/6] Found via importlib.metadata: {dist.version}")
                        return (dist.version, None)
                    else:
                        if not self.quiet:
                            safe_print(f"      ℹ️  [Strategy 2/6] Found {package_name} but version mismatch: {dist.version} != {requested_version}")
        except Exception as e:
            if not self.quiet:
                safe_print(f"      ⚠️  [Strategy 2/6] importlib.metadata failed: {e}")
        
        # ═══════════════════════════════════════════════════════════
        # STRATEGY 3: Glob search
        # ═══════════════════════════════════════════════════════════
        glob_pattern = f"{filesystem_name}-*.dist-info"
        for match in site_packages.glob(glob_pattern):
            if match.is_dir():
                try:
                    version_part = match.name.replace(f"{filesystem_name}-", "").replace(".dist-info", "")
                    if version_part == requested_version:
                        if not self.quiet:
                            safe_print(f"      ✅ [Strategy 3/6] Found via glob: {match}")
                        return (requested_version, None)
                except Exception:
                    continue
        
        # All strategies exhausted
        if not self.quiet:
            safe_print(f"      ❌ All strategies exhausted. {package_name}=={requested_version} not found.")
            if cloaked_versions:
                safe_print(f"      ⚠️  WARNING: Found {len(cloaked_versions)} cloaked versions but none match {requested_version}")
                safe_print(f"      💡 Running emergency cleanup...")
                self._cleanup_all_cloaks_for_package(package_name)
        
        return (None, None)
    
    def _uncloak_main_package_if_needed(self, pkg_name: str, cloaked_dist_path: Path):
        """
        Restores a cloaked package in the main environment so it can be used.
        Critical for recovering from interrupted sessions or race conditions.
        """
        restored_any = False
        
        # Helper to clean up the destination and move
        def safe_restore(source: Path, dest: Path):
            nonlocal restored_any
            try:
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(source), str(dest))
                restored_any = True
                return True
            except Exception as e:
                if not self.quiet:
                    safe_print(f"      ⚠️ Failed to restore {source.name}: {e}")
                return False

        # 1. Restore the dist-info we found
        if cloaked_dist_path and cloaked_dist_path.exists():
            # Extract original name by stripping the suffix
            # Suffix is like: .1764124481278_omnipkg_cloaked
            original_name = re.sub(r'\.\d+_omnipkg_cloaked.*$', '', cloaked_dist_path.name)
            target_path = cloaked_dist_path.with_name(original_name)
            if safe_restore(cloaked_dist_path, target_path):
                pass

        # 2. Search for cloaked module directories/files
        # We check both the raw package name and the canonical name
        names_to_check = {pkg_name, pkg_name.lower().replace('-', '_')}
        
        for name in names_to_check:
            # Glob for any cloaked items matching this package name
            # The pattern matches "numpy.12345_omnipkg_cloaked"
            for cloaked_item in self.site_packages_root.glob(f"{name}.*_omnipkg_cloaked*"):
                original_name = re.sub(r'\.\d+_omnipkg_cloaked.*$', '', cloaked_item.name)
                target_item = cloaked_item.with_name(original_name)
                
                # Verify this cloak actually belongs to the package (simple name check)
                if original_name == name:
                    safe_restore(cloaked_item, target_item)

        if restored_any and not self.quiet:
            safe_print(f"      ✅ Restored cloaked '{pkg_name}' in main environment")
    
    def _should_use_worker_proactively(self, pkg_name: str) -> bool:
        """
        Decide if we should proactively use worker mode for this package.
        """
        # 1. Check if C++ backend already loaded in memory (Existing logic)
        cpp_indicators = {
            'torch': 'torch._C',
            'numpy': 'numpy.core._multiarray_umath',
            'tensorflow': 'tensorflow.python.pywrap_tensorflow',
            'scipy': 'scipy.linalg._fblas',
        }
        
        for pkg, indicator in cpp_indicators.items():
            if pkg in pkg_name.lower() and indicator in sys.modules:
                if not self.quiet:
                    safe_print(f"   🧠 Proactive worker mode: {indicator} already loaded")
                return True
        
        # 2. FORCE WORKER for these packages to ensure Daemon usage
        #    (Add numpy and scipy here to force isolation testing)
        force_daemon_packages = ['tensorflow', 'numpy', 'scipy', 'pandas']
        
        for force_pkg in force_daemon_packages:
            if force_pkg in pkg_name.lower():
                if not self.quiet:
                    safe_print(f"   🧠 Proactive worker mode: Force-enabling Daemon for {force_pkg}")
                return True
        
        return False
     
    def _get_daemon_client(self):
        """
        Attempts to connect to the daemon. If not running, starts it.
        """
        if not DAEMON_AVAILABLE:
            raise RuntimeError("Worker Daemon code missing (omnipkg.isolation.worker_daemon)")

        client = DaemonClient()
        
        # 1. Try simple status check to see if it's alive
        status = client.status()
        if status.get('success'):
            return client
            
        # 2. Daemon not running? Start it!
        if not self.quiet:
            safe_print("   ⚙️  Worker Daemon not running. Auto-starting background service...")

        # Launch independent process using the CLI command
        subprocess.Popen(
            [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
        
        # 3. Wait for warmup (up to 3 seconds)
        for i in range(30):
            time.sleep(0.1)
            status = client.status()
            if status.get('success'):
                if not self.quiet:
                    safe_print("   ✅ Daemon warmed up and ready.")
                return client
                
        raise RuntimeError("Failed to auto-start Worker Daemon")

    def _activate_bubble(self, bubble_path, pkg_name):
        """
        Activate a bubble with proper tracking of what we cloak.
        CRITICAL: Only cloak packages that CONFLICT, not all dependencies.
        """
        try:
            
            bubble_deps = self._get_bubble_dependencies(bubble_path)
            
            # ═══════════════════════════════════════════════════════════
            # CRITICAL FIX: Detect torch conflicts BEFORE setting dependencies
            # ═══════════════════════════════════════════════════════════
            skip_torch_validation = False
            torch_related_packages = ['torch', 'triton', 'nvidia_cudnn_cu12',
                                    'nvidia_nvtx_cu12', 'nvidia_cusparse_cu12',
                                    'nvidia_nccl_cu12', 'nvidia_nvjitlink_cu12',
                                    'nvidia_cuda_nvrtc_cu12', 'nvidia_cuda_runtime_cu12',
                                    'nvidia_cufft_cu12', 'nvidia_cusolver_cu12',
                                    'nvidia_cublas_cu12', 'nvidia_cuda_cupti_cu12',
                                    'nvidia_curand_cu12']
            
            # Check if we're activating a bubble with torch when torch is already loaded
            if 'torch' in bubble_deps and 'torch._C' in sys.modules:
                try:
                    current_torch_ver = sys.modules['torch'].__version__
                    bubble_torch_ver = bubble_deps['torch']
    
                    if current_torch_ver != bubble_torch_ver:
                        if not self.quiet:
                            safe_print(f"   ⚠️  PyTorch C++ backend already loaded!")
                            safe_print(f"      Active: torch {current_torch_ver}")
                            safe_print(f"      Bubble has: torch {bubble_torch_ver}")
                            safe_print(f"   🔧 Skipping bubble's torch to prevent C++ collision")
                        
                        # CRITICAL: Remove torch from bubble_deps BEFORE tracking
                        bubble_deps = {k: v for k, v in bubble_deps.items()
                                    if k not in torch_related_packages}
                        skip_torch_validation = True
                except (AttributeError, KeyError):
                    pass
            
            # NOW set the dependencies (after torch removal)
            self._activated_bubble_dependencies = list(bubble_deps.keys())
            
            # Determine which packages actually conflict
            main_env_versions = {}
            for pkg in self._activated_bubble_dependencies:
                try:
                    main_version = get_version(pkg)
                    main_env_versions[pkg] = main_version
                except PackageNotFoundError:
                    pass
            
            packages_to_cloak = []
            for pkg, bubble_version in bubble_deps.items():
                if pkg in main_env_versions:
                    main_version = main_env_versions[pkg]
                    if main_version != bubble_version:
                        packages_to_cloak.append(pkg)
                        if not self.quiet:
                            safe_print(f"   ⚠️ Version conflict: {pkg} (main: {main_version} vs bubble: {bubble_version})")
    
            if not self.quiet:
                safe_print(f"   📊 Bubble has {len(bubble_deps)} packages, {len(packages_to_cloak)} conflict with main env")
    
            # Aggressively exorcise the conflicting modules from memory *before* cloaking
            for pkg in packages_to_cloak:
                self._aggressive_module_cleanup(pkg)
    
            self._packages_we_cloaked.update(packages_to_cloak)
            cloaked_count = self._batch_cloak_packages(packages_to_cloak)
            
            if not self.quiet and cloaked_count > 0:
                safe_print(f"   🔒 Cloaked {cloaked_count} conflicting packages")
            
            # Setup paths
            bubble_path_str = str(bubble_path)
            if self.isolation_mode == 'overlay':
                if not self.quiet:
                    safe_print("   - 🧬 Activating in OVERLAY mode (merging with main env)")
                
                # 🧠 SMART FIX: AUTO-RESTORE MAIN ENV
                # If we are in a worker that stripped the path, but we requested overlay,
                # we need to put the main site-packages back so we can see tools like scipy.
                if self._true_site_packages:
                    true_site_str = str(self._true_site_packages)
                    if true_site_str not in sys.path:
                        if not self.quiet:
                            safe_print(f"   🔧 Auto-restoring main site-packages visibility: {self._true_site_packages}")
                        sys.path.append(true_site_str)

                sys.path.insert(0, bubble_path_str)
            else:
                bubble_bin_path = bubble_path / 'bin'
                if bubble_bin_path.is_dir():
                    os.environ['PATH'] = f'{str(bubble_bin_path)}{os.pathsep}{self.original_path_env}'
        
                # sys.path setup
                if self.isolation_mode == 'overlay':
                    if not self.quiet:
                        safe_print("   - 🧬 Activating in OVERLAY mode (merging with main env)")
                    sys.path.insert(0, bubble_path_str)
                else:
                    if not self.quiet:
                        safe_print("   - 🔒 Activating in STRICT mode (isolating from main env)")
                    new_sys_path = [bubble_path_str] + [p for p in self.original_sys_path                                                    if not self._is_main_site_packages(p)]
                    sys.path[:] = new_sys_path
        
                self._ensure_omnipkg_access_in_bubble(bubble_path_str)
                self._activated_bubble_path = bubble_path_str
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                
                if not self.quiet:
                    safe_print(f"   ⚡ HEALED in {self._total_activation_time_ns / 1000:,.1f} μs")
                
                # ═══════════════════════════════════════════════════════════
                # CRITICAL: TensorFlow MUST be validated in subprocess only
                # ═══════════════════════════════════════════════════════════
                if pkg_name == 'tensorflow':
                    if not self.quiet:
                        safe_print(f"   ⚠️  TensorFlow detected - skipping in-process validation")
                        safe_print(f"   ℹ️  Will validate in clean subprocess only")
                    
                    if self._is_bubble_healthy_in_subprocess(pkg_name, bubble_path_str):
                        if not self.quiet:
                            safe_print(f"   ✅ TensorFlow validated successfully in clean process")
                        self._activation_successful = True
                        return self
                    else:
                        if not self.quiet:
                            safe_print(f"   ❌ TensorFlow validation failed in subprocess")
                        self._panic_restore_cloaks()
                        raise RuntimeError(f"TensorFlow bubble failed subprocess validation")
                
                # ═══════════════════════════════════════════════════════════
                # CRITICAL: PyTorch C++ reload handling
                # ═══════════════════════════════════════════════════════════
                if skip_torch_validation or (pkg_name == 'torch' and 'torch._C' in sys.modules):
                    if not self.quiet:
                        safe_print(f"   ⚠️  PyTorch C++ reload limitation detected (non-fatal)")
                        safe_print(f"   ℹ️  Bubble is functional, validation skipped")
                    self._activation_successful = True
                    return self
                
                # ═══════════════════════════════════════════════════════════
                # Normal validation for other packages
                # ═══════════════════════════════════════════════════════════
                import_ok = self._validate_import(pkg_name, max_retries=3)
                
                if not import_ok:
                    if self._is_bubble_healthy_in_subprocess(pkg_name, bubble_path_str):
                        if not self.quiet:
                            safe_print(f"   🧪 DIAGNOSIS: Bubble is HEALTHY in a clean process.")
                        raise ProcessCorruptedException(
                            f"Memory corrupted by C++ collision while activating {pkg_name}"
                        )
                    
                    if not self.quiet:
                        safe_print(f"   🏥 Last resort: Force-reinstalling bubble...")
                    
                    healed = self._auto_heal_broken_bubble(pkg_name, bubble_path)
                    
                    if not healed:
                        self._panic_restore_cloaks()
                        raise RuntimeError(f"Bubble activation failed validation: {pkg_name} import broken")
                
                self._activation_successful = True
                return self
        
        except Exception as e:
            safe_print(_('   ❌ Activation failed: {}').format(str(e)))
            self._panic_restore_cloaks()
            raise

    def __enter__(self):
        """
        Enhanced activation with automatic worker fallback.
        
        Three-tier strategy:
        1. Try in-process activation (existing logic) - DEFAULT
        2. Reactive worker fallback on ProcessCorruptedException
        3. Proactive worker mode (ONLY if explicitly requested)
        """
        self._activation_start_time = time.perf_counter_ns()
        
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")
        
        # ═══════════════════════════════════════════════════════════
        # TIER 1: Try In-Process Activation (Your Existing Logic)
        # ═══════════════════════════════════════════════════════════
        try:
            # Check if system version matches
            try:
                current_system_version = get_version(pkg_name)
                
                if current_system_version == requested_version and not self.force_activation:
                    if not self.quiet:
                        safe_print(_('✅ System version already matches requested version ({}). No bubble needed.').format(current_system_version))
                    
                    self._ensure_main_site_packages_in_path() 
                    
                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                    return self
            except PackageNotFoundError:
                pass

            if not self.quiet:
                safe_print(_('🚀 Fast-activating {} ...').format(self._current_package_spec))
            
            bubble_path = self.multiversion_base / f'{pkg_name}-{requested_version}'
            
            if not self.quiet:
                safe_print(f"   📂 Searching for bubble: {bubble_path}")
            
            # Track numpy version if applicable
            is_numpy_involved = 'numpy' in self._current_package_spec.lower()
            
            # PRIORITY 1: Try BUBBLE first
            if bubble_path.is_dir():
                if not self.quiet:
                    safe_print(f"   ✅ Bubble found: {bubble_path}")
                self._using_main_env = False
                
                if is_numpy_involved:
                    with omnipkgLoader._numpy_lock:
                        omnipkgLoader._numpy_version_history.append(requested_version)
                
                return self._activate_bubble(bubble_path, pkg_name)
            
            # PRIORITY 2: Try MAIN ENV
            if not self.quiet:
                safe_print(f"   ⚠️  Bubble not found. Checking main environment...")
            
            found_ver, cloaked_path = self._get_version_from_original_env(pkg_name, requested_version)
            
            if found_ver == requested_version:
                # Handle cloaked versions or use main env directly
                if cloaked_path:
                    if not self.quiet:
                        safe_print(f"   🔓 Found CLOAKED version, restoring to main env...")
                    self._uncloak_main_package_if_needed(pkg_name, cloaked_path)

                    found_ver_after, cloaked_path_after = self._get_version_from_original_env(pkg_name, requested_version)

                    if found_ver_after == requested_version:
                        if not self.quiet:
                            safe_print(f"   🔄 Installer restored {pkg_name} in main env. Switching strategy.")
                        
                        self._ensure_main_site_packages_in_path()
                        self._using_main_env = True
                        
                        pkg_canonical = pkg_name.lower().replace('-', '_')
                        omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                        self._my_main_env_package = pkg_canonical
                        
                        if is_numpy_involved:
                            with omnipkgLoader._numpy_lock:
                                omnipkgLoader._numpy_version_history.append(requested_version)
                        
                        self._activation_successful = True
                        self._activation_end_time = time.perf_counter_ns()
                        self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                        return self
                else:
                    # Found in main env, not cloaked
                    if not self.quiet:
                        safe_print(f"   ✅ Found in main environment")
                    
                    self._ensure_main_site_packages_in_path()
                    self._using_main_env = True
                    
                    pkg_canonical = pkg_name.lower().replace('-', '_')
                    omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                    self._my_main_env_package = pkg_canonical
                    
                    if is_numpy_involved:
                        with omnipkgLoader._numpy_lock:
                            omnipkgLoader._numpy_version_history.append(requested_version)
                    
                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                    return self

            # PRIORITY 3: AUTO-INSTALL BUBBLE
            install_lock = self._get_install_lock(self._current_package_spec)

            if not self.quiet:
                safe_print(f"   - 🛡️  Acquiring install lock for {self._current_package_spec}...")
                
            with install_lock:
                if not self.quiet:
                    safe_print(f"   - ✅ Install lock acquired.")
                
                # Double-check another thread didn't install it
                if bubble_path.is_dir():
                    if not self.quiet:
                        safe_print(f"   - 🏁 Another thread finished the install. Proceeding to activate.")
                    self._using_main_env = False
                    
                    if is_numpy_involved:
                        with omnipkgLoader._numpy_lock:
                            omnipkgLoader._numpy_version_history.append(requested_version)
                    
                    return self._activate_bubble(bubble_path, pkg_name)

                if not self.quiet:
                    safe_print(f"   - 🔧 I am the installer. Auto-creating bubble for: {self._current_package_spec}")
                
                install_success = self._install_bubble_inline(self._current_package_spec)
                
                if not install_success:
                    raise RuntimeError(f"Failed to install {self._current_package_spec}")

                # Post-install check
                if bubble_path.is_dir():
                    if not self.quiet:
                        safe_print(f"   - ✅ Bubble created successfully at: {bubble_path}")
                    self._using_main_env = False
                    
                    if is_numpy_involved:
                        with omnipkgLoader._numpy_lock:
                            omnipkgLoader._numpy_version_history.append(requested_version)
                    
                    return self._activate_bubble(bubble_path, pkg_name)
                else:
                    # Package landed in main environment
                    if not self.quiet:
                        safe_print(f"   - ⚠️  Bubble not created. Package installed to main environment.")
                    
                    found_ver, cloaked_path = self._get_version_from_original_env(pkg_name, requested_version)
                    
                    if found_ver == requested_version:
                        if not self.quiet:
                            safe_print(f"   - ✅ Confirmed {pkg_name}=={requested_version} in main environment")
                        
                        self._using_main_env = True
                        
                        pkg_canonical = pkg_name.lower().replace('-', '_')
                        omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                        self._my_main_env_package = pkg_canonical
                        
                        if is_numpy_involved:
                            with omnipkgLoader._numpy_lock:
                                omnipkgLoader._numpy_version_history.append(requested_version)
                        
                        self._activation_successful = True
                        self._activation_end_time = time.perf_counter_ns()
                        self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                        return self
                    else:
                        raise RuntimeError(
                            f"Installation reported success but {pkg_name}=={requested_version} "
                            f"not found in bubble or main environment. Found version: {found_ver}"
                        )
        
        except ProcessCorruptedException as e:
            # ═══════════════════════════════════════════════════════════
            # TIER 2: Reactive Worker Fallback (ONLY on crash)
            # ═══════════════════════════════════════════════════════════
            if not self._worker_fallback_enabled:
                raise  # Re-raise if worker fallback disabled
            
            if not self.quiet:
                safe_print(f"   🔄 C++ collision detected! Recovering with worker mode...")
                safe_print(f"      Details: {str(e)}")
            
            # Clean up partial activation
            try:
                self._panic_restore_cloaks()
            except Exception:
                pass  # Best-effort cleanup
            
            # Create worker as fallback
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            
            if not self._active_worker:
                raise RuntimeError(
                    f"Both in-process and worker activation failed for "
                    f"{self._current_package_spec}. Original error: {e}"
                )
            
            self._worker_mode = True
            self._activation_successful = True
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = (self._activation_end_time - 
                                            self._activation_start_time)
            
            if not self.quiet:
                safe_print(f"   ✅ Successfully recovered using worker mode!")
                safe_print(f"   ⚡ Total recovery time: "
                        f"{self._total_activation_time_ns / 1000000:.1f} ms")
            
            return self
        
    def _panic_restore_cloaks(self):
        """Emergency cloak restoration when activation fails."""
        if not self.quiet:
            safe_print(_(' 🚨 Emergency cloak restoration in progress...'))
        self._restore_cloaked_modules()
    
    def _install_bubble_inline(self, spec):
        """
        Install a missing bubble directly, inline.
        Returns True if successful, False otherwise.
        """
        start_time = time.perf_counter()
        
        try:
            from omnipkg.core import omnipkg as OmnipkgCore
            from omnipkg.core import ConfigManager
            
            # Create a fresh ConfigManager
            cm = ConfigManager(suppress_init_messages=True)
            
            if hasattr(self, 'config') and isinstance(self.config, dict):
                cm.config.update(self.config)
            
            core = OmnipkgCore(cm)
            
            original_strategy = core.config.get('install_strategy')
            core.config['install_strategy'] = 'stable-main'
            
            try:
                if not self.quiet:
                    safe_print(f"      📦 Installing {spec} with dependencies...")
                
                result = core.smart_install([spec])
                
                if result != 0:
                    if not self.quiet:
                        safe_print(f"      ❌ Installation failed with exit code {result}")
                    return False
                
                elapsed = time.perf_counter() - start_time
                
                if not self.quiet:
                    safe_print(f"      ✅ Bubble created in {elapsed:.1f}s (tested & deps bundled)")
                    safe_print(f"      💡 Future loads will be instant (~100μs)")
                
                # CRITICAL FIX: Force a clean import state after installation
                # The installer may have imported modules that conflict with our context
                importlib.invalidate_caches()
                gc.collect()
                
                return True
                
            finally:
                if original_strategy:
                    core.config['install_strategy'] = original_strategy
        
        except Exception as e:
            if not self.quiet:
                safe_print(f"      ❌ Auto-install exception: {e}")
                import traceback
                safe_print(traceback.format_exc())
            return False

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with worker pool awareness."""
        
        # Worker cleanup path
        if self._worker_mode and self._active_worker:
            if self._worker_from_pool:
                # DON'T shutdown pooled workers - keep them alive!
                if not self.quiet:
                    safe_print(f"   ♻️  Releasing pooled worker (keeping alive)")
                self._active_worker = None
            else:
                # Shutdown temporary workers
                if not self.quiet:
                    safe_print(f"   🛑 Shutting down temporary worker...")
                try:
                    self._active_worker.shutdown()
                except Exception as e:
                    if not self.quiet:
                        safe_print(f"   ⚠️  Worker shutdown warning: {e}")
                finally:
                    self._active_worker = None
            
            self._worker_mode = False
            return  # Early exit
        self._deactivation_start_time = time.perf_counter_ns()
        
        if not self.quiet:
            safe_print(f'🌀 omnipkg loader: Deactivating {self._current_package_spec}...')
        
        if not self._activation_successful:
            return
        
        pkg_name = self._current_package_spec.split('==')[0] if self._current_package_spec else None
        
        # Step 1: Unregister main env package protection
        if self._my_main_env_package:
            omnipkgLoader._active_main_env_packages.discard(self._my_main_env_package)
            if not self.quiet:
                safe_print(f"   - 🔓 Released protection for {self._my_main_env_package}")
        
        # Step 2: Restore cloaked modules (only if we used a bubble)
        if not self._using_main_env:
            if self._cloaked_main_modules:
                if not self.quiet:
                    safe_print(f"   - 🔓 Restoring {len(self._cloaked_main_modules)} cloaked packages...")
                self._restore_cloaked_modules()
            
            # Verify cleanup was successful
            if pkg_name:
                remaining_cloaks = self._scan_for_cloaked_versions(pkg_name)
                if remaining_cloaks:
                    if not self.quiet:
                        safe_print(f"   ⚠️  WARNING: Found {len(remaining_cloaks)} orphaned cloaks after cleanup!")
                        safe_print(f"   🧹 Running emergency cleanup...")
                    self._cleanup_all_cloaks_for_package(pkg_name)
        else:
            if not self.quiet:
                safe_print(f"   - ℹ️  Used main env directly - skipping cloak restoration")
            self._cloaked_main_modules.clear()
        
        # Step 3: Restore environment
        if self.isolation_mode == 'overlay' and self._activated_bubble_path:
            try:
                # Remove the exact path we added
                sys.path.remove(self._activated_bubble_path)
            except ValueError:
                # It might have already been removed by another process, which is fine.
                pass
        else: # The old 'strict' mode cleanup
            os.environ['PATH'] = self.original_path_env
            sys.path[:] = self.original_sys_path
        
        # Step 4: Purge bubble modules (only if we used a bubble)
        if not self._using_main_env and self._activated_bubble_dependencies:
            if not self.quiet:
                safe_print(f"   - 👻 Exorcising {len(self._activated_bubble_dependencies)} bubble modules...")
            
            for pkg_name_dep in self._activated_bubble_dependencies:
                self._aggressive_module_cleanup(pkg_name_dep)
            
            if pkg_name:
                self._aggressive_module_cleanup(pkg_name)
        
        # Step 5: Force cache invalidation
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()
        
        gc.collect()
        
        self._deactivation_end_time = time.perf_counter_ns()
        self._total_deactivation_time_ns = self._deactivation_end_time - self._deactivation_start_time
        total_swap_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        
        if not self.quiet:
            safe_print(f'   ✅ Environment fully restored.')
            safe_print(f'   ⏱️  Total Swap Time: {total_swap_time_ns / 1000:,.3f} μs ({total_swap_time_ns:,} ns)')
            
            # Final verification
            if pkg_name and not self._using_main_env:
                final_cloaks = self._scan_for_cloaked_versions(pkg_name)
                if not final_cloaks:
                    safe_print(f'   ✅ Verified: No orphaned cloaks remaining')
                else:
                    safe_print(f'   ⚠️  WARNING: Still {len(final_cloaks)} cloaks remaining!')

    def _restore_cloaked_modules(self):
        """
        Restore cloaked modules with PROCESS-WIDE SAFETY.
        """
        with omnipkgLoader._global_cloaking_lock:
            restored_count = 0
            failed_count = 0
            
            for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
                if not was_successful:
                    continue
                
                pkg_name = original_path.stem
                lock = self._get_cloak_lock(pkg_name)
                
                try:
                    with lock.acquire(timeout=5):
                        # Check if already restored by another thread
                        if not cloak_path.exists():
                            if original_path.exists():
                                if not self.quiet:
                                    safe_print(f'   ℹ️  Already restored by another loader: {original_path.name}')
                                continue 
                            else:
                                if not self.quiet:
                                    safe_print(f'   ❌ CRITICAL: Cloak missing: {cloak_path.name}')
                                failed_count += 1
                                continue
                        
                        # Remove any existing target first
                        if original_path.exists():
                            try:
                                if original_path.is_dir():
                                    shutil.rmtree(original_path, ignore_errors=True)
                                else:
                                    original_path.unlink()
                            except Exception as e:
                                if not self.quiet:
                                    safe_print(f'   ⚠️  Could not remove conflicting path {original_path.name}: {e}')
                        
                        # THREAD-SAFE: Move with lock held
                        shutil.move(str(cloak_path), str(original_path))
                        restored_count += 1
                        if not self.quiet:
                            safe_print(f'   ✅ Restored: {original_path.name}')
                            
                except filelock.Timeout:
                    if not self.quiet:
                        safe_print(f'   ⏱️  Timeout waiting for lock on {pkg_name}, skipping restore...')
                    failed_count += 1
                except Exception as e:
                    if not self.quiet:
                        safe_print(f'   ❌ Failed to restore {original_path.name}: {e}')
                    failed_count += 1
            
            self._cloaked_main_modules.clear()
            
            if not self.quiet and (restored_count > 0 or failed_count > 0):
                safe_print(f'   📊 Restoration: {restored_count} restored, {failed_count} failed')

    def _find_cloaked_versions(self, pkg_name):
        """
        Find cloaked versions of a package in the environment.
        """
        cloaked_versions = []
        site_packages = Path(self.site_packages_path)
        
        # Look for cloaked files/directories
        for cloaked_path in site_packages.glob(f"*{pkg_name}*_omnipkg_cloaked*"):
            if "_omnipkg_cloaked" in cloaked_path.name:
                # Extract original name and timestamp
                name_parts = cloaked_path.name.split('_omnipkg_cloaked')
                if len(name_parts) >= 1:
                    original_name = name_parts[0]
                    timestamp = name_parts[1] if len(name_parts) > 1 else "unknown"
                    cloaked_versions.append((cloaked_path, original_name, timestamp))
        
        if cloaked_versions and not self.quiet:
            safe_print(f"   🔍 Found {len(cloaked_versions)} cloaked version(s) of {pkg_name}:")
            for cloak_path, orig_name, ts in cloaked_versions:
                safe_print(f"      - {cloak_path.name} (timestamp: {ts})")
        
        return cloaked_versions

    def _cleanup_omnipkg_links_in_bubble(self, bubble_path_str: str):
        """
        Clean up symlinks created for omnipkg dependencies in the bubble.
        """
        bubble_path = Path(bubble_path_str)
        for dep_name in self._omnipkg_dependencies.keys():
            bubble_dep_path = bubble_path / dep_name
            if bubble_dep_path.is_symlink():
                try:
                    bubble_dep_path.unlink()
                except Exception:
                    pass

    def debug_version_compatibility(self):
        """Debug helper to check version compatibility of current paths."""
        safe_print(_('\n🔍 DEBUG: Python Version Compatibility Check'))
        safe_print(_('Current Python version: {}').format(self.python_version))
        safe_print(_('Site-packages root: {}').format(self.site_packages_root))
        safe_print(_('Compatible: {}').format(self._is_version_compatible_path(self.site_packages_root)))
        safe_print(_('\n🔍 Current sys.path compatibility ({} entries):').format(len(sys.path)))
        compatible_count = 0
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            is_compatible = self._is_version_compatible_path(path_obj)
            exists = path_obj.exists()
            status = '✅' if exists and is_compatible else '🚫' if exists else '❌'
            if is_compatible and exists:
                compatible_count += 1
            safe_print(_('   [{}] {} {}').format(i, status, path))
        safe_print(_('\n📊 Summary: {}/{} paths are Python {}-compatible').format(compatible_count, len(sys.path), self.python_version))
        safe_print()

    def get_performance_stats(self):
        """Returns detailed performance statistics for CI/logging purposes."""
        if self._total_activation_time_ns is None or self._total_deactivation_time_ns is None:
            return None
        total_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        return {'package_spec': self._current_package_spec, 'python_version': self.python_version, 'activation_time_ns': self._total_activation_time_ns, 'activation_time_us': self._total_activation_time_ns / 1000, 'activation_time_ms': self._total_activation_time_ns / 1000000, 'deactivation_time_ns': self._total_deactivation_time_ns, 'deactivation_time_us': self._total_deactivation_time_ns / 1000, 'deactivation_time_ms': self._total_deactivation_time_ns / 1000000, 'total_swap_time_ns': total_time_ns, 'total_swap_time_us': total_time_ns / 1000, 'total_swap_time_ms': total_time_ns / 1000000, 'swap_speed_description': self._get_speed_description(total_time_ns)}

    def _get_speed_description(self, time_ns):
        """Returns a human-readable description of swap speed."""
        if time_ns < 1000:
            return f'Ultra-fast ({time_ns} nanoseconds)'
        elif time_ns < 1000000:
            return f'Lightning-fast ({time_ns / 1000:.1f} microseconds)'
        elif time_ns < 1000000000:
            return f'Very fast ({time_ns / 1000000:.1f} milliseconds)'
        else:
            return f'Standard ({time_ns / 1000000000:.2f} seconds)'

    def print_ci_performance_summary(self):
        """Prints a CI-friendly performance summary focused on healing success."""
        safe_print('\n' + '=' * 70)
        safe_print('🚀 EXECUTION ANALYSIS: Standard Runner vs. Omnipkg Auto-Healing')
        safe_print('=' * 70)

        loader_stats = self.get_performance_stats()


        uv_failed_ms = uv_failure_detector.get_execution_time_ms()
        
        omnipkg_heal_and_run_ms = loader_stats.get('total_swap_time_ms', 0) if loader_stats else 0
        
        total_omnipkg_time_ms = uv_failed_ms + omnipkg_heal_and_run_ms

        safe_print(f"  - Standard Runner (uv):   [ FAILED ] at {uv_failed_ms:>8.3f} ms")
        safe_print(f"  - Omnipkg Healing & Run:  [ SUCCESS ] in {omnipkg_heal_and_run_ms:>8.3f} ms")
        safe_print('-' * 70)
        safe_print(f"  - Total Time to Success via Omnipkg: {total_omnipkg_time_ms:>8.3f} ms")
        safe_print('=' * 70)
        safe_print("🌟 Verdict:")
        safe_print("   A standard runner fails instantly. Omnipkg absorbs the failure,")
        safe_print("   heals the environment in microseconds, and completes the job.")
        safe_print('=' * 70)

    def _get_package_modules(self, pkg_name: str):
        """Helper to find all modules related to a package in sys.modules."""
        pkg_name_normalized = pkg_name.replace('-', '_')
        return [mod for mod in list(sys.modules.keys()) if mod.startswith(pkg_name_normalized + '.') or mod == pkg_name_normalized or mod.replace('_', '-').startswith(pkg_name.lower())]

    def _aggressive_module_cleanup(self, pkg_name: str):
        """
        Removes specified package's modules from sys.modules.
        Special handling for torch which cannot be fully cleaned.
        
        TWO STRATEGIES:
        1. If torch._C is loaded: Preserve core torch modules (torch, torch._C, torch.nn, etc.)
        but clean utility modules (torch.utils, torch.testing, etc.)
        2. Otherwise: Normal aggressive cleanup
        """
        pkg_name_normalized = pkg_name.replace('-', '_')

        # ═══════════════════════════════════════════════════════════
        # SPECIAL: Surgical torch cleanup when C++ backend is loaded
        # ═══════════════════════════════════════════════════════════
        if pkg_name == 'torch' and 'torch._C' in sys.modules:
            if not self.quiet:
                safe_print(f"      ℹ️  Preserving torch._C (C++ backend cannot be unloaded)")
            
            # Core modules that must be preserved
            core_modules = {
                'torch',           # Top-level module
                'torch._C',        # C++ backend
                'torch.nn',        # Neural network core
                'torch.autograd',  # Automatic differentiation
                'torch.cuda',      # CUDA support
                'torch.jit',       # JIT compiler
                'torch.onnx',      # ONNX support
            }
            
            # Get all torch modules
            all_torch_modules = [
                mod for mod in list(sys.modules.keys())
                if mod.startswith('torch')
            ]
            
            # Separate into core and cleanable
            modules_to_preserve = []
            modules_to_clean = []
            
            for mod_name in all_torch_modules:
                # Check if this module or any parent is in core_modules
                is_core = any(
                    mod_name == core or mod_name.startswith(core + '.')
                    for core in core_modules
                )
                
                if is_core:
                    modules_to_preserve.append(mod_name)
                else:
                    modules_to_clean.append(mod_name)
            
            # Clean non-core modules
            if modules_to_clean:
                if not self.quiet:
                    safe_print(f"      - Purging {len(modules_to_clean)} non-core torch modules")
                    safe_print(f"      - Preserving {len(modules_to_preserve)} core torch modules")
                
                for mod_name in modules_to_clean:
                    if mod_name in sys.modules:
                        del sys.modules[mod_name]
            else:
                if not self.quiet:
                    safe_print(f"      ℹ️  All torch modules are core, preserving all {len(modules_to_preserve)}")
            
            gc.collect()
            if hasattr(importlib, 'invalidate_caches'):
                importlib.invalidate_caches()
            
            return

        # ═══════════════════════════════════════════════════════════
        # Normal cleanup for other packages
        # ═══════════════════════════════════════════════════════════
        modules_to_clear = self._get_package_modules(pkg_name)

        if modules_to_clear:
            if not self.quiet:
                safe_print(f"      - Purging {len(modules_to_clear)} modules for '{pkg_name_normalized}'")
            for mod_name in modules_to_clear:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

        gc.collect()
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()

    def _cloak_main_package(self, pkg_name: str):
        """Temporarily renames the main environment installation of a package."""
        canonical_pkg_name = pkg_name.lower().replace('-', '_')
        paths_to_check = [self.site_packages_root / canonical_pkg_name, next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.dist-info'), None), next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.egg-info'), None), self.site_packages_root / f'{canonical_pkg_name}.py']
        for original_path in paths_to_check:
            if original_path and original_path.exists():
                timestamp = int(time.time() * 1000)
                if original_path.is_dir():
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked')
                else:
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked{original_path.suffix}')
                cloak_record = (original_path, cloak_path, False)
                if cloak_path.exists():
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                    except Exception as e:
                        if not self.quiet:
                            safe_print(_(' ⚠️ Warning: Could not remove existing cloak {}: {}').format(cloak_path.name, e))
                try:
                    shutil.move(str(original_path), str(cloak_path))
                    cloak_record = (original_path, cloak_path, True)
                except Exception as e:
                    if not self.quiet:
                        safe_print(_(' ⚠️ Failed to cloak {}: {}').format(original_path.name, e))
                self._cloaked_main_modules.append(cloak_record)

    def cleanup_abandoned_cloaks(self):
        """
        Utility method to clean up any abandoned cloak files.
        Can be called manually if you suspect there are leftover cloaks.
        """
        safe_print(_('🧹 Scanning for abandoned omnipkg cloaks...'))
        cloak_pattern = '*_omnipkg_cloaked*'
        found_cloaks = list(self.site_packages_root.glob(cloak_pattern))
        if not found_cloaks:
            safe_print(_(' ✅ No abandoned cloaks found.'))
            return
        safe_print(_(' 🔍 Found {} potential abandoned cloak(s):').format(len(found_cloaks)))
        for cloak_path in found_cloaks:
            safe_print(_('   - {}').format(cloak_path.name))
        safe_print(_(' ℹ️ To remove these manually: rm -rf /path/to/site-packages/*_omnipkg_cloaked*'))
        safe_print(_(" ⚠️ WARNING: Only remove if you're sure no omnipkg operations are running!"))

    def debug_sys_path(self):
        """Debug helper to print current sys.path state."""
        safe_print(_('\n🔍 DEBUG: Current sys.path ({} entries):').format(len(sys.path)))
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            status = '✅' if path_obj.exists() else '❌'
            safe_print(_('   [{}] {} {}').format(i, status, path))
        safe_print()

    def debug_omnipkg_dependencies(self):
        """Debug helper to show detected omnipkg dependencies."""
        safe_print(_('\n🔍 DEBUG: Detected omnipkg dependencies:'))
        if not self._omnipkg_dependencies:
            safe_print(_('   ❌ No dependencies detected'))
            return
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            status = '✅' if dep_path.exists() else '❌'
            safe_print(_('   {} {}: {}').format(status, dep_name, dep_path))
        safe_print()

    def _get_import_name_for_package(self, pkg_name: str) -> str:
        """
        Get the actual import name for a package by reading top_level.txt.
        Falls back to name transformations if not found.
        
        Examples:
            - "scikit-learn" -> "sklearn"
            - "pillow" -> "PIL"
            - "beautifulsoup4" -> "bs4"
        """
        # Common known mappings (fallback if dist-info lookup fails)
        known_mappings = {
            'scikit-learn': 'sklearn',
            'pillow': 'PIL',
            'beautifulsoup4': 'bs4',
            'opencv-python': 'cv2',
            'python-dateutil': 'dateutil',
            'attrs': 'attr',
            'pyyaml': 'yaml',
            'protobuf': 'google.protobuf',
        }
        
        # Try to find the import name from dist-info
        search_paths = []
        
        # Add bubble path if activated
        if self._activated_bubble_path:
            search_paths.append(Path(self._activated_bubble_path))
        
        # Also check sys.path directories for dist-info
        for path_str in sys.path:
            if 'site-packages' in path_str:
                path = Path(path_str)
                if path.exists() and path not in search_paths:
                    search_paths.append(path)
        
        for search_path in search_paths:
            if not search_path.exists():
                continue
            
            # Normalize package name for matching (lowercase, replace - with _)
            normalized_pkg = pkg_name.lower().replace('-', '_')
            
            # Try multiple glob patterns to catch different naming schemes
            patterns = [
                f"{pkg_name}-*.dist-info",           # Exact match with version
                f"{pkg_name.replace('-', '_')}-*.dist-info",  # Underscore variant
                f"*{normalized_pkg}*.dist-info",     # Fuzzy match (last resort)
            ]
            
            for pattern in patterns:
                for dist_info in search_path.glob(pattern):
                    # Verify this is actually a dist-info directory
                    if not dist_info.is_dir():
                        continue
                    
                    top_level_file = dist_info / 'top_level.txt'
                    if top_level_file.exists():
                        try:
                            content = top_level_file.read_text(encoding='utf-8').strip()
                            if content:
                                # Return the first import name (most packages have only one)
                                import_name = content.split('\n')[0].strip()
                                if import_name:
                                    if not self.quiet:
                                        safe_print(f"      📦 Resolved import name: {pkg_name} -> {import_name}")
                                    return import_name
                        except Exception as e:
                            if not self.quiet:
                                safe_print(f"      ⚠️  Failed to read {top_level_file}: {e}")
                            continue
                    
                    # If top_level.txt doesn't exist, try RECORD file
                    record_file = dist_info / 'RECORD'
                    if record_file.exists():
                        try:
                            import_name = self._extract_import_from_record(record_file)
                            if import_name:
                                if not self.quiet:
                                    safe_print(f"      📦 Resolved import name from RECORD: {pkg_name} -> {import_name}")
                                return import_name
                        except Exception:
                            continue
        
        # Check known mappings
        if pkg_name.lower() in known_mappings:
            import_name = known_mappings[pkg_name.lower()]
            if not self.quiet:
                safe_print(f"      📦 Using known mapping: {pkg_name} -> {import_name}")
            return import_name
        
        # Last resort: transform package name
        # Replace hyphens with underscores (common convention)
        transformed = pkg_name.replace('-', '_').lower()
        
        if not self.quiet and transformed != pkg_name:
            safe_print(f"      📦 Using transformed name: {pkg_name} -> {transformed}")
        
        return transformed

    def _extract_import_from_record(self, record_file: Path) -> str:
        """
        Extract the import name by finding the most common top-level directory
        in the RECORD file (excluding common non-package directories).
        """
        try:
            content = record_file.read_text(encoding='utf-8')
            
            # Count occurrences of top-level directories
            from collections import Counter
            top_level_dirs = Counter()
            
            for line in content.splitlines():
                if not line.strip():
                    continue
                
                # RECORD format: filename,hash,size
                parts = line.split(',')
                if not parts:
                    continue
                
                filepath = parts[0]
                
                # Skip metadata and common non-package files
                if any(skip in filepath for skip in [
                    '.dist-info/', '__pycache__/', '.pyc', 
                    '../', 'bin/', 'scripts/'
                ]):
                    continue
                
                # Extract top-level directory
                path_parts = filepath.split('/')
                if path_parts and path_parts[0]:
                    # Skip if it's a direct file (no directory)
                    if len(path_parts) > 1:
                        top_level = path_parts[0]
                        # Must be a valid Python identifier
                        if top_level.replace('_', '').replace('.', '').isalnum():
                            top_level_dirs[top_level] += 1
            
            # Return the most common top-level directory
            if top_level_dirs:
                most_common = top_level_dirs.most_common(1)[0][0]
                return most_common
        
        except Exception:
            pass
        
        return None

    def _validate_import(self, pkg_name: str, max_retries: int = 3) -> bool:
        """
        Validate that a package can actually be imported after activation.
        Special handling for PyTorch which cannot be reloaded.
        """
        # Get the actual import name (e.g., "sklearn" for "scikit-learn")
        import_name = self._get_import_name_for_package(pkg_name)
        
        # SPECIAL CASE: PyTorch cannot be reloaded once C++ backend is loaded
        if pkg_name == 'torch':
            if 'torch._C' in sys.modules:
                if not self.quiet:
                    safe_print(f"      ℹ️  PyTorch C++ backend already loaded - reusing existing instance")
                
                # Check if the torch module itself is accessible
                if 'torch' in sys.modules:
                    try:
                        # Verify it's functional
                        torch = sys.modules['torch']
                        _ = torch.__version__  # Quick sanity check
                        return True
                    except Exception:
                        pass
                
                # If we get here, torch._C is loaded but torch module is missing
                # This is the problematic state - we need to skip validation
                if not self.quiet:
                    safe_print(f"      ⚠️  PyTorch in partial state - skipping validation (known limitation)")
                return True  # Allow activation but warn user
        
        # Normal validation for other packages
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    if not self.quiet:
                        safe_print(f"      🔄 Import retry {attempt}/{max_retries} after cache clear...")
                    
                    # AGGRESSIVE CACHE CLEARING
                    importlib.invalidate_caches()
                    self._clear_pycache_for_package(import_name)
                    self._aggressive_module_cleanup(import_name)
                    gc.collect()
                    time.sleep(0.01 * attempt)
                
                # Try import with correct name
                module = __import__(import_name)
                
                # 2. RUN THE BRAIN CHECK
                if not self._perform_sanity_check(pkg_name):
                    raise ImportError(f"Package {pkg_name} imported but failed sanity check (Zombie State detected!)")
                
                if attempt > 0 and not self.quiet:
                    safe_print(f"      ✅ Import & Sanity Check succeeded after {attempt} retries!")
                
                return True
                
            except Exception as e:
                error_str = str(e)
                
                # SPECIAL: PyTorch docstring error = known limitation, not fatal
                if 'already has a docstring' in error_str or '_has_torch_function' in error_str:
                    if not self.quiet:
                        safe_print(f"      ⚠️  PyTorch C++ reload limitation detected (non-fatal)")
                        safe_print(f"      ℹ️  Bubble is functional, validation skipped")
                    return True  # Treat as success
                
                if attempt == max_retries - 1:
                    if not self.quiet:
                        safe_print(f"      ❌ Import validation failed after {max_retries} attempts: {e}")
                    return False
                else:
                    if not self.quiet:
                        error_snippet = str(e).split('\n')[0][:80]
                        safe_print(f"      ⚠️  Attempt {attempt + 1} failed: {error_snippet}")
                    continue
        
        return False

    def _perform_sanity_check(self, pkg_name: str) -> bool:
        """
        Runs a quick functional test.
        Importing isn't enough - we need to verify the C++ backend is alive.
        """
        try:
            if pkg_name == 'tensorflow':
                import tensorflow as tf
                with tf.device('/cpu:0'):
                    result = tf.constant(1)
                    
            elif pkg_name == 'torch':
                import torch
                # CRITICAL FIX: Skip NumPy initialization if we're also switching numpy
                # Check if numpy is in an uncertain state
                try:
                    import numpy as np
                    # If numpy imports cleanly, we can do the full check
                    result = torch.tensor([1])
                except (ImportError, RuntimeError) as e:
                    # NumPy is in flux - skip the tensor check
                    if not self.quiet:
                        safe_print(f"      ℹ️  Skipping torch tensor check (numpy unavailable)")
                    # Just verify torch module loaded
                    _ = torch.__version__
                    return True
                
            elif pkg_name == 'numpy':
                import numpy as np
                result = np.array([1]).sum()
                
        except Exception:
            return False
            
        return True

    def _is_bubble_healthy_in_subprocess(self, pkg_name: str, bubble_path_str: str) -> bool:
        """
        Spawns a fresh, clean Python process to check if the bubble is actually importable.
        If this returns True, the bubble is fine, but our current process memory is corrupted.
        """
        # Get the actual import name
        import_name = self._get_import_name_for_package(pkg_name)
        
        # FIXED: Use textwrap.dedent to remove leading whitespace
        check_script = textwrap.dedent(f"""\
            import sys
            import importlib
            sys.path.insert(0, r'{bubble_path_str}')
            try:
                # Use __import__ to get top-level module
                mod = __import__('{import_name}')
                print("SUCCESS")
            except Exception as e:
                print(f"FAILURE: {{e}}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
        """)
        
        try:
            # Run the check in a clean subprocess
            result = subprocess.run(
                [sys.executable, "-c", check_script],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if not self.quiet and result.returncode != 0:
                safe_print(f"      🔍 Subprocess validation output:")
                safe_print(f"         stdout: {result.stdout}")
                safe_print(f"         stderr: {result.stderr}")
            
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            if not self.quiet:
                safe_print(f"      ⏱️  Subprocess validation timed out")
            return False
        except Exception as e:
            if not self.quiet:
                safe_print(f"      ❌ Subprocess validation error: {e}")
            return False

    def _trigger_process_reexec(self):
        """
        NUCLEAR OPTION: The current process is corrupted (likely C++ extension state).
        We restart the entire script from scratch to clear the memory.
        """
        # Prevent infinite loops if re-exec fails repeatedly
        try:
            restart_count = int(os.environ.get("OMNIPKG_REEXEC_COUNT", "0"))
        except ValueError:
            restart_count = 0
            
        if restart_count >= 3:
            safe_print("   ❌ CRITICAL: Max re-execution attempts reached. Aborting re-exec.")
            return

        safe_print(f"   🔄 INITIATING PROCESS RE-EXECUTION (Attempt {restart_count + 1}/3)...")
        safe_print("   👋 See you in the next life!")
        
        # Mark the environment so the next process knows it's a restart
        env = os.environ.copy()
        env["OMNIPKG_REEXEC_COUNT"] = str(restart_count + 1)
        
        # Flush buffers to ensure logs are printed
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Replace the current process with a new one
        os.execve(sys.executable, [sys.executable] + sys.argv, env)

    def _clear_pycache_for_package(self, pkg_name: str):
        """
        Remove __pycache__ directories for a package to force fresh imports.
        Handles both bubble and main env locations.
        """
        try:
            # Find package location
            if self._activated_bubble_path:
                pkg_path = Path(self._activated_bubble_path) / pkg_name
            else:
                pkg_path = self.site_packages_root / pkg_name
            
            if pkg_path.exists() and pkg_path.is_dir():
                # Remove all __pycache__ directories recursively
                for pycache_dir in pkg_path.rglob('__pycache__'):
                    try:
                        shutil.rmtree(pycache_dir, ignore_errors=True)
                    except Exception:
                        pass
                
                # Also remove top-level .pyc files
                for pyc_file in pkg_path.rglob('*.pyc'):
                    try:
                        pyc_file.unlink()
                    except Exception:
                        pass
                        
        except Exception as e:
            # Non-critical, just log if verbose
            if not self.quiet:
                safe_print(f"      ℹ️  Could not clear pycache for {pkg_name}: {e}")

    def _auto_heal_broken_bubble(self, pkg_name: str, bubble_path: Path) -> bool:
        """
        Attempt to automatically heal a broken bubble installation.
        """
        pkg_spec = f"{pkg_name}=={self._current_package_spec.split('==')[1]}"
        
        if not self.quiet:
            safe_print(f"   🔧 Auto-healing: Force-reinstalling bubble...")
        
        try:
            if bubble_path.exists():
                shutil.rmtree(bubble_path)
                if not self.quiet:
                    safe_print(f"      🗑️  Removed corrupted bubble")
            
            from omnipkg.core import omnipkg as OmnipkgCore, ConfigManager
            
            cm = ConfigManager(suppress_init_messages=True)
            if hasattr(self, 'config') and isinstance(self.config, dict):
                cm.config.update(self.config)
            
            core = OmnipkgCore(cm)
            original_strategy = core.config.get('install_strategy')
            core.config['install_strategy'] = 'stable-main'
            
            try:
                if not self.quiet:
                    safe_print(f"      📦 Reinstalling {pkg_spec}...")
                
                result = core.smart_install([pkg_spec])
                
                if result != 0:
                    return False
                
                if bubble_path.exists():
                    if not self.quiet:
                        safe_print(f"      ✅ Bubble recreated, re-activating...")
                    
                    bubble_path_str = str(bubble_path)
                    sys.path[:] = [bubble_path_str] + [p for p in self.original_sys_path 
                                                    if not self._is_main_site_packages(p)]
                    
                    importlib.invalidate_caches()
                    if self._validate_import(pkg_name):
                        if not self.quiet:
                            safe_print(f"      🏥 HEALED! Package now imports successfully")
                        return True
                
                return False
                
            finally:
                if original_strategy:
                    core.config['install_strategy'] = original_strategy
                    
        except Exception as e:
            if not self.quiet:
                safe_print(f"      ❌ Auto-heal failed: {e}")
            return False
            
    def execute(self, code: str) -> dict:
        """
        Execute Python code in the activated environment.
        
        Works transparently in both worker and in-process modes.
        
        Args:
            code: Python code string to execute
            
        Returns:
            dict with keys:
                - success (bool): Whether execution succeeded
                - stdout (str): Captured output (if success)
                - error (str): Error message (if failure)
                - locals (str): Local variable names (if success)
        """
        if self._worker_mode and self._active_worker:
            # Worker mode: delegate to subprocess
            return self._active_worker.execute(code)
        else:
            # In-process mode: direct execution
            try:
                f = io.StringIO()
                old_stdout = sys.stdout
                sys.stdout = f
                
                try:
                    loc = {}
                    exec(code, globals(), loc)
                finally:
                    sys.stdout = old_stdout
                
                output = f.getvalue()
                return {
                    "success": True,
                    "stdout": output,
                    "locals": str(list(loc.keys()))
                }
            except Exception as e:
                import traceback
                return {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }
            
    def get_version(self, package_name):
        # Execute code to get version and path
        # The worker extracts the local variable named 'result' and merges it into the response
        code = f"import importlib.metadata; result = {{'version': importlib.metadata.version('{package_name}'), 'path': __import__('{package_name}').__file__}}"
        res = self.execute(code)
        
        if res.get('success'):
            return {
                'success': True,
                'version': res.get('version', 'unknown'),
                'path': res.get('path', 'daemon_managed')
            }
        return {'success': False, 'error': res.get('error', 'Unknown error')}
        
    
"""
Enhanced omnipkgLoader with automatic worker fallback for C++ extension conflicts.

This patch adds intelligent detection and automatic subprocess delegation when
the in-process loader encounters memory corruption from C++ extensions.

INTEGRATION: Add this to your loader.py
"""

class WorkerDelegationMixin:
    def __init__(self, *args, worker_fallback=True, **kwargs):
        super().__init__(*args, **kwargs)
        
        # CRITICAL FIX: Disable worker fallback if we are already inside a worker
        if os.environ.get("OMNIPKG_IS_WORKER_PROCESS") == "1":
            self._worker_fallback_enabled = False
        else:
            self._worker_fallback_enabled = worker_fallback
            
        self._active_worker = None
        self._worker_mode = False

        
    def _should_use_worker_mode(self, pkg_name: str) -> bool:
        # Double check: If we are in a worker, NEVER spawn another one
        if os.environ.get("OMNIPKG_IS_WORKER_PROCESS") == "1":
            return False
        # Packages with known C++ collision issues
        problematic_packages = {
            'flask', 'werkzeug', 'jinja2', 'markupsafe',
            'scipy', 'pandas', 'numpy',
            'tensorflow', 'tensorflow-gpu',
            'torch', 'torchvision',
            'pillow', 'opencv-python', 'cv2',
            'lxml', 'cryptography'
        }
        
        pkg_lower = pkg_name.lower().replace('-', '_')
        
        # Check if package or any of its known dependencies are problematic
        if pkg_lower in problematic_packages:
            return True
        
        # Check if bubble dependencies include problematic packages
        if self._activated_bubble_path:
            bubble_path = Path(self._activated_bubble_path)
            bubble_deps = self._get_bubble_dependencies(bubble_path)
            
            for dep in bubble_deps.keys():
                if dep.lower().replace('-', '_') in problematic_packages:
                    return True
        
        return False
    
    def _detect_cpp_collision_risk(self, pkg_name: str, bubble_deps: dict) -> bool:
        """
        Analyze if activating this bubble would cause C++ extension conflicts.
        Returns True if collision is likely.
        """
        # Check if any problematic modules are already loaded in memory
        problematic_modules = [
            'werkzeug._internal', 'jinja2.ext', 'markupsafe._speedups',
            'numpy.core', 'scipy.linalg', 'torch._C',
            'tensorflow.python', 'cv2'
        ]
        
        for mod_pattern in problematic_modules:
            # Check exact match
            if mod_pattern in sys.modules:
                if not self.quiet:
                    safe_print(f"   🚨 C++ collision risk: {mod_pattern} already loaded")
                return True
            
            # Check prefix match (e.g., 'torch._C' matches 'torch._C._something')
            for loaded_mod in sys.modules:
                if loaded_mod.startswith(mod_pattern):
                    if not self.quiet:
                        safe_print(f"   🚨 C++ collision risk: {loaded_mod} detected")
                    return True
        
        # Check for version conflicts in C++-heavy packages
        main_env_versions = {}
        for pkg in bubble_deps.keys():
            try:
                main_version = get_version(pkg)
                bubble_version = bubble_deps[pkg]
                
                if main_version != bubble_version:
                    # If this is a known C++ package, flag it
                    if pkg.lower() in {'numpy', 'scipy', 'torch', 'tensorflow', 
                                      'werkzeug', 'jinja2', 'markupsafe'}:
                        if not self.quiet:
                            safe_print(f"   🚨 C++ version conflict: {pkg} "
                                     f"({main_version} vs {bubble_version})")
                        return True
            except PackageNotFoundError:
                continue
        
        return False
    
    def _create_worker_for_spec(self, package_spec: str):
        """
        Connects to the daemon to handle this package spec.
        """
        if not self._use_worker_pool:
            return None
    
        # Don't use daemon if we ARE the daemon worker (prevent recursion)
        if os.environ.get("OMNIPKG_IS_DAEMON_WORKER"):
            return None
    
        try:
            # Get the client (auto-starts if needed)
            client = self._get_daemon_client()
            
            # Return proxy that looks like a worker but talks to daemon
            proxy = DaemonProxy(client, package_spec)
            
            if not self.quiet:
                safe_print(f"   ⚡ Connected to Daemon for {package_spec}")
                
            return proxy
            
        except Exception as e:
            if not self.quiet:
                safe_print(f"   ⚠️  Daemon connection failed: {e}. Falling back to local.")
            return None
    
    def __enter__(self):
        self._activation_start_time = time.perf_counter_ns()
        if not self._current_package_spec: raise ValueError("Package spec required")
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")

        # 1. Proactive Worker Mode (Daemon)
        if self._worker_fallback_enabled and self._should_use_worker_proactively(pkg_name):
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            if self._active_worker:
                self._worker_mode = True
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                return self

        # Store original activation start time
        self._activation_start_time = time.perf_counter_ns()
        
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")
        
        # STRATEGY 1: Proactive Worker Mode for Known Problematic Packages
        if self._worker_fallback_enabled and self._should_use_worker_mode(pkg_name):
            if not self.quiet:
                safe_print(f"   🧠 Smart Decision: Using worker mode for {pkg_name} "
                          f"(known C++ collision risk)")
            
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            if self._active_worker:
                self._worker_mode = True
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (self._activation_end_time - 
                                                 self._activation_start_time)
                return self
            else:
                if not self.quiet:
                    safe_print(f"   ⚠️  Worker creation failed, falling back to in-process")
        
        # STRATEGY 2: Try In-Process Activation (Original Logic)
        try:
            # Call the original __enter__ logic
            return super().__enter__()
            
        except ProcessCorruptedException as e:
            # STRATEGY 3: Reactive Worker Fallback on C++ Collision
            if not self._worker_fallback_enabled:
                raise  # Re-raise if worker fallback is disabled
            
            if not self.quiet:
                safe_print(f"   🔄 C++ collision detected, switching to worker mode...")
                safe_print(f"      Original error: {str(e)}")
            
            # Clean up any partial activation state
            self._panic_restore_cloaks()
            
            # Create worker as fallback
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            
            if not self._active_worker:
                if not self.quiet:
                    safe_print(f"   ❌ Worker fallback failed")
                raise RuntimeError(f"Both in-process and worker activation failed for "
                                 f"{self._current_package_spec}")
            
            self._worker_mode = True
            self._activation_successful = True
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = (self._activation_end_time - 
                                             self._activation_start_time)
            
            if not self.quiet:
                safe_print(f"   ✅ Successfully recovered using worker mode")
            
            return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with worker cleanup."""
        if self._worker_mode and self._active_worker:
            if not self.quiet:
                safe_print(f"   🛑 Shutting down worker for {self._current_package_spec}...")
            
            try:
                self._active_worker.shutdown()
            except Exception as e:
                if not self.quiet:
                    safe_print(f"   ⚠️  Worker shutdown warning: {e}")
            finally:
                self._active_worker = None
                self._worker_mode = False
        else:
            # Call original deactivation
            super().__exit__(exc_type, exc_val, exc_tb)
    
    def execute(self, code: str) -> dict:
        """
        Execute code either in worker or in-process depending on mode.
        This provides a unified interface regardless of activation strategy.
        """
        if self._worker_mode and self._active_worker:
            return self._active_worker.execute(code)
        else:
            # In-process execution
            try:
                f = io.StringIO()
                sys.stdout = f
                try:
                    loc = {}
                    exec(code, globals(), loc)
                finally:
                    sys.stdout = sys.__stdout__
                
                output = f.getvalue()
                return {
                    "success": True,
                    "stdout": output,
                    "locals": str(loc.keys())
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e)
                }
    
    def get_version(self, package_name: str) -> dict:
        """Get package version, works in both worker and in-process mode."""
        if self._worker_mode and self._active_worker:
            return self._active_worker.get_version(package_name)
        else:
            try:
                from importlib.metadata import version
                ver = version(package_name)
                mod = __import__(package_name)
                return {
                    "success": True,
                    "version": ver,
                    "path": mod.__file__ if hasattr(mod, '__file__') else None
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e)
                }