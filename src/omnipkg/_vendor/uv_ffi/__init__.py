"""
uv_ffi — thin wrapper around the PyO3 native extension.
run(cmd) -> (rc, installed, removed, err)
  installed: list of (name, version) tuples
  removed:   list of (name, version) tuples
  err:       error string (empty on success)
"""
try:
    # Prefer the properly installed wheel
    from uv_ffi.uv_ffi import (
        run as _run_native,
        get_site_packages_cache,
        invalidate_site_packages_cache,
        patch_site_packages_cache,
        clear_registry_cache,
    )
except ImportError:
    # Fall back to vendored .so
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '_native'))
    from uv_ffi import (
        run as _run_native,
        get_site_packages_cache,
        invalidate_site_packages_cache,
        patch_site_packages_cache,
        clear_registry_cache,
    )

def run(cmd: str) -> tuple:
    """Call uv FFI directly — returns (rc, installed, removed, err).
    Gracefully handles old 3-tuple .so builds by padding err=''."""
    result = _run_native(cmd)
    if len(result) == 3:
        return (result[0], result[1], result[2], '')
    return result

run_capture = run

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
    "__version__",
]