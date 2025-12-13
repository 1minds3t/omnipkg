from __future__ import annotations
import os
import sys
import json
import tempfile
import time
import socket
import signal
# import psutil  # Made lazy
import threading
import glob
import subprocess
import select
from pathlib import Path
from typing import Dict, Optional, Any, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import traceback
from collections import deque
import ctypes
import base64  # <--- ENSURE THIS IS HERE

# ═══════════════════════════════════════════════════════════════
# 0. CONSTANTS & UTILITIES
# ═══════════════════════════════════════════════════════════════

DEFAULT_SOCKET = "/tmp/omnipkg_daemon.sock"
PID_FILE = "/tmp/omnipkg_daemon.pid"
SHM_REGISTRY_FILE = "/tmp/omnipkg_shm_registry.json"
DAEMON_LOG_FILE = "/tmp/omnipkg_daemon.log"

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
            if rtype == 'shared_memory':
                return
            return _orig_register(name, rtype)

        def hft_unregister(name, rtype):
            if rtype == 'shared_memory':
                return
            return _orig_unregister(name, rtype)

        # Apply patch
        resource_tracker.register = hft_register
        resource_tracker.unregister = hft_unregister

    # Apply immediately
    _hft_ignore_shm_tracking()

except ImportError:
    pass

def send_json(sock: socket.socket, data: dict, timeout: float = 30.0):
    """Sends a JSON dictionary over a socket with timeout protection."""
    sock.settimeout(timeout)
    json_string = json.dumps(data)
    length_prefix = len(json_string).to_bytes(8, 'big')
    sock.sendall(length_prefix + json_string.encode('utf-8'))

def recv_json(sock: socket.socket, timeout: float = 30.0) -> dict:
    """Receives a JSON dictionary over a socket with timeout protection."""
    sock.settimeout(timeout)
    length_prefix = sock.recv(8)
    if not length_prefix:
        raise ConnectionResetError("Socket closed by peer.")
    length = int.from_bytes(length_prefix, 'big')
    data_buffer = bytearray()
    while len(data_buffer) < length:
        chunk = sock.recv(min(length - len(data_buffer), 8192))
        if not chunk:
            raise ConnectionResetError("Socket stream interrupted.")
        data_buffer.extend(chunk)
    return json.loads(data_buffer.decode('utf-8'))

import ctypes
import glob
import os

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
            torch_lib = os.path.join(os.path.dirname(torch.__file__), 'lib')
            candidates.extend(glob.glob(os.path.join(torch_lib, 'libcudart.so*')))
        except: 
            pass
        
        # Try conda environment
        if 'CONDA_PREFIX' in os.environ:
            candidates.extend(glob.glob(
                os.path.join(os.environ['CONDA_PREFIX'], 'lib', 'libcudart.so*')
            ))
        
        # Try system libraries
        candidates.extend(['libcudart.so.12', 'libcudart.so.11.0', 'libcudart.so'])
        
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
        import base64
        
        lib = UniversalGpuIpc.get_lib()
        ptr = tensor.data_ptr()
        
        # Define CUDA structures
        class cudaPointerAttributes(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int), 
                ("device", ctypes.c_int), 
                ("devicePointer", ctypes.c_void_p), 
                ("hostPointer", ctypes.c_void_p)
            ]
        
        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]
        
        # Set function signatures
        lib.cudaPointerGetAttributes.argtypes = [
            ctypes.POINTER(cudaPointerAttributes), 
            ctypes.c_void_p
        ]
        lib.cudaIpcGetMemHandle.argtypes = [
            ctypes.POINTER(cudaIpcMemHandle_t), 
            ctypes.c_void_p
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
            "handle": base64.b64encode(handle_bytes).decode('ascii'),  # JSON-safe!
            "offset": offset,
            "shape": tuple(tensor.shape),
            "typestr": "<f4",
            "device": tensor.device.index or 0
        }
    
    @staticmethod
    def load(data):
        """
        Load a CUDA tensor from IPC metadata.
        Returns PyTorch tensor pointing to shared GPU memory.
        """
        import base64
        
        lib = UniversalGpuIpc.get_lib()
        
        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]
        
        lib.cudaIpcOpenMemHandle.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), 
            cudaIpcMemHandle_t, 
            ctypes.c_uint
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
                    "version": 3
                }
        
        # PyTorch can consume __cuda_array_interface__
        return torch.as_tensor(
            CUDABuffer(final_ptr, data["shape"], data["typestr"]), 
            device=f"cuda:{data['device']}"
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
                with open(SHM_REGISTRY_FILE, 'r') as f:
                    self.active_blocks = set(json.load(f))
        except:
            self.active_blocks = set()
    
    def _save_registry(self):
        try:
            with open(SHM_REGISTRY_FILE, 'w') as f:
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

_DAEMON_SCRIPT = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 🔥 CUDA PRE-INJECTION (BEFORE ANY IMPORTS)
# ═══════════════════════════════════════════════════════════
try:
    import site
    site_packages = Path(site.getsitepackages()[0])
    multiversion_base = site_packages / '.omnipkg_versions'
    
    cuda_lib_paths = []
    for nvidia_bubble in multiversion_base.glob('nvidia-*-cu12-*'):
        if nvidia_bubble.is_dir():
            nvidia_dir = nvidia_bubble / 'nvidia'
            if nvidia_dir.exists():
                for module_dir in nvidia_dir.iterdir():
                    if module_dir.is_dir():
                        lib_dir = module_dir / 'lib'
                        if lib_dir.exists():
                            cuda_lib_paths.append(str(lib_dir))
    
    if cuda_lib_paths:
        current_ld = os.environ.get('LD_LIBRARY_PATH', '')
        new_ld = os.pathsep.join(cuda_lib_paths) + (os.pathsep + current_ld if current_ld else '')
        os.environ['LD_LIBRARY_PATH'] = new_ld
        sys.stderr.write(f'🔧 [PRE-INIT] Set LD_LIBRARY_PATH with {len(cuda_lib_paths)} CUDA paths\\n')
        sys.stderr.flush()
except Exception as e:
    sys.stderr.write(f'⚠️  [PRE-INIT] CUDA setup failed: {e}\\n')
    sys.stderr.flush()

# NOW proceed with normal imports
import json
import shutil
from pathlib import Path

# CRITICAL: Mark as daemon worker
os.environ['OMNIPKG_IS_DAEMON_WORKER'] = '1'
os.environ['OMNIPKG_DISABLE_WORKER_POOL'] = '1'

sys.stdin.reconfigure(line_buffering=True)

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

try:
    input_line = sys.stdin.readline()
    if not input_line:
        fatal_error('No input received on stdin')
    
    setup_data = json.loads(input_line.strip())
    PKG_SPEC = setup_data.get('package_spec')
    
    if not PKG_SPEC:
        fatal_error('Missing package_spec')
except Exception as e:
    fatal_error('Startup configuration failed', e)

try:
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    fatal_error('Failed to import omnipkgLoader', e)

# ═══════════════════════════════════════════════════════════════
# CRITICAL FIX: Force Non-Nested Context
# ═══════════════════════════════════════════════════════════════
if hasattr(omnipkgLoader, '_nesting_depth'):
    omnipkgLoader._nesting_depth = 0

# ═══════════════════════════════════════════════════════════════
# STARTUP: ONE-TIME ACTIVATION + CLEANUP
# ═══════════════════════════════════════════════════════════════
try:
    specs = [s.strip() for s in PKG_SPEC.split(',')]
    loaders = []
    
    for s in specs:
        l = omnipkgLoader(s, isolation_mode='overlay')
        l.__enter__()
        loaders.append(l)

    # ═══════════════════════════════════════════════════════════
    # 🔥 CUDA LIBRARY PATH INJECTION (CORRECT PATTERN)
    # ═══════════════════════════════════════════════════════════
    cuda_lib_paths = []
    
    if loaders and hasattr(loaders[0], 'multiversion_base'):
        from pathlib import Path
        multiversion_base = Path(loaders[0].multiversion_base)
        
        # Pattern: nvidia-{name}-cu12-{version}/nvidia/{module}/lib/
        for nvidia_bubble in multiversion_base.glob('nvidia-*-cu12-*'):
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
        
        sys.stderr.write(f'🔧 [DAEMON] Injected {len(cuda_lib_paths)} CUDA library paths\\n')
        sys.stderr.flush()
        
        for path in cuda_lib_paths[:5]:
            sys.stderr.write(f'   - {path}\\n')
            sys.stderr.flush()
        
        # Pre-load libcudart
        import ctypes
        for lib_path in cuda_lib_paths:
            cudart = Path(lib_path) / 'libcudart.so.12'
            if cudart.exists():
                try:
                    ctypes.CDLL(str(cudart))
                    sys.stderr.write(f'   ✅ Pre-loaded: {cudart.name}\\n')
                    sys.stderr.flush()
                    break
                except:
                    pass
    else:
        sys.stderr.write('ℹ️  [DAEMON] No CUDA libraries found\\n')
        sys.stderr.flush()
    
    ### POST LD PATH INJECTION ###

    globals()['_omnipkg_loaders'] = loaders
    
    sys.stderr.write('🧹 [DAEMON] Starting immediate post-activation cleanup...\\n')
    sys.stderr.flush()
    
    cleanup_count = 0
    
    for loader in loaders:
        # Restore main env cloaks
        if hasattr(loader, '_cloaked_main_modules') and loader._cloaked_main_modules:
            sys.stderr.write(f'   🔓 Restoring {len(loader._cloaked_main_modules)} main env cloaks...\\n')
            sys.stderr.flush()
            
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
                    sys.stderr.write(f'      ✅ Restored: {original_path.name}\\n')
                    sys.stderr.flush()
                except Exception as e:
                    sys.stderr.write(f'      ⚠️  Failed: {original_path.name}: {e}\\n')
                    sys.stderr.flush()
            
            loader._cloaked_main_modules.clear()
        
        # Restore bubble cloaks
        if hasattr(loader, '_cloaked_bubbles') and loader._cloaked_bubbles:
            sys.stderr.write(f'   🔓 Restoring {len(loader._cloaked_bubbles)} bubble cloaks...\\n')
            sys.stderr.flush()
            
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
                        sys.stderr.write(f'      ✅ Restored: {original_path.name}\\n')
                        sys.stderr.flush()
                except Exception as e:
                    sys.stderr.write(f'      ⚠️  Failed: {original_path.name}: {e}\\n')
                    sys.stderr.flush()
            
            loader._cloaked_bubbles.clear()
        
        # Clean up global tracking
        if hasattr(loader, '_my_main_env_package') and loader._my_main_env_package:
            if hasattr(omnipkgLoader, '_active_main_env_packages'):
                omnipkgLoader._active_main_env_packages.discard(loader._my_main_env_package)
        
        # Clear global cloak registry
        if hasattr(omnipkgLoader, '_active_cloaks_lock') and hasattr(omnipkgLoader, '_active_cloaks'):
            with omnipkgLoader._active_cloaks_lock:
                loader_id = id(loader)
                cloaks_to_remove = []
                for cloak_path_str, owner_id in list(omnipkgLoader._active_cloaks.items()):
                    if owner_id == loader_id:
                        cloaks_to_remove.append(cloak_path_str)
                
                for cloak_path_str in cloaks_to_remove:
                    omnipkgLoader._active_cloaks.pop(cloak_path_str, None)
    
    sys.stderr.write(f'✅ [DAEMON] Cleanup complete! Restored {cleanup_count} items\\n')
    sys.stderr.flush()
    
except Exception as e:
    fatal_error(f'Failed to activate {PKG_SPEC}', e)

_devnull.close()
sys.stdout = _original_stdout
sys.stdout.reconfigure(line_buffering=True)

import ctypes
import glob
import os

class UniversalGpuIpc:
    _lib = None
    
    @classmethod
    def get_lib(cls):
        if cls._lib: 
            return cls._lib
        
        candidates = []
        
        # Try PyTorch's lib directory (if torch is installed)
        try:
            import torch
            torch_lib = os.path.join(os.path.dirname(torch.__file__), 'lib')
            candidates.extend(glob.glob(os.path.join(torch_lib, 'libcudart.so*')))
        except: 
            pass
        
        # Try conda environment
        if 'CONDA_PREFIX' in os.environ:
            candidates.extend(glob.glob(
                os.path.join(os.environ['CONDA_PREFIX'], 'lib', 'libcudart.so*')
            ))
        
        # Try system libraries
        candidates.extend(['libcudart.so.12', 'libcudart.so.11.0', 'libcudart.so'])
        
        for lib in candidates:
            try:
                cls._lib = ctypes.CDLL(lib)
                return cls._lib
            except: 
                continue
        
        raise RuntimeError("Could not load libcudart.so - CUDA not available")
    
    @staticmethod
    def share(tensor):
        import base64
        
        lib = UniversalGpuIpc.get_lib()
        ptr = tensor.data_ptr()
        
        # Define CUDA structures
        class cudaPointerAttributes(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int), 
                ("device", ctypes.c_int), 
                ("devicePointer", ctypes.c_void_p), 
                ("hostPointer", ctypes.c_void_p)
            ]
        
        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]
        
        # Set function signatures
        lib.cudaPointerGetAttributes.argtypes = [
            ctypes.POINTER(cudaPointerAttributes), 
            ctypes.c_void_p
        ]
        lib.cudaIpcGetMemHandle.argtypes = [
            ctypes.POINTER(cudaIpcMemHandle_t), 
            ctypes.c_void_p
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
            "handle": base64.b64encode(handle_bytes).decode('ascii'),
            "offset": offset,
            "shape": tuple(tensor.shape),
            "typestr": "<f4",
            "device": tensor.device.index or 0
        }
    
    @staticmethod
    def load(data):
        import base64
        
        lib = UniversalGpuIpc.get_lib()
        
        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]
        
        lib.cudaIpcOpenMemHandle.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), 
            cudaIpcMemHandle_t, 
            ctypes.c_uint
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
            def __init__(self, ptr, shape, typestr):
                self.__cuda_array_interface__ = {
                    "data": (ptr, False),
                    "shape": shape,
                    "typestr": typestr,
                    "version": 3
                }
        
        # PyTorch can consume __cuda_array_interface__
        return torch.as_tensor(
            CUDABuffer(final_ptr, data["shape"], data["typestr"]), 
            device=f"cuda:{data['device']}"
        )

_universal_gpu_ipc_available = False

try:
    # Test if we can load CUDA runtime
    UniversalGpuIpc.get_lib()
    _universal_gpu_ipc_available = True
    sys.stderr.write('🔥🔥🔥 [DAEMON] UNIVERSAL CUDA IPC ENABLED (ctypes - NO PYTORCH NEEDED)\\n')
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write(f'⚠️  [DAEMON] Universal CUDA IPC unavailable: {e}\\n')
    sys.stderr.flush()

# ═══════════════════════════════════════════════════════════════
# NOW CHECK GPU IPC AFTER LOADER ACTIVATION (SEES BUBBLE TORCH)
# ═══════════════════════════════════════════════════════════════
from multiprocessing import shared_memory
from contextlib import redirect_stdout, redirect_stderr
import io
import numpy as np
import base64

_gpu_ipc_available = False
_torch_available = False
_cuda_available = False
_native_ipc_mode = False

try:
    import tensorflow as tf
    # Prevent TensorFlow from hogging all VRAM
    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        sys.stderr.write('✅ [DAEMON] TensorFlow GPU memory growth enabled\\n')
except Exception as e:
    pass

try:
    import torch
    _torch_available = True
    _cuda_available = torch.cuda.is_available()
    
    sys.stderr.write(f'🔍 [DAEMON] Detected PyTorch version: {torch.__version__}\\n')
    sys.stderr.flush()
    
    if _cuda_available:
        # Check for PyTorch 1.x native CUDA IPC
        torch_version = torch.__version__.split('+')[0]
        major = int(torch_version.split('.')[0])
        
        sys.stderr.write(f'🔍 [DAEMON] PyTorch major version: {major}\\n')
        sys.stderr.flush()
        
        if major == 1:
            sys.stderr.write(f'🔍 [DAEMON] Checking for native CUDA IPC support...\\n')
            sys.stderr.flush()
            
            try:
                # Test if we can use native CUDA IPC via storage._share_cuda_()
                test_tensor = torch.zeros(1).cuda()
                test_storage = test_tensor.storage()
                
                if hasattr(test_storage, '_share_cuda_'):
                    # Try to get IPC handle
                    ipc_data = test_storage._share_cuda_()
                    if len(ipc_data) == 8:
                        _native_ipc_mode = True
                        _gpu_ipc_available = True
                        sys.stderr.write('🔥🔥🔥 [DAEMON] NATIVE CUDA IPC ENABLED (PyTorch 1.x - TRUE ZERO-COPY)\\n')
                        sys.stderr.flush()
                    else:
                        sys.stderr.write(f'⚠️  [DAEMON] _share_cuda_() returned unexpected data: {len(ipc_data)} elements\\n')
                        sys.stderr.flush()
                else:
                    _gpu_ipc_available = True
                    sys.stderr.write('⚠️  [DAEMON] PyTorch 1.x but _share_cuda_() not available\\n')
                    sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f'⚠️  [DAEMON] CUDA IPC test failed: {e}\\n')
                sys.stderr.flush()
        else:
            # PyTorch 2.x - hybrid mode only
            sys.stderr.write(f'ℹ️  [DAEMON] PyTorch {major}.x - using hybrid mode\\n')
            sys.stderr.flush()
            try:
                test_tensor = torch.zeros(1).cuda()
                test_tensor.share_memory_()
                _gpu_ipc_available = True
                sys.stderr.write('🚀 [DAEMON] GPU IPC available via PyTorch CUDA (hybrid mode)\\n')
                sys.stderr.flush()
            except:
                sys.stderr.write('⚠️  [DAEMON] PyTorch CUDA detected but IPC unavailable\\n')
                sys.stderr.flush()
except ImportError as e:
    sys.stderr.write(f'⚠️  [DAEMON] Failed to import torch: {e}\\n')
    sys.stderr.flush()

if not _gpu_ipc_available:
    sys.stderr.write('ℹ️  [DAEMON] Running in CPU-only mode (standard SHM)\\n')
    sys.stderr.flush()

try:
    ready_msg = {'status': 'READY', 'package': PKG_SPEC, 'native_ipc': _native_ipc_mode}
    print(json.dumps(ready_msg), flush=True)
except Exception as e:
    sys.stderr.write(f"ERROR: Failed to send READY: {e}\\n")
    sys.stderr.flush()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# MAIN EXECUTION LOOP
# ═══════════════════════════════════════════════════════════════

while True:
    try:
        command_line = sys.stdin.readline()
        if not command_line:
            break
        
        command_line = command_line.strip()
        if not command_line:
            continue
        
        command = json.loads(command_line)
        
        if command.get('type') == 'shutdown':
            break
        
        task_id = command.get('task_id', 'UNKNOWN')
        worker_code = command.get('code', '')
        exec_scope = {'input_data': command}
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
        
        # UNIVERSAL IPC OUTPUT
        if out_meta and is_cuda_request and _universal_gpu_ipc_available and 'universal_ipc' in out_meta:
            try:
                # Load tensor using universal IPC
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
            shm_name = out_meta.get('shm_name') or out_meta.get('name')
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
        
        if _torch_available:
            exec_scope['torch'] = torch
        exec_scope['np'] = np
        
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
            
            print(json.dumps(result), flush=True)
            
        except Exception as e:
            import traceback
            error_response = {
                'status': 'ERROR',
                'task_id': task_id,
                'error': f'{e.__class__.__name__}: {str(e)}',
                'traceback': traceback.format_exc(),
                'success': False
            }
            print(json.dumps(error_response), flush=True)
        finally:
            for shm in shm_blocks:
                try:
                    shm.close()
                except:
                    pass
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
        print(json.dumps(error_response), flush=True)

# Cleanup on exit
"""

# Additional diagnostic helper for debugging
def diagnose_worker_issue(package_spec: str):
    """
    Run this to diagnose why a worker might return the wrong version.
    """
    print(f"\n🔍 Diagnosing worker issue for: {package_spec}")
    print("=" * 70)
    
    pkg_name, expected_version = package_spec.split('==')
    
    # Check what's in sys.path
    print("\n1. Current sys.path:")
    import sys
    for i, path in enumerate(sys.path):
        print(f"   [{i}] {path}")
    
    # Check what version is importable
    print(f"\n2. Attempting to import {pkg_name}:")
    try:
        from importlib.metadata import version
        actual_version = version(pkg_name)
        print(f"   ✅ Found version: {actual_version}")
        
        if actual_version != expected_version:
            print(f"   ❌ VERSION MISMATCH!")
            print(f"      Expected: {expected_version}")
            print(f"      Got: {actual_version}")
    except Exception as e:
        print(f"   ❌ Import failed: {e}")
    
    # Check for bubble
    from pathlib import Path
    site_packages = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    bubble_path = site_packages / '.omnipkg_versions' / f'{pkg_name}-{expected_version}'
    
    print(f"\n3. Bubble check:")
    print(f"   Path: {bubble_path}")
    print(f"   Exists: {bubble_path.exists()}")
    
    if bubble_path.exists():
        print(f"   Contents: {list(bubble_path.glob('*'))[:5]}")
    
    print("\n" + "=" * 70)

# ═══════════════════════════════════════════════════════════════
# 2. WORKER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class PersistentWorker:
    def __init__(self, package_spec: str, python_exe: str = None, verbose: bool = False):
        self.package_spec = package_spec
        self.python_exe = python_exe or sys.executable # <--- STORE IT
        self.package_spec = package_spec
        self.process: Optional[subprocess.Popen] = None
        self.temp_file: Optional[str] = None
        self.lock = threading.RLock()  # Per-worker lock
        self.last_health_check = time.time()
        self.health_check_failures = 0
        self._start_worker()

    def wait_for_ready_with_activity_monitoring(process, timeout_idle_seconds=30.0):
        """
        Wait for worker READY signal while monitoring actual process activity.
        Only timeout if the process is ACTUALLY idle (no CPU/memory activity).
        
        Args:
            process: subprocess.Popen instance
            timeout_idle_seconds: How long to wait if process shows NO activity
        
        Returns:
            ready_line: The READY JSON line from stdout
        
        Raises:
            RuntimeError: If process is idle for too long or crashes
        """
        import psutil
        start_time = time.time()
        last_activity_time = start_time
        last_cpu_percent = 0.0
        last_memory_mb = 0.0
        
        try:
            ps_process = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            raise RuntimeError("Worker process died immediately after spawn")
        
        stderr_lines = []
        
        while True:
            # Check if process is still alive
            if process.poll() is not None:
                stderr_output = ''.join(stderr_lines)
                raise RuntimeError(f"Worker crashed during startup. Stderr: {stderr_output}")
            
            # Check for READY on stdout (non-blocking)
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            if ready:
                ready_line = process.stdout.readline()
                if ready_line:
                    return ready_line
            
            # Collect stderr (non-blocking)
            err_ready, _, _ = select.select([process.stderr], [], [], 0.0)
            if err_ready:
                line = process.stderr.readline()
                if line:
                    stderr_lines.append(line)
            
            # Monitor process activity
            try:
                cpu_percent = ps_process.cpu_percent(interval=0.1)
                memory_mb = ps_process.memory_info().rss / 1024 / 1024
                
                # Detect activity: CPU usage or memory growth
                activity_detected = False
                
                if cpu_percent > 1.0:  # More than 1% CPU usage
                    activity_detected = True
                
                if memory_mb > last_memory_mb + 1.0:  # Memory grew by >1MB
                    activity_detected = True
                
                if activity_detected:
                    last_activity_time = time.time()
                    last_cpu_percent = cpu_percent
                    last_memory_mb = memory_mb
                
                # Check idle timeout
                idle_duration = time.time() - last_activity_time
                
                if idle_duration > timeout_idle_seconds:
                    stderr_output = ''.join(stderr_lines)
                    raise RuntimeError(
                        f"Worker startup timeout: No activity for {idle_duration:.1f}s\n"
                        f"Last CPU: {last_cpu_percent:.1f}%, Last Memory: {last_memory_mb:.1f}MB\n"
                        f"Stderr: {stderr_output if stderr_output else 'empty'}"
                    )
            
            except psutil.NoSuchProcess:
                raise RuntimeError("Worker process disappeared during startup")
            
            # Small sleep to avoid busy-waiting
            time.sleep(0.1)
        
    def execute_with_activity_monitoring(worker_process, task_id, code, shm_in, shm_out, 
                                        timeout_idle_seconds=30.0, max_total_time=600.0):
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
        import json
        
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
            "shm_out": shm_out
        }
        
        worker_process.stdin.write(json.dumps(command) + '\n')
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
            
            # Check for response (non-blocking)
            ready, _, _ = select.select([worker_process.stdout], [], [], 0.1)
            if ready:
                response_line = worker_process.stdout.readline()
                if response_line:
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
                if hasattr(execute_with_activity_monitoring, '_last_io'):
                    last_io = execute_with_activity_monitoring._last_io
                    if (io_counters.read_bytes > last_io.read_bytes or 
                        io_counters.write_bytes > last_io.write_bytes):
                        activity_detected = True
                execute_with_activity_monitoring._last_io = io_counters
                
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
        Checks bubbles for nvidia-* packages.
        """
        from pathlib import Path
        
        cuda_paths = []
        
        # Parse package name from spec
        pkg_name = self.package_spec.split('==')[0] if '==' in self.package_spec else self.package_spec
        
        # Get the multiversion base (bubble directory)
        try:
            # Import here to avoid circular dependency
            from omnipkg.loader import omnipkgLoader
            loader = omnipkgLoader(package_spec=self.package_spec, quiet=True)
            multiversion_base = loader.multiversion_base
        except Exception:
            # Fallback: try to find it in sys.path
            import site
            site_packages = Path(site.getsitepackages()[0])
            multiversion_base = site_packages / '.omnipkg_versions'
        
        if not multiversion_base.exists():
            return cuda_paths
        
        # Strategy 1: Check the main package bubble for NVIDIA dependencies
        _, version = self.package_spec.split('==') if '==' in self.package_spec else (pkg_name, None)
        if version:
            main_bubble = multiversion_base / f"{pkg_name}-{version}"
            if main_bubble.exists():
                # Scan for symlinked or nested nvidia packages
                for nvidia_dir in main_bubble.glob('nvidia_*'):
                    if nvidia_dir.is_dir():
                        # Check for lib subdirectory
                        lib_dir = nvidia_dir / 'lib'
                        if lib_dir.exists():
                            cuda_paths.append(str(lib_dir))
                        # Also check for .so files directly in package dir
                        if list(nvidia_dir.glob('*.so*')):
                            cuda_paths.append(str(nvidia_dir))
        
        # Strategy 2: Check for standalone NVIDIA bubbles
        # These are the separate nvidia-cuda-runtime-cu12-12.1.105 bubbles
        nvidia_bubble_patterns = [
            'nvidia-cuda-runtime-cu12-*',
            'nvidia-cudnn-cu12-*',
            'nvidia-cublas-cu12-*',
            'nvidia-cufft-cu12-*',
            'nvidia-cusolver-cu12-*',
            'nvidia-cusparse-cu12-*',
        ]
        
        for pattern in nvidia_bubble_patterns:
            for nvidia_bubble in multiversion_base.glob(pattern):
                if nvidia_bubble.is_dir() and '_omnipkg_cloaked' not in nvidia_bubble.name:
                    # Find the package directory inside
                    pkg_dir_name = nvidia_bubble.name.split('-')[0:3]  # e.g., ['nvidia', 'cuda', 'runtime']
                    pkg_dir_name = '_'.join(pkg_dir_name)  # 'nvidia_cuda_runtime'
                    
                    pkg_dir = nvidia_bubble / pkg_dir_name
                    if pkg_dir.exists():
                        # Check for lib subdirectory
                        lib_dir = pkg_dir / 'lib'
                        if lib_dir.exists():
                            cuda_paths.append(str(lib_dir))
                        # Check for .so files in package dir
                        if list(pkg_dir.glob('*.so*')):
                            cuda_paths.append(str(pkg_dir))
        
        return cuda_paths

    def _start_worker(self):
        """Start worker process with proper error handling."""
        # CRITICAL DEBUG: Check _DAEMON_SCRIPT before writing
        print(f"\n🔍 DEBUG: _DAEMON_SCRIPT length: {len(_DAEMON_SCRIPT)} chars", file=sys.stderr)
        print(f"🔍 DEBUG: Last 200 chars of _DAEMON_SCRIPT:", file=sys.stderr)
        print(f"   '{_DAEMON_SCRIPT[-200:]}'", file=sys.stderr)
        
        # Create temp script file
        with tempfile.NamedTemporaryFile(
            mode='w', 
            delete=False, 
            suffix=f"_{self.package_spec.replace('=', '_').replace('==', '_')}.py"
        ) as f:
            f.write(_DAEMON_SCRIPT)
            self.temp_file = f.name
        
        # CRITICAL DEBUG: Print the temp file path and validate syntax
        print(f"\n🔍 DEBUG: Worker script written to: {self.temp_file}", file=sys.stderr)
        print(f"🔍 DEBUG: File size: {os.path.getsize(self.temp_file)} bytes", file=sys.stderr)
        
        # Validate syntax before running
        try:
            with open(self.temp_file, 'r') as f:
                script_content = f.read()
            compile(script_content, self.temp_file, 'exec')
            print(f"✅ DEBUG: Script syntax is valid", file=sys.stderr)
        except SyntaxError as e:
            print(f"\n💥 SYNTAX ERROR IN GENERATED SCRIPT!", file=sys.stderr)
            print(f"   File: {self.temp_file}", file=sys.stderr)
            print(f"   Line {e.lineno}: {e.msg}", file=sys.stderr)
            print(f"\n📄 SCRIPT CONTENT (last 50 lines):", file=sys.stderr)
            with open(self.temp_file, 'r') as f:
                lines = f.readlines()
                start_line = max(0, len(lines) - 50)
                for i, line in enumerate(lines[start_line:], start=start_line + 1):
                    marker = " ⚠️ " if i == e.lineno else "    "
                    print(f"{marker}{i:3d}: {line.rstrip()}", file=sys.stderr)
            raise RuntimeError(f"Generated script has syntax error at line {e.lineno}: {e.msg}")

        env = os.environ.copy()
        current_pythonpath = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = f"{os.getcwd()}{os.pathsep}{current_pythonpath}"
        
        # ═══════════════════════════════════════════════════════════
        # 🔥 NEW: INJECT CUDA LIBRARY PATHS BEFORE SPAWN
        # ═══════════════════════════════════════════════════════════
        cuda_lib_paths = self._discover_cuda_paths()
        if cuda_lib_paths:
            current_ld = env.get('LD_LIBRARY_PATH', '')
            new_ld = os.pathsep.join(cuda_lib_paths)
            if current_ld:
                new_ld = new_ld + os.pathsep + current_ld
            env['LD_LIBRARY_PATH'] = new_ld
            
            print(f"🔧 [WORKER] Injecting {len(cuda_lib_paths)} CUDA paths into environment", file=sys.stderr)
            for path in cuda_lib_paths:
                print(f"   - {path}", file=sys.stderr)
        
        # Open daemon log for worker stderr (store as instance variable)
        self.log_file = open(DAEMON_LOG_FILE, 'a', buffering=1)

        self.process = subprocess.Popen(
            [self.python_exe, '-u', self.temp_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log_file,
            text=True,
            bufsize=0,
            env=env,  # ← Use modified env
            preexec_fn=os.setsid
        )
        
        # Send setup command
        try:
            setup_cmd = json.dumps({'package_spec': self.package_spec})
            self.process.stdin.write(setup_cmd + '\n')
            self.process.stdin.flush()
        except Exception as e:
            self.force_shutdown()
            raise RuntimeError(f"Failed to send setup: {e}")
        
        # Wait for READY with timeout
        try:
            # ONLY check stdout now (stderr is going to log file)
            readable, _, _ = select.select([self.process.stdout], [], [], 30.0)
            
            ready_line = None

            # Read stdout
            if readable:
                ready_line = self.process.stdout.readline()

            if not ready_line:
                # Check if process died
                if self.process.poll() is not None:
                    raise RuntimeError(f"Worker crashed during startup (check {DAEMON_LOG_FILE})")
                raise RuntimeError(f"Worker timeout waiting for READY")
            
            ready_line = ready_line.strip()
            
            if not ready_line:
                raise RuntimeError("Worker sent blank READY line")
            
            try:
                ready_status = json.loads(ready_line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Worker sent invalid READY JSON: {repr(ready_line)}: {e}")
            
            if ready_status.get('status') != 'READY':
                raise RuntimeError(f"Worker failed to initialize: {ready_status}")
            
            # Success!
            self.last_health_check = time.time()
            self.health_check_failures = 0
            
        except Exception as e:
            self.force_shutdown()
            raise RuntimeError(f"Worker initialization failed: {e}")

    def execute_shm_task(self, task_id: str, code: str, shm_in: Dict[str, Any], 
                         shm_out: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
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
                    "shm_out": shm_out
                }
                
                self.process.stdin.write(json.dumps(command) + '\n')
                self.process.stdin.flush()
                
                # Wait for response
                readable, _, _ = select.select([self.process.stdout], [], [], timeout)
                
                if not readable:
                    raise TimeoutError(f"Task timed out after {timeout}s")
                
                response_line = self.process.stdout.readline()
                if not response_line:
                    raise RuntimeError("Worker closed connection")
                
                return json.loads(response_line.strip())
                
            except Exception as e:
                self.health_check_failures += 1
                raise

    def health_check(self) -> bool:
        """Check if worker is responsive."""
        try:
            result = self.execute_shm_task(
                "health_check",
                "result = {'status': 'ok'}",
                {}, {},
                timeout=5.0
            )
            self.last_health_check = time.time()
            self.health_check_failures = 0
            return result.get('status') == 'COMPLETED'
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
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except Exception:
                        pass
                finally:
                    self.process = None
            
            # Close log file
            if hasattr(self, 'log_file') and self.log_file:
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

class WorkerPoolDaemon:
    def __init__(self, max_workers: int = 10, max_idle_time: int = 300, warmup_specs: list = None):
        self.max_workers = max_workers
        self.max_idle_time = max_idle_time
        self.warmup_specs = warmup_specs or []
        self.workers: Dict[str, Dict[str, Any]] = {}
        self.worker_locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)  # Per-spec locks
        self.pool_lock = threading.RLock()  # Only for pool modifications
        self.running = True
        self.socket_path = DEFAULT_SOCKET
        self.stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'workers_created': 0,
            'workers_killed': 0,
            'errors': 0
        }
        self.executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="daemon-handler")
    
    def start(self, daemonize: bool = True):
        if self.is_running():
            return
        
        if daemonize:
            self._daemonize()
        
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        
        # CRITICAL FIX: Only register signals if we're in the main thread
        try:
            import threading
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, self._handle_shutdown)
                signal.signal(signal.SIGINT, self._handle_shutdown)
        except ValueError:
            # We're not in the main thread, skip signal handlers
            pass
        
        # Cleanup orphaned SHM blocks from previous runs
        shm_registry.cleanup_orphans()
        
        # Start background threads
        threading.Thread(target=self._health_monitor, daemon=True, name="health-monitor").start()
        threading.Thread(target=self._memory_manager, daemon=True, name="memory-manager").start()
        threading.Thread(target=self._warmup_workers, daemon=True, name="warmup").start()
        
        self._run_socket_server()
    
    def _daemonize(self):
        """Double-fork daemonization with visual feedback."""
        try:
            pid = os.fork()
            if pid > 0:
                # ---------------------------------------------------------
                # PARENT PROCESS: Print success and exit
                # ---------------------------------------------------------
                print(f"✅ Daemon started successfully (PID: {pid})")
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"fork #1 failed: {e}\n")
            sys.exit(1)

        # Decouple from parent environment
        os.setsid()
        os.umask(0)

        # Second fork
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"fork #2 failed: {e}\n")
            sys.exit(1)

        # Flush standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Redirect standard file descriptors
        with open('/dev/null', 'r') as f:
            os.dup2(f.fileno(), sys.stdin.fileno())
        with open(DAEMON_LOG_FILE, 'a+') as f:          # ← CHANGED TO DAEMON_LOG_FILE
            os.dup2(f.fileno(), sys.stdout.fileno())
            os.dup2(f.fileno(), sys.stderr.fileno())
    
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
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.socket_path)
        sock.listen(128)  # CRITICAL FIX: Increased backlog for high concurrency
        
        while self.running:
            try:
                sock.settimeout(1.0)
                conn, _ = sock.accept()
                # CRITICAL FIX: Use thread pool instead of unbounded threads
                self.executor.submit(self._handle_client, conn)
            except socket.timeout:
                continue
            except Exception:
                if self.running:
                    pass

    def _handle_client(self, conn: socket.socket):
        """Handle client request with timeout protection."""
        conn.settimeout(30.0)
        try:
            req = recv_json(conn, timeout=30.0)
            self.stats['total_requests'] += 1
            
            if req['type'] == 'execute':
                res = self._execute_code(
                    req['spec'], 
                    req['code'], 
                    req.get('shm_in', {}), 
                    req.get('shm_out', {}),
                    req.get('python_exe')
                )
            elif req['type'] == 'execute_cuda':  # ← ADD THIS
                res = self._execute_cuda_code(
                    req['spec'],
                    req['code'],
                    req.get('cuda_in', {}),
                    req.get('cuda_out', {}),
                    req.get('python_exe')
                )
            elif req['type'] == 'status':
                res = self._get_status()
            elif req['type'] == 'shutdown':
                self.running = False
                res = {'success': True}
            else:
                res = {'success': False, 'error': 'Unknown type'}
            
            send_json(conn, res, timeout=30.0)
        except Exception as e:
            self.stats['errors'] += 1
            try:
                send_json(conn, {'success': False, 'error': str(e)}, timeout=5.0)
            except:
                pass
        finally:
            try:
                conn.close()
            except:
                pass
    
    def _execute_code(self, spec: str, code: str, shm_in: dict, shm_out: dict, python_exe: str = None) -> dict:
        if not python_exe:
            python_exe = sys.executable
        
        worker_key = f"{spec}::{python_exe}"
        
        with self.worker_locks[worker_key]:
            # 🔥 FIX: Get worker_info inside the pool_lock
            with self.pool_lock:
                if worker_key not in self.workers:
                    if len(self.workers) >= self.max_workers:
                        self._evict_oldest_worker_async()
                    
                    try:
                        worker = PersistentWorker(spec, python_exe=python_exe)
                        self.workers[worker_key] = {
                            'worker': worker,
                            'created': time.time(),
                            'last_used': time.time(),
                            'request_count': 0,
                            'memory_mb': 0.0
                        }
                        self.stats['workers_created'] += 1
                    except Exception as e:
                        import traceback
                        error_msg = f'Worker creation failed: {e}\n{traceback.format_exc()}'
                        return {'success': False, 'error': error_msg, 'status': 'ERROR'}
                else:
                    self.stats['cache_hits'] += 1
                
                # 🔥 FIX: Always get worker_info from dict (works for both branches)
                worker_info = self.workers[worker_key]
            
            # Execute outside pool lock
            worker_info['last_used'] = time.time()
            worker_info['request_count'] += 1
            
            try:
                result = worker_info['worker'].execute_shm_task(
                    f"{spec}-{self.stats['total_requests']}",
                    code,
                    shm_in,
                    shm_out,
                    timeout=60.0
                )
                return result
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _execute_cuda_code(self, spec: str, code: str, cuda_in: dict, cuda_out: dict, python_exe: str = None) -> dict:
        """Execute code with CUDA IPC tensors."""
        if not python_exe:
            python_exe = sys.executable
        
        worker_key = f"{spec}::{python_exe}"
        
        with self.worker_locks[worker_key]:
            with self.pool_lock:
                if worker_key not in self.workers:
                    # Check capacity
                    if len(self.workers) >= self.max_workers:
                        self._evict_oldest_worker_async()
                    
                    # Create worker
                    try:
                        worker = PersistentWorker(spec, python_exe=python_exe)
                        self.workers[worker_key] = {
                            'worker': worker,
                            'created': time.time(),
                            'last_used': time.time(),
                            'request_count': 0,
                            'memory_mb': 0.0,
                            'is_gpu_worker': True,      # 🔥 FIX: Set inside dict creation
                            'gpu_timeout': 60
                        }
                        self.stats['workers_created'] += 1
                    except Exception as e:
                        import traceback
                        error_msg = f'Worker creation failed: {e}\n{traceback.format_exc()}'
                        return {'success': False, 'error': error_msg, 'status': 'ERROR'}
                else:
                    self.stats['cache_hits'] += 1
                    # 🔥 FIX: Mark existing worker as GPU worker too
                    self.workers[worker_key]['is_gpu_worker'] = True
                    self.workers[worker_key]['gpu_timeout'] = 60
                
                # 🔥 FIX: Get worker_info AFTER it's definitely in the dict
                worker_info = self.workers[worker_key]
            
            # Execute outside pool lock
            worker_info['last_used'] = time.time()
            worker_info['request_count'] += 1
            # In _execute_cuda_code, after creating worker_info dict:
            if 'tensorflow' in spec.lower():
                worker_info['is_gpu_worker'] = True
                worker_info['gpu_timeout'] = 30  # Even more aggressive for TF (30s)
                worker_info['tensorflow_worker'] = True
            try:
                # Send CUDA IPC command
                command = {
                    'type': 'execute_cuda',
                    'task_id': f"{spec}-{self.stats['total_requests']}",
                    'code': code,
                    'cuda_in': cuda_in,
                    'cuda_out': cuda_out
                }
                
                worker_info['worker'].process.stdin.write(json.dumps(command) + '\n')
                worker_info['worker'].process.stdin.flush()
                
                # Wait for response with timeout
                import select
                readable, _, _ = select.select([worker_info['worker'].process.stdout], [], [], 60.0)
                
                if not readable:
                    raise TimeoutError("CUDA task timed out after 60s")
                
                response_line = worker_info['worker'].process.stdout.readline()
                if not response_line:
                    raise RuntimeError("Worker closed connection")
                
                return json.loads(response_line.strip())
                
            except Exception as e:
                return {'success': False, 'error': str(e)}

    def _evict_oldest_worker_async(self):
        """CRITICAL FIX: Evict worker without blocking on shutdown."""
        with self.pool_lock:
            if not self.workers:
                return
            
            oldest = min(self.workers.keys(), key=lambda k: self.workers[k]['last_used'])
            worker_info = self.workers.pop(oldest)  # Remove from pool FIRST
            self.stats['workers_killed'] += 1
        
        # Shutdown in background thread (don't block)
        def async_shutdown():
            try:
                worker_info['worker'].force_shutdown()
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
                    if worker_info['worker'].process.poll() is not None:
                        with self.pool_lock:
                            if spec in self.workers:
                                del self.workers[spec]
                        continue
                    
                    # Perform health check
                    if not worker_info['worker'].health_check():
                        # 3 strikes and you're out
                        if worker_info['worker'].health_check_failures >= 3:
                            with self.pool_lock:
                                if spec in self.workers:
                                    del self.workers[spec]
                            worker_info['worker'].force_shutdown()

    def _memory_manager(self):

        """Enhanced with GPU memory monitoring."""
        import psutil
        while self.running:
            time.sleep(60)
            now = time.time()
            
            # 🔥 NEW: Check GPU memory pressure
            try:
                import torch
                if torch.cuda.is_available():
                    mem_allocated = torch.cuda.memory_allocated(0) / 1024**3  # GB
                    mem_reserved = torch.cuda.memory_reserved(0) / 1024**3
                    mem_free = torch.cuda.get_device_properties(0).total_memory / 1024**3 - mem_reserved
                    
                    # Aggressive eviction if VRAM < 2GB free
                    if mem_free < 2.0:
                        print(f"⚠️  Low VRAM ({mem_free:.1f}GB free), evicting idle workers...")
                        with self.pool_lock:
                            to_kill = sorted(
                                self.workers.items(),
                                key=lambda x: x[1]['last_used']
                            )[:max(1, len(self.workers) // 2)]  # Kill half
                            
                            for spec, info in to_kill:
                                del self.workers[spec]
                                self.stats['workers_killed'] += 1
                                threading.Thread(
                                    target=info['worker'].force_shutdown,
                                    daemon=True
                                ).start()
                        continue
            except:
                pass
            
            # Memory pressure check
            mem = psutil.virtual_memory()
            
            if mem.percent > 85:
                # Aggressive eviction
                with self.pool_lock:
                    to_kill = sorted(
                        self.workers.items(),
                        key=lambda x: x[1]['last_used']
                    )[:len(self.workers) // 2]
                    
                    for spec, info in to_kill:
                        del self.workers[spec]
                        self.stats['workers_killed'] += 1
                        threading.Thread(
                            target=info['worker'].force_shutdown,
                            daemon=True
                        ).start()
                continue
            
            # Normal idle timeout
            with self.pool_lock:
                specs_to_remove = []
                for spec, info in self.workers.items():
                    timeout = info.get('gpu_timeout', self.max_idle_time)
                    if now - info['last_used'] > timeout:
                        specs_to_remove.append(spec)
                
                for spec in specs_to_remove:
                    info = self.workers.pop(spec)
                    self.stats['workers_killed'] += 1
                    threading.Thread(
                        target=info['worker'].force_shutdown,
                        daemon=True
                    ).start()

    def _get_status(self) -> dict:
        with self.pool_lock:
            worker_details = {}
            for k, v in self.workers.items():
                worker_details[k] = {
                    'last_used': v['last_used'],
                    'request_count': v['request_count'],
                    'health_failures': v['worker'].health_check_failures
                }
            
            return {
                'success': True,
                'running': self.running,
                'workers': len(self.workers),
                'stats': self.stats,
                'worker_details': worker_details,
                'memory_percent': psutil.virtual_memory().percent
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
                info['worker'].force_shutdown()
            else:
                try:
                    info['worker'].force_shutdown()
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
        if not os.path.exists(PID_FILE):
            return False
        try:
            with open(PID_FILE, 'r') as f:
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
    torch_version = torch.__version__.split('+')[0]
    major, minor = map(int, torch_version.split('.')[:2])
    
    # Check for PyTorch 1.x native CUDA IPC
    if major == 1:
        try:
            # Test if the method exists
            if hasattr(torch.FloatStorage, '_new_using_cuda_ipc'):
                return 'native_1x'
        except:
            pass
    
    # Check for custom CUDA IPC capability
    try:
        cuda = ctypes.CDLL('libcuda.so.1')
        # Test basic CUDA driver calls
        cuda.cuInit(0)
        return 'custom'
    except:
        pass
    
    # Fallback to hybrid mode
    return 'hybrid'

# ═══════════════════════════════════════════════════════════════
# 2. NATIVE PYTORCH 1.x IPC (TRUE ZERO-COPY)
# ═══════════════════════════════════════════════════════════════

def share_tensor_native_1x(tensor: torch.Tensor) -> dict:
    import torch
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
        'ipc_handle': ipc_handle,
        'shape': tuple(tensor.shape),
        'dtype': str(tensor.dtype).split('.')[-1],
        'device': tensor.device.index,
        'method': 'native_1x'
    }

def receive_tensor_native_1x(meta: dict) -> torch.Tensor:
    import torch
    """Reconstruct tensor from PyTorch 1.x IPC handle."""
    storage = torch.FloatStorage._new_using_cuda_ipc(meta['ipc_handle'])
    
    dtype_map = {
        'float32': torch.float32,
        'float64': torch.float64,
        'float16': torch.float16
    }
    
    tensor = torch.tensor([], dtype=dtype_map[meta['dtype']], device=f"cuda:{meta['device']}")
    tensor.set_(storage, 0, meta['shape'])
    
    return tensor

# ═══════════════════════════════════════════════════════════════
# 3. CUSTOM CUDA IPC (CTYPES - WORKS WITH ANY PYTORCH)
# ═══════════════════════════════════════════════════════════════

class CUDAIPCHandle(ctypes.Structure):
    """CUDA IPC memory handle structure."""
    _fields_ = [("reserved", ctypes.c_char * 64)]

def share_tensor_custom_cuda(tensor: torch.Tensor) -> dict:
    import torch
    """
    Share GPU tensor using raw CUDA IPC (ctypes).
    Works with PyTorch 2.x and bypasses PyTorch's broken IPC.
    """
    if not tensor.is_cuda:
        raise ValueError("Tensor must be on GPU")
    
    # Get CUDA context
    cuda = ctypes.CDLL('libcuda.so.1')
    
    # Get device pointer
    data_ptr = tensor.data_ptr()
    
    # Create IPC handle
    ipc_handle = CUDAIPCHandle()
    result = cuda.cuIpcGetMemHandle(
        ctypes.byref(ipc_handle),
        ctypes.c_void_p(data_ptr)
    )
    
    if result != 0:
        raise RuntimeError(f"cuIpcGetMemHandle failed with code {result}")
    
    return {
        'ipc_handle': bytes(ipc_handle.reserved),
        'shape': tuple(tensor.shape),
        'dtype': str(tensor.dtype).split('.')[-1],
        'device': tensor.device.index,
        'size_bytes': tensor.numel() * tensor.element_size(),
        'method': 'custom'
    }

def receive_tensor_custom_cuda(meta: dict) -> torch.Tensor:
    import torch
    """Reconstruct tensor from custom CUDA IPC handle."""
    cuda = ctypes.CDLL('libcuda.so.1')
    
    # Reconstruct IPC handle
    ipc_handle = CUDAIPCHandle()
    ipc_handle.reserved = meta['ipc_handle']
    
    # Open IPC handle
    device_ptr = ctypes.c_void_p()
    result = cuda.cuIpcOpenMemHandle(
        ctypes.byref(device_ptr),
        ipc_handle,
        1  # CU_IPC_MEM_LAZY_ENABLE_PEER_ACCESS
    )
    
    if result != 0:
        raise RuntimeError(f"cuIpcOpenMemHandle failed with code {result}")
    
    # Create tensor from device pointer
    dtype_map = {
        'float32': torch.float32,
        'float64': torch.float64,
        'float16': torch.float16
    }
    
    # Use PyTorch's internal method to wrap device pointer
    storage = torch.cuda.FloatStorage._new_with_weak_ptr(device_ptr.value)
    
    tensor = torch.tensor([], dtype=dtype_map[meta['dtype']], device=f"cuda:{meta['device']}")
    tensor.set_(storage, 0, meta['shape'])
    
    return tensor

# ═══════════════════════════════════════════════════════════════
# 4. HYBRID MODE (CPU SHM FALLBACK)
# ═══════════════════════════════════════════════════════════════

def share_tensor_hybrid(tensor: torch.Tensor) -> dict:
    import torch
    """
    Fallback: Copy to CPU SHM, worker copies to GPU.
    2 PCIe transfers per stage, but still faster than JSON.
    """
    input_cpu = tensor.cpu().numpy()
    
    shm = shared_memory.SharedMemory(create=True, size=input_cpu.nbytes)
    shm_array = np.ndarray(input_cpu.shape, dtype=input_cpu.dtype, buffer=shm.buf)
    shm_array[:] = input_cpu[:]
    
    return {
        'shm_name': shm.name,
        'shape': tuple(tensor.shape),
        'dtype': str(tensor.dtype).split('.')[-1],
        'device': tensor.device.index,
        'method': 'hybrid'
    }

def receive_tensor_hybrid(meta: dict) -> torch.Tensor:
    import torch
    """Reconstruct tensor from CPU SHM."""
    shm = shared_memory.SharedMemory(name=meta['shm_name'])
    
    dtype_map = {
        'float32': np.float32,
        'float64': np.float64,
        'float16': np.float16
    }
    
    input_cpu = np.ndarray(
        tuple(meta['shape']),
        dtype=dtype_map[meta['dtype']],
        buffer=shm.buf
    )
    
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
        print(f"🔥 GPU IPC Mode: {self.mode}")
       
        if self.mode == 'native_1x':
            self.share = share_tensor_native_1x
            self.receive = receive_tensor_native_1x
        elif self.mode == 'custom':
            # NEW: Use the custom methods
            self.share = share_tensor_custom_cuda
            self.receive = receive_tensor_custom_cuda
        else:
            self.share = share_tensor_hybrid
            self.receive = receive_tensor_hybrid
    
    def share_tensor(self, tensor: torch.Tensor) -> dict:
        """Share a GPU tensor using best available method."""
        return self.share(tensor)
    
    def receive_tensor(self, meta: dict) -> torch.Tensor:
        """Receive a GPU tensor using method specified in metadata."""
        return self.receive(meta)

from enum import Enum
from typing import Optional, Tuple
# import torch

class IPCMode(Enum):
    """Available IPC transfer modes."""
    AUTO = "auto"              # Smart detection (default)
    UNIVERSAL = "universal"    # Pure CUDA IPC (ctypes) - FASTEST
    PYTORCH_NATIVE = "pytorch_native"  # PyTorch 1.x _share_cuda_() - VERY FAST
    CPU_SHM = "cpu_shm"        # CPU zero-copy SHM - MEDIUM (fallback)
    HYBRID = "hybrid"          # CPU SHM + GPU copies - SLOW (testing only)

class IPCCapabilities:
    """Detect available IPC methods on the system."""
    
    @staticmethod
    def has_pytorch_1x_native() -> bool:
        """Check if PyTorch 1.x native IPC is available."""
        try:
            import torch
            version = torch.__version__.split('+')[0]
            major = int(version.split('.')[0])
            
            if major != 1:
                return False
            
            # Test if _share_cuda_() exists and works
            if not torch.cuda.is_available():
                return False
            
            test_tensor = torch.zeros(1).cuda()
            storage = test_tensor.storage()
            
            if not hasattr(storage, '_share_cuda_'):
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
                return fallback, f"Universal IPC unavailable, using {fallback.value}"
        
        if requested_mode == IPCMode.PYTORCH_NATIVE:
            if IPCCapabilities.has_pytorch_1x_native():
                return requested_mode, "PyTorch 1.x native IPC available"
            else:
                fallback = IPCCapabilities.detect_optimal_mode()
                return fallback, f"PyTorch native unavailable, using {fallback.value}"
        
        # CPU SHM always works (no GPU needed)
        if requested_mode == IPCMode.CPU_SHM:
            return requested_mode, "Using CPU SHM (zero-copy, no GPU)"
        
        # Hybrid always works (but slower than CPU SHM)
        if requested_mode == IPCMode.HYBRID:
            return requested_mode, "Using hybrid mode (CPU SHM + GPU copies)"
        
        # Unknown mode
        fallback = IPCCapabilities.detect_optimal_mode()
        return fallback, f"Unknown mode, using {fallback.value}"

# ═══════════════════════════════════════════════════════════════
# 4. CLIENT & PROXY (With Auto-Resurrection)
# ═══════════════════════════════════════════════════════════════

class DaemonClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 30.0, auto_start: bool = True):
        self.socket_path = socket_path
        self.timeout = timeout
        self.auto_start = auto_start

    def execute_shm(self, spec, code, shm_in, shm_out, python_exe=None):
        if not python_exe:
            python_exe = sys.executable
        return self._send({
            'type': 'execute', 
            'spec': spec, 
            'code': code, 
            'shm_in': shm_in, 
            'shm_out': shm_out,
            'python_exe': python_exe
        })
    
    def status(self):
        old_auto = self.auto_start
        self.auto_start = False
        try:
            return self._send({'type': 'status'})
        finally:
            self.auto_start = old_auto
    
    def shutdown(self):
        return self._send({'type': 'shutdown'})

    def _spawn_daemon(self):
        import subprocess
        daemon_script = os.path.abspath(__file__)
        
        # Optional: Set minimal CUDA paths for daemon itself
        env = os.environ.copy()
        
        subprocess.Popen(
            [sys.executable, daemon_script, "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,  # Pass environment
            preexec_fn=os.setsid 
        )

    def _wait_for_socket(self, timeout=5.0):
        start_time = time.time()
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

    def _send(self, req):
        attempts = 0
        max_attempts = 3 if not self.auto_start else 2 
        while attempts < max_attempts:
            attempts += 1
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                send_json(sock, req, timeout=self.timeout)
                res = recv_json(sock, timeout=self.timeout)
                sock.close()
                return res
            except (ConnectionRefusedError, FileNotFoundError):
                if not self.auto_start:
                    if attempts >= max_attempts: return {'success': False, 'error': 'Daemon not running'}
                    time.sleep(0.2)
                    continue
                try: os.unlink(self.socket_path)
                except: pass
                self._spawn_daemon()
                if self._wait_for_socket(timeout=5.0):
                    attempts = 0
                    self.auto_start = False
                    continue
                else:
                    return {'success': False, 'error': 'Failed to auto-start daemon (timeout)'}
            except Exception as e:
                return {'success': False, 'error': f'Communication error: {e}'}
        return {'success': False, 'error': 'Connection failed after retries'}

    def execute_cuda_ipc(
        self, 
        spec: str, 
        code: str, 
        input_tensor: torch.Tensor,
        output_shape: tuple, 
        output_dtype: str, 
        python_exe: str = None,
        ipc_mode: str = 'auto'
    ):
        """
        Execute code with GPU IPC using specified mode.
        
        Args:
            ipc_mode: 'auto', 'universal', 'pytorch_native', 'cpu_shm', or 'hybrid'
        """
        import sys
        import torch
        
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")
        
        if not input_tensor.is_cuda:
            raise ValueError("Input tensor must be on GPU")
        
        # Parse IPC mode
        try:
            mode_enum = IPCMode(ipc_mode.lower())
        except ValueError:
            print(f"⚠️  Invalid IPC mode '{ipc_mode}', using auto")
            mode_enum = IPCMode.AUTO
        
        # Validate and get actual mode
        actual_mode, mode_msg = IPCCapabilities.validate_mode(mode_enum)
        
        print(f"   🎯 IPC Mode: {mode_msg}")
        
        # ═══════════════════════════════════════════════════════════
        # ROUTE 1: UNIVERSAL CUDA IPC (DEFAULT - FASTEST)
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.UNIVERSAL:
            return self._execute_universal_ipc(
                spec, code, input_tensor, output_shape, output_dtype, python_exe
            )
        
        # ═══════════════════════════════════════════════════════════
        # ROUTE 2: PYTORCH 1.x NATIVE IPC
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.PYTORCH_NATIVE:
            return self._execute_pytorch_native_ipc(
                spec, code, input_tensor, output_shape, output_dtype, python_exe
            )
        
        # ═══════════════════════════════════════════════════════════
        # ROUTE 3: CPU SHM (ZERO-COPY, NO GPU - MEDIUM SPEED)
        # ═══════════════════════════════════════════════════════════
        if actual_mode == IPCMode.CPU_SHM:
            return self._execute_cpu_shm(
                spec, code, input_tensor, output_shape, output_dtype, python_exe
            )
        
        # ═══════════════════════════════════════════════════════════
        # ROUTE 4: HYBRID (CPU SHM + GPU COPIES - SLOWEST)
        # ═══════════════════════════════════════════════════════════
        return self._execute_hybrid_ipc(
            spec, code, input_tensor, output_shape, output_dtype, python_exe
        )
    
    def _execute_cpu_shm(self, spec, code, input_tensor, output_shape, output_dtype, python_exe):
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
        from multiprocessing import shared_memory
        import numpy as np
        import torch
        
        print(f"   💾 Using CPU SHM mode (zero-copy, no GPU transfers)")
        
        # Convert tensor to CPU numpy
        input_cpu = input_tensor.cpu().numpy()
        
        # Create output array on CPU
        dtype_map = {
            'float32': np.float32,
            'float64': np.float64,
            'float16': np.float16,
            'int32': np.int32,
            'int64': np.int64,
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
                python_exe=python_exe or sys.executable
            )
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            print(f"   ✅ CPU SHM mode completed")
            
            # Convert result back to GPU tensor
            output_tensor = torch.from_numpy(result_cpu).to(input_tensor.device)
            
            # Add method info to response
            response['cuda_method'] = 'cpu_shm'
            
            return output_tensor, response
            
        except Exception as e:
            print(f"   ⚠️  CPU SHM failed: {e}")
            raise

    def _execute_universal_ipc(self, spec, code, input_tensor, output_shape, output_dtype, python_exe):
        """Universal CUDA IPC using ctypes (fastest, most compatible)."""
        import torch
        from omnipkg.isolation.worker_daemon import UniversalGpuIpc
        
        print(f"   🔥 Using UNIVERSAL CUDA IPC (ctypes - TRUE ZERO-COPY)")
        
        try:
            # Share input tensor using Universal IPC
            cuda_in_meta = {
                'universal_ipc': UniversalGpuIpc.share(input_tensor),
                'device': input_tensor.device.index
            }
            
            # Create output tensor and share it
            dtype_map = {
                'float32': torch.float32, 
                'float64': torch.float64, 
                'float16': torch.float16,
                'int32': torch.int32,
                'int64': torch.int64,
            }
            torch_dtype = dtype_map.get(output_dtype, torch.float32)
            output_tensor = torch.empty(output_shape, dtype=torch_dtype, device=input_tensor.device)
            
            cuda_out_meta = {
                'universal_ipc': UniversalGpuIpc.share(output_tensor),
                'device': output_tensor.device.index
            }
            
            # Send to daemon
            response = self._send({
                'type': 'execute_cuda',
                'spec': spec,
                'code': code,
                'cuda_in': cuda_in_meta,
                'cuda_out': cuda_out_meta,
                'python_exe': python_exe or sys.executable
            })
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            actual_method = response.get('cuda_method', 'unknown')
            if actual_method == 'universal_ipc':
                print(f"   🔥 Worker confirmed UNIVERSAL IPC (true zero-copy)!")
            else:
                print(f"   ⚠️  Worker fell back to {actual_method}")
            
            return output_tensor, response
            
        except Exception as e:
            print(f"   ⚠️  Universal IPC failed: {e}")
            raise
    
    def _execute_pytorch_native_ipc(self, spec, code, input_tensor, output_shape, output_dtype, python_exe):
        """PyTorch 1.x native IPC (framework-managed)."""
        import torch
        import base64
        
        print(f"   🔥 Using PYTORCH NATIVE IPC (PyTorch 1.x)")
        
        try:
            # Share input tensor via native CUDA IPC
            input_storage = input_tensor.storage()
            (storage_device, storage_handle, storage_size_bytes, storage_offset_bytes,
             ref_counter_handle, ref_counter_offset, event_handle, event_sync_required) = input_storage._share_cuda_()
            
            cuda_in_meta = {
                'ipc_data': {
                    'tensor_size': list(input_tensor.shape),
                    'tensor_stride': list(input_tensor.stride()),
                    'tensor_offset': input_tensor.storage_offset(),
                    'storage_cls': type(input_storage).__name__,
                    'dtype': str(input_tensor.dtype).replace('torch.', ''),
                    'storage_device': storage_device,
                    'storage_handle': base64.b64encode(storage_handle).decode('ascii'),
                    'storage_size_bytes': storage_size_bytes,
                    'storage_offset_bytes': storage_offset_bytes,
                    'ref_counter_handle': base64.b64encode(ref_counter_handle).decode('ascii'),
                    'ref_counter_offset': ref_counter_offset,
                    'event_handle': base64.b64encode(event_handle).decode('ascii') if event_handle else '',
                    'event_sync_required': event_sync_required
                },
                'device': input_tensor.device.index
            }
            
            # Create output tensor and share it
            dtype_map = {'float32': torch.float32, 'float64': torch.float64, 'float16': torch.float16}
            torch_dtype = dtype_map.get(output_dtype, torch.float32)
            output_tensor = torch.empty(output_shape, dtype=torch_dtype, device=input_tensor.device)
            
            output_storage = output_tensor.storage()
            (storage_device, storage_handle, storage_size_bytes, storage_offset_bytes,
             ref_counter_handle, ref_counter_offset, event_handle, event_sync_required) = output_storage._share_cuda_()
            
            cuda_out_meta = {
                'ipc_data': {
                    'tensor_size': list(output_tensor.shape),
                    'tensor_stride': list(output_tensor.stride()),
                    'tensor_offset': output_tensor.storage_offset(),
                    'storage_cls': type(output_storage).__name__,
                    'dtype': str(output_tensor.dtype).replace('torch.', ''),
                    'storage_device': storage_device,
                    'storage_handle': base64.b64encode(storage_handle).decode('ascii'),
                    'storage_size_bytes': storage_size_bytes,
                    'storage_offset_bytes': storage_offset_bytes,
                    'ref_counter_handle': base64.b64encode(ref_counter_handle).decode('ascii'),
                    'ref_counter_offset': ref_counter_offset,
                    'event_handle': base64.b64encode(event_handle).decode('ascii') if event_handle else '',
                    'event_sync_required': event_sync_required
                },
                'device': output_tensor.device.index
            }
            
            response = self._send({
                'type': 'execute_cuda',
                'spec': spec,
                'code': code,
                'cuda_in': cuda_in_meta,
                'cuda_out': cuda_out_meta,
                'python_exe': python_exe or sys.executable
            })
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            actual_method = response.get('cuda_method', 'unknown')
            if actual_method == 'native_ipc':
                print(f"   🔥 Worker confirmed NATIVE IPC (PyTorch managed)!")
            else:
                print(f"   ⚠️  Worker fell back to {actual_method}")
            
            return output_tensor, response
            
        except Exception as e:
            print(f"   ⚠️  PyTorch native IPC failed: {e}")
            raise
    
    
    def _execute_hybrid_ipc(self, spec, code, input_tensor, output_shape, output_dtype, python_exe):
        """
        Hybrid mode: Copy to CPU SHM, worker copies to GPU.
        
        NOTE: Benchmarks show this is the SLOWEST mode (14ms vs 1.5ms Universal).
        Only use this for testing or when all other modes fail.
        
        Prefer CPU_SHM mode over this (10ms vs 14ms) - it's faster!
        """
        from multiprocessing import shared_memory
        import numpy as np
        import torch
        
        print(f"   🔄 Using HYBRID mode (CPU SHM + GPU copies) - SLOWEST MODE")
        print(f"   💡 Consider using cpu_shm mode instead (1.34x faster)")
        
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
                'shm_name': shm_in.name,
                'shape': tuple(input_tensor.shape),
                'dtype': output_dtype,
                'device': input_tensor.device.index
            }
            
            cuda_out_meta = {
                'shm_name': shm_out.name,
                'shape': output_shape,
                'dtype': output_dtype,
                'device': input_tensor.device.index
            }
            
            response = self._send({
                'type': 'execute_cuda',
                'spec': spec,
                'code': code,
                'cuda_in': cuda_in_meta,
                'cuda_out': cuda_out_meta,
                'python_exe': python_exe or sys.executable
            })
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            print(f"   ✅ Hybrid mode completed")
            
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
            
    def execute_zero_copy(self, spec: str, code: str, input_array, output_shape, output_dtype, python_exe=None):
        """
        🚀 HFT MODE: Zero-Copy Tensor Handoff via Shared Memory.
        """
        import numpy as np
        from multiprocessing import shared_memory
        
        shm_in = shared_memory.SharedMemory(create=True, size=input_array.nbytes)
        
        start_shm = np.ndarray(input_array.shape, dtype=input_array.dtype, buffer=shm_in.buf)
        start_shm[:] = input_array[:] 
        
        dummy = np.zeros(1, dtype=output_dtype)
        out_size = int(np.prod(output_shape)) * dummy.itemsize
        shm_out = shared_memory.SharedMemory(create=True, size=out_size)
        
        try:
            in_meta = {
                'name': shm_in.name,
                'shape': input_array.shape,
                'dtype': str(input_array.dtype)
            }
            
            out_meta = {
                'name': shm_out.name,
                'shape': output_shape,
                'dtype': str(output_dtype)
            }
            
            # Pass python_exe to execute_shm
            response = self.execute_shm(spec, code, in_meta, out_meta, python_exe=python_exe)
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            result_view = np.ndarray(output_shape, dtype=output_dtype, buffer=shm_out.buf)
            return result_view.copy(), response
            
        finally:
            try: shm_in.close(); shm_in.unlink()
            except: pass
            try: shm_out.close(); shm_out.unlink()
            except: pass

    def execute_smart(self, spec: str, code: str, data=None, python_exe=None):
        """
        🧠 INTELLIGENT DISPATCH:
        - GPU Tensor → CUDA IPC (fastest, <5µs)
        - Large CPU Array → CPU SHM (fast, ~5ms)
        - Small Data → JSON (acceptable, ~10ms)
        """
        import numpy as np
        
        # ═══════════════════════════════════════════════════════
        # GPU FAST PATH - CUDA IPC
        # ═══════════════════════════════════════════════════════
        if data is not None and hasattr(data, 'is_cuda') and data.is_cuda:
            import torch
            
            # Assume code modifies tensor in-place or returns same shape/dtype
            output_shape = data.shape
            output_dtype = str(data.dtype).split('.')[-1]  # "float32"
            
            result_tensor, meta = self.execute_cuda_ipc(
                spec, code, data, output_shape, output_dtype, python_exe
            )
            
            return {
                'success': True,
                'result': result_tensor,
                'meta': meta,
                'transport': 'CUDA_IPC',
                'latency_us': '<5'  # Sub-microsecond handoff
            }
        
        # ═══════════════════════════════════════════════════════
        # CPU SHM PATH (Large Arrays)
        # ═══════════════════════════════════════════════════════
        SMART_THRESHOLD = 1024 * 64  # 64KB
        
        if data is not None and isinstance(data, np.ndarray) and data.nbytes >= SMART_THRESHOLD:
            output_shape = data.shape
            output_dtype = data.dtype
            
            result, meta = self.execute_zero_copy(
                spec, code, data, output_shape, output_dtype, python_exe
            )
            
            return {
                'success': True,
                'result': result,
                'meta': meta,
                'transport': 'SHM',
                'latency_ms': '~5'
            }
        
        # ═══════════════════════════════════════════════════════
        # JSON PATH (Small Data)
        # ═══════════════════════════════════════════════════════
        prefix = ""
        if data is not None:
            if isinstance(data, np.ndarray):
                prefix = f"import numpy as np\narr_in = np.array({data.tolist()})\n"
            else:
                prefix = f"arr_in = {json.dumps(data)}\n"
        
        response = self.execute_shm(spec, prefix + code, {}, {}, python_exe=python_exe)
        
        if response.get('success'):
            return {
                'success': True,
                'result': response.get('stdout', '').strip(),
                'meta': response,
                'transport': 'JSON',
                'latency_ms': '~10'
            }
        
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
        if result.get('status') == 'COMPLETED':
            return {
                'success': True,
                'stdout': result.get('stdout', ''),
                'stderr': result.get('stderr', ''),
                'locals': result.get('locals', '')
            }
        else:
            return {
                'success': False,
                'error': result.get('error', 'Unknown daemon error'),
                'traceback': result.get('traceback', '')
            }

    def get_version(self, package_name):
        code = f"import importlib.metadata; result = {{'version': importlib.metadata.version('{package_name}'), 'path': __import__('{package_name}').__file__}}"
        res = self.execute(code)
        if res.get('success'):
            return {'success': True, 'version': 'unknown', 'path': 'daemon'}
        return {'success': False, 'error': res.get('error')}

    def shutdown(self):
        pass

# ═══════════════════════════════════════════════════════════════
# 5. CLI FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def cli_start():
    """Start the daemon with status checks."""
    if WorkerPoolDaemon.is_running():
        print("⚠️  Daemon is already running.")
        # Optional: Print info about the running instance
        cli_status()
        return

    print("🚀 Initializing OmniPkg Worker Daemon...", end=" ", flush=True)
    
    # Initialize
    daemon = WorkerPoolDaemon(
        max_workers=10,
        max_idle_time=300,
        warmup_specs=[]
    )
    
    # Start (The parent process will print "✅" and exit inside this call)
    try:
        daemon.start(daemonize=True)
    except Exception as e:
        print(f"\n❌ Failed to start: {e}")

def cli_stop():
    """Stop the daemon."""
    client = DaemonClient()
    result = client.shutdown()
    if result.get('success'):
        print("✅ Daemon stopped")
        try:
            os.unlink(PID_FILE)
        except:
            pass
    else:
        print(f"❌ Failed to stop: {result.get('error', 'Unknown error')}")

def cli_status():
    """Get daemon status."""
    if not WorkerPoolDaemon.is_running():
        print("❌ Daemon not running")
        return
    
    client = DaemonClient()
    result = client.status()
    
    if not result.get('success'):
        print(f"❌ Error: {result.get('error', 'Unknown error')}")
        return
    
    print("\n" + "="*60)
    print("🔥 OMNIPKG WORKER DAEMON STATUS")
    print("="*60)
    print(f"  Workers: {result.get('workers', 0)}")
    print(f"  Memory Usage: {result.get('memory_percent', 0):.1f}%")
    print(f"  Total Requests: {result['stats']['total_requests']}")
    print(f"  Cache Hits: {result['stats']['cache_hits']}")
    print(f"  Errors: {result['stats']['errors']}")
    
    if result.get('worker_details'):
        print("\n  📦 Active Workers:")
        for spec, info in result['worker_details'].items():
            idle = time.time() - info['last_used']
            print(f"    - {spec}")
            print(f"      Requests: {info['request_count']}, Idle: {idle:.0f}s, Failures: {info['health_failures']}")
    
    print("="*60 + "\n")

def cli_logs(follow: bool = False, tail_lines: int = 50):
    """View or follow the daemon logs."""
    log_path = Path(DAEMON_LOG_FILE)
    if not log_path.exists():
        print(f"❌ Log file not found at: {log_path}")
        print("   (The daemon might not have started yet)")
        return

    print(f"📄 Tailing {log_path} (last {tail_lines} lines)...")
    print("-" * 60)
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            # 1. Efficiently read last N lines
            f.seek(0, 2)
            file_size = f.tell()
            
            # Heuristic: average line ~150 bytes. Read enough blocks to cover it.
            block_size = max(4096, tail_lines * 200)
            
            if file_size > block_size:
                f.seek(file_size - block_size)
                # Discard potential partial line at start of block
                f.readline()
            else:
                f.seek(0)
            
            # Print the tail
            lines = f.readlines()
            for line in lines[-tail_lines:]:
                print(line, end='')
                
            # 2. Follow mode (tail -f)
            if follow:
                print("-" * 60)
                print("📡 Following logs... (Ctrl+C to stop)")
                
                # Seek to end just in case
                f.seek(0, 2)
                
                while True:
                    line = f.readline()
                    if line:
                        print(line, end='', flush=True)
                    else:
                        time.sleep(0.1)
                        
    except KeyboardInterrupt:
        print("\n🛑 Stopped following logs.")
    except Exception as e:
        print(f"\n❌ Error reading logs: {e}")

# ═══════════════════════════════════════════════════════════════
# CLI ENTRY
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m omnipkg.isolation.worker_daemon {start|stop|status}")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "start":
        cli_start()
    elif cmd == "stop":
        cli_stop()
    elif cmd == "status":
        cli_status()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)