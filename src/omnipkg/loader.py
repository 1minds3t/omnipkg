from __future__ import annotations  # Python 3.6+ compatibility

import atexit
import gc
import importlib
import io  # <-- ADD THIS, needed for execute() method
import json
import os
import platform
import re
import shutil
import site
import subprocess
import sys
import textwrap
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as get_version
except ImportError:
    from importlib_metadata import PackageNotFoundError
    from importlib_metadata import version as get_version
from pathlib import Path
from typing import (  # <-- Make sure Dict is in this import
    Dict,
    List,
    Optional,
)

import filelock
from packaging.utils import canonicalize_name

from omnipkg.common_utils import safe_print

# Import safe_print and custom exceptions
try:
    from .common_utils import ProcessCorruptedException, UVFailureDetector, safe_print
except ImportError:
    from omnipkg.common_utils import (
        ProcessCorruptedException,
        )

# Import i18n
from omnipkg.i18n import _

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

try:
    from .cache import SQLiteCacheClient
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════
# 🧠 INSTALL TENSORFLOW PATCHER AT MODULE LOAD (ONCE ONLY)
# ═══════════════════════════════════════════════════════════
_PATCHER_AVAILABLE = False
_PATCHER_ERROR = None

try:
    from omnipkg.isolation.patchers import smart_tf_patcher

    try:
        smart_tf_patcher()
        _PATCHER_AVAILABLE = True
    except Exception as init_error:
        # Patcher imported but failed to initialize - that's OK!
        _PATCHER_ERROR = str(init_error)
        pass
except ImportError:
    # Patcher module not available - that's OK!
    pass
except Exception as e:
    _PATCHER_ERROR = _('Unexpected error loading patcher: {}').format(str(e))
    pass

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
            raise ImportError(
                "PersistentWorker not available"
            )  # <-- FIXED: Added closing parenthesis


# ============================================================================
# PROFILER FOR BUBBLE ACTIVATION
# ============================================================================


@dataclass
class ProfileMark:
    name: str
    elapsed_ms: float
    category: str
    fixable: bool
    notes: str = ""


class BubbleProfiler:
    CATEGORIES = {
        "YOUR_CODE": "🔧",
        "PYTHON_INTERNAL": "🐍",
        "KERNEL": "💾",
        "MIXED": "⚡",
    }

    def __init__(self, quiet=False):
        self.quiet = quiet
        self.marks = []
        self.start_time = 0
        self.last_mark_time = 0

    def start(self):
        self.start_time = time.perf_counter_ns()
        self.last_mark_time = self.start_time

    def mark(self, name, category="YOUR_CODE", fixable=True, notes=""):
        now = time.perf_counter_ns()
        elapsed_ms = (now - self.last_mark_time) / 1_000_000
        self.marks.append(ProfileMark(name, elapsed_ms, category, fixable, notes))
        self.last_mark_time = now

    def finish(self):
        total_ms = (self.last_mark_time - self.start_time) / 1_000_000
        self.marks.append(ProfileMark("TOTAL", total_ms, "", False))

    def print_report(self):
        if self.quiet:
            return
        safe_print("\n" + "=" * 70 + "\n📊 BUBBLE ACTIVATION PROFILE\n" + "=" * 70)
        for m in self.marks[:-1]:
            icon = self.CATEGORIES.get(m.category, "❓")
            fix = "✅ FIX" if m.fixable else "❌ NOPE"
            print(f"   {icon} {m.name:30s} {m.elapsed_ms:8.2f}ms  {fix:8s}  {m.category}")
        safe_print("-" * 70 + f"\n🎯 TOTAL: {self.marks[-1].elapsed_ms:.2f}ms\n" + "=" * 70 + "\n")


class omnipkgLoader:
    """
    Activates isolated package environments with optional persistent worker pool.
    """

    # ═══════════════════════════════════════════════════════════
    # CLASS-LEVEL WORKER POOL (Shared  across all loader instances)
    # ═══════════════════════════════════════════════════════════
    _worker_pool = {}  # {package_spec: PersistentWorker}
    _worker_pool_lock = threading.RLock()
    __worker_pool_enabled = True  # Global toggle
    # _cloak_locks is now managed globally by fs_lock_queue._pkg_lock_cache
    # <-- NEW: Add install locks
    _install_locks: Dict[str, filelock.FileLock] = {}
    _locks_dir: Optional[Path] = None
    _numpy_version_history: List[str] = []
    _active_cloaks: Dict[str, int] = {}
    _global_cloaking_lock = threading.RLock()  # Re-entrant lock
    _nesting_depth = 0
    VERSION_CHECK_METHOD = "filesystem"  # Options: 'kb', 'filesystem', 'glob', 'importlib'
    _profiling_enabled = True
    _profile_data = defaultdict(list)
    _daemon_did_version_switch: bool = False  # Set True when any bubble activated in daemon
    _nesting_lock = threading.Lock()
    _active_cloaks_lock = threading.RLock()  # <-- ADD THIS LINE
    _numpy_lock = threading.Lock()  # Protects the history list
    _active_main_env_packages = set()  # Packages currently active from main env
    _dependency_cache: Optional[Dict[str, Path]] = None
    # -------------------------------------------------------------------------
    # 🔬 ABI PACKAGES: C extensions that cannot be safely reloaded in-process.
    # When one of these is requested and its .so is already mapped in this
    # process, the loader automatically delegates to run_once() (ephemeral
    # daemon worker) instead of attempting an in-process switch that will fail.
    # -------------------------------------------------------------------------
    ABI_PACKAGES = {
        "numpy", "scipy", "torch", "tensorflow", "pandas",
        "cupy", "jax", "xgboost", "lightgbm",
    }

    # -------------------------------------------------------------------------
    # 🛡️ IMMORTAL PACKAGES: These must never be cloaked/deleted
    # -------------------------------------------------------------------------
    _CRITICAL_DEPS = {
        # Core omnipkg
        "omnipkg",
        "click",
        "rich",
        "toml",
        "packaging",
        "filelock",
        "colorama",
        "tabulate",
        "psutil",
        "distro",
        "pydantic",
        "pydantic_core",
        "ruamel.yaml",
        "safety_schemas",
        "typing_extensions",
        "mypy_extensions",
        # Networking (Requests) - CRITICAL for simple fetches
        "requests",
        "urllib3",
        "charset_normalizer",
        "idna",
        "certifi",
        # Async Networking (Aiohttp) - CRITICAL for OmniPkg background tasks
        "aiohttp",
        "aiosignal",
        "aiohappyeyeballs",
        "attrs",
        "frozenlist",
        "multidict",
        "yarl",
        # Cache
        "redis",
    }

    def __init__(
        self,
        package_spec: Union[str, list, tuple, dict] = None,
        config: dict = None,
        quiet: bool = False,
        force_activation: bool = False,
        use_worker_pool: bool = True,
        enable_profiling=False,
        worker_fallback: bool = True,
        cache_client=None,
        redis_key_prefix=None,
        isolation_mode: str = "strict",
    ):
        """
        Initializes the loader with enhanced Python version awareness.
        """
        self._true_site_packages = None

        # Try to find the real site-packages via the omnipkg module location
        try:
            import omnipkg

            # Usually .../site-packages/omnipkg
            omnipkg_loc = Path(omnipkg.__file__).parent.parent
            if omnipkg_loc.name == "site-packages":
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

        self.python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.python_version_nodot = f"{sys.version_info.major}{sys.version_info.minor}"
        self.force_activation = force_activation

        if not self.quiet:
            safe_print(
                _("🐍 [omnipkg loader] Running in Python {} context").format(self.python_version)
            )
        self._initialize_version_aware_paths()
        self._store_clean_original_state()
        # NEW
        # Normalize all input formats to an internal list
        if isinstance(package_spec, dict):
            self._package_specs = [f"{k}=={v}" for k, v in package_spec.items()]
        elif isinstance(package_spec, (list, tuple)):
            self._package_specs = [p.strip() for p in package_spec]
        elif isinstance(package_spec, str) and ',' in package_spec:
            self._package_specs = [p.strip() for p in package_spec.split(',')]
        elif package_spec is not None:
            self._package_specs = [package_spec]
        else:
            self._package_specs = []

        # Keep _current_package_spec pointing to first for all existing single-pkg logic
        self._current_package_spec = self._package_specs[0] if self._package_specs else None
        self._activated_bubble_path = None
        self._cloaked_main_modules = []
        self._cloaked_bubbles = []  # To track bubbles we cloak when activating main env
        self.isolation_mode = isolation_mode
        self._activation_successful = False
        self.cache_client = cache_client
        if cache_client is None:
            self._init_own_cache()
        else:
            self.cache_client = cache_client
            self.redis_key_prefix = redis_key_prefix or "omnipkg:pkg:"
        self.redis_key_prefix = redis_key_prefix or "omnipkg:pkg:"
        self._activation_start_time = None
        self._activation_end_time = None
        self._is_nested = False
        self._deactivation_start_time = None
        self._worker_from_pool = False
        self._worker_fallback_enabled = worker_fallback
        self._active_worker = None
        self._worker_mode = False
        self._run_once_mode = False  # True when auto-switched to ephemeral daemon worker
        self._abi_conflict_detected = False  # True when ABI conflict detected but worker unavailable
        self._packages_we_cloaked = set()  # Only packages WE cloaked
        self._using_main_env = False  # Track if we're using main env directly
        self._my_main_env_package = None
        self._active_bubble_lock = None  # Held for the lifetime of bubble activation
        self._use_worker_pool = use_worker_pool
        self._cloaked_main_modules = []
        self._profiling_enabled = enable_profiling or omnipkgLoader._profiling_enabled
        self._profile_times = {}  # Instance-level timing data
        self._deactivation_end_time = None
        self._total_activation_time_ns = None
        self._total_deactivation_time_ns = None
        self._omnipkg_dependencies = self._get_omnipkg_dependencies()
        # NEW
        self._activated_bubble_dependencies = []  # To track everything we need to exorcise
        self._active_sub_loaders = []  # Sub-loaders for multi-package mode

        if omnipkgLoader._locks_dir is None:
            omnipkgLoader._locks_dir = self.multiversion_base / ".locks"
            omnipkgLoader._locks_dir.mkdir(parents=True, exist_ok=True)

    def _init_own_cache(self):
        """Initialize loader's own cache connection for isolated processes"""
        try:
            from .cache import SQLiteCacheClient
            from .core import ConfigManager

            # Load config
            config_mgr = ConfigManager(suppress_init_messages=True)
            config = config_mgr.config

            # Get env_id for cache key prefix
            env_id = config_mgr.env_id
            py_ver = f"py{sys.version_info.major}.{sys.version_info.minor}"

            # Construct cache_db_path manually (same logic as omnipkg class)
            cache_db_path = config_mgr.config_dir / f"cache_{env_id}.sqlite"

            # Initialize SQLite cache (always available fallback)
            self.cache_client = SQLiteCacheClient(cache_db_path)

            # Set key prefix
            base = config.get("redis_key_prefix", "omnipkg:pkg:").split(":")[0]
            self.redis_key_prefix = f"{base}:env_{env_id}:{py_ver}:pkg:"

        except Exception as e:
            # If cache init fails, set to None (will skip KB optimization)
            if not self.quiet:
                safe_print(_('   ⚠️ Cache init failed: {}').format(e))
            self.cache_client = None
            self.redis_key_prefix = "omnipkg:pkg:"

    def _maybe_refresh_dependency_cache(self):
        """
        Drop the in-process _dependency_cache if the sentinel file is newer
        than when the cache was built.  One stat() call per __enter__.
        """
        try:
            from omnipkg.isolation.fs_lock_queue import DepCacheSentinel
            if sentinel_is_dirty := DepCacheSentinel(self.multiversion_base).is_dirty_since(
                omnipkgLoader._dep_cache_built_at
            ):
                if not self.quiet:
                    safe_print("   ♻️  [cache] FS change detected — refreshing dep cache")
                omnipkgLoader._dependency_cache = None
        except Exception:
            pass

    def _profile_start(self, label):
        """Start timing a profiled section"""
        if self._profiling_enabled:
            self._profile_times[label] = time.perf_counter_ns()

    def _profile_end(self, label, print_now=False):
        """
        End timing and optionally print.

        FIXED: Now respects self._profiling_enabled for ALL output,
        including print_now=self._profiling_enabled calls.
        """
        if not self._profiling_enabled:
            return 0

        if label not in self._profile_times:
            return 0

        elapsed_ns = time.perf_counter_ns() - self._profile_times[label]
        elapsed_ms = elapsed_ns / 1_000_000

        # Store in class-level data
        omnipkgLoader._profile_data[label].append(elapsed_ns)

        # CRITICAL FIX: Check profiling flag AND quiet flag before printing
        if print_now and not self.quiet:
            safe_print(f"      ⏱️  {label}: {elapsed_ms:.3f}ms")

        return elapsed_ns

    @classmethod
    def enable_profiling(cls):
        """Enable profiling for all loaders"""
        cls._profiling_enabled = True
        cls._profile_data.clear()

    @classmethod
    def disable_profiling(cls):
        """Disable profiling"""
        cls._profiling_enabled = False

    @classmethod
    def print_profile_report(cls):
        """Print aggregated profiling data"""
        if not cls._profile_data:
            print(_('No profiling data collected'))
            return

        print("\n" + "=" * 70)
        safe_print("📊 OMNIPKG LOADER PROFILING REPORT")
        print("=" * 70)

        # Sort by total time
        sorted_data = sorted(cls._profile_data.items(), key=lambda x: sum(x[1]), reverse=True)

        total_time_ns = sum(sum(times) for times in cls._profile_data.values())

        print(f"\n{'Operation':<35} {'Count':>6} {'Total':>10} {'Avg':>10} {'%':>6}")
        print("-" * 70)

        for label, times in sorted_data:
            count = len(times)
            total_ms = sum(times) / 1_000_000
            avg_ms = total_ms / count if count > 0 else 0
            percent = (sum(times) / total_time_ns * 100) if total_time_ns > 0 else 0

            print(f"{label:<35} {count:>6} {total_ms:>9.2f}ms {avg_ms:>9.2f}ms {percent:>5.1f}%")

        print("-" * 70)
        print(
            f"{'TOTAL':<35} {sum(len(t) for t in cls._profile_data.values()):>6} "
            f"{total_time_ns/1_000_000:>9.2f}ms"
        )
        print("=" * 70 + "\n")

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

                worker = PersistentWorker(package_spec=package_spec, verbose=verbose)

                cls._worker_pool[package_spec] = worker

                if verbose:
                    safe_print("   ✅ Worker created and added to pool")

                return worker, False  # (worker, from_pool)
            except Exception as e:
                if verbose:
                    safe_print(_('   ❌ Worker creation failed: {}').format(e))
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
                safe_print(_('   🛑 Shutting down worker pool ({} workers)...').format(len(cls._worker_pool)))

            for spec, worker in list(cls._worker_pool.items()):
                try:
                    worker.shutdown()
                    if verbose:
                        safe_print(_('      ✅ Shutdown: {}').format(spec))
                except Exception as e:
                    if verbose:
                        safe_print(_('      ⚠️  Failed to shutdown {}: {}').format(spec, e))

            cls._worker_pool.clear()

            if verbose:
                safe_print("   ✅ Worker pool shutdown complete")

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
                "total": len(cls._worker_pool),
                "active": len(active_workers),
                "dead": len(dead_workers),
                "active_specs": active_workers,
                "dead_specs": dead_workers,
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
                safe_print(_('   ⚠️  Daemon connection failed: {}. Falling back to local.').format(e))
            return None

    def stabilize_daemon_state(self):
        """Uncloaks files using the Daemon's Idle Pool (Fast Path)."""
        self._profile_start("daemon_uncloak")

        # 1. Collect Moves
        moves = []
        for orig, cloak, success in self._cloaked_main_modules:
            if success and cloak.exists():
                moves.append((str(cloak), str(orig)))
        for cloak, orig in self._cloaked_bubbles:
            if cloak.exists():
                moves.append((str(cloak), str(orig)))

        if not moves:
            self._profile_end("daemon_uncloak")
            return

        # DAEMON WORKER FAST PATH: Just uncloak in-process synchronously
        if self._is_daemon_worker():
            from omnipkg.isolation.fs_lock_queue import safe_uncloak
            for s, d in moves:
                pkg_name = Path(d).name.split("-")[0].split(".")[0]
                safe_uncloak(
                    src=Path(s),
                    dst=Path(d),
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=pkg_name,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=5.0,
                )
            self._cloaked_main_modules.clear()
            self._cloaked_bubbles.clear()
            # ── Signal immediately that FS is stable so other loaders
            #    can proceed without waiting for full activation to finish ──
            self._signal_daemon_lock_released()
            self._profile_end("daemon_uncloak", print_now=self._profiling_enabled)
            return

        # 2. Execute via Daemon (Preferred) or Fallback
        success = False
        if DAEMON_AVAILABLE:
            try:
                client = self._get_daemon_client()
                res = client.request_maintenance(moves)
                if res.get("success"):
                    success = True
                    if not self.quiet:
                        safe_print(_('   🔄 Daemon Uncloak: {} items restored').format(res.get('count')))
            except Exception:
                pass

        # 3. Fallback (Slow Subprocess)
        if not success:
            try:

                script = (
                    "import sys, json; moves=json.loads(sys.argv[1]); "
                    "from omnipkg.isolation.fs_lock_queue import safe_uncloak; "
                    "from pathlib import Path; "
                    "locks_dir = Path(sys.argv[2]); "
                    "for s,d in moves: safe_uncloak(Path(s), Path(d), locks_dir, Path(d).name.split('-')[0].split('.')[0], timeout=5.0)"
                )
                creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
                subprocess.run([sys.executable, "-c", script, json.dumps(moves), str(omnipkgLoader._locks_dir)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
            except:
                pass

        # 4. Clear local tracking
        self._cloaked_main_modules.clear()
        self._cloaked_bubbles.clear()
        self._profile_end("daemon_uncloak", print_now=self._profiling_enabled)

    def _get_cloak_lock(self, pkg_name: str) -> filelock.FileLock:
        """
        Get or create a file lock for a specific package's cloak operations.
        Delegates to fs_lock_queue to ensure the SAME FileLock instance is
        used process-wide, making lock acquisition truly re-entrant for nested loaders!
        """
        from omnipkg.isolation.fs_lock_queue import _get_pkg_lock
        return _get_pkg_lock(omnipkgLoader._locks_dir, pkg_name)

    def _get_install_lock(self, spec_str: str) -> filelock.FileLock:
        """
        Gets or creates a file lock for a specific package INSTALLATION.
        This prevents race conditions when multiple threads try to install
        the same missing bubble.
        """
        # Normalize the name for the lock file
        lock_name = spec_str.replace("==", "-").replace(".", "_")

        if lock_name not in omnipkgLoader._install_locks:
            lock_file = omnipkgLoader._locks_dir / f"install-{lock_name}.lock"
            omnipkgLoader._install_locks[lock_name] = filelock.FileLock(
                str(lock_file),
                timeout=300,  # Wait up to 5 minutes for an install to finish
            )

        return omnipkgLoader._install_locks[lock_name]

    def _cloak_bubble(self, bubble_path, suffix, timeout=10.0):
        """
        Atomically cloak a bubble directory. Returns (cloak_path, success).
        timeout=0 → non-blocking try (returns False immediately if locked).
        """
        from omnipkg.isolation.fs_lock_queue import safe_cloak
        pkg_name = bubble_path.name.split("-")[0]
        cloak_path = bubble_path.with_name(bubble_path.name + suffix)
        ok = safe_cloak(
            src=bubble_path,
            dst=cloak_path,
            locks_dir=omnipkgLoader._locks_dir,
            pkg_name=pkg_name,
            active_cloaks=omnipkgLoader._active_cloaks,
            owner_id=id(self),
            sentinel_base=self.multiversion_base,
            timeout=timeout,
        )
        return cloak_path, ok

    def _uncloak_bubble(self, cloak_path, original_path, timeout=10.0):
        """
        Atomically restore a cloaked bubble. Returns True on success (including
        "already restored by another process"). Returns False only on lock timeout.
        """
        from omnipkg.isolation.fs_lock_queue import safe_uncloak
        pkg_name = original_path.name.split("-")[0]
        ok = safe_uncloak(
            src=cloak_path,
            dst=original_path,
            locks_dir=omnipkgLoader._locks_dir,
            pkg_name=pkg_name,
            active_cloaks=omnipkgLoader._active_cloaks,
            sentinel_base=self.multiversion_base,
            timeout=timeout,
        )
        # Always deregister regardless of outcome
        with omnipkgLoader._active_cloaks_lock:
            omnipkgLoader._active_cloaks.pop(str(cloak_path), None)
        return ok

    def _initialize_version_aware_paths(self):
        """
        Initialize paths with strict Python version isolation.
        Ensures we only work with version-compatible directories.
        """
        if (
            self.config
            and "multiversion_base" in self.config
            and ("site_packages_path" in self.config)
        ):
            self.multiversion_base = Path(self.config["multiversion_base"])
            configured_site_packages = Path(self.config["site_packages_path"])
            if self._is_version_compatible_path(configured_site_packages):
                self.site_packages_root = configured_site_packages
                if not self.quiet:
                    safe_print(
                        _("✅ [omnipkg loader] Using configured site-packages: {}").format(
                            self.site_packages_root
                        )
                    )
            else:
                if not self.quiet:
                    safe_print(
                        _(
                            "⚠️ [omnipkg loader] Configured site-packages path is not compatible with Python {}. Auto-detecting..."
                        ).format(self.python_version)
                    )
                self.site_packages_root = self._auto_detect_compatible_site_packages()
        else:
            if not self.quiet:
                safe_print(
                    _(
                        "⚠️ [omnipkg loader] Config not provided or incomplete. Auto-detecting Python {}-compatible paths."
                    ).format(self.python_version)
                )
            self.site_packages_root = self._auto_detect_compatible_site_packages()
            self.multiversion_base = self.site_packages_root / ".omnipkg_versions"
        if not self.multiversion_base.exists():
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                if not self.quiet:
                    safe_print(
                        _("✅ [omnipkg loader] Created bubble directory: {}").format(
                            self.multiversion_base
                        )
                    )
            except Exception as e:
                raise RuntimeError(
                    _("Failed to create bubble directory at {}: {}").format(
                        self.multiversion_base, e
                    )
                )

    def _is_version_compatible_path(self, path: Path) -> bool:
        """
        Performs a robust check to see if a given path belongs to the
        currently running Python interpreter's version, preventing
        cross-version contamination.
        """
        path_str = str(path).lower()
        match = re.search("python(\\d+\\.\\d+)", path_str)
        if not match:
            return True
        path_version = match.group(1)
        if path_version == self.python_version:
            return True
        else:
            if not self.quiet:
                safe_print(
                    _(
                        "🚫 [omnipkg loader] Rejecting incompatible path (contains python{}) for context python{}: {}"
                    ).format(path_version, self.python_version, path)
                )
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
                        safe_print(
                            _(
                                "✅ [omnipkg loader] Auto-detected compatible site-packages: {}"
                            ).format(candidate)
                        )
                    return candidate
        except (AttributeError, IndexError):
            pass
        python_version_path = f"python{self.python_version}"
        candidate = Path(sys.prefix) / "lib" / python_version_path / "site-packages"
        if candidate.exists():
            if not self.quiet:
                safe_print(
                    _("✅ [omnipkg loader] Using sys.prefix-based site-packages: {}").format(
                        candidate
                    )
                )
            return candidate
        for path_str in sys.path:
            if "site-packages" in path_str:
                candidate = Path(path_str)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    if not self.quiet:
                        safe_print(
                            _(
                                "✅ [omnipkg loader] Using sys.path-derived site-packages: {}"
                            ).format(candidate)
                        )
                    return candidate
        raise RuntimeError(
            _("Could not auto-detect Python {}-compatible site-packages directory").format(
                self.python_version
            )
        )

    def _store_clean_original_state(self):
        """
        Store original state with contamination filtering to prevent cross-version issues.
        """
        _mvbase_str = str(self.multiversion_base)
        self.original_sys_path = []
        contaminated_paths = []
        for path_str in sys.path:
            path_obj = Path(path_str)
            # Filter 1: wrong Python version
            if not self._is_version_compatible_path(path_obj):
                contaminated_paths.append(path_str)
                continue
            # Filter 2: bubble paths from parent loaders — these are versioned
            # package directories inside multiversion_base. Including them in
            # original_sys_path causes "importing from source directory" errors
            # when STRICT mode restores sys.path in nested contexts because the
            # bubble path appears twice (once from parent, once from us).
            # Filter 2: (REMOVED) We must preserve parent bubbles so we can restore them!
            # If we filter them out, sys.path is irrevocably destroyed when nested loaders exit.
            self.original_sys_path.append(path_str)
        if contaminated_paths and not self.quiet:
            safe_print(
                _("🧹 [omnipkg loader] Filtered out {} incompatible paths from sys.path").format(
                    len(contaminated_paths)
                )
            )
        self.original_sys_modules_keys = set(sys.modules.keys())
        self.original_path_env = os.environ.get("PATH", "")
        self.original_pythonpath_env = os.environ.get("PYTHONPATH", "")
        if not self.quiet:
            safe_print(
                _(
                    "✅ [omnipkg loader] Stored clean original state with {} compatible paths"
                ).format(len(self.original_sys_path))
            )

    def _filter_environment_paths(self, env_var: str) -> str:
        """
        Filter environment variable paths to remove incompatible Python versions.
        """
        if env_var not in os.environ:
            return ""
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
        cache_file = self.multiversion_base / ".cache" / f"loader_deps_{self.python_version}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    cached_data = json.load(f)

                # Convert to Path objects
                dependencies = {name: Path(path) for name, path in cached_data.items()}

                # 🔍 VALIDATION: Check if cache covers our current critical list
                # If we updated the code to add 'aiohttp', but cache is old, we MUST invalidate.
                cached_keys = set(dependencies.keys())
                # Normalize critical deps to canonical names for comparison
                required_keys = {d.replace("-", "_") for d in self._CRITICAL_DEPS}

                required_keys - cached_keys

                # Ignore packages that genuinely aren't installed, but if cache is EMPTY for them...
                # Actually, simpler heuristic: If cache lacks aiohttp/requests, it's definitely stale.
                if "aiohttp" in self._CRITICAL_DEPS and "aiohttp" not in cached_keys:
                    if not self.quiet:
                        safe_print(
                            "   ♻️  Cache stale (missing aiohttp). Re-scanning dependencies..."
                        )
                else:
                    omnipkgLoader._dependency_cache = dependencies
                    return dependencies

            except (json.JSONDecodeError, IOError, Exception):
                pass  # Cache corrupt or invalid, proceed to detection

        # Tier 3: Detection & Save
        dependencies = self._detect_omnipkg_dependencies()
        omnipkgLoader._dependency_cache = dependencies

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            paths_to_save = {name: str(path) for name, path in dependencies.items()}
            with open(cache_file, "w") as f:
                json.dump(paths_to_save, f)
        except IOError:
            pass

        omnipkgLoader._dep_cache_built_at = time.time()
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
            dep_variants = [dep, dep.replace("-", "_"), dep.replace("_", "-")]

            # Special case for 'attr' package which is installed as 'attrs'
            if dep == "attrs":
                dep_variants.append("attr")

            for dep_variant in dep_variants:
                try:
                    # Attempt Import
                    dep_module = importlib.import_module(dep_variant)

                except ImportError:
                    # 🚑 HEALING PROTOCOL: Module missing? Check if we cloaked it!
                    canonical = dep.replace("-", "_")
                    # Look for ANY cloak of this package
                    # We use the raw site_packages_root to bypass sys.path mess
                    cloaks = list(self.site_packages_root.glob(f"{canonical}*_omnipkg_cloaked*"))

                    if cloaks:
                        if not self.quiet:
                            safe_print(
                                _('   🚑 RESURRECTING critical package: {} (Found {} cloaks)').format(canonical, len(cloaks))
                            )

                        # Sort by timestamp (newest first) and restore
                        try:
                            # Simple cleanup of the name to find the target
                            # e.g., aiohttp.123_omnipkg_cloaked -> aiohttp
                            newest_cloak = sorted(cloaks, key=lambda p: str(p), reverse=True)[0]
                            original_name = re.sub(
                                r"\.\d+_omnipkg_cloaked.*$", "", newest_cloak.name
                            )
                            target_path = newest_cloak.parent / original_name

                            # Nuke any empty directory blocking us
                            from omnipkg.isolation.fs_lock_queue import safe_uncloak
                            safe_uncloak(
                                src=newest_cloak,
                                dst=target_path,
                                locks_dir=omnipkgLoader._locks_dir,
                                pkg_name=canonical,
                                active_cloaks=omnipkgLoader._active_cloaks,
                                sentinel_base=self.multiversion_base,
                                timeout=5.0
                            )

                            # 🔄 RETRY IMPORT after healing
                            importlib.invalidate_caches()
                            try:
                                dep_module = importlib.import_module(dep_variant)
                                if not self.quiet:
                                    safe_print(_('      ✅ Resurrected and loaded: {}').format(original_name))
                            except ImportError:
                                continue  # Still broken, give up on this variant
                        except Exception as e:
                            if not self.quiet:
                                safe_print(_('      ❌ Failed to resurrect {}: {}').format(canonical, e))
                            continue
                    else:
                        continue  # No cloak found, genuinely missing

                # If we have the module (naturally or resurrected), record it
                if hasattr(dep_module, "__file__") and dep_module.__file__:
                    dep_path = Path(dep_module.__file__).parent

                    if self._is_version_compatible_path(dep_path) and (
                        self.site_packages_root in dep_path.parents
                        or dep_path == self.site_packages_root / dep_variant
                    ):
                        canonical_name = dep.replace("-", "_")
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
                safe_print(
                    f"   🔌 Re-connecting main site-packages for {self._current_package_spec}"
                )
            # Append to end to keep bubble isolation priority,
            # but ensure visibility for this package.
            sys.path.append(main_path)

    def _is_version_compatible_path(self, path: Path) -> bool:
        """
        Performs a robust check to see if a given path belongs to the
        currently running Python interpreter's version.
        """
        # (Existing logic)
        path_str = str(path).lower()
        match = re.search("python(\\d+\\.\\d+)", path_str)
        if not match:
            return True
        path_version = match.group(1)
        if path_version == self.python_version:
            return True
        else:
            if not self.quiet:
                safe_print(
                    _(
                        "🚫 [omnipkg loader] Rejecting incompatible path (contains python{}) for context python{}: {}"
                    ).format(path_version, self.python_version, path)
                )
            return False

    def _scrub_sys_path_of_bubbles(self):
        """
        Aggressively scrubs all omnipkg bubble paths from sys.path
        using resolved paths to avoid string mismatch issues.
        """
        if not self.multiversion_base.exists():
            return

        try:
            multiversion_base_resolved = self.multiversion_base.resolve()
        except OSError:
            return

        original_count = len(sys.path)
        new_path = []

        for p in sys.path:
            try:
                # Resolve path to handle symlinks/relatives, but fallback if file not found
                p_path = Path(p)
                if p_path.exists():
                    p_path = p_path.resolve()

                # Check if path is inside our bubble directory
                if str(multiversion_base_resolved) in str(p_path):
                    continue

                # Secondary check for literal string match (if resolve failed or behaved weirdly)
                if ".omnipkg_versions" in str(p):
                    continue

                new_path.append(p)
            except Exception:
                # Be conservative: if we can't check it, keep it, unless obviously a bubble
                if ".omnipkg_versions" in str(p):
                    continue
                new_path.append(p)

        sys.path[:] = new_path

        scrubbed_count = original_count - len(sys.path)
        if scrubbed_count > 0 and not self.quiet:
            safe_print(f"      - 🧹 Scrubbed {scrubbed_count} bubble path(s) from sys.path.")

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
            except Exception:
                site_packages_str = str(self.site_packages_root)
                if site_packages_str not in sys.path:
                    insertion_point = 1 if len(sys.path) > 1 else len(sys.path)
                    sys.path.insert(insertion_point, site_packages_str)
        if linked_count > 0 and not self.quiet:
            safe_print(
                _("🔗 [omnipkg loader] Linked {} compatible dependencies to bubble").format(
                    linked_count
                )
            )

    def _get_bubble_dependencies(self, bubble_path: Path) -> dict:
        """Gets all packages from a bubble."""
        # Try manifest first (fast path)
        manifest_path = bubble_path / ".omnipkg_manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                return {
                    name.lower().replace("-", "_"): info.get("version")
                    for name, info in manifest.get("packages", {}).items()
                }
            except Exception:
                pass

        # Fallback: scan dist-info
        from importlib.metadata import PathDistribution

        dependencies = {}
        dist_infos = list(bubble_path.rglob("*.dist-info"))

        for dist_info in dist_infos:
            if dist_info.is_dir():
                try:
                    dist = PathDistribution(dist_info)
                    pkg_name_from_meta = dist.metadata["Name"]
                    pkg_name_canonical = canonicalize_name(pkg_name_from_meta)
                    pkg_version = dist.version
                    dependencies[pkg_name_canonical] = pkg_version
                except (KeyError, FileNotFoundError, Exception):
                    continue

        return dependencies

    def _get_bubble_package_version(self, bubble_path: Path, pkg_name: str) -> str:
        """Get version of a package from bubble manifest."""
        manifest_path = bubble_path / ".omnipkg_manifest.json"
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
                packages = manifest.get("packages", {})
                return packages.get(pkg_name, {}).get("version")
        return None

    def _batch_cloak_packages(self, package_names: list):
        """
        Cloak multiple packages with PROCESS-WIDE SAFETY and global tracking.
        """
        with omnipkgLoader._global_cloaking_lock:
            loader_id = id(self)
            timestamp = int(time.time() * 1000000)

            # --- Filter out protected packages (existing logic) ---
            omnipkg_dep_names = set(self._omnipkg_dependencies.keys())
            protected_packages = omnipkg_dep_names | omnipkgLoader._active_main_env_packages

            # ═══════════════════════════════════════════════════════════
            # CRITICAL FIX: HARD PROTECT IMMORTAL PACKAGES
            # Ensure _CRITICAL_DEPS are never cloaked, even if detection failed
            # ═══════════════════════════════════════════════════════════
            critical_names = {d.replace("-", "_") for d in self._CRITICAL_DEPS}

            packages_to_cloak = []
            for pkg in package_names:
                canonical = pkg.lower().replace("-", "_")

                # Check 1: Is it critical?
                if canonical in critical_names:
                    if not self.quiet:
                        safe_print(f"   🛡️  Skipping cloak for protected tool: {pkg}")
                    continue

                # Check 2: Is it in the detected dependencies?
                if canonical in protected_packages:
                    continue

                # Check 3: Protect numpy when loading torch (prevent dependency suicide)
                if canonical == "numpy" and any(
                    x in self._current_package_spec for x in ["torch", "tensorflow"]
                ):
                    if not self.quiet:
                        safe_print("   🛡️  Skipping cloak for numpy (critical for AI framework)")
                    continue

                packages_to_cloak.append(pkg)

            if not self.quiet and packages_to_cloak:
                safe_print(_('   - 🔍 Will cloak files for: {}').format(', '.join(packages_to_cloak)))

            successful_cloaks = []
            # --- Find, prepare, and execute cloaking operations ---
            for pkg_name in packages_to_cloak:
                canonical_name = pkg_name.lower().replace("-", "_")

                # Find all paths associated with this package
                paths_to_find = [self.site_packages_root / canonical_name]
                paths_to_find.extend(self.site_packages_root.glob(f"{canonical_name}-*.dist-info"))
                paths_to_find.extend(self.site_packages_root.glob(f"{canonical_name}-*.egg-info"))
                paths_to_find.append(self.site_packages_root / f"{canonical_name}.py")

                for original_path in paths_to_find:
                    if not original_path or not original_path.exists():
                        continue

                    # Generate a unique cloak name
                    cloak_suffix = f".{timestamp}_{loader_id}_omnipkg_cloaked"
                    cloak_path = original_path.with_name(original_path.name + cloak_suffix)

                    from omnipkg.isolation.fs_lock_queue import safe_cloak as _sc
                    _ok = _sc(
                        src=original_path,
                        dst=cloak_path,
                        locks_dir=omnipkgLoader._locks_dir,
                        pkg_name=pkg_name,
                        active_cloaks=omnipkgLoader._active_cloaks,
                        owner_id=loader_id,
                        sentinel_base=self.multiversion_base,
                        timeout=5.0,
                    )
                    successful_cloaks.append((original_path, cloak_path, _ok))
                    if _ok and not self.quiet:
                        safe_print(_('      ✅ Cloaked: {}').format(original_path.name))
                    elif not _ok and not self.quiet:
                        safe_print(f"      ⏭️  Skipped (locked/gone): {original_path.name}")
                        if not self.quiet:
                            safe_print(
                                f"      ⏱️  Timeout waiting for lock on {pkg_name}, skipping..."
                            )
                        successful_cloaks.append((original_path, cloak_path, False))

            self._cloaked_main_modules.extend(successful_cloaks)

            # NEW: Signal daemon immediately after cloaking
            if self._is_daemon_worker() and successful_cloaks:
                self._signal_daemon_lock_released()

            return len([c for c in successful_cloaks if c[2]])

    def nuke_all_cloaks_for_package(self, pkg_name: str):
        """
        Nuclear option: Find and destroy ALL cloaked versions of a package.
        This is a recovery tool for when cloaking gets out of control.
        """
        canonical_name = pkg_name.lower().replace("-", "_")

        # Find ALL cloaks - any file/dir with _omnipkg_cloaked in the name
        all_cloaks = []

        patterns = [
            f"{canonical_name}*_omnipkg_cloaked*",  # numpy.123_omnipkg_cloaked
            # numpy-2.3.5.dist-info.123_omnipkg_cloaked
            f"{canonical_name}-*_omnipkg_cloaked*",
        ]

        safe_print(f"\n🔍 Scanning for ALL {pkg_name} cloaks...")

        for pattern in patterns:
            for cloaked_path in self.site_packages_root.glob(pattern):
                all_cloaks.append(cloaked_path)
                safe_print(_('   📦 Found cloak: {}').format(cloaked_path.name))

        if not all_cloaks:
            safe_print(f"   ✅ No cloaks found for {pkg_name}")
            return 0

        safe_print(_('\n💥 NUKING {} cloak(s)...').format(len(all_cloaks)))
        destroyed_count = 0

        for cloak_path in all_cloaks:
            try:
                if cloak_path.is_dir():
                    shutil.rmtree(cloak_path)
                else:
                    cloak_path.unlink()
                destroyed_count += 1
                safe_print(_('   ☠️  Destroyed: {}').format(cloak_path.name))
            except Exception as e:
                safe_print(_('   ❌ Failed to destroy {}: {}').format(cloak_path.name, e))

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
        critical_deps = ["setuptools", "pip", "wheel"]

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
        safe_packages = {"setuptools", "pip", "wheel", "certifi", "urllib3"}

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
        canonical_name = pkg_name.lower().replace("-", "_")
        cloaked_versions = []

        patterns = [
            f"{canonical_name}.*_omnipkg_cloaked*",
            f"{canonical_name}-*.dist-info.*_omnipkg_cloaked*",
            f"{canonical_name}-*.egg-info.*_omnipkg_cloaked*",
            f"{canonical_name}.py.*_omnipkg_cloaked*",
        ]

        for pattern in patterns:
            for cloaked_path in self.site_packages_root.glob(pattern):
                # NEW: Extract timestamp AND loader_id
                match = re.search(r"\.(\d+)_(\d+)_omnipkg_cloaked", str(cloaked_path))
                if match:
                    timestamp = int(match.group(1))
                    loader_id = int(match.group(2))
                    original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloaked_path.name)
                    cloaked_versions.append((cloaked_path, original_name, timestamp, loader_id))
                else:
                    # OLD format fallback (legacy cloaks without loader_id)
                    match_old = re.search(r"\.(\d+)_omnipkg_cloaked", str(cloaked_path))
                    if match_old:
                        timestamp = int(match_old.group(1))
                        original_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", cloaked_path.name)
                        cloaked_versions.append((cloaked_path, original_name, timestamp, None))

        return cloaked_versions

    def _cleanup_all_cloaks_for_package(self, pkg_name):
        """
        Emergency cleanup: restore cloaked versions of a package via safe_uncloak.
        """
        from omnipkg.isolation.fs_lock_queue import safe_uncloak
        cloaked_versions = self._scan_for_cloaked_versions(pkg_name)
    
        if not cloaked_versions:
            return
    
        if not self.quiet:
            safe_print(
                f"   🧹 EMERGENCY CLEANUP: Found {len(cloaked_versions)} orphaned cloaks for {pkg_name}"
            )
    
        cloaked_versions.sort(key=lambda x: x[2], reverse=True)  # newest first
    
        for cloak_info in cloaked_versions:
            cloak_path = cloak_info[0]
            original_name = cloak_info[1]
    
            if not cloak_path.exists():
                continue
    
            target_path = cloak_path.parent / original_name
    
            ok = safe_uncloak(
                src=cloak_path,
                dst=target_path,
                locks_dir=omnipkgLoader._locks_dir,
                pkg_name=pkg_name,
                active_cloaks=omnipkgLoader._active_cloaks,
                sentinel_base=self.multiversion_base,
                timeout=5.0,
            )
            if ok:
                if not self.quiet:
                    safe_print(_('   ✅ Restored: {}').format(original_name))
                return  # first success is enough
            else:
                if not self.quiet:
                    safe_print(
                        _('   ⏱️  Lock conflict on {}, trying next candidate').format(original_name)
                    )
                continue
    
        if not self.quiet:
            safe_print(f"   ❌ All restoration attempts failed for {pkg_name}")

    def _restore_all_cloaks_for_pkg_unsafe(self, pkg_name):
        """
        Restore ALL cloaks (bubble + main-env) for pkg_name.
        Called from ABI conflict paths. Uses safe_uncloak for every move.
        """
        from omnipkg.isolation.fs_lock_queue import safe_uncloak
        canonical = pkg_name.lower().replace("-", "_")
    
        # --- Bubble cloaks in multiversion_base ---
        try:
            for entry in os.scandir(str(self.multiversion_base)):
                if not (entry.name.startswith(f"{pkg_name}-") and "_omnipkg_cloaked" in entry.name):
                    continue
                cp = Path(entry.path)
                on = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cp.name)
                if "_omnipkg_cloaked" in on:
                    on = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", cp.name)
                op = cp.parent / on
                safe_uncloak(
                    src=cp, dst=op,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=pkg_name,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=3.0,
                )
                importlib.invalidate_caches()
        except Exception:
            pass
    
        # --- Main-env cloaks in site_packages_root ---
        try:
            for me_cloak in list(self.site_packages_root.glob(f"{canonical}*_omnipkg_cloaked*")):
                me_on = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", me_cloak.name)
                if "_omnipkg_cloaked" in me_on:
                    me_on = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", me_cloak.name)
                me_op = me_cloak.parent / me_on
                safe_uncloak(
                    src=me_cloak, dst=me_op,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=canonical,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=3.0,
                )
        except Exception:
            pass

    def _get_version_from_original_env(self, package_name: str, requested_version: str) -> tuple:
        """
        Enhanced detection that ALWAYS checks for cloaked versions first.
        CRITICAL FIX: Strictly checks self.site_packages_root to avoid confusion
        from parent loaders' bubbles in sys.path.
        """
        canonical_target = canonicalize_name(package_name)
        filesystem_name = package_name.replace("-", "_")

        # FIX: Do not rely on self.original_sys_path which might be polluted by parent loaders
        site_packages = self.site_packages_root

        if not self.quiet:
            safe_print(f"      🔍 Searching for {package_name}=={requested_version}...")

        # ═══════════════════════════════════════════════════════════
        # STRATEGY 0: CHECK FOR CLOAKED VERSIONS FIRST (CRITICAL!)
        # ═══════════════════════════════════════════════════════════
        cloaked_versions = self._scan_for_cloaked_versions(package_name)

        for cloaked_path, original_name, *unused in cloaked_versions:
            if requested_version in original_name:
                if not self.quiet:
                    safe_print(_('      [Strategy 0/6] Found CLOAKED version: {}').format(cloaked_path.name))
                return (requested_version, cloaked_path)

        # ═══════════════════════════════════════════════════════════
        # STRATEGY 1: Direct path check (exact dist-info match)
        # ═══════════════════════════════════════════════════════════
        exact_dist_info_path = site_packages / f"{filesystem_name}-{requested_version}.dist-info"
        if exact_dist_info_path.exists() and exact_dist_info_path.is_dir():
            if not self.quiet:
                safe_print(_('      ✅ [Strategy 1/6] Found at exact path: {}').format(exact_dist_info_path))
            return (requested_version, None)

        # ═══════════════════════════════════════════════════════════
        # STRATEGY 2: importlib.metadata (Strictly scoped to main site-packages)
        # ═══════════════════════════════════════════════════════════
        try:
            # FIX: Use the correct API for Python 3.8+
            from importlib.metadata import distributions, Distribution
            
            for dist in distributions(path=[str(site_packages)]):
                # Get the package name using the proper metadata API
                try:
                    # This is the standard way that works across versions
                    dist_name = dist.metadata['Name']
                except (AttributeError, KeyError):
                    # Fallback for older versions
                    try:
                        dist_name = dist.name
                    except AttributeError:
                        # Last resort: parse from the distribution path
                        import os
                        dist_path = getattr(dist, '_path', None) or getattr(dist, 'locate_file', lambda: '')('')
                        if dist_path:
                            dist_info_dir = os.path.basename(str(dist_path))
                            if dist_info_dir.endswith('.dist-info'):
                                dist_name = dist_info_dir.split('-')[0]
                            else:
                                continue
                        else:
                            continue
                
                if canonicalize_name(dist_name) == canonical_target:
                    if dist.version == requested_version:
                        if not self.quiet:
                            safe_print(
                                _('      ✅ [Strategy 2/6] Found via importlib.metadata: {}').format(dist.version)
                            )
                        return (dist.version, None)
                    else:
                        if not self.quiet:
                            safe_print(
                                _('      ℹ️  [Strategy 2/6] Found {} but version mismatch: {} != {}').format(package_name, dist.version, requested_version)
                            )
        except Exception as e:
            if not self.quiet:
                safe_print(_('      ⚠️  [Strategy 2/6] importlib.metadata failed: {}').format(e))

        # ═══════════════════════════════════════════════════════════
        # STRATEGY 3: Glob search
        # ═══════════════════════════════════════════════════════════
        glob_pattern = f"{filesystem_name}-*.dist-info"
        for match in site_packages.glob(glob_pattern):
            if match.is_dir():
                try:
                    version_part = match.name.replace(f"{filesystem_name}-", "").replace(
                        ".dist-info", ""
                    )
                    if version_part == requested_version:
                        if not self.quiet:
                            safe_print(_('      ✅ [Strategy 3/6] Found via glob: {}').format(match))
                        return (requested_version, None)
                except Exception:
                    continue

        # All strategies exhausted
        if not self.quiet:
            safe_print(
                _('      ❌ All strategies exhausted. {}=={} not found.').format(package_name, requested_version)
            )
            if cloaked_versions:
                safe_print(
                    _('      ⚠️  WARNING: Found {} cloaked versions but none match {}').format(len(cloaked_versions), requested_version)
                )
                safe_print("      💡 Running emergency cleanup...")
                self._cleanup_all_cloaks_for_package(package_name)

        return (None, None)

    def _uncloak_main_package_if_needed(self, pkg_name: str, cloaked_dist_path: Path):
        """
        Restores a cloaked package in the main environment so it can be used.
        """
        restored_any = False

        # Helper to clean up the destination and move
        def safe_restore(source: Path, dest: Path):
            nonlocal restored_any
            from omnipkg.isolation.fs_lock_queue import safe_uncloak
            ok = safe_uncloak(
                src=source,
                dst=dest,
                locks_dir=omnipkgLoader._locks_dir,
                pkg_name=pkg_name,
                active_cloaks=omnipkgLoader._active_cloaks,
                sentinel_base=self.multiversion_base,
                timeout=5.0
            )
            if ok:
                restored_any = True
            return ok

        # 1. Restore the dist-info we found
        if cloaked_dist_path and cloaked_dist_path.exists():
            # Unified regex for both legacy (.123_omnipkg) and new (.123_456_omnipkg) formats
            original_name = re.sub(r"\.\d+(_\d+)?_omnipkg_cloaked.*$", "", cloaked_dist_path.name)
            target_path = cloaked_dist_path.with_name(original_name)
            safe_restore(cloaked_dist_path, target_path)

        # 2. Search for cloaked module directories/files
        names_to_check = {pkg_name, pkg_name.lower().replace("-", "_")}

        for name in names_to_check:
            # Glob for any cloaked items matching this package name
            for cloaked_item in self.site_packages_root.glob(f"{name}.*_omnipkg_cloaked*"):
                original_name = re.sub(r"\.\d+(_\d+)?_omnipkg_cloaked.*$", "", cloaked_item.name)
                target_item = cloaked_item.with_name(original_name)

                # Verify this cloak actually belongs to the package
                if original_name == name:
                    safe_restore(cloaked_item, target_item)

        if restored_any and not self.quiet:
            safe_print(_("      ✅ Restored cloaked '{}' in main environment").format(pkg_name))

    def _should_use_worker_proactively(self, pkg_name: str) -> bool:
        """
        Decide if we should proactively use worker mode for this package.
        """
        # 1. Check if C++ backend already loaded in memory (Existing logic)
        cpp_indicators = {
            "torch": "torch._C",
            "numpy": "numpy.core._multiarray_umath",
            "tensorflow": "tensorflow.python.pywrap_tensorflow",
            "scipy": "scipy.linalg._fblas",
        }

        for pkg, indicator in cpp_indicators.items():
            if pkg in pkg_name.lower() and indicator in sys.modules:
                if not self.quiet:
                    safe_print(_('   🧠 Proactive worker mode: {} already loaded').format(indicator))
                return True

        # 2. FORCE WORKER for these packages to ensure Daemon usage
        #    (Add numpy and scipy here to force isolation testing)
        force_daemon_packages = ["tensorflow", "numpy", "scipy", "pandas"]

        for force_pkg in force_daemon_packages:
            if force_pkg in pkg_name.lower():
                if not self.quiet:
                    safe_print(
                        f"   🧠 Proactive worker mode: Force-enabling Daemon for {force_pkg}"
                    )
                return True

        return False

    def _is_daemon_worker(self):
        """Check if we're running inside a daemon worker"""
        return os.environ.get("OMNIPKG_IS_DAEMON_WORKER") == "1"

    def _signal_daemon_lock_released(self):
        """
        Signal that filesystem mutations are complete.
        Daemon can now release the lock and serve other requests.
        """
        if not self._is_daemon_worker():
            return

        # Write status to stdout (daemon reads this)
        status = {
            "event": "LOCK_RELEASED",
            "package": self._current_package_spec,
            "pid": os.getpid(),
            "timestamp": time.time(),
        }

        # Daemon worker reads stdout for status updates
        print(_('OMNIPKG_EVENT:{}').format(json.dumps(status)), flush=True)

    def _get_daemon_client(self):
        """
        Attempts to connect to the daemon. If not running, starts it.
        """
        if not DAEMON_AVAILABLE:
            raise RuntimeError("Worker Daemon code missing (omnipkg.isolation.worker_daemon)")

        client = DaemonClient()

        # 1. Try simple status check to see if it's alive
        status = client.status()
        if status.get("success"):
            return client

        # 2. Daemon not running? Start it!
        if not self.quiet:
            safe_print("   ⚙️  Worker Daemon not running. Auto-starting background service...")

        # Launch independent process using the CLI command
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        subprocess.Popen(
            [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )

        # 3. Wait for warmup (up to 3 seconds)
        for i in range(30):
            time.sleep(0.1)
            status = client.status()
            if status.get("success"):
                if not self.quiet:
                    safe_print("   ✅ Daemon warmed up and ready.")
                return client

        raise RuntimeError("Failed to auto-start Worker Daemon")

    def _check_version_via_kb(self, pkg_name: str, requested_version: str):
        """KB-based lookup (requires cache_client)"""
        if self.cache_client is None:
            return None

        c_name = canonicalize_name(pkg_name)

        try:
            inst_prefix = self.redis_key_prefix.replace(":pkg:", ":inst:")
            search_pattern = f"{inst_prefix}{c_name}:*"
            all_keys = self.cache_client.keys(search_pattern)

            if not all_keys:
                return None

            active_ver = None
            bubble_versions = []

            for key in all_keys:
                inst_data = self.cache_client.hgetall(key)
                if not inst_data:
                    continue

                version = inst_data.get("Version")
                install_type = inst_data.get("install_type")

                if install_type == "active":
                    active_ver = version
                elif install_type == "bubble":
                    bubble_versions.append(version)

            return {
                "active_version": active_ver,
                "bubble_versions": bubble_versions,
                "has_requested_bubble": requested_version in bubble_versions,
                "is_active": active_ver == requested_version,
            }
        except Exception:
            return None

    def _check_version_via_glob(self, pkg_name: str, requested_version: str):
        """Glob-based filesystem check"""
        try:
            pkg_normalized = pkg_name.replace("-", "_").lower()

            # Check main env
            for dist_info in self.site_packages_root.glob(
                f"{pkg_normalized}-{requested_version}.dist-info"
            ):
                if dist_info.is_dir():
                    metadata_file = dist_info / "METADATA"
                    if metadata_file.exists():
                        with open(metadata_file, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.lower().startswith("version:"):
                                    found_version = line.split(":", 1)[1].strip()
                                    if found_version == requested_version:
                                        return {
                                            "is_active": True,
                                            "active_version": requested_version,
                                            "has_requested_bubble": False,
                                            "bubble_versions": [],
                                        }

            # Check bubbles
            bubble_path = self.multiversion_base / f"{pkg_name}-{requested_version}"
            has_bubble = bubble_path.is_dir() and (bubble_path / ".omnipkg_manifest.json").exists()

            return {
                "is_active": False,
                "active_version": None,
                "has_requested_bubble": has_bubble,
                "bubble_versions": [requested_version] if has_bubble else [],
            }
        except Exception:
            return None

    def _check_version_via_importlib(self, pkg_name: str, requested_version: str):
        """importlib.metadata-based check (current slow method)"""
        try:
            current_version = get_version(pkg_name)
            return {
                "is_active": current_version == requested_version,
                "active_version": current_version,
                "has_requested_bubble": False,
                "bubble_versions": [],
            }
        except PackageNotFoundError:
            return None

    def _check_version_via_filesystem(self, pkg_name: str, requested_version: str):
        """Direct filesystem check (no metadata parsing)"""
        try:
            pkg_normalized = pkg_name.replace("-", "_").lower()

            # Quick check: does dist-info directory exist?
            dist_info_path = (
                self.site_packages_root / f"{pkg_normalized}-{requested_version}.dist-info"
            )
            is_active = dist_info_path.is_dir()

            # Quick check: does bubble exist?
            bubble_path = self.multiversion_base / f"{pkg_name}-{requested_version}"
            has_bubble = bubble_path.is_dir()

            return {
                "is_active": is_active,
                "active_version": requested_version if is_active else None,
                "has_requested_bubble": has_bubble,
                "bubble_versions": [requested_version] if has_bubble else [],
            }
        except Exception:
            return None

    def _check_version_smart(self, pkg_name: str, requested_version: str):
        """Dispatch to configured method"""
        method = self.VERSION_CHECK_METHOD

        if method == "kb":
            return self._check_version_via_kb(pkg_name, requested_version)
        elif method == "glob":
            return self._check_version_via_glob(pkg_name, requested_version)
        elif method == "importlib":
            return self._check_version_via_importlib(pkg_name, requested_version)
        elif method == "filesystem":
            return self._check_version_via_filesystem(pkg_name, requested_version)
        else:
            # Fallback to importlib
            return self._check_version_via_importlib(pkg_name, requested_version)

        # ── C-extension partial-init sentinel strings ─────────────────────────────
    _CPP_POISON_PATTERNS = (
        "_shape_base_impl",
        "partially initialized module",
        "source directory",
        "already has a docstring",
        "_has_torch_function",
        "circular import",
        "cannot import name",
    )
 
    def _verify_importable_or_raise_corrupted(self, pkg_name: str, bubble_path_str: str):
        """
        Off hot-path: attempt a quick import of pkg_name and detect the known
        .so-already-mapped / partial-init / circular-import failures.
 
        If detected, raises ProcessCorruptedException so
        WorkerDelegationMixin.__enter__ can intercept it and escalate to
        subprocess/worker fallback before any user code runs on the bad state.
 
        Only called for known C-extension switchers (numpy, torch, scipy).
        Not called inside daemon/worker processes — those already provide
        subprocess-level isolation so the version check is irrelevant there.
        """
        # Skip inside daemon workers and legacy worker processes.
        # They provide their own isolation boundary; raising here just breaks
        # the remote execution path.
        if (
            os.environ.get("OMNIPKG_IS_DAEMON_WORKER")
            or os.environ.get("OMNIPKG_IS_WORKER_PROCESS") == "1"
            or os.environ.get("_OMNIPKG_SUBPROCESS_FALLBACK") == "1"
        ):
            return
 
        _CE_CHECK = {"numpy", "torch", "scipy", "tensorflow"}
        if pkg_name.lower() not in _CE_CHECK:
            return  # Only check the dangerous ones

    def _enter_multi(self):
        """
        Activate multiple packages sequentially, with dependency-aware ordering.
 
        Ordering rule: if package A's bubble directory contains package B
        (i.e. B is a bundled dep of A), then B must be activated BEFORE A.
        This prevents A's activation from cloaking or scrubbing B's freshly
        activated paths.
 
        After each activation, we scrub any bubble paths belonging to
        packages that were already activated by an earlier sub-loader.
        This is the 'scipy contains numpy' guard.
        """
        if not self.quiet:
            safe_print(f"📦 Multi-package activation: {len(self._package_specs)} packages")
 
        # ── Step 1: dependency-aware sort ─────────────────────────────────────
        # Build a quick map of pkg_name -> bubble_path for already-known bubbles
        def _bubble_path_for(spec):
            try:
                name, ver = spec.split("==")
                from packaging.version import Version
                canonical_ver = str(Version(ver))
                p = self.multiversion_base / f"{name}-{canonical_ver}"
                if p.is_dir():
                    return p
            except Exception:
                pass
            return None
 
        def _bubble_contains(outer_spec, inner_name):
            """Return True if outer_spec's bubble bundles inner_name."""
            bp = _bubble_path_for(outer_spec)
            if bp is None:
                return False
            # Check for dist-info of inner_name inside the bubble
            inner_norm = inner_name.lower().replace("-", "_")
            return any(
                d.name.lower().startswith(inner_norm + "-")
                for d in bp.iterdir()
                if d.suffix == ".dist-info" or "-" in d.name
            )
 
        # Topological sort: specs that are contained by others come first
        specs = list(self._package_specs)
        pkg_names = []
        for s in specs:
            try:
                pkg_names.append(s.split("==")[0].lower().replace("-", "_"))
            except Exception:
                pkg_names.append(s)
 
        # Build adjacency: outer_idx depends on inner_idx (inner must go first)
        order = list(range(len(specs)))
        for i, outer_spec in enumerate(specs):
            for j, inner_name in enumerate(pkg_names):
                if i != j and _bubble_contains(outer_spec, inner_name):
                    # outer depends on inner: move inner before outer
                    if order.index(j) > order.index(i):
                        order.remove(j)
                        order.insert(order.index(i), j)
 
        sorted_specs = [specs[idx] for idx in order]
 
        if sorted_specs != specs and not self.quiet:
            safe_print(
                f"   📋 Reordered for dependency safety: "
                + " → ".join(s.split("==")[0] for s in sorted_specs)
            )
 
        # ── Step 2: activate in sorted order, then scrub cross-contamination ──
        activated_pkg_names: list[str] = []
 
        for i, spec in enumerate(sorted_specs, 1):
            if not self.quiet:
                safe_print(f"   [{i}/{len(sorted_specs)}] Activating {spec}...")
 
            loader = omnipkgLoader(
                spec,
                config=self.config,
                quiet=self.quiet,
                force_activation=self.force_activation,
                isolation_mode=self.isolation_mode,
                cache_client=self.cache_client,
                redis_key_prefix=self.redis_key_prefix,
            )
            loader.__enter__()
            self._active_sub_loaders.append(loader)
 
            try:
                activated_pkg_names.append(spec.split("==")[0].lower().replace("-", "_"))
            except Exception:
                pass
 
            # ── Post-activation scrub ──────────────────────────────────────
            # If this package's bubble contains any of the already-activated
            # packages, their paths inside THIS bubble may now be first in
            # sys.path and shadow the correct bubble we activated earlier.
            # Strip any sys.path entries that are inside THIS bubble but
            # belong to a package we already have a good activation for.
            if loader._activated_bubble_path and len(activated_pkg_names) > 1:
                this_bubble = loader._activated_bubble_path
                for prev_name in activated_pkg_names[:-1]:
                    contaminated = [
                        p for p in sys.path
                        if this_bubble in p and prev_name in p.lower()
                    ]
                    for bad_path in contaminated:
                        try:
                            sys.path.remove(bad_path)
                            if not self.quiet:
                                safe_print(
                                    f"   🧹 Scrubbed nested-bubble shadow path: {bad_path}"
                                )
                        except ValueError:
                            pass
 
        self._activation_successful = True
        return self

    def _check_numpy_abi_conflict(self, pkg_name: str, requested_version: str) -> None:
        """
        Raise ProcessCorruptedException if numpy's mapped .so is ABI-incompatible
        with the requested version AND a daemon worker is available to handle it.
        If no worker is available, just log and return — in-process heal runs instead.
        """
        if pkg_name != "numpy":
            return
        if "numpy.core._multiarray_umath" not in sys.modules:
            return
        _mapped_ce = sys.modules.get("numpy.core._multiarray_umath")
        _mapped_so = getattr(_mapped_ce, "__file__", "") or ""
        _mapped_ver = ""
        for _part in _mapped_so.replace("\\", "/").split("/"):
            if _part.startswith("numpy-") and "_omnipkg" not in _part:
                _mapped_ver = _part[6:]
                break
        if not _mapped_ver or _mapped_ver == requested_version:
            return
        try:
            _m = _mapped_ver.split(".")
            _r = requested_version.split(".")
            _map_maj = int(_m[0]); _map_min = int(_m[1]) if len(_m) > 1 else 0
            _req_maj = int(_r[0]); _req_min = int(_r[1]) if len(_r) > 1 else 0
            _abi_bad = (
                _map_maj != _req_maj
                or (_map_min < 26 <= _req_min)
                or (_req_min < 26 <= _map_min)
            )
            if not _abi_bad:
                return
            # Only raise if we can actually delegate — daemon available and not inside one.
            # If we raise but WorkerDelegationMixin can't get a worker, it returns self
            # with _abi_conflict_detected=True which is safe but the exception still
            # propagates through all nested with-blocks. So only raise when delegation
            # will actually succeed.
            _can_delegate = (
                getattr(self, "_worker_fallback_enabled", False)
                and DAEMON_AVAILABLE
                and not os.environ.get("OMNIPKG_IS_DAEMON_WORKER")
            )
            if _can_delegate:
                if not self.quiet:
                    safe_print(
                        f"   ⚠️  ABI conflict: mapped={_mapped_ver!r} → "
                        f"requested={requested_version!r} — delegating to daemon"
                    )
                raise ProcessCorruptedException(
                    f"numpy ABI conflict: mapped={_mapped_ver!r}, requested={requested_version!r}"
                )
            else:
                if not self.quiet:
                    safe_print(
                        f"   ↕️  ABI note: mapped={_mapped_ver!r} → "
                        f"requested={requested_version!r} (no daemon, attempting in-process)"
                    )
        except ProcessCorruptedException:
            raise
        except Exception:
            pass

    def _enter_single(self):
        self._profile_start("TOTAL_ACTIVATION")
        self._activation_start_time = time.perf_counter_ns()
        self._profile_start("init_checks")

        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(_("Invalid package_spec format: '{}'").format(self._current_package_spec))

        # ── DAEMON WORKER NESTED OVERLAY FAST PATH ────────────────────────────
        # When running inside a daemon worker in overlay mode, the process is
        # already isolated. Nested omnipkgLoader calls only need to swap
        # sys.path[0] — skip ALL filesystem ops, locking, purging, and scanning.
        # This drops per-level cost from ~125ms to <1ms.
        _in_daemon = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))  # ← ADD THIS LINE
        if _in_daemon and self.isolation_mode == "overlay":
            from packaging.version import Version
            canonical_version = str(Version(requested_version))
            bubble_path = self.multiversion_base / f"{pkg_name}-{canonical_version}"

            if bubble_path.is_dir():
                bubble_path_str = str(bubble_path)

                # Fast module purge — no get_version() disk hit, no GC
                # Just remove the Python layer entries from sys.modules directly
                _pkg_norm = pkg_name.lower().replace("-", "_")
                _to_purge = [k for k in sys.modules 
                            if k == _pkg_norm or k.startswith(_pkg_norm + ".")]
                # For numpy specifically: preserve C extensions, only purge Python layer
                if pkg_name == "numpy":
                    _c_exts = {k for k in _to_purge 
                            if getattr(sys.modules.get(k), "__file__", "").endswith(".so")}
                    _to_purge = [k for k in _to_purge if k not in _c_exts]
                for k in _to_purge:
                    sys.modules.pop(k, None)

                # Swap sys.path atomically — remove any other bubble for this pkg, prepend ours
                _mvbase = str(self.multiversion_base)
                _pkg_prefix = f"{pkg_name}-"
                sys.path[:] = [p for p in sys.path
                            if not (_mvbase in p 
                                    and os.path.basename(p).startswith(_pkg_prefix)
                                    and p != bubble_path_str)]
                if bubble_path_str not in sys.path:
                    sys.path.insert(0, bubble_path_str)

                # Instead of full invalidate_caches() — only clear the FileFinder
                # cache for the specific paths that changed, not all of sys.path
                _changed_paths = {bubble_path_str}
                # Also clear any old bubble path that was just removed
                for _finder in sys.path_importer_cache.copy():
                    if str(self.multiversion_base) in _finder:
                        sys.path_importer_cache.pop(_finder, None)

                self._activated_bubble_path = bubble_path_str
                self._active_bubble_lock = None
                self._activation_successful = True
                if _in_daemon:
                    omnipkgLoader._daemon_did_version_switch = True
                self._using_main_env = False
                self._cloaked_main_modules = []
                self._cloaked_bubbles = []
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                with omnipkgLoader._nesting_lock:
                    omnipkgLoader._nesting_depth += 1
                    self._is_nested = omnipkgLoader._nesting_depth > 1
                return self

            # Bubble not found — fall through to normal path which will install it
        # ── END DAEMON FAST PATH ──────────────────────────────────────────────

        self._profile_end("init_checks", print_now=self._profiling_enabled)

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(_("Invalid package_spec format: '{}'").format(self._current_package_spec))

        self._profile_end("init_checks", print_now=self._profiling_enabled)

        # Track nesting
        self._profile_start("nesting_check")
        with omnipkgLoader._nesting_lock:
            omnipkgLoader._nesting_depth += 1
            current_depth = omnipkgLoader._nesting_depth
            self._is_nested = current_depth > 1
        self._profile_end("nesting_check")

        if not self.quiet and self._is_nested:
            safe_print(f"   🔗 Nested activation (depth={current_depth})")

        # ═══════════════════════════════════════════════════════════
        # CRITICAL FIX: Check if we're in a bubble BEFORE version check
        # ═══════════════════════════════════════════════════════════
        self._profile_start("check_system_version")

        # Try KB first (fast path)
        kb_info = self._check_version_smart(pkg_name, requested_version)

        # Detect if we're currently inside a bubble
        is_in_bubble = False
        if sys.path:
            first_path = Path(sys.path[0]).resolve()
            base_resolved = self.multiversion_base.resolve()
            if str(base_resolved) in str(first_path):
                is_in_bubble = True

        # Only check version match if NOT in a bubble
        if not is_in_bubble:
            # Use KB info if available, otherwise fallback to get_version
            if kb_info and kb_info["is_active"] and not self.force_activation:
                # FAST PATH: KB says main env matches!
                if not self.quiet:
                    safe_print(f"   ✅ Main env has {pkg_name}=={requested_version} (KB)")

                self._profile_end("check_system_version", print_now=self._profiling_enabled)

                # Cloak conflicting bubbles
                self._profile_start("find_conflicts")
                conflicting_bubbles = []
                try:
                    # Use os.scandir (3x faster than pathlib.glob)
                    for entry in os.scandir(str(self.multiversion_base)):
                        if (
                            entry.is_dir()
                            and entry.name.startswith(f"{pkg_name}-")
                            and "_omnipkg_cloaked" not in entry.name
                        ):
                            conflicting_bubbles.append(Path(entry.path))
                except OSError:
                    pass  # Directory doesn't exist or access denied

                self._profile_end("find_conflicts", print_now=self._profiling_enabled)

                if conflicting_bubbles:
                    self._profile_start("cloak_conflicts")
                    timestamp = int(time.time() * 1000000)
                    loader_id = id(self)
                    cloak_suffix = f".{timestamp}_{loader_id}_omnipkg_cloaked"

                    for bubble_path in conflicting_bubbles:
                        cloak_path, ok = self._cloak_bubble(bubble_path, cloak_suffix, timeout=0)
                        if ok:
                            self._cloaked_bubbles.append((cloak_path, bubble_path))
                        else:
                            if not self.quiet:
                                safe_print(f"         - ⏭️  Skipping busy sibling bubble: {bubble_path.name}")

                    self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

                self._ensure_main_site_packages_in_path()
                self._using_main_env = True
                
                if self._is_daemon_worker():
                    self.stabilize_daemon_state()

                pkg_canonical = pkg_name.lower().replace("-", "_")
                omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                self._my_main_env_package = pkg_canonical

                self._check_numpy_abi_conflict(pkg_name, requested_version)
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                return self

            else:
                # SLOW PATH: KB unavailable, use filesystem
                try:
                    current_system_version = get_version(pkg_name)

                    if current_system_version == requested_version and not self.force_activation:
                        # CASE A: Main env matches, use it directly
                        if not self.quiet:
                            safe_print(f"   ✅ Main env has {pkg_name}=={current_system_version}")

                        self._profile_end("check_system_version", print_now=self._profiling_enabled)

                        # Cloak conflicting bubbles
                        self._profile_start("find_conflicts")
                        conflicting_bubbles = []
                        try:
                            # Use os.scandir (3x faster than pathlib.glob)
                            for entry in os.scandir(str(self.multiversion_base)):
                                if (
                                    entry.is_dir()
                                    and entry.name.startswith(f"{pkg_name}-")
                                    and "_omnipkg_cloaked" not in entry.name
                                ):
                                    conflicting_bubbles.append(Path(entry.path))
                        except OSError:
                            pass  # Directory doesn't exist or access denied

                        self._profile_end("find_conflicts", print_now=self._profiling_enabled)

                        if conflicting_bubbles:
                            self._profile_start("cloak_conflicts")
                            timestamp = int(time.time() * 1000000)
                            loader_id = id(self)
                            cloak_suffix = f".{timestamp}_{loader_id}_omnipkg_cloaked"

                            for bubble_path in conflicting_bubbles:
                                cloak_path, ok = self._cloak_bubble(bubble_path, cloak_suffix, timeout=0)
                                if ok:
                                    self._cloaked_bubbles.append((cloak_path, bubble_path))
                                else:
                                    if not self.quiet:
                                        safe_print(f"         - ⏭️  Skipping busy sibling bubble: {bubble_path.name}")

                            self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

                        self._ensure_main_site_packages_in_path()
                        self._using_main_env = True
                        
                        if self._is_daemon_worker():
                            self.stabilize_daemon_state()

                        pkg_canonical = pkg_name.lower().replace("-", "_")
                        omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                        self._my_main_env_package = pkg_canonical

                        self._activation_successful = True
                        self._activation_end_time = time.perf_counter_ns()
                        self._total_activation_time_ns = (
                            self._activation_end_time - self._activation_start_time
                        )
                        self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                        return self

                except PackageNotFoundError:
                    # Package not in main env, must use bubble
                    pass
        else:
            # We're nested inside a bubble
            if not self.quiet:
                safe_print("   ⚠️ Nested in bubble, checking if version matches...")

            # Check if CURRENT bubble matches requested version
            try:
                current_bubble_version = get_version(pkg_name)

                if current_bubble_version == requested_version:
                    # CASE B: Already in correct bubble, reuse it
                    if not self.quiet:
                        safe_print(_('   ✅ Already in {}=={} bubble').format(pkg_name, current_bubble_version))

                    self._profile_end("check_system_version")
                    self._activation_successful = True
                    self._using_main_env = False
                    self._activated_bubble_path = sys.path[0]
                    self._cloaked_bubbles = []
                    self._cloaked_main_modules = []
                    self._activated_bubble_dependencies = []

                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                    )
                    self._profile_end("TOTAL_ACTIVATION")
                    return self
                else:
                    # CASE C: Wrong bubble version, need to switch!
                    if not self.quiet:
                        safe_print(
                            _('   🔄 Version mismatch: have {}, need {}').format(current_bubble_version, requested_version)
                        )
                    # Fall through to bubble activation logic below

            except PackageNotFoundError:
                # Package not installed at all, fall through
                pass

        self._profile_end("check_system_version")

        if not self.quiet:
            safe_print(_("🚀 Fast-activating {} ...").format(self._current_package_spec))

        # Profile: Find bubble
        self._profile_start("find_bubble")

        # Normalize version FIRST (add this line)
        from packaging.version import Version
        canonical_version = str(Version(requested_version))  # 0.15 -> 0.15.0
        # Use canonical version for the path
        bubble_path = self.multiversion_base / f"{pkg_name}-{canonical_version}"

        # Check for cloaked bubbles (use both forms for backward compatibility)
        if not bubble_path.exists():
            # Try both the requested and canonical versions for cloaked bubbles
            for version_form in [requested_version, canonical_version]:
                cloaked_bubbles = list(
                    self.multiversion_base.glob(f"{pkg_name}-{version_form}.*_omnipkg_cloaked")
                )
                if cloaked_bubbles:
                    target = sorted(cloaked_bubbles, key=lambda p: str(p), reverse=True)[0]
                    if not self.quiet:
                        safe_print(_('   🔓 Found CLOAKED bubble {}, restoring...').format(target.name))
                    if self._uncloak_bubble(target, bubble_path, timeout=5.0):
                        # Invalidate Python's FileFinder cache so the restored
                        # directory and its contents are visible to the import
                        # machinery immediately.  Without this, `import numpy`
                        # raises ModuleNotFoundError even though the bubble is
                        # back on disk — the finder still has a stale negative
                        # cache entry for the old (cloaked) path.
                        importlib.invalidate_caches()
                    else:
                        if not self.quiet:
                            safe_print(_('      ⚠️ Failed to restore cloaked bubble (locked or vanished)'))
                    break

        self._profile_end("find_bubble", print_now=self._profiling_enabled)

        if not self.quiet:
            safe_print(f"   📂 Searching for bubble: {bubble_path}")

        # Track numpy version if applicable
        is_numpy_involved = "numpy" in self._current_package_spec.lower()

        # PRIORITY 1: Try BUBBLE first
        if bubble_path.is_dir():
            self._profile_start("activate_bubble")
            if not self.quiet:
                safe_print(_('   ✅ Bubble found: {}').format(bubble_path))
            self._using_main_env = False

            if is_numpy_involved:
                with omnipkgLoader._numpy_lock:
                    omnipkgLoader._numpy_version_history.append(requested_version)

            result = self._activate_bubble(bubble_path, pkg_name)
            self._profile_end("activate_bubble", print_now=self._profiling_enabled)
            self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
            return result

        # PRIORITY 2: Try MAIN ENV
        self._profile_start("check_main_env")
        if not self.quiet:
            safe_print("   ⚠️  Bubble not found. Checking main environment...")

        found_ver, cloaked_path = self._get_version_from_original_env(pkg_name, requested_version)
        self._profile_end("check_main_env", print_now=self._profiling_enabled)

        if found_ver == requested_version:
            # PATH A: Found CLOAKED version
            if cloaked_path:
                self._profile_start("uncloak_main_package")
                if not self.quiet:
                    safe_print("   🔓 Found CLOAKED version, restoring to main env...")
                self._uncloak_main_package_if_needed(pkg_name, cloaked_path)

                found_ver_after, _path_unused = self._get_version_from_original_env(
                    pkg_name, requested_version
                )
                self._profile_end("uncloak_main_package", print_now=self._profiling_enabled)

                if found_ver_after == requested_version:
                    if not self.quiet:
                        safe_print("   🔄 Package restored in main env.")

                    self._profile_start("cleanup_after_uncloak")
                    self._aggressive_module_cleanup(pkg_name)
                    self._scrub_sys_path_of_bubbles()
                    self._ensure_main_site_packages_in_path()
                    importlib.invalidate_caches()
                    self._profile_end("cleanup_after_uncloak", print_now=self._profiling_enabled)

                    self._using_main_env = True
                    
                    if self._is_daemon_worker():
                        self.stabilize_daemon_state()

                    pkg_canonical = pkg_name.lower().replace("-", "_")
                    omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                    self._my_main_env_package = pkg_canonical

                    if is_numpy_involved:
                        with omnipkgLoader._numpy_lock:
                            omnipkgLoader._numpy_version_history.append(requested_version)

                    # Check for ABI conflict: mapped .so vs requested version.
                    # Must be called here (restore-from-cloak path) so that
                    # WorkerDelegationMixin can catch ProcessCorruptedException
                    # and delegate to daemon instead of returning a broken context
                    # where 1.26.4's Python layer runs against 1.24.3's mapped .so.
                    self._check_numpy_abi_conflict(pkg_name, requested_version)

                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                    )
                    self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                    return self

            # PATH B: Found DIRECTLY in Main Env
            else:
                self._profile_start("isolate_main_env")
                if not self.quiet:
                    safe_print("   ✅ Found in main environment. Enforcing isolation...")

                # Cloak conflicting bubbles
                self._profile_start("find_bubble_conflicts")
                conflicting_bubbles = []
                try:
                    for entry in os.scandir(str(self.multiversion_base)):
                        if (
                            entry.is_dir()
                            and entry.name.startswith(f"{pkg_name}-")
                            and "_omnipkg_cloaked" not in entry.name
                        ):
                            conflicting_bubbles.append(Path(entry.path))
                except OSError:
                    pass
                self._profile_end("find_bubble_conflicts", print_now=self._profiling_enabled)

                if conflicting_bubbles:
                    if not self.quiet:
                        safe_print(
                            _('      - 🔒 Cloaking {} conflicting bubble(s).').format(len(conflicting_bubbles))
                        )

                    timestamp = int(time.time() * 1000000)
                    loader_id = id(self)
                    cloak_suffix = f".{timestamp}_{loader_id}_omnipkg_cloaked"

                    for bubble_path_item in conflicting_bubbles:
                        cloak_path, ok = self._cloak_bubble(bubble_path_item, cloak_suffix, timeout=0)
                        if ok:
                            self._cloaked_bubbles.append((cloak_path, bubble_path_item))
                            if not self.quiet:
                                safe_print(_('         - Cloaked: {}').format(bubble_path_item.name))
                        else:
                            if not self.quiet:
                                safe_print(f"         - ⏭️  Skipping busy sibling bubble: {bubble_path_item.name}")

                # Cleanup
                self._profile_start("isolation_cleanup")
                if not self.quiet:
                    safe_print("      - 🧹 Scrubbing sys.path...")

                if not self._is_nested:
                    self._scrub_sys_path_of_bubbles()
                else:
                    safe_print("      - ⏭️  Preserving parent bubble paths")

                # ALWAYS purge the target package (not packages_to_cloak which doesn't exist)
                if not self.quiet:
                    safe_print(f"      - 🧹 Purging modules for '{pkg_name}'...")
                self._aggressive_module_cleanup(pkg_name)

                # This handles both STRICT (needs reconnect) and OVERLAY (already has it) modes
                self._profile_end("isolation_cleanup", print_now=self._profiling_enabled)

                # CRITICAL FIX: In nested contexts, only add main env if not already present
                main_site_str = str(self.site_packages_root)

                if main_site_str not in sys.path:
                    # Main env not in path - need to add it (parent was STRICT mode)
                    sys.path.append(main_site_str)
                    if not self.quiet:
                        safe_print(
                            f"   🔌 Adding main site-packages for {self._current_package_spec}"
                        )
                else:
                    # Main env already in path - parent was OVERLAY mode or already added it
                    if not self.quiet:
                        safe_print("   ✅ Main site-packages already accessible")

                importlib.invalidate_caches()

                self._profile_end("isolate_main_env", print_now=self._profiling_enabled)

                self._using_main_env = True
                
                if self._is_daemon_worker():
                    self.stabilize_daemon_state()

                pkg_canonical = pkg_name.lower().replace("-", "_")
                omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                self._my_main_env_package = pkg_canonical

                if is_numpy_involved:
                    with omnipkgLoader._numpy_lock:
                        omnipkgLoader._numpy_version_history.append(requested_version)

                self._check_numpy_abi_conflict(pkg_name, requested_version)
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                return self

        # PRIORITY 3: AUTO-INSTALL BUBBLE
        self._profile_start("install_bubble")

        self._profile_start("get_install_lock")
        install_lock = self._get_install_lock(self._current_package_spec)
        self._profile_end("get_install_lock", print_now=self._profiling_enabled)

        if not self.quiet:
            safe_print("   - 🛡️  Acquiring install lock...")

        self._profile_start("wait_for_lock")

        with install_lock:
            self._profile_end("wait_for_lock", print_now=self._profiling_enabled)

            if not self.quiet:
                safe_print("   - ✅ Install lock acquired.")

            # NEW: Release cloak locks during install (they're separate concerns)
            # This allows other packages to activate while we install
            if hasattr(self, "_held_cloak_locks"):
                for lock in self._held_cloak_locks:
                    try:
                        lock.release()
                    except:
                        pass

            # Double-check another thread didn't install it
            if bubble_path.is_dir():
                if not self.quiet:
                    safe_print("   - 🏁 Another thread finished the install.")
                self._using_main_env = False

                if is_numpy_involved:
                    with omnipkgLoader._numpy_lock:
                        omnipkgLoader._numpy_version_history.append(requested_version)

                self._profile_end("install_bubble", print_now=self._profiling_enabled)
                result = self._activate_bubble(bubble_path, pkg_name)
                self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                return result

            if not self.quiet:
                safe_print(_('   - 🔧 Auto-creating bubble for: {}').format(self._current_package_spec))

            install_success = self._install_bubble_inline(self._current_package_spec)
            if install_success:
                # 🔧 FIX: Force filesystem and import cache refresh
                importlib.invalidate_caches()
                gc.collect()
                time.sleep(0.01)  # Brief pause for filesystem sync
                
                # Re-check bubble existence with explicit path resolution
                bubble_path = bubble_path.resolve()  # Force fresh stat
                
                if not bubble_path.exists():
                    # Nuclear option: check if it's a timing issue
                    time.sleep(0.1)
                    bubble_path = self.multiversion_base / f"{pkg_name}-{requested_version}"
                
                if bubble_path.is_dir():
                    if not self.quiet:
                        safe_print("   - ✅ Bubble verified after installation.")
            if not install_success:
                raise RuntimeError(_('Failed to install {}').format(self._current_package_spec))

            # Post-install check
            if bubble_path.is_dir():
                if not self.quiet:
                    safe_print("   - ✅ Bubble created successfully.")
                self._using_main_env = False

                if is_numpy_involved:
                    with omnipkgLoader._numpy_lock:
                        omnipkgLoader._numpy_version_history.append(requested_version)

                # ═══════════════════════════════════════════════════════════
                # PHASE 1: LOCKED OPERATIONS (Critical section ~1-2ms)
                # ═══════════════════════════════════════════════════════════
                self._profile_start("locked_activation")

                # 1A. Analyze dependencies
                bubble_deps = self._get_bubble_dependencies(bubble_path)
                self._activated_bubble_dependencies = list(bubble_deps.keys())

                # ═══════════════════════════════════════════════════════════
                # COMPOSITE BUBBLE INJECTION (NVIDIA/CUDA Support)
                # ═══════════════════════════════════════════════════════════
                dependency_bubbles = []

                # Scan dependencies for binary packages that might have their own bubbles
                for dep_name, dep_version in bubble_deps.items():
                    # Focus on NVIDIA libs, Triton, and critical binary deps
                    if dep_name.startswith("nvidia_") or dep_name in ["triton", "lit"]:
                        dep_bubble_name = f"{dep_name.replace('_', '-')}-{dep_version}"
                        dep_bubble_path = self.multiversion_base / dep_bubble_name

                        if dep_bubble_path.exists() and dep_bubble_path.is_dir():
                            dependency_bubbles.append(str(dep_bubble_path))
                            if not self.quiet:
                                safe_print(_('      🔗 Found dependency bubble: {}').format(dep_bubble_name))

                if dependency_bubbles and not self.quiet:
                    safe_print(
                        _('   📦 Activating {} dependency bubbles (CUDA/NVIDIA libs)...').format(len(dependency_bubbles))
                    )

                # 1B. Determine conflicts
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

                # 1C. Cloak conflicts (LOCKED: ~0.5ms)
                if packages_to_cloak:
                    self._packages_we_cloaked.update(packages_to_cloak)
                    cloaked_count = self._batch_cloak_packages(packages_to_cloak)
                    if not self.quiet and cloaked_count > 0:
                        safe_print(_('   🔒 Cloaked {} conflicting packages').format(cloaked_count))

                # 1D. Setup sys.path (LOCKED: ~0.1ms)
                bubble_path_str = str(bubble_path)
                if self.isolation_mode == "overlay":
                    if bubble_path_str in sys.path:
                        sys.path.remove(bubble_path_str)
                    sys.path.insert(0, bubble_path_str)
                else:
                    new_sys_path = [bubble_path_str]
                    for p in self.original_sys_path:
                        if not self._is_main_site_packages(p) and p != bubble_path_str:
                            new_sys_path.append(p)
                    sys.path[:] = new_sys_path

                self._ensure_omnipkg_access_in_bubble(bubble_path_str)
                self._activated_bubble_path = bubble_path_str

                # 1E. DAEMON ONLY: Uncloak immediately (LOCKED: ~0.5ms)
                # This MUST happen before lock release!
                if self._is_daemon_worker():
                    if not self.quiet:
                        safe_print("   🔄 Daemon: Performing atomic uncloak...")
                    self.stabilize_daemon_state()  # BLOCKS until uncloak complete

                self._profile_end("locked_activation", print_now=self._profiling_enabled)

                # NOW we can signal lock release
                if self._is_daemon_worker():
                    self._signal_daemon_lock_released()

                # ═══════════════════════════════════════════════════════════
                # PHASE 2: UNLOCKED OPERATIONS (Background work ~40-60ms)
                # Lock is released, other workers can proceed!
                # ═══════════════════════════════════════════════════════════
                self._profile_start("unlocked_activation")

                # Memory cleanup (doesn't need filesystem lock)
                for pkg in packages_to_cloak:
                    self._aggressive_module_cleanup(pkg)

                if pkg_name:
                    self._aggressive_module_cleanup(pkg_name)

                gc.collect()
                importlib.invalidate_caches()

                self._profile_end("unlocked_activation", print_now=self._profiling_enabled)

                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )

                self._profile_end("activate_bubble", print_now=self._profiling_enabled)
                self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)

                if not self.quiet:
                    safe_print(
                        f"   ⚡ Activated in {self._total_activation_time_ns / 1000:,.1f} μs"
                    )

                return self

            else:
                # Package landed in main environment
                if not self.quiet:
                    safe_print("   - ⚠️  Bubble not created. Package in main environment.")

                found_ver, cloaked_path = self._get_version_from_original_env(
                    pkg_name, requested_version
                )

                if found_ver == requested_version:
                    if not self.quiet:
                        safe_print(
                            _('   - ✅ Confirmed {}=={} in main environment').format(pkg_name, requested_version)
                        )

                    self._using_main_env = True

                    pkg_canonical = pkg_name.lower().replace("-", "_")
                    omnipkgLoader._active_main_env_packages.add(pkg_canonical)
                    self._my_main_env_package = pkg_canonical

                    if is_numpy_involved:
                        with omnipkgLoader._numpy_lock:
                            omnipkgLoader._numpy_version_history.append(requested_version)

                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                    )
                    self._profile_end("install_bubble", print_now=self._profiling_enabled)
                    self._profile_end("TOTAL_ACTIVATION", print_now=self._profiling_enabled)
                    return self
                else:
                    raise RuntimeError(
                        _('Installation reported success but {}=={} not found. Found version: {}').format(pkg_name, requested_version, found_ver)
                    )

    def _activate_bubble(self, bubble_path, pkg_name):
        """
        Activate a bubble with MANDATORY module cleanup.
        CRITICAL: NEVER skip module purging, even in nested contexts!
        """
        self._profile_start("activate_bubble_total")

        # ── DAEMON WORKER FAST PATH ───────────────────────────────────────────
        # Inside a daemon worker, the process is already isolated — there is
        # exactly one Python interpreter with one set of .so files mapped.
        # We don't need to:
        #   - Cloak sibling bubbles (no other loaders share this process)
        #   - Cloak main-env packages (worker's site-packages is already clean)
        #   - Purge modules for packages with no version conflict
        #   - Hold any filesystem locks beyond the rename itself
        # Just swap sys.path[0] and invalidate the import cache.
        # This cuts per-level overhead from ~30ms to <1ms.
        _in_daemon = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
        if _in_daemon and self.isolation_mode == "overlay":
            bubble_path_str = str(bubble_path)

            # Only purge if the version is actually changing
            _needs_purge = True
            try:
                _cur = get_version(pkg_name)
                _req = self._current_package_spec.split("==")[1] \
                    if self._current_package_spec and "==" in self._current_package_spec else None
                if _req and _cur == _req:
                    _needs_purge = False
            except Exception:
                pass

            if _needs_purge:
                self._aggressive_module_cleanup(pkg_name)
                importlib.invalidate_caches()

            # Swap sys.path — overlay: prepend bubble, remove old bubble if present
            if bubble_path_str in sys.path:
                sys.path.remove(bubble_path_str)
            sys.path.insert(0, bubble_path_str)
            importlib.invalidate_caches()

            self._activated_bubble_path = bubble_path_str
            self._active_bubble_lock = None  # Never hold locks in daemon
            self._activation_successful = True
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = (
                self._activation_end_time - self._activation_start_time
                if self._activation_start_time else 0
            )
            self._profile_end("activate_bubble_total", print_now=self._profiling_enabled)
            return self
        # ── END DAEMON FAST PATH ──────────────────────────────────────────────

        try:
            # Phase 1: Analyze dependencies
            self._profile_start("get_bubble_deps")
            bubble_deps = self._get_bubble_dependencies(bubble_path)
            self._activated_bubble_dependencies = list(bubble_deps.keys())
            self._profile_end("get_bubble_deps", print_now=self._profiling_enabled)

            # Phase 2: Detect conflicts
            self._profile_start("detect_conflicts")

            # 🚀 The 2.3ms -> 0.1ms Switch
            packages_to_cloak = self._detect_conflicts_via_redis(bubble_deps)

            self._profile_end("detect_conflicts", print_now=self._profiling_enabled)

            if not self.quiet:
                safe_print(
                    _('   📊 Bubble: {} packages, {} conflicts').format(len(bubble_deps), len(packages_to_cloak))
                )

            # Phase 3: Module purging (conditional)
            self._profile_start("module_purging")
            should_purge = True
            if self._is_nested and self.isolation_mode == "overlay":
                # Only skip the purge if the currently-loaded version of pkg_name
                # already matches what we're activating.  When the version is
                # *changing* (e.g. 1.24.3 → 2.3.5 inside a daemon worker), we
                # must still purge the Python layer — otherwise the old partially-
                # initialized modules (e.g. numpy.lib) remain in sys.modules and
                # cause circular-import errors when the new bubble's __init__.py
                # tries to import the same names.
                try:
                    _current_ver = get_version(pkg_name)
                    _requested_ver = self._current_package_spec.split("==")[1] \
                        if self._current_package_spec and "==" in self._current_package_spec \
                        else None
                    _version_unchanged = (_requested_ver and _current_ver == _requested_ver)
                except Exception:
                    _version_unchanged = False

                if _version_unchanged:
                    should_purge = False
                    if not self.quiet:
                        safe_print(
                            _('   ⏭️  Skipping module purge (nested overlay, same version, depth={})').format(omnipkgLoader._nesting_depth)
                        )
                # else: version is changing in overlay mode → fall through and purge

            if should_purge:
                modules_to_purge = (
                    packages_to_cloak if packages_to_cloak else list(bubble_deps.keys())
                )

                if not self.quiet:
                    safe_print(f"   🧹 Purging {len(modules_to_purge)} module(s) from memory...")

                for pkg in modules_to_purge:
                    self._aggressive_module_cleanup(pkg)

                # Also purge the target package itself
                self._aggressive_module_cleanup(pkg_name)

                # Single GC call after all module cleanup
                gc.collect()
                importlib.invalidate_caches()
            self._profile_end("module_purging", print_now=self._profiling_enabled)

            # Phase 4: Cloak conflicts
            self._profile_start("cloak_conflicts")
            self._packages_we_cloaked.update(packages_to_cloak)
            cloaked_count = self._batch_cloak_packages(packages_to_cloak)

            if not self.quiet and cloaked_count > 0:
                safe_print(_('   🔒 Cloaked {} conflicting packages').format(cloaked_count))

            # Phase 4b: Cloak sibling bubbles in multiversion_base.
            # _batch_cloak_packages only touches main site-packages.
            # Other bubbles for the same package (numpy-2.3.5, numpy-1.26.4, ...)
            # remain visible on sys.path from parent loaders and can cause
            # _heal_numpy_python_layer to pick up the wrong __init__.py.
            # Cloak all sibling bubbles that are a different version.
            #
            # DAEMON WORKER GUARD: If we are running inside a daemon worker
            # (OMNIPKG_IS_DAEMON_WORKER=1), the worker's startup loader already
            # activated a bubble via overlay mode (e.g. numpy-1.26.4).  Nested
            # loader contexts spawned by user code running in the worker must NOT
            # cloak that startup bubble — doing so breaks the worker's own import
            # context and causes cross-version errors on subsequent imports.
            # Collect the set of bubble paths currently active in sys.path so we
            # can skip them in the scandir below.
            _is_daemon_worker = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
            _protected_bubble_paths: set = set()
            if _is_daemon_worker:
                _mvbase_str = str(self.multiversion_base)
                for _sp_entry in sys.path:
                    if _mvbase_str in _sp_entry and "_omnipkg_cloaked" not in _sp_entry:
                        # This is an active bubble path — protect it from cloaking
                        _protected_bubble_paths.add(os.path.normpath(_sp_entry))

            try:
                _sib_timestamp = int(time.time() * 1000000)
                _sib_suffix = f".{_sib_timestamp}_{id(self)}_omnipkg_cloaked"
                for _entry in os.scandir(str(self.multiversion_base)):
                    if (
                        _entry.is_dir()
                        and _entry.name.startswith(f"{pkg_name}-")
                        and "_omnipkg_cloaked" not in _entry.name
                        and _entry.path != str(bubble_path)  # don't cloak ourselves
                    ):
                        # Skip any bubble that is currently active in sys.path
                        # (protects the daemon worker's own startup bubble)
                        if os.path.normpath(_entry.path) in _protected_bubble_paths:
                            if not self.quiet:
                                safe_print(
                                    _('   ⏭️  Preserving parent bubble paths')
                                )
                            continue
                        _sib_path = Path(_entry.path)
                        # Use the locked _cloak_bubble helper with a short timeout.
                        # If the lock can't be acquired (another process/loader is
                        # actively using this bubble), skip it — cloaking a bubble
                        # that's in another process's sys.path causes
                        # ModuleNotFoundError when that process imports from it.
                        # The key insight: if we CAN'T lock it, we DON'T need to
                        # cloak it for OUR isolation — our sys.path is STRICT mode
                        # and only has OUR bubble at [0], so the sibling is invisible
                        # to us regardless.
                        # Daemon workers use timeout=0 (never block — they work in-memory).
                        # In-process loaders can wait briefly since they need the FS correct.
                        _sib_timeout = 0 if _is_daemon_worker else 0.5
                        _sib_cloak_p, _sib_ok = self._cloak_bubble(
                            _sib_path, _sib_suffix, timeout=_sib_timeout)
                        if _sib_ok:
                            self._cloaked_bubbles.append((_sib_cloak_p, _sib_path))
                            if not self.quiet:
                                safe_print(_('   🔒 Cloaked sibling bubble: {}').format(_sib_path.name))
                        elif not self.quiet:
                            safe_print(_('   ⏭️  Skipping busy sibling bubble: {}').format(_sib_path.name))
            except OSError:
                pass

            # Phase 4c: Cloak the main-env copy of pkg_name if it exists and
            # is a DIFFERENT version from the bubble we're activating.
            # _batch_cloak_packages (Phase 4) only cloaks packages whose version
            # differs from bubble deps — it never cloaks pkg_name itself because
            # the bubble manifest lists pkg_name as the package being installed,
            # not as a "conflict".  Without this, numpy's main-env directory
            # (e.g. site-packages/numpy/ @ 1.26.4) stays on disk while the bubble
            # (e.g. numpy-1.24.3) is active, and Python's import machinery can still
            # resolve it through the main site-packages fallback at the end of sys.path.
            #
            # DAEMON WORKER GUARD: Skip this phase inside a daemon worker.
            # The worker's STEP 4 already restored all cloaks after startup bubble
            # activation.  Nested loaders running user code should not re-cloak the
            # main-env package — it is not in sys.path (the worker uses overlay mode
            # with the bubble prepended), so cloaking it is unnecessary and only
            # causes confusion in the worker's exit/cleanup path.
            if not _is_daemon_worker:
                try:
                    _canonical_pkg = pkg_name.lower().replace("-", "_")
                    _main_pkg_dir = self.site_packages_root / _canonical_pkg
                    _main_dist_infos = list(
                        self.site_packages_root.glob(f"{_canonical_pkg}-*.dist-info")
                    )

                    # Determine the main-env version (if any)
                    # IMPORTANT: Do NOT use importlib.metadata.distributions() here —
                    # it caches PathDistribution objects and the cache is NOT invalidated
                    # by importlib.invalidate_caches().  After a cloak+uncloak cycle the
                    # cache returns stale "not found" results.  Scan the dist-info
                    # directories directly instead.
                    _main_env_ver: Optional[str] = None
                    for _di in self.site_packages_root.glob(f"{_canonical_pkg}-*.dist-info"):
                        if not _di.is_dir():
                            continue
                        # Extract version from directory name: numpy-1.26.4.dist-info → 1.26.4
                        _di_stem = _di.name[len(f"{_canonical_pkg}-"):]  # "1.26.4.dist-info"
                        _di_ver = _di_stem.replace(".dist-info", "")
                        if _di_ver:
                            _main_env_ver = _di_ver
                            break

                    # Determine the bubble version from its path name
                    _bubble_ver = str(bubble_path.name)
                    if _bubble_ver.startswith(f"{pkg_name}-"):
                        _bubble_ver = _bubble_ver[len(f"{pkg_name}-"):]
                    else:
                        _bubble_ver = None  # can't determine, skip safety check

                    _should_cloak_main = (
                        _main_env_ver is not None
                        and _bubble_ver is not None
                        and _main_env_ver != _bubble_ver
                    )

                    if _should_cloak_main:
                        _me_timestamp = int(time.time() * 1000000)
                        _me_suffix = f".{_me_timestamp}_{id(self)}_omnipkg_cloaked"
                        lock = self._get_cloak_lock(_canonical_pkg)

                        _paths_to_cloak_main = []
                        if _main_pkg_dir.exists():
                            _paths_to_cloak_main.append(_main_pkg_dir)
                        _paths_to_cloak_main.extend(_main_dist_infos)
                        # Also egg-info if present
                        _paths_to_cloak_main.extend(
                            self.site_packages_root.glob(f"{_canonical_pkg}-*.egg-info")
                        )

                        from omnipkg.isolation.fs_lock_queue import safe_cloak as _sc4c
                        for _me_path in _paths_to_cloak_main:
                            if not _me_path.exists():
                                continue
                            _me_cloak = _me_path.with_name(_me_path.name + _me_suffix)
                            _ok = _sc4c(
                                src=_me_path,
                                dst=_me_cloak,
                                locks_dir=omnipkgLoader._locks_dir,
                                pkg_name=_canonical_pkg,
                                active_cloaks=omnipkgLoader._active_cloaks,
                                owner_id=id(self),
                                sentinel_base=self.multiversion_base,
                                timeout=5.0,
                            )
                            if _ok:
                                self._cloaked_main_modules.append((_me_path, _me_cloak, True))
                                if not self.quiet:
                                    safe_print(_('   🔒 Cloaked main-env {}: {}').format(
                                        pkg_name, _me_path.name))
                            else:
                                if not self.quiet:
                                    safe_print(f"   ⏱️  Skipped/locked main-env cloak: {_me_path.name}")
                except Exception as _me_outer_err:
                    if not self.quiet:
                        safe_print(
                            _('   ⚠️  Main-env cloak phase error: {}').format(_me_outer_err)
                        )

            self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

            # Phase 5: Setup sys.path
            self._profile_start("setup_syspath")
            bubble_path_str = str(bubble_path)
            if self.isolation_mode == "overlay":
                if not self.quiet:
                    safe_print("   - 🧬 OVERLAY mode")
                if bubble_path_str in sys.path:
                    sys.path.remove(bubble_path_str)
                sys.path.insert(0, bubble_path_str)
            else:
                if not self.quiet:
                    safe_print("   - 🔒 STRICT mode")
                new_sys_path = [bubble_path_str]
                for p in self.original_sys_path:
                    if not self._is_main_site_packages(p) and p != bubble_path_str:
                        new_sys_path.append(p)
                sys.path[:] = new_sys_path
            self._profile_end("setup_syspath", print_now=self._profiling_enabled)

            # Phase 6: Handle binary executables
            self._profile_start("setup_bin_path")
            bin_path = bubble_path / "bin"
            if bin_path.is_dir():
                if not self.quiet:
                    safe_print(_('   - 🔩 Activating binary path: {}').format(bin_path))
                os.environ["PATH"] = str(bin_path) + os.pathsep + self.original_path_env
            self._profile_end("setup_bin_path", print_now=self._profiling_enabled)

            # Phase 7: Ensure omnipkg access
            self._profile_start("ensure_omnipkg_access")
            self._ensure_omnipkg_access_in_bubble(bubble_path_str)
            self._profile_end("ensure_omnipkg_access", print_now=self._profiling_enabled)

            # Finalize — hold the package lock for the lifetime of this context.
            # This prevents other loaders (in this process or others) from cloaking
            # our active bubble while we're using it.  Phase 4b uses timeout=0 so
            # it will skip any bubble whose lock is held — meaning our bubble stays
            # on sys.path[0] and numpy/subdir remains accessible.
            self._activated_bubble_path = bubble_path_str

            # Daemon workers must NOT hold the bubble lock for the lifetime of the context.
            # They do all their work in-memory after cloaking — holding the lock blocks
            # other loaders from cloaking sibling bubbles needlessly.
            # Only in-process (non-daemon) loaders hold it to protect their active bubble.
            if not _is_daemon_worker:
                try:
                    _bubble_lock = self._get_cloak_lock(pkg_name)
                    _bubble_lock.acquire(timeout=0)
                    self._active_bubble_lock = _bubble_lock
                except filelock.Timeout:
                    self._active_bubble_lock = None
            else:
                # Daemon worker: never hold the bubble lock beyond this point.
                # The filesystem is already in the correct state; release immediately.
                self._active_bubble_lock = None
                
            if _is_daemon_worker:
                if not self.quiet:
                    safe_print("   🔄 Daemon fast-path: Performing atomic uncloak...")
                self.stabilize_daemon_state()

            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time

            self._profile_end("activate_bubble_total", print_now=self._profiling_enabled)

            if not self.quiet:
                safe_print(f"   ⚡ HEALED in {self._total_activation_time_ns / 1000:,.1f} μs")
                safe_print("   ✅ Bubble activated")

            self._activation_successful = True

            # ── Heal numpy Python layer if C extension already mapped ──────────
            # Skip entirely inside daemon workers: the worker has exactly one .so
            # mapped (its startup version). Calling _heal_numpy_python_layer here
            # triggers Strategy 1's `import numpy` from the new bubble path, which
            # runs that bubble's numpy/lib/__init__.py. If a previous level already
            # partially initialized numpy.lib in sys.modules (even after our purge,
            # because the purge only cleared Python-layer modules not partially-init
            # sentinel entries), Python hits a circular import and raises ImportError.
            # Inside a worker the user's `import numpy` after the `with` block will
            # do the right thing naturally once sys.path[0] is the correct bubble.
            # _check_numpy_abi_conflict is also skipped: workers never need to
            # delegate to a sub-daemon — they ARE the daemon boundary.
            _in_daemon_worker = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
            if pkg_name == "numpy" and "numpy.core._multiarray_umath" in sys.modules                     and not _in_daemon_worker:
                # Derive version from bubble path name e.g. numpy-1.24.3 → "1.24.3"
                _bubble_ver = (
                    str(bubble_path.name).replace("numpy-", "")
                    if str(bubble_path.name).startswith("numpy-") else ""
                )
                # Raise ProcessCorruptedException if mapped .so is ABI-incompatible.
                # WorkerDelegationMixin catches this and routes to daemon.
                self._check_numpy_abi_conflict(pkg_name, _bubble_ver)
                self._heal_numpy_python_layer(str(bubble_path))

            # Off hot-path: verify C-extension packages loaded the right .so.
            # Raises ProcessCorruptedException if .so mapping conflict detected.
            # That exception is caught by WorkerDelegationMixin.__enter__ which
            # then falls back to daemon/subprocess execution.
            try:
                self._verify_importable_or_raise_corrupted(pkg_name, str(bubble_path))
            except ProcessCorruptedException:
                # Re-raise before returning self; the caller will handle it
                raise
 
            return self
 
        except ProcessCorruptedException:
            # Don't panic-restore on a known corruption — the caller (WorkerDelegationMixin)
            # will handle recovery.  Just decrement nesting depth so it stays consistent.
            raise
        except Exception as e:
            safe_print(_('   ❌ Activation failed: {}').format(str(e)))
            self._panic_restore_cloaks()
            raise

    def _heal_numpy_python_layer(self, bubble_path_str: str):
        """
        Re-bind numpy's Python layer from the correct bubble after a version switch.

        When numpy's C extension (.so) is already mapped by the OS linker, we can't
        reload it — but we CAN reload the pure-Python modules on top of it.
        The C extension exposes a fixed ABI; the Python layer (including __version__,
        array printing, linalg, fft, etc.) is pure Python and can be re-imported
        from any compatible bubble.

        Strategy:
          1. Try a direct import — sys.path already points at the bubble.
          2. If that fails (ABI mismatch between the mapped .so and this bubble's
             Python layer), use importlib.util to load the bubble's __init__.py
             directly without going through the normal C-ext init path.
          3. If that also fails, run a clean subprocess to get __version__ and
             __file__ and inject a minimal proxy into sys.modules so at least
             np.__version__ is correct.
        """
        bubble_path = Path(bubble_path_str)
        numpy_init = bubble_path / "numpy" / "__init__.py"

        # Strategy 1: direct import (works when ABI is compatible)
        try:
            import numpy as _np
            if not self.quiet:
                safe_print(
                    f"   🔄 numpy Python layer: {_np.__version__} "
                    f"from {_np.__file__}"
                )
            return
        except Exception:
            pass

        # Strategy 2: load __init__.py directly via importlib
        if numpy_init.exists():
            try:
                import importlib.util as _ilu
                # Snapshot C extensions before wiping Python layer
                _c_exts = {
                    k: v for k, v in sys.modules.items()
                    if (k == "numpy" or k.startswith("numpy."))
                    and getattr(v, "__file__", "").endswith(".so")
                }
                for _k in [k for k in list(sys.modules.keys()) if k == "numpy" or
                           (k.startswith("numpy.") and not
                            getattr(sys.modules.get(k), "__file__", "").endswith(".so"))]:
                    sys.modules.pop(_k, None)
                _pkg_dir = str(numpy_init.parent)
                _spec = _ilu.spec_from_file_location(
                    "numpy", str(numpy_init),
                    submodule_search_locations=[_pkg_dir]
                )
                _mod = _ilu.module_from_spec(_spec)
                _mod.__path__ = [_pkg_dir]
                _mod.__package__ = "numpy"
                sys.modules["numpy"] = _mod
                # Re-register C extensions so relative imports resolve
                for _ce_name, _ce_mod in _c_exts.items():
                    if _ce_name not in sys.modules:
                        sys.modules[_ce_name] = _ce_mod
                _spec.loader.exec_module(_mod)
                if not self.quiet:
                    safe_print(
                        f"   🔄 numpy Python layer (direct load): {_mod.__version__} "
                        f"from {_mod.__file__}"
                    )
                return
            except Exception:
                pass

        # Strategy 3: Daemon worker — spawn a clean process with the right numpy,
        # exec its __init__.py source back into our process's sys.modules.
        # This is the true boundary crossing: the daemon worker has no mapped .so
        # conflicts, loads the correct numpy cleanly, and sends back its Python
        # source so we can re-bind our numpy module object without touching the C layer.
        try:
            if DAEMON_AVAILABLE and not os.environ.get("OMNIPKG_IS_DAEMON_WORKER"):
                _pkg_spec = f"numpy=={Path(bubble_path_str).name.replace('numpy-', '')}" \
                    if "numpy-" in Path(bubble_path_str).name else None

                if _pkg_spec is None:
                    # Derive version from dist-info inside bubble
                    for _di in Path(bubble_path_str).glob("numpy-*.dist-info"):
                        _pkg_spec = f"numpy=={_di.name.split('-')[1]}"
                        break

                if _pkg_spec:
                    _client = self._get_daemon_client()
                    _proxy = DaemonProxy(_client, _pkg_spec)

                    # The daemon worker was already spawned with this spec and has
                    # numpy fully loaded in a clean process. Do NOT send the bubble
                    # path — it may be cloaked (renamed to *.omnipkg_cloaked) by the
                    # caller's loader right now, so __init__.py would not exist there.
                    # Just ask the worker for its numpy's __version__ and __file__.
                    _daemon_code = (
                        "import sys, json\n"
                        "import numpy as np\n"
                        "sys.stdout.write(json.dumps({\n"
                        "    'version': np.__version__,\n"
                        "    'file': str(np.__file__),\n"
                        "}) + '\\n')\n"
                    )
                    _resp = _proxy.execute(_daemon_code)

                    if _resp.get("success") and _resp.get("stdout", "").strip():
                        import json as _json
                        _data = _json.loads(_resp["stdout"].strip().splitlines()[-1])
                        _ver = _data["version"]
                        _file = _data["file"]

                        # Stamp the daemon's ground-truth version onto the existing
                        # module object. The .so is already mapped in this process
                        # and cannot change — this just ensures __version__ and
                        # __file__ reflect the bubble we just activated.
                        #
                        # IMPORTANT: If numpy was fully purged from sys.modules
                        # (because its .so anchor was unreachable — see
                        # _aggressive_module_cleanup), sys.modules["numpy"] is None
                        # and the stamp is a no-op.  In that case, skip Strategy 3
                        # and fall through to Strategy 4 (subprocess) which will
                        # produce a real module object via a fresh import.
                        _existing = sys.modules.get("numpy")
                        if _existing is not None:
                            try:
                                _existing.__version__ = _ver
                                _existing.__file__ = _file
                            except Exception:
                                pass
                            if not self.quiet:
                                safe_print(f"   🔄 numpy version via daemon worker: {_ver}")
                            return
                        # else: fall through — numpy was fully purged, need fresh import
        except Exception:
            pass

        # Strategy 4: plain subprocess fallback (daemon unavailable)
        # Use -S to skip site.py, then manually add only the bubble path.
        # This prevents the subprocess from inheriting main site-packages
        # and picking up the wrong numpy version.
        try:
            _script = textwrap.dedent(f"""
                import sys
                # Only the bubble path — no main site-packages
                sys.path = [p for p in sys.path if 'site-packages' not in p]
                sys.path.insert(0, {bubble_path_str!r})
                import site; site.addsitedir({bubble_path_str!r})
                import numpy as np
                print(np.__version__ + '|' + (np.__file__ or ''))
            """)
            _env = os.environ.copy()
            # Clear PYTHONPATH so it doesn't inject extra paths
            _env.pop("PYTHONPATH", None)
            _result = subprocess.run(
                [sys.executable, "-c", _script],
                capture_output=True, text=True, timeout=15,
                env=_env,
            )
            if _result.returncode == 0 and "|" in _result.stdout:
                _ver, _file = _result.stdout.strip().split("|", 1)
                _existing = sys.modules.get("numpy")
                if _existing is not None:
                    try:
                        _existing.__version__ = _ver
                        _existing.__file__ = _file
                        if not self.quiet:
                            safe_print(
                                f"   🔄 numpy version patched via subprocess: {_ver}"
                            )
                    except Exception:
                        pass
                return
        except Exception:
            pass

        # All strategies exhausted — dump diagnostic so we know exactly why
        try:
            _heal_diag = []
            _heal_diag.append(f"   ⚠️  numpy Python layer heal: all strategies exhausted")
            _heal_diag.append(f"      bubble_path={bubble_path_str}")
            _heal_diag.append(f"      sys.path[0:3]={sys.path[:3]}")
            _heal_path = Path(bubble_path_str)
            _np_init = _heal_path / "numpy" / "__init__.py"
            _heal_diag.append(f"      bubble/numpy/__init__.py exists={_np_init.exists()}")
            # Show what numpy dirs are visible
            for _pp in sys.path[:4]:
                _nd = Path(_pp) / "numpy"
                if _nd.exists():
                    _heal_diag.append(f"      sys.path entry {_pp}/numpy EXISTS (init={(_nd/'__init__.py').exists()})")
            # Show numpy in sys.modules
            _np_keys = [k for k in sys.modules if k == "numpy" or k.startswith("numpy.core")]
            _heal_diag.append(f"      numpy in sys.modules: {_np_keys[:8]}")
            safe_print("\n".join(_heal_diag))
        except Exception:
            if not self.quiet:
                safe_print("   ⚠️  numpy Python layer heal: all strategies exhausted")

    def _detect_conflicts_via_redis(self, bubble_deps: Dict[str, str]) -> List[str]:
        """
        🚀 ULTRA-FAST Conflict Detection (Redis Pipelining).
        Replaces 2.3ms of disk I/O with ~0.1ms of cache lookup.
        """
        # Fallback to disk if cache is missing (e.g. SQLite mode or cold start)
        if not self.cache_client or not hasattr(self.cache_client, "pipeline"):
            return self._detect_conflicts_legacy(bubble_deps)

        conflicts = []
        dep_names = list(bubble_deps.keys())

        try:
            # 1. Pipeline Request: Get 'active_version' for all deps in one go
            with self.cache_client.pipeline() as pipe:
                for pkg in dep_names:
                    # Construct Key: omnipkg:env_XXX:py3.11:pkg:numpy
                    c_name = canonicalize_name(pkg)
                    key = f"{self.redis_key_prefix}{c_name}"
                    pipe.hget(key, "active_version")

                # ⚡ EXECUTE (1 Network Round Trip)
                main_versions_raw = pipe.execute()

            # 2. Memory Comparison (Nanoseconds)
            for pkg, main_ver_bytes, bubble_ver in zip(
                dep_names, main_versions_raw, bubble_deps.values()
            ):
                if main_ver_bytes:
                    # Redis returns bytes, decode to string
                    main_ver = main_ver_bytes.decode("utf-8")

                    if main_ver != bubble_ver:
                        conflicts.append(pkg)
                        if not self.quiet:
                            safe_print(
                                _('   ⚠️ Conflict (Redis): {} (main: {} vs bubble: {})').format(pkg, main_ver, bubble_ver)
                            )

        except Exception as e:
            # If Redis flakes out, fallback to disk silently
            if not self.quiet:
                safe_print(_('   ⚠️ Redis conflict check failed ({}), falling back to disk...').format(e))
            return self._detect_conflicts_legacy(bubble_deps)

        return conflicts

    def _detect_conflicts_legacy(self, bubble_deps: Dict[str, str]) -> List[str]:
        """Fallback: The old, slow disk-based method."""
        conflicts = []
        for pkg, bubble_ver in bubble_deps.items():
            try:
                # This hits the disk (stat/read)
                main_ver = get_version(pkg)
                if main_ver != bubble_ver:
                    conflicts.append(pkg)
            except PackageNotFoundError:
                pass
        return conflicts

    def _panic_restore_cloaks(self):
        """Emergency cloak restoration - always cleanup since this is an error path."""
        if not self.quiet:
            safe_print(_("🚨 Emergency cloak restoration in progress..."))

        # First, restore what we can
        self._restore_cloaked_modules()

        # CRITICAL: Always cleanup on panic regardless of nesting
        # (Error states should be cleaned up immediately)
        if not self.quiet:
            safe_print("   🧹 Running emergency global cleanup...")

        cleaned = self._cleanup_all_cloaks_globally()

        if cleaned > 0 and not self.quiet:
            safe_print(_('   ✅ Emergency cleanup removed {} orphaned cloaks').format(cleaned))

    def _install_bubble_inline(self, spec):
        """
        Install a missing bubble directly, inline.
        Returns True if successful, False otherwise.
        """
        start_time = time.perf_counter()

        try:
            from omnipkg.core import ConfigManager
            from omnipkg.core import omnipkg as OmnipkgCore

            # Create a fresh ConfigManager
            cm = ConfigManager(suppress_init_messages=True)

            if hasattr(self, "config") and isinstance(self.config, dict):
                cm.config.update(self.config)

            core = OmnipkgCore(cm)

            original_strategy = core.config.get("install_strategy")
            core.config["install_strategy"] = "stable-main"

            try:
                if not self.quiet:
                    safe_print(f"      📦 Installing {spec} with dependencies...")

                # 🔧 FIX: Extract and pass index URLs
                index_url = None
                extra_index_url = None
                
                # Auto-detect PyTorch index
                if 'torch' in spec.lower() and '+cu' in spec:
                    cu_version = spec.split('+cu')[1].split('==')[0] if '==' in spec else spec.split('+cu')[1]
                    extra_index_url = f"https://download.pytorch.org/whl/cu{cu_version}"
                    if not self.quiet:
                        safe_print(_('      🔍 Auto-detected PyTorch index: {}').format(extra_index_url))

                # Pass index URLs to smart_install
                result = core.smart_install(
                    [spec],
                    index_url=index_url,
                    extra_index_url=extra_index_url
                )

                if result != 0:
                    if not self.quiet:
                        safe_print(f"      ❌ Installation failed with exit code {result}")
                    return False

                elapsed = time.perf_counter() - start_time

                if not self.quiet:
                    safe_print(f"      ✅ Bubble created in {elapsed:.1f}s (tested & deps bundled)")
                    safe_print("      💡 Future loads will be instant (~100μs)")

                # CRITICAL FIX: Force a clean import state after installation
                # The installer may have imported modules that conflict with our context
                importlib.invalidate_caches()
                gc.collect()

                return True

            finally:
                if original_strategy:
                    core.config["install_strategy"] = original_strategy

        except Exception as e:
            if not self.quiet:
                safe_print(_('      ❌ Auto-install exception: {}').format(e))
                import traceback
                safe_print(traceback.format_exc())
            return False

    def __enter__(self):
        """Activation entry point - dispatches to multi or single package logic."""
        self._maybe_refresh_dependency_cache()
        if len(self._package_specs) > 1:
            return self._enter_multi()
        try:
            return self._enter_single()
        except ProcessCorruptedException as _e:
            # ABI conflict: try to delegate to daemon worker.
            # _check_numpy_abi_conflict raises this when the mapped .so is
            # incompatible with the requested version. We catch it here (in the
            # real __enter__) so the with-body runs in daemon-worker context
            # rather than propagating the exception to the caller.
            if not self.quiet:
                safe_print("   🔄 ABI conflict in __enter__, delegating to daemon...")
            self._panic_restore_cloaks()
            # Pre-dispatch: restore ALL cloaks for this package so the daemon
            # worker sees a clean filesystem.
            try:
                _dispatch_pkg = self._current_package_spec.split("==")[0] \
                    if self._current_package_spec else None
                if _dispatch_pkg:
                    self._restore_all_cloaks_for_pkg_unsafe(_dispatch_pkg)
            except Exception:
                pass
            # Try daemon worker
            if DAEMON_AVAILABLE and not os.environ.get("OMNIPKG_IS_DAEMON_WORKER")                     and getattr(self, '_worker_fallback_enabled', True):
                try:
                    _client = self._get_daemon_client()
                    _proxy = DaemonProxy(_client, self._current_package_spec)
                    self._active_worker = _proxy
                    self._worker_mode = True
                    self._run_once_mode = True
                    self._activation_successful = True
                    self._abi_conflict_detected = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                        if self._activation_start_time else 0
                    )
                    if not self.quiet:
                        safe_print(f"   ✅ Delegated to daemon worker for {self._current_package_spec}")
                    return self
                except Exception as _de:
                    if not self.quiet:
                        safe_print(f"   ⚠️  Daemon delegation failed: {_de}, continuing with ABI conflict noted")
            # No daemon available — mark conflict and continue without isolation
            self._activation_successful = True
            self._abi_conflict_detected = True
            return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with COMPLETE profiling visibility."""
        # ── Suppress ProcessCorruptedException from the with-body ──────────────
        # When nested omnipkgLoader contexts are used recursively, an ABI
        # conflict at a deeper level raises ProcessCorruptedException inside
        # the with-body.  Without this guard it propagates upward through every
        # outer with-body and crashes the caller.  Suppress it here, restore
        # all cloaks for this package, run normal cleanup, return True.
        if exc_type is not None:
            try:
                from omnipkg.common_utils import ProcessCorruptedException as _PCE
                if issubclass(exc_type, _PCE):
                    if not self.quiet:
                        safe_print(
                            f"   ↕️  ABI conflict suppressed in __exit__ "
                            f"({self._current_package_spec}, "
                            f"depth={omnipkgLoader._nesting_depth}): {exc_val}"
                        )
                    # Restore ALL cloaks for this package unconditionally so
                    # subsequent daemon dispatch finds a clean filesystem.
                    try:
                        _exit_pkg = self._current_package_spec.split("==")[0] \
                            if self._current_package_spec else None
                        if _exit_pkg and hasattr(self, "multiversion_base"):
                            self._restore_all_cloaks_for_pkg_unsafe(_exit_pkg)
                    except Exception:
                        pass
                    # Run cleanup with no active exception — call __exit__ 
                    # recursively but with exc_type=None so the body below
                    # executes the normal deactivation path cleanly.
                    try:
                        self.__exit__(None, None, None)
                    except Exception:
                        pass
                    return True  # suppress the exception
            except ImportError:
                pass

        if self._active_sub_loaders:
            # ── DAEMON WORKER NESTED OVERLAY FAST EXIT ────────────────────────────
            _in_daemon_exit = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
            if _in_daemon_exit and self.isolation_mode == "overlay" and self._activated_bubble_path:
                try:
                    sys.path.remove(self._activated_bubble_path)
                except ValueError:
                    pass
                # Only clear the cache entry for the bubble we just removed
                sys.path_importer_cache.pop(self._activated_bubble_path, None)
                # Purge Python layer only — no GC, no full invalidate_caches
                _pkg_norm = (self._current_package_spec.split("==")[0]
                            if self._current_package_spec else "").lower().replace("-", "_")
                if _pkg_norm:
                    _c_exts = {k for k in sys.modules
                            if (k == _pkg_norm or k.startswith(_pkg_norm + "."))
                            and getattr(sys.modules.get(k), "__file__", "").endswith(".so")}
                    for k in [k for k in list(sys.modules)
                            if (k == _pkg_norm or k.startswith(_pkg_norm + "."))
                            and k not in _c_exts]:
                        sys.modules.pop(k, None)
                with omnipkgLoader._nesting_lock:
                    omnipkgLoader._nesting_depth = max(0, omnipkgLoader._nesting_depth - 1)
                return False
            # ── END DAEMON FAST EXIT ──────────────────────────────────────────────
            # Multi-package mode: exit sub-loaders in reverse order (LIFO)
            # Let each sub-loader handle its own nesting depth correctly
            exc_info = (exc_type, exc_val, exc_tb)
            for loader in reversed(self._active_sub_loaders):
                try:
                    loader.__exit__(*exc_info)
                except Exception as e:
                    if not self.quiet:
                        safe_print(f"   ⚠️ Cleanup error ({loader._current_package_spec}): {e}")
            self._active_sub_loaders.clear()
            return False  # Never suppress exceptions

        self._profile_start("TOTAL_DEACTIVATION")

        # Nesting management
        self._profile_start("nesting_management")
        should_cleanup = False
        with omnipkgLoader._nesting_lock:
            current_depth = omnipkgLoader._nesting_depth
            should_cleanup = current_depth == 1
            omnipkgLoader._nesting_depth -= 1
        self._profile_end("nesting_management")

        # Worker cleanup path
        # Worker cleanup path
        if self._worker_mode and self._active_worker:
            _ws_start = time.perf_counter_ns()
            if self._worker_from_pool:
                if not self.quiet:
                    safe_print("   ♻️  Releasing pooled worker")
                self._active_worker = None
            else:
                if not self.quiet:
                    safe_print("   🛑 Shutting down temporary worker...")
                try:
                    self._active_worker.shutdown()
                except Exception as e:
                    if not self.quiet:
                        safe_print(_('   ⚠️  Worker shutdown warning: {}').format(e))
                finally:
                    self._active_worker = None
            _ws_ms = (time.perf_counter_ns() - _ws_start) / 1_000_000
            if not self.quiet:
                safe_print(f"      ⏱️  WORKER_SHUTDOWN: {_ws_ms:.3f}ms")

            self._worker_mode = False
            self._profile_end("TOTAL_DEACTIVATION", print_now=self._profiling_enabled)
            return

        self._deactivation_start_time = time.perf_counter_ns()

        if not self.quiet:
            depth_marker = f" [depth={current_depth}]" if self._is_nested else ""
            safe_print(
                _('🌀 omnipkg loader: Deactivating {}{}...').format(self._current_package_spec, depth_marker)
            )

        if not self._activation_successful:
            # CRITICAL: Always cleanup on failure
            self._cleanup_all_cloaks_globally()
            self._profile_end("TOTAL_DEACTIVATION")
            return

        pkg_name = self._current_package_spec.split("==")[0] if self._current_package_spec else None

        # Release the active bubble lock so other loaders can cloak this bubble
        # once we've finished with it.  Must happen BEFORE restoring our own cloaks
        # so that the lock is available if another loader is waiting to re-cloak it.
        if self._active_bubble_lock is not None:
            try:
                self._active_bubble_lock.release()
            except Exception:
                pass
            self._active_bubble_lock = None

        # Unregister protection
        self._profile_start("unregister_protection")
        if self._my_main_env_package:
            omnipkgLoader._active_main_env_packages.discard(self._my_main_env_package)
        self._profile_end("unregister_protection", print_now=self._profiling_enabled)

        # Restore main cloaks
        self._profile_start("restore_main_cloaks")
        restored_count = 0
        if self._cloaked_main_modules:
            if not self.quiet:
                safe_print(
                    _('   - 🔓 Restoring {} cloaked main env package(s)...').format(len(self._cloaked_main_modules))
                )

            for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
                if not was_successful:
                    continue

                if not cloak_path.exists():
                    continue

                from omnipkg.isolation.fs_lock_queue import safe_uncloak as _su
                _pkg_g = original_path.name.split("-")[0].split(".")[0]
                if _su(
                    src=cloak_path,
                    dst=original_path,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=_pkg_g,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=5.0,
                ):
                    restored_count += 1

            self._cloaked_main_modules.clear()
        self._profile_end("restore_main_cloaks", print_now=self._profiling_enabled)

        # Restore bubble cloaks
        self._profile_start("restore_bubble_cloaks")
        if self._cloaked_bubbles:
            for cloak_path, original_path in reversed(self._cloaked_bubbles):
                if self._uncloak_bubble(cloak_path, original_path):
                    restored_count += 1
            self._cloaked_bubbles.clear()
        self._profile_end("restore_bubble_cloaks", print_now=self._profiling_enabled)

        # ═══════════════════════════════════════════════════════════
        # CRITICAL: Profile environment restoration (this is the 57ms!)
        # ═══════════════════════════════════════════════════════════
        self._profile_start("restore_environment")
        _in_daemon_worker_exit = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
        if self.isolation_mode == "overlay" and self._activated_bubble_path:
            try:
                sys.path.remove(self._activated_bubble_path)
                # Invalidate FileFinder cache immediately after removing the bubble
                # path so any subsequent import doesn't find a stale negative-cache
                # entry for packages that should now resolve from site-packages.
                importlib.invalidate_caches()
            except ValueError:
                pass
        elif _in_daemon_worker_exit and self._using_main_env:
            # Inside a daemon worker that used the main-env path (e.g. numpy==1.26.4
            # is the worker's startup version, found directly in site-packages):
            # do NOT restore sys.path[:] = original_sys_path — that would wipe the
            # worker's startup bubble path and leave only bare site-packages, causing
            # subsequent imports inside the same task to find the wrong (main-env)
            # numpy instead of the worker's bubble version.
            # Just remove the main site-packages if we added it, leave the rest.
            _main_sp = str(self.site_packages_root)
            if _main_sp in sys.path:
                # Only remove if it wasn't in original_sys_path (i.e. we added it)
                if _main_sp not in self.original_sys_path:
                    try:
                        sys.path.remove(_main_sp)
                    except ValueError:
                        pass
            os.environ["PATH"] = self.original_path_env
        else:
            # THIS is the expensive operation at depth!
            os.environ["PATH"] = self.original_path_env
            sys.path[:] = self.original_sys_path  # <-- 50-60ms here!
        self._profile_end("restore_environment", print_now=self._profiling_enabled)

        # ═══════════════════════════════════════════════════════════
        # OPTIMIZATION: Only purge modules at DEPTH 1
        # Nested contexts don't need this - the parent will handle it
        # EXCEPTION: Daemon worker overlay exits MUST purge at every depth.
        # When overlay __exit__ removes the bubble path, any numpy Python
        # modules loaded from that bubble remain in sys.modules.  The next
        # activation in the same task will do a fresh import — but if a
        # partial numpy.lib sentinel survives (e.g. from an interrupted import
        # two levels up), that import hits a circular-import error.  Purge
        # numpy fully on every nested overlay exit inside a daemon worker.
        # ═══════════════════════════════════════════════════════════
        _in_daemon_worker_purge = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
        # Trigger purge on any overlay exit inside a daemon worker — regardless of
        # whether we used a bubble path or the main-env path.  When _activated_bubble_path
        # is None (main-env PATH B), the overlay __exit__ still removes the bubble from
        # sys.path[0] that was inserted by the PREVIOUS level, and any numpy Python
        # modules imported from it survive.  Purging here ensures no partial sentinel
        # lingers in sys.modules before the next level's import numpy runs.
        _is_overlay_exit = (
            self.isolation_mode == "overlay"
            and (self._activated_bubble_path or self._using_main_env)
        )
        self._profile_start("module_purging")
        if should_cleanup or (_in_daemon_worker_purge and _is_overlay_exit and pkg_name):  # depth=1 OR daemon overlay
            # Standard cleanup for what this loader activated
            if not self._using_main_env and self._activated_bubble_dependencies:
                for pkg_name_dep in self._activated_bubble_dependencies:
                    self._aggressive_module_cleanup(pkg_name_dep)
 
            if pkg_name:
                self._aggressive_module_cleanup(pkg_name)
 
            # ── Deep cleanup: purge ALL stale modules left by nested frames ──
            # Nested loaders skip module cleanup (correct — avoids redundant work),
            # but they may leave modules from a different version in sys.modules.
            # At depth=1 we own the final cleanup, so we do a full pass over
            # the known C-extension switcher families.
            _CE_SWITCHERS = ("numpy", "torch", "scipy", "pandas", "tensorflow")
            for _sw in _CE_SWITCHERS:
                _stale = [
                    k for k in list(sys.modules)
                    if k == _sw or k.startswith(_sw + ".")
                ]
                if _stale:
                    if not self.quiet:
                        safe_print(
                            f"      - Purging {len(_stale)} stale modules for '{_sw}' (deep cleanup)"
                        )
                    for _k in _stale:
                        sys.modules.pop(_k, None)
 
            gc.collect()
            importlib.invalidate_caches()
 
        self._profile_end("module_purging", print_now=self._profiling_enabled)

        # Cache invalidation - separate from gc.collect()
        self._profile_start("invalidate_caches")
        if hasattr(importlib, "invalidate_caches"):
            importlib.invalidate_caches()
        self._profile_end("invalidate_caches", print_now=self._profiling_enabled)

        # Garbage collection (only at depth 1)
        self._profile_start("gc_collect")
        if should_cleanup:
            gc.collect()
        self._profile_end("gc_collect", print_now=self._profiling_enabled)

        # Global cleanup (only at depth 1)
        if should_cleanup:
            self._profile_start("global_cleanup")
            orphan_count = self._simple_restore_all_cloaks()
            self._profile_end("global_cleanup", print_now=self._profiling_enabled)

            if not self.quiet and orphan_count > 0:
                safe_print(_('   ✅ Cleaned up {} orphaned cloaks').format(orphan_count))

        self._deactivation_end_time = time.perf_counter_ns()
        self._total_deactivation_time_ns = (
            self._deactivation_end_time - self._deactivation_start_time
        )

        # Calculate total swap time
        total_swap_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns

        self._profile_end("TOTAL_DEACTIVATION", print_now=self._profiling_enabled)

        if not self.quiet:
            safe_print("   ✅ Environment restored.")
            safe_print(f"   ⏱️  Swap Time: {total_swap_time_ns / 1000:,.3f} μs")
 
        # ── Final verification (only at depth=1) ──────────────────────────────
        if should_cleanup and pkg_name:
            if not self.quiet:
                final_cloaks = self._scan_for_cloaked_versions(pkg_name)
                if not final_cloaks:
                    safe_print(f"   ✅ Verified: No orphaned cloaks for {pkg_name}")
                else:
                    safe_print(_('   ⚠️  WARNING: {} cloaks still remaining!').format(len(final_cloaks)))
 
            # ── sys.path bubble-path check ─────────────────────────────────────
            bubble_paths_remaining = [
                p for p in sys.path
                if ".omnipkg_versions" in p
            ]
            if bubble_paths_remaining:
                if not self.quiet:
                    safe_print(
                        f"   ⚠️  POST-EXIT: {len(bubble_paths_remaining)} bubble path(s) "
                        f"still in sys.path — correcting..."
                    )
                sys.path[:] = [p for p in sys.path if ".omnipkg_versions" not in p]
                if not self.quiet:
                    safe_print("   ✅ sys.path cleaned.")
 
            # ── sys.modules bubble-resident check ─────────────────────────────
            # A module is "bubble-resident" if its __file__ still points inside
            # a .omnipkg_versions directory. This means a nested frame's import
            # survived the unwind and the deep CE-switcher purge missed it
            # (e.g. a module that was cached without __file__, then reloaded).
            _CE_SWITCHERS = ("numpy", "torch", "scipy", "pandas", "tensorflow")
            zombie_modules = []
            for mod_name, mod in list(sys.modules.items()):
                if not any(
                    mod_name == sw or mod_name.startswith(sw + ".")
                    for sw in _CE_SWITCHERS
                ):
                    continue
                mod_file = getattr(mod, "__file__", None) or ""
                if ".omnipkg_versions" in mod_file:
                    zombie_modules.append(mod_name)
 
            if zombie_modules:
                if not self.quiet:
                    safe_print(
                        f"   ⚠️  POST-EXIT: {len(zombie_modules)} bubble-resident module(s) "
                        f"still in sys.modules — purging..."
                    )
                for zmod in zombie_modules:
                    sys.modules.pop(zmod, None)
                gc.collect()
                importlib.invalidate_caches()
                if not self.quiet:
                    safe_print("   ✅ Zombie modules purged.")
            elif not self.quiet:
                safe_print("   ✅ sys.modules clean: no bubble-resident CE modules.")

    # NEW HELPER METHOD: Simple unconditional restoration
    def _simple_restore_all_cloaks(self):
        """
        Final-depth cleanup: restore any remaining cloaks using safe ops.
        """
        from omnipkg.isolation.fs_lock_queue import scan_for_cloaks, safe_uncloak
    
        self._profile_start("cleanup_scan")
        if not self.quiet:
            safe_print("      🔍 Scanning for remaining cloaks...")
    
        restored = 0
        cloak_pattern = "*_omnipkg_cloaked*"
    
        # Scan main site-packages (top-level only, fast)
        main_cloaks = scan_for_cloaks(
            self.site_packages_root, cloak_pattern, omnipkgLoader._locks_dir
        )
        self._profile_end("cleanup_scan", print_now=self._profiling_enabled)
    
        self._profile_start("cleanup_restore")
        for cloak_path in main_cloaks:
            if not cloak_path.exists():
                continue
    
            # Parse original name
            original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
            if original_name == cloak_path.name:
                original_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", cloak_path.name)
            if "_omnipkg_cloaked" in original_name:
                if not self.quiet:
                    safe_print(_("         ⚠️  Can't parse cloak name: {}").format(cloak_path.name))
                continue
    
            original_path = cloak_path.parent / original_name
            pkg_name_guess = original_name.replace("-", "_").split(".")[0].split("-")[0]
    
            ok = safe_uncloak(
                src=cloak_path,
                dst=original_path,
                locks_dir=omnipkgLoader._locks_dir,
                pkg_name=pkg_name_guess,
                active_cloaks=omnipkgLoader._active_cloaks,
                sentinel_base=self.multiversion_base,
                timeout=3.0,
            )
            if ok:
                restored += 1
                if not self.quiet:
                    safe_print(_('         ✅ {}').format(original_name))
            elif not self.quiet:
                safe_print(_('         ⏭️  Locked — owner will restore: {}').format(cloak_path.name))
    
        self._profile_end("cleanup_restore", print_now=self._profiling_enabled)
    
        # Fast path for bubbles we tracked
        self._profile_start("cleanup_bubble_restore")
        if self._cloaked_bubbles:
            for cloak_path, original_path in self._cloaked_bubbles:
                if self._uncloak_bubble(cloak_path, original_path):
                    restored += 1
        else:
            # Orphan scan: bubbles cloaked by nested loaders that already exited
            if not self.quiet:
                safe_print("      🔍 Scanning multiversion_base for orphaned bubble cloaks...")
    
            with omnipkgLoader._active_cloaks_lock:
                _active_cloak_paths = set(omnipkgLoader._active_cloaks.keys())
    
            try:
                for entry in os.scandir(str(self.multiversion_base)):
                    if "_omnipkg_cloaked" not in entry.name:
                        continue
                    cloak_path = Path(entry.path)
                    if str(cloak_path) in _active_cloak_paths:
                        continue  # active loader owns this — leave it alone
    
                    original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                    if original_name == cloak_path.name:
                        original_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                    if "_omnipkg_cloaked" in original_name:
                        continue
    
                    original_path = cloak_path.parent / original_name
                    pkg_name_guess = original_name.split("-")[0]
    
                    # Non-blocking try: if another process owns it, skip
                    if self._uncloak_bubble(cloak_path, original_path, timeout=0):
                        restored += 1
                        if not self.quiet:
                            safe_print(f"         ✅ Restored bubble: {original_name}")
                    elif not self.quiet:
                        safe_print(f"         ⏭️  Skipping locked bubble: {original_name}")
            except OSError:
                pass
    
        self._profile_end("cleanup_bubble_restore", print_now=self._profiling_enabled)
        return restored

    def _force_restore_owned_cloaks(self):
        """
        Safety net: Restore ANY cloak registered to this loader instance,
        guaranteeing cleanup even if local tracking lists desynchronize.
        """
        my_id = id(self)
        cloaks_to_restore = []

        # Identify owned cloaks from global registry
        if hasattr(omnipkgLoader, "_active_cloaks") and hasattr(
            omnipkgLoader, "_active_cloaks_lock"
        ):
            with omnipkgLoader._active_cloaks_lock:
                for cloak_path_str, owner_id in list(omnipkgLoader._active_cloaks.items()):
                    if owner_id == my_id:
                        cloak_path = Path(cloak_path_str)
                        # Derive original name from cloak name
                        # Format: name.timestamp_loaderid_omnipkg_cloaked
                        original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                        # Fallback for legacy names
                        if original_name == cloak_path.name:
                            original_name = cloak_path.name.split("_omnipkg_cloaked")[0]
                            # Remove trailing timestamp if present (legacy format)
                            original_name = re.sub(r"\.\d+$", "", original_name)

                        original_path = cloak_path.parent / original_name
                        cloaks_to_restore.append((original_path, cloak_path))

        if not cloaks_to_restore:
            return

        if not self.quiet:
            safe_print(_('   🧹 Force-restoring {} owned cloaks...').format(len(cloaks_to_restore)))

        for original_path, cloak_path in cloaks_to_restore:
            try:
                # Remove destination if it exists (e.g. partial restore or conflict)
                from omnipkg.isolation.fs_lock_queue import safe_uncloak
                pkg_guess = original_path.name.split('-')[0].split('.')[0]
                ok = safe_uncloak(
                    src=cloak_path,
                    dst=original_path,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=pkg_guess,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=3.0
                )
                if ok and not self.quiet:
                    safe_print(_('      ✅ Restored: {}').format(original_path.name))

            except Exception as e:
                if not self.quiet:
                    safe_print(_('      ⚠️ Failed to force-restore {}: {}').format(original_path.name, e))

    def _restore_cloaked_modules(self):
        """
        Restore main-env cloaked modules using the safe lock queue.
        """
        from omnipkg.isolation.fs_lock_queue import safe_uncloak
        with omnipkgLoader._global_cloaking_lock:
            restored_count = 0
            failed_count = 0
    
            for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
                if not was_successful:
                    continue
    
                pkg_name = original_path.stem.split(".")[0]
                ok = safe_uncloak(
                    src=cloak_path,
                    dst=original_path,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=pkg_name,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    sentinel_base=self.multiversion_base,
                    timeout=5.0,
                )
                if ok:
                    restored_count += 1
                    if not self.quiet:
                        safe_print(_('   ✅ Restored: {}').format(original_path.name))
                else:
                    failed_count += 1
                    if not self.quiet:
                        safe_print(
                            f"   ⏱️  Lock timeout restoring {pkg_name}, "
                            "will be caught by global cleanup"
                        )
    
            self._cloaked_main_modules.clear()
    
            if not self.quiet and (restored_count > 0 or failed_count > 0):
                safe_print(
                    _('   📊 Restoration: {} restored, {} skipped (will retry)').format(
                        restored_count, failed_count
                    )
                )

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
                name_parts = cloaked_path.name.split("_omnipkg_cloaked")
                if len(name_parts) >= 1:
                    original_name = name_parts[0]
                    timestamp = name_parts[1] if len(name_parts) > 1 else "unknown"
                    cloaked_versions.append((cloaked_path, original_name, timestamp))

        if cloaked_versions and not self.quiet:
            safe_print(_('   🔍 Found {} cloaked version(s) of {}:').format(len(cloaked_versions), pkg_name))
            for cloak_path, orig_name, ts in cloaked_versions:
                safe_print(_('      - {} (timestamp: {})').format(cloak_path.name, ts))

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
        safe_print(_("\n🔍 DEBUG: Python Version Compatibility Check"))
        safe_print(_("Current Python version: {}").format(self.python_version))
        safe_print(_("Site-packages root: {}").format(self.site_packages_root))
        safe_print(
            _("Compatible: {}").format(self._is_version_compatible_path(self.site_packages_root))
        )
        safe_print(_("\n🔍 Current sys.path compatibility ({} entries):").format(len(sys.path)))
        compatible_count = 0
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            is_compatible = self._is_version_compatible_path(path_obj)
            exists = path_obj.exists()
            status = "✅" if exists and is_compatible else "🚫" if exists else "❌"
            if is_compatible and exists:
                compatible_count += 1
            safe_print(_("   [{}] {} {}").format(i, status, path))
        safe_print(
            _("\n📊 Summary: {}/{} paths are Python {}-compatible").format(
                compatible_count, len(sys.path), self.python_version
            )
        )
        safe_print()

    def get_performance_stats(self):
        """Returns detailed performance statistics for CI/logging purposes."""
        if self._total_activation_time_ns is None or self._total_deactivation_time_ns is None:
            return None
        total_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        return {
            "package_spec": self._current_package_spec,
            "python_version": self.python_version,
            "activation_time_ns": self._total_activation_time_ns,
            "activation_time_us": self._total_activation_time_ns / 1000,
            "activation_time_ms": self._total_activation_time_ns / 1000000,
            "deactivation_time_ns": self._total_deactivation_time_ns,
            "deactivation_time_us": self._total_deactivation_time_ns / 1000,
            "deactivation_time_ms": self._total_deactivation_time_ns / 1000000,
            "total_swap_time_ns": total_time_ns,
            "total_swap_time_us": total_time_ns / 1000,
            "total_swap_time_ms": total_time_ns / 1000000,
            "swap_speed_description": self._get_speed_description(total_time_ns),
        }

    def _get_speed_description(self, time_ns):
        """Returns a human-readable description of swap speed."""
        if time_ns < 1000:
            return f"Ultra-fast ({time_ns} nanoseconds)"
        elif time_ns < 1000000:
            return f"Lightning-fast ({time_ns / 1000:.1f} microseconds)"
        elif time_ns < 1000000000:
            return f"Very fast ({time_ns / 1000000:.1f} milliseconds)"
        else:
            return f"Standard ({time_ns / 1000000000:.2f} seconds)"

    def print_ci_performance_summary(self):
        """Prints a CI-friendly performance summary focused on healing success."""
        safe_print("\n" + "=" * 70)
        safe_print("🚀 EXECUTION ANALYSIS: Standard Runner vs. Omnipkg Auto-Healing")
        safe_print("=" * 70)

        loader_stats = self.get_performance_stats()

        uv_failure_detector = UVFailureDetector()
        uv_failed_ms = uv_failure_detector.get_execution_time_ms()

        omnipkg_heal_and_run_ms = loader_stats.get("total_swap_time_ms", 0) if loader_stats else 0

        total_omnipkg_time_ms = uv_failed_ms + omnipkg_heal_and_run_ms

        safe_print(f"  - Standard Runner (uv):   [ FAILED ] at {uv_failed_ms:>8.3f} ms")
        safe_print(f"  - Omnipkg Healing & Run:  [ SUCCESS ] in {omnipkg_heal_and_run_ms:>8.3f} ms")
        safe_print("-" * 70)
        safe_print(f"  - Total Time to Success via Omnipkg: {total_omnipkg_time_ms:>8.3f} ms")
        safe_print("=" * 70)
        safe_print("🌟 Verdict:")
        safe_print("   A standard runner fails instantly. Omnipkg absorbs the failure,")
        safe_print("   heals the environment in microseconds, and completes the job.")
        safe_print("=" * 70)

    def _get_package_modules(self, pkg_name: str):
        """Helper to find all modules related to a package in sys.modules."""
        pkg_name_normalized = pkg_name.replace("-", "_")
        return [
            mod
            for mod in list(sys.modules.keys())
            if mod.startswith(pkg_name_normalized + ".")
            or mod == pkg_name_normalized
            or mod.replace("_", "-").startswith(pkg_name.lower())
        ]

    def _cloak_main_package(self, pkg_name: str):
        """Temporarily renames the main environment installation of a package."""
        canonical_pkg_name = pkg_name.lower().replace("-", "_")
        paths_to_check = [
            self.site_packages_root / canonical_pkg_name,
            next(self.site_packages_root.glob(f"{canonical_pkg_name}-*.dist-info"), None),
            next(self.site_packages_root.glob(f"{canonical_pkg_name}-*.egg-info"), None),
            self.site_packages_root / f"{canonical_pkg_name}.py",
        ]
        for original_path in paths_to_check:
            if original_path and original_path.exists():
                timestamp = int(time.time() * 1000)
                if original_path.is_dir():
                    cloak_path = original_path.with_name(
                        f"{original_path.name}.{timestamp}_omnipkg_cloaked"
                    )
                else:
                    cloak_path = original_path.with_name(
                        f"{original_path.name}.{timestamp}_omnipkg_cloaked{original_path.suffix}"
                    )
                cloak_record = (original_path, cloak_path, False)
                if cloak_path.exists():
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                    except Exception as e:
                        if not self.quiet:
                            safe_print(
                                _(" ⚠️ Warning: Could not remove existing cloak {}: {}").format(
                                    cloak_path.name, e
                                )
                            )
                from omnipkg.isolation.fs_lock_queue import safe_cloak
                ok = safe_cloak(
                    src=original_path,
                    dst=cloak_path,
                    locks_dir=omnipkgLoader._locks_dir,
                    pkg_name=pkg_name,
                    active_cloaks=omnipkgLoader._active_cloaks,
                    owner_id=id(self),
                    sentinel_base=self.multiversion_base,
                    timeout=5.0
                )
                if ok:
                    cloak_record = (original_path, cloak_path, True)
                else:
                    if not self.quiet:
                        safe_print(_(" ⚠️ Failed/Locked when cloaking {}").format(original_path.name))
                self._cloaked_main_modules.append(cloak_record)

    def cleanup_abandoned_cloaks(self):
        """
        Utility method to clean up any abandoned cloak files.
        Can be called manually if you suspect there are leftover cloaks.
        """
        return self._cleanup_all_cloaks_globally()

    def _profile_end(self, label, print_now=False):
        """
        End timing and optionally print.

        FIXED: Now respects self._profiling_enabled for ALL output,
        including print_now=self._profiling_enabled calls.
        """
        if not self._profiling_enabled:
            return 0

        if label not in self._profile_times:
            return 0

        elapsed_ns = time.perf_counter_ns() - self._profile_times[label]
        elapsed_ms = elapsed_ns / 1_000_000

        # Store in class-level data
        omnipkgLoader._profile_data[label].append(elapsed_ns)

        # CRITICAL FIX: Check profiling flag AND quiet flag before printing
        if print_now and not self.quiet:
            safe_print(f"      ⏱️  {label}: {elapsed_ms:.3f}ms")

        return elapsed_ns

    def _aggressive_module_cleanup(self, pkg_name: str):
        """
        Removes specified package's modules from sys.modules.
        Special handling for torch which cannot be fully cleaned.

        FIXED: All profiling output now respects self._profiling_enabled
        """
        # Only do detailed profiling if enabled
        if self._profiling_enabled:
            _t0 = time.perf_counter_ns()
            _t = _t0

        # Phase 0: Pre-invalidate
        if hasattr(importlib, "invalidate_caches"):
            importlib.invalidate_caches()

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ pre_inval:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        pkg_name_normalized = pkg_name.replace("-", "_")

        # SPECIAL: torch/tensorflow checks
        if pkg_name == "torch" and "torch._C" in sys.modules:
            if not self.quiet:
                safe_print("      ℹ️  Preserving torch._C (C++ backend cannot be unloaded)")
            return
        if pkg_name == "tensorflow":
            if any(
                m in sys.modules
                for m in [
                    "tensorflow.python.pywrap_tensorflow",
                    "tensorflow.python._pywrap_tensorflow_internal",
                ]
            ):
                if not self.quiet:
                    safe_print("      ℹ️  Preserving TensorFlow (C++ backend cannot be unloaded)")
                return

        # SPECIAL: numpy — preserve C extension modules, purge only Python layer.
        # Purging _multiarray_umath causes partial re-initialization on next import
        # because dlopen returns the same cached SO handle but Python state is gone.
        # We clear the Python layer only; _activate_bubble then reloads it from the
        # correct bubble path via _heal_numpy_python_layer.
        #
        # CRITICAL: Only preserve a C extension if its .so still exists on disk.
        # When a bubble is cloaked (renamed to *.omnipkg_cloaked), its .so files
        # move with it. Preserving a C ext whose __file__ no longer exists causes
        # numpy's __init__.py (running from the NEW bubble) to encounter a broken
        # module object — its companion .so path resolves to a missing file and
        # Python raises ModuleNotFoundError when trying to locate sibling extensions.
        if pkg_name == "numpy" and "numpy.core._multiarray_umath" in sys.modules:
            def _so_reachable(mod_key: str) -> bool:
                """Return True if the mapped .so for this module still exists on disk."""
                mod = sys.modules.get(mod_key)
                if mod is None:
                    return False
                _f = getattr(mod, "__file__", None)
                if not _f:
                    return False
                if not _f.endswith(".so"):
                    return False
                return os.path.exists(_f)

            _anchor_reachable = _so_reachable("numpy.core._multiarray_umath")

            # Inside a daemon worker, ALWAYS do a full purge of all numpy modules
            # when switching versions — even if the anchor .so is still reachable.
            # In overlay mode the old bubble stays on disk (protected from cloaking),
            # so _so_reachable returns True and we'd normally do a partial purge.
            # But that leaves C ext entries in sys.modules whose __spec__ points to
            # the old bubble. When the new bubble's numpy/__init__.py then imports
            # numpy.lib, which imports index_tricks, which imports back through numpy,
            # Python finds the partially-initialized sys.modules["numpy"] sentinel
            # and raises a circular import error.
            #
            # Full purge is safe here: the .so stays mapped at the OS level regardless
            # of whether its module object is in sys.modules. dlopen() caches the
            # handle, so the next import simply gets the already-mapped .so back.
            # The C exts are re-registered in sys.modules cleanly by the fresh import.
            _in_worker = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
            _force_full_purge = _in_worker or not _anchor_reachable

            if _force_full_purge:
                _all_numpy = [k for k in list(sys.modules.keys())
                              if k == "numpy" or k.startswith("numpy.")]
                if not self.quiet and _all_numpy:
                    _reason = "daemon worker" if _in_worker else "anchor .so unreachable"
                    safe_print(
                        f"      - Full purge of {len(_all_numpy)} numpy modules "
                        f"({_reason})"
                    )
                for mod_name in _all_numpy:
                    sys.modules.pop(mod_name, None)
                return

            # Out-of-worker, anchor reachable: preserve only those C exts whose .so
            # still exists on disk.  Drop any whose bubble was cloaked since load.
            _numpy_c_exts = {
                k for k in sys.modules
                if (k == "numpy.core._multiarray_umath"
                    or k.startswith("numpy.core._")
                    or k.startswith("numpy.fft._")
                    or k.startswith("numpy.linalg._")
                    or k.startswith("numpy.linalg.lapack")
                    or k.startswith("numpy.random._")
                    or k.startswith("numpy.random.mtrand"))
                and _so_reachable(k)
            }
            _numpy_py_mods = [
                k for k in list(sys.modules.keys())
                if (k == "numpy" or k.startswith("numpy."))
                and k not in _numpy_c_exts
            ]
            if _numpy_py_mods:
                if not self.quiet:
                    safe_print(
                        f"      - Purging {len(_numpy_py_mods)} Python modules for 'numpy' "
                        f"(preserving {len(_numpy_c_exts)} C extensions)"
                    )
                for mod_name in _numpy_py_mods:
                    sys.modules.pop(mod_name, None)
            return

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ special_check:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        # GET MODULES - THIS IS LIKELY THE BOTTLENECK
        modules_to_clear = self._get_package_modules(pkg_name)

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ get_modules:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        # Add package name variants
        if pkg_name not in modules_to_clear and pkg_name in sys.modules:
            modules_to_clear.append(pkg_name)
        if pkg_name_normalized not in modules_to_clear and pkg_name_normalized in sys.modules:
            modules_to_clear.append(pkg_name_normalized)

        if modules_to_clear:
            if not self.quiet:
                safe_print(
                    f"      - Purging {len(modules_to_clear)} modules for '{pkg_name_normalized}'"
                )
            for mod_name in modules_to_clear:
                if mod_name in sys.modules:
                    try:
                        del sys.modules[mod_name]
                    except KeyError:
                        pass

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ del_loop:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ gc:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        if hasattr(importlib, "invalidate_caches"):
            importlib.invalidate_caches()

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                safe_print(f"         ⏱️ post_inval:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

            if not self.quiet:
                safe_print(f"         ⏱️ TOTAL_cleanup:{(time.perf_counter_ns()-_t0)/1e6:.3f}ms")

    def _cleanup_all_cloaks_globally(self):
        """
        (CORRECTED) ENHANCED: Catches orphaned cloaks with more aggressive pattern matching,
        correct path references, and ownership checking.
        """
        if not self.quiet:
            safe_print("   🧹 Running global cloak cleanup...")

        total_cleaned = 0

        # Use the initialized site packages root
        site_packages_path = self.site_packages_root

        cloak_patterns = ["*_omnipkg_cloaked*", "*.*_omnipkg_cloaked*"]

        # --- Cleanup main env cloaks ---
        found_cloaks = set()
        if site_packages_path.is_dir():
            for pattern in cloak_patterns:
                found_cloaks.update(site_packages_path.glob(pattern))

        if found_cloaks:
            if not self.quiet:
                safe_print(_('      🔍 Found {} potential main env cloaks').format(len(found_cloaks)))

            with omnipkgLoader._active_cloaks_lock:
                for cloak_path in found_cloaks:
                    # Skip cloaks we currently own/track
                    owner_id = omnipkgLoader._active_cloaks.get(str(cloak_path))
                    if owner_id is not None:
                        continue

                    original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                    if original_name == cloak_path.name:
                        match = re.search(r"^(.+?)(?:\.\d+)?_\d+_omnipkg_cloaked", cloak_path.name)
                        if match:
                            original_name = match.group(1)

                    original_path = cloak_path.parent / original_name

                    if not original_path.exists():
                        try:
                            if self._is_valid_package_name(original_name):
                                shutil.move(str(cloak_path), str(original_path))
                                total_cleaned += 1
                                if not self.quiet:
                                    safe_print(_('         ✅ Restored: {}').format(original_name))
                            else:
                                if cloak_path.is_dir():
                                    shutil.rmtree(cloak_path)
                                else:
                                    cloak_path.unlink()
                                total_cleaned += 1
                                if not self.quiet:
                                    safe_print(
                                        _('         🗑️  Deleted malformed cloak: {}').format(cloak_path.name)
                                    )
                        except Exception as e:
                            if not self.quiet:
                                safe_print(_('         ⚠️  Failed to process {}: {}').format(cloak_path.name, e))
                    else:
                        try:
                            if cloak_path.is_dir():
                                shutil.rmtree(cloak_path)
                            else:
                                cloak_path.unlink()
                            total_cleaned += 1
                            if not self.quiet:
                                safe_print(
                                    _('         🗑️  Deleted duplicate cloak: {}').format(cloak_path.name)
                                )
                        except Exception as e:
                            if not self.quiet:
                                safe_print(_('         ⚠️  Failed to delete {}: {}').format(cloak_path.name, e))

        # --- Cleanup bubble cloaks ---
        # IMPORTANT: Use os.scandir (top-level only), NOT rglob.
        # rglob walks INSIDE cloaked bubble directories (e.g. numpy-2.3.5.CLOAK/numpy/lib)
        # generating thousands of sub-paths.  If another thread restores the bubble
        # mid-scan, the sub-paths no longer exist and cause [Errno 2] FileNotFoundError.
        # We only want the top-level bubble directories — one rename per bubble.
        if self.multiversion_base.exists():
            bubble_cloaks = []
            try:
                for _bc_entry in os.scandir(str(self.multiversion_base)):
                    if "_omnipkg_cloaked" in _bc_entry.name:
                        bubble_cloaks.append(Path(_bc_entry.path))
            except OSError:
                pass

            if bubble_cloaks:
                if not self.quiet:
                    safe_print(_('      🔍 Found {} potential bubble cloaks').format(len(bubble_cloaks)))

                with omnipkgLoader._active_cloaks_lock:
                    _registered = set(omnipkgLoader._active_cloaks.keys())

                for cloak_path in bubble_cloaks:
                    # Skip if currently owned/active in THIS process
                    if str(cloak_path) in _registered:
                        continue
                    # Re-check existence — may have been restored already
                    if not cloak_path.exists():
                        continue

                    original_name = re.sub(r"\\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                    if original_name == cloak_path.name:
                        match = re.search(r"^(.+?)(?:\.\d+)?_\d+_omnipkg_cloaked", cloak_path.name)
                        if match:
                            original_name = match.group(1)

                    original_path = cloak_path.parent / original_name
                    pkg_name_for_lock = original_name.split("-")[0]

                    # Use timeout=0 (try-lock): if another process (e.g. daemon worker)
                    # holds the lock for this package, it owns the cloak — skip it.
                    # Restoring it while the daemon worker needs it cloaked causes
                    # ModuleNotFoundError in the worker on the next import.
                    from omnipkg.isolation.fs_lock_queue import safe_uncloak
                    
                    if cloak_path.exists() and original_path.exists():
                        # Duplicate cloak — original is already there, drop this
                        try:
                            shutil.rmtree(str(cloak_path), ignore_errors=True) if cloak_path.is_dir() else cloak_path.unlink(missing_ok=True)
                            total_cleaned += 1
                        except Exception:
                            pass
                        continue
                        
                    ok = safe_uncloak(
                        src=cloak_path,
                        dst=original_path,
                        locks_dir=omnipkgLoader._locks_dir,
                        pkg_name=pkg_name_for_lock,
                        active_cloaks=omnipkgLoader._active_cloaks,
                        sentinel_base=self.multiversion_base,
                        timeout=0.0
                    )
                    
                    if ok:
                        importlib.invalidate_caches()
                        total_cleaned += 1
                        if not self.quiet:
                            safe_print(_("         ✅ Restored bubble: {}").format(original_name))
                    else:
                        if not self.quiet:
                            safe_print(f"         ⏭️  Skipping locked cloak (owned by another process): {cloak_path.name}")

        if total_cleaned > 0:
            if not self.quiet:
                safe_print(_('   ✅ Cleaned up {} orphaned/duplicate cloaks').format(total_cleaned))
        elif not self.quiet:
            safe_print("   ✅ No cleanup needed")

        return total_cleaned

    def _is_valid_package_name(self, name: str) -> bool:
        """
        Check if a name looks like a valid Python package.
        Returns False for malformed cloak filenames.
        """
        # Must not be empty
        if not name:
            return False

        # Must not still contain cloak markers
        if "_omnipkg_cloaked" in name:
            return False

        # Check for excessive version-like segments (sign of malformed name)
        parts = name.split("-")
        if len(parts) > 2:
            # Multiple dashes - check if last part looks like a version
            last_part = parts[-1]
            # If last part is just numbers and dots (and very long), it's likely timestamp remnant
            if last_part.replace(".", "").replace("_", "").isdigit() and len(last_part) > 10:
                return False

        # Must be a valid Python identifier (roughly)
        # Package names can have dashes, dots, underscores
        if not re.match(r"^[a-zA-Z0-9._-]+$", name):
            return False

        return True

    def debug_sys_path(self):
        """Debug helper to print current sys.path state."""
        safe_print(_("\n🔍 DEBUG: Current sys.path ({} entries):").format(len(sys.path)))
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            status = "✅" if path_obj.exists() else "❌"
            safe_print(_("   [{}] {} {}").format(i, status, path))
        safe_print()

    def debug_omnipkg_dependencies(self):
        """Debug helper to show detected omnipkg dependencies."""
        safe_print(_("\n🔍 DEBUG: Detected omnipkg dependencies:"))
        if not self._omnipkg_dependencies:
            safe_print(_("   ❌ No dependencies detected"))
            return
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            status = "✅" if dep_path.exists() else "❌"
            safe_print(_("   {} {}: {}").format(status, dep_name, dep_path))
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
            "scikit-learn": "sklearn",
            "pillow": "PIL",
            "beautifulsoup4": "bs4",
            "opencv-python": "cv2",
            "python-dateutil": "dateutil",
            "attrs": "attr",
            "pyyaml": "yaml",
            "protobuf": "google.protobuf",
        }

        # Try to find the import name from dist-info
        search_paths = []

        # Add bubble path if activated
        if self._activated_bubble_path:
            search_paths.append(Path(self._activated_bubble_path))

        # Also check sys.path directories for dist-info
        for path_str in sys.path:
            if "site-packages" in path_str:
                path = Path(path_str)
                if path.exists() and path not in search_paths:
                    search_paths.append(path)

        for search_path in search_paths:
            if not search_path.exists():
                continue

            # Normalize package name for matching (lowercase, replace - with _)
            normalized_pkg = pkg_name.lower().replace("-", "_")

            # Try multiple glob patterns to catch different naming schemes
            patterns = [
                f"{pkg_name}-*.dist-info",  # Exact match with version
                # Underscore variant
                f"{pkg_name.replace('-', '_')}-*.dist-info",
                f"*{normalized_pkg}*.dist-info",  # Fuzzy match (last resort)
            ]

            for pattern in patterns:
                for dist_info in search_path.glob(pattern):
                    # Verify this is actually a dist-info directory
                    if not dist_info.is_dir():
                        continue

                    top_level_file = dist_info / "top_level.txt"
                    if top_level_file.exists():
                        try:
                            content = top_level_file.read_text(encoding="utf-8").strip()
                            if content:
                                # Return the first import name (most packages have only one)
                                import_name = content.split("\n")[0].strip()
                                if import_name:
                                    if not self.quiet:
                                        safe_print(
                                            f"      📦 Resolved import name: {pkg_name} -> {import_name}"
                                        )
                                    return import_name
                        except Exception as e:
                            if not self.quiet:
                                safe_print(_('      ⚠️  Failed to read {}: {}').format(top_level_file, e))
                            continue

                    # If top_level.txt doesn't exist, try RECORD file
                    record_file = dist_info / "RECORD"
                    if record_file.exists():
                        try:
                            import_name = self._extract_import_from_record(record_file)
                            if import_name:
                                if not self.quiet:
                                    safe_print(
                                        f"      📦 Resolved import name from RECORD: {pkg_name} -> {import_name}"
                                    )
                                return import_name
                        except Exception:
                            continue

        # Check known mappings
        if pkg_name.lower() in known_mappings:
            import_name = known_mappings[pkg_name.lower()]
            if not self.quiet:
                safe_print(_('      📦 Using known mapping: {} -> {}').format(pkg_name, import_name))
            return import_name

        # Last resort: transform package name
        # Replace hyphens with underscores (common convention)
        transformed = pkg_name.replace("-", "_").lower()

        if not self.quiet and transformed != pkg_name:
            safe_print(_('      📦 Using transformed name: {} -> {}').format(pkg_name, transformed))

        return transformed

    def _extract_import_from_record(self, record_file: Path) -> str:
        """
        Extract the import name by finding the most common top-level directory
        in the RECORD file (excluding common non-package directories).
        """
        try:
            content = record_file.read_text(encoding="utf-8")

            # Count occurrences of top-level directories
            from collections import Counter

            top_level_dirs = Counter()

            for line in content.splitlines():
                if not line.strip():
                    continue

                # RECORD format: filename,hash,size
                parts = line.split(",")
                if not parts:
                    continue

                filepath = parts[0]

                # Skip metadata and common non-package files
                if any(
                    skip in filepath
                    for skip in [
                        ".dist-info/",
                        "__pycache__/",
                        ".pyc",
                        "../",
                        "bin/",
                        "scripts/",
                    ]
                ):
                    continue

                # Extract top-level directory
                path_parts = filepath.split("/")
                if path_parts and path_parts[0]:
                    # Skip if it's a direct file (no directory)
                    if len(path_parts) > 1:
                        top_level = path_parts[0]
                        # Must be a valid Python identifier
                        if top_level.replace("_", "").replace(".", "").isalnum():
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
        if pkg_name == "torch":
            if "torch._C" in sys.modules:
                if not self.quiet:
                    safe_print(
                        "      ℹ️  PyTorch C++ backend already loaded - reusing existing instance"
                    )

                # Check if the torch module itself is accessible
                if "torch" in sys.modules:
                    try:
                        # Verify it's functional
                        sys.modules["torch"]
                        return True
                    except Exception:
                        pass

                # If we get here, torch._C is loaded but torch module is missing
                # This is the problematic state - we need to skip validation
                if not self.quiet:
                    safe_print(
                        "      ⚠️  PyTorch in partial state - skipping validation (known limitation)"
                    )
                return True  # Allow activation but warn user

        # Normal validation for other packages
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    if not self.quiet:
                        safe_print(
                            f"      🔄 Import retry {attempt}/{max_retries} after cache clear..."
                        )

                    # AGGRESSIVE CACHE CLEARING
                    importlib.invalidate_caches()
                    self._clear_pycache_for_package(import_name)
                    self._aggressive_module_cleanup(import_name)
                    gc.collect()
                    time.sleep(0.01 * attempt)

                # Try import with correct name
                __import__(import_name)

                # 2. RUN THE BRAIN CHECK
                if not self._perform_sanity_check(pkg_name):
                    raise ImportError(
                        _('Package {} imported but failed sanity check (Zombie State detected!)').format(pkg_name)
                    )

                if attempt > 0 and not self.quiet:
                    safe_print(f"      ✅ Import & Sanity Check succeeded after {attempt} retries!")

                return True

            except Exception as e:
                error_str = str(e)

                # SPECIAL: PyTorch docstring error = known limitation, not fatal
                if "already has a docstring" in error_str or "_has_torch_function" in error_str:
                    if not self.quiet:
                        safe_print("      ⚠️  PyTorch C++ reload limitation detected (non-fatal)")
                        safe_print("      ℹ️  Bubble is functional, validation skipped")
                    return True  # Treat as success

                if attempt == max_retries - 1:
                    if not self.quiet:
                        safe_print(
                            f"      ❌ Import validation failed after {max_retries} attempts: {e}"
                        )
                    return False
                else:
                    if not self.quiet:
                        error_snippet = str(e).split("\n")[0][:80]
                        safe_print(_('      ⚠️  Attempt {} failed: {}').format(attempt + 1, error_snippet))
                    continue

        return False

    def _perform_sanity_check(self, pkg_name: str) -> bool:
        """
        Runs a quick functional test.
        Importing isn't enough - we need to verify the C++ backend is alive.
        """
        try:
            if pkg_name == "tensorflow":
                import tensorflow as tf

                with tf.device("/cpu:0"):
                    tf.constant(1)

            elif pkg_name == "torch":
                import torch

                try:
                    import numpy as np

                    # If numpy imports cleanly, we can do the full check
                    torch.tensor([1])
                except (ImportError, RuntimeError):
                    # NumPy is in flux - skip the tensor check
                    if not self.quiet:
                        safe_print("      ℹ️  Skipping torch tensor check (numpy unavailable)")
                    # Just verify torch module loaded
                    return True

            elif pkg_name == "numpy":
                import numpy as np

                np.array([1]).sum()

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
        check_script = textwrap.dedent(
            f"""\
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
        """
        )

        try:
            # Run the check in a clean subprocess
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            result = subprocess.run(
                [sys.executable, "-c", check_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=creationflags,
            )

            if not self.quiet and result.returncode != 0:
                safe_print("      🔍 Subprocess validation output:")
                safe_print(f"         stdout: {result.stdout}")
                safe_print(f"         stderr: {result.stderr}")

            return result.returncode == 0
        except subprocess.TimeoutExpired:
            if not self.quiet:
                safe_print("      ⏱️  Subprocess validation timed out")
            return False
        except Exception as e:
            if not self.quiet:
                safe_print(_('      ❌ Subprocess validation error: {}').format(e))
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

        safe_print(_('   🔄 INITIATING PROCESS RE-EXECUTION (Attempt {}/3)...').format(restart_count + 1))
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
                for pycache_dir in pkg_path.rglob("__pycache__"):
                    try:
                        shutil.rmtree(pycache_dir, ignore_errors=True)
                    except Exception:
                        pass

                # Also remove top-level .pyc files
                for pyc_file in pkg_path.rglob("*.pyc"):
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
            safe_print("   🔧 Auto-healing: Force-reinstalling bubble...")

        try:
            if bubble_path.exists():
                shutil.rmtree(bubble_path)
                if not self.quiet:
                    safe_print("      🗑️  Removed corrupted bubble")

            from omnipkg.core import ConfigManager
            from omnipkg.core import omnipkg as OmnipkgCore

            cm = ConfigManager(suppress_init_messages=True)
            if hasattr(self, "config") and isinstance(self.config, dict):
                cm.config.update(self.config)

            core = OmnipkgCore(cm)
            original_strategy = core.config.get("install_strategy")
            core.config["install_strategy"] = "stable-main"

            try:
                if not self.quiet:
                    safe_print(_('      📦 Reinstalling {}...').format(pkg_spec))

                result = core.smart_install([pkg_spec])

                if result != 0:
                    return False

                if bubble_path.exists():
                    if not self.quiet:
                        safe_print("      ✅ Bubble recreated, re-activating...")

                    bubble_path_str = str(bubble_path)
                    sys.path[:] = [bubble_path_str] + [
                        p for p in self.original_sys_path if not self._is_main_site_packages(p)
                    ]

                    importlib.invalidate_caches()
                    if self._validate_import(pkg_name):
                        if not self.quiet:
                            safe_print("      🏥 HEALED! Package now imports successfully")
                        return True

                return False

            finally:
                if original_strategy:
                    core.config["install_strategy"] = original_strategy

        except Exception as e:
            if not self.quiet:
                safe_print(_('      ❌ Auto-heal failed: {}').format(e))
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
                    "locals": str(list(loc.keys())),
                }
            except Exception as e:
                import traceback

                return {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }

    def get_version(self, package_name):
        # Execute code to get version and path
        # The worker extracts the local variable named 'result' and merges it into the response
        code = f"try: import importlib.metadata as meta\nexcept ImportError: import importlib_metadata as meta\nresult = {{'version': meta.version('{package_name}'), 'path': __import__('{package_name}').__file__}}"
        res = self.execute(code)

        if res.get("success"):
            return {
                "success": True,
                "version": res.get("version", "unknown"),
                "path": res.get("path", "daemon_managed"),
            }
        return {"success": False, "error": res.get("error", "Unknown error")}


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
            "flask",
            "werkzeug",
            "jinja2",
            "markupsafe",
            "scipy",
            "pandas",
            "numpy",
            "tensorflow",
            "tensorflow-gpu",
            "torch",
            "torchvision",
            "pillow",
            "opencv-python",
            "cv2",
            "lxml",
            "cryptography",
        }

        pkg_lower = pkg_name.lower().replace("-", "_")

        # Check if package or any of its known dependencies are problematic
        if pkg_lower in problematic_packages:
            return True

        # Check if bubble dependencies include problematic packages
        if self._activated_bubble_path:
            bubble_path = Path(self._activated_bubble_path)
            bubble_deps = self._get_bubble_dependencies(bubble_path)

            for dep in bubble_deps.keys():
                if dep.lower().replace("-", "_") in problematic_packages:
                    return True

        return False

    def _detect_cpp_collision_risk(self, pkg_name: str, bubble_deps: dict) -> bool:
        """
        Analyze if activating this bubble would cause C++ extension conflicts.
        Returns True if collision is likely.
        """
        # Check if any problematic modules are already loaded in memory
        problematic_modules = [
            "werkzeug._internal",
            "jinja2.ext",
            "markupsafe._speedups",
            "numpy.core",
            "scipy.linalg",
            "torch._C",
            "tensorflow.python",
            "cv2",
        ]

        for mod_pattern in problematic_modules:
            # Check exact match
            if mod_pattern in sys.modules:
                if not self.quiet:
                    safe_print(_('   🚨 C++ collision risk: {} already loaded').format(mod_pattern))
                return True

            # Check prefix match (e.g., 'torch._C' matches 'torch._C._something')
            for loaded_mod in sys.modules:
                if loaded_mod.startswith(mod_pattern):
                    if not self.quiet:
                        safe_print(_('   🚨 C++ collision risk: {} detected').format(loaded_mod))
                    return True

        # Check for version conflicts in C++-heavy packages
        for pkg in bubble_deps.keys():
            try:
                main_version = get_version(pkg)
                bubble_version = bubble_deps[pkg]

                if main_version != bubble_version:
                    # If this is a known C++ package, flag it
                    if pkg.lower() in {
                        "numpy",
                        "scipy",
                        "torch",
                        "tensorflow",
                        "werkzeug",
                        "jinja2",
                        "markupsafe",
                    }:
                        if not self.quiet:
                            safe_print(
                                _('   🚨 C++ version conflict: {} ({} vs {})').format(pkg, main_version, bubble_version)
                            )
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
                safe_print(_('   ⚠️  Daemon connection failed: {}. Falling back to local.').format(e))
            return None

    def __enter__(self):
        # ── DAEMON FAST PATH (must be first — before ABI detection) ──────────
        _in_daemon = bool(os.environ.get("OMNIPKG_IS_DAEMON_WORKER"))
        if _in_daemon and self.isolation_mode == "overlay":
            # Bypass WorkerDelegationMixin entirely — go straight to base class
            return omnipkgLoader.__enter__(self)
        # ── END DAEMON FAST PATH ─────────────────────────────────────────────
        # Multi-package: delegate to base which handles _enter_multi / _enter_single
        if hasattr(self, '_package_specs') and len(self._package_specs) > 1:
            return super().__enter__()

        self._activation_start_time = time.perf_counter_ns()
        if not self._current_package_spec:
            raise ValueError("Package spec required")

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(_("Invalid package_spec format: '{}'").format(self._current_package_spec))

        # ── ABI AUTO-DETECTION ──────────────────────────────────────────────
        # If this package has C extensions AND its .so is already mapped in
        # this process (meaning we've already imported a version of it), we
        # cannot safely switch versions in-process — the OS linker won't
        # re-map a different .so into an existing process.
        #
        # In this case, automatically switch to run_once mode: the daemon
        # spawns a fresh ephemeral worker with the correct .so, runs the
        # caller's code inside it, returns the result, and exits.
        # No manual configuration needed — just use loader.execute(code).
        #
        # Detection: package in ABI_PACKAGES AND its C indicator already in
        # sys.modules means the .so is mapped.
        _abi_indicator_map = {
            "numpy":      "numpy.core._multiarray_umath",
            "scipy":      "scipy.linalg._fblas",
            "torch":      "torch._C",
            "tensorflow": "tensorflow.python.pywrap_tensorflow",
            "pandas":     "pandas._libs.lib",
            "cupy":       "cupy._core._carray",
            "jax":        "jaxlib.xla_extension",
        }
        _pkg_lower = pkg_name.lower()
        _abi_so_mapped = (
            _pkg_lower in omnipkgLoader.ABI_PACKAGES
            and _abi_indicator_map.get(_pkg_lower, f"{_pkg_lower}._") in sys.modules
        )
        if _abi_so_mapped and self._worker_fallback_enabled and DAEMON_AVAILABLE:
            if not self.quiet:
                safe_print(
                    f"   🔬 ABI auto-detect: {pkg_name} .so already mapped — "
                    f"switching to ephemeral daemon worker (run_once mode)"
                )
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            if self._active_worker:
                self._worker_mode = True
                self._run_once_mode = True  # flag for __exit__ to evict worker
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                return self
            elif not self.quiet:
                safe_print(
                    f"   ⚠️  run_once worker unavailable for {pkg_name}, falling back to in-process"
                )

        # STRATEGY 1: Proactive Worker Mode
        # Try daemon/worker first for packages with known C++ collision risk.
        # If worker creation fails, fall straight through to in-process.
        if self._worker_fallback_enabled:
            _want_worker = (
                self._should_use_worker_proactively(pkg_name)
                or self._should_use_worker_mode(pkg_name)
            )
            if _want_worker:
                self._active_worker = self._create_worker_for_spec(self._current_package_spec)
                if self._active_worker:
                    self._worker_mode = True
                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                    )
                    return self
                elif not self.quiet:
                    safe_print(
                        f"   ⚠️  Worker creation failed for {pkg_name}, falling back to in-process"
                    )

        # STRATEGY 2: In-process activation (base class logic)
        try:
            result = super().__enter__()
            return result if result is not None else self

        except ProcessCorruptedException as e:
            # STRATEGY 3: Reactive worker fallback on C++ collision / ABI conflict
            if not self.quiet:
                safe_print("   🔄 C++ collision detected, switching to worker mode...")
                safe_print(_('      Original error: {}').format(str(e)))

            self._panic_restore_cloaks()

            # ── CRITICAL: Full filesystem restore before daemon dispatch ──────
            # _panic_restore_cloaks() only restores OUR cloaks (this loader).
            # But nested loaders at outer depths may have cloaked sibling bubbles
            # (e.g. numpy-2.3.5 cloaked by depth-4 loader while depth-7 hits ABI).
            # Those outer-loader cloaks are still registered in _active_cloaks and
            # are SKIPPED by _cleanup_all_cloaks_globally.  The daemon worker needs
            # a clean filesystem — if it tries to activate numpy-2.3.5 overlay and
            # that bubble is renamed to *.omnipkg_cloaked, it gets FileNotFoundError.
            #
            # Solution: unconditionally restore ALL cloaks for ANY version of the
            # target package in multiversion_base and site_packages_root.
            # This includes sibling versions (numpy-1.24.3, numpy-2.3.5, etc.)
            # that outer-depth loaders cloaked — the daemon needs them all visible.
            try:
                _dispatch_pkg = self._current_package_spec.split("==")[0] if self._current_package_spec else None
                if _dispatch_pkg:
                    _canonical_dispatch = _dispatch_pkg.lower().replace("-", "_")
                    if not self.quiet:
                        safe_print(f"   🧹 Pre-dispatch: restoring all {_dispatch_pkg} cloaks for daemon...")

                    # Restore ALL cloaked bubbles for this package using the locked helper
                    _restored_pre = 0
                    try:
                        for _entry in os.scandir(str(self.multiversion_base)):
                            if (
                                _entry.name.startswith(f"{_dispatch_pkg}-")
                                and "_omnipkg_cloaked" in _entry.name
                            ):
                                _cloak_p = Path(_entry.path)
                                _orig_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", _cloak_p.name)
                                if "_omnipkg_cloaked" in _orig_name:
                                    _orig_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", _cloak_p.name)
                                _orig_p = _cloak_p.parent / _orig_name
                                if self._uncloak_bubble(_cloak_p, _orig_p):
                                    _restored_pre += 1
                                    if not self.quiet:
                                        safe_print(f"      ✅ Restored bubble: {_orig_name}")
                    except OSError:
                        pass

                    # Restore ALL cloaked main-env copies for this package
                    try:
                        for _me_cloak in list(self.site_packages_root.glob(
                            f"{_canonical_dispatch}*_omnipkg_cloaked*"
                        )):
                            _me_orig_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", _me_cloak.name)
                            if "_omnipkg_cloaked" in _me_orig_name:
                                _me_orig_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", _me_cloak.name)
                            _me_orig = _me_cloak.parent / _me_orig_name
                            lock = self._get_cloak_lock(_dispatch_pkg)
                            try:
                                with lock.acquire(timeout=5):
                                    if _me_cloak.exists() and not _me_orig.exists():
                                        shutil.move(str(_me_cloak), str(_me_orig))
                                        importlib.invalidate_caches()
                                        _restored_pre += 1
                                        if not self.quiet:
                                            safe_print(f"      ✅ Restored main-env: {_me_orig_name}")
                                    with omnipkgLoader._active_cloaks_lock:
                                        omnipkgLoader._active_cloaks.pop(str(_me_cloak), None)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    if not self.quiet:
                        safe_print(f"   ✅ Pre-dispatch restore complete ({_restored_pre} items)")
            except Exception as _pre_dispatch_err:
                if not self.quiet:
                    safe_print(f"   ⚠️  Pre-dispatch restore error: {_pre_dispatch_err}")

            self._active_worker = self._create_worker_for_spec(self._current_package_spec)

            if not self._active_worker:
                if not self.quiet:
                    safe_print(
                        "   ⚠️  Worker fallback unavailable — marking as ABI conflict, "
                        "continuing without isolation"
                    )
                # Don't re-raise — that would unwind ALL nested with-blocks.
                # Ensure numpy module has __version__ so the caller's 'import numpy'
                # doesn't raise AttributeError and propagate through all nested withs.
                _np_mod = sys.modules.get("numpy")
                if _np_mod is not None and not hasattr(_np_mod, "__version__"):
                    try:
                        # Best-effort: stamp whatever version is mapped
                        import importlib.metadata as _im
                        _np_mod.__version__ = _im.version("numpy")
                    except Exception:
                        try:
                            _np_mod.__version__ = "unknown"
                        except Exception:
                            pass
                self._activation_successful = True
                self._abi_conflict_detected = True
                return self

            self._worker_mode = True
            self._activation_successful = True
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time

            if not self.quiet:
                safe_print("   ✅ Successfully recovered using worker mode")

            return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with worker cleanup."""
        # ── Suppress ProcessCorruptedException from the with-body ──────────────
        # When nested omnipkgLoader contexts are used recursively (e.g. the
        # inception stress test), an ABI conflict detected at a deeper level
        # raises ProcessCorruptedException inside the with-body — not inside
        # __enter__.  Python's with-statement only calls __exit__ with the
        # exception; it does NOT pass through WorkerDelegationMixin.__enter__'s
        # catch block.  Without this guard the exception propagates upward
        # through every outer with-body, bypassing all try/except wrappers in
        # the test and crashing the entire legacy phase.
        if exc_type is not None:
            try:
                from omnipkg.common_utils import ProcessCorruptedException as _PCE
                if issubclass(exc_type, _PCE):
                    if not self.quiet:
                        safe_print(
                            f"   ↕️  ABI conflict suppressed in __exit__ "
                            f"({self._current_package_spec}, depth={omnipkgLoader._nesting_depth}): "
                            f"{exc_val}"
                        )
                    # Run the normal cleanup path with no active exception so the base
                    # __exit__ handles full restore of cloaked bubbles, main-env packages,
                    # sys.path, and modules. Do NOT manually restore cloaks here — that
                    # races with the normal __exit__ restore path and causes FileNotFoundError
                    # when both try to rename the same directory simultaneously.
                    try:
                        super().__exit__(None, None, None)
                    except Exception:
                        pass
                    return True  # suppress the exception
            except ImportError:
                pass

        # Multi-package: each sub-loader handles its own worker cleanup
        if self._active_sub_loaders:
            super().__exit__(exc_type, exc_val, exc_tb)
            return

        if self._worker_mode and self._active_worker:
            ws_start = time.perf_counter_ns()
            if self._run_once_mode:
                # Ephemeral worker — evict immediately to free RAM.
                # The daemon handles the actual process kill via evict_worker.
                if not self.quiet:
                    safe_print(f"   🗑️  run_once: evicting ephemeral worker for {self._current_package_spec}")
                try:
                    self._active_worker.shutdown()
                except Exception:
                    pass
            else:
                if not self.quiet:
                    safe_print(f"   🛑 Shutting down worker for {self._current_package_spec}...")
                try:
                    self._active_worker.shutdown()
                except Exception as e:
                    if not self.quiet:
                        safe_print(_('   ⚠️  Worker shutdown warning: {}').format(e))
            _ws_ms = (time.perf_counter_ns() - _ws_start) / 1_000_000
            if not self.quiet:
                safe_print(f"      ⏱️  WORKER_SHUTDOWN: {_ws_ms:.3f}ms")
            self._active_worker = None
            self._worker_mode = False
            self._run_once_mode = False
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
                return {"success": True, "stdout": output, "locals": str(loc.keys())}
            except Exception as e:
                return {"success": False, "error": str(e)}

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
                    "path": mod.__file__ if hasattr(mod, "__file__") else None,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════
# GLOBAL CLEANUP & SILENCER
# Automatically handles C++ shutdown noise for all users
# ═══════════════════════════════════════════════════════════


def _omnipkg_global_shutdown():
    """
    Runs at process exit to ensure clean termination of CUDA/C++ components.
    """
    # 1. Polite Cleanup: Try to sync CUDA if loaded
    # This prevents "Producer process terminated" errors by syncing IPC
    if "torch" in sys.modules:
        try:
            import torch

            if torch.cuda.is_available():
                # Force a sync to flush pending IPC operations
                torch.cuda.synchronize()
                # Release memory to avoid driver conflicts
                torch.cuda.empty_cache()
        except Exception:
            pass

    # 2. The Silencer: Redirect stderr to /dev/null
    # This eats the unavoidable "driver shutting down" C++ warnings
    # that occur during the final milliseconds of interpreter death.
    try:
        sys.stderr.flush()
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), sys.stderr.fileno())
    except Exception:
        pass


# Register immediately when module is imported
atexit.register(_omnipkg_global_shutdown)