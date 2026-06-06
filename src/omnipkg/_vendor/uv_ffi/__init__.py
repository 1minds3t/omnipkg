"""
uv_ffi — thin wrapper around the PyO3 native extension.
Includes safety stubs for legacy native binaries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IPC / FFI CONTRACT OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All functions below are bound from the PyO3 native extension (_native .so/.pyd).
If the native binary predates a symbol, the getattr() fallback activates instead
of crashing — legacy binaries get _noop / typed stubs.

DATA FLOW (install path):
  Python calls  run("pip install rich==14.3.3 --target /tmp/bubble_xyz")
       │
       ▼  [inside Rust / uv]
  uv resolves + plans → writes INSTALL_PLAN (Vec<(name, version, action)>)
       │
       ▼  fires PLAN_READY_CALLBACK  (set via set_plan_callback)
       │
       ▼  Python callback receives plan_entries: list[tuple[str,str,str]]
            return True  → Rust skips the actual install (Python handled it)
            return False → Rust proceeds with normal install

SELF-HEALING RESPONSIBILITY:
  When the callback returns True the Rust install() function returns
  Changelog::default() immediately.  Python then OWNS the operation:
    - It must use the SAME target path that was originally passed in run().
    - It must clean up stale directories (package dir present, dist-info gone).
    - It must not fall back to site-packages if the original target was a bubble.
    See: _SelfHealingGuard usage notes below.

PLAN ENTRY TUPLE:  (name: str, version: str, action: str)
  name    — normalised PEP 503 package name  e.g. "rich"
  version — PEP 440 version string            e.g. "14.3.3"  (may be "" for remote
             entries when uv hasn't resolved the version yet)
  action  — one of: "cached" | "remote" | "reinstall" | "extraneous"

NOTE: PLAN_HANDLED AtomicBool is REMOVED from the Rust side (was vestigial).
  mark_plan_handled() stub is kept here for one-cycle backwards-compat only.
  Do NOT use it in new code — return True from the plan callback instead.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys as _sys
import os as _os


def _load_native():
    import sysconfig as _sc
    import importlib.util as _ilu

    _here = _os.path.dirname(__file__)
    _tag = _sc.get_config_var('EXT_SUFFIX') or ''

    # abi3 first (everyone except cp37), then cpython-specific (cp37 only)
    # same dir first (normal pip install), then _native/ subdir (vendor layout)
    _candidates = [
        _os.path.join(_here, '_native.abi3.so'),
        _os.path.join(_here, f'_native{_tag}'),
        _os.path.join(_here, '_native', '_native.abi3.so'),
        _os.path.join(_here, '_native', f'_native{_tag}'),
        # legacy filenames from before module-name rename
        _os.path.join(_here, '_native', 'uv_ffi.abi3.so'),
        _os.path.join(_here, '_native', f'uv_ffi{_tag}'),
    ]

    for _so in _candidates:
        if _os.path.exists(_so):
            _spec = _ilu.spec_from_file_location('uv_ffi._native', _so)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod

    raise ImportError(
        f'uv_ffi: no .so found for {_sys.executable} '
        f'(tag={_tag}, site={_sc.get_path("purelib")})'
    )

# Load the native binary
_native = _load_native()
_loaded_so_path = getattr(_native, '__file__', 'unknown')

# ── SAFETY STUBS ──────────────────────────────────────────────────────────────
# getattr(obj, name, fallback) means: if the loaded .so is older and doesn't
# export the symbol yet, use the fallback instead of raising AttributeError.
# ─────────────────────────────────────────────────────────────────────────────
_noop = lambda *a, **kw: None


# ── CORE EXECUTION ────────────────────────────────────────────────────────────

# run(cmd: str) -> tuple[int, Changelog | None, str]  [from Rust: (i32, Option<Changelog>, String)]
# Exposed through the run() function below which normalises to 4-tuple.
# Do NOT call _native.run() directly — always use run() / run_capture().

# ── CACHE MANAGEMENT ─────────────────────────────────────────────────────────

# get_site_packages_cache() -> object | None
#   Returns the cached Arc<SitePackages> handle (opaque Python object).
#   Returns None if the cache has not been primed.
get_site_packages_cache     = getattr(_native, 'get_site_packages_cache', _noop)

# invalidate_site_packages_cache() -> None
#   Drops the cached Arc<SitePackages>, forcing a fresh scan on next access.
invalidate_site_packages_cache = getattr(_native, 'invalidate_site_packages_cache', _noop)

# patch_site_packages_cache(dist_name: str, version: str, location: str) -> None
#   Surgically insert one dist into the live cache without a full rescan.
#   dist_name : normalised PEP 503 name  ("rich", "numpy")
#   version   : PEP 440 string           ("14.3.3")
#   location  : absolute path to the dist-info directory
patch_site_packages_cache   = getattr(_native, 'patch_site_packages_cache', _noop)

# clear_registry_cache() -> None
#   Wipes the uv registry / index response cache in-process.
#   Call before any install where you suspect stale PyPI metadata.
#   Added in uv-ffi post6 — older binaries get _noop.
clear_registry_cache        = getattr(_native, 'clear_registry_cache', _noop)

# ── BUBBLE CACHE ─────────────────────────────────────────────────────────────

# evict_bubble_cache() -> None
#   Clears ALL bubble site-packages cache entries.
#   Use when a bubble is destroyed or its contents change externally.
evict_bubble_cache          = getattr(_native, 'evict_bubble_cache', _noop)

# evict_packages_from_bubble_cache(bubble_id: str, *package_names: str) -> None
#   Evict specific packages from a single bubble's cache entry.
#   bubble_id     : the bubble identifier string used at creation time
#   package_names : one or more normalised PEP 503 package names
evict_packages_from_bubble_cache = getattr(_native, 'evict_packages_from_bubble_cache', _noop)

# patch_bubble_site_packages_cache(bubble_id: str, dist_name: str,
#                                   version: str, location: str) -> None
#   Equivalent of patch_site_packages_cache but for a named bubble.
#   bubble_id : the bubble identifier string
#   dist_name : normalised PEP 503 name
#   version   : PEP 440 string
#   location  : absolute path to the dist-info directory inside the bubble
patch_bubble_site_packages_cache = getattr(_native, 'patch_bubble_site_packages_cache', _noop)

# ── INSTALL PLAN IPC ─────────────────────────────────────────────────────────

# get_install_plan() -> list[tuple[str, str, str]]
#   Returns the last plan written by Rust during the current or most-recent
#   install() call.  Each entry is (name, version, action).
#   action is one of: "cached" | "remote" | "reinstall" | "extraneous"
#   version may be "" for "remote" entries (version not yet resolved).
#   The list is CLEARED at the start of each resolve() and install() call,
#   and again at the start of execute_plan() sub-steps.
#
#   Thread safety: backed by std::sync::Mutex — safe to call from any thread,
#   but reads are not atomic with respect to concurrent Rust writes.
#   Best practice: read only inside the plan callback, not after returning.
#
#   Fallback on legacy .so: returns []
get_install_plan = getattr(_native, 'get_install_plan', lambda: [])

# set_plan_callback(cb: Callable[[list[tuple[str, str, str]]], bool]) -> None
#   Register a Python callable that Rust fires when the install plan is ready.
#
#   cb signature:
#       def my_callback(entries: list[tuple[str, str, str]]) -> bool:
#           ...
#           return handled   # True → Rust skips install; False → Rust proceeds
#
#   entries  — same format as get_install_plan() return value (snapshot at
#              callback time, same list that was just written to INSTALL_PLAN)
#
#   ⚠️  CRITICAL — TARGET TRACKING:
#       When the callback returns True, Rust exits install() immediately.
#       The callback is responsible for performing any needed operation on
#       the SAME target that was passed to run().  Do NOT default to
#       site-packages — always carry the original target path through
#       your callback closure.
#
#   ⚠️  CRITICAL — STALE DIR CLEANUP:
#       Before installing into a target dir, check for the case where the
#       package directory exists but its dist-info is gone (uv partial-write
#       or interrupted uninstall).  If found: remove the package dir first,
#       then install fresh.  uv may warn "files may have been left behind" —
#       trust that warning and act on it.
#
#   Only one callback can be registered at a time (last write wins).
#   Fallback on legacy .so: _noop (callback never fires, plan never handled).
set_plan_callback = getattr(_native, 'set_plan_callback', _noop)

# mark_plan_handled() -> None
#   ⚠️  DEPRECATED — do NOT use in new code.
#   The PLAN_HANDLED AtomicBool has been removed from the Rust side.
#   This stub exists only to avoid ImportError on one-cycle-old callers.
#   Signal "handled" by returning True from your set_plan_callback callback.
mark_plan_handled = getattr(_native, 'mark_plan_handled', _noop)


# ── CORE run() WRAPPER ────────────────────────────────────────────────────────

def run(cmd: str) -> tuple:
    """
    Execute a uv command via the Rust FFI layer.

    Args:
        cmd: Shell-style uv argument string, WITHOUT the leading "uv".
             Examples:
               "pip install rich==14.3.3"
               "pip install rich==15.0.0 --target /tmp/bubble_xyz"
               "pip uninstall rich --yes"

    Returns:
        4-tuple: (returncode: int, changelog: object | None,
                  stderr: str, extra: str)
        returncode == 0  → success
        returncode != 0  → failure; check stderr for details

    Notes:
        - If set_plan_callback was set and the callback returns True, the
          install is considered handled by Python.  returncode will be 0
          and changelog will be a default (empty) Changelog.
        - Always call run() through this wrapper, not _native.run() directly,
          so that the 3-tuple → 4-tuple normalisation is applied consistently.
    """
    if hasattr(_native, 'run'):
        result = _native.run(cmd)
        if len(result) == 3:
            return (result[0], result[1], result[2], '')
        return result
    else:
        return (1, None, "Native 'run' method not found in loaded .so", '')


run_capture = run  # alias kept for callers using the older name

try:
    from importlib.metadata import version as _meta_version
    __version__ = _meta_version("uv_ffi")
except Exception:
    __version__ = "unknown"

__all__ = [
    "run",
    "run_capture",
    "get_site_packages_cache",
    "invalidate_site_packages_cache",
    "patch_site_packages_cache",
    "clear_registry_cache",
    "evict_bubble_cache",
    "evict_packages_from_bubble_cache",
    "patch_bubble_site_packages_cache",
    "get_install_plan",
    "set_plan_callback",
    "mark_plan_handled",   # deprecated stub — see docstring above
    "__version__",
]