"""
uv_ffi — thin wrapper around the PyO3 native extension.
run(cmd) -> (rc, installed, removed, err)
  installed: list of (name, version) tuples
  removed:   list of (name, version) tuples
  err:       error string (empty on success)
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '_native'))
from uv_ffi import run as _run_native

def run(cmd: str) -> tuple:
    """Call uv FFI directly — returns (rc, installed, removed, err).
    Gracefully handles old 3-tuple .so builds by padding err='' ."""
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

# ── Optional exports: present only if the .so was built with them ────────────
# Imported lazily so that an older .so without these functions doesn't cause
# an ImportError on the whole module and break FFI availability detection.

def _try_import(name: str):
    try:
        import uv_ffi as _m
        return getattr(_m, name)
    except (ImportError, AttributeError):
        return None

_invalidate = _try_import('invalidate_site_packages_cache')
if _invalidate is not None:
    invalidate_site_packages_cache = _invalidate

_patch = _try_import('patch_site_packages_cache')
if _patch is not None:
    patch_site_packages_cache = _patch
_gspc = _try_import('get_site_packages_cache')
if _gspc is not None:
    get_site_packages_cache = _gspc

_clear_reg = _try_import('clear_registry_cache')
if _clear_reg is not None:
    clear_registry_cache = _clear_reg

_clear_reg = _try_import('clear_registry_cache')
if _clear_reg is not None:
    clear_registry_cache = _clear_reg
