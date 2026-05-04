"""
uv_ffi — thin wrapper around the PyO3 native extension.
Includes safety stubs for legacy native binaries.
"""
import sys as _sys
import os as _os

def _load_native():
    _here = _os.path.dirname(__file__)
    _native_dir = _os.path.join(_here, '_native')

    import sysconfig as _sc
    import glob as _glob
    _tag = _sc.get_config_var('EXT_SUFFIX') or ''
    _versioned = _os.path.join(_native_dir, f'uv_ffi{_tag}')
    _abi3 = _os.path.join(_native_dir, 'uv_ffi.abi3.so')
    _is_vendored_native = _os.path.exists(_versioned) or _os.path.exists(_abi3)

    # Check if this interpreter has its OWN uv_ffi installed — glob for any .so/.pyd
    _site_uv_ffi_dir = _os.path.join(_sc.get_path('purelib'), 'uv_ffi')
    _site_sos = (
        _glob.glob(_os.path.join(_site_uv_ffi_dir, 'uv_ffi*.so')) +
        _glob.glob(_os.path.join(_site_uv_ffi_dir, 'uv_ffi*.pyd'))
    )
    _sys.stderr.flush()

    if _site_sos:
        _site_so = _site_sos[0]
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("uv_ffi", _site_so)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sys.stderr.flush()
        return _mod

    if _is_vendored_native:
        _so = _versioned if _os.path.exists(_versioned) else _abi3
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("uv_ffi._native", _so)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sys.stderr.flush()
        return _mod

    raise ImportError(f'uv_ffi: no .so found for {_sys.executable} (tag={_tag}, site={_sc.get_path("purelib")})')

# Load the native binary
_native = _load_native()
_loaded_so_path = getattr(_native, '__file__', 'unknown')

# ── SAFETY STUBS ─────────────────────────────────────────────────────────────
# We use getattr(..., fallback) to ensure that if a legacy .so is loaded, 
# the import doesn't crash with an AttributeError. 
# The functions will exist as no-ops instead of blowing up the process.
# ─────────────────────────────────────────────────────────────────────────────
_noop = lambda *a, **kw: None

run                         = getattr(_native, 'run', lambda cmd: (1, "Native 'run' missing", ""))
get_site_packages_cache     = getattr(_native, 'get_site_packages_cache', _noop)
invalidate_site_packages_cache = getattr(_native, 'invalidate_site_packages_cache', _noop)
patch_site_packages_cache   = getattr(_native, 'patch_site_packages_cache', _noop)
clear_registry_cache        = getattr(_native, 'clear_registry_cache', _noop)

def run(cmd: str) -> tuple:
    """
    Core execution wrapper. 
    If the native 'run' was missing, this will call the stub return value.
    """
    # If 'run' is the stub, it returns a tuple; otherwise call the native function.
    if hasattr(_native, 'run'):
        result = _native.run(cmd)
        if len(result) == 3:
            return (result[0], result[1], result[2], '')
        return result
    else:
        # Fallback return for the stub to prevent crashing the caller
        return (1, "Native 'run' method not found in loaded .so", "")

run_capture = run

try:
    from importlib.metadata import version as _meta_version
    __version__ = _meta_version("uv_ffi")
except Exception:
    __version__ = "unknown"

__all__ = [
    "run", "run_capture",
    "get_site_packages_cache",
    "invalidate_site_packages_cache",
    "patch_site_packages_cache",
    "clear_registry_cache",
    "__version__",
]