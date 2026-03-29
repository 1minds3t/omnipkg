from __future__ import annotations

import base64  # <--- ENSURE THIS IS HERE
import ctypes
import glob
import json
import os
import platform
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import struct  # <--- ADDED for control block packing
# import psutil  # Made lazy
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

import filelock

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

try:
    from .common_utils import safe_print
except ImportError:
    pass
IS_WINDOWS = platform.system() == "Windows"


def _normalize_exe(path: str) -> str:
    """
    Normalize a Python executable path for reliable cross-platform comparison.
    On Windows, Path.resolve() is needed for drive-letter/junction canonicalization.
    On macOS/Linux we must NOT call resolve() — venv python binaries are symlinks
    to the framework Python, so resolve() returns the framework path and loses
    all venv context. absolute() makes the path absolute without following symlinks.
    """
    try:
        if IS_WINDOWS:
            return str(Path(path).resolve()).lower()
        return str(Path(path).absolute())
    except Exception:
        return path


def _venv_python_exe() -> str:
    """
    Return the venv-aware Python executable path on macOS/Linux.

    Problem: when the daemon is forked, macOS resolves the venv symlink at the
    OS level, so sys.executable becomes the framework path
    (/Library/Frameworks/.../python3.11) and all workers get spawned with that,
    losing venv context and causing omnipkg import failures.

    Solution priority:
      1. __PYVENV_LAUNCHER__ — set by macOS when a venv symlink was the entry point
      2. sys.prefix / bin / pythonX.Y — reconstruct from the venv prefix
      3. sys.executable — last resort (may be the framework path on macOS)
    """
    if IS_WINDOWS:
        return sys.executable

    # macOS sets this env var to the original symlink path when launching via venv
    launcher = os.environ.get("__PYVENV_LAUNCHER__", "")
    if launcher and os.path.exists(launcher):
        return launcher

    # Reconstruct from sys.prefix (the venv root is always correct)
    try:
        v = sys.version_info
        candidates = [
            os.path.join(sys.prefix, "bin", f"python{v.major}.{v.minor}"),
            os.path.join(sys.prefix, "bin", f"python{v.major}"),
            os.path.join(sys.prefix, "bin", "python"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    except Exception:
        pass

    return sys.executable


def _resolve_python_exe(python_exe: str | None) -> str:
    """
    Resolve a Python interpreter reference to its full absolute path.

    Accepts any of:
      - None / ""          → returns sys.executable (current interpreter)
      - Full path          → returned as-is after normalization
                             e.g. "/home/user/miniforge3/.../python3.11"
      - Short version      → resolved via omnipkg's interpreter registry
                             e.g. "3.11", "3.11.2", "py311", "python3.11"

    This means callers never have to know the full path.  The daemon and
    DaemonClient both call this before building the worker_key, so the
    resolved path is always what ends up in the key — consistent everywhere.

    Raises nothing: if resolution fails it falls back to the input string
    so existing code that passes bad-but-working paths continues to work.
    """
    if not python_exe:
        return _venv_python_exe()

    # Already a usable path — don't touch it.
    if os.path.isabs(python_exe) and os.path.exists(python_exe):
        return python_exe

    # Try omnipkg's own resolver (the same one used by `8pkg daemon idle`).
    try:
        from omnipkg.dispatcher import resolve_python_path
        resolved = str(resolve_python_path(python_exe))
        if resolved and os.path.exists(resolved):
            return resolved
    except Exception:
        pass

    # Unrecognised string — return as-is and let the OS complain later.
    return python_exe

def _derive_paths_for_exe(python_exe: str) -> dict:
    """
    Compute site_packages_path and multiversion_base directly from a Python
    executable path, without relying on ConfigManager lookup.

    IMPORTANT: Always pass the ORIGINAL-CASE path (not normalized/lowercased),
    because the returned paths are passed to pip/subprocess which need real
    filesystem paths. On Windows, lowercased paths cause pip to fail writing
    temp files even though the FS is case-insensitive.

    For a managed interpreter like:
      C:\\...\\cpython-3.9.23\\python.exe
    The site-packages is:
      C:\\...\\cpython-3.9.23\\Lib\\site-packages   (Windows)
      .../cpython-3.9.23/lib/pythonX.Y/site-packages  (Unix)
    """
    try:
        # Use the path as-is for filesystem operations to preserve case on Windows.
        # If the path is already normalized/lowercased, try to resolve to original case.
        exe_path = Path(python_exe)

        # On Windows, if the path doesn't exist as-is (e.g. it was lowercased),
        # try to find the actual mixed-case path by resolving via the OS.
        if IS_WINDOWS and not exe_path.exists():
            # Try to restore original case by asking the OS
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(32768)
                get_long = ctypes.windll.kernel32.GetLongPathNameW
                if get_long(str(exe_path), buf, 32768):
                    exe_path = Path(buf.value)
            except Exception:
                pass

        if not exe_path.exists():
            return {}

        exe_dir = exe_path.parent

        if IS_WINDOWS:
            # Windows layout: <install_dir>\python.exe  → <install_dir>\Lib\site-packages
            candidate = exe_dir / "Lib" / "site-packages"
        else:
            # Unix layout: <install_dir>/bin/python → <install_dir>/lib/pythonX.Y/site-packages
            candidate = None
            lib_dir = exe_dir.parent / "lib"
            if lib_dir.exists():
                for d in sorted(lib_dir.iterdir()):
                    if d.name.startswith("python") and (d / "site-packages").exists():
                        candidate = d / "site-packages"
                        break

        if candidate and Path(candidate).exists():
            multiversion_base = str(Path(candidate) / ".omnipkg_versions")
            return {
                "site_packages_path": str(candidate),
                "multiversion_base": multiversion_base,
            }
    except Exception:
        pass
    return {}


def _resolve_target_paths(cm, python_exe_normalized: str) -> dict:
    """
    Get site_packages_path and multiversion_base for the given interpreter.

    Tries ConfigManager._get_paths_for_interpreter() with multiple path variants
    (normalized, original-case, registry scan) to handle Windows case-sensitivity
    issues. Falls back to _derive_paths_for_exe() which reads the filesystem
    layout directly from the interpreter directory — this ALWAYS works for
    python-build-standalone managed interpreters regardless of cm state.
    """
    if cm is not None:
        # Try 1: normalized (lowercase) path
        try:
            paths = cm._get_paths_for_interpreter(python_exe_normalized) or {}
            if paths:
                return paths
        except Exception:
            pass

        # Try 2: original-case resolved path (without lowercasing)
        try:
            raw_path = str(Path(python_exe_normalized).resolve())
            paths = cm._get_paths_for_interpreter(raw_path) or {}
            if paths:
                return paths
        except Exception:
            pass

        # Try 3: scan interpreter registry for a case-insensitive match
        try:
            reg_path = getattr(cm, 'venv_path', None)
            if reg_path:
                reg_file = Path(reg_path) / ".omnipkg" / "interpreters" / "registry.json"
                if reg_file.exists():
                    with open(reg_file, "r", encoding="utf-8") as f:
                        reg_data = json.load(f)
                    for raw_key in reg_data.get("interpreters", {}).values():
                        if _normalize_exe(str(raw_key)) == python_exe_normalized:
                            paths = cm._get_paths_for_interpreter(raw_key) or {}
                            if paths:
                                return paths
        except Exception:
            pass

    # Fallback: derive directly from filesystem layout of the interpreter.
    # IMPORTANT: Pass the original-case path, not the lowercased normalized key,
    # because pip needs real filesystem paths on Windows.
    # Try to reconstruct original case from the normalized path.
    original_case_path = python_exe_normalized  # start with what we have
    try:
        raw = str(Path(python_exe_normalized).resolve())
        if Path(raw).exists():
            original_case_path = raw
    except Exception:
        pass

    paths = _derive_paths_for_exe(original_case_path)
    if paths:
        safe_print(
            f"   📂 [DAEMON] Using filesystem-derived paths for {python_exe_normalized}: "
            f"site_packages={paths.get('site_packages_path')}",
            file=sys.stderr,
        )
    else:
        safe_print(
            f"   ⚠️ [DAEMON] Could not resolve paths for {python_exe_normalized} — "
            f"OMNIPKG_MULTIVERSION_BASE will NOT be set. Package may install to wrong location!",
            file=sys.stderr,
        )
    return paths


def _ensure_worker_config(python_exe: str, site_packages: str, multiversion_base: str) -> None:
    """
    Write a minimal .omnipkg_config.json next to the worker's Python executable
    so ConfigManager._get_our_config_path() finds it and doesn't fall back to
    the global config (which has the wrong multiversion_base for managed interpreters).

    This is intentionally lightweight — no imports from omnipkg core, no subprocesses.
    Idempotent: will not overwrite an existing config.
    """
    try:
        exe_path = Path(python_exe)
        if not exe_path.exists():
            return

        config_path = exe_path.parent / ".omnipkg_config.json"
        if config_path.exists():
            # Validate it has the keys we need — patch if missing
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if ("multiversion_base" in existing and "site_packages_path" in existing
                        and existing["multiversion_base"] == multiversion_base):
                    return  # Already correct
            except Exception:
                pass  # Fall through to rewrite

        config_data = {
            "python_executable": str(exe_path.resolve()),
            "site_packages_path": site_packages,
            "multiversion_base": multiversion_base,
            "install_strategy": "stable-main",
            "redis_enabled": True,
            "redis_host": "localhost",
            "redis_port": 6379,
            "enable_python_hotswap": True,
            "managed_by_omnipkg": True,
            "_auto_generated_by": "worker_daemon",
        }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        tmp.replace(config_path)

    except Exception:
        pass  # Non-fatal — env var OMNIPKG_MULTIVERSION_BASE is still the safety net


try:
    from omnipkg.isolation import omnipkg_atomic
    _HAS_ATOMICS = True
    # Directly check command-line args. This is clean and has no side effects.
    if "--verbose" in sys.argv or "-V" in sys.argv:
        sys.stderr.write(_('✅ [DAEMON] Hardware Atomics LOADED: {}\n').format(omnipkg_atomic))
except ImportError as e:
    _HAS_ATOMICS = False
    # Only show the failure warning if the user explicitly asks for verbose output.
    if "--verbose" in sys.argv or "-V" in sys.argv:
        sys.stderr.write(_('⚠️ [DAEMON] Hardware Atomics FAILED: {}\n').format(e))
        # Also keep the helpful debug info inside the verbose check.
        sys.stderr.write(f"   sys.path: {sys.path}\n")

# ═══════════════════════════════════════════════════════════════
# 0. CONSTANTS & UTILITIES
# ═══════════════════════════════════════════════════════════════

# Use a system-agnostic temporary directory (e.g., /tmp on Linux, AppData\Local\Temp on Windows)
# and create an 'omnipkg' subdirectory for cleanliness.
OMNIPKG_TEMP_DIR = os.path.join(tempfile.gettempdir(), "omnipkg")

# Define all temp files using the cross-platform path
DEFAULT_SOCKET = os.path.join(OMNIPKG_TEMP_DIR, "omnipkg_daemon.sock")
PID_FILE = os.path.join(OMNIPKG_TEMP_DIR, "omnipkg_daemon.pid")
SHM_REGISTRY_FILE = os.path.join(OMNIPKG_TEMP_DIR, "omnipkg_shm_registry.json")
DAEMON_LOG_FILE = os.path.join(OMNIPKG_TEMP_DIR, "omnipkg_daemon.log")

# ═══════════════════════════════════════════════════════════════
# STATE MONITOR (OPTIMISTIC CONCURRENCY CONTROL)
# ═══════════════════════════════════════════════════════════════
class SharedStateMonitor:
    """
    Manages a shared memory control block for Optimistic Concurrency Control.
    
    Structure Layout (128 bytes total to ensure cache line isolation):
    - [0:8]   Version (int64) - Monotonically increasing
    - [8:16]  Writer PID (int64) - Who holds the lock
    - [16:24] Lock State (int64) - 0=Free, 1=Locked
    - [24:128] Padding (Prevent False Sharing)
    
    NOTE: In the future, this class will be replaced by a C++ extension
    performing true atomic hardware instructions (LOCK CMPXCHG).
    For now, we simulate atomicity using a file lock on the control block.
    """
    
    STRUCT_FMT = "qqq104x"  # 3 int64s + 104 pad bytes = 128 bytes
    STRUCT_SIZE = struct.calcsize(STRUCT_FMT)

    def __init__(self, name: str, create: bool = False):
        from multiprocessing import shared_memory
        self.name = name
        try:
            if create:
                # Cleanup existing if needed
                try:
                    s = shared_memory.SharedMemory(name=name)
                    s.close()
                    s.unlink()
                except: pass
                self.shm = shared_memory.SharedMemory(create=True, size=self.STRUCT_SIZE, name=name)
                # Initialize to 0
                self.shm.buf[:] = bytearray(self.STRUCT_SIZE)
            else:
                self.shm = shared_memory.SharedMemory(name=name)
        except Exception as e:
            raise RuntimeError(_('Failed to attach control block {}: {}').format(name, e))
            
        # We need a secondary lock mechanism because Python lacks true atomic CAS
        # In C++, this would be std::atomic<T>
        self._lock_file = Path(tempfile.gettempdir()) / "omnipkg" / f"{name}.lock"
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = filelock.FileLock(str(self._lock_file))

    def read_state(self) -> Tuple[int, int, int]:
        """Reads current state without locking (dirty read)."""
        return struct.unpack(self.STRUCT_FMT, self.shm.buf)

    def get_version(self) -> int:
        """Get the current version number."""
        ver, unused, unused = self.read_state()
        return ver

    def try_lock_and_validate(self, expected_version: int) -> bool:
        """
        Attempt to lock for writing, BUT only if version hasn't changed.
        This is the CAS (Compare-And-Swap) Simulation.
        """
        try:
            self._lock.acquire(timeout=0.01) # Non-blocking attempt
            
            # Re-read state inside lock
            current_ver, unused, is_locked = struct.unpack(self.STRUCT_FMT, self.shm.buf)
            
            if is_locked or current_ver != expected_version:
                self._lock.release()
                return False
                
            # Acquired! Set locked flag
            struct.pack_into(self.STRUCT_FMT, self.shm.buf, 0, current_ver, os.getpid(), 1)
            # Note: We KEEP the file lock held until commit
            return True
            
        except (filelock.Timeout, Exception):
            return False

    def acquire_atomic_spinlock(self, timeout_seconds: float = 5.0) -> int:
        """
        Spinlock using LOCK CMPXCHG.
        Protocol: We only lock if Version is EVEN.
        Action: CAS(Current, Current + 1).
        Returns: The new (Odd) version if successful, else raises Timeout.
        """
        if not _HAS_ATOMICS: raise NotImplementedError("No atomic extension found")
        
        # Wrap memoryview in ctypes to get address
        c_obj = ctypes.c_longlong.from_buffer(self.shm.buf)
        addr = ctypes.addressof(c_obj)
        
        start = time.time()
        
        while True:
            # 1. Dirty Read (Fast check)
            current = self.get_version()
            
            # If locked (Odd), wait
            if current % 2 != 0:
                if time.time() - start > timeout_seconds:
                    raise TimeoutError("Spinlock timeout")
                
                # 🔥 CRITICAL FIX: YIELD THE GIL!
                # Without this, spinning threads starve the lock holder.
                # time.sleep(0) yields the thread's timeslice.
                time.sleep(0) 
                continue 
                
            # 2. Atomic Attempt: Even -> Odd
            # If successful, we own the lock!
            if omnipkg_atomic.cas64(addr, current, current + 1):  # ← FIXED!
                return current + 1
            
            # CAS failed (contention). Backoff slightly.
            time.sleep(0)

    def release_atomic_spinlock(self, my_odd_version: int):
        """
        Unlock: CAS(Odd -> Odd + 1). Makes it Even (Free).
        """
        # 🔥 FIX: Wrap memoryview in ctypes to get address
        c_obj = ctypes.c_longlong.from_buffer(self.shm.buf)
        addr = ctypes.addressof(c_obj)
        
        # Verify we still own it (sanity check)
        if not omnipkg_atomic.cas64(addr, my_odd_version, my_odd_version + 1):  # ← FIXED!
             # Should never happen if logic is sound
             sys.stderr.write("CRITICAL: Atomic release failed (Version Mismatch)!\n")

    def commit_and_release(self, new_version: int):
        """Write finished. Increment version, clear lock flag, release file lock."""
        try:
            struct.pack_into(self.STRUCT_FMT, self.shm.buf, 0, new_version, 0, 0)
        finally:
            if self._lock.is_locked:
                self._lock.release()

    def close(self):
        self.shm.close()
        try:
            if hasattr(self, '_lock') and self._lock.is_locked:
                self._lock.release()
        except: pass
        
    def unlink(self):
        try:
            self.shm.unlink()
        except: pass

# ═══════════════════════════════════════════════════════════════
# HFT OPTIMIZATION: Silence Resource Tracker
# ═══════════════════════════════════════════════════════════════

try:
    from multiprocessing import resource_tracker

    def _hft_ignore_shm_tracking():
        """
        Monkey-patch Python's resource_tracker to ignore SharedMemory segments.
        In HFT/Daemon mode, we manage memory lifecycles manually for zero-latency.
        The tracker adds overhead and complains when we are faster than it.
        """
        # Save original methods
        _orig_register = resource_tracker.register
        _orig_unregister = resource_tracker.unregister

        def hft_register(name, rtype):
            if rtype == "shared_memory":
                return
            return _orig_register(name, rtype)

        def hft_unregister(name, rtype):
            if rtype == "shared_memory":
                return
            return _orig_unregister(name, rtype)

        # Apply patch
        resource_tracker.register = hft_register
        resource_tracker.unregister = hft_unregister

    # Apply immediately
    _hft_ignore_shm_tracking()

except ImportError:
    pass


if TYPE_CHECKING:
    from multiprocessing import shared_memory

    import numpy as np
    import torch


def send_json(sock: socket.socket, data: dict, timeout: float = 30.0):
    """Sends a JSON dictionary over a socket with timeout protection."""
    sock.settimeout(timeout)
    json_string = json.dumps(data)
    length_prefix = len(json_string).to_bytes(8, "big")
    sock.sendall(length_prefix + json_string.encode("utf-8"))


def recv_json(sock: socket.socket, timeout: float = 30.0) -> dict:
    """Receives a JSON dictionary over a socket with timeout protection."""
    sock.settimeout(timeout)
    length_prefix = sock.recv(8)
    if not length_prefix:
        raise ConnectionResetError("Socket closed by peer.")
    length = int.from_bytes(length_prefix, "big")
    data_buffer = bytearray()
    while len(data_buffer) < length:
        chunk = sock.recv(min(length - len(data_buffer), 8192))
        if not chunk:
            raise ConnectionResetError("Socket stream interrupted.")
        data_buffer.extend(chunk)
    return json.loads(data_buffer.decode("utf-8"))

class UniversalGpuIpc:
    """
    Pure CUDA IPC using ctypes - works WITHOUT PyTorch!
    This is the secret sauce for true zero-copy.
    """

    _lib = None

    @classmethod
    def get_lib(cls):
        """Find and load libcudart.so from various locations."""
        if cls._lib:
            return cls._lib

        candidates = []

        # Try PyTorch's lib directory (if torch is installed)
        try:
            import torch

            torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
            candidates.extend(glob.glob(os.path.join(torch_lib, "libcudart.so*")))
        except:
            pass

        # Try conda environment
        if "CONDA_PREFIX" in os.environ:
            candidates.extend(
                glob.glob(os.path.join(os.environ["CONDA_PREFIX"], "lib", "libcudart.so*"))
            )

        # Try system libraries
        candidates.extend(["libcudart.so.12", "libcudart.so.11.0", "libcudart.so"])

        for lib in candidates:
            try:
                cls._lib = ctypes.CDLL(lib)
                return cls._lib
            except:
                continue

        raise RuntimeError("Could not load libcudart.so - CUDA not available")

    @staticmethod
    def share(tensor):
        """
        Share a PyTorch CUDA tensor via CUDA IPC handle.
        Returns serializable metadata that can be sent over socket.
        """

        lib = UniversalGpuIpc.get_lib()
        ptr = tensor.data_ptr()

        # Define CUDA structures
        class cudaPointerAttributes(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("device", ctypes.c_int),
                ("devicePointer", ctypes.c_void_p),
                ("hostPointer", ctypes.c_void_p),
            ]

        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]

        # Set function signatures
        lib.cudaPointerGetAttributes.argtypes = [
            ctypes.POINTER(cudaPointerAttributes),
            ctypes.c_void_p,
        ]
        lib.cudaIpcGetMemHandle.argtypes = [
            ctypes.POINTER(cudaIpcMemHandle_t),
            ctypes.c_void_p,
        ]

        # Get base pointer and offset
        attrs = cudaPointerAttributes()
        if lib.cudaPointerGetAttributes(ctypes.byref(attrs), ctypes.c_void_p(ptr)) == 0:
            base_ptr = attrs.devicePointer or ptr
            offset = ptr - base_ptr
        else:
            base_ptr = ptr
            offset = 0

        # Get IPC handle
        handle = cudaIpcMemHandle_t()
        err = lib.cudaIpcGetMemHandle(ctypes.byref(handle), ctypes.c_void_p(base_ptr))

        if err != 0:
            raise RuntimeError(f"cudaIpcGetMemHandle failed with code {err}")

        # Return JSON-serializable metadata (base64-encode bytes!)
        handle_bytes = ctypes.string_at(ctypes.byref(handle), 64)
        return {
            # JSON-safe!
            "handle": base64.b64encode(handle_bytes).decode("ascii"),
            "offset": offset,
            "shape": tuple(tensor.shape),
            "typestr": "<f4",
            "device": tensor.device.index or 0,
        }

    @staticmethod
    def load(data):
        """
        Load a CUDA tensor from IPC metadata.
        Returns PyTorch tensor pointing to shared GPU memory.
        """

        lib = UniversalGpuIpc.get_lib()

        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]

        lib.cudaIpcOpenMemHandle.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            cudaIpcMemHandle_t,
            ctypes.c_uint,
        ]

        # Reconstruct handle (decode from base64)
        handle = cudaIpcMemHandle_t()
        handle_bytes = base64.b64decode(data["handle"])
        ctypes.memmove(ctypes.byref(handle), handle_bytes, 64)

        # Open IPC handle
        dev_ptr = ctypes.c_void_p()
        err = lib.cudaIpcOpenMemHandle(ctypes.byref(dev_ptr), handle, 1)

        if err == 201:  # cudaErrorAlreadyMapped
            return None  # Same process - can't IPC to yourself

        if err != 0:
            raise RuntimeError(f"cudaIpcOpenMemHandle failed with code {err}")

        # Calculate final pointer with offset
        final_ptr = dev_ptr.value + data["offset"]

        # Create PyTorch tensor from raw pointer
        import torch

        class CUDABuffer:
            """Dummy buffer that exposes __cuda_array_interface__."""

            def __init__(self, ptr, shape, typestr):
                self.__cuda_array_interface__ = {
                    "data": (ptr, False),
                    "shape": shape,
                    "typestr": typestr,
                    "version": 3,
                }

        # PyTorch can consume __cuda_array_interface__
        return torch.as_tensor(
            CUDABuffer(final_ptr, data["shape"], data["typestr"]),
            device=f"cuda:{data['device']}",
        )
        
class SHMRegistry:
    """Track and cleanup orphaned shared memory blocks."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active_blocks: Set[str] = set()
        self._load_registry()

    def _load_registry(self):
        try:
            if os.path.exists(SHM_REGISTRY_FILE):
                with open(SHM_REGISTRY_FILE, "r", encoding="utf-8") as f:
                    self.active_blocks = set(json.load(f))
        except:
            self.active_blocks = set()

    def _save_registry(self):
        try:
            with open(SHM_REGISTRY_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.active_blocks), f)
        except:
            pass

    def register(self, name: str):
        with self.lock:
            self.active_blocks.add(name)
            self._save_registry()

    def unregister(self, name: str):
        with self.lock:
            self.active_blocks.discard(name)
            self._save_registry()

    def cleanup_orphans(self):
        """Remove orphaned shared memory blocks from /dev/shm/."""
        with self.lock:
            from multiprocessing import shared_memory

            for name in list(self.active_blocks):
                try:
                    shm = shared_memory.SharedMemory(name=name)
                    shm.close()
                    shm.unlink()
                    self.active_blocks.discard(name)
                except FileNotFoundError:
                    self.active_blocks.discard(name)
                except Exception:
                    pass
            self._save_registry()

# Global SHM registry
shm_registry = SHMRegistry()

# ═══════════════════════════════════════════════════════════════
# 1. PERSISTENT WORKER SCRIPT (FIXED - No raw string)
# ═══════════════════════════════════════════════════════════════
# CRITICAL FIX: Proper string escaping in _DAEMON_SCRIPT
# The issue: sys.stderr.write() calls need proper escaping of backslash-n
# ALWAYS USE '\\n' IN PLACE OF '\n' INSIDE THE RAW STRING
# DO NOT PUT DOCSTRINGS INSIDE THE RAW STRING EITHER, IT BREAKS THE ESCAPES

# ═══════════════════════════════════════════════════════════════
# 1. PERSISTENT WORKER SCRIPT (FIXED - NO BLIND IMPORTS)
# ═══════════════════════════════════════════════════════════════

"""
CRITICAL FIX: Correct Import Order in _DAEMON_SCRIPT

The Problem:
------------
The script was trying to import tensorflow/torch BEFORE activating the bubble.
This caused "No module named 'tensorflow'" errors because the bubble paths
weren't in sys.path yet.

The Solution:
------------
Move ALL framework imports to AFTER the bubble activation and cleanup.

Correct Order:
1. Read PKG_SPEC from stdin
2. Import omnipkgLoader
3. Activate bubble (adds paths to sys.path)
4. Cleanup cloaks
5. Restore stdout
6. NOW import torch/tensorflow (they're in sys.path now!)
7. Send READY signal
8. Enter execution loop
"""

_DAEMON_SCRIPT = """#!/usr/bin/env python3
import os
import sys
import json
import shutil
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

# CRITICAL: Mark as daemon worker
os.environ['OMNIPKG_IS_DAEMON_WORKER'] = '1'
os.environ['OMNIPKG_DISABLE_WORKER_POOL'] = '1'

# ═══════════════════════════════════════════════════════════════
# STEP 0: SCRUB INHERITED BUBBLE PATHS
# The parent process may have an omnipkg bubble active when it
# spawns this worker subprocess.  The bubble path leaks into the
# child via:
#   • os.environ["PYTHONPATH"] — prepended by bubble activation
#   • sys.path itself (inherited via fork / execv with -c)
# If we don't remove it now, Python will import numpy (or any
# other bubble package) from the PARENT'S active bubble instead
# of the bubble we are about to activate for this worker spec.
# This causes "cannot import name 'index_tricks' from partially
# initialized module 'numpy.lib'" and similar cross-version errors.
# Scrub unconditionally — the worker will activate the correct
# bubble in STEP 3.
# ═══════════════════════════════════════════════════════════════
def _scrub_bubble_paths():
    _marker = '.omnipkg_versions'
    # 1. Strip from sys.path
    sys.path[:] = [p for p in sys.path if _marker not in p]
    # 2. Strip from PYTHONPATH so any child subprocesses don't inherit it either
    _pypath = os.environ.get('PYTHONPATH', '')
    if _marker in _pypath:
        _clean = os.pathsep.join(
            p for p in _pypath.split(os.pathsep) if _marker not in p
        )
        if _clean:
            os.environ['PYTHONPATH'] = _clean
        else:
            os.environ.pop('PYTHONPATH', None)

_scrub_bubble_paths()

# CRITICAL: Configure stdin/stdout/stderr for proper encoding and buffering
sys.stdin.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

_original_stdout = sys.stdout
_devnull = open(os.devnull, 'w')
sys.stdout = _devnull

def fatal_error(msg, error=None):
    import traceback
    error_obj = {'status': 'FATAL', 'error': msg}
    if error:
        error_obj['exception'] = str(error)
        error_obj['traceback'] = traceback.format_exc()
    sys.stderr.write(json.dumps(error_obj) + '\\n')
    sys.stderr.flush()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# STEP 1: READ PKG_SPEC (MUST BE FIRST)
# ═══════════════════════════════════════════════════════════════
try:
    # An idle worker will block here until it's assigned a spec.
    input_line = sys.stdin.readline()
    
    # If readline returns empty, it means EOF (the daemon process died).
    # Exit gracefully instead of logging a FATAL error. The daemon's
    # idle pool monitor will replace this worker if needed.
    if not input_line:
        sys.exit(0)
    
    setup_data = json.loads(input_line.strip())
    PKG_SPEC = setup_data.get('package_spec', '')
    
    if not PKG_SPEC:
        fatal_error('Missing package_spec')
except Exception as e:
    fatal_error('Startup configuration failed', e)

# ═══════════════════════════════════════════════════════════════
# STEP 2: IMPORT OMNIPKG LOADER
# ═══════════════════════════════════════════════════════════════
try:
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    fatal_error('Failed to import omnipkgLoader', e)

if hasattr(omnipkgLoader, '_nesting_depth'):
    omnipkgLoader._nesting_depth = 0

# ═══════════════════════════════════════════════════════════════
# STEP 3: ACTIVATE BUBBLE (Adds paths to sys.path)
# ═══════════════════════════════════════════════════════════════
try:
    specs = [s.strip() for s in PKG_SPEC.split(',')]
    loaders = []
    
    for s in specs:
        l = omnipkgLoader(s, isolation_mode='overlay', quiet=False)
        l.__enter__()
        loaders.append(l)

    # CUDA injection (your existing code is correct here)
    cuda_lib_paths = []
    target_cuda_ver = None
    
    if '+cu11' in PKG_SPEC or 'cu11' in PKG_SPEC:
        target_cuda_ver = '11'
    elif '+cu12' in PKG_SPEC or 'cu12' in PKG_SPEC:
        target_cuda_ver = '12'
    
    if target_cuda_ver and loaders and hasattr(loaders[0], 'multiversion_base'):
        from pathlib import Path
        multiversion_base = Path(loaders[0].multiversion_base)
        search_pattern = f'nvidia-*-cu{target_cuda_ver}-*'
        for nvidia_bubble in multiversion_base.glob(search_pattern):
            if nvidia_bubble.is_dir() and '_omnipkg_cloaked' not in nvidia_bubble.name:
                nvidia_dir = nvidia_bubble / 'nvidia'
                if nvidia_dir.exists():
                    for module_dir in nvidia_dir.iterdir():
                        if module_dir.is_dir():
                            lib_dir = module_dir / 'lib'
                            if lib_dir.exists() and list(lib_dir.glob('*.so*')):
                                cuda_lib_paths.append(str(lib_dir))
    
    if cuda_lib_paths:
        current_ld = os.environ.get('LD_LIBRARY_PATH', '')
        new_ld = os.pathsep.join(cuda_lib_paths)
        if current_ld:
            new_ld = new_ld + os.pathsep + current_ld
        os.environ['LD_LIBRARY_PATH'] = new_ld
        
        sys.stderr.write(f'🔧 [DAEMON] Injected {len(cuda_lib_paths)} CUDA paths (Target: cu{target_cuda_ver})\\n')
        sys.stderr.flush()
        
        import ctypes
        candidates = [f'libcudart.so.{target_cuda_ver}.0', 'libcudart.so.12', 'libcudart.so.11.0']
        for lib_path in cuda_lib_paths:
            for cand in candidates:
                cudart = Path(lib_path) / cand
                if cudart.exists():
                    try:
                        ctypes.CDLL(str(cudart))
                        sys.stderr.write(f'   ✅ Pre-loaded: {cudart.name}\\n')
                        sys.stderr.flush()
                        break
                    except:
                        pass
            if 'cudart' in locals() and 'ctypes.CDLL' in locals(): 
                break 
    elif target_cuda_ver:
        sys.stderr.write(f'ℹ️  [DAEMON] No CUDA libraries found for requested cu{target_cuda_ver}\\n')
        sys.stderr.flush()
    
    globals()['_omnipkg_loaders'] = loaders

    # ═══════════════════════════════════════════════════════════════
    # STEP 3b: NUMPY COMPATIBILITY SIDECAR
    # torch/tensorflow need a numpy ABI-compatible with their build.
    # Loader auto-installs if missing. Activating before the bare
    # numpy import ensures the right one wins sys.path precedence.
    # ═══════════════════════════════════════════════════════════════
    if any(fw in PKG_SPEC for fw in ('torch', 'tensorflow')):
        try:
            _np_loader = omnipkgLoader('numpy==1.26.4', isolation_mode='overlay', quiet=True)
            _np_loader.__enter__()
            loaders.append(_np_loader)
        except Exception as _np_err:
            sys.stderr.write(f'⚠️  [DAEMON] numpy sidecar failed: {_np_err}\\n')
            sys.stderr.flush()

    # ═══════════════════════════════════════════════════════════════
    # STEP 4: CLEANUP CLOAKS (Critical - must happen before imports)
    # ═══════════════════════════════════════════════════════════════
    sys.stderr.write('🧹 [DAEMON] Starting immediate post-activation cleanup...\\n')
    sys.stderr.flush()
    
    cleanup_count = 0
    
    for loader in loaders:
        if hasattr(loader, '_cloaked_main_modules') and loader._cloaked_main_modules:
            for original_path, cloak_path, was_successful in reversed(loader._cloaked_main_modules):
                if not was_successful or not cloak_path.exists(): 
                    continue
                try:
                    if original_path.exists():
                        if original_path.is_dir(): 
                            shutil.rmtree(original_path, ignore_errors=True)
                        else: 
                            original_path.unlink()
                    shutil.move(str(cloak_path), str(original_path))
                    cleanup_count += 1
                except Exception: 
                    pass
            loader._cloaked_main_modules.clear()
        
        if hasattr(loader, '_cloaked_bubbles') and loader._cloaked_bubbles:
            for cloak_path, original_path in reversed(loader._cloaked_bubbles):
                try:
                    if cloak_path.exists():
                        if original_path.exists():
                            if original_path.is_dir(): 
                                shutil.rmtree(original_path, ignore_errors=True)
                            else: 
                                original_path.unlink()
                        shutil.move(str(cloak_path), str(original_path))
                        cleanup_count += 1
                except Exception: 
                    pass
            loader._cloaked_bubbles.clear()
        
        if hasattr(loader, '_my_main_env_package') and loader._my_main_env_package:
            if hasattr(omnipkgLoader, '_active_main_env_packages'):
                omnipkgLoader._active_main_env_packages.discard(loader._my_main_env_package)

    sys.stderr.write(f'✅ [DAEMON] Cleanup complete! Restored {cleanup_count} items\\n')
    sys.stderr.flush()
    
except Exception as e:
    fatal_error(f'Failed to activate {PKG_SPEC}', e)

# ═══════════════════════════════════════════════════════════════
# STEP 5: RESTORE STDOUT
# ═══════════════════════════════════════════════════════════════
_devnull.close()
sys.stdout = _original_stdout
sys.stdout.reconfigure(line_buffering=True)

# ═══════════════════════════════════════════════════════════════
# THE REAL FIX: Complete stub backend modules
# ═══════════════════════════════════════════════════════════════

def _patch_opt_einsum_worker():    
    # Check what frameworks are NOT in this worker's spec
    unavailable = []
    for framework in ['torch', 'jax', 'cupy']:
        if framework not in PKG_SPEC and framework not in sys.modules:
            unavailable.append(framework)
    
    if not unavailable:
        return  # All frameworks available, no patching needed
    
    try:
        import types
        
        for framework in unavailable:
            backend_name = f'opt_einsum.backends.{framework}'
            
            # Create a realistic backend module with expected exports
            backend_module = types.ModuleType(backend_name)
            backend_module.__file__ = '<omnipkg-isolated>'
            
            # Add stub functions/classes that opt_einsum expects
            # These are named after what opt_einsum.backends.* modules export
            
            # Satisfy opt_einsum interface checks
            backend_module.build_expression = lambda *args, **kwargs: None
            backend_module.evaluate_constants = lambda *args, **kwargs: None
            backend_module.compute_size_by_dict = lambda *args, **kwargs: None

            if framework == 'torch':
                # opt_einsum.backends.torch exports: to_torch, TorchBackend
                def stub_to_torch(array): raise NotImplementedError("torch backend unavailable")
                backend_module.to_torch = stub_to_torch
                backend_module.TorchBackend = object  # Dummy class
                
            elif framework == 'jax':
                # opt_einsum.backends.jax exports: to_jax, JaxBackend
                def stub_to_jax(array): raise NotImplementedError("jax backend unavailable")
                backend_module.to_jax = stub_to_jax
                backend_module.JaxBackend = object
                
            elif framework == 'cupy':
                # opt_einsum.backends.cupy exports: to_cupy, CupyBackend
                def stub_to_cupy(array): raise NotImplementedError("cupy backend unavailable")
                backend_module.to_cupy = stub_to_cupy
                backend_module.CupyBackend = object
            
            # Add to sys.modules
            sys.modules[backend_name] = backend_module
        
        sys.stderr.write(f'🩹 [DAEMON] Isolated worker from: {", ".join(unavailable)}\\n')
        sys.stderr.flush()
        
    except Exception as e:
        import traceback
        sys.stderr.write(f'⚠️  [DAEMON] Isolation patch failed: {e}\\n')
        sys.stderr.write(traceback.format_exc())
        sys.stderr.flush()

# Apply isolation patch BEFORE any imports
_patch_opt_einsum_worker()

# NOW it's safe to import TensorFlow - when it tries to import opt_einsum,
# and opt_einsum.backends tries to "from .torch import TorchBackend",
# it will succeed (getting our stub) instead of crashing

# ═══════════════════════════════════════════════════════════
# STEP 6: NOW IMPORT FRAMEWORKS (Paths are in sys.path now!)
# ═══════════════════════════════════════════════════════════

# 🔥 CRITICAL FIX: Capture stdout during imports
# TensorFlow's NumPy patcher writes to stdout, breaking JSON protocol
import io
_capture_stdout = io.StringIO()
_temp_stdout = sys.stdout
sys.stdout = _capture_stdout

try:
    import ctypes
    import glob

    # Lazy import numpy (always safe to try)
    try:
        import numpy as np
    except ImportError:
        np = None
        sys.stderr.write('⚠️  [DAEMON] NumPy not found - SHM features disabled\\n')
        sys.stderr.flush()

    # UniversalGpuIpc class (keep your existing code here - don't change it)
    class UniversalGpuIpc:
        _lib = None
        @classmethod
        def get_lib(cls):
            if cls._lib: return cls._lib
            candidates = []
            # OPTIMIZATION: Only look inside torch if it is requested or already loaded.
            # Importing torch is heavy (~300MB) and we want lightweight workers for non-ML tasks.
            if 'torch' in PKG_SPEC or 'torch' in sys.modules:
                try:
                    import torch
                    torch_lib = os.path.join(os.path.dirname(torch.__file__), 'lib')
                    candidates.extend(glob.glob(os.path.join(torch_lib, 'libcudart.so*')))
                except: pass
            
            if 'CONDA_PREFIX' in os.environ:
                candidates.extend(glob.glob(os.path.join(os.environ['CONDA_PREFIX'], 'lib', 'libcudart.so*')))
            candidates.extend(['libcudart.so.12', 'libcudart.so.11.0', 'libcudart.so'])
            for lib in candidates:
                try:
                    cls._lib = ctypes.CDLL(lib)
                    return cls._lib
                except: continue
            raise RuntimeError("Could not load libcudart.so")
        
        @staticmethod
        def share(tensor):
            import base64
            lib = UniversalGpuIpc.get_lib()
            ptr = tensor.data_ptr()
            class cudaPointerAttributes(ctypes.Structure):
                _fields_ = [("type", ctypes.c_int), ("device", ctypes.c_int), 
                            ("devicePointer", ctypes.c_void_p), ("hostPointer", ctypes.c_void_p)]
            class cudaIpcMemHandle_t(ctypes.Structure):
                _fields_ = [("reserved", ctypes.c_char * 64)]
            lib.cudaPointerGetAttributes.argtypes = [ctypes.POINTER(cudaPointerAttributes), ctypes.c_void_p]
            lib.cudaIpcGetMemHandle.argtypes = [ctypes.POINTER(cudaIpcMemHandle_t), ctypes.c_void_p]
            attrs = cudaPointerAttributes()
            if lib.cudaPointerGetAttributes(ctypes.byref(attrs), ctypes.c_void_p(ptr)) == 0:
                base_ptr = attrs.devicePointer or ptr
                offset = ptr - base_ptr
            else:
                base_ptr = ptr
                offset = 0
            handle = cudaIpcMemHandle_t()
            err = lib.cudaIpcGetMemHandle(ctypes.byref(handle), ctypes.c_void_p(base_ptr))
            if err != 0: raise RuntimeError(f"cudaIpcGetMemHandle failed: {err}")
            handle_bytes = ctypes.string_at(ctypes.byref(handle), 64)
            return {"handle": base64.b64encode(handle_bytes).decode('ascii'), "offset": offset,
                    "shape": tuple(tensor.shape), "typestr": "<f4", "device": tensor.device.index or 0}
        
        @staticmethod
        def load(data):
            import base64
            lib = UniversalGpuIpc.get_lib()
            class cudaIpcMemHandle_t(ctypes.Structure):
                _fields_ = [("reserved", ctypes.c_char * 64)]
            lib.cudaIpcOpenMemHandle.argtypes = [ctypes.POINTER(ctypes.c_void_p), cudaIpcMemHandle_t, ctypes.c_uint]
            handle = cudaIpcMemHandle_t()
            handle_bytes = base64.b64decode(data["handle"])
            ctypes.memmove(ctypes.byref(handle), handle_bytes, 64)
            dev_ptr = ctypes.c_void_p()
            err = lib.cudaIpcOpenMemHandle(ctypes.byref(dev_ptr), handle, 1)
            if err == 201: return None 
            if err != 0: raise RuntimeError(f"cudaIpcOpenMemHandle failed: {err}")
            final_ptr = dev_ptr.value + data["offset"]
            import torch
            class CUDABuffer:
                def __init__(self, ptr, shape, typestr):
                    self.__cuda_array_interface__ = { "data": (ptr, False), "shape": shape, "typestr": typestr, "version": 3 }
            return torch.as_tensor(CUDABuffer(final_ptr, data["shape"], data["typestr"]), device=f"cuda:{data['device']}")

    # LAZY CUDA DETECTION: Don't load libcudart until we actually need it.
    # This keeps VIRT memory low for CPU-only workers (like rich).
    _universal_gpu_ipc_available = None 

    def ensure_gpu_ipc():
        global _universal_gpu_ipc_available
        if _universal_gpu_ipc_available is not None:
            return _universal_gpu_ipc_available
            
        try:
            UniversalGpuIpc.get_lib()
            _universal_gpu_ipc_available = True
            sys.stderr.write('🔥🔥🔥 [DAEMON] UNIVERSAL CUDA IPC ENABLED (ctypes - NO PYTORCH NEEDED)\\n')
            sys.stderr.flush()
        except Exception:
            _universal_gpu_ipc_available = False
        return _universal_gpu_ipc_available

    # Initialize flags to prevent NameError
    _gpu_ipc_available = False
    _torch_available = False
    _cuda_available = False
    _native_ipc_mode = False

    # Import TensorFlow if in spec
    if 'tensorflow' in PKG_SPEC:
        try:
            import tensorflow as tf
            gpus = tf.config.list_physical_devices('GPU')
            if gpus:
                for gpu in gpus:
                    try: 
                        tf.config.experimental.set_memory_growth(gpu, True)
                    except: 
                        pass
            sys.stderr.write('✅ [DAEMON] TensorFlow initialized (Memory Growth ON)\\n')
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f'⚠️  [DAEMON] TensorFlow import failed: {e}\\n')
            sys.stderr.flush()

    # Import PyTorch if in spec
    if 'torch' in PKG_SPEC:
        try:
            import torch
            _torch_available = True
            _cuda_available = torch.cuda.is_available()
            sys.stderr.write(f'🔍 [DAEMON] PyTorch {torch.__version__} initialized\\n')
            sys.stderr.flush()
            
            if _cuda_available:
                torch_version = torch.__version__.split('+')[0]
                major = int(torch_version.split('.')[0])
                if major == 1:
                    try:
                        test_tensor = torch.zeros(1).cuda()
                        if hasattr(test_tensor.storage(), '_share_cuda_'):
                            _native_ipc_mode = True
                            _gpu_ipc_available = True
                            sys.stderr.write('🔥🔥🔥 [DAEMON] NATIVE CUDA IPC ENABLED\\n')
                            sys.stderr.flush()
                    except: 
                        pass
                else:
                    _gpu_ipc_available = True
                    sys.stderr.write('🚀 [DAEMON] GPU IPC available (Hybrid/Universal)\\n')
                    sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f'⚠️  [DAEMON] PyTorch import failed: {e}\\n')
            sys.stderr.flush()

    
    # Eagerly resolve Universal IPC at startup — True/False before first request.
    try:
        UniversalGpuIpc.get_lib()
        _universal_gpu_ipc_available = True
        _gpu_ipc_available = True
        sys.stderr.write('🔥🔥🔥 [DAEMON] UNIVERSAL CUDA IPC ENABLED (ctypes - NO PYTORCH NEEDED)\\n')
        sys.stderr.flush()
    except Exception:
        _universal_gpu_ipc_available = False

finally:
    # 🔥 RESTORE STDOUT and log any captured output to stderr
    sys.stdout = _temp_stdout
    _captured = _capture_stdout.getvalue()
    if _captured:
        sys.stderr.write(f'📝 [DAEMON] Captured stdout during imports:\\n{_captured}')
        sys.stderr.flush()
        
from multiprocessing import shared_memory
from contextlib import redirect_stdout, redirect_stderr
import base64

# Ensure all IPC flags are defined (defensive programming)
try:
    _gpu_ipc_available
except NameError:
    _gpu_ipc_available = False

try:
    _torch_available
except NameError:
    _torch_available = False

try:
    _cuda_available
except NameError:
    _cuda_available = False

try:
    _native_ipc_mode
except NameError:
    _native_ipc_mode = False

try:
    _universal_gpu_ipc_available
except NameError:
    _universal_gpu_ipc_available = None

# Now it's safe to use these variables in the READY signal

# ═══════════════════════════════════════════════════════════════
# STEP 7: SEND READY SIGNAL
# ═══════════════════════════════════════════════════════════════
try:
    ready_msg = {'status': 'READY', 'package': PKG_SPEC, 'native_ipc': _native_ipc_mode}
    # CRITICAL FIX: Write to _original_stdout, not sys.stdout (which is /dev/null)
    _original_stdout.write(json.dumps(ready_msg) + '\\n')
    _original_stdout.flush()
    sys.stderr.write(f'✅ [DAEMON] Sent READY signal for {PKG_SPEC}\\n')
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write(f"ERROR: Failed to send READY: {e}\\n")
    sys.stderr.flush()
    sys.exit(1)
sys.stderr.write('🎯 [WORKER] READY signal sent, entering main execution loop...\\n')
sys.stderr.flush()

# ═══════════════════════════════════════════════════════════════
# PERSISTENT GLOBALS — survives across all task executions
# This is what makes model caching work: globals()["_CACHED_MODEL"]
# set on call 1 is still there on call 2, 3, ... N.
# Per-task transient values (tensor_in, arr_in, etc.) are injected
# fresh each call but do NOT leak back into this dict.
# ═══════════════════════════════════════════════════════════════
_worker_globals = {
    '__builtins__': __builtins__,
    'sys': sys,
    'os': os,
    'json': json,
}
# Pre-populate with already-imported heavy modules so worker code
# can use them without re-importing (torch, np already loaded above).
if _torch_available:
    _worker_globals['torch'] = torch
if np is not None:
    _worker_globals['np'] = np

# ═══════════════════════════════════════════════════════════════
# MAIN EXECUTION LOOP
# ═══════════════════════════════════════════════════════════════

_last_ipc_tensor = None

while True:
    try:
        command_line = sys.stdin.readline()
        if not command_line:
            sys.stderr.write('🛑 [WORKER] EOF received, exiting\\n')
            sys.stderr.flush()
            break
        command_line = command_line.strip()
        if not command_line:
            continue
        command = json.loads(command_line)
        if command.get('type') == 'shutdown':
            break
        task_id = command.get('task_id', 'UNKNOWN')
        
        worker_code = command.get('code', '')
        # Per-task transient scope: IPC handles, arr_in/out, input_data.
        # This is SEPARATE from _worker_globals so transient objects don't
        # accumulate across calls (e.g. large tensors freed after each task).
        task_scope = {'input_data': command}
        # exec_scope merges persistent globals + task-local — code sees both.
        # We pass _worker_globals as the globals dict so that assignments like
        #   globals()["_CACHED_MODEL"] = ...
        # persist into _worker_globals and survive to the next call.
        exec_scope = _worker_globals
        shm_blocks = []
        
        is_cuda_request = command.get('type') == 'execute_cuda'
        in_meta = command.get('cuda_in') if is_cuda_request else command.get('shm_in')
        out_meta = command.get('cuda_out') if is_cuda_request else command.get('shm_out')
        actual_cuda_method = 'hybrid'
        # ═══════════════════════════════════════════════════════════
        # INPUT HANDLING - UNIVERSAL IPC FIRST!
        # ═══════════════════════════════════════════════════════════

        if in_meta and is_cuda_request and _universal_gpu_ipc_available and 'universal_ipc' in in_meta:
            try:
                # Load tensor using universal IPC
                tensor = UniversalGpuIpc.load(in_meta['universal_ipc'])
                
                if tensor is None:
                    raise RuntimeError("Same process - cannot IPC to self")
                
                exec_scope['tensor_in'] = tensor
                actual_cuda_method = 'universal_ipc'
                
                sys.stderr.write(f'🔥 [TASK {task_id}] UNIVERSAL IPC input (TRUE ZERO-COPY)\\n')
                sys.stderr.flush()
                
            except Exception as e:
                import traceback
                sys.stderr.write(f'⚠️  [TASK {task_id}] Universal IPC failed: {e}\\n')
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()
                in_meta.pop('universal_ipc', None)
        
        # NATIVE PYTORCH IPC (1.x)
        if in_meta and is_cuda_request and _native_ipc_mode and 'ipc_data' in in_meta and 'tensor_in' not in exec_scope:
            try:
                import base64
                data = in_meta['ipc_data']
                device = torch.device(f"cuda:{in_meta['device']}")
                
                storage_cls_name = data['storage_cls']
                # Fix for PyTorch 1.13+ TypedStorage issue
                if storage_cls_name == 'TypedStorage':
                    dtype_to_storage = {
                        'float32': 'FloatStorage', 'float64': 'DoubleStorage', 'float16': 'HalfStorage',
                        'int32': 'IntStorage', 'int64': 'LongStorage', 'int8': 'CharStorage', 
                        'uint8': 'ByteStorage', 'bool': 'BoolStorage', 'bfloat16': 'BFloat16Storage'
                    }
                    storage_cls_name = dtype_to_storage.get(data['dtype'], 'FloatStorage')
                
                storage_cls = getattr(torch, storage_cls_name, torch.FloatStorage)
                handle = base64.b64decode(data['storage_handle'])
                
                # Reconstruct storage from handle
                # Reconstruct storage from full IPC data (PyTorch 1.13+ compatible)
                storage = storage_cls._new_shared_cuda(
                    data['storage_device'],
                    handle,
                    data['storage_size_bytes'],
                    data['storage_offset_bytes'],
                    base64.b64decode(data['ref_counter_handle']),
                    data['ref_counter_offset'],
                    base64.b64decode(data['event_handle']) if data['event_handle'] else b'',
                    data['event_sync_required']
                )

                # Create tensor view
                tensor = torch.tensor([], dtype=getattr(torch, data['dtype']), device=device)
                tensor.set_(storage, data['tensor_offset'], tuple(data['tensor_size']), tuple(data['tensor_stride']))
                
                exec_scope['tensor_in'] = tensor
                actual_cuda_method = 'native_ipc'
                
                sys.stderr.write(f'🔥 [TASK {task_id}] NATIVE IPC input (PyTorch 1.x)\\n')
                sys.stderr.flush()
            except Exception as e:
                import traceback
                sys.stderr.write(f'⚠️  [TASK {task_id}] Native IPC input failed: {e}\\n')
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()

        # HYBRID PATH (SHM + GPU copy)
        if in_meta and 'tensor_in' not in exec_scope:
            if np is None:
                raise RuntimeError("NumPy is required for SHM inputs but is not available")
            shm_name = in_meta.get('shm_name') or in_meta.get('name')
            shm_in = shared_memory.SharedMemory(name=shm_name)
            shm_blocks.append(shm_in)
            
            arr_in = np.ndarray(
                tuple(in_meta['shape']),
                dtype=in_meta['dtype'],
                buffer=shm_in.buf
            )
            
            if is_cuda_request and _torch_available and _cuda_available:
                device = torch.device(f"cuda:{in_meta.get('device', 0)}")
                exec_scope['tensor_in'] = torch.from_numpy(arr_in).to(device)
                sys.stderr.write(f'🔄 [TASK {task_id}] HYBRID input (SHM→GPU)\\n')
                sys.stderr.flush()
            else:
                exec_scope['tensor_in'] = arr_in
                exec_scope['arr_in'] = arr_in
        
        # ═══════════════════════════════════════════════════════════
        # OUTPUT HANDLING
        # ═══════════════════════════════════════════════════════════
        arr_out = None
        
        # UNIVERSAL IPC OUTPUT — client pre-allocated, worker just opens handle
        if out_meta and is_cuda_request and _universal_gpu_ipc_available and 'universal_ipc' in out_meta:
            try:
                tensor = UniversalGpuIpc.load(out_meta['universal_ipc'])
                if tensor is None:
                    raise RuntimeError("Same process - cannot IPC to self")
                exec_scope['tensor_out'] = tensor
                actual_cuda_method = 'universal_ipc'
                sys.stderr.write(f'🔥 [TASK {task_id}] UNIVERSAL IPC output (TRUE ZERO-COPY)\\n')
                sys.stderr.flush()
            except Exception as e:
                import traceback
                sys.stderr.write(f'⚠️  [TASK {task_id}] Universal IPC output failed: {e}\\n')
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()
                out_meta.pop('universal_ipc', None)
        
        # NATIVE PYTORCH IPC (1.x) OUTPUT
        if out_meta and is_cuda_request and _native_ipc_mode and 'ipc_data' in out_meta and 'tensor_out' not in exec_scope:
            try:
                import base64
                data = out_meta['ipc_data']
                device = torch.device(f"cuda:{out_meta['device']}")
                
                storage_cls_name = data['storage_cls']
                # Fix for PyTorch 1.13+ TypedStorage issue
                if storage_cls_name == 'TypedStorage':
                    dtype_to_storage = {
                        'float32': 'FloatStorage', 'float64': 'DoubleStorage', 'float16': 'HalfStorage',
                        'int32': 'IntStorage', 'int64': 'LongStorage', 'int8': 'CharStorage', 
                        'uint8': 'ByteStorage', 'bool': 'BoolStorage', 'bfloat16': 'BFloat16Storage'
                    }
                    storage_cls_name = dtype_to_storage.get(data['dtype'], 'FloatStorage')
                
                storage_cls = getattr(torch, storage_cls_name, torch.FloatStorage)
                handle = base64.b64decode(data['storage_handle'])
                
                # Reconstruct storage from handle
                # Reconstruct storage from full IPC data (PyTorch 1.13+ compatible)
                storage = storage_cls._new_shared_cuda(
                    data['storage_device'],
                    handle,
                    data['storage_size_bytes'],
                    data['storage_offset_bytes'],
                    base64.b64decode(data['ref_counter_handle']),
                    data['ref_counter_offset'],
                    base64.b64decode(data['event_handle']) if data['event_handle'] else b'',
                    data['event_sync_required']
                )

                tensor = torch.tensor([], dtype=getattr(torch, data['dtype']), device=device)
                tensor.set_(storage, data['tensor_offset'], tuple(data['tensor_size']), tuple(data['tensor_stride']))
                
                exec_scope['tensor_out'] = tensor
                if actual_cuda_method == 'hybrid':
                    actual_cuda_method = 'native_ipc'
                
                sys.stderr.write(f'🔥 [TASK {task_id}] NATIVE IPC output (PyTorch 1.x)\\n')
                sys.stderr.flush()
            except Exception as e:
                import traceback
                sys.stderr.write(f'⚠️  [TASK {task_id}] Native IPC output failed: {e}\\n')
                sys.stderr.write(traceback.format_exc())
                sys.stderr.flush()

        # HYBRID PATH (SHM + GPU copy) OUTPUT
        if out_meta and 'tensor_out' not in exec_scope:
            if np is None:
                raise RuntimeError("NumPy is required for SHM outputs but is not available")
            shm_name = out_meta.get('shm_name') or out_meta.get('name')
            if not shm_name:
                raise RuntimeError(f"[TASK {task_id}] Output IPC failed and no SHM fallback available.")
            shm_out = shared_memory.SharedMemory(name=shm_name)
            shm_blocks.append(shm_out)
            
            arr_out = np.ndarray(
                tuple(out_meta['shape']),
                dtype=out_meta['dtype'],
                buffer=shm_out.buf
            )
            
            if is_cuda_request and _torch_available and _cuda_available:
                device = torch.device(f"cuda:{out_meta.get('device', 0)}")
                dtype_map = {'float32': torch.float32, 'float64': torch.float64}
                torch_dtype = dtype_map.get(out_meta['dtype'], torch.float32)
                exec_scope['tensor_out'] = torch.empty(
                    tuple(out_meta['shape']), 
                    dtype=torch_dtype,
                    device=device
                )
            else:
                exec_scope['tensor_out'] = arr_out
                exec_scope['arr_out'] = arr_out
        
        # ═══════════════════════════════════════════════════════════
        # EXECUTE USER CODE
        # ═══════════════════════════════════════════════════════════
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        
        # torch/np already in _worker_globals from startup — no-op reassign is fine
        if _torch_available:
            exec_scope['torch'] = torch
        exec_scope['np'] = np
        # Make current task's input_data visible (overwritten each call, not persisted)
        exec_scope['input_data'] = command
        
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(worker_code + '\\nworker_result = locals().get("result", None)', exec_scope, exec_scope)
            
            # Copy result back to SHM if hybrid mode
            if is_cuda_request and out_meta and 'tensor_out' in exec_scope and arr_out is not None:
                result_tensor = exec_scope['tensor_out']
                if hasattr(result_tensor, 'is_cuda') and result_tensor.is_cuda:
                    try:
                        arr_out[:] = result_tensor.cpu().numpy()
                        sys.stderr.write(f'✅ [TASK {task_id}] HYBRID: Copied GPU→SHM\\n')
                        sys.stderr.flush()
                    except Exception as e:
                        sys.stderr.write(f'⚠️  [TASK {task_id}] Copy-back failed: {e}\\n')
                        sys.stderr.flush()
            
            result = exec_scope.get("worker_result", {})
            if not isinstance(result, dict):
                result = {}
            
            result['task_id'] = task_id
            result['status'] = 'COMPLETED'
            result['success'] = True
            result['stdout'] = stdout_buffer.getvalue()
            result['stderr'] = stderr_buffer.getvalue()
            result['cuda_method'] = actual_cuda_method
            _original_stdout.write(json.dumps(result) + '\\n')
            _original_stdout.flush()
            
        except Exception as e:
            import traceback
            error_response = {
                'status': 'ERROR',
                'task_id': task_id,
                'error': f'{e.__class__.__name__}: {str(e)}',
                'traceback': traceback.format_exc(),
                'success': False
            }
            sys.stderr.write(f'❌ [DAEMON] Sending error for task {task_id}: {str(e)}\\n')
            sys.stderr.flush()
            # CRITICAL FIX: Write to _original_stdout
            _original_stdout.write(json.dumps(error_response) + '\\n')
            _original_stdout.flush()
        finally:
            for shm in shm_blocks:
                try:
                    shm.close()
                except:
                    pass
            # Remove per-task transient keys from the persistent globals dict.
            # This releases tensor/array references immediately so memory is freed,
            # while user-defined globals (_CACHED_MODEL etc.) remain untouched.
            for _transient_key in ('tensor_in', 'tensor_out', 'arr_in', 'arr_out',
                                   'input_data', 'worker_result'):
                _worker_globals.pop(_transient_key, None)
    except KeyboardInterrupt:
        break
    except Exception as e:
        import traceback
        error_response = {
            'status': 'ERROR',
            'task_id': 'UNKNOWN',
            'error': f'Command processing failed: {e}',
            'traceback': traceback.format_exc(),
            'success': False
        }
        sys.stderr.write(f'❌ [DAEMON] Command processing error: {str(e)}\\n')
        sys.stderr.flush()
        # CRITICAL FIX: Write to _original_stdout
        _original_stdout.write(json.dumps(error_response) + '\\n')
        _original_stdout.flush()

# Cleanup on exit
"""


# Additional diagnostic helper for debugging
def diagnose_worker_issue(package_spec: str):
    """
    Run this to diagnose why a worker might return the wrong version.
    """
    safe_print(_('\n🔍 Diagnosing worker issue for: {}').format(package_spec))
    print("=" * 70)

    pkg_name, expected_version = package_spec.split("==")

    # Check what's in sys.path
    print("\n1. Current sys.path:")

    for i, path in enumerate(sys.path):
        print(_('   [{}] {}').format(i, path))

    # Check what version is importable
    print(f"\n2. Attempting to import {pkg_name}:")
    try:
        from importlib.metadata import version

        actual_version = version(pkg_name)
        safe_print(_('   ✅ Found version: {}').format(actual_version))

        if actual_version != expected_version:
            safe_print("   ❌ VERSION MISMATCH!")
            print(_('      Expected: {}').format(expected_version))
            print(_('      Got: {}').format(actual_version))
    except Exception as e:
        safe_print(f"   ❌ Import failed: {e}")

    site_packages = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    bubble_path = site_packages / ".omnipkg_versions" / f"{pkg_name}-{expected_version}"

    print(_('\n3. Bubble check:'))
    print(_('   Path: {}').format(bubble_path))
    print(_('   Exists: {}').format(bubble_path.exists()))

    if bubble_path.exists():
        print(_('   Contents: {}').format(list(bubble_path.glob('*'))[:5]))

    print("\n" + "=" * 70)


# ═══════════════════════════════════════════════════════════════
# 2. WORKER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════


class PersistentWorker:
    def __init__(self, package_spec: str = None, python_exe: str = None, verbose: bool = False, defer_setup: bool = False, site_packages: str = None, multiversion_base: str = None):
        self.package_spec = package_spec
        # Normalize so that _normalize_exe(worker.python_exe) == pool_key always matches
        self.python_exe = _normalize_exe(python_exe or _venv_python_exe())
        self.site_packages = site_packages
        self.multiversion_base = multiversion_base
        self.process: Optional[subprocess.Popen] = None
        self.temp_file: Optional[str] = None
        self.lock = threading.RLock()
        self.last_health_check = time.time()
        self.health_check_failures = 0
        self._last_io = None
        self._is_ready = False

        # Start the Python process immediately
        self._spawn_process()
        
        # If not idle, configure it immediately
        if not defer_setup and package_spec:
            self.assign_spec(package_spec)

    def execute(self, code: str) -> dict:
        """
        Backward compatibility wrapper for legacy execute calls.
        Maps the new SHM-based task system to the old dictionary format.
        """
        import uuid
        task_id = f"legacy_{uuid.uuid4().hex[:8]}"
        
        try:
            # Call the new internal execution logic
            # We use a generous timeout because PyTorch/Lightning loads can be slow
            response = self.execute_shm_task(
                task_id=task_id,
                code=code,
                shm_in={},
                shm_out={},
                timeout=300.0 
            )

            # Map the new response protocol to the legacy format
            if response.get("status") == "COMPLETED":
                # In the new worker, results are usually tucked in the 'result' key
                res_data = response.get("result", {})
                return {
                    "success": True, 
                    "stdout": res_data.get("stdout", ""), 
                    "locals": str(res_data.get("locals", []))
                }
            else:
                return {
                    "success": False, 
                    "error": response.get("message", "Execution failed")
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def shutdown(self):
        """Alias for force_shutdown to support legacy cleanup calls."""
        self.force_shutdown()

    def _spawn_process(self):
        """Starts the raw Python process. Sits waiting for JSON spec."""
        # Ensure our dedicated temp dir exists
        os.makedirs(OMNIPKG_TEMP_DIR, exist_ok=True)
        
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, suffix="_idle.py", dir=OMNIPKG_TEMP_DIR
        ) as f:
            f.write(_DAEMON_SCRIPT)
            self.temp_file = f.name

        # 🔥 CRITICAL: Sanitize environment for the worker
        # We MUST NOT inherit PYTHONPATH from the daemon's environment (usually 3.11).
        env = os.environ.copy()
        
        # Scrub variables that cause cross-version contamination
        for var in ["PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "OMNIPKG_IS_DAEMON"]:
            env.pop(var, None)

        # Inject the correct multiversion base so omnipkgLoader knows exactly where bubbles are
        if self.multiversion_base:
            env["OMNIPKG_MULTIVERSION_BASE"] = self.multiversion_base
        if self.site_packages:
            env["OMNIPKG_SITE_PACKAGES"] = self.site_packages

        # 🔑 ENSURE per-interpreter config file exists next to this interpreter.
        # Without this, the worker falls back to global config (wrong multiversion_base).
        # _ensure_worker_config writes a lightweight JSON that ConfigManager finds first.
        if self.site_packages and self.python_exe:
            _ensure_worker_config(self.python_exe, self.site_packages, self.multiversion_base)

        # SMART PYTHONPATH INJECTION:
        # Only inject PYTHONPATH if we are running from source (Dev Mode).
        # If we are running from an installed package, we assume the target interpreter
        # also has omnipkg installed (via 'adopt'). Injecting the daemon's site-packages
        # into a different Python version is dangerous (binary incompatibility).
        try:
            import omnipkg
            omnipkg_path = Path(omnipkg.__file__).resolve()
            
            # Check if we are in a site-packages directory
            is_installed = "site-packages" in str(omnipkg_path) or "dist-packages" in str(omnipkg_path)
            
            if not is_installed:
                # Dev mode: Inject source root
                pkg_root = str(omnipkg_path.parent.parent)
                env["PYTHONPATH"] = pkg_root
            else:
                # Installed mode: Do NOT inject PYTHONPATH. Rely on target env.
                # This prevents cross-contamination between Python versions (e.g. 3.11 -> 3.9)
                pass
                
        except Exception:
            # Fallback
            pass

        self.log_file = open(DAEMON_LOG_FILE, "a", encoding="utf-8", buffering=1)

        # Initialize output queue for thread-safe reading on Windows
        import queue
        self.stdout_queue = queue.Queue()

        # Windows: Add CREATE_NO_WINDOW flag to prevent console window and I/O deadlock
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.process = subprocess.Popen(
            [self.python_exe, "-u", self.temp_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log_file,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=0,
            env=env,
            preexec_fn=os.setsid if not IS_WINDOWS else None,
            creationflags=creationflags,
        )

        # Start a single persistent reader thread to prevent deadlocks on Windows
        def _reader_thread():
            while self.process and self.process.stdout:
                try:
                    line = self.process.stdout.readline()
                    if line:
                        self.stdout_queue.put(line)
                    else:
                        # Process stdout has been closed, exit thread
                        break
                except (ValueError, OSError):
                    # Pipe closed or other I/O error
                    break
        
        self._io_thread = threading.Thread(target=_reader_thread, daemon=True)
        self._io_thread.start()

    def assign_spec(self, package_spec: str):
        """Converts an IDLE worker into a specific package worker."""
        if self._is_ready: return
        
        lock_path = os.path.join(OMNIPKG_TEMP_DIR, f"worker_init_{id(self)}.lock")
        lock = filelock.FileLock(lock_path, timeout=10)
        
        with lock:
            self.package_spec = package_spec
            try:
                # 1. Send the configuration to the waiting process
                setup_cmd = json.dumps({"package_spec": self.package_spec})
                self.process.stdin.write(setup_cmd + "\n")
                self.process.stdin.flush()
                
                # 2. Wait for READY with ACTIVITY MONITORING (not blind timeout)
                # 2. Wait for READY with ACTIVITY MONITORING (not blind timeout)
                # INCREASED TIMEOUT: Package installation (bubbling) can take minutes.
                # We use a long timeout, but the activity monitor will keep it alive
                # as long as it's actually doing work (installing/compiling).
                timeout = 600.0 
                
                safe_print(f"   ⏳ [DAEMON] Configuring worker for '{self.package_spec}' (Timeout: {timeout}s)...", file=sys.stderr)
                
                ready_line = self.wait_for_ready_with_activity_monitoring(
                    self.process, 
                    timeout_idle_seconds=timeout
                )
                
                if ready_line and json.loads(ready_line.strip()).get("status") == "READY":
                    self.last_health_check = time.time()
                    self._is_ready = True
                    safe_print(_('   ✅ [DAEMON] Worker ready: {}').format(self.package_spec), file=sys.stderr)
                    return
                    
                raise RuntimeError("Worker failed to send READY status.")
                    
            except Exception as e:
                self.force_shutdown()
                raise RuntimeError(_('Worker spec assignment failed: {}').format(e))
                
    def wait_for_ready_with_activity_monitoring(self, process, timeout_idle_seconds=300.0):
        """Wait for worker READY signal with REAL activity monitoring (CPU/Mem/IO)."""
        try:
            import psutil
            ps_process = psutil.Process(process.pid)
            has_psutil = True
        except ImportError:
            has_psutil = False

        start_time = time.time()
        last_activity_time = start_time
        
        # Initial resource baselines
        last_mem = 0.0
        if has_psutil:
            try:
                last_mem = ps_process.memory_info().rss
            except: pass

        while True:
            # Check if process crashed
            if process.poll() is not None:
                raise RuntimeError('Worker crashed during startup')

            # Try to read READY signal from Queue (Platform Independent)
            ready_line = None
            try:
                # Non-blocking check since we loop anyway
                import queue
                ready_line = self.stdout_queue.get_nowait()
            except queue.Empty:
                pass

            if ready_line:
                safe_print(f"✅ [DAEMON] Got READY signal: {ready_line[:100]}", file=sys.stderr)
                return ready_line

            # Activity Monitoring
            now = time.time()
            is_active = False
            
            if has_psutil:
                try:
                    # Check CPU (interval=0.0 is non-blocking)
                    cpu = ps_process.cpu_percent(interval=0.0)
                    if cpu > 0.1: is_active = True
                    
                    # Check Memory change
                    mem = ps_process.memory_info().rss
                    if abs(mem - last_mem) > 1024 * 1024: # 1MB change
                        is_active = True
                        last_mem = mem
                        
                    # Check IO (if available)
                    try:
                        io = ps_process.io_counters()
                        if not hasattr(self, '_last_io_startup'): self._last_io_startup = io
                        if io.read_bytes != self._last_io_startup.read_bytes or io.write_bytes != self._last_io_startup.write_bytes:
                            is_active = True
                            self._last_io_startup = io
                    except: pass
                except:
                    pass
            
            if is_active:
                last_activity_time = now
            
            # Timeout check
            if has_psutil:
                # If we have psutil, we timeout on IDLE time (no activity)
                if now - last_activity_time > timeout_idle_seconds:
                     raise RuntimeError(f"Worker startup timed out (Idle for {timeout_idle_seconds}s)")
                # Hard limit: 3x idle timeout (e.g. 30 mins) to prevent infinite loops even with activity
                if now - start_time > timeout_idle_seconds * 3:
                     raise RuntimeError(f"Worker startup exceeded hard limit ({timeout_idle_seconds*3}s)")
            else:
                # Without psutil, treat timeout_idle_seconds as total timeout
                if now - start_time > timeout_idle_seconds:
                    raise RuntimeError(f"Worker timeout after {timeout_idle_seconds}s")

            time.sleep(0.1)

    def execute_with_activity_monitoring(
        self,
        worker_process,
        task_id,
        code,
        shm_in,
        shm_out,
        timeout_idle_seconds=30.0,
        max_total_time=600.0,
    ):
        """
        Execute task while monitoring worker activity.
        Only timeout if worker is idle, not if it's actively working.

        Args:
            worker_process: The worker subprocess
            task_id: Unique task identifier
            code: Code to execute
            shm_in/shm_out: Shared memory metadata
            timeout_idle_seconds: Timeout if no CPU/memory activity
            max_total_time: Absolute maximum time (safety limit)

        Returns:
            Response dict from worker
        """
        import psutil

        try:
            ps_process = psutil.Process(worker_process.pid)
        except psutil.NoSuchProcess:
            raise RuntimeError("Worker process not running")

        # Send command
        command = {
            "type": "execute",
            "task_id": task_id,
            "code": code,
            "shm_in": shm_in,
            "shm_out": shm_out,
        }

        worker_process.stdin.write(json.dumps(command) + "\n")
        worker_process.stdin.flush()

        # Monitor execution
        start_time = time.time()
        last_activity_time = start_time
        last_cpu_percent = 0.0
        last_memory_mb = ps_process.memory_info().rss / 1024 / 1024

        while True:
            # Check absolute timeout
            if time.time() - start_time > max_total_time:
                raise TimeoutError(f"Task exceeded maximum time limit ({max_total_time}s)")

            # CRITICAL: Must use stdout_queue, NOT process.stdout directly.
            # The _reader_thread in _spawn_process() continuously drains
            # process.stdout into stdout_queue. Reading process.stdout directly
            # races with that thread and leaves stale responses in the queue,
            # permanently desyncing the request/response protocol.
            # CRITICAL: Must use stdout_queue, NOT process.stdout directly.
            # The _reader_thread in _spawn_process() continuously drains
            # process.stdout into stdout_queue. Reading process.stdout directly
            # races with that thread and leaves stale responses in the queue,
            # permanently desyncing the request/response protocol.
            # CRITICAL: Must use stdout_queue, NOT process.stdout directly.
            # The _reader_thread in _spawn_process() continuously drains
            # process.stdout into stdout_queue. Reading process.stdout directly
            # races with that thread and leaves stale responses in the queue,
            # permanently desyncing the request/response protocol.
            import queue as _queue
            try:
                safe_print(f"⏱️  [DAEMON] Waiting for response from queue...", file=sys.stderr)
                t = time.perf_counter()
                response_line = self.stdout_queue.get(timeout=60.0)
                safe_print(f"⏱️  [DAEMON] Got response in {(time.perf_counter()-t)*1000:.1f}ms", file=sys.stderr)
            except _queue.Empty:
                response_line = None

            if not response_line:
                raise TimeoutError("Task timed out after 60s")

            return json.loads(response_line.strip())

            # Monitor activity
            try:
                cpu_percent = ps_process.cpu_percent(interval=0.1)
                memory_mb = ps_process.memory_info().rss / 1024 / 1024

                # Activity detection
                activity_detected = False

                if cpu_percent > 1.0:  # CPU active
                    activity_detected = True

                if abs(memory_mb - last_memory_mb) > 1.0:  # Memory changing
                    activity_detected = True

                # Check I/O activity (reading/writing data)
                io_counters = ps_process.io_counters()
                if hasattr(self, "_last_io"):
                    last_io = self._last_io
                    if (
                        io_counters.read_bytes > last_io.read_bytes
                        or io_counters.write_bytes > last_io.write_bytes
                    ):
                        activity_detected = True
                self._last_io = io_counters

                if activity_detected:
                    last_activity_time = time.time()
                    last_cpu_percent = cpu_percent
                    last_memory_mb = memory_mb

                # Check idle timeout
                idle_duration = time.time() - last_activity_time

                if idle_duration > timeout_idle_seconds:
                    raise TimeoutError(
                        f"Task timed out: No activity for {idle_duration:.1f}s\n"
                        f"Last CPU: {last_cpu_percent:.1f}%, Memory: {memory_mb:.1f}MB\n"
                        f"Task may be deadlocked or waiting indefinitely"
                    )

            except psutil.NoSuchProcess:
                raise RuntimeError("Worker process crashed during task execution")

            time.sleep(0.1)

    def _discover_cuda_paths(self) -> List[str]:
        """
        Discover CUDA library paths for this package spec.
        Dynamically detects CUDA version requirement (cu11 vs cu12).
        """
        cuda_paths = []

        # 1. Detect required CUDA version from spec
        # e.g. "torch==2.0.0+cu118" -> target="11"
        target_cuda = "12"  # Default to modern
        if "+cu11" in self.package_spec or "cu11" in self.package_spec:
            target_cuda = "11"
        elif "+cu12" in self.package_spec or "cu12" in self.package_spec:
            target_cuda = "12"

        # Parse package name
        pkg_name = (
            self.package_spec.split("==")[0] if "==" in self.package_spec else self.package_spec
        )

        # Get the multiversion base
        try:
            # Import here to avoid circular dependency
            from omnipkg.loader import omnipkgLoader

            loader = omnipkgLoader(package_spec=self.package_spec, quiet=False)
            multiversion_base = loader.multiversion_base
        except Exception:
            import site

            site_packages = Path(site.getsitepackages()[0])
            multiversion_base = site_packages / ".omnipkg_versions"

        if not multiversion_base.exists():
            return cuda_paths

        # Strategy 1: Check main bubble
        unused, version = (
            self.package_spec.split("==") if "==" in self.package_spec else (pkg_name, None)
        )
        if version:
            main_bubble = multiversion_base / f"{pkg_name}-{version}"
            if main_bubble.exists():
                for nvidia_dir in main_bubble.glob("nvidia_*"):
                    if nvidia_dir.is_dir():
                        lib_dir = nvidia_dir / "lib"
                        if lib_dir.exists():
                            cuda_paths.append(str(lib_dir))
                        if list(nvidia_dir.glob("*.so*")):
                            cuda_paths.append(str(nvidia_dir))

        # Strategy 2: Check standalone NVIDIA bubbles using TARGET VERSION
        # We only look for the version requested in the spec
        nvidia_bubble_patterns = [
            f"nvidia-cuda-runtime-cu{target_cuda}-*",
            f"nvidia-cudnn-cu{target_cuda}-*",
            f"nvidia-cublas-cu{target_cuda}-*",
            f"nvidia-cufft-cu{target_cuda}-*",
            f"nvidia-cusolver-cu{target_cuda}-*",
            f"nvidia-cusparse-cu{target_cuda}-*",
            f"nvidia-nccl-cu{target_cuda}-*",
            f"nvidia-nvtx-cu{target_cuda}-*",
        ]

        for pattern in nvidia_bubble_patterns:
            for nvidia_bubble in multiversion_base.glob(pattern):
                if nvidia_bubble.is_dir() and "_omnipkg_cloaked" not in nvidia_bubble.name:
                    pkg_dir_name = nvidia_bubble.name.split("-")[0:3]
                    pkg_dir_name = "_".join(pkg_dir_name)

                    pkg_dir = nvidia_bubble / pkg_dir_name
                    if pkg_dir.exists():
                        lib_dir = pkg_dir / "lib"
                        if lib_dir.exists():
                            cuda_paths.append(str(lib_dir))
                        if list(pkg_dir.glob("*.so*")):
                            cuda_paths.append(str(pkg_dir))

        return cuda_paths

    def _start_worker(self):
        """Start worker process with proper error handling."""
        # CRITICAL DEBUG: Check _DAEMON_SCRIPT before writing
        safe_print(
            _('\n🔍 DEBUG: _DAEMON_SCRIPT length: {} chars').format(len(_DAEMON_SCRIPT)),
            file=sys.stderr,
        )
        safe_print("🔍 DEBUG: Last 200 chars of _DAEMON_SCRIPT:", file=sys.stderr)
        print(_("   '{}'").format(_DAEMON_SCRIPT[-200:]), file=sys.stderr)

        # Create temp script file
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            suffix=f"_{self.package_spec.replace('=', '_').replace('==', '_')}.py",
        ) as f:
            f.write(_DAEMON_SCRIPT)
            self.temp_file = f.name

        # CRITICAL DEBUG: Print the temp file path and validate syntax
        safe_print(_('\n🔍 DEBUG: Worker script written to: {}').format(self.temp_file), file=sys.stderr)
        safe_print(
            _('🔍 DEBUG: File size: {} bytes').format(os.path.getsize(self.temp_file)),
            file=sys.stderr,
        )

        # Validate syntax before running
        try:
            with open(self.temp_file, "r", encoding="utf-8") as f:
                script_content = f.read()
            compile(script_content, self.temp_file, "exec")
            safe_print("✅ DEBUG: Script syntax is valid", file=sys.stderr)
        except SyntaxError as e:
            safe_print("\n💥 SYNTAX ERROR IN GENERATED SCRIPT!", file=sys.stderr)
            print(_('   File: {}').format(self.temp_file), file=sys.stderr)
            print(_('   Line {}: {}').format(e.lineno, e.msg), file=sys.stderr)
            safe_print("\n📄 SCRIPT CONTENT (last 50 lines):", file=sys.stderr)
            with open(self.temp_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                start_line = max(0, len(lines) - 50)
                for i, line in enumerate(lines[start_line:], start=start_line + 1):
                    marker = " ⚠️ " if i == e.lineno else "    "
                    print(f"{marker}{i:3d}: {line.rstrip()}", file=sys.stderr)
            raise RuntimeError(f"Generated script has syntax error at line {e.lineno}: {e.msg}")

        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{os.getcwd()}{os.pathsep}{current_pythonpath}"

        # 🔥 FIX: Open daemon log for worker stderr (store as instance variable)
        self.log_file = open(DAEMON_LOG_FILE, "a", encoding="utf-8", buffering=1)  # Line buffering

        # ═══════════════════════════════════════════════════════════
        # 🔥 NEW: INJECT CUDA LIBRARY PATHS BEFORE SPAWN
        # ═══════════════════════════════════════════════════════════
        cuda_lib_paths = self._discover_cuda_paths()
        if cuda_lib_paths:
            current_ld = env.get("LD_LIBRARY_PATH", "")
            new_ld = os.pathsep.join(cuda_lib_paths)
            if current_ld:
                new_ld = new_ld + os.pathsep + current_ld
            env["LD_LIBRARY_PATH"] = new_ld

            safe_print(
                _('🔧 [WORKER] Injecting {} CUDA paths into environment').format(len(cuda_lib_paths)),
                file=sys.stderr,
            )
            for path in cuda_lib_paths:
                print(_('   - {}').format(path), file=sys.stderr)

        # Open daemon log for worker stderr (store as instance variable)
        self.log_file = open(DAEMON_LOG_FILE, "a", encoding="utf-8", buffering=1)

        # Windows: Add CREATE_NO_WINDOW flag to prevent console window and I/O deadlock
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.process = subprocess.Popen(
            [self.python_exe, "-u", self.temp_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log_file,  # ✅ Log to file instead of /dev/null
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=0,
            env=env,
            preexec_fn=os.setsid if not IS_WINDOWS else None,  # 🔥 Windows fix
            creationflags=creationflags,
        )

        # Send setup command
        try:
            setup_cmd = json.dumps({"package_spec": self.package_spec})
            self.process.stdin.write(setup_cmd + "\n")
            self.process.stdin.flush()
        except Exception as e:
            self.force_shutdown()
            raise RuntimeError(_('Failed to send setup: {}').format(e))

        # Wait for READY with timeout
        try:
            # Windows-compatible waiting for READY
            ready_line = None
            if IS_WINDOWS:
                result = [None]
                def try_read():
                    try: result[0] = self.process.stdout.readline()
                    except: pass
                t = threading.Thread(target=try_read, daemon=True)
                t.start()
                t.join(timeout=30.0)
                ready_line = result[0]
            else:
                readable, unused, unused = select.select([self.process.stdout], [], [], 30.0)
                if readable:
                    ready_line = self.process.stdout.readline()

            if not ready_line:
                # Check if process died
                if self.process.poll() is not None:
                    raise RuntimeError(f"Worker crashed during startup (check {DAEMON_LOG_FILE})")
                raise RuntimeError("Worker timeout waiting for READY")

            ready_line = ready_line.strip()

            if not ready_line:
                raise RuntimeError("Worker sent blank READY line")

            try:
                ready_status = json.loads(ready_line)
            except json.JSONDecodeError as e:
                raise RuntimeError(_('Worker sent invalid READY JSON: {}: {}').format(repr(ready_line), e))

            if ready_status.get("status") != "READY":
                raise RuntimeError(_('Worker failed to initialize: {}').format(ready_status))

            # Success!
            self.last_health_check = time.time()
            self.health_check_failures = 0

        except Exception as e:
            self.force_shutdown()
            raise RuntimeError(_('Worker initialization failed: {}').format(e))

    def execute_shm_task(
        self,
        task_id: str,
        code: str,
        shm_in: Dict[str, Any],
        shm_out: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Execute task with timeout."""
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise Exception("Worker not running.")

            try:
                command = {
                    "type": "execute",
                    "task_id": task_id,
                    "code": code,
                    "shm_in": shm_in,
                    "shm_out": shm_out,
                }

                safe_print(f"🔍 [DAEMON] Sending task {task_id} to worker (package_spec={self.package_spec})", file=sys.stderr)
                safe_print(f"🔍 [DAEMON] Command: {json.dumps(command)[:200]}...", file=sys.stderr)
                
                self.process.stdin.write(json.dumps(command) + "\n")
                self.process.stdin.flush()
                
                safe_print(f"🔍 [DAEMON] Task sent, waiting for response (timeout={timeout}s)...", file=sys.stderr)

                # Read from Queue with timeout
                import queue
                try:
                    # Drain any stale output before waiting for new response
                    # (optional, but helps keep protocol sync)
                    # while not self.stdout_queue.empty():
                    #     self.stdout_queue.get_nowait()

                    response_line = self.stdout_queue.get(timeout=timeout)
                except queue.Empty:
                    response_line = None

                if not response_line:
                    safe_print(f"❌ [DAEMON] No response received within {timeout}s", file=sys.stderr)
                    safe_print(f"❌ [DAEMON] Worker process poll status: {self.process.poll()}", file=sys.stderr)
                    raise TimeoutError(f"Task timed out after {timeout}s")

                safe_print(f"✅ [DAEMON] Got response: {response_line[:200]}...", file=sys.stderr)
                parsed = json.loads(response_line.strip())
                safe_print(f"✅ [DAEMON] Parsed response status: {parsed.get('status')}", file=sys.stderr)
                return parsed

            except Exception as e:
                safe_print(f"❌ [DAEMON] execute_shm_task exception: {e}", file=sys.stderr)
                import traceback
                safe_print(traceback.format_exc(), file=sys.stderr)
                self.health_check_failures += 1
                raise

    def health_check(self) -> bool:
        """Check if worker is responsive. Skips check if worker is mid-task."""
        if getattr(self, '_is_executing', False):
            # Worker is busy — count as healthy, don't interrupt it
            self.last_health_check = time.time()
            return True
        try:
            result = self.execute_shm_task(
                "health_check", "result = {'status': 'ok'}", {}, {}, timeout=15.0
            )
            self.last_health_check = time.time()
            self.health_check_failures = 0
            return result.get("status") == "COMPLETED"
        except Exception:
            self.health_check_failures += 1
            return False

    def force_shutdown(self):
        """Forcefully shutdown worker."""
        with self.lock:
            if self.process:
                try:
                    self.process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                    self.process.stdin.flush()
                    self.process.wait(timeout=2)
                except Exception:
                    try:
                        if not IS_WINDOWS:
                            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                        else:
                            self.process.terminate()
                    except Exception:
                        pass
                finally:
                    self.process = None

            # 🔥 FIX: Close log file handle
            if hasattr(self, "log_file") and self.log_file:
                try:
                    self.log_file.close()
                except Exception:
                    pass

            if self.temp_file and os.path.exists(self.temp_file):
                try:
                    os.unlink(self.temp_file)
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════
# 3. DAEMON MANAGER
# ═══════════════════════════════════════════════════════════════


import queue

class WorkerPoolDaemon:
    def __init__(self, max_workers: int = 10, max_idle_time: int = 300, warmup_specs: list = None):
        self.max_workers = max_workers
        self.max_idle_time = max_idle_time
        self.warmup_specs = warmup_specs or []
        
        # Use ConfigManager for path resolution
        try:
            from omnipkg.core import ConfigManager
            self.cm = ConfigManager(suppress_init_messages=True)
        except ImportError:
            self.cm = None

        self.workers: Dict[str, Dict[str, Any]] = {}
        self.worker_locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)
        self.pool_lock = threading.RLock()
        
        # 🚀 IDLE WORKER POOL: Keep bare Python processes ready per executable
        # Key: python_exe path (ALWAYS normalized via _normalize_exe), Value: Queue of idle workers
        self.idle_pools: Dict[str, queue.Queue] = defaultdict(lambda: queue.Queue(maxsize=10))
        # Default configuration: 3 idle workers for the daemon's own python.
        # Use _venv_python_exe() not sys.executable — on macOS the daemon is forked
        # via a venv symlink so sys.executable resolves to the framework Python,
        # causing workers to spawn without venv context and crash on omnipkg import.
        _own_exe = _normalize_exe(_venv_python_exe())
        self.idle_config: Dict[str, int] = {_own_exe: 3}

        # 🚀 AUTO-DISCOVERY: Find other managed interpreters and keep 1 idle for them
        if self.cm:
            try:
                registry_path = self.cm.venv_path / ".omnipkg" / "interpreters" / "registry.json"
                if registry_path.exists():
                    with open(registry_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for version, path in data.get("interpreters", {}).items():
                            # Always normalize path using consistent helper
                            norm_path = _normalize_exe(path)
                            if norm_path != _own_exe:
                                # Keep 1 idle worker for other versions to ensure fast swapping
                                self.idle_config[norm_path] = 1
            except Exception:
                # Non-fatal, just won't have warm workers for others
                pass

        self.worker_locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)
        self.running = True
        self.socket_path = DEFAULT_SOCKET
        
        self.running = True
        self.socket_path = DEFAULT_SOCKET
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "workers_created": 0,
            "workers_killed": 0,
            "errors": 0,
        }
        self.executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="daemon-handler")

    def _cleanup_stale_temp_files(self):
        """Clean up old temporary files from previous runs."""
        import shutil
        safe_print("   🧹 [DAEMON] Cleaning up stale temporary files...", file=sys.stderr)
        script_count = 0
        pip_dir_count = 0
        
        try:
            # --- Worker & Swap Script Cleanup ---
            # Clean up our dedicated temp directory for worker scripts
            if os.path.exists(OMNIPKG_TEMP_DIR):
                for f in glob.glob(os.path.join(OMNIPKG_TEMP_DIR, "tmp*_idle.py")):
                    try:
                        os.unlink(f)
                        script_count += 1
                    except OSError:
                        pass
            
            # Clean up swap cleanup scripts from system temp
            for f in glob.glob(os.path.join(tempfile.gettempdir(), "tmp*.sh")):
                try:
                    os.unlink(f)
                    script_count += 1
                except OSError:
                    pass

            if script_count > 0:
                safe_print(_('   🗑️  [DAEMON] Removed {} stale temp script(s).').format(script_count), file=sys.stderr)

            # --- Pip Temporary Directory Cleanup (CRITICAL for disk space) ---
            temp_dir = tempfile.gettempdir()
            for pattern in ["pip-unpack-*", "pip-target-*"]:
                for path in glob.glob(os.path.join(temp_dir, pattern)):
                    try:
                        # Only remove if it's old (e.g., > 1 hour) to avoid race conditions
                        if time.time() - os.path.getmtime(path) > 3600:
                            if os.path.isdir(path):
                                shutil.rmtree(path, ignore_errors=True)
                                pip_dir_count += 1
                            elif os.path.isfile(path):
                                os.unlink(path)
                                pip_dir_count += 1
                    except Exception:
                        pass
            
            if pip_dir_count > 0:
                 safe_print(_('   🗑️  [DAEMON] Removed {} stale pip temporary directory/file(s).').format(pip_dir_count), file=sys.stderr)

        except Exception as e:
            safe_print(_('   ⚠️  [DAEMON] Error during cleanup: {}').format(e), file=sys.stderr)

    def start(self, daemonize: bool = True, wait_for_ready: bool = False):
        """
        Starts the daemon, handling platform differences and waiting logic.
        """
        # 🔥 DEBUG
        os.makedirs(os.path.dirname(DAEMON_LOG_FILE), exist_ok=True)
        with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[DEBUG] WorkerPoolDaemon.start() called - daemonize={daemonize}, wait_for_ready={wait_for_ready}\n")
            f.write(f"[DEBUG] IS_WINDOWS={IS_WINDOWS}, is_running={self.is_running()}\n")
            f.flush()
        
        self._cleanup_stale_temp_files()
        
        if self.is_running():
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[DEBUG] Daemon already running, returning True\n")
                f.flush()
            return True

        if daemonize:
            if IS_WINDOWS:
                with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(f"[DEBUG] Calling _start_windows_daemon\n")
                    f.flush()
                # Windows spawner handles its own waiting/exiting logic.
                return self._start_windows_daemon(wait_for_ready=wait_for_ready)
            else:  # Unix/Linux/macOS
                try:
                    pid = os.fork()
                    if pid > 0:
                        # PARENT process: Waits for the child to be ready, then exits or returns.
                        return self._handle_parent_after_fork(pid, wait_for_ready)
                    
                    # CHILD process: Continues below to become the daemon.
                    self._daemonize() # Detaches from the terminal.
                
                except OSError as e:
                    safe_print(_('❌ Fork failed: {}').format(e), file=sys.stderr)
                    return False

        # This code is executed by:
        # 1. The final, detached grandchild process on Unix.
        # 2. A foreground process if daemonize=False.
        # It is NOT executed by the initial parent process that the user runs.
        self._initialize_daemon_process()
        self._run_socket_server()  # This is a blocking call that starts the server loop.

    def _replenish_idle_pool(self, python_exe: str):
        """
        Spawn ONE new idle worker for the given python_exe to replace one that was
        just converted to a pkg-spec worker. Called asynchronously so it never blocks
        the request path.
        """
        # Normalize so the idle_pools key matches what _execute_code uses
        python_exe = _normalize_exe(python_exe)

        def _do_spawn():
            target_paths = _resolve_target_paths(self.cm, python_exe)
            try:
                idle_worker = PersistentWorker(
                    package_spec=None,
                    python_exe=python_exe,
                    defer_setup=True,
                    site_packages=target_paths.get("site_packages_path"),
                    multiversion_base=target_paths.get("multiversion_base"),
                )
                pool = self.idle_pools[python_exe]
                try:
                    pool.put_nowait(idle_worker)
                    safe_print(
                        f"   💤 [DAEMON] Replenished idle worker for {python_exe} "
                        f"(Pool size: {pool.qsize()})",
                        file=sys.stderr,
                    )
                except queue.Full:
                    idle_worker.force_shutdown()
            except Exception as e:
                safe_print(
                    f"   ⚠️ [DAEMON] Failed to replenish idle worker for {python_exe}: {e}",
                    file=sys.stderr,
                )

        threading.Thread(target=_do_spawn, daemon=True, name=f"replenish-{python_exe[-20:]}").start()

    def _maintain_idle_pool(self):
        """🚀 Pre-spawns Python processes for specific executables so they are ready instantly."""
        while self.running:
            try:
                # Iterate over configured python executables and their target counts
                # Use list(items) to safely iterate while potentially modifying elsewhere
                for python_exe, target_count in list(self.idle_config.items()):
                    # python_exe is already normalized (stored via _normalize_exe),
                    # so idle_pools[python_exe] is the right key directly.
                    pool = self.idle_pools[python_exe]

                    if pool.qsize() < target_count:
                        # Resolve authoritative paths for this specific python version.
                        # _resolve_target_paths handles cm lookup + filesystem fallback.
                        target_paths = _resolve_target_paths(self.cm, python_exe)

                        # Spawn a generic idle worker pinned to this exact python executable.
                        # We pass the ORIGINAL (non-normalized) path to subprocess.Popen so
                        # the OS can actually find the binary, but we track it by normalized key.
                        idle_worker = PersistentWorker(
                            package_spec=None,
                            python_exe=python_exe,  # normalized is fine for Popen on Windows
                            defer_setup=True,
                            site_packages=target_paths.get("site_packages_path"),
                            multiversion_base=target_paths.get("multiversion_base")
                        )
                        try:
                            pool.put_nowait(idle_worker)
                            safe_print(f"   💤 [DAEMON] Spawned idle worker for {python_exe} (Pool size: {pool.qsize()})", file=sys.stderr)
                        except queue.Full:
                            # If pool is full (e.g. config changed downward), kill the extra worker
                            idle_worker.force_shutdown()
                        except Exception as e:
                            safe_print(f"   ⚠️ [DAEMON] Failed to spawn idle worker for {python_exe}: {e}", file=sys.stderr)
                            # Avoid tight loop on failure
                            time.sleep(1.0)
            except Exception:
                pass
            time.sleep(0.5)

    def set_idle_config(self, python_exe: str, count: int):
        """Runtime configuration of idle pools."""
        python_exe = _normalize_exe(python_exe)
        self.idle_config[python_exe] = count

    def _start_windows_daemon(self, wait_for_ready: bool = False):
        """Start daemon on Windows using subprocess."""
        daemon_script = os.path.abspath(__file__)
        
        os.makedirs(os.path.dirname(DAEMON_LOG_FILE), exist_ok=True)
        
        safe_print("🚀 Starting daemon in background (Windows mode)...", file=sys.stderr)
        
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000  # ADD THIS - prevents console popup
            
            # Keep log file handle open in parent process to prevent premature close
            log_file_handle = open(DAEMON_LOG_FILE, "a", encoding="utf-8", buffering=1)
            
            # 🔥 CRITICAL: Add OMNIPKG_DAEMON_CHILD to environment to prevent infinite spawning
            env = dict(os.environ, 
                      PYTHONUNBUFFERED="1",
                      OMNIPKG_DAEMON_CHILD="1")
            
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[DEBUG] Spawning subprocess NOW\n")
                f.write(f"[DEBUG] env['OMNIPKG_DAEMON_CHILD'] = {env.get('OMNIPKG_DAEMON_CHILD')}\n")
                f.flush()
            
            process = subprocess.Popen(
                [sys.executable, "-u", daemon_script, "start", "--no-fork"],  # ADD -u for unbuffered
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=log_file_handle,  # ALSO redirect stdout
                stderr=log_file_handle,
                close_fds=False,
                env=env
            )
            
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[DEBUG] Subprocess spawned, PID={process.pid}\n")
                f.flush()
            
            # DON'T close log_file_handle here - keep it alive
            # Store it so Python doesn't GC it
            self._daemon_log_handle = log_file_handle
            self._daemon_process_handle = process  # Keep reference
            
            if wait_for_ready:
                if self._wait_for_daemon_ready(timeout=15):  # Increase timeout
                    safe_print(f'✅ Daemon running (PID from file)', file=sys.stderr)
                    return True
                else:
                    safe_print(_('❌ Timeout (check {})').format(DAEMON_LOG_FILE), file=sys.stderr)
                    return False
            else:
                time.sleep(5)  # Give Windows more time
                if self.is_running():
                    safe_print('✅ Daemon started', file=sys.stderr)
                    return True
                else:
                    safe_print(_('❌ Failed (check {})').format(DAEMON_LOG_FILE), file=sys.stderr)
                    return False
        except Exception as e:
            safe_print(_('❌ Failed: {}').format(e), file=sys.stderr)
            return False if wait_for_ready else sys.exit(1)

    def _wait_for_daemon_ready(self, timeout: int = 10) -> bool:
        """Waits for the daemon's PID file to appear and be valid."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.is_running():
                return True
            time.sleep(0.2)
        return False

    def _handle_parent_after_fork(self, child_pid: int, wait_for_ready: bool) -> bool:
        """Logic for the parent process on Unix after forking."""
        if wait_for_ready:
            if self._wait_for_daemon_ready(timeout=10):
                safe_print(
                    _('✅ Daemon confirmed running. Resuming original command...'),
                    file=sys.stderr
                )
                return True
            else:
                safe_print(
                    _('❌ Daemon failed to start within timeout (check {})').format(DAEMON_LOG_FILE),
                    file=sys.stderr
                )
                return False
        else: # Fire-and-forget for standard daemon start
            safe_print(_('✅ Daemon process forked (PID: {})').format(child_pid))
            sys.exit(0)

    def _initialize_daemon_process(self):
        """Tasks performed by the final, detached daemon process before serving."""
        # This is the first thing the final daemon process should do to signal readiness.
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

        # Set signal handlers for graceful shutdown
        try:
            if threading.current_thread() is threading.main_thread():
                if not IS_WINDOWS:
                    signal.signal(signal.SIGTERM, self._handle_shutdown)
                    signal.signal(signal.SIGINT, self._handle_shutdown)
        except (ValueError, AttributeError):
            pass

        # Cleanup and start background maintenance threads
        shm_registry.cleanup_orphans()
        threading.Thread(target=self._health_monitor, daemon=True, name="health-monitor").start()
        threading.Thread(target=self._memory_manager, daemon=True, name="memory-manager").start()
        threading.Thread(target=self._warmup_workers, daemon=True, name="warmup").start()
        threading.Thread(target=self._maintain_idle_pool, daemon=True, name="idle-pool").start()

    def _daemonize(self):
        """Double-fork daemonization to fully detach the process."""
        # Decouple from parent environment after the first fork
        os.setsid()
        os.umask(0)

        # Second fork to prevent the process from acquiring a controlling terminal
        try:
            pid = os.fork()
            if pid > 0:
                # This is the intermediate process, which exits cleanly.
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(_('fork #2 failed: {}\n').format(e))
            sys.exit(1)

        # --- GRANDCHILD (FINAL DAEMON) PROCESS CONTINUES ---
        sys.stdout.flush()
        sys.stderr.flush()

        # Ensure log directory exists and redirect stdio
        try:
            os.makedirs(os.path.dirname(DAEMON_LOG_FILE), exist_ok=True)
        except OSError:
            pass

        with open("/dev/null", "r") as devnull:
            os.dup2(devnull.fileno(), sys.stdin.fileno())
        
        with open(DAEMON_LOG_FILE, "a+") as log_file:
            os.dup2(log_file.fileno(), sys.stdout.fileno())
            os.dup2(log_file.fileno(), sys.stderr.fileno())

    def _warmup_workers(self):
        """Pre-warm popular packages to reduce latency."""
        time.sleep(1)  # Let daemon settle
        for spec in self.warmup_specs:
            try:
                # We execute a simple "pass" to force the worker to spawn
                # This uses the same logic as a real request, so it triggers creation + import
                self._execute_code(spec, "pass", {}, {})
            except Exception:
                pass

    def _run_socket_server(self):
        """
        Fixed version that works on Windows (TCP) and Unix (domain socket)
        """
        
        # Platform detection
        is_windows = sys.platform == 'win32'
        
        if is_windows:
            # ============================================================
            # WINDOWS: Use TCP socket on localhost
            # ============================================================
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Get port from config (should already be in self)
            port = getattr(self, 'daemon_port', 5678)
            address = ('127.0.0.1', port)
            
            try:
                sock.bind(address)
                print(_('[DAEMON] Bound to TCP 127.0.0.1:{}').format(port), flush=True)
            except OSError as e:
                print(_('[DAEMON] Failed to bind to port {}: {}').format(port, e), flush=True)
                raise
            
            # Store connection info for clients to find us
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            conn_file.parent.mkdir(parents=True, exist_ok=True)
            conn_file.write_text(f"tcp://127.0.0.1:{port}")
            
        else:
            # ============================================================
            # UNIX/LINUX/MACOS: Use Unix domain socket
            # ============================================================
            # Remove stale socket file if it exists
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
            
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(self.socket_path)
            print(_('[DAEMON] Bound to Unix socket {}').format(self.socket_path), flush=True)
        
        # ============================================================
        # COMMON: Setup and main loop (same for both platforms)
        # ============================================================
        
        # CRITICAL FIX: Increased backlog for high concurrency
        sock.listen(128)
        
        print(_('[DAEMON] Server ready, entering accept loop'), flush=True)
        
        while self.running:
            try:
                sock.settimeout(1.0)
                conn, unused = sock.accept()
                
                # CRITICAL FIX: Use thread pool instead of unbounded threads
                self.executor.submit(self._handle_client, conn)
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    # Only log if we're not shutting down
                    print(_('[DAEMON] Accept error: {}').format(e), flush=True)
        
        # Cleanup
        sock.close()
        print(_('[DAEMON] Socket closed'), flush=True)
        
        if is_windows and conn_file.exists():
            conn_file.unlink()


    # ============================================================
    # CLIENT-SIDE: How to connect to the daemon
    # ============================================================

    def connect_to_daemon(socket_path=None, daemon_port=5678):
        """
        Client function to connect to daemon (works on both platforms)
        
        Args:
            socket_path: Unix socket path (ignored on Windows)
            daemon_port: TCP port for Windows (default 5678)
        
        Returns:
            Connected socket
        """
        is_windows = sys.platform == 'win32'
        
        if is_windows:
            # Read connection info from file
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            
            if conn_file.exists():
                conn_str = conn_file.read_text().strip()
                # Parse "tcp://127.0.0.1:5678"
                if conn_str.startswith('tcp://'):
                    host_port = conn_str[6:]  # Remove "tcp://"
                    host, port = host_port.split(':')
                    port = int(port)
                else:
                    # Fallback
                    host, port = '127.0.0.1', daemon_port
            else:
                # Use default
                host, port = '127.0.0.1', daemon_port
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            return sock
            
        else:
            # Unix socket
            if socket_path is None:
                socket_path = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon.sock'
            
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(socket_path))
            return sock


    # ============================================================
    # DAEMON STATUS CHECK: Update to work on both platforms
    # ============================================================

    def check_daemon_running(socket_path=None, daemon_port=5678):
        """
        Check if daemon is running by attempting connection
        Returns True if daemon responds, False otherwise
        """
        try:
            sock = connect_to_daemon(socket_path, daemon_port)
            sock.close()
            return True
        except Exception:
            return False

    def _handle_client(self, conn: socket.socket):
        """Handle client request with timeout protection."""
        conn.settimeout(30.0)
        try:
            # 1. Receive Request
            try:
                req = recv_json(conn, timeout=30.0)
            except (ConnectionResetError, BrokenPipeError, EOFError):
                # Client disconnected abruptly (common on Windows during teardown)
                # Just return silently to avoid log noise.
                return

            self.stats["total_requests"] += 1

            # 2. Process Logic
            if req["type"] == "execute":
                res = self._execute_code(
                    req["spec"],
                    req["code"],
                    req.get("shm_in", {}),
                    req.get("shm_out", {}),
                    python_exe     = req.get("python_exe"),
                    worker_tag     = req.get("worker_tag"),      # NEW (optional)
                    max_memory_mb  = req.get("max_memory_mb"),   # NEW (optional)
                )
            elif req["type"] == "execute_cuda":
                res = self._execute_cuda_code(
                    req["spec"],
                    req["code"],
                    req.get("cuda_in", {}),
                    req.get("cuda_out", {}),
                    python_exe     = req.get("python_exe"),
                    worker_tag     = req.get("worker_tag"),      # NEW (optional)
                    max_memory_mb  = req.get("max_memory_mb"),   # NEW (optional)
                )
            elif req["type"] == "status":
                res = self._get_status()
            elif req["type"] == "configure_idle":
                p_exe = req.get("python_exe", sys.executable)
                count = req.get("count", 3)
                self.set_idle_config(p_exe, count)
                res = {"success": True, "config": dict(self.idle_config)}
            elif req["type"] == "get_idle_config":
                res = {
                    "success": True,
                    "config": dict(self.idle_config)
                }
            elif req["type"] == "set_idle_config":
                python_exe = req.get("python_exe")
                count = req.get("count", 0)
                if python_exe:
                    self.set_idle_config(python_exe, count)
                    res = {"success": True}
                else:
                    res = {"success": False, "error": "python_exe required"}
            elif req["type"] == "shutdown":
                self.running = False
                res = {"success": True}
            else:
                res = {"success": False, "error": f"Unknown type: {req['type']}"}

            # 3. Send Response
            try:
                send_json(conn, res)
            except (ConnectionResetError, BrokenPipeError):
                # Client disconnected before we could send response - ignore
                pass

        except Exception as e:
            # 4. Handle Actual Logic Errors (Log these!)
            import traceback
            safe_print(f"❌ [DAEMON] _handle_client exception: {e}\n{traceback.format_exc()}", file=sys.stderr)
            try:
                send_json(conn, {"success": False, "error": str(e)})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _execute_code(
        self,
        spec: str,
        code: str,
        shm_in: dict,
        shm_out: dict,
        python_exe: str = None,
        worker_tag: str = None,
            pin: bool = False,          # NEW: worker survives idle timeout indefinitely
        max_memory_mb: float = None,
    ) -> dict:
        """
        Execute Python code inside a persistent isolated worker process.

        The daemon keeps one worker process alive per (spec, python, tag) triplet
        and routes subsequent calls to that same process — so model weights,
        loaded tokenizers, and any other globals stay warm across calls.

        Parameters
        ----------
        spec : str
            Package requirement, e.g. ``"torch==2.9.1"`` or ``"rich>=13"``.
            This is what pip resolves; it must not contain the tag suffix.
        code : str
            Python source to execute inside the worker.  The worker's globals
            persist between calls, so you can cache expensive objects::

                if "_model" not in globals():
                    globals()["_model"] = load_heavy_model()
                result = globals()["_model"].predict(arr_in)
                print(json.dumps(result))

        shm_in / shm_out : dict
            Shared-memory metadata for zero-copy array handoff.  Pass ``{}``
            for the JSON (small-data) path — execute_smart handles this for you.

        python_exe : str, optional
            Interpreter to use.  Accepts:
              - ``None``         → current interpreter (sys.executable)
              - Full path        → ``"/home/user/.omnipkg/.../python3.11"``
              - Short version    → ``"3.11"``, ``"py311"``, ``"python3.11"``
            The short-version form is resolved via the omnipkg interpreter registry
            (same versions listed by ``8pkg info python``).

        worker_tag : str, optional
            Opaque string that makes this call land on its own dedicated worker,
            separate from other calls with the same spec+python.

            Use this when you load large models and want process-level isolation::

                # nllb and seedx each get their own process → no RAM sharing
                client.execute_smart(spec="torch==2.9.1", worker_tag="nllb-600m", ...)
                client.execute_smart(spec="torch==2.9.1", worker_tag="seedx-7b",  ...)

            The tag is NEVER passed to pip; it only affects which worker bucket
            this call routes to.  If omitted, behaviour is identical to before.

        max_memory_mb : float, optional
            If set, the daemon will evict this worker (and restart it fresh on the
            next call) whenever its RSS exceeds this many megabytes.  Useful for
            guarding against accumulation when running many large models.
            Example: ``max_memory_mb=18_000`` for an ~18 GB model cap.
        """
        # ── Resolve interpreter (accepts short versions like "3.11") ──────────
        python_exe = _normalize_exe(_resolve_python_exe(python_exe))

        # ── Build worker key ───────────────────────────────────────────────────
        # The tag (if any) is appended to the key so this call gets its own
        # worker bucket, but the raw spec is kept clean for pip installs.
        key_spec   = f"{spec}#{worker_tag}" if worker_tag else spec
        worker_key = f"{key_spec}::{python_exe}"

        # ═══════════════════════════════════════════════════════════════
        # FAST PATH: Worker exists, execute immediately
        # ═══════════════════════════════════════════════════════════════
        with self.pool_lock:
            if worker_key in self.workers:
                self.stats["cache_hits"] += 1
                worker_info = self.workers[worker_key]

        if worker_key in self.workers:
            worker_info["last_used"] = time.time()
            worker_info["request_count"] += 1
            if pin and not worker_info.get("pinned"):   # upgrade to pinned, one-way
                worker_info["pinned"] = True

            try:
                result = worker_info["worker"].execute_shm_task(
                    f"{spec}-{self.stats['total_requests']}",
                    code,
                    shm_in,
                    shm_out,
                    timeout=60.0,
                )
                return result
            except Exception as e:
                return {"success": False, "error": str(e)}

        # ═══════════════════════════════════════════════════════════════
        # SLOW PATH: Create worker (loader handles all locking internally)
        # ═══════════════════════════════════════════════════════════════
        # Only prevents duplicate worker creation
        with self.worker_locks[worker_key]:
            # Double-check after acquiring lock
            with self.pool_lock:
                if worker_key in self.workers:
                    self.stats["cache_hits"] += 1
                    worker_info = self.workers[worker_key]

                    worker_info["last_used"] = time.time()
                    worker_info["request_count"] += 1
                    if pin and not worker_info.get("pinned"):   # upgrade to pinned, one-way
                        worker_info["pinned"] = True

                    try:
                        result = worker_info["worker"].execute_shm_task(
                            f"{spec}-{self.stats['total_requests']}",
                            code,
                            shm_in,
                            shm_out,
                            timeout=60.0,
                        )
                        return result
                    except Exception as e:
                        return {"success": False, "error": str(e)}

            # Check capacity
            with self.pool_lock:
                if len(self.workers) >= self.max_workers:
                    self._evict_oldest_worker_async()

            # Create worker - loader's __enter__ handles ALL the locking
            try:
                _took_from_idle = False
                try:
                    # Instant spawn (0ms) - MUST match requested python_exe
                    # Both idle_pools keys and python_exe are normalized via _normalize_exe,
                    # so a direct .get() is sufficient on all platforms including Windows.
                    pool = self.idle_pools.get(python_exe)
                    if pool:
                        worker = pool.get_nowait()
                        # Double-check: worker must use the exact normalized python we need.
                        # This guards against any race where a wrong-python idle sneaked in.
                        if _normalize_exe(worker.python_exe) != python_exe:
                            safe_print(
                                f"   ⚠️ [DAEMON] Idle worker python mismatch! "
                                f"Got {worker.python_exe}, wanted {python_exe}. Discarding.",
                                file=sys.stderr,
                            )
                            worker.force_shutdown()
                            raise queue.Empty  # fall through to fresh spawn
                        worker.assign_spec(spec)
                        _took_from_idle = True
                    else:
                        raise queue.Empty
                except queue.Empty:
                    _took_from_idle = False
                    target_paths = {}
                    if self.cm:
                        target_paths = _resolve_target_paths(self.cm, python_exe)

                    # Fallback if queue empty or no pool exists for this exe (~30ms)
                    # Note: PersistentWorker constructor calls assign_spec if spec is provided
                    worker = PersistentWorker(
                        spec,
                        python_exe=python_exe,
                        site_packages=target_paths.get("site_packages_path"),
                        multiversion_base=target_paths.get("multiversion_base")
                    )

                # Once an idle worker is taken and assigned a spec, spawn a replacement
                if _took_from_idle:
                    self._replenish_idle_pool(python_exe)

                with self.pool_lock:
                    self.workers[worker_key] = {
                        "worker": worker,
                        "created": time.time(),
                        "last_used": time.time(),
                        "request_count": 0,
                        "pinned": pin,
                        "memory_mb": 0.0,
                        # Optional RSS cap in MB; enforced by _memory_manager.
                        # None means no cap (default, same as before).
                        "max_memory_mb": max_memory_mb,
                        # Store the clean pip-installable spec separately so
                        # status / health-monitor code can show it without the tag.
                        "pip_spec": spec,
                    }
                    self.stats["workers_created"] += 1
                    worker_info = self.workers[worker_key]

            except Exception as e:
                import traceback

                error_msg = _('Worker creation failed: {}\n{}').format(e, traceback.format_exc())
                return {"success": False, "error": error_msg, "status": "ERROR"}

        # Execute (outside all locks)
        worker_info["last_used"] = time.time()
        worker_info["request_count"] += 1
        if pin and not worker_info.get("pinned"):   # upgrade to pinned, one-way
            worker_info["pinned"] = True

        try:
            # CRITICAL: Ensure worker is ready before executing
            # If it came from idle pool, assign_spec was called above.
            # If it was created new, constructor called assign_spec.
            # But we double check here to be safe.
            if not worker_info["worker"]._is_ready:
                 worker_info["worker"].assign_spec(spec)

            result = worker_info["worker"].execute_shm_task(
                f"{spec}-{self.stats['total_requests']}",
                code,
                shm_in,
                shm_out,
                timeout=60.0,
            )
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_install_lock_for_daemon(self, spec: str) -> filelock.FileLock:
        """
        Separate install lock (prevents duplicate installations).
        This is DIFFERENT from worker_locks (which protect worker creation).
        """
        lock_name = f"daemon-install-{spec.replace('==', '-')}"

        if not hasattr(self, "_install_locks"):
            self._install_locks = {}

        if lock_name not in self._install_locks:
            lock_file = Path(OMNIPKG_TEMP_DIR) / f"{lock_name}.lock"
            self._install_locks[lock_name] = filelock.FileLock(
                str(lock_file), timeout=300  # 5 minute max for installation
            )

        return self._install_locks[lock_name]

    def _install_bubble_for_worker(self, spec: str) -> bool:
        """
        Install a bubble directly (called by daemon during worker creation).
        Returns True if successful.
        """
        try:
            from omnipkg.core import ConfigManager
            from omnipkg.core import omnipkg as OmnipkgCore

            cm = ConfigManager(suppress_init_messages=True)
            core = OmnipkgCore(cm)

            original_strategy = core.config.get("install_strategy")
            core.config["install_strategy"] = "stable-main"

            try:
                result = core.smart_install([spec])
                return result == 0
            finally:
                if original_strategy:
                    core.config["install_strategy"] = original_strategy

        except Exception as e:
            safe_print(_('   ❌ [DAEMON] Installation failed: {}').format(e), file=sys.stderr)
            return False

    def _execute_cuda_code(
        self,
        spec: str,
        code: str,
        cuda_in: dict,
        cuda_out: dict,
        python_exe: str = None,
        worker_tag: str = None,
            pin: bool = False,          # NEW: worker survives idle timeout indefinitely
        max_memory_mb: float = None,
    ) -> dict:
        """Execute code with CUDA IPC tensors."""
        # ── Resolve interpreter (accepts short versions like "3.11") ──────────
        python_exe = _normalize_exe(_resolve_python_exe(python_exe))

        # ── Build worker key ───────────────────────────────────────────────────
        key_spec   = f"{spec}#{worker_tag}" if worker_tag else spec
        worker_key = f"{key_spec}::{python_exe}"
        worker_info = None

        # FAST PATH: Worker already exists for this spec
        with self.pool_lock:
            if worker_key in self.workers:
                self.stats["cache_hits"] += 1
                worker_info = self.workers[worker_key]

        if worker_info:
            worker_info["last_used"] = time.time()
            worker_info["request_count"] += 1
            if pin and not worker_info.get("pinned"):   # upgrade to pinned, one-way
                worker_info["pinned"] = True
        else:
            # SLOW PATH: No worker exists, need to create or assign one
            with self.worker_locks[worker_key]:
                # Double-check inside lock in case another thread created it
                with self.pool_lock:
                    if worker_key in self.workers:
                        worker_info = self.workers[worker_key]
                    
                if not worker_info:
                    # Evict an old worker if we are at capacity
                    with self.pool_lock:
                        if len(self.workers) >= self.max_workers:
                            self._evict_oldest_worker_async()
                    
                    # 🚀 ACQUIRE WORKER (IDLE POOL OR NEW)
                    try:
                        _took_from_idle = False
                        try:
                            # Instant spawn from idle pool (0ms) - MUST match requested python_exe.
                            # Both pool keys and python_exe are normalized via _normalize_exe.
                            pool = self.idle_pools.get(python_exe)
                            if pool:
                                worker = pool.get_nowait()
                                # Hard safety check: reject any worker that slipped through for wrong python
                                if _normalize_exe(worker.python_exe) != python_exe:
                                    safe_print(
                                        f"   ⚠️ [DAEMON] CUDA idle worker python mismatch! "
                                        f"Got {worker.python_exe}, wanted {python_exe}. Discarding.",
                                        file=sys.stderr,
                                    )
                                    worker.force_shutdown()
                                    raise queue.Empty
                                worker.assign_spec(spec)
                                _took_from_idle = True
                            else:
                                raise queue.Empty
                        except queue.Empty:
                            _took_from_idle = False
                            # Resolve authoritative paths for the target Python version
                            target_paths = {}
                            if self.cm:
                                target_paths = _resolve_target_paths(self.cm, python_exe)

                            # Fallback if idle pool is empty (~30ms)
                            worker = PersistentWorker(
                                spec,
                                python_exe=python_exe,
                                site_packages=target_paths.get("site_packages_path"),
                                multiversion_base=target_paths.get("multiversion_base")
                            )

                        if _took_from_idle:
                            self._replenish_idle_pool(python_exe)

                        # Add the newly assigned worker to the active pool
                        with self.pool_lock:
                            self.workers[worker_key] = {
                                "worker": worker,
                                "created": time.time(),
                                "last_used": time.time(),
                                "request_count": 0,
                                "pinned": pin,
                                "memory_mb": 0.0,
                                "is_gpu_worker": True,
                                "gpu_timeout": 60,
                                "max_memory_mb": max_memory_mb,
                                "pip_spec": spec,
                            }
                            self.stats["workers_created"] += 1
                            worker_info = self.workers[worker_key]

                    except Exception as e:
                        import traceback
                        error_msg = _('Worker creation failed: {}\n{}').format(e, traceback.format_exc())
                        return {"success": False, "error": error_msg, "status": "ERROR"}

        # EXECUTE TASK (on either existing or newly acquired worker)
        worker_info["last_used"] = time.time()
        worker_info["request_count"] += 1
        if pin and not worker_info.get("pinned"):   # upgrade to pinned, one-way
            worker_info["pinned"] = True

        try:
            command = {
                "type": "execute_cuda",
                "task_id": f"{spec}-{self.stats['total_requests']}",
                "code": code,
                "cuda_in": cuda_in,
                "cuda_out": cuda_out,
            }

            worker_info["worker"].process.stdin.write(json.dumps(command) + "\n")
            worker_info["worker"].process.stdin.flush()
            
            # CRITICAL FIX: Read from stdout_queue, NOT process.stdout directly.
            # _reader_thread is constantly draining process.stdout into stdout_queue.
            import queue
            try:
                response_line = worker_info["worker"].stdout_queue.get(timeout=60.0)
            except queue.Empty:
                response_line = None

            if not response_line:
                raise TimeoutError("CUDA task timed out after 60s")

            return json.loads(response_line.strip())
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _evict_oldest_worker_async(self):
        """CRITICAL FIX: Evict worker without blocking on shutdown."""
        with self.pool_lock:
            if not self.workers:
                return

            # Pinned workers are never chosen as eviction victims.
            candidates = {k: v for k, v in self.workers.items() if not v.get("pinned")}
            if not candidates:
                return   # all workers are pinned — nothing to evict

            oldest = min(candidates.keys(), key=lambda k: self.workers[k]["last_used"])
            worker_info = self.workers.pop(oldest)
            self.stats["workers_killed"] += 1

        # Shutdown in background thread (don't block)
        def async_shutdown():
            try:
                worker_info["worker"].force_shutdown()
            except Exception:
                pass

        threading.Thread(target=async_shutdown, daemon=True).start()

    def _health_monitor(self):
        """CRITICAL FIX: Actually test worker responsiveness."""
        while self.running:
            time.sleep(30)

            with self.pool_lock:
                specs_to_check = list(self.workers.keys())

            for spec in specs_to_check:
                with self.worker_locks[spec]:
                    with self.pool_lock:
                        if spec not in self.workers:
                            continue
                        worker_info = self.workers[spec]

                    # Check if process died
                    if worker_info["worker"].process.poll() is not None:
                        with self.pool_lock:
                            if spec in self.workers:
                                del self.workers[spec]
                        continue

                    # Perform health check
                    if not worker_info["worker"].health_check():
                        # 3 strikes and you're out
                        if worker_info["worker"].health_check_failures >= 3:
                            with self.pool_lock:
                                if spec in self.workers:
                                    del self.workers[spec]
                            worker_info["worker"].force_shutdown()

    def _memory_manager(self):
        """
        Background thread that enforces two levels of memory policy:

        1. Per-worker cap (``max_memory_mb`` set at call time):
           A specific worker is evicted the moment its RSS crosses the cap.
           The next call for that spec will start a fresh worker.  This is
           how you prevent a 7B-param model from accumulating 60 GB after
           many iterations — set ``max_memory_mb=20_000`` and the worker is
           recycled before it can balloon.

        2. System-wide emergency eviction (85% RAM used):
           Kill the oldest half of all workers indiscriminately.  Last resort.

        3. Idle-timeout eviction (always active, no psutil required):
           Workers idle longer than ``max_idle_time`` are reaped.
        """
        while self.running:
            time.sleep(60)
            now = time.time()

            try:
                import psutil

                # ── Level 1: per-worker RSS cap ───────────────────────────
                with self.pool_lock:
                    keys_to_check = list(self.workers.keys())

                for wkey in keys_to_check:
                    with self.pool_lock:
                        info = self.workers.get(wkey)
                        if info is None:
                            continue
                        cap = info.get("max_memory_mb")
                        pid = info["worker"].process.pid if info["worker"].process else None

                    if cap and pid:
                        try:
                            rss_mb = psutil.Process(pid).memory_info().rss / 1_048_576
                            with self.pool_lock:
                                if wkey in self.workers:
                                    self.workers[wkey]["memory_mb"] = rss_mb
                            if rss_mb > cap:
                                # Don't evict pinned workers even if over RSS cap — they'll be reused.
                                with self.pool_lock:
                                    if self.workers.get(wkey, {}).get("pinned"):
                                        continue
                                safe_print(
                                    f"   🧹[DAEMON] Worker '{wkey}' RSS {rss_mb:.0f} MB "
                                    f"> cap {cap:.0f} MB — evicting for next call",
                                    file=sys.stderr,
                                )
                                with self.pool_lock:
                                    evicted = self.workers.pop(wkey, None)
                                if evicted:
                                    self.stats["workers_killed"] += 1
                                    threading.Thread(
                                        target=evicted["worker"].force_shutdown, daemon=True
                                    ).start()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                # ── Level 2: system-wide emergency eviction ───────────────
                mem = psutil.virtual_memory()

                if mem.percent > 85:
                    # Aggressive eviction
                    with self.pool_lock:
                        to_kill = sorted(self.workers.items(), key=lambda x: x[1]["last_used"])[
                            : len(self.workers) // 2
                        ]

                        for spec, info in to_kill:
                            del self.workers[spec]
                            self.stats["workers_killed"] += 1
                            threading.Thread(
                                target=info["worker"].force_shutdown, daemon=True
                            ).start()
                    continue
            except ImportError:
                # psutil not available - use basic timeout-based eviction only
                pass

            # Normal idle timeout (always runs, even without psutil)
            with self.pool_lock:
                specs_to_remove = []
                for spec, info in self.workers.items():
                    if info.get("pinned"):
                        continue                          # NEW: never idle-timeout a pinned worker
                    timeout = info.get("gpu_timeout", self.max_idle_time)
                    if now - info["last_used"] > timeout:
                        specs_to_remove.append(spec)

                for spec in specs_to_remove:
                    info = self.workers.pop(spec)
                    self.stats["workers_killed"] += 1
                    threading.Thread(target=info["worker"].force_shutdown, daemon=True).start()

    def _get_status(self) -> dict:
        with self.pool_lock:
            worker_details = {}
            for k, v in self.workers.items():
                pid = v["worker"].process.pid if v["worker"].process else None
                # k is "spec::python_exe" — split it back out
                parts = k.split("::", 1)
                pkg_spec = parts[0] if parts else k
                python_exe = parts[1] if len(parts) > 1 else ""
                worker_details[k] = {
                    "last_used":       v["last_used"],
                    "request_count":   v["request_count"],
                    "health_failures": v["worker"].health_check_failures,
                    "pid":             pid,
                    "pkg_spec":        pkg_spec,
                    "python_exe":      python_exe,
                    "pinned":          v.get("pinned", False),   # NEW
                }

            # 🔥 FIX: Also report idle pool sizes
            idle_pool_info = {}
            for py_exe, pool in self.idle_pools.items():
                idle_pool_info[py_exe] = pool.qsize()

            # 🔥 FIX: Safe psutil memory check
            memory_percent = -1  # Sentinel value
            try:
                import psutil

                memory_percent = psutil.virtual_memory().percent
            except ImportError:
                pass  # Will show as -1 in status output

            return {
                "success": True,
                "running": self.running,
                "workers": len(self.workers),
                "stats": self.stats,
                "worker_details": worker_details,
                "memory_percent": memory_percent,
                "idle_pool_info": idle_pool_info,
            }

    def _handle_shutdown(self, signum, frame):
        """CRITICAL FIX: Graceful shutdown with timeout."""
        self.running = False

        # Shutdown executor first
        self.executor.shutdown(wait=False)

        deadline = time.time() + 5.0

        with self.pool_lock:
            workers_list = list(self.workers.values())

        for info in workers_list:
            remaining = deadline - time.time()
            if remaining <= 0:
                info["worker"].force_shutdown()
            else:
                try:
                    info["worker"].force_shutdown()
                except Exception:
                    pass

        # Cleanup
        shm_registry.cleanup_orphans()
        try:
            os.unlink(self.socket_path)
            os.unlink(PID_FILE)
        except:
            pass

        sys.exit(0)

    @classmethod
    def is_running(cls) -> bool:
        # On Windows, the most reliable check is whether the TCP socket is accepting connections.
        if IS_WINDOWS:
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            if not conn_file.exists():
                return False
            try:
                conn_str = conn_file.read_text().strip()
                if conn_str.startswith('tcp://'):
                    host_port = conn_str[6:]
                    host, port_str = host_port.split(':')
                    port = int(port_str)
                else:
                    port = 5678
                    host = '127.0.0.1'
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((host, port))
                s.close()
                return True
            except Exception:
                return False

        # Unix: PID file check
        if not os.path.exists(PID_FILE):
            return False
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except:
            return False


# ═══════════════════════════════════════════════════════════════
# GPU IPC MULTI-FALLBACK STRATEGY
# Handles PyTorch 1.x, 2.x, and custom CUDA IPC
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 1. CAPABILITY DETECTION
# ═══════════════════════════════════════════════════════════════


def detect_torch_cuda_ipc_mode():
    import torch

    """
    Detect which CUDA IPC method is available.
    
    Returns:
        'native_1x': PyTorch 1.x with _new_using_cuda_ipc (FASTEST)
        'custom': Custom CUDA IPC via ctypes (FAST)
        'hybrid': CPU SHM fallback (ACCEPTABLE)
    """
    torch_version = torch.__version__.split("+")[0]
    major, minor = map(int, torch_version.split(".")[:2])

    # Check for PyTorch 1.x native CUDA IPC
    if major == 1:
        try:
            # Test if the method exists
            if hasattr(torch.FloatStorage, "_new_using_cuda_ipc"):
                return "native_1x"
        except:
            pass

    # Check for custom CUDA IPC capability
    try:
        cuda = ctypes.CDLL("libcuda.so.1")
        # Test basic CUDA driver calls
        cuda.cuInit(0)
        return "custom"
    except:
        pass

    # Fallback to hybrid mode
    return "hybrid"


# ═══════════════════════════════════════════════════════════════
# 2. NATIVE PYTORCH 1.x IPC (TRUE ZERO-COPY)
# ═══════════════════════════════════════════════════════════════


def share_tensor_native_1x(tensor: "torch.Tensor") -> dict:
    """
    Share GPU tensor using PyTorch 1.x native CUDA IPC.
    This is the FASTEST method - true zero-copy.
    """
    if not tensor.is_cuda:
        raise ValueError("Tensor must be on GPU")

    # Share the underlying storage
    tensor.storage().share_cuda_()

    # Get IPC handle
    ipc_handle = tensor.storage()._share_cuda_()

    return {
        "ipc_handle": ipc_handle,
        "shape": tuple(tensor.shape),
        "dtype": str(tensor.dtype).split(".")[-1],
        "device": tensor.device.index,
        "method": "native_1x",
    }


def receive_tensor_native_1x(meta: dict) -> "torch.Tensor":
    import torch

    """Reconstruct tensor from PyTorch 1.x IPC handle."""
    storage = torch.FloatStorage._new_using_cuda_ipc(meta["ipc_handle"])

    dtype_map = {
        "float32": torch.float32,
        "float64": torch.float64,
        "float16": torch.float16,
    }

    tensor = torch.tensor([], dtype=dtype_map[meta["dtype"]], device=f"cuda:{meta['device']}")
    tensor.set_(storage, 0, meta["shape"])

    return tensor

# ═══════════════════════════════════════════════════════════════
# 3. CUSTOM CUDA IPC (CTYPES - WORKS WITH ANY PYTORCH)
# ═══════════════════════════════════════════════════════════════

class CUDAIPCHandle(ctypes.Structure):
    """CUDA IPC memory handle structure."""

    _fields_ = [("reserved", ctypes.c_char * 64)]


def share_tensor_custom_cuda(tensor: "torch.Tensor") -> dict:
    """
    Share GPU tensor using raw CUDA IPC (ctypes).
    Works with PyTorch 2.x and bypasses PyTorch's broken IPC.
    """
    if not tensor.is_cuda:
        raise ValueError("Tensor must be on GPU")

    # Get CUDA context
    cuda = ctypes.CDLL("libcuda.so.1")

    # Get device pointer
    data_ptr = tensor.data_ptr()

    # Create IPC handle
    ipc_handle = CUDAIPCHandle()
    result = cuda.cuIpcGetMemHandle(ctypes.byref(ipc_handle), ctypes.c_void_p(data_ptr))

    if result != 0:
        raise RuntimeError(f"cuIpcGetMemHandle failed with code {result}")

    return {
        "ipc_handle": bytes(ipc_handle.reserved),
        "shape": tuple(tensor.shape),
        "dtype": str(tensor.dtype).split(".")[-1],
        "device": tensor.device.index,
        "size_bytes": tensor.numel() * tensor.element_size(),
        "method": "custom",
    }


def receive_tensor_custom_cuda(meta: dict) -> "torch.Tensor":
    import torch

    """Reconstruct tensor from custom CUDA IPC handle."""
    cuda = ctypes.CDLL("libcuda.so.1")

    # Reconstruct IPC handle
    ipc_handle = CUDAIPCHandle()
    ipc_handle.reserved = meta["ipc_handle"]

    # Open IPC handle
    device_ptr = ctypes.c_void_p()
    result = cuda.cuIpcOpenMemHandle(
        # CU_IPC_MEM_LAZY_ENABLE_PEER_ACCESS
        ctypes.byref(device_ptr),
        ipc_handle,
        1,
    )

    if result != 0:
        raise RuntimeError(f"cuIpcOpenMemHandle failed with code {result}")

    # Create tensor from device pointer
    dtype_map = {
        "float32": torch.float32,
        "float64": torch.float64,
        "float16": torch.float16,
    }

    # Use PyTorch's internal method to wrap device pointer
    storage = torch.cuda.FloatStorage._new_with_weak_ptr(device_ptr.value)

    tensor = torch.tensor([], dtype=dtype_map[meta["dtype"]], device=f"cuda:{meta['device']}")
    tensor.set_(storage, 0, meta["shape"])

    return tensor


# ═══════════════════════════════════════════════════════════════
# 4. HYBRID MODE (CPU SHM FALLBACK)
# ═══════════════════════════════════════════════════════════════


def share_tensor_hybrid(tensor: "torch.Tensor") -> dict:
    """
    Fallback: Copy to CPU SHM, worker copies to GPU.
    2 PCIe transfers per stage, but still faster than JSON.
    """
    input_cpu = tensor.cpu().numpy()

    shm = shared_memory.SharedMemory(create=True, size=input_cpu.nbytes)
    shm_array = np.ndarray(input_cpu.shape, dtype=input_cpu.dtype, buffer=shm.buf)
    shm_array[:] = input_cpu[:]

    return {
        "shm_name": shm.name,
        "shape": tuple(tensor.shape),
        "dtype": str(tensor.dtype).split(".")[-1],
        "device": tensor.device.index,
        "method": "hybrid",
    }


def receive_tensor_hybrid(meta: dict) -> "torch.Tensor":
    import torch

    """Reconstruct tensor from CPU SHM."""
    shm = shared_memory.SharedMemory(name=meta["shm_name"])

    dtype_map = {"float32": np.float32, "float64": np.float64, "float16": np.float16}

    input_cpu = np.ndarray(tuple(meta["shape"]), dtype=dtype_map[meta["dtype"]], buffer=shm.buf)

    device = torch.device(f"cuda:{meta['device']}")
    tensor = torch.from_numpy(input_cpu.copy()).to(device)
    shm.close()

    return tensor


# ═══════════════════════════════════════════════════════════════
# 5. UNIFIED API
# ═══════════════════════════════════════════════════════════════


class SmartGPUIPC:
    """
    Automatically selects best available GPU IPC method.
    Graceful degradation: native_1x > custom > hybrid
    """

    def __init__(self):
        self.mode = detect_torch_cuda_ipc_mode()
        safe_print(_('🔥 GPU IPC Mode: {}').format(self.mode))

        if self.mode == "native_1x":
            self.share = share_tensor_native_1x
            self.receive = receive_tensor_native_1x
        elif self.mode == "custom":
            # NEW: Use the custom methods
            self.share = share_tensor_custom_cuda
            self.receive = receive_tensor_custom_cuda
        else:
            self.share = share_tensor_hybrid
            self.receive = receive_tensor_hybrid

    def share_tensor(self, tensor: "torch.Tensor") -> dict:
        """Share a GPU tensor using best available method."""
        return self.share(tensor)

    def receive_tensor(self, meta: dict) -> "torch.Tensor":
        """Receive a GPU tensor using method specified in metadata."""
        return self.receive(meta)


# import torch


class IPCMode(Enum):
    """Available IPC transfer modes."""

    AUTO = "auto"  # Smart detection (default)
    UNIVERSAL = "universal"  # Pure CUDA IPC (ctypes) - FASTEST
    PYTORCH_NATIVE = "pytorch_native"  # PyTorch 1.x _share_cuda_() - VERY FAST
    CPU_SHM = "cpu_shm"  # CPU zero-copy SHM - MEDIUM (fallback)
    HYBRID = "hybrid"  # CPU SHM + GPU copies - SLOW (testing only)


class IPCCapabilities:
    """Detect available IPC methods on the system."""

    @staticmethod
    def has_pytorch_1x_native() -> bool:
        """Check if PyTorch 1.x native IPC is available."""
        try:
            import torch

            version = torch.__version__.split("+")[0]
            major = int(version.split(".")[0])

            if major != 1:
                return False

            # Test if _share_cuda_() exists and works
            if not torch.cuda.is_available():
                return False

            test_tensor = torch.zeros(1).cuda()
            storage = test_tensor.storage()

            if not hasattr(storage, "_share_cuda_"):
                return False

            # Try to get IPC handle
            ipc_data = storage._share_cuda_()
            return len(ipc_data) == 8

        except Exception:
            return False

    @staticmethod
    def has_universal_cuda_ipc() -> bool:
        """Check if Universal CUDA IPC is available."""
        try:
            from omnipkg.isolation.worker_daemon import UniversalGpuIpc

            UniversalGpuIpc.get_lib()
            return True
        except Exception:
            return False

    @staticmethod
    def detect_optimal_mode() -> IPCMode:
        """
        Auto-detect the best available IPC mode.

        Priority order (based on benchmarks):
        1. Universal IPC - fastest (1.5-2ms), works everywhere
        2. PyTorch Native - very fast (2-2.5ms), PyTorch 1.x only
        3. CPU SHM - medium (10-11ms), always available
        4. Hybrid - slowest (14-15ms), last resort
        """
        # Universal IPC is now the default (fastest, most compatible)
        if IPCCapabilities.has_universal_cuda_ipc():
            return IPCMode.UNIVERSAL

        # Fall back to PyTorch native if available (still very fast)
        if IPCCapabilities.has_pytorch_1x_native():
            return IPCMode.PYTORCH_NATIVE

        # CPU SHM is faster than Hybrid (10ms vs 14ms in benchmarks)
        # Always available as it doesn't need GPU
        # Hybrid is kept available for testing but not used in auto-fallback
        return IPCMode.CPU_SHM

    @staticmethod
    def validate_mode(requested_mode: IPCMode) -> Tuple[IPCMode, str]:
        """
        Validate requested IPC mode and return actual mode + message.

        Returns:
            (actual_mode, message)
        """
        if requested_mode == IPCMode.AUTO:
            mode = IPCCapabilities.detect_optimal_mode()
            return mode, f"Auto-detected: {mode.value}"

        # Validate specific modes
        if requested_mode == IPCMode.UNIVERSAL:
            if IPCCapabilities.has_universal_cuda_ipc():
                return requested_mode, "Universal CUDA IPC available"
            else:
                fallback = IPCCapabilities.detect_optimal_mode()
                return fallback, _('Universal IPC unavailable, using {}').format(fallback.value)

        if requested_mode == IPCMode.PYTORCH_NATIVE:
            if IPCCapabilities.has_pytorch_1x_native():
                return requested_mode, "PyTorch 1.x native IPC available"
            else:
                fallback = IPCCapabilities.detect_optimal_mode()
                return fallback, _('PyTorch native unavailable, using {}').format(fallback.value)

        # CPU SHM always works (no GPU needed)
        if requested_mode == IPCMode.CPU_SHM:
            return requested_mode, "Using CPU SHM (zero-copy, no GPU)"

        # Hybrid always works (but slower than CPU SHM)
        if requested_mode == IPCMode.HYBRID:
            return requested_mode, "Using hybrid mode (CPU SHM + GPU copies)"

        # Unknown mode
        fallback = IPCCapabilities.detect_optimal_mode()
        return fallback, _('Unknown mode, using {}').format(fallback.value)


# ═══════════════════════════════════════════════════════════════
# 4. CLIENT & PROXY (With Auto-Resurrection)
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# DIRTY PACKAGE REGISTRY
# Packages that permanently contaminate sys.modules on import.
# A worker that has loaded one of these can NEVER be reassigned
# to a different version — it must be pinned to its exact spec.
# ═══════════════════════════════════════════════════════════════
_DIRTY_PACKAGE_ROOTS = frozenset({
    "torch",
    "tensorflow",
    "tf",
    "jax",
    "jaxlib",
})

def _spec_is_dirty(spec: str) -> bool:
    """Return True if spec contains a package that can't be version-switched."""
    name = spec.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
    # torch covers torchvision, torchaudio, etc.
    return any(name == root or name.startswith(root) for root in _DIRTY_PACKAGE_ROOTS)


# ═══════════════════════════════════════════════════════════════
# WORKER POOL  (in-process fallback when daemon is unavailable)
#
# Mirrors WorkerPoolDaemon behaviour but lives in the calling
# process.  Simpler, higher startup latency, same isolation.
#
# Worker lifecycle:
#   idle >5 min  → reaped automatically
#   dirty spec   → pinned, never reassigned
#   clean spec   → reused across requests (assign_spec guards re-init)
# ═══════════════════════════════════════════════════════════════
class WorkerPool:
    """
    Singleton in-process pool of PersistentWorkers.
    Used as a fallback when the daemon socket is unavailable.

    Usage:
        result = WorkerPool.get_instance().execute("rich==13.4.2", code, python_exe)
    """

    _instance: "WorkerPool | None" = None
    _instance_lock = threading.Lock()

    IDLE_TTL = 300.0          # seconds before an idle worker is reaped
    REAP_INTERVAL = 60.0      # how often the reaper thread wakes up
    MAX_WORKERS = 20          # hard cap to prevent runaway memory

    def __init__(self):
        # workers keyed by "spec::python_exe"
        self._workers: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._start_reaper()

    # ── singleton ────────────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "WorkerPool":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── public API ───────────────────────────────────────────────
    def execute(self, spec: str, code: str, python_exe: str = None) -> dict:
        """
        Run *code* inside an isolated worker that has *spec* installed.
        Worker is created on first call and reused on subsequent calls.

        For dirty packages (torch/tf/jax) the worker is pinned to the
        exact spec and python_exe — it will never be reassigned.
        """
        python_exe = str(Path(python_exe or sys.executable).resolve())
        worker_key = f"{spec}::{python_exe}"
        dirty = _spec_is_dirty(spec)

        worker = self._get_or_create(worker_key, spec, python_exe, dirty)
        try:
            result = worker["worker"].execute_shm_task(
                task_id=f"pool-{int(time.time()*1000)}",
                code=code,
                shm_in={},
                shm_out={},
                timeout=300.0,
            )
            with self._lock:
                worker["last_used"] = time.time()
            return result
        except Exception as e:
            # Worker probably crashed — remove it so next call gets a fresh one
            with self._lock:
                self._workers.pop(worker_key, None)
            return {"success": False, "error": str(e), "status": "ERROR"}

    # ── internals ────────────────────────────────────────────────
    def _get_or_create(self, key: str, spec: str, python_exe: str, dirty: bool) -> dict:
        with self._lock:
            if key in self._workers:
                entry = self._workers[key]
                entry["last_used"] = time.time()
                return entry

            # Enforce cap — evict the stalest non-dirty worker
            if len(self._workers) >= self.MAX_WORKERS:
                self._evict_one()

            sys.stderr.write(
                f"   🔧 [WorkerPool] Creating {'pinned' if dirty else 'reusable'} "
                f"worker for {spec} ({Path(python_exe).name})\n"
            )
            pw = PersistentWorker(
                package_spec=spec,
                python_exe=python_exe,
            )
            entry = {
                "worker": pw,
                "spec": spec,
                "python_exe": python_exe,
                "dirty": dirty,
                "created": time.time(),
                "last_used": time.time(),
                    "pinned": pin,
            }
            self._workers[key] = entry
            return entry

    def _evict_one(self):
        """Evict the longest-idle non-dirty worker. Call with self._lock held."""
        candidates = [
            (k, v) for k, v in self._workers.items() if not v["dirty"]
        ]
        if not candidates:
            # All workers are dirty — evict the oldest dirty one (last resort)
            candidates = list(self._workers.items())
        if candidates:
            oldest_key = min(candidates, key=lambda kv: kv[1]["last_used"])[0]
            entry = self._workers.pop(oldest_key)
            try:
                entry["worker"].force_shutdown()
            except Exception:
                pass
            sys.stderr.write(
                f"   🗑️  [WorkerPool] Evicted idle worker: {entry['spec']}\n"
            )

    def _reap_stale(self):
        """Remove workers idle longer than IDLE_TTL."""
        now = time.time()
        with self._lock:
            stale = [
                k for k, v in self._workers.items()
                if (now - v["last_used"]) > self.IDLE_TTL
            ]
            for k in stale:
                entry = self._workers.pop(k)
                try:
                    entry["worker"].force_shutdown()
                except Exception:
                    pass
                sys.stderr.write(
                    f"   💤 [WorkerPool] TTL-reaped idle worker: {entry['spec']}\n"
                )

    def _start_reaper(self):
        def _loop():
            while True:
                time.sleep(self.REAP_INTERVAL)
                try:
                    self._reap_stale()
                except Exception:
                    pass
        t = threading.Thread(target=_loop, daemon=True, name="WorkerPool-Reaper")
        t.start()

    def shutdown_all(self):
        """Forcefully shut down every worker. Call on process exit if needed."""
        with self._lock:
            for entry in self._workers.values():
                try:
                    entry["worker"].force_shutdown()
                except Exception:
                    pass
            self._workers.clear()


class DaemonClient:
    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET,
        timeout: float = 300.0,
        auto_start: bool = True,
    ):
        self.socket_path = socket_path
        self.timeout = timeout
        self.auto_start = auto_start

    def execute(self, spec: str, code: str, python_exe: str = None) -> dict:
        """
        Simple entrypoint. Run code in an isolated worker with spec installed.

        Args:
            spec:       Package spec, e.g. "rich==13.4.2"
            code:       Python code to run inside the worker
            python_exe: Path to the interpreter (default: current Python)

        Returns:
            dict with keys: success, stdout, stderr, (error on failure)

        Falls back to WorkerPool (in-process PersistentWorkers) if the daemon
        is unreachable.
        """
        result = self.execute_shm(
            spec=spec,
            code=code,
            shm_in={},
            shm_out={},
            python_exe=python_exe or sys.executable,
        )
        # Daemon returned a connection-level failure → fall back to WorkerPool
        if not result.get("success") and "Daemon not running" in result.get("error", ""):
            sys.stderr.write(
                f"⚠️  [DaemonClient] Daemon unavailable, falling back to WorkerPool for {spec}\n"
            )
            return WorkerPool.get_instance().execute(spec, code, python_exe)
        return result

    def execute_shm(
        self,
        spec: str,
        code: str,
        shm_in: dict,
        shm_out: dict,
        python_exe: str = None,
        worker_tag: str = None,
            pin: bool = False,          # NEW: worker survives idle timeout indefinitely
        max_memory_mb: float = None,
    ):
        """
        Low-level execute with explicit SHM metadata.
        Prefer execute_smart() for all normal use — it picks the right transport
        automatically (CUDA IPC → CPU SHM → JSON).

        worker_tag and max_memory_mb are forwarded to the daemon unchanged;
        see _execute_code docstring for full semantics.
        """
        python_exe = _resolve_python_exe(python_exe)
        payload = {
            "type":       "execute",
            "spec":       spec,
            "code":       code,
            "shm_in":     shm_in,
            "shm_out":    shm_out,
            "python_exe": python_exe,
        }
        # Only include optional fields when set — keeps wire format backward-compat
        if worker_tag is not None:
            payload["worker_tag"] = worker_tag
        if pin:
            payload["pin"] = True
        if pin:
            payload["pin"] = True
        if max_memory_mb is not None:
            payload["max_memory_mb"] = max_memory_mb
        return self._send(payload)

    def status(self):
        old_auto = self.auto_start
        self.auto_start = False
        try:
            return self._send({"type": "status"})
        finally:
            self.auto_start = old_auto

    def shutdown(self):
        return self._send({"type": "shutdown"})

    def _spawn_daemon(self):

        daemon_script = os.path.abspath(__file__)

        # Optional: Set minimal CUDA paths for daemon itself
        env = os.environ.copy()
        # 🔥 CRITICAL: Mark as daemon child to prevent infinite re-spawning
        env["OMNIPKG_DAEMON_CHILD"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        if IS_WINDOWS:
            # Windows: Use full detachment flags + OMNIPKG_DAEMON_CHILD guard
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            os.makedirs(os.path.dirname(DAEMON_LOG_FILE), exist_ok=True)
            log_fh = open(DAEMON_LOG_FILE, "a", encoding="utf-8", buffering=1)
            subprocess.Popen(
                [sys.executable, "-u", daemon_script, "start", "--no-fork"],
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                env=env,
                close_fds=False,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            )
            # Keep handle alive briefly so daemon has time to open the file itself
            self._auto_start_log_handle = log_fh
        else:
            # Unix: Use preexec_fn for process group
            subprocess.Popen(
                [sys.executable, daemon_script, "start"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                preexec_fn=os.setsid,
            )

    def get_idle_config(self) -> dict:
        """Get current idle pool configuration."""
        return self._send({"type": "get_idle_config"})
    
    def set_idle_config(self, python_exe: str, count: int) -> dict:
        """Set idle pool configuration for a Python executable."""
        return self._send({
            "type": "set_idle_config",
            "python_exe": python_exe,
            "count": count
        })

    def _wait_for_socket(self, timeout=5.0):
        """
        Fixed version that works on Windows and Unix
        Wait for daemon to be ready to accept connections
        """
        start_time = time.time()
        
        if sys.platform == 'win32':
            # Windows: Wait for connection file and test TCP connection
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            
            while time.time() - start_time < timeout:
                if conn_file.exists():
                    try:
                        # Read connection info
                        conn_str = conn_file.read_text().strip()
                        if conn_str.startswith('tcp://'):
                            host_port = conn_str[6:]
                            host, port_str = host_port.split(':')
                            port = int(port_str)
                        else:
                            # Fallback
                            port = getattr(self, 'daemon_port', 5678)
                            host = '127.0.0.1'
                        
                        # Try to connect
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(0.5)
                        s.connect((host, port))
                        s.close()
                        return True
                        
                    except (ConnectionRefusedError, OSError, ValueError):
                        pass
                
                time.sleep(0.1)
            return False
        
        else:
            # Unix: Wait for socket file and test connection (original logic)
            while time.time() - start_time < timeout:
                if os.path.exists(self.socket_path):
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        s.settimeout(0.5)
                        s.connect(self.socket_path)
                        s.close()
                        return True
                    except (ConnectionRefusedError, OSError):
                        pass
                time.sleep(0.1)
            return False


    # ============================================================
    # HELPER FUNCTION: Get connection info based on platform
    # ============================================================

    def _get_connection_info(self):
        """
        Get socket family and address for connecting to daemon.
        Works on both Windows (TCP) and Unix (domain socket).
        
        Returns:
            tuple: (socket_family, address)
                - Windows: (AF_INET, ('127.0.0.1', port))
                - Unix: (AF_UNIX, '/path/to/socket')
        """
        if sys.platform == 'win32':
            # Windows: Read TCP connection from file
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            
            if conn_file.exists():
                try:
                    conn_str = conn_file.read_text().strip()
                    # Parse "tcp://127.0.0.1:5678"
                    if conn_str.startswith('tcp://'):
                        host_port = conn_str[6:]
                        host, port_str = host_port.split(':')
                        port = int(port_str)
                        return (socket.AF_INET, (host, port))
                except Exception:
                    pass
            
            # Fallback to default port
            port = getattr(self, 'daemon_port', 5678)
            return (socket.AF_INET, ('127.0.0.1', port))
        else:
            # Unix: Use socket path
            return (socket.AF_UNIX, self.socket_path)


    # ============================================================
    # FIX 1: _send method (CRITICAL - used for all daemon communication)
    # ============================================================

    def _send(self, req):
        """
        Fixed version that works on Windows and Unix
        """
        attempts = 0
        max_attempts = 3 if not self.auto_start else 2
        
        while attempts < max_attempts:
            attempts += 1
            try:
                # Get platform-appropriate connection info
                sock_family, address = self._get_connection_info()
                
                # Create and connect socket
                sock = socket.socket(sock_family, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect(address)
                
                # Send request and receive response
                send_json(sock, req, timeout=self.timeout)
                res = recv_json(sock, timeout=self.timeout)
                sock.close()
                return res
                
            except (ConnectionRefusedError, FileNotFoundError):
                if not self.auto_start:
                    if attempts >= max_attempts:
                        return {"success": False, "error": "Daemon not running"}
                    time.sleep(0.2)
                    continue
                
                # Clean up stale connection info
                if sys.platform == 'win32':
                    # Remove stale connection file
                    conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
                    try:
                        conn_file.unlink()
                    except:
                        pass
                else:
                    # Remove stale Unix socket
                    try:
                        os.unlink(self.socket_path)
                    except:
                        pass
                
                # Try to auto-start daemon
                self._spawn_daemon()
                
                if self._wait_for_socket(timeout=30.0):
                    attempts = 0
                    self.auto_start = False
                    continue
                else:
                    return {
                        "success": False,
                        "error": "Failed to auto-start daemon (timeout)",
                    }
                    
            except Exception as e:
                return {"success": False, "error": _('Communication error: {}').format(e)}
        
        return {"success": False, "error": "Connection failed after retries"}


    # ============================================================
    # BONUS FIX: Check if daemon is running (for status command)
    # ============================================================

    def is_daemon_running(self):
        """
        Check if daemon is currently running
        Works on both Windows and Unix
        """
        if sys.platform == 'win32':
            # Windows: Check if connection file exists and port is listening
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            if not conn_file.exists():
                return False
            
            try:
                conn_str = conn_file.read_text().strip()
                if conn_str.startswith('tcp://'):
                    host_port = conn_str[6:]
                    host, port_str = host_port.split(':')
                    port = int(port_str)
                else:
                    port = getattr(self, 'daemon_port', 5678)
                    host = '127.0.0.1'
                
                # Try quick connection
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((host, port))
                s.close()
                return True
            except:
                return False
        else:
            # Unix: Check if socket exists and is connectable
            if not os.path.exists(self.socket_path):
                return False
            
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(self.socket_path)
                s.close()
                return True
            except:
                return False

    def _get_connection_info(self):
        """
        Get socket family and address for connecting to daemon.
        
        Returns:
            tuple: (socket_family, address)
        """
        if sys.platform == 'win32':
            conn_file = Path(tempfile.gettempdir()) / 'omnipkg' / 'daemon_connection.txt'
            
            if conn_file.exists():
                try:
                    conn_str = conn_file.read_text().strip()
                    if conn_str.startswith('tcp://'):
                        host_port = conn_str[6:]
                        host, port_str = host_port.split(':')
                        return (socket.AF_INET, (host, int(port_str)))
                except Exception:
                    pass
            
            # Fallback
            port = getattr(self, 'daemon_port', 5678)
            return (socket.AF_INET, ('127.0.0.1', port))
        else:
            return (socket.AF_UNIX, self.socket_path)

    def optimistic_update_atomic(self, expected_version: int) -> bool:
        """
        TRUE HARDWARE CAS.
        Replaces try_lock_and_validate for the C++ path.
        """
        if not _HAS_ATOMICS:
            return self.try_lock_and_validate(expected_version)
            
        # Get raw pointer address
        addr = ctypes.addressof(self.shm.buf)
        
        # Call C extension
        # Atomically: if version == expected, set version = expected + 1
        # We skip the "Lock" state entirely because CAS *is* the lock.
        success = omnipkg_atomic.cas64(addr, expected_version, expected_version + 1)  # ← FIXED!
        
        return success

    def execute_optimistic_write(
        self,
        spec: str,
        code_template: str, 
        control_block_name: str,
        tensor_in: "torch.Tensor",
        python_exe: str = None,
        max_retries: int = 100
    ):
        monitor = SharedStateMonitor(control_block_name)
        
        # 🟢 FAST PATH: Hardware Atomics
        if _HAS_ATOMICS:
            try:
                locked_ver = monitor.acquire_atomic_spinlock(timeout_seconds=5.0)
                res, meta = self.execute_cuda_ipc(
                    spec, code_template, tensor_in, tensor_in.shape, "float32", 
                    python_exe=python_exe, ipc_mode="universal"
                )
                monitor.release_atomic_spinlock(locked_ver)
                return res, meta, 0
            except Exception as e:
                # If spinlock fails, fall back to retry loop or raise
                raise e

        # 🟡 SLOW PATH: Legacy File Lock (Existing Logic)
        retries = 0
        while retries < max_retries:
            start_version = monitor.get_version()
            if monitor.try_lock_and_validate(start_version):
                try:
                    res, meta = self.execute_cuda_ipc(
                        spec, code_template, tensor_in, tensor_in.shape, "float32",
                        python_exe=python_exe, ipc_mode="universal"
                    )
                    monitor.commit_and_release(start_version + 2) # Maintain parity
                    return res, meta, retries
                except:
                    monitor.commit_and_release(start_version)
                    raise
            else:
                retries += 1
                time.sleep(0.001 * (2 ** min(retries, 5)))
        raise RuntimeError("Max retries exceeded")

    def execute_cuda_ipc(
        self,
        spec: str,
        code: str,
        input_tensor: "torch.Tensor",
        output_shape: tuple,
        output_dtype: str,
        python_exe: str = None,
        ipc_mode: str = "auto",
            pin: bool = False,          # NEW: worker survives idle timeout indefinitely
        worker_tag: str = None,
        max_memory_mb: float = None,
    ):
        """
        Execute code with GPU IPC using specified mode.

        Args:
            ipc_mode: 'auto', 'universal', 'pytorch_native', 'cpu_shm', or 'hybrid'
        """
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")

        if not input_tensor.is_cuda:
            raise ValueError("Input tensor must be on GPU")

        # Parse IPC mode
        try:
            mode_enum = IPCMode(ipc_mode.lower())
        except ValueError:
            safe_print(_("⚠️  Invalid IPC mode '{}', using auto").format(ipc_mode))
            mode_enum = IPCMode.AUTO

        # Validate and get actual mode
        actual_mode, mode_msg = IPCCapabilities.validate_mode(mode_enum)

        safe_print(_('   🎯 IPC Mode: {}').format(mode_msg))

        # ═══════════════════════════════════════════════════════════
        # ROUTE 1: UNIVERSAL CUDA IPC (DEFAULT - FASTEST)
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.UNIVERSAL:
            return self._execute_universal_ipc(
                spec, code, input_tensor, output_shape, output_dtype, python_exe,
                worker_tag=worker_tag, max_memory_mb=max_memory_mb
            )

        # ═══════════════════════════════════════════════════════════
        # ROUTE 2: PYTORCH 1.x NATIVE IPC
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.PYTORCH_NATIVE:
            return self._execute_pytorch_native_ipc(
                spec, code, input_tensor, output_shape, output_dtype, python_exe,
                worker_tag=worker_tag, max_memory_mb=max_memory_mb
            )

        # ═══════════════════════════════════════════════════════════
        # ROUTE 3: CPU SHM (ZERO-COPY, NO GPU - MEDIUM SPEED)
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.CPU_SHM:
            return self._execute_cpu_shm(
                spec, code, input_tensor, output_shape, output_dtype, python_exe,
                worker_tag=worker_tag, max_memory_mb=max_memory_mb
            )

        # ═══════════════════════════════════════════════════════════
        # ROUTE 4: HYBRID (CPU SHM + GPU COPIES - SLOWEST)
        # ═══════════════════════════════════════════════════════════
        return self._execute_hybrid_ipc(
            spec, code, input_tensor, output_shape, output_dtype, python_exe,
            worker_tag=worker_tag, max_memory_mb=max_memory_mb
        )

    def _execute_cpu_shm(self, spec, code, input_tensor, output_shape, output_dtype, python_exe, worker_tag=None, max_memory_mb=None):
        """
        CPU-only mode: Run computation on CPU without any GPU transfers.
        Uses zero-copy SHM like Test 17.

        This is faster than Hybrid mode (10ms vs 14ms) because:
        - No GPU→CPU copy
        - No CPU→GPU copy
        - Just pure CPU compute on shared memory

        Benchmarks show this is 6.29x slower than Universal IPC,
        but 1.34x FASTER than Hybrid mode!
        """
        import numpy as np
        import torch

        safe_print("   💾 Using CPU SHM mode (zero-copy, no GPU transfers)")

        # Convert tensor to CPU numpy
        input_cpu = input_tensor.cpu().numpy()

        # Create output array on CPU
        dtype_map = {
            "float32": np.float32,
            "float64": np.float64,
            "float16": np.float16,
            "int32": np.int32,
            "int64": np.int64,
        }
        np_dtype = dtype_map.get(output_dtype, np.float32)

        try:
            # Use zero_copy execution (like Test 17)
            result_cpu, response = self.execute_zero_copy(
                spec,
                code,
                input_array=input_cpu,
                output_shape=output_shape,
                output_dtype=np_dtype,
                python_exe=python_exe or sys.executable,
                worker_tag=worker_tag,
                max_memory_mb=max_memory_mb,
            )

            if not response.get("success"):
                raise RuntimeError(_('Worker Error: {}').format(response.get('error')))

            safe_print("   ✅ CPU SHM mode completed")

            # Convert result back to GPU tensor
            output_tensor = torch.from_numpy(result_cpu).to(input_tensor.device)

            # Add method info to response
            response["cuda_method"] = "cpu_shm"

            return output_tensor, response

        except Exception as e:
            safe_print(_('   ⚠️  CPU SHM failed: {}').format(e))
            raise

    def _execute_universal_ipc(
        self, spec, code, input_tensor, output_shape, output_dtype, python_exe, worker_tag=None, max_memory_mb=None
    ):
        """Universal CUDA IPC - client pre-allocates both buffers, worker just opens handles."""
        import torch
        from omnipkg.isolation.worker_daemon import UniversalGpuIpc

        try:
            try:
                ipc_meta = UniversalGpuIpc.share(input_tensor)
            except RuntimeError as e:
                if "code 1" in str(e):
                    input_tensor = input_tensor.clone()
                    ipc_meta = UniversalGpuIpc.share(input_tensor)
                else:
                    raise

            cuda_in_meta = {"universal_ipc": ipc_meta, "device": input_tensor.device.index}

            dtype_map = {"float32": torch.float32, "float64": torch.float64,
                         "float16": torch.float16, "int32": torch.int32, "int64": torch.int64}
            output_tensor = torch.empty(output_shape, dtype=dtype_map.get(output_dtype, torch.float32),
                                        device=input_tensor.device)
            cuda_out_meta = {"universal_ipc": UniversalGpuIpc.share(output_tensor),
                             "device": output_tensor.device.index}

            payload = {
                "type": "execute_cuda", "spec": spec, "code": code,
                "cuda_in": cuda_in_meta, "cuda_out": cuda_out_meta,
                "python_exe": python_exe or sys.executable,
            }
            if worker_tag is not None:
                payload["worker_tag"] = worker_tag
            if pin:
                payload["pin"] = True
            if max_memory_mb is not None:
                payload["max_memory_mb"] = max_memory_mb
            response = self._send(payload)

            if not response.get("success"):
                raise RuntimeError(_('Worker Error: {}').format(response.get('error')))

            actual_method = response.get("cuda_method", "unknown")
            if actual_method != "universal_ipc":
                safe_print(_('   ⚠️  Worker fell back to {}').format(actual_method))

            return output_tensor, response

        except Exception as e:
            safe_print(f"   ❌ [UNIVERSAL IPC] Failed: {e}", file=sys.stderr)
            raise

    def _execute_pytorch_native_ipc(
        self, spec, code, input_tensor, output_shape, output_dtype, python_exe, worker_tag=None, max_memory_mb=None
    ):
        """PyTorch 1.x native IPC (framework-managed)."""
        import torch

        safe_print("   🔥 Using PYTORCH NATIVE IPC (PyTorch 1.x)")

        try:
            # Share input tensor via native CUDA IPC
            try:
                input_storage = input_tensor.storage()
                (
                    storage_device,
                    storage_handle,
                    storage_size_bytes,
                    storage_offset_bytes,
                    ref_counter_handle,
                    ref_counter_offset,
                    event_handle,
                    event_sync_required,
                ) = input_storage._share_cuda_()
            except RuntimeError as e:
                # Same issue: Cannot export memory that was imported from another process.
                safe_print(f"   ⚠️ [NATIVE IPC] Cannot re-export imported IPC memory. Cloning tensor...", file=sys.stderr)
                input_tensor = input_tensor.clone()
                input_storage = input_tensor.storage()
                (
                    storage_device,
                    storage_handle,
                    storage_size_bytes,
                    storage_offset_bytes,
                    ref_counter_handle,
                    ref_counter_offset,
                    event_handle,
                    event_sync_required,
                ) = input_storage._share_cuda_()

            cuda_in_meta = {
                "ipc_data": {
                    "tensor_size": list(input_tensor.shape),
                    "tensor_stride": list(input_tensor.stride()),
                    "tensor_offset": input_tensor.storage_offset(),
                    "storage_cls": type(input_storage).__name__,
                    "dtype": str(input_tensor.dtype).replace("torch.", ""),
                    "storage_device": storage_device,
                    "storage_handle": base64.b64encode(storage_handle).decode("ascii"),
                    "storage_size_bytes": storage_size_bytes,
                    "storage_offset_bytes": storage_offset_bytes,
                    "ref_counter_handle": base64.b64encode(ref_counter_handle).decode("ascii"),
                    "ref_counter_offset": ref_counter_offset,
                    "event_handle": (
                        base64.b64encode(event_handle).decode("ascii") if event_handle else ""
                    ),
                    "event_sync_required": event_sync_required,
                },
                "device": input_tensor.device.index,
            }

            # Create output tensor and share it
            dtype_map = {
                "float32": torch.float32,
                "float64": torch.float64,
                "float16": torch.float16,
            }
            torch_dtype = dtype_map.get(output_dtype, torch.float32)
            output_tensor = torch.empty(output_shape, dtype=torch_dtype, device=input_tensor.device)

            output_storage = output_tensor.storage()
            (
                storage_device,
                storage_handle,
                storage_size_bytes,
                storage_offset_bytes,
                ref_counter_handle,
                ref_counter_offset,
                event_handle,
                event_sync_required,
            ) = output_storage._share_cuda_()

            cuda_out_meta = {
                "ipc_data": {
                    "tensor_size": list(output_tensor.shape),
                    "tensor_stride": list(output_tensor.stride()),
                    "tensor_offset": output_tensor.storage_offset(),
                    "storage_cls": type(output_storage).__name__,
                    "dtype": str(output_tensor.dtype).replace("torch.", ""),
                    "storage_device": storage_device,
                    "storage_handle": base64.b64encode(storage_handle).decode("ascii"),
                    "storage_size_bytes": storage_size_bytes,
                    "storage_offset_bytes": storage_offset_bytes,
                    "ref_counter_handle": base64.b64encode(ref_counter_handle).decode("ascii"),
                    "ref_counter_offset": ref_counter_offset,
                    "event_handle": (
                        base64.b64encode(event_handle).decode("ascii") if event_handle else ""
                    ),
                    "event_sync_required": event_sync_required,
                },
                "device": output_tensor.device.index,
            }

            payload = {
                "type": "execute_cuda",
                "spec": spec,
                "code": code,
                "cuda_in": cuda_in_meta,
                "cuda_out": cuda_out_meta,
                "python_exe": python_exe or sys.executable,
            }
            if worker_tag is not None:
                payload["worker_tag"] = worker_tag
            if pin:
                payload["pin"] = True
            if max_memory_mb is not None:
                payload["max_memory_mb"] = max_memory_mb
            response = self._send(payload)

            if not response.get("success"):
                raise RuntimeError(_('Worker Error: {}').format(response.get('error')))

            actual_method = response.get("cuda_method", "unknown")
            if actual_method == "native_ipc":
                safe_print("   🔥 Worker confirmed NATIVE IPC (PyTorch managed)!")
            else:
                safe_print(_('   ⚠️  Worker fell back to {}').format(actual_method))

            return output_tensor, response

        except Exception as e:
            safe_print(_('   ⚠️  PyTorch native IPC failed: {}').format(e))
            raise

    def _execute_hybrid_ipc(self, spec, code, input_tensor, output_shape, output_dtype, python_exe, worker_tag=None, max_memory_mb=None):
        """
        Hybrid mode: Copy to CPU SHM, worker copies to GPU.

        NOTE: Benchmarks show this is the SLOWEST mode (14ms vs 1.5ms Universal).
        Only use this for testing or when all other modes fail.

        Prefer CPU_SHM mode over this (10ms vs 14ms) - it's faster!
        """
        from multiprocessing import shared_memory

        import numpy as np
        import torch

        safe_print("   🔄 Using HYBRID mode (CPU SHM + GPU copies) - SLOWEST MODE")
        safe_print("   💡 Consider using cpu_shm mode instead (1.34x faster)")

        # Copy tensor to CPU, share via SHM
        input_cpu = input_tensor.cpu().numpy()

        shm_in = shared_memory.SharedMemory(create=True, size=input_cpu.nbytes)
        shm_in_array = np.ndarray(input_cpu.shape, dtype=input_cpu.dtype, buffer=shm_in.buf)
        shm_in_array[:] = input_cpu[:]

        # Create output SHM
        output_cpu = np.zeros(output_shape, dtype=getattr(np, output_dtype))
        shm_out = shared_memory.SharedMemory(create=True, size=output_cpu.nbytes)

        try:
            cuda_in_meta = {
                "shm_name": shm_in.name,
                "shape": tuple(input_tensor.shape),
                "dtype": output_dtype,
                "device": input_tensor.device.index,
            }

            cuda_out_meta = {
                "shm_name": shm_out.name,
                "shape": output_shape,
                "dtype": output_dtype,
                "device": input_tensor.device.index,
            }

            payload = {
                "type": "execute_cuda",
                "spec": spec,
                "code": code,
                "cuda_in": cuda_in_meta,
                "cuda_out": cuda_out_meta,
                "python_exe": python_exe or sys.executable,
            }
            if worker_tag is not None:
                payload["worker_tag"] = worker_tag
            if pin:
                payload["pin"] = True
            
            if max_memory_mb is not None:
                payload["max_memory_mb"] = max_memory_mb
            response = self._send(payload)

            if not response.get("success"):
                raise RuntimeError(_('Worker Error: {}').format(response.get('error')))

            safe_print("   ✅ Hybrid mode completed")

            # Copy result back to GPU
            shm_out_array = np.ndarray(output_shape, dtype=output_cpu.dtype, buffer=shm_out.buf)
            output_tensor = torch.from_numpy(shm_out_array.copy()).to(input_tensor.device)

            return output_tensor, response

        finally:
            try:
                shm_in.close()
                shm_in.unlink()
            except:
                pass
            try:
                shm_out.close()
                shm_out.unlink()
            except:
                pass

    def execute_zero_copy(
        self,
        spec: str,
        code: str,
        input_array,
        output_shape,
        output_dtype,
        python_exe=None,
        worker_tag: str = None,
            pin: bool = False,          # NEW: worker survives idle timeout indefinitely
        max_memory_mb: float = None,
    ):
        """
        🚀 HFT MODE: Zero-Copy Tensor Handoff via Shared Memory.
        """
        from multiprocessing import shared_memory

        import numpy as np

        shm_in = shared_memory.SharedMemory(create=True, size=input_array.nbytes)

        start_shm = np.ndarray(input_array.shape, dtype=input_array.dtype, buffer=shm_in.buf)
        start_shm[:] = input_array[:]

        dummy = np.zeros(1, dtype=output_dtype)
        out_size = int(np.prod(output_shape)) * dummy.itemsize
        shm_out = shared_memory.SharedMemory(create=True, size=out_size)

        try:
            in_meta = {
                "name": shm_in.name,
                "shape": input_array.shape,
                "dtype": str(input_array.dtype),
            }

            out_meta = {
                "name": shm_out.name,
                "shape": output_shape,
                "dtype": str(output_dtype),
            }

            # Pass python_exe to execute_shm
            response = self.execute_shm(
                spec, code, in_meta, out_meta,
                python_exe=python_exe,
                worker_tag=worker_tag,
                max_memory_mb=max_memory_mb,
            )

            if not response.get("success"):
                raise RuntimeError(_('Worker Error: {}').format(response.get('error')))

            result_view = np.ndarray(output_shape, dtype=output_dtype, buffer=shm_out.buf)
            return result_view.copy(), response

        finally:
            try:
                shm_in.close()
                shm_in.unlink()
            except:
                pass
            try:
                shm_out.close()
                shm_out.unlink()
            except:
                pass

    def execute_smart(
    self,
    spec: str,
    code: str,
    data=None,
    output_shape=None,
    output_dtype=None,
    python_exe: str = None,
    worker_tag: str = None,
    max_memory_mb: int = None,
    pin: bool = False,          # NEW: worker survives idle timeout indefinitely
):
        """
        ✨ THE ONE METHOD YOU NORMALLY CALL.

        Runs ``code`` inside a persistent worker that has ``spec`` installed,
        automatically choosing the fastest transport for ``data``.

        Quick-start examples
        --------------------
        # Simplest: no data, just run code in an isolated env
        res = client.execute_smart("rich==13.4", "from rich import print; print('hi')")

        # Pass a small list/dict (JSON path, ~10ms overhead)
        res = client.execute_smart("mylib==1.0", "print(arr_in[0])", data=[1, 2, 3])
        output = res["result"]   # stdout string

        # Pass a large numpy array (zero-copy SHM, ~5ms)
        import numpy as np
        arr = np.random.rand(1_000_000).astype(np.float32)
        res = client.execute_smart("scipy==1.13", "arr_out = arr_in * 2", data=arr)

        # Pass a CUDA tensor (CUDA IPC, <5µs — near-zero overhead)
        import torch
        gpu_t = torch.randn(1024, device="cuda")
        res = client.execute_smart("torch==2.9.1", "arr_out = arr_in * 2", data=gpu_t)

        # Isolate heavy models in separate workers (prevents RAM accumulation)
        # Each tag → its own dedicated process; models never share memory.
        client.execute_smart("torch==2.9.1", nllb_code,  worker_tag="nllb-600m")
        client.execute_smart("torch==2.9.1", seedx_code, worker_tag="seedx-7b",
                             max_memory_mb=20_000)   # auto-evict if RSS > 20 GB

        # Use a different Python version — short form accepted
        client.execute_smart("torch==2.9.1", code, python_exe="3.11")

        Parameters
        ----------
        spec : str
            pip-style package requirement.  Must NOT include the worker_tag —
            that is handled separately so pip only sees the clean spec.
        code : str
            Python source to execute.  Globals persist across calls to the same
            worker, so cache expensive objects in globals()::

                if "_model" not in globals():
                    globals()["_model"] = AutoModel.from_pretrained(...)
                result = globals()["_model"](arr_in)
                print(json.dumps(result))    # ← worker captures stdout as result

        data : optional
            Input data for the code.  Available inside the worker as ``arr_in``.
            Accepted types and the transport chosen:
              - ``None``              → JSON path, no arr_in set
              - ``list`` / ``dict``   → JSON path  (~10ms)
              - ``np.ndarray`` ≥ 64KB → CPU shared memory (~5ms)
              - CUDA ``torch.Tensor`` → CUDA IPC (<5µs)

        python_exe : str, optional
            Which Python to use.  Accepts short forms (``"3.11"``) in addition
            to full paths.  Defaults to the current interpreter.

        worker_tag : str, optional
            Route this call to a private worker bucket.  Two calls with the
            same (spec, python) but different tags get separate processes.
            Use this for model isolation — see example above.

        max_memory_mb : float, optional
            RSS ceiling in megabytes for this worker's process.  When the
            worker exceeds the cap it is evicted, and the next call spawns a
            fresh replacement.  Has no effect if psutil is not installed.

        Returns
        -------
        dict with keys:
          ``success`` bool
          ``result``  stdout string (JSON path) or numpy/torch array (SHM/CUDA)
          ``transport`` one of ``"JSON"``, ``"SHM"``, ``"CUDA_IPC"``
          ``error``   present only on failure
        """
        import numpy as np

        # ── CUDA IPC path ──────────────────────────────────────────────────
        if data is not None and hasattr(data, "is_cuda") and data.is_cuda:
            output_shape = data.shape
            output_dtype = str(data.dtype).split(".")[-1]
            result_tensor, meta = self.execute_cuda_ipc(
                spec, code, data, output_shape, output_dtype,
                python_exe, worker_tag=worker_tag, max_memory_mb=max_memory_mb,
            )
            return {"success": True, "result": result_tensor, "meta": meta,
                    "transport": "CUDA_IPC"}

        # ── CPU SHM path (large arrays) ────────────────────────────────────
        SMART_THRESHOLD = 1024 * 64   # 64 KB
        if data is not None and isinstance(data, np.ndarray) and data.nbytes >= SMART_THRESHOLD:
            output_shape = data.shape
            output_dtype = data.dtype
            result, meta = self.execute_zero_copy(
                spec, code, data, output_shape, output_dtype,
                python_exe, worker_tag=worker_tag, max_memory_mb=max_memory_mb,
            )
            return {"success": True, "result": result, "meta": meta, "transport": "SHM"}

        # ── JSON path (small / no data) ────────────────────────────────────
        prefix = ""
        if data is not None:
            if isinstance(data, np.ndarray):
                prefix = f"import numpy as np\narr_in = np.array({data.tolist()})\n"
            else:
                prefix = f"arr_in = {json.dumps(data)}\n"

        response = self.execute_shm(
            spec, prefix + code, {}, {},
            python_exe=python_exe,
            worker_tag=worker_tag,
            max_memory_mb=max_memory_mb,
        )
        if response.get("success"):
            return {"success": True,
                    "result": response.get("stdout", "").strip(),
                    "meta": response,
                    "transport": "JSON"}
        return response


class DaemonProxy:
    """Proxies calls from Loader to the Daemon via Socket/SHM"""

    def __init__(self, client, package_spec, python_exe=None):
        self.client = client
        self.spec = package_spec
        self.python_exe = python_exe
        self.process = "DAEMON_MANAGED"

    def execute(self, code: str):
        result = self.client.execute_shm(self.spec, code, shm_in={}, shm_out={})

        # Transform daemon response to match loader.execute() format
        if result.get("status") == "COMPLETED":
            return {
                "success": True,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "locals": result.get("locals", ""),
            }
        else:
            return {
                "success": False,
                "error": result.get("error", "Unknown daemon error"),
                "traceback": result.get("traceback", ""),
            }

    def get_version(self, package_name):
        code = f"try: import importlib.metadata as meta\nexcept ImportError: import importlib_metadata as meta\nresult = {{'version': meta.version('{package_name}'), 'path': __import__('{package_name}').__file__}}"
        res = self.execute(code)
        if res.get("success"):
            return {"success": True, "version": "unknown", "path": "daemon"}
        return {"success": False, "error": res.get("error")}

    def shutdown(self):
        pass


# ═══════════════════════════════════════════════════════════════
# 5. CLI FUNCTIONS
# ═══════════════════════════════════════════════════════════════


def cli_start():
    """Start the daemon with status checks."""
    if WorkerPoolDaemon.is_running():
        safe_print("⚠️  Daemon is already running.")
        # Optional: Print info about the running instance
        cli_status()
        return

    safe_print("🚀 Initializing OmniPkg Worker Daemon...", end=" ", flush=True)

    # Initialize
    daemon = WorkerPoolDaemon(max_workers=10, max_idle_time=300, warmup_specs=[])

    # Start (The parent process will print "✅" and exit inside this call)
    try:
        daemon.start(daemonize=True)
    except Exception as e:
        safe_print(_('\n❌ Failed to start: {}').format(e))


def cli_stop():
    """Stop the daemon."""
    client = DaemonClient()
    result = client.shutdown()
    if result.get("success"):
        safe_print("✅ Daemon stopped")
        try:
            os.unlink(PID_FILE)
        except:
            pass
    else:
        safe_print(_('❌ Failed to stop: {}').format(result.get('error', 'Unknown error')))


def cli_status():
    """Get daemon status."""
    if not WorkerPoolDaemon.is_running():
        safe_print("❌ Daemon not running")
        return

    client = DaemonClient()
    result = client.status()

    if not result.get("success"):
        safe_print(_('❌ Error: {}').format(result.get('error', 'Unknown error')))
        return

    print("\n" + "=" * 60)
    safe_print("🔥 OMNIPKG WORKER DAEMON STATUS")
    print("=" * 60)
    print(_('  Workers: {}').format(result.get('workers', 0)))

    # 🔥 FIX: Handle missing psutil gracefully
    memory_percent = result.get("memory_percent", -1)
    if memory_percent >= 0:
        print(f"  Memory Usage: {memory_percent:.1f}%")
    else:
        print(_('  Memory Usage: N/A (psutil not installed)'))

    print(_('  Total Requests: {}').format(result['stats']['total_requests']))
    print(_('  Cache Hits: {}').format(result['stats']['cache_hits']))
    print(_('  Errors: {}').format(result['stats']['errors']))

    if result.get("worker_details"):
        safe_print("\n  📦 Active Workers:")
        for spec, info in result["worker_details"].items():
            idle = time.time() - info["last_used"]
            pkg_spec = info.get("pkg_spec", spec.split("::")[0])
            python_exe = info.get("python_exe", "")
            pid = info.get("pid", "?")
            # Extract short python version from path
            import re
            py_ver = "?"
            m = re.search(r"cpython[\-_](3\.\d+)", python_exe, re.IGNORECASE)
            if m:
                py_ver = m.group(1)
            else:
                m = re.search(r"python(3\.\d+)", python_exe, re.IGNORECASE)
                if m:
                    py_ver = m.group(1)
            safe_print(f"    - {pkg_spec}  [py{py_ver}]  PID {pid}")
            print(
                f"      Requests: {info['request_count']}, Idle: {idle:.0f}s, Failures: {info['health_failures']}"
            )

    idle_pool_info = result.get("idle_pool_info", {})
    if idle_pool_info:
        safe_print("\n  💤 Idle Worker Pool:")
        for py_exe, count in idle_pool_info.items():
            import re
            py_ver = "?"
            m = re.search(r"cpython[\-_](3\.\d+)", py_exe, re.IGNORECASE)
            if m:
                py_ver = m.group(1)
            else:
                m = re.search(r"python(3\.\d+)", py_exe, re.IGNORECASE)
                if m:
                    py_ver = m.group(1)
            safe_print(f"    - Python {py_ver}: {count} idle worker(s)")

    print("=" * 60 + "\n")


def cli_logs(follow: bool = False, tail_lines: int = 50):
    """View or follow the daemon logs."""
    log_path = Path(DAEMON_LOG_FILE)
    if not log_path.exists():
        safe_print(_('❌ Log file not found at: {}').format(log_path))
        print(_('   (The daemon might not have started yet)'))
        return

    safe_print(_('📄 Tailing {} (last {} lines)...').format(log_path, tail_lines))
    print("-" * 60)

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # 1. Efficiently read last N lines
            f.seek(0, 2)
            file_size = f.tell()

            # Heuristic: average line ~150 bytes
            block_size = max(4096, tail_lines * 200)

            if file_size > block_size:
                f.seek(file_size - block_size)
                f.readline()  # Discard potential partial line
            else:
                f.seek(0)

            # Print the tail
            lines = f.readlines()
            for line in lines[-tail_lines:]:
                print(line, end="")

            # 2. Follow mode (tail -f)
            if follow:
                print("-" * 60)
                safe_print("📡 Following logs... (Ctrl+C to stop)")

                f.seek(0, 2)  # Seek to end

                while True:
                    line = f.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        time.sleep(0.1)

    except KeyboardInterrupt:
        safe_print("\n🛑 Stopped following logs.")
    except Exception as e:
        safe_print(_('\n❌ Error reading logs: {}').format(e))

def cli_idle_config(python_version: str = None, count: int = None):
    """Configure idle worker pools."""
    from omnipkg.dispatcher import resolve_python_path
    
    client = DaemonClient()
    
    if python_version is None and count is None:
        # Show current config
        result = client.get_idle_config()
        if result.get("success"):
            safe_print("\n📊 Current Idle Worker Configuration:")
            safe_print("=" * 60)
            config = result.get("config", {})
            if not config:
                safe_print("   No idle workers configured.")
                safe_print("\n💡 Example: 8pkg daemon idle --python 3.11 --count 5")
            else:
                for py_exe, target_count in sorted(config.items()):
                    # Extract version from path for display
                    version = "unknown"
                    # Try to extract from cpython-X.Y.Z in the path
                    match = re.search(r'cpython-(\d+\.\d+\.\d+[a-z0-9]*)', py_exe)
                    if match:
                        version = match.group(1)
                    else:
                        # Fallback to executable name
                        if "python3." in py_exe:
                            version = py_exe.split("python3.")[1].split("/")[0]
                            version = f"3.{version}"
                    safe_print(f"   Python {version}: {target_count} worker(s)")
                    safe_print(f"     └─ {py_exe}")
            safe_print("=" * 60 + "\n")
        else:
            safe_print(_('❌ Error: {}').format(result.get('error')))
        return
    
    if python_version and count is not None:
        # Resolve version to actual path
        try:
            python_path = resolve_python_path(python_version)
            python_exe = str(python_path)
        except Exception as e:
            safe_print(f"❌ Could not find Python {python_version}: {e}")
            safe_print("💡 Use '8pkg list python' to see available versions")
            return
        
        # Set config
        result = client.set_idle_config(python_exe, count)
        if result.get("success"):
            if count == 0:
                safe_print(f"✅ Disabled idle workers for Python {python_version}")
            else:
                safe_print(f"✅ Set {count} idle worker(s) for Python {python_version}")
                safe_print(f"   └─ Using: {python_exe}")
        else:
            safe_print(_('❌ Error: {}').format(result.get('error')))
    else:
        safe_print("❌ Both --python and --count are required")
        safe_print("💡 Examples:")
        safe_print("   8pkg daemon idle                    # View current config")
        safe_print("   8pkg daemon idle --python 3.11 --count 5")
        safe_print("   8pkg daemon idle --python 3.12 --count 0  # Disable")


# ═══════════════════════════════════════════════════════════════
# CLI ENTRY
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 🔥 CRITICAL: Check if we're a daemon child on Windows FIRST
    if IS_WINDOWS and os.environ.get("OMNIPKG_DAEMON_CHILD") == "1":
        try:
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[DEBUG] OMNIPKG_DAEMON_CHILD detected - starting daemon (daemonize=False)\n")
                f.flush()
            daemon = WorkerPoolDaemon(max_workers=10, max_idle_time=300, warmup_specs=[])
            daemon.start(daemonize=False)
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[DEBUG] daemon.start() returned, exiting now\n")
                f.flush()
        except Exception as e:
            with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[ERROR] Daemon child crashed: {e}\n")
                import traceback
                traceback.print_exc(file=f)
                f.flush()
        with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[DEBUG] sys.exit(0) from daemon child\n")
            f.flush()
        sys.exit(0)

    if len(sys.argv) < 2:
        print(_('Usage: python -m omnipkg.isolation.worker_daemon {start|stop|status|logs}'))
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        # 🔥 FIX: Check for --no-fork flag (Windows internal use)
        no_fork = "--no-fork" in sys.argv

        if no_fork:
            # Direct start without fork (for Windows subprocess spawn)
            daemon = WorkerPoolDaemon(max_workers=10, max_idle_time=300, warmup_specs=[])
            daemon.start(daemonize=False)
        else:
            cli_start()

    elif cmd == "stop":
        cli_stop()
    elif cmd == "status":
        cli_status()
    elif cmd == "logs":
        follow = "-f" in sys.argv or "--follow" in sys.argv
        cli_logs(follow=follow)
    elif cmd == "monitor":
        watch = "-w" in sys.argv or "--watch" in sys.argv
        try:
            from omnipkg.isolation.resource_monitor import start_monitor

            start_monitor(watch_mode=watch)
        except ImportError:
            # Fallback for direct execution without package context
            try:
                from resource_monitor import start_monitor

                start_monitor(watch_mode=watch)
            except ImportError:
                safe_print(_('❌ resource_monitor module not found.'))
                sys.exit(1)
    else:
        print(_('Unknown command: {}').format(cmd))
        sys.exit(1)