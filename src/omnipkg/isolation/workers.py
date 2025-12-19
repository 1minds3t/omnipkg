import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


class PersistentWorker:
    """
    A persistent subprocess that acts as a specific package environment.
    Provides REAL-TIME output streaming.
    """

    def __init__(self, package_spec: str, verbose: bool = False):
        self.package_spec = package_spec
        self.verbose = verbose
        self.process = None
        self._log_queue = queue.Queue()
        self._stop_logging = threading.Event()
        self._start_worker()

    def _start_worker(self):
        # Calculate root path FIRST
        current_file = Path(__file__).resolve()
        src_root = str(current_file.parent.parent.parent)

        # Store package_spec in local variable for f-string
        package_spec = self.package_spec

        # Build worker code WITH all variables available
        worker_code = f"""
import sys
import os
import json
import traceback
import io
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
# CRITICAL: Disable buffering on stderr for real-time output
sys.stderr = open(sys.stderr.fileno(), 'w', buffering=1, closefd=False)

# 1. SETUP COMM CHANNEL
try:
    # Duplicate stdout for clean JSON channel
    ifd = os.dup(sys.stdout.fileno())
    ipc_pipe = os.fdopen(ifd, 'w', buffering=1)
    
    # Redirect print() to stderr
    sys.stdout = sys.stderr
except Exception as e:
    sys.stderr.write(f"FATAL SETUP ERROR: {{e}}\\n")
    sys.stderr.flush()
    sys.exit(1)

def log(msg):
    sys.stderr.write(msg + "\\n")
    sys.stderr.flush()

def send_ipc(data):
    try:
        ipc_pipe.write(json.dumps(data) + "\\n")
        ipc_pipe.flush()
    except Exception as e:
        log(f"IPC ERROR: {{e}}")

# 2. ADD OMNIPKG TO PATH
try:
    import omnipkg
except ImportError:
    sys.path.insert(0, r"{src_root}")

from omnipkg.loader import omnipkgLoader

try:
    log(f"🐚 Worker initializing environment: {package_spec}...")
    loader = omnipkgLoader("{package_spec}", quiet=False, worker_fallback=False)
    loader.__enter__()
    
    send_ipc({{"status": "ready"}})
    log(f"✅ Worker ready: {package_spec}")
    
except Exception as e:
    send_ipc({{"status": "error", "message": str(e)}})
    traceback.print_exc()
    sys.exit(1)

# 3. COMMAND LOOP
while True:
    try:
        line = sys.stdin.readline()
        if not line: break
        
        cmd = json.loads(line)
        
        if cmd['type'] == 'execute':
            try:
                # Capture stdout for return value
                f = io.StringIO()
                
                # Create a custom stdout that duplicates to both buffer and stderr
                class TeeOutput:
                    def __init__(self, buffer, stream):
                        self.buffer = buffer
                        self.stream = stream
                    
                    def write(self, text):
                        self.buffer.write(text)
                        self.stream.write(text)
                        self.stream.flush()
                    
                    def flush(self):
                        self.buffer.flush()
                        self.stream.flush()
                
                old_stdout = sys.stdout
                
                # Tee stdout to both buffer and stderr
                sys.stdout = TeeOutput(f, sys.stderr)
                
                try:
                    loc = {{}}
                    exec(cmd['code'], globals(), loc)
                finally:
                    sys.stdout = old_stdout
                
                output = f.getvalue()
                send_ipc({{"success": True, "stdout": output, "locals": str(list(loc.keys()))}})
            except Exception as e:
                log(f"EXECUTION ERROR: {{e}}")
                traceback.print_exc()
                send_ipc({{"success": False, "error": str(e)}})
                
        elif cmd['type'] == 'get_version':
            try:
                pkg_name = cmd['package']
                mod = __import__(pkg_name)
                send_ipc({{"success": True, "version": mod.__version__, "path": mod.__file__}})
            except Exception as e:
                send_ipc({{"success": False, "error": str(e)}})

        elif cmd['type'] == 'shutdown':
            break
            
    except Exception as e:
        log(f"LOOP ERROR: {{e}}")
        break

try:
    loader.__exit__(None, None, None)
except:
    pass
"""

        # Setup environment with worker flag
        env = os.environ.copy()
        # Prevent infinite worker recursion
        env["OMNIPKG_IS_WORKER_PROCESS"] = "1"

        # CRITICAL: Disable buffering in subprocess
        # NOW worker_code is defined, so we can use it!
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", worker_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=True,
            env=env,
        )

        # Start logging thread
        self._log_thread = threading.Thread(target=self._stream_logs, daemon=True)
        self._log_thread.start()

        # Handshake
        try:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("Worker process died immediately.")
            data = json.loads(line)
            if data.get("status") != "ready":
                raise RuntimeError(f"Worker initialization failed: {data}")
        except Exception as e:
            self._stop_logging.set()
            raise RuntimeError(f"Handshake failed: {e}")

    def _stream_logs(self):
        """Streams stderr from worker to console in real-time."""
        prefix = f"[{self.package_spec}] "
        try:
            while not self._stop_logging.is_set():
                line = self.process.stderr.readline()
                if not line:
                    break
                if self.verbose:
                    sys.stdout.write(f"{prefix}{line}")
                    sys.stdout.flush()
        except (ValueError, OSError):
            pass

    def execute(self, code: str) -> dict:
        """Run Python code in the worker."""
        return self._send({"type": "execute", "code": code})

    def get_version(self, package_name: str) -> dict:
        """Query the worker for a package version."""
        return self._send({"type": "get_version", "package": package_name})

    def _send(self, payload: dict) -> dict:
        if self.process.poll() is not None:
            return {"success": False, "error": "Process is dead"}

        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()

            response = self.process.stdout.readline()
            if not response:
                return {"success": False, "error": "No response"}
            return json.loads(response)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def shutdown(self):
        self._stop_logging.set()
        if self.process:
            try:
                self.process.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                self.process.stdin.flush()
                self.process.wait(timeout=1)
            except:
                self.process.kill()
