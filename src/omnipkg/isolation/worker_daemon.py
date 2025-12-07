from __future__ import annotations
import os
import sys
import json
import tempfile
import time
import socket
import signal
import psutil
import threading
import subprocess
import select
from pathlib import Path
from typing import Dict, Optional, Any, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import traceback
from collections import deque
import ctypes

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
# Reset nesting depth to ensure we get cleanup
if hasattr(omnipkgLoader, '_nesting_depth'):
    omnipkgLoader._nesting_depth = 0

try:
    specs = [s.strip() for s in PKG_SPEC.split(',')]
    loaders = []
    for s in specs:
        l = omnipkgLoader(s, isolation_mode='overlay')
        l.__enter__()
        loaders.append(l)
    
    globals()['_omnipkg_loaders'] = loaders
    
    # ═══════════════════════════════════════════════════════════════
    # CRITICAL FIX: FORCE IMMEDIATE CLEANUP AFTER ACTIVATION
    # ═══════════════════════════════════════════════════════════════
    sys.stderr.write('🧹 [DAEMON] Starting immediate post-activation cleanup...\\n')
    sys.stderr.flush()
    
    for loader in loaders:
        # Force cleanup regardless of nesting state
        cleanup_count = 0
        
        # Restore main env cloaks
        if hasattr(loader, '_cloaked_main_modules') and loader._cloaked_main_modules:
            sys.stderr.write(f'   🔓 Restoring {len(loader._cloaked_main_modules)} main env cloaks...\\n')
            sys.stderr.flush()
            
            for original_path, cloak_path, was_successful in reversed(loader._cloaked_main_modules):
                if not was_successful or not cloak_path.exists():
                    continue
                
                try:
                    # Force cleanup destination
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

try:
    ready_msg = {'status': 'READY', 'package': PKG_SPEC}
    print(json.dumps(ready_msg), flush=True)
except Exception as e:
    sys.stderr.write(f"ERROR: Failed to send READY: {e}\\n")
    sys.stderr.flush()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# MAIN EXECUTION LOOP with GPU IPC Support
# ═══════════════════════════════════════════════════════════════
from multiprocessing import shared_memory
from contextlib import redirect_stdout, redirect_stderr
import io

# GPU IPC capability detection
_gpu_ipc_available = False
_torch_available = False
_cuda_available = False

try:
    import torch
    _torch_available = True
    _cuda_available = torch.cuda.is_available()
    if _cuda_available:
        # Check for CUDA IPC support
        try:
            test_tensor = torch.zeros(1).cuda()
            test_tensor.share_memory_()
            _gpu_ipc_available = True
            sys.stderr.write('🚀 [DAEMON] GPU IPC available via PyTorch CUDA\\n')
            sys.stderr.flush()
        except:
            sys.stderr.write('⚠️  [DAEMON] PyTorch CUDA detected but IPC unavailable\\n')
            sys.stderr.flush()
except ImportError:
    pass

if not _gpu_ipc_available:
    sys.stderr.write('ℹ️  [DAEMON] Running in CPU-only mode (standard SHM)\\n')
    sys.stderr.flush()

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
        shm_in_meta = command.get('shm_in')
        shm_out_meta = command.get('shm_out')
        cuda_ipc_in = command.get('cuda_ipc_in')
        cuda_ipc_out = command.get('cuda_ipc_out')
        exec_scope = {'input_data': command}
        shm_blocks = []
        
        # ═══════════════════════════════════════════════════════════
        # HYBRID ZERO-COPY: CUDA IPC (GPU) or SHM (CPU)
        # ═══════════════════════════════════════════════════════════
        
        # Handle CUDA IPC inputs (GPU zero-copy)
        if cuda_ipc_in and _gpu_ipc_available and _torch_available:
            try:
                import torch
                handle_bytes = bytes.fromhex(cuda_ipc_in['handle'])
                
                # Reconstruct tensor from IPC handle
                storage = torch.cuda.ByteStorage._new_shared_cuda(
                    cuda_ipc_in['device'],
                    handle_bytes,
                    cuda_ipc_in['size'],
                    None
                )
                
                tensor = torch.tensor([], dtype=getattr(torch, cuda_ipc_in['dtype']))
                tensor.set_(
                    storage,
                    cuda_ipc_in['offset'],
                    tuple(cuda_ipc_in['shape']),
                    tuple(cuda_ipc_in['stride'])
                )
                
                exec_scope['arr_in'] = tensor
                sys.stderr.write(f'🚀 [TASK {task_id}] Using GPU zero-copy input\\n')
                sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f'⚠️  [TASK {task_id}] CUDA IPC failed: {e}, falling back to SHM\\n')
                sys.stderr.flush()
                cuda_ipc_in = None
        
        # Handle CUDA IPC outputs (GPU zero-copy)
        if cuda_ipc_out and _gpu_ipc_available and _torch_available:
            try:
                import torch
                handle_bytes = bytes.fromhex(cuda_ipc_out['handle'])
                
                storage = torch.cuda.ByteStorage._new_shared_cuda(
                    cuda_ipc_out['device'],
                    handle_bytes,
                    cuda_ipc_out['size'],
                    None
                )
                
                tensor = torch.tensor([], dtype=getattr(torch, cuda_ipc_out['dtype']))
                tensor.set_(
                    storage,
                    cuda_ipc_out['offset'],
                    tuple(cuda_ipc_out['shape']),
                    tuple(cuda_ipc_out['stride'])
                )
                
                exec_scope['arr_out'] = tensor
                sys.stderr.write(f'🚀 [TASK {task_id}] Using GPU zero-copy output\\n')
                sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f'⚠️  [TASK {task_id}] CUDA IPC failed: {e}, falling back to SHM\\n')
                sys.stderr.flush()
                cuda_ipc_out = None
        
        # Fallback to standard SHM if CUDA IPC not used/available
        if (shm_in_meta or shm_out_meta) and not (cuda_ipc_in or cuda_ipc_out):
            import numpy as np
            
            if shm_in_meta:
                shm_in = shared_memory.SharedMemory(name=shm_in_meta['name'])
                shm_blocks.append(shm_in)
                exec_scope['arr_in'] = np.ndarray(
                    tuple(shm_in_meta['shape']), 
                    dtype=shm_in_meta['dtype'], 
                    buffer=shm_in.buf
                )
                sys.stderr.write(f'💾 [TASK {task_id}] Using CPU SHM input\\n')
                sys.stderr.flush()
            
            if shm_out_meta:
                shm_out = shared_memory.SharedMemory(name=shm_out_meta['name'])
                shm_blocks.append(shm_out)
                exec_scope['arr_out'] = np.ndarray(
                    tuple(shm_out_meta['shape']), 
                    dtype=shm_out_meta['dtype'], 
                    buffer=shm_out.buf
                )
                sys.stderr.write(f'💾 [TASK {task_id}] Using CPU SHM output\\n')
                sys.stderr.flush()
        
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(f'{worker_code}\\nworker_result = locals().get("result", None)', exec_scope, exec_scope)
            
            result = exec_scope.get("worker_result", {})
            if not isinstance(result, dict):
                result = {}
            
            result['task_id'] = task_id
            result['status'] = 'COMPLETED'
            result['success'] = True
            result['stdout'] = stdout_buffer.getvalue()
            result['stderr'] = stderr_buffer.getvalue()
            
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
            # Only close standard SHM blocks (CUDA IPC handles auto-cleanup)
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

# Cleanup on exit (but DON'T call __exit__ as it would re-cloak)
# The loaders persist in memory for fast execution
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
        
        # Open daemon log for worker stderr (store as instance variable)
        self.log_file = open(DAEMON_LOG_FILE, 'a', buffering=1)

        self.process = subprocess.Popen(
            [self.python_exe, '-u', self.temp_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log_file,  # <-- Worker stderr goes to log file
            text=True,
            bufsize=0,
            env=env,
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
        # Default to daemon's own python if not specified
        if not python_exe:
            python_exe = sys.executable
            
        # CRITICAL: The key must include the Python path to differentiate environments
        worker_key = f"{spec}::{python_exe}"
        
        with self.worker_locks[worker_key]: # Use new key for locking
            with self.pool_lock:
                if worker_key not in self.workers:
                    # Need to create worker - check capacity
                    if len(self.workers) >= self.max_workers:
                        # Evict WITHOUT holding pool lock
                        self._evict_oldest_worker_async()
                    
                    # Create worker
                    try:
                        worker = PersistentWorker(spec, python_exe=python_exe) # <--- PASS IT
                        self.workers[worker_key] = { # Store with new key
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
                
                worker_info = self.workers[worker_key] # Use worker_key
            
            # Execute outside pool lock (only spec lock held)
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
                            'memory_mb': 0.0
                        }
                        self.stats['workers_created'] += 1
                    except Exception as e:
                        import traceback
                        error_msg = f'Worker creation failed: {e}\n{traceback.format_exc()}'
                        return {'success': False, 'error': error_msg, 'status': 'ERROR'}
                else:
                    self.stats['cache_hits'] += 1
                
                worker_info = self.workers[worker_key]
            
            # Execute outside pool lock
            worker_info['last_used'] = time.time()
            worker_info['request_count'] += 1
            
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
        """CRITICAL FIX: Monitor system memory pressure."""
        while self.running:
            time.sleep(60)
            now = time.time()
            
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
                    if now - info['last_used'] > self.max_idle_time:
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
        subprocess.Popen(
            [sys.executable, daemon_script, "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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

    def execute_cuda_ipc(self, spec: str, code: str, input_tensor, 
                     output_shape: tuple, output_dtype: str, python_exe: str = None):
        """
        🔥 GPU-RESIDENT MODE: Zero-copy via CUDA IPC.
        
        FIXED: Don't try native IPC in main process - worker will auto-detect.
        """
        import torch
        import numpy as np
        from multiprocessing import shared_memory
        
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA not available")
        
        if not input_tensor.is_cuda:
            raise ValueError("Input tensor must be on GPU")
        
        # ═══════════════════════════════════════════════════════════════
        # ALWAYS USE HYBRID PATH - Worker will optimize internally
        # ═══════════════════════════════════════════════════════════════
        print(f"   🔄 Using HYBRID mode (worker may upgrade to native IPC)")
        
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
                'device': input_tensor.device.index,
                'method': 'auto'  # ← Let worker decide!
            }
            
            cuda_out_meta = {
                'shm_name': shm_out.name,
                'shape': output_shape,
                'dtype': output_dtype,
                'device': input_tensor.device.index,
                'method': 'auto'
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
            
            # Check if worker upgraded to native IPC
            actual_method = response.get('cuda_method', 'hybrid')

            if actual_method == 'native_1x':
                print(f"   🔥 Worker upgraded to NATIVE IPC (true zero-copy)!")
                
                # Deserialize IPC handle if provided
                if 'ipc_handle' in response:
                    import base64
                    raw_handle = response['ipc_handle']
                    deserialized_handle = []
                    for item in raw_handle:
                        if item.get('type') == 'bytes':
                            deserialized_handle.append(
                                base64.b64decode(item['data'].encode('ascii'))
                            )
                        else:
                            deserialized_handle.append(item['data'])
                    
                    # Store deserialized handle for potential reuse
                    response['deserialized_ipc_handle'] = tuple(deserialized_handle)
            else:
                print(f"   ✅ Hybrid mode (2 GPU copies per stage)")
            
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