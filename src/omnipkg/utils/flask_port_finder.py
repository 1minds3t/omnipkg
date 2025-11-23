#!/usr/bin/env python3
from __future__ import annotations
"""
Flask Port Finder - Robust Version
"""

import socket
import re
import sys
import platform
import time
import os
import threading
import atexit
import tempfile
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Optional, Tuple

try:
    from ..common_utils import safe_print
except ImportError:
    try:
        from omnipkg.common_utils import safe_print
    except ImportError:
        def safe_print(*args, **kwargs):
            try:
                print(*args, **kwargs)
            except:
                pass

# Global port reservation system (thread-safe)
_port_lock = threading.Lock()
_reserved_ports = set()
_port_pids = {}

def is_windows():
    return platform.system() == 'Windows' or sys.platform == 'win32'

def reserve_port(port: int, duration: float = 5.0) -> bool:
    with _port_lock:
        if port in _reserved_ports: return False
        _reserved_ports.add(port)
        _port_pids[port] = os.getpid()
    
    def release_later():
        time.sleep(duration)
        release_port(port)
    
    threading.Thread(target=release_later, daemon=True).start()
    return True

def release_port(port: int):
    with _port_lock:
        _reserved_ports.discard(port)
        _port_pids.pop(port, None)

def is_port_actually_free(port: int) -> bool:
    try:
        if is_windows():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Windows: Do not use SO_REUSEADDR for availability check to ensure exclusivity
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
    except:
        return False

def find_free_port(start_port=5000, max_attempts=100, reserve=True) -> int:
    for port in range(start_port, start_port + max_attempts):
        with _port_lock:
            if port in _reserved_ports: continue
        
        if not is_port_actually_free(port): continue
        
        if reserve:
            if not reserve_port(port, duration=10.0): continue
        
        return port
    
    raise RuntimeError(f"Could not find free port in range {start_port}-{start_port + max_attempts}")

def validate_flask_app(code: str, port: int, timeout: float = 5.0) -> bool:
    # Validation logic
    validation_code = f'''
import sys
try:
    exec({repr(code)}, {{'__name__': '__omnipkg_validation__'}})
    print("VALIDATION_SUCCESS")
except Exception as e:
    print(f"VALIDATION_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    try:
        result = subprocess.run(
            [sys.executable, '-c', validation_code],
            capture_output=True, text=True, timeout=timeout
        )
        return 'VALIDATION_SUCCESS' in result.stdout
    except:
        return False

class FlaskAppManager:
    def __init__(self, code: str, port: int, validate_only: bool = False):
        self.code = code
        self.port = port
        self.validate_only = validate_only
        self.process = None
        self.is_running = False
        self.shutdown_file = Path(tempfile.gettempdir()) / f"flask_shutdown_{port}.signal"
        
        if self.shutdown_file.exists():
            self.shutdown_file.unlink()
        
        atexit.register(self.shutdown)
    
    def start(self) -> bool:
        if self.validate_only:
            safe_print(f"🔍 Validating Flask app on port {self.port}...")
            return validate_flask_app(self.code, self.port)
        
        # Remove __main__ block
        cleaned_code = re.sub(r"if\s+__name__\s*==\s*['\"]__main__['\"]:\s*\n((?:\s+.*\n)*)", "", self.code, flags=re.MULTILINE)
        
        wrapper_code = f'''
import signal, sys, time, threading
from pathlib import Path

shutdown_file = Path("{self.shutdown_file.as_posix()}")
def check_shutdown():
    if shutdown_file.exists(): sys.exit(0)

threading.Thread(target=lambda: [time.sleep(0.5) or check_shutdown() for _ in iter(int, 1)], daemon=True).start()

{cleaned_code}

try:
    if 'app' in dir():
        try:
            from waitress import serve
            serve(app, host='127.0.0.1', port={self.port}, threads=4)
        except ImportError:
            app.run(host='127.0.0.1', port={self.port}, debug=False, use_reloader=False)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
'''
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(wrapper_code)
                temp_file = f.name
            
            # CRITICAL FIX FOR WINDOWS/MAC DEADLOCKS:
            # -u: Unbuffered output.
            # stdout=subprocess.DEVNULL: Discard access logs to prevent pipe buffer fill.
            # stderr=None: Inherit stderr so exceptions (like ModuleNotFoundError) show up in CI logs.
            self.process = subprocess.Popen(
                [sys.executable, '-u', temp_file],
                stdout=subprocess.DEVNULL,
                stderr=None, 
                cwd=os.getcwd()
            )
            
            self.is_running = True
            safe_print(f"✅ Flask app subprocess started (PID: {self.process.pid})")
            return True
        except Exception as e:
            safe_print(f"❌ Failed to start Flask app: {e}")
            return False
    
    def shutdown(self):
        if not self.is_running or not self.process:
            release_port(self.port); return
        
        try:
            self.shutdown_file.write_text("SHUTDOWN")
            try: self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired: self.process.kill()
            
            if self.shutdown_file.exists(): self.shutdown_file.unlink()
            release_port(self.port)
            self.is_running = False
        except Exception:
            if self.process: self.process.kill()
            release_port(self.port)

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    if sock.connect_ex(('127.0.0.1', self.port)) == 0:
                        safe_print(f"✅ Flask app is ready on port {self.port}")
                        return True
            except: pass
            time.sleep(0.2)
        safe_print(f"⚠️ Flask app did not become ready within {timeout}s")
        return False


def patch_flask_code(code: str, interactive: bool = False, validate_only: bool = False) -> Tuple[str, int, Optional[FlaskAppManager]]:
    free_port = find_free_port(reserve=True)
    pattern = r'app\.run\s*\([^)]*\)'
    
    if re.search(pattern, code):
        patched_code = re.sub(pattern, f"app.run(host='127.0.0.1', port={free_port}, debug=False, use_reloader=False)", code)
    else:
        patched_code = code
    
    manager = FlaskAppManager(patched_code, free_port, validate_only) if interactive else None
    return patched_code, free_port, manager

def auto_patch_flask_port(code: str, interactive: bool = False, validate_only: bool = False) -> str:
    if 'flask' in code.lower() and re.search(r'app\.run\s*\(', code):
        patched_code, port, manager = patch_flask_code(code, interactive, validate_only)
        if manager:
            if manager.start():
                if not validate_only: safe_print(f"🔧 Flask app running on port {port}")
            else: safe_print(f"❌ Flask app failed to start")
        else: safe_print(f"🔧 Auto-patched Flask app to use port {port}")
        return patched_code
    return code