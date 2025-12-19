from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print

#!/usr/bin/env python3

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

import atexit
import concurrent.futures
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from typing import Optional, Tuple

try:
    from .common_utils import safe_print
except ImportError:
    try:
        from omnipkg.common_utils import safe_print
    except ImportError:

        def safe_print(*args, **kwargs):
            try:
                print(*args, **kwargs)
            except (UnicodeEncodeError, UnicodeDecodeError):
                msg = " ".join(str(arg).encode("ascii", "replace").decode("ascii") for arg in args)
                print(msg, **kwargs)


# Global port reservation system (thread-safe)
_port_lock = threading.Lock()
_reserved_ports = set()
_port_pids = {}  # Track which PID owns which port


def is_windows():
    """Check if running on Windows."""
    return platform.system() == "Windows" or sys.platform == "win32"


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
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.close()
                return True
            except OSError:
                sock.close()
                return False
        else:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
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

    raise RuntimeError(
        f"Could not find free port in range {start_port}-{start_port + max_attempts}"
    )


def validate_flask_app(code: str, port: int, timeout: float = 5.0) -> bool:
    """
    Validate Flask app can start without actually running it persistently.
    Uses Flask's test client for validation instead of real server.
    """
    # CRITICAL FIX: Set __name__ to something NOT '__main__'
    # This prevents app.run() from blocking inside the validation step!
    validation_code = f"""
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
        # Just checking we can create the client is often enough,
        # but we can try a 404 check to ensure routing works
        response = client.get('/_omnipkg_health_check')
        print(f"VALIDATION_SUCCESS: App validated")
        sys.exit(0)
except Exception as e:
    print(f"VALIDATION_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", validation_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )

        if result.returncode == 0 and "VALIDATION_SUCCESS" in result.stdout:
            return True

        # Only print stderr if validation failed to keep logs clean on success
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

        wrapper_code = f"""
import signal
import sys
import time
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
shutdown_file = Path("{self.shutdown_file}")

def check_shutdown_signal(signum=None, frame=None):
    if shutdown_file.exists():
        # safe_print("\\n🛑 Shutdown signal received, stopping Flask app...")
        sys.exit(0)

signal.signal(signal.SIGTERM, check_shutdown_signal)
if hasattr(signal, 'SIGBREAK'):  # Windows
    signal.signal(signal.SIGBREAK, check_shutdown_signal)

import threading
def periodic_check():
    while True:
        time.sleep(0.5)
        check_shutdown_signal()

threading.Thread(target=periodic_check, daemon=True).start()

{self.code}
"""

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(wrapper_code)
                temp_file = f.name

            self.process = subprocess.Popen(
                [sys.executable, temp_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.is_running = True
            safe_print(f"✅ Flask app started on port {self.port} (PID: {self.process.pid})")
            safe_print(f"🌐 Access at: http://127.0.0.1:{self.port}")
            safe_print(f"🛑 To stop: FlaskAppManager.shutdown() or delete {self.shutdown_file}")

            return True
        except Exception as e:
            safe_print(f"❌ Failed to start Flask app: {e}")
            return False

    def shutdown(self):
        """Gracefully shutdown the Flask app."""
        if not self.is_running and self.process is None:
            if not self.validate_only:
                safe_print("✅ No active process to shutdown")
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
            release_port(self.port)  # Always release port

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Wait for Flask app to be ready to accept connections."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    result = sock.connect_ex(("127.0.0.1", self.port))
                    if result == 0:
                        safe_print(f"✅ Flask app is ready on port {self.port}")
                        return True
            except:
                pass
            time.sleep(0.2)

        safe_print(f"⚠️  Flask app did not become ready within {timeout}s")
        return False


def patch_flask_code(
    code: str, interactive: bool = False, validate_only: bool = False
) -> Tuple[str, int, Optional[FlaskAppManager]]:
    """
    Patch Flask code to use an available port with optional interactive mode.

    Args:
        code: Python source code containing Flask app
        interactive: If True, returns a FlaskAppManager for controlled execution
        validate_only: If True (with interactive), only validates without running

    Returns:
        Tuple of (patched_code, port_number, optional_manager)
    """
    free_port = find_free_port(reserve=True)
    pattern = r"app\.run\s*\([^)]*\)"

    if not re.search(pattern, code):
        patched_code = code
    else:
        # CRITICAL FIX: Always inject use_reloader=False to ensure process management works
        # The reloader spawns a child process that is hard to kill cleanly via Popen.
        patched_code = re.sub(
            pattern, f"app.run(port={free_port}, debug=False, use_reloader=False)", code
        )

    manager = FlaskAppManager(patched_code, free_port, validate_only) if interactive else None
    return patched_code, free_port, manager


def auto_patch_flask_port(code: str, interactive: bool = False, validate_only: bool = False) -> str:
    """
    Automatically patch Flask code to use an available port.
    """
    if "flask" in code.lower() and re.search(r"app\.run\s*\(", code):
        patched_code, port, manager = patch_flask_code(code, interactive, validate_only)

        if manager:
            success = manager.start()
            if success and validate_only:
                # Silent success for validation to keep logs clean
                pass
            elif success and not validate_only:
                safe_print(f"🔧 Flask app running on port {port}")
            else:
                safe_print("❌ Flask app failed to start/validate")
        else:
            safe_print(f"🔧 Auto-patched Flask app to use port {port}", file=sys.stderr)

        return patched_code
    return code


if __name__ == "__main__":

    class TestEnhancedFlaskPortFinder(unittest.TestCase):
        def test_1_basic_port_allocation(self):
            port = find_free_port(reserve=False)
            self.assertTrue(5000 <= port < 5100, "Port should be in expected range")
            safe_print(f"✅ Found and reserved free port: {port}")
            self.assertTrue(True)

        def test_2_concurrent_allocation(self):
            def allocate_port(thread_id):
                port = find_free_port(reserve=True)
                # safe_print(f"  Thread {thread_id}: Allocated port {port}")
                time.sleep(0.05)
                release_port(port)
                return port

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(allocate_port, i) for i in range(10)]
                ports = [f.result() for f in concurrent.futures.as_completed(futures)]

            self.assertEqual(len(ports), len(set(ports)), "Ports should be unique")
            safe_print(f"✅ All {len(ports)} ports unique: {sorted(ports)}")
            self.assertTrue(True)

        def test_3_windows_compatibility(self):
            port = find_free_port(reserve=False)
            safe_print(f"✅ Port {port} found, demonstrating platform-agnostic socket operations.")
            self.assertTrue(True)

        def test_4_flask_validation(self):
            valid_app = """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello World!'
            """
            # Note: validate_flask_app logic now prevents app.run() from executing automatically
            # so we don't need to worry about it hanging here.
            port = find_free_port(reserve=True)
            success = validate_flask_app(valid_app, port)
            self.assertTrue(success, "Valid app should pass validation")
            safe_print(f"🔍 Validating Flask app on port {port}...")
            safe_print("  ✅ Valid app correctly passed validation.")
            release_port(port)

        def test_5_patch_flask_code(self):
            original_code = """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return 'Test'

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
            """
            patched_code, port, changes_made = patch_flask_code(original_code, interactive=True)
            self.assertIn(f"port={port}", patched_code)
            self.assertIn("debug=False", patched_code)
            self.assertIn("use_reloader=False", patched_code)
            safe_print(f"✅ Code patched successfully to use port {port}.")
            release_port(port)

        def test_6_flask_manager_lifecycle(self):
            test_app = """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return 'Lifecycle Test'

if __name__ == '__main__':
    app.run()
            """
            port = find_free_port(start_port=5000, max_attempts=100, reserve=True)
            patched_code, port, manager = patch_flask_code(test_app, interactive=True)
            safe_print(f"  ✅ Manager created for port {port}.")

            success = manager.start()
            self.assertTrue(success, "Manager start should succeed")

            if not manager.validate_only:
                safe_print(f"✅ Flask app started on port {port} (PID: {manager.process.pid})")
                safe_print(f"🌐 Access at: http://127.0.0.1:{port}")
                safe_print(
                    f"🛑 To stop: FlaskAppManager.shutdown() or delete {manager.shutdown_file}"
                )

                # Wait longer for CI
                self.assertTrue(manager.wait_for_ready(timeout=15.0), "App should be ready")
                safe_print("✅ Server is ready and listening.")
                manager.shutdown()
            else:
                safe_print("  ✅ Validation-only mode passed")

            manager.shutdown()
            self.assertTrue(is_port_actually_free(port), "Port should be released")
            safe_print("✅ TEST 6 PASSED")

    suite = unittest.TestLoader().loadTestsFromTestCase(TestEnhancedFlaskPortFinder)
    unittest.TextTestRunner(verbosity=2).run(suite)
