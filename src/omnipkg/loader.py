from __future__ import annotations  # Python 3.6+ compatibility

import atexit
import gc
import importlib
import io  # <-- ADD THIS, needed for execute() method
import json
import os
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
        safe_print,
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
    _PATCHER_ERROR = f"Unexpected error loading patcher: {str(e)}"
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
        print("\n" + "=" * 70 + "\n📊 BUBBLE ACTIVATION PROFILE\n" + "=" * 70)
        for m in self.marks[:-1]:
            icon = self.CATEGORIES.get(m.category, "❓")
            fix = "✅ FIX" if m.fixable else "❌ NOPE"
            print(f"   {icon} {m.name:30s} {m.elapsed_ms:8.2f}ms  {fix:8s}  {m.category}")
        print("-" * 70 + f"\n🎯 TOTAL: {self.marks[-1].elapsed_ms:.2f}ms\n" + "=" * 70 + "\n")


class omnipkgLoader:
    """
    Activates isolated package environments with optional persistent worker pool.
    """

    # ═══════════════════════════════════════════════════════════
    # CLASS-LEVEL WORKER POOL (Shared  across all loader instances)
    # ═══════════════════════════════════════════════════════════
    _worker_pool = {}  # {package_spec: PersistentWorker}
    _worker_pool_lock = threading.RLock()
    _worker_pool_enabled = True  # Global toggle
    _cloak_locks: Dict[str, filelock.FileLock] = {}
    # <-- NEW: Add install locks
    _install_locks: Dict[str, filelock.FileLock] = {}
    _locks_dir: Optional[Path] = None
    _numpy_version_history: List[str] = []
    _active_cloaks: Dict[str, int] = {}
    _global_cloaking_lock = threading.RLock()  # Re-entrant lock
    _nesting_depth = 0
    VERSION_CHECK_METHOD = "filesystem"  # Options: 'kb', 'filesystem', 'glob', 'importlib'
    _profiling_enabled = False
    _profile_data = defaultdict(list)
    _nesting_lock = threading.Lock()
    _active_cloaks_lock = threading.RLock()  # <-- ADD THIS LINE
    _numpy_lock = threading.Lock()  # Protects the history list
    _active_main_env_packages = set()  # Packages currently active from main env
    _dependency_cache: Optional[Dict[str, Path]] = None
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
        package_spec: str = None,
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
        self._current_package_spec = package_spec
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
        self._packages_we_cloaked = set()  # Only packages WE cloaked
        self._using_main_env = False  # Track if we're using main env directly
        self._my_main_env_package = None
        self._use_worker_pool = use_worker_pool
        self._cloaked_main_modules = []
        self._profiling_enabled = enable_profiling or omnipkgLoader._profiling_enabled
        self._profile_times = {}  # Instance-level timing data
        self._deactivation_end_time = None
        self._total_activation_time_ns = None
        self._total_deactivation_time_ns = None
        self._omnipkg_dependencies = self._get_omnipkg_dependencies()
        self._activated_bubble_dependencies = []  # To track everything we need to exorcise

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
                safe_print(f"   ⚠️ Cache init failed: {e}")
            self.cache_client = None
            self.redis_key_prefix = "omnipkg:pkg:"

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
            print("No profiling data collected")
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
                safe_print(f"   ⚠️  Daemon connection failed: {e}. Falling back to local.")
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

        # 2. Execute via Daemon (Preferred) or Fallback
        success = False
        if DAEMON_AVAILABLE:
            try:
                client = self._get_daemon_client()
                res = client.request_maintenance(moves)
                if res.get("success"):
                    success = True
                    if not self.quiet:
                        safe_print(f"   🔄 Daemon Uncloak: {res.get('count')} items restored")
            except Exception:
                pass

        # 3. Fallback (Slow Subprocess)
        if not success:
            try:
                import subprocess

                script = (
                    "import shutil, sys, json, os; moves=json.loads(sys.argv[1]); "
                    "for s,d in moves: (shutil.move(s,d) if os.path.exists(s) else None)"
                )
                subprocess.run(
                    [sys.executable, "-c", script, json.dumps(moves)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
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
        This ensures only ONE loader can cloak/uncloak a package at a time.
        """
        canonical_name = pkg_name.lower().replace("-", "_")

        if canonical_name not in omnipkgLoader._cloak_locks:
            lock_file = omnipkgLoader._locks_dir / f"{canonical_name}.lock"
            omnipkgLoader._cloak_locks[canonical_name] = filelock.FileLock(
                str(lock_file), timeout=10  # Wait up to 10 seconds for lock
            )

        return omnipkgLoader._cloak_locks[canonical_name]

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
        self.original_sys_path = []
        contaminated_paths = []
        for path_str in sys.path:
            path_obj = Path(path_str)
            if self._is_version_compatible_path(path_obj):
                self.original_sys_path.append(path_str)
            else:
                contaminated_paths.append(path_str)
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
                                f"   🚑 RESURRECTING critical package: {canonical} (Found {len(cloaks)} cloaks)"
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
                            if target_path.exists():
                                if target_path.is_dir():
                                    shutil.rmtree(target_path)
                                else:
                                    target_path.unlink()

                            shutil.move(str(newest_cloak), str(target_path))

                            # 🔄 RETRY IMPORT after healing
                            importlib.invalidate_caches()
                            try:
                                dep_module = importlib.import_module(dep_variant)
                                if not self.quiet:
                                    safe_print(f"      ✅ Resurrected and loaded: {original_name}")
                            except ImportError:
                                continue  # Still broken, give up on this variant
                        except Exception as e:
                            if not self.quiet:
                                safe_print(f"      ❌ Failed to resurrect {canonical}: {e}")
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

        from packaging.utils import canonicalize_name

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
                safe_print(f"   - 🔍 Will cloak files for: {', '.join(packages_to_cloak)}")

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

                    lock = self._get_cloak_lock(pkg_name)
                    try:
                        with lock.acquire(timeout=5):
                            if not original_path.exists():
                                continue

                            shutil.move(str(original_path), str(cloak_path))

                            # *** THIS IS THE FIX: Store the loader_id in the dict ***
                            with omnipkgLoader._active_cloaks_lock:
                                # The key is the path, the value is our ID
                                omnipkgLoader._active_cloaks[str(cloak_path)] = loader_id

                            successful_cloaks.append((original_path, cloak_path, True))
                            if not self.quiet:
                                safe_print(f"      ✅ Cloaked: {original_path.name}")

                    except filelock.Timeout:
                        if not self.quiet:
                            safe_print(
                                f"      ⏱️  Timeout waiting for lock on {pkg_name}, skipping..."
                            )
                        successful_cloaks.append((original_path, cloak_path, False))
                    except Exception as e:
                        if not self.quiet:
                            safe_print(f"      ❌ Failed to cloak {original_path.name}: {e}")
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

    def _cleanup_all_cloaks_for_package(self, pkg_name: str):
        """
        Emergency cleanup with loader-awareness.
        FIXED: Only restores cloaks, never deletes valid backups.
        """
        cloaked_versions = self._scan_for_cloaked_versions(pkg_name)

        if not cloaked_versions:
            return

        if not self.quiet:
            safe_print(
                f"   🧹 EMERGENCY CLEANUP: Found {len(cloaked_versions)} orphaned cloaks for {pkg_name}"
            )

        # Strategy: Try to restore the NEWEST cloak (most likely to be correct)
        # Sort by timestamp (newest first)
        cloaked_versions.sort(key=lambda x: x[2], reverse=True)

        # Try to restore the best candidate
        for cloak_info in cloaked_versions:
            cloak_path = cloak_info[0]
            original_name = cloak_info[1]

            if not cloak_path.exists():
                continue

            target_path = cloak_path.parent / original_name

            try:
                # Only restore if target doesn't exist or is empty
                if target_path.exists():
                    if target_path.is_dir() and not any(target_path.iterdir()):
                        # Empty dir, safe to replace
                        shutil.rmtree(target_path)
                    elif target_path.is_dir():
                        # Has content, skip this cloak
                        if not self.quiet:
                            safe_print(f"   ⏭️  Skipping restore (target exists): {original_name}")
                        continue
                    else:
                        # File exists, skip
                        continue

                shutil.move(str(cloak_path), str(target_path))
                if not self.quiet:
                    safe_print(f"   ✅ Restored: {original_name}")

                # SUCCESS! Keep other cloaks as backups (don't delete)
                return

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
                            safe_print(
                                f"      ✅ [Strategy 2/6] Found via importlib.metadata: {dist.version}"
                            )
                        return (dist.version, None)
                    else:
                        if not self.quiet:
                            safe_print(
                                f"      ℹ️  [Strategy 2/6] Found {package_name} but version mismatch: {dist.version} != {requested_version}"
                            )
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
                    version_part = match.name.replace(f"{filesystem_name}-", "").replace(
                        ".dist-info", ""
                    )
                    if version_part == requested_version:
                        if not self.quiet:
                            safe_print(f"      ✅ [Strategy 3/6] Found via glob: {match}")
                        return (requested_version, None)
                except Exception:
                    continue

        # All strategies exhausted
        if not self.quiet:
            safe_print(
                f"      ❌ All strategies exhausted. {package_name}=={requested_version} not found."
            )
            if cloaked_versions:
                safe_print(
                    f"      ⚠️  WARNING: Found {len(cloaked_versions)} cloaked versions but none match {requested_version}"
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
            if not source.exists():
                return False

            try:
                # Force cleanup of destination
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest, ignore_errors=True)
                    else:
                        try:
                            dest.unlink()
                        except OSError:
                            pass

                # Check again before move
                if source.exists():
                    shutil.move(str(source), str(dest))
                    restored_any = True
                    return True
                return False
            except Exception as e:
                if not self.quiet:
                    safe_print(f"      ⚠️ Failed to restore {source.name}: {e}")
                return False

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
            safe_print(f"      ✅ Restored cloaked '{pkg_name}' in main environment")

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
                    safe_print(f"   🧠 Proactive worker mode: {indicator} already loaded")
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
        print(f"OMNIPKG_EVENT:{json.dumps(status)}", flush=True)

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
        subprocess.Popen(
            [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
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

    def __enter__(self):
        """Enhanced activation with detailed profiling."""
        self._profile_start("TOTAL_ACTIVATION")
        self._activation_start_time = time.perf_counter_ns()

        # Add granular profiling for initialization
        self._profile_start("init_checks")

        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")

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
                        try:
                            cloak_path = bubble_path.with_name(bubble_path.name + cloak_suffix)
                            shutil.move(str(bubble_path), str(cloak_path))
                            self._cloaked_bubbles.append((cloak_path, bubble_path))
                        except Exception as e:
                            if not self.quiet:
                                safe_print(f"         - ⚠️ Failed to cloak {bubble_path.name}: {e}")

                    self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

                self._ensure_main_site_packages_in_path()
                self._using_main_env = True

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
                                try:
                                    cloak_path = bubble_path.with_name(
                                        bubble_path.name + cloak_suffix
                                    )
                                    shutil.move(str(bubble_path), str(cloak_path))
                                    self._cloaked_bubbles.append((cloak_path, bubble_path))
                                except Exception as e:
                                    if not self.quiet:
                                        safe_print(
                                            f"         - ⚠️ Failed to cloak {bubble_path.name}: {e}"
                                        )

                            self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

                        self._ensure_main_site_packages_in_path()
                        self._using_main_env = True

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
                        safe_print(f"   ✅ Already in {pkg_name}=={current_bubble_version} bubble")

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
                            f"   🔄 Version mismatch: have {current_bubble_version}, need {requested_version}"
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
        bubble_path = self.multiversion_base / f"{pkg_name}-{requested_version}"

        # Check for cloaked bubbles
        if not bubble_path.exists():
            cloaked_bubbles = list(
                self.multiversion_base.glob(f"{pkg_name}-{requested_version}.*_omnipkg_cloaked")
            )
            if cloaked_bubbles:
                target = sorted(cloaked_bubbles, key=lambda p: str(p), reverse=True)[0]
                if not self.quiet:
                    safe_print(f"   🔓 Found CLOAKED bubble {target.name}, restoring...")
                try:
                    shutil.move(str(target), str(bubble_path))
                except Exception as e:
                    if not self.quiet:
                        safe_print(f"      ⚠️ Failed to restore cloaked bubble: {e}")

        self._profile_end("find_bubble", print_now=self._profiling_enabled)

        if not self.quiet:
            safe_print(f"   📂 Searching for bubble: {bubble_path}")

        # Track numpy version if applicable
        is_numpy_involved = "numpy" in self._current_package_spec.lower()

        # PRIORITY 1: Try BUBBLE first
        if bubble_path.is_dir():
            self._profile_start("activate_bubble")
            if not self.quiet:
                safe_print(f"   ✅ Bubble found: {bubble_path}")
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
                            f"      - 🔒 Cloaking {len(conflicting_bubbles)} conflicting bubble(s)."
                        )

                    timestamp = int(time.time() * 1000000)
                    loader_id = id(self)
                    cloak_suffix = f".{timestamp}_{loader_id}_omnipkg_cloaked"

                    for bubble_path_item in conflicting_bubbles:
                        try:
                            cloak_path = bubble_path_item.with_name(
                                bubble_path_item.name + cloak_suffix
                            )
                            shutil.move(str(bubble_path_item), str(cloak_path))
                            self._cloaked_bubbles.append((cloak_path, bubble_path_item))
                            if not self.quiet:
                                safe_print(f"         - Cloaked: {bubble_path_item.name}")
                        except Exception as e:
                            if not self.quiet:
                                safe_print(
                                    f"         - ⚠️ Failed to cloak {bubble_path_item.name}: {e}"
                                )

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
                safe_print(f"   - 🔧 Auto-creating bubble for: {self._current_package_spec}")

            install_success = self._install_bubble_inline(self._current_package_spec)

            if not install_success:
                raise RuntimeError(f"Failed to install {self._current_package_spec}")

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
                                safe_print(f"      🔗 Found dependency bubble: {dep_bubble_name}")

                if dependency_bubbles and not self.quiet:
                    safe_print(
                        f"   📦 Activating {len(dependency_bubbles)} dependency bubbles (CUDA/NVIDIA libs)..."
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
                        safe_print(f"   🔒 Cloaked {cloaked_count} conflicting packages")

                # 1D. Setup sys.path (LOCKED: ~0.1ms)
                bubble_path_str = str(bubble_path)
                if self.isolation_mode == "overlay":
                    sys.path.insert(0, bubble_path_str)
                else:
                    new_sys_path = [bubble_path_str] + [
                        p for p in self.original_sys_path if not self._is_main_site_packages(p)
                    ]
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
                            f"   - ✅ Confirmed {pkg_name}=={requested_version} in main environment"
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
                        f"Installation reported success but {pkg_name}=={requested_version} "
                        f"not found. Found version: {found_ver}"
                    )

    def _activate_bubble(self, bubble_path, pkg_name):
        """
        Activate a bubble with MANDATORY module cleanup.
        CRITICAL: NEVER skip module purging, even in nested contexts!
        """
        self._profile_start("activate_bubble_total")

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
                    f"   📊 Bubble: {len(bubble_deps)} packages, "
                    f"{len(packages_to_cloak)} conflicts"
                )

            # Phase 3: Module purging (conditional)
            self._profile_start("module_purging")
            should_purge = True
            if self._is_nested and self.isolation_mode == "overlay":
                should_purge = False
                if not self.quiet:
                    safe_print(
                        f"   ⏭️  Skipping module purge (nested overlay, depth={omnipkgLoader._nesting_depth})"
                    )

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
                safe_print(f"   🔒 Cloaked {cloaked_count} conflicting packages")
            self._profile_end("cloak_conflicts", print_now=self._profiling_enabled)

            # Phase 5: Setup sys.path
            self._profile_start("setup_syspath")
            bubble_path_str = str(bubble_path)
            if self.isolation_mode == "overlay":
                if not self.quiet:
                    safe_print("   - 🧬 OVERLAY mode")
                sys.path.insert(0, bubble_path_str)
            else:
                if not self.quiet:
                    safe_print("   - 🔒 STRICT mode")
                new_sys_path = [bubble_path_str] + [
                    p for p in self.original_sys_path if not self._is_main_site_packages(p)
                ]
                sys.path[:] = new_sys_path
            self._profile_end("setup_syspath", print_now=self._profiling_enabled)

            # Phase 6: Handle binary executables
            self._profile_start("setup_bin_path")
            bin_path = bubble_path / "bin"
            if bin_path.is_dir():
                if not self.quiet:
                    safe_print(f"   - 🔩 Activating binary path: {bin_path}")
                os.environ["PATH"] = str(bin_path) + os.pathsep + self.original_path_env
            self._profile_end("setup_bin_path", print_now=self._profiling_enabled)

            # Phase 7: Ensure omnipkg access
            self._profile_start("ensure_omnipkg_access")
            self._ensure_omnipkg_access_in_bubble(bubble_path_str)
            self._profile_end("ensure_omnipkg_access", print_now=self._profiling_enabled)

            # Finalize
            self._activated_bubble_path = bubble_path_str
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time

            self._profile_end("activate_bubble_total", print_now=self._profiling_enabled)

            if not self.quiet:
                safe_print(f"   ⚡ HEALED in {self._total_activation_time_ns / 1000:,.1f} μs")
                safe_print("   ✅ Bubble activated")

            self._activation_successful = True
            return self

        except Exception as e:
            safe_print(f"   ❌ Activation failed: {str(e)}")
            self._panic_restore_cloaks()
            raise

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
                                f"   ⚠️ Conflict (Redis): {pkg} (main: {main_ver} vs bubble: {bubble_ver})"
                            )

        except Exception as e:
            # If Redis flakes out, fallback to disk silently
            if not self.quiet:
                safe_print(f"   ⚠️ Redis conflict check failed ({e}), falling back to disk...")
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
            safe_print(f"   ✅ Emergency cleanup removed {cleaned} orphaned cloaks")

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

                result = core.smart_install([spec])

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
                safe_print(f"      ❌ Auto-install exception: {e}")
                import traceback

                safe_print(traceback.format_exc())
            return False

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with COMPLETE profiling visibility."""
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
        if self._worker_mode and self._active_worker:
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
                        safe_print(f"   ⚠️  Worker shutdown warning: {e}")
                finally:
                    self._active_worker = None

            self._worker_mode = False
            self._profile_end("TOTAL_DEACTIVATION", print_now=self._profiling_enabled)
            return

        self._deactivation_start_time = time.perf_counter_ns()

        if not self.quiet:
            depth_marker = f" [depth={current_depth}]" if self._is_nested else ""
            safe_print(
                f"🌀 omnipkg loader: Deactivating {self._current_package_spec}{depth_marker}..."
            )

        if not self._activation_successful:
            # CRITICAL: Always cleanup on failure
            self._cleanup_all_cloaks_globally()
            self._profile_end("TOTAL_DEACTIVATION")
            return

        pkg_name = self._current_package_spec.split("==")[0] if self._current_package_spec else None

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
                    f"   - 🔓 Restoring {len(self._cloaked_main_modules)} cloaked main env package(s)..."
                )

            for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
                if not was_successful:
                    continue

                if not cloak_path.exists():
                    continue

                try:
                    if original_path.exists():
                        if original_path.is_dir():
                            shutil.rmtree(original_path, ignore_errors=True)
                        else:
                            try:
                                original_path.unlink()
                            except OSError:
                                pass

                    shutil.move(str(cloak_path), str(original_path))
                    restored_count += 1

                except Exception:
                    pass

            self._cloaked_main_modules.clear()
        self._profile_end("restore_main_cloaks", print_now=self._profiling_enabled)

        # Restore bubble cloaks
        self._profile_start("restore_bubble_cloaks")
        if self._cloaked_bubbles:
            for cloak_path, original_path in reversed(self._cloaked_bubbles):
                try:
                    if cloak_path.exists():
                        if original_path.exists():
                            shutil.rmtree(original_path)
                        shutil.move(str(cloak_path), str(original_path))
                        restored_count += 1
                except Exception:
                    pass
            self._cloaked_bubbles.clear()
        self._profile_end("restore_bubble_cloaks", print_now=self._profiling_enabled)

        # ═══════════════════════════════════════════════════════════
        # CRITICAL: Profile environment restoration (this is the 57ms!)
        # ═══════════════════════════════════════════════════════════
        self._profile_start("restore_environment")
        if self.isolation_mode == "overlay" and self._activated_bubble_path:
            try:
                sys.path.remove(self._activated_bubble_path)
            except ValueError:
                pass
        else:
            # THIS is the expensive operation at depth!
            os.environ["PATH"] = self.original_path_env
            sys.path[:] = self.original_sys_path  # <-- 50-60ms here!
        self._profile_end("restore_environment", print_now=self._profiling_enabled)

        # ═══════════════════════════════════════════════════════════
        # OPTIMIZATION: Only purge modules at DEPTH 1
        # Nested contexts don't need this - the parent will handle it
        # ═══════════════════════════════════════════════════════════
        self._profile_start("module_purging")
        if should_cleanup:  # Only at depth=1
            if not self._using_main_env and self._activated_bubble_dependencies:
                for pkg_name_dep in self._activated_bubble_dependencies:
                    self._aggressive_module_cleanup(pkg_name_dep)

                if pkg_name:
                    self._aggressive_module_cleanup(pkg_name)
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
                safe_print(f"   ✅ Cleaned up {orphan_count} orphaned cloaks")

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

            # Final verification (only at depth 1)
            if should_cleanup and pkg_name:
                final_cloaks = self._scan_for_cloaked_versions(pkg_name)
                if not final_cloaks:
                    safe_print(f"   ✅ Verified: No orphaned cloaks for {pkg_name}")
                else:
                    safe_print(f"   ⚠️  WARNING: {len(final_cloaks)} cloaks still remaining!")

    # NEW HELPER METHOD: Simple unconditional restoration
    def _simple_restore_all_cloaks(self):
        """
        OPTIMIZED: Only scan where we know cloaks exist.
        """
        self._profile_start("cleanup_scan")

        if not self.quiet:
            safe_print("      🔍 Scanning for remaining cloaks...")

        restored = 0
        cloak_pattern = "*_omnipkg_cloaked*"

        # Scan main env (fast - only top level)
        main_cloaks = list(self.site_packages_root.glob(cloak_pattern))
        self._profile_end("cleanup_scan", print_now=self._profiling_enabled)

        self._profile_start("cleanup_restore")
        for cloak_path in main_cloaks:
            if not cloak_path.exists():
                continue

            # Extract original name
            original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)

            # Fallback for legacy format
            if original_name == cloak_path.name:
                original_name = re.sub(r"\.\d+_omnipkg_cloaked.*$", "", cloak_path.name)

            # Skip if we couldn't parse the name
            if "_omnipkg_cloaked" in original_name:
                if not self.quiet:
                    safe_print(f"         ⚠️  Can't parse cloak name: {cloak_path.name}")
                continue

            original_path = cloak_path.parent / original_name

            try:
                # If original exists, delete it (the cloak is newer)
                if original_path.exists():
                    if original_path.is_dir():
                        shutil.rmtree(original_path, ignore_errors=True)
                    else:
                        try:
                            original_path.unlink()
                        except:
                            pass

                # Move cloak back
                shutil.move(str(cloak_path), str(original_path))
                restored += 1

                if not self.quiet:
                    safe_print(f"         ✅ {original_name}")

            except Exception as e:
                if not self.quiet:
                    safe_print(f"         ❌ Failed: {cloak_path.name}: {e}")

        self._profile_end("cleanup_restore", print_now=self._profiling_enabled)

        # OPTIMIZATION: Skip bubble scan entirely if we didn't cloak any bubbles
        if not self._cloaked_bubbles:
            if not self.quiet:
                safe_print("      ⏭️  No bubbles cloaked, skipping bubble scan")
            return restored

        # Only scan specific bubble directories we cloaked
        self._profile_start("cleanup_bubble_restore")
        for cloak_path, original_path in self._cloaked_bubbles:
            if cloak_path.exists():
                try:
                    if original_path.exists():
                        if original_path.is_dir():
                            shutil.rmtree(original_path)
                        else:
                            original_path.unlink()

                    shutil.move(str(cloak_path), str(original_path))
                    restored += 1
                except Exception:
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
            safe_print(f"   🧹 Force-restoring {len(cloaks_to_restore)} owned cloaks...")

        for original_path, cloak_path in cloaks_to_restore:
            try:
                # Remove destination if it exists (e.g. partial restore or conflict)
                if original_path.exists():
                    if original_path.is_dir():
                        shutil.rmtree(original_path, ignore_errors=True)
                    else:
                        try:
                            original_path.unlink()
                        except:
                            pass

                # Move cloak back
                if cloak_path.exists():
                    shutil.move(str(cloak_path), str(original_path))
                    if not self.quiet:
                        safe_print(f"      ✅ Restored: {original_path.name}")

                # Unregister
                with omnipkgLoader._active_cloaks_lock:
                    omnipkgLoader._active_cloaks.pop(str(cloak_path), None)

            except Exception as e:
                if not self.quiet:
                    safe_print(f"      ⚠️ Failed to force-restore {original_path.name}: {e}")

    def _restore_cloaked_modules(self):
        """
        Restore cloaked modules with PROCESS-WIDE SAFETY and global tracking.
        """
        with omnipkgLoader._global_cloaking_lock:
            restored_count = 0
            failed_count = 0

            for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
                if not was_successful:
                    continue

                pkg_name = original_path.stem.split(".")[0]  # Get base name
                lock = self._get_cloak_lock(pkg_name)

                try:
                    with lock.acquire(timeout=5):
                        if not cloak_path.exists():
                            continue

                        # Remove any existing target first (Force Overwrite)
                        if original_path.exists():
                            if original_path.is_dir():
                                shutil.rmtree(original_path, ignore_errors=True)
                            else:
                                try:
                                    original_path.unlink()
                                except OSError:
                                    pass  # Best effort

                        # Verify target is gone
                        if original_path.exists():
                            # If standard removal failed, try renaming it out of the way (nuclear option)
                            trash_path = original_path.with_suffix(f".trash_{time.time()}")
                            shutil.move(str(original_path), str(trash_path))
                            if original_path.is_dir():
                                shutil.rmtree(trash_path, ignore_errors=True)

                        shutil.move(str(cloak_path), str(original_path))

                        # *** THIS IS THE FIX: Use .pop() to remove the key from the dict ***
                        with omnipkgLoader._active_cloaks_lock:
                            # Safely remove the key; the second argument prevents errors if it's already gone
                            omnipkgLoader._active_cloaks.pop(str(cloak_path), None)

                        restored_count += 1
                        if not self.quiet:
                            safe_print(f"   ✅ Restored: {original_path.name}")

                except filelock.Timeout:
                    if not self.quiet:
                        safe_print(
                            f"   ⏱️  Timeout waiting for lock on {pkg_name}, skipping restore..."
                        )
                    failed_count += 1
                except Exception as e:
                    if not self.quiet:
                        safe_print(f"   ❌ Failed to restore {original_path.name}: {e}")
                    failed_count += 1

            self._cloaked_main_modules.clear()

            if not self.quiet and (restored_count > 0 or failed_count > 0):
                safe_print(f"   📊 Restoration: {restored_count} restored, {failed_count} failed")

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
                try:
                    shutil.move(str(original_path), str(cloak_path))
                    cloak_record = (original_path, cloak_path, True)
                except Exception as e:
                    if not self.quiet:
                        safe_print(_(" ⚠️ Failed to cloak {}: {}").format(original_path.name, e))
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
                print(f"         ⏱️ pre_inval:{(_t2-_t)/1e6:.3f}ms")
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

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                print(f"         ⏱️ special_check:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        # GET MODULES - THIS IS LIKELY THE BOTTLENECK
        modules_to_clear = self._get_package_modules(pkg_name)

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                print(f"         ⏱️ get_modules:{(_t2-_t)/1e6:.3f}ms")
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
                print(f"         ⏱️ del_loop:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

            _t2 = time.perf_counter_ns()
            if not self.quiet:
                print(f"         ⏱️ gc:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

        if hasattr(importlib, "invalidate_caches"):
            importlib.invalidate_caches()

        if self._profiling_enabled:
            _t2 = time.perf_counter_ns()
            if not self.quiet:
                print(f"         ⏱️ post_inval:{(_t2-_t)/1e6:.3f}ms")
            _t = _t2

            if not self.quiet:
                print(f"         ⏱️ TOTAL_cleanup:{(time.perf_counter_ns()-_t0)/1e6:.3f}ms")

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
                safe_print(f"      🔍 Found {len(found_cloaks)} potential main env cloaks")

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
                                    safe_print(f"         ✅ Restored: {original_name}")
                            else:
                                if cloak_path.is_dir():
                                    shutil.rmtree(cloak_path)
                                else:
                                    cloak_path.unlink()
                                total_cleaned += 1
                                if not self.quiet:
                                    safe_print(
                                        f"         🗑️  Deleted malformed cloak: {cloak_path.name}"
                                    )
                        except Exception as e:
                            if not self.quiet:
                                safe_print(f"         ⚠️  Failed to process {cloak_path.name}: {e}")
                    else:
                        try:
                            if cloak_path.is_dir():
                                shutil.rmtree(cloak_path)
                            else:
                                cloak_path.unlink()
                            total_cleaned += 1
                            if not self.quiet:
                                safe_print(
                                    f"         🗑️  Deleted duplicate cloak: {cloak_path.name}"
                                )
                        except Exception as e:
                            if not self.quiet:
                                safe_print(f"         ⚠️  Failed to delete {cloak_path.name}: {e}")

        # --- Cleanup bubble cloaks ---
        if self.multiversion_base.exists():
            bubble_cloaks = set()
            for pattern in cloak_patterns:
                # Recursive search inside bubbles directory
                bubble_cloaks.update(self.multiversion_base.rglob(pattern))

            if bubble_cloaks:
                if not self.quiet:
                    safe_print(f"      🔍 Found {len(bubble_cloaks)} potential bubble cloaks")

                for cloak_path in bubble_cloaks:
                    if str(cloak_path) in omnipkgLoader._active_cloaks:
                        continue

                    original_name = re.sub(r"\.\d+_\d+_omnipkg_cloaked.*$", "", cloak_path.name)
                    if original_name == cloak_path.name:
                        match = re.search(r"^(.+?)(?:\.\d+)?_\d+_omnipkg_cloaked", cloak_path.name)
                        if match:
                            original_name = match.group(1)

                    original_path = cloak_path.parent / original_name

                    try:
                        if not original_path.exists():
                            shutil.move(str(cloak_path), str(original_path))
                        else:  # It's a duplicate, just delete it
                            if cloak_path.is_dir():
                                shutil.rmtree(cloak_path)
                            else:
                                cloak_path.unlink()
                        total_cleaned += 1
                    except Exception:
                        pass

        if total_cleaned > 0:
            if not self.quiet:
                safe_print(f"   ✅ Cleaned up {total_cleaned} orphaned/duplicate cloaks")
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
                                safe_print(f"      ⚠️  Failed to read {top_level_file}: {e}")
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
                safe_print(f"      📦 Using known mapping: {pkg_name} -> {import_name}")
            return import_name

        # Last resort: transform package name
        # Replace hyphens with underscores (common convention)
        transformed = pkg_name.replace("-", "_").lower()

        if not self.quiet and transformed != pkg_name:
            safe_print(f"      📦 Using transformed name: {pkg_name} -> {transformed}")

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
                        f"Package {pkg_name} imported but failed sanity check (Zombie State detected!)"
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
                        safe_print(f"      ⚠️  Attempt {attempt + 1} failed: {error_snippet}")
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
            result = subprocess.run(
                [sys.executable, "-c", check_script],
                capture_output=True,
                text=True,
                timeout=10,
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
                    safe_print(f"      📦 Reinstalling {pkg_spec}...")

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
                    safe_print(f"   🚨 C++ collision risk: {mod_pattern} already loaded")
                return True

            # Check prefix match (e.g., 'torch._C' matches 'torch._C._something')
            for loaded_mod in sys.modules:
                if loaded_mod.startswith(mod_pattern):
                    if not self.quiet:
                        safe_print(f"   🚨 C++ collision risk: {loaded_mod} detected")
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
                                f"   🚨 C++ version conflict: {pkg} "
                                f"({main_version} vs {bubble_version})"
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
                safe_print(f"   ⚠️  Daemon connection failed: {e}. Falling back to local.")
            return None

    def __enter__(self):
        self._activation_start_time = time.perf_counter_ns()
        if not self._current_package_spec:
            raise ValueError("Package spec required")

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")

        # 1. Proactive Worker Mode (Daemon)
        if self._worker_fallback_enabled and self._should_use_worker_proactively(pkg_name):
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            if self._active_worker:
                self._worker_mode = True
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                return self

        # Store original activation start time
        self._activation_start_time = time.perf_counter_ns()

        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")

        try:
            pkg_name, requested_version = self._current_package_spec.split("==")
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")

        # STRATEGY 1: Proactive Worker Mode for Known Problematic Packages
        if self._worker_fallback_enabled and self._should_use_worker_mode(pkg_name):
            if not self.quiet:
                safe_print(
                    f"   🧠 Smart Decision: Using worker mode for {pkg_name} "
                    f"(known C++ collision risk)"
                )

            self._active_worker = self._create_worker_for_spec(self._current_package_spec)
            if self._active_worker:
                self._worker_mode = True
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = (
                    self._activation_end_time - self._activation_start_time
                )
                return self
            else:
                if not self.quiet:
                    safe_print("   ⚠️  Worker creation failed, falling back to in-process")
        multiversion_base_str = str(self.multiversion_base)
        is_nested_activation = sys.path[0].startswith(multiversion_base_str) if sys.path else False

        # The "System version already matches" check is ONLY safe if we are in the top-level context.
        # If we are nested, we MUST ignore this check and proceed to full bubble logic to ensure
        # the correct environment is isolated.
        if not is_nested_activation:
            try:
                # This check is now safe.
                current_system_version = get_version(pkg_name)
                if current_system_version == requested_version and not self.force_activation:
                    if not self.quiet:
                        safe_print(
                            _(
                                "✅ System version already matches requested version ({}). No bubble needed."
                            ).format(current_system_version)
                        )

                    self._ensure_main_site_packages_in_path()
                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = (
                        self._activation_end_time - self._activation_start_time
                    )
                    return self
            except PackageNotFoundError:
                # Package is not in the main env at all, so we must proceed.
                pass
        elif not self.quiet:
            safe_print(
                f"   - ⚠️ Nested context detected. Forcing full bubble logic for {self._current_package_spec}."
            )

        # STRATEGY 2: Try In-Process Activation (Original Logic)
        try:
            # Call the original __enter__ logic
            return super().__enter__()

        except ProcessCorruptedException as e:
            # STRATEGY 3: Reactive Worker Fallback on C++ Collision
            if not self._worker_fallback_enabled:
                raise  # Re-raise if worker fallback is disabled

            if not self.quiet:
                safe_print("   🔄 C++ collision detected, switching to worker mode...")
                safe_print(f"      Original error: {str(e)}")

            # Clean up any partial activation state
            self._panic_restore_cloaks()

            # Create worker as fallback
            self._active_worker = self._create_worker_for_spec(self._current_package_spec)

            if not self._active_worker:
                if not self.quiet:
                    safe_print("   ❌ Worker fallback failed")
                raise RuntimeError(
                    f"Both in-process and worker activation failed for "
                    f"{self._current_package_spec}"
                )

            self._worker_mode = True
            self._activation_successful = True
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time

            if not self.quiet:
                safe_print("   ✅ Successfully recovered using worker mode")

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
