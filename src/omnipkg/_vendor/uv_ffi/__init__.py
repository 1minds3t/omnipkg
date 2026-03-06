"""
uv_ffi — thin wrapper around the PyO3 native extension.

run() calls uv's pip_install directly with no fork and no CLI re-init.
"""
from uv_ffi.uv_ffi import run as _run_native
import threading

_lock = threading.Lock()

def run(cmd: str) -> tuple[int, str, str]:
    with _lock:
        return _run_native(cmd)

def run_capture(cmd: str) -> tuple[int, str]:
    rc, _out, err = run(cmd)
    return rc, err
