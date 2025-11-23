from __future__ import annotations  # Python 3.6+ compatibility
"""
Flask Port Finder - Automatically finds available ports for Flask apps and patches app.run() calls to use them.

Features:
- Windows-compatible socket handling
- Concurrent-safe port allocation (prevents race conditions)
- Interactive mode with validation-only option
- Graceful shutdown handling
- Port reservation system

Usage:
1. Add this to the test execution wrapper
2. It will automatically patch Flask apps to use random available ports
"""

import socket
import re
import sys
import platform
import time
import os
import signal
import threading
import atexit
import tempfile
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Optional, Tuple
import concurrent.futures

try:
    from ..common_utils import safe_print
except ImportError:
    try:
        from omnipkg.common_utils import safe_print
    except ImportError:
        def safe_print(*args, **kwargs):
            try:
                print(*args, **kwargs)
            except (UnicodeEncodeError, UnicodeDecodeError):
                msg = ' '.join(str(arg).encode('ascii', 'replace').decode('ascii') for arg in args)
                print(msg, **kwargs)

# Global port reservation system (thread-safe)
_port_lock = threading.Lock()
_reserved_ports = set()
_port_pids = {}  # Track which PID owns which port

def is_windows():
    """Check if running on Windows."""
    return platform.system() == 'Windows' or sys.platform == 'win32'

def reserve_port(port: int, duration: float = 5.0) -> bool:
    """
    Reserve a port to prevent concurrent allocation race conditions.
    """
    with _port_lock:
        if port in _reserved_ports:
            return False
        _reserved_ports.add(port)
        _port_pids[port] = os.getpid()
    
    def release_later():
        time.sleep(duration)
        release_port(port)
    
    threading.Thread(target=release_later, daemon=True).start()
    return True

def release_port(port: int):
    """Release a reserved port."""
    with _port_lock:
        _reserved_ports.discard(port)
        _port_pids.pop(port, None)

def is_port_actually_free(port: int) -> bool:
    """
    Double-check if a port is actually free (not just unreserved).
    """
    try:
        if is_windows():
            # Windows specific: Don't use SO_REUSEADDR for availability check.
            # We want to know if it's TRULY free and exclusive to prevent
            # "Permission denied" or binding conflicts later.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # REMOVED FOR WINDOWS STABILITY
                sock.bind(('127.0.0.1', port))
                sock.close()
                return True
            except OSError:
                sock.close()
                return False
        else:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', port))
                return True
    except OSError:
        return False
    except Exception:
        return False

def find_free_port(start_port=5000, max_attempts=100, reserve=True) -> int:
    """
    Find an available port with concurrent safety.
    """
    for port in range(start_port, start_port + max_attempts):
        with _port_lock:
            if port in _reserved_ports:
                continue
        
        if not is_port_actually_free(port):
            continue
        
        if reserve:
            if not reserve_port(port, duration=10.0):
                continue
        
        return port
    
    raise RuntimeError(f"Could not find free port in range {start_port}-{start_port + max_attempts}")

def validate_flask_app(code: str, port: int, timeout: float = 5.0) -> bool:
    """
    Validate Flask app can start without actually running it persistently.
    Uses Flask's test client for validation instead of real server.
    """
    validation_code = f'''
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
app_code = {repr(code)}
# SAFETY: Prevent app.run() from executing by changing __name__
exec_globals = {{'__name__': '__omnipkg_validation__'}}
try:
    exec(app_code, exec_globals)
except Exception as e:
    print(f"EXEC_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)

app = exec_globals.get('app')
if app is None:
    # Fallback: try to find any Flask instance
    from flask import Flask
    for val in exec_globals.values():
        if isinstance(val, Flask):
            app = val
            break

if app is None:
    print("ERROR: No Flask app found", file=sys.stderr)
    sys.exit(1)

try:
    # Use test client to verify app structure without binding port
    with app.test_client() as client:
        # Just checking we can create the client is often enough
        response = client.get('/_omnipkg_health_check')
        print(f"VALIDATION_SUCCESS: App validated")
        sys.exit(0)
except Exception as e:
    print(f"VALIDATION_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    
    try:
        # validate_flask_app uses capture_output=True which is safe because run() drains pipes
        result = subprocess.run(
            [sys.executable, '-c', validation_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy()
        )
        
        if result.returncode == 0 and 'VALIDATION_SUCCESS' in result.stdout:
            return True
        
        if result.stderr:
            safe_print(f"Flask validation failed details: {result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        safe_print(f"Flask validation timed out after {timeout}s")
        return False
    except Exception as e:
        safe_print(f"Flask validation error: {e}")
        return False

class FlaskAppManager:
    """
    Manages Flask app lifecycle with graceful shutdown support.
    """
    
    def __init__(self, code: str, port: int, validate_only: bool = False):
        self.code = code
        self.port = port
        self.validate_only = validate_only
        self.process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.shutdown_file = Path(tempfile.gettempdir()) / f"flask_shutdown_{port}.signal"
        
        if self.shutdown_file.exists():
            self.shutdown_file.unlink()
        
        atexit.register(self.shutdown)
    
    def start(self) -> bool:
        """Start the Flask app (or just validate it)."""
        if self.validate_only:
            safe_print(f"🔍 Validating Flask app on port {self.port}...")
            return validate_flask_app(self.code, self.port)
        
        # Remove any if __name__ == '__main__': blocks to prevent premature app.run()
        import re
        cleaned_code = re.sub(
            r"if\s+__name__\s*==\s*['\"]__main__['\"]:\s*\n((?:\s+.*\n)*)",
            "",
            self.code,
            flags=re.MULTILINE
        )
        
        wrapper_code = f'''
import signal
import sys
import time
from pathlib import Path

shutdown_file = Path("{self.shutdown_file.as_posix()}")

def check_shutdown_signal(signum=None, frame=None):
    if shutdown_file.exists():
        sys.exit(0)

signal.signal(signal.SIGTERM, check_shutdown_signal)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, check_shutdown_signal)

import threading
def periodic_check():
    while True:
        time.sleep(0.5)
        check_shutdown_signal()

threading.Thread(target=periodic_check, daemon=True).start()

# User's code (with __main__ block removed)
{cleaned_code}

# Use waitress instead of Flask's built-in server for better subprocess compatibility
try:
    if 'app' in dir():
        # print("DEBUG: Starting with waitress", flush=True) 
        from waitress import serve
        # print("DEBUG: Serving on 127.0.0.1:{self.port}", flush=True)
        serve(app, host='127.0.0.1', port={self.port}, threads=4)
    else:
        sys.exit(1)
except ImportError:
    # print("DEBUG: Waitress not available, falling back to Flask dev server", flush=True)
    if 'app' in dir():
        app.run(host='127.0.0.1', port={self.port}, debug=False, use_reloader=False, threaded=True)
    else:
        sys.exit(1)
except Exception as e:
    sys.exit(1)
'''
    
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(wrapper_code)
                temp_file = f.name
            
            # CRITICAL WINDOWS FIX:
            # We use subprocess.DEVNULL for stdout/stderr to prevent pipe buffering deadlocks.
            # In CI (specifically with unittest), the parent process captures stdout but 
            # might stop reading it while waiting for the socket. If the child fills the 
            # pipe buffer, the child blocks, leading to a deadlock/timeout.
            self.process = subprocess.Popen(
                [sys.executable, '-u', temp_file], # -u forces unbuffered python
                stdout=subprocess.DEVNULL,         # Send output to void to avoid deadlock
                stderr=subprocess.DEVNULL,
                cwd=os.getcwd()
            )
            
            self.is_running = True
            safe_print(f"✅ Flask app started on port {self.port} (PID: {self.process.pid})")
            # safe_print(f"🌐 Access at: http://127.0.0.1:{self.port}")
            
            return True
        except Exception as e:
            safe_print(f"❌ Failed to start Flask app: {e}")
            return False
    
    def shutdown(self):
        """Gracefully shutdown the Flask app."""
        if not self.is_running and self.process is None:
            release_port(self.port)
            return
        
        if self.process is None:
            release_port(self.port)
            return
        
        try:
            self.shutdown_file.write_text("SHUTDOWN")
            try:
                self.process.wait(timeout=3.0)
                safe_print(f"✅ Flask app (PID {self.process.pid}) shut down gracefully")
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                safe_print(f"⚠️  Flask app (PID {self.process.pid}) force killed")
            
            if self.shutdown_file.exists():
                self.shutdown_file.unlink()
            
            release_port(self.port)
            self.is_running = False
        except Exception as e:
            safe_print(f"⚠️  Error during shutdown: {e}")
            release_port(self.port)

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Wait for Flask app to be ready to accept connections."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Use a short timeout for connection attempts
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    result = sock.connect_ex(('127.0.0.1', self.port))
                    if result == 0:
                        safe_print(f"✅ Flask app is ready on port {self.port}")
                        return True
            except:
                pass
            time.sleep(0.2)
        
        safe_print(f"⚠️ Flask app did not become ready within {timeout}s")
        return False


def patch_flask_code(code: str, interactive: bool = False, validate_only: bool = False) -> Tuple[str, int, Optional[FlaskAppManager]]:
    """
    Patch Flask code to use an available port.
    """
    free_port = find_free_port(reserve=True)
    pattern = r'app\.run\s*\([^)]*\)'
    
    if not re.search(pattern, code):
        patched_code = code
    else:
        # Always use 127.0.0.1 for maximum compatibility
        patched_code = re.sub(
            pattern,
            f"app.run(host='127.0.0.1', port={free_port}, debug=False, use_reloader=False)",
            code
        )
    
    manager = FlaskAppManager(patched_code, free_port, validate_only) if interactive else None
    return patched_code, free_port, manager

def auto_patch_flask_port(code: str, interactive: bool = False, validate_only: bool = False) -> str:
    """
    Automatically patch Flask code to use an available port.
    """
    if 'flask' in code.lower() and re.search(r'app\.run\s*\(', code):
        patched_code, port, manager = patch_flask_code(code, interactive, validate_only)
        
        if manager:
            success = manager.start()
            if success and not validate_only:
                safe_print(f"🔧 Flask app running on port {port}")
            elif not success:
                safe_print(f"❌ Flask app failed to start/validate")
        else:
            safe_print(f"🔧 Auto-patched Flask app to use port {port}", file=sys.stderr)
        
        return patched_code
    return code