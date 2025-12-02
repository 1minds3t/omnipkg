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
from pathlib import Path
from typing import Dict, Optional, Any, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import traceback

# ═══════════════════════════════════════════════════════════════
# 0. CONSTANTS & UTILITIES
# ═══════════════════════════════════════════════════════════════

DEFAULT_SOCKET = "/tmp/omnipkg_daemon.sock"
PID_FILE = "/tmp/omnipkg_daemon.pid"
SHM_REGISTRY_FILE = "/tmp/omnipkg_shm_registry.json"

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
# 1. PERSISTENT WORKER SCRIPT
# ═══════════════════════════════════════════════════════════════



_DAEMON_SCRIPT = r"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json

# CRITICAL: Set environment FIRST, before any imports
os.environ['OMNIPKG_IS_DAEMON_WORKER'] = '1'
os.environ['OMNIPKG_DISABLE_WORKER_POOL'] = '1'

# CRITICAL: Force unbuffered I/O
sys.stdin.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# CRITICAL FIX: Redirect stdout to /dev/null BEFORE omnipkg imports
# This suppresses ALL diagnostic output from module initialization
import io
_original_stdout = sys.stdout
_devnull = open(os.devnull, 'w')
sys.stdout = _devnull

def fatal_error(msg, error=None):
    import traceback
    error_obj = {
        'status': 'FATAL',
        'error': msg
    }
    if error:
        error_obj['exception'] = str(error)
        error_obj['traceback'] = traceback.format_exc()
    sys.stderr.write(json.dumps(error_obj) + '\n')
    sys.stderr.flush()
    sys.exit(1)

# Read setup configuration
try:
    input_line = sys.stdin.readline()
    
    if not input_line:
        fatal_error('No input received on stdin')
    
    input_line = input_line.strip()
    
    if not input_line:
        fatal_error('Empty input received on stdin')
    
    try:
        setup_data = json.loads(input_line)
    except json.JSONDecodeError as e:
        fatal_error(f'Invalid JSON received: {repr(input_line)}', e)
    
    PKG_SPEC = setup_data.get('package_spec')
    
    if not PKG_SPEC:
        fatal_error(f'Missing package_spec in setup data: {setup_data}')
        
except Exception as e:
    fatal_error('Startup configuration failed', e)

# Import omnipkg loader (stdout is still redirected to /dev/null)
try:
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    fatal_error('Failed to import omnipkgLoader', e)

# Activate the bubble environment (stdout still suppressed)
try:
    # Support multiple packages separated by comma
    specs = [s.strip() for s in PKG_SPEC.split(',')]
    loaders = []
    for s in specs:
        l = omnipkgLoader(s, quiet=True)
        l.__enter__()
        loaders.append(l)
        
    # Keep reference to loaders so they don't exit
    globals()['_omnipkg_loaders'] = loaders
except Exception as e:
    fatal_error(f'Failed to activate {PKG_SPEC}', e)

# CRITICAL: Now restore stdout for JSON protocol
_devnull.close()
sys.stdout = _original_stdout
sys.stdout.reconfigure(line_buffering=True)

# Send READY signal (stdout is now clean)
try:
    ready_msg = {'status': 'READY', 'package': PKG_SPEC}
    print(json.dumps(ready_msg), flush=True)
except Exception as e:
    sys.stderr.write(f"ERROR: Failed to send READY: {e}\n")
    sys.stderr.flush()
    sys.exit(1)

# Main execution loop
from multiprocessing import shared_memory
from contextlib import redirect_stdout, redirect_stderr

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
        exec_scope = {'input_data': command}
        shm_blocks = []
        
        # Lazy import numpy only if needed
        if shm_in_meta or shm_out_meta:
            import numpy as np
        
        # Attach shared memory
        if shm_in_meta:
            shm_in = shared_memory.SharedMemory(name=shm_in_meta['name'])
            shm_blocks.append(shm_in)
            exec_scope['arr_in'] = np.ndarray(
                tuple(shm_in_meta['shape']), 
                dtype=shm_in_meta['dtype'], 
                buffer=shm_in.buf
            )
        
        if shm_out_meta:
            shm_out = shared_memory.SharedMemory(name=shm_out_meta['name'])
            shm_blocks.append(shm_out)
            exec_scope['arr_out'] = np.ndarray(
                tuple(shm_out_meta['shape']), 
                dtype=shm_out_meta['dtype'], 
                buffer=shm_out.buf
            )
        
        # Execute code with captured output
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(f'{worker_code}\nworker_result = locals().get("result", None)', exec_scope, exec_scope)
            
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
                'success': False,
                'stdout': stdout_buffer.getvalue(),
                'stderr': stderr_buffer.getvalue()
            }
            print(json.dumps(error_response), flush=True)
        
        finally:
            # Cleanup shared memory
            for shm in shm_blocks:
                try:
                    shm.close()
                    shm.unlink()
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
try:
    loader.__exit__(None, None, None)
except:
    pass
"""

# ═══════════════════════════════════════════════════════════════
# 2. WORKER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class PersistentWorker:
    def __init__(self, package_spec: str):
        self.package_spec = package_spec
        self.process: Optional[subprocess.Popen] = None
        self.temp_file: Optional[str] = None
        self.lock = threading.RLock()  # Per-worker lock
        self.last_health_check = time.time()
        self.health_check_failures = 0
        self._start_worker()
        
    def _start_worker(self):
        """Start worker process with proper unbuffering and error handling."""
        import tempfile
        import select
        
        # Create temp script file
        with tempfile.NamedTemporaryFile(
            mode='w', 
            delete=False, 
            suffix=f"_{self.package_spec.replace('=', '_').replace('==', '_')}.py"
        ) as f:
            f.write(_DAEMON_SCRIPT)
            self.temp_file = f.name
        
        # CRITICAL: Use -u for unbuffered output
        self.process = subprocess.Popen(
            [sys.executable, '-u', self.temp_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,  # Unbuffered
            preexec_fn=os.setsid
        )
        
        try:
            # Send setup data
            setup_json = json.dumps({'package_spec': self.package_spec}) + '\n'
            self.process.stdin.write(setup_json)
            self.process.stdin.flush()
            
            # Wait for READY with timeout
            ready, _, _ = select.select([self.process.stdout], [], [], 30.0)
            
            if not ready:
                # Timeout - collect any error output
                stderr_lines = []
                while True:
                    err_ready, _, _ = select.select([self.process.stderr], [], [], 0.1)
                    if not err_ready:
                        break
                    line = self.process.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line)
                
                stderr_output = ''.join(stderr_lines)
                raise RuntimeError(
                    f"Worker startup timeout (30s). "
                    f"Stderr: {stderr_output if stderr_output else 'empty'}"
                )
            
            # Read READY response
            ready_line = self.process.stdout.readline()
            
            if not ready_line:
                raise RuntimeError("Worker sent empty READY line")
            
            ready_line = ready_line.strip()
            
            if not ready_line:
                raise RuntimeError("Worker sent blank READY line")
            
            try:
                ready_status = json.loads(ready_line)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Worker sent invalid READY JSON: {repr(ready_line)}: {e}"
                )
            
            if ready_status.get('status') != 'READY':
                raise RuntimeError(
                    f"Worker failed to initialize: {ready_status}"
                )
            
            # Success!
            self.last_health_check = time.time()
            self.health_check_failures = 0
            
        except Exception as e:
            # Capture stderr for debugging
            try:
                stderr_lines = []
                while True:
                    err_ready, _, _ = select.select([self.process.stderr], [], [], 0.1)
                    if not err_ready:
                        break
                    line = self.process.stderr.readline()
                    if not line:
                        break
                    stderr_lines.append(line)
                
                if stderr_lines:
                    import sys as _sys
                    print(f"Worker initialization stderr:", file=_sys.stderr)
                    for line in stderr_lines:
                        print(f"  {line.rstrip()}", file=_sys.stderr)
            except:
                pass
            
            # Cleanup
            self.force_shutdown()
            
            raise RuntimeError(f"Worker initialization failed: {e}")

    def execute_shm_task(self, task_id: str, code: str, shm_in: Dict[str, Any], shm_out: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise Exception("Worker not running.")
            
            command = {"type": "execute", "task_id": task_id, "code": code, "shm_in": shm_in, "shm_out": shm_out}
            
            try:
                self.process.stdin.write(json.dumps(command) + '\n')
                self.process.stdin.flush()
                
                # CRITICAL FIX: Timeout on response
                import select
                ready, _, _ = select.select([self.process.stdout], [], [], timeout)
                if not ready:
                    raise TimeoutError(f"Worker response timeout ({timeout}s)")
                
                response_line = self.process.stdout.readline().strip()
                if not response_line:
                    raise RuntimeError("Worker returned empty response")
                
                return json.loads(response_line)
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
        """Forcefully shutdown worker with proper cleanup."""
        with self.lock:
            if self.process:
                try:
                    # Try graceful shutdown first
                    self.process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                    self.process.stdin.flush()
                    self.process.wait(timeout=2)
                except Exception:
                    # Kill entire process group
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except Exception:
                        pass
                finally:
                    self.process = None
            
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
        
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
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
        with open('/dev/null', 'a+') as f:
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
        conn.settimeout(30.0)  # CRITICAL FIX: Client timeout
        try:
            req = recv_json(conn, timeout=30.0)
            self.stats['total_requests'] += 1
            
            if req['type'] == 'execute':
                res = self._execute_code(req['spec'], req['code'], req.get('shm_in', {}), req.get('shm_out', {}))
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
    
    def _execute_code(self, spec: str, code: str, shm_in: dict, shm_out: dict) -> dict:
        # CRITICAL FIX: Use per-spec lock instead of global lock
        with self.worker_locks[spec]:
            # Check if worker exists (within spec lock, not pool lock)
            with self.pool_lock:
                if spec not in self.workers:
                    # Need to create worker - check capacity
                    if len(self.workers) >= self.max_workers:
                        # Evict WITHOUT holding pool lock
                        self._evict_oldest_worker_async()
                    
                    # Create worker
                    try:
                        worker = PersistentWorker(spec)
                        self.workers[spec] = {
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
                
                worker_info = self.workers[spec]
            
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
# 4. CLIENT & PROXY
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 4. CLIENT & PROXY (With Auto-Resurrection)
# ═══════════════════════════════════════════════════════════════

class DaemonClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 30.0, auto_start: bool = True):
        self.socket_path = socket_path
        self.timeout = timeout
        self.auto_start = auto_start

    def execute_shm(self, spec, code, shm_in, shm_out):
        return self._send({'type': 'execute', 'spec': spec, 'code': code, 'shm_in': shm_in, 'shm_out': shm_out})
    
    def status(self):
        # Disable auto-start for status checks to avoid infinite loops if broken
        old_auto = self.auto_start
        self.auto_start = False
        try:
            return self._send({'type': 'status'})
        finally:
            self.auto_start = old_auto
    
    def shutdown(self):
        return self._send({'type': 'shutdown'})

    def _spawn_daemon(self):
        """Spawns the daemon process detached from the current process group."""
        import subprocess
        
        # Use the current file path to restart the daemon logic
        daemon_script = os.path.abspath(__file__)
        
        # CRITICAL: launch with setsid to detach completely
        subprocess.Popen(
            [sys.executable, daemon_script, "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid 
        )

    def _wait_for_socket(self, timeout=5.0):
        """Waits for the socket file to appear and be connectable."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(self.socket_path):
                # Try a quick connect/close to ensure it's actually listening
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect(self.socket_path)
                    s.close()
                    return True
                except (ConnectionRefusedError, OSError):
                    pass # Exists but not listening yet
            time.sleep(0.1)
        return False

    def _send(self, req):
        attempts = 0
        # If auto_start is on, give us enough retries to spawn and connect
        max_attempts = 3 if not self.auto_start else 2 
        
        while attempts < max_attempts:
            attempts += 1
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                
                # Send/Recv
                send_json(sock, req, timeout=self.timeout)
                res = recv_json(sock, timeout=self.timeout)
                sock.close()
                return res

            except (ConnectionRefusedError, FileNotFoundError):
                # Socket is dead or missing
                if not self.auto_start:
                    if attempts >= max_attempts:
                        return {'success': False, 'error': 'Daemon not running'}
                    time.sleep(0.2)
                    continue

                # AUTO-START LOGIC
                # 1. Clean up stale socket if it exists (ConnectionRefused)
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

                # 2. Spawn Daemon
                self._spawn_daemon()
                
                # 3. Wait for it to come up
                if self._wait_for_socket(timeout=5.0):
                    # Reset attempts so we have a fresh try at the now-running daemon
                    attempts = 0 
                    self.auto_start = False # Don't loop infinitely if it crashes immediately
                    continue
                else:
                    return {'success': False, 'error': 'Failed to auto-start daemon (timeout)'}

            except Exception as e:
                # Other transport errors
                return {'success': False, 'error': f'Communication error: {e}'}

        return {'success': False, 'error': 'Connection failed after retries'}
    
    def execute_zero_copy(self, spec: str, code: str, input_array, output_shape, output_dtype):
        """
        🚀 HFT MODE: Zero-Copy Tensor Handoff via Shared Memory.
        """
        import numpy as np
        from multiprocessing import shared_memory
        
        # 1. Setup Input SHM
        # create=True means we allocate new memory in RAM (/dev/shm)
        shm_in = shared_memory.SharedMemory(create=True, size=input_array.nbytes)
        
        # Wrap it in a numpy array so we can write to it
        start_shm = np.ndarray(input_array.shape, dtype=input_array.dtype, buffer=shm_in.buf)
        start_shm[:] = input_array[:] # Copy data into shared buffer
        
        # 2. Setup Output SHM
        dummy = np.zeros(1, dtype=output_dtype)
        out_size = int(np.prod(output_shape)) * dummy.itemsize
        shm_out = shared_memory.SharedMemory(create=True, size=out_size)
        
        try:
            # 3. Construct Metadata Packets for the Worker
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
            
            # 4. Execute via Daemon
            # We send strings (names), not data. 0.00s overhead.
            response = self.execute_shm(spec, code, in_meta, out_meta)
            
            if not response.get('success'):
                raise RuntimeError(f"Worker Error: {response.get('error')}")
            
            # 5. Read Result
            result_view = np.ndarray(output_shape, dtype=output_dtype, buffer=shm_out.buf)
            
            # CRITICAL UPDATE: Return tuple (Data, Metadata)
            return result_view.copy(), response
            
        finally:
            # 6. Cleanup (Client MUST do this, or RAM leaks)
            try:
                shm_in.close()
                shm_in.unlink() # Destroy input block
            except: pass
            
            try:
                shm_out.close()
                shm_out.unlink() # Destroy output block
            except: pass

    def execute_smart(self, spec: str, code: str, data=None):
        """
        🧠 INTELLIGENT DISPATCH:
        - Small Data (< 64KB) -> JSON (Low Overhead)
        - Large Data (>= 64KB) -> Zero-Copy SHM (High Bandwidth)
        """
        import numpy as np
        
        # Threshold: 64KB is roughly where SHM setup cost < JSON serialization cost
        SMART_THRESHOLD = 1024 * 64 
        
        if data is not None and isinstance(data, np.ndarray) and data.nbytes >= SMART_THRESHOLD:
            # 🚀 LARGE DATA PATH: Zero-Copy SHM
            # For generic execution, we assume output shape matches input shape/dtype 
            # (or you can extend this method to accept output specs)
            output_shape = data.shape 
            output_dtype = data.dtype
            
            # Helper code wrapper to map generic var names
            wrapped_code = f"""
# Smart Wrapper
{code}
"""
            result, meta = self.execute_zero_copy(
                spec,
                wrapped_code,
                data,
                output_shape,
                output_dtype
            )
            return {'success': True, 'result': result, 'meta': meta, 'transport': 'SHM'}
            
        else:
            # 🐢 SMALL DATA PATH: JSON via Socket
            # Serialize input if it exists
            prefix = ""
            if data is not None:
                if isinstance(data, np.ndarray):
                    prefix = f"import numpy as np\narr_in = np.array({data.tolist()})\n"
                else:
                    prefix = f"arr_in = {json.dumps(data)}\n"
            
            # Execute
            response = self.execute_shm(spec, prefix + code, {}, {})
            
            # Normalize response structure
            if response.get('success'):
                return {'success': True, 'result': response.get('stdout', '').strip(), 'meta': response, 'transport': 'JSON'}
            return response

class DaemonProxy:
    """Proxies calls from Loader to the Daemon via Socket/SHM"""
    def __init__(self, client, package_spec):
        self.client = client
        self.spec = package_spec
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