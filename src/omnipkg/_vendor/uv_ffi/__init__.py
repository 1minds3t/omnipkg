"""
uv_ffi — thin wrapper around the PyO3 native extension.
run(cmd) -> (rc, installed, removed)
  installed: list of (name, version) tuples
  removed:   list of (name, version) tuples
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '_native'))
from uv_ffi import run as _run_native

def run(cmd: str) -> tuple:
    """Call uv FFI directly — returns structured (rc, installed, removed)."""
    return _run_native(cmd)

run_capture = run

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