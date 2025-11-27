import sys
import os
import json
import subprocess
import threading
import queue
from pathlib import Path

class PersistentWorker:
    """
    A persistent subprocess that acts as a dedicated worker for a specific package environment.
    It keeps the environment loaded in memory for ultra-fast execution.
    """
    def __init__(self, package_spec: str, verbose: bool = True):
        self.package_spec = package_spec
        self.verbose = verbose
        self.process = None
        self._log_queue = queue.Queue()
        self._stop_logging = threading.Event()
        self._start_worker()

    def _start_worker(self):
        # Calculate root path to ensure worker can find omnipkg
        current_file = Path(__file__).resolve()
        src_root = str(current_file.parent.parent.parent)

        # The Worker Script
        # We redirect sys.stdout to sys.stderr so that 'print()' calls don't break our JSON pipe.
        # We use a duplicate file descriptor (ipc_pipe) specifically for data.
        worker_code = f"""
import sys
import os
import json
import traceback
import io
import contextlib

# 1. SETUP COMM CHANNEL
try:
    # Duplicate stdout to keep a clean channel for JSON
    ipc_fd = os.dup(sys.stdout.fileno())
    ipc_pipe = os.fdopen(ipc_fd, 'w')
    
    # Redirect all future print() calls to stderr (which the parent logs)
    sys.stdout = sys.stderr
except Exception as e:
    sys.stderr.write(f"FATAL SETUP ERROR: {{e}}\\n")
    sys.exit(1)

def send_ipc(data):
    try:
        ipc_pipe.write(json.dumps(data) + "\\n")
        ipc_pipe.flush()
    except Exception as e:
        sys.stderr.write(f"IPC ERROR: {{e}}\\n")

# 2. ADD OMNIPKG TO PATH
try:
    import omnipkg
except ImportError:
    sys.path.insert(0, r"{src_root}")

from omnipkg.loader import omnipkgLoader

try:
    print(f"🐍 Worker initializing environment: {self.package_spec}...")
    loader = omnipkgLoader("{self.package_spec}", quiet=True)
    loader.__enter__()
    
    # Prove we are ready
    send_ipc({{"status": "ready"}})
    print(f"✅ Worker ready: {self.package_spec}")
    
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
                # Capture stdout so we can return it
                f = io.StringIO()
                with contextlib.redirect_stdout(f):
                    # We use a dict for locals to capture variables
                    loc = {{}}
                    exec(cmd['code'], globals(), loc)
                
                output = f.getvalue()
                send_ipc({{"success": True, "stdout": output, "locals": str(loc.keys())}})
            except Exception as e:
                traceback.print_exc() # Print trace to logs
                send_ipc({{"success": False, "error": str(e)}})
                
        elif cmd['type'] == 'get_version':
            # Specific helper to prove version switching
            try:
                pkg_name = cmd['package']
                mod = __import__(pkg_name)
                send_ipc({{"success": True, "version": mod.__version__, "path": mod.__file__}})
            except Exception as e:
                send_ipc({{"success": False, "error": str(e)}})

        elif cmd['type'] == 'shutdown':
            break
            
    except Exception as e:
        sys.stderr.write(f"LOOP ERROR: {{e}}\\n")
        break

try:
    loader.__exit__(None, None, None)
except:
    pass
"""

        self.process = subprocess.Popen(
            [sys.executable, "-c", worker_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,  # This is the JSON data pipe
            stderr=subprocess.PIPE,  # This is the Log pipe
            text=True,
            bufsize=1
        )

        # Start a background thread to print logs from the worker
        self._log_thread = threading.Thread(target=self._stream_logs, daemon=True)
        self._log_thread.start()

        # Handshake
        try:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("Worker process died immediately.")
            data = json.loads(line)
            if data.get('status') != 'ready':
                raise RuntimeError(f"Worker initialization failed: {data}")
        except Exception as e:
            # Wait a moment for the log thread to maybe catch the traceback
            self._stop_logging.set()
            raise RuntimeError(f"Worker handshake failed: {e}")

    def _stream_logs(self):
        """Reads stderr from the worker and prints it to the main console."""
        prefix = f"[{self.package_spec}] "
        try:
            for line in iter(self.process.stderr.readline, ''):
                if self._stop_logging.is_set(): break
                if self.verbose:
                    # Print raw worker logs to our stdout
                    sys.stdout.write(f"{prefix} {line}")
                    sys.stdout.flush()
        except ValueError:
            pass # File closed

    def execute(self, code: str) -> dict:
        """Run arbitrary Python code in the worker."""
        return self._send({"type": "execute", "code": code})

    def get_version(self, package_name: str) -> dict:
        """Ask the worker what version of a package it has loaded."""
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