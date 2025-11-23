#!/usr/bin/env python3
"""
SUPER VERBOSE DEBUG VERSION - Let's find out WTF is happening!
"""
import sys
import os
import subprocess
import time
from pathlib import Path
import socket
import unittest
import threading
import requests
import importlib.util

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    if importlib.util.find_spec("omnipkg.utils.flask_port_finder") is None:
        raise ImportError
    from omnipkg.utils.flask_port_finder import (
        find_free_port,
        release_port,
        patch_flask_code,
        FlaskAppManager,
        safe_print
    )
except ImportError:
    print("Warning: 'omnipkg' not found. Using mock objects for demonstration.")
    _reserved_ports = set()
    _lock = threading.Lock()
    
    def safe_print(message, **kwargs):
        print(message, file=sys.stderr, **kwargs)
    
    def find_free_port(start_port=5000, max_attempts=1000, reserve=False):
        for port in range(start_port, start_port + max_attempts):
            with _lock:
                if port in _reserved_ports: continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                if reserve:
                    with _lock: _reserved_ports.add(port)
                return port
            except OSError:
                continue
        raise IOError("No free ports found.")
    
    def release_port(port):
        with _lock:
            if port in _reserved_ports: _reserved_ports.remove(port)
    
    class FlaskAppManager:
        def __init__(self, code, port, interactive=False, validate_only=False):
            self.code = code
            self.port = port
            self.interactive = interactive
            self.validate_only = validate_only
            self.process = None
        
        def start(self):
            if self.validate_only:
                return "import" not in self.code or "flask" in self.code.lower()
            
            print(f"\n🔍 DEBUG: Starting Flask on port {self.port}", file=sys.stderr)
            print(f"🔍 DEBUG: Code to execute:\n{self.code}", file=sys.stderr)
            
            command = [sys.executable, "-c", self.code]
            self.process = subprocess.Popen(
                command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            
            print(f"🔍 DEBUG: Process started with PID {self.process.pid}", file=sys.stderr)
            
            # Give it a moment to start
            time.sleep(0.5)
            
            # Check if process died immediately
            retcode = self.process.poll()
            if retcode is not None:
                stdout, stderr = self.process.communicate()
                print(f"❌ DEBUG: Process died immediately with code {retcode}", file=sys.stderr)
                print(f"📤 STDOUT:\n{stdout}", file=sys.stderr)
                print(f"📤 STDERR:\n{stderr}", file=sys.stderr)
                return False
            
            print(f"✅ DEBUG: Process still alive, waiting for ready...", file=sys.stderr)
            return self.wait_for_ready()
        
        def shutdown(self):
            if self.process:
                print(f"🔍 DEBUG: Shutting down process {self.process.pid}", file=sys.stderr)
                
                # Try to get output before killing
                try:
                    stdout, stderr = self.process.communicate(timeout=0.1)
                    print(f"📤 Final STDOUT:\n{stdout}", file=sys.stderr)
                    print(f"📤 Final STDERR:\n{stderr}", file=sys.stderr)
                except subprocess.TimeoutExpired:
                    pass
                
                self.process.terminate()
                try: 
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired: 
                    self.process.kill()
            release_port(self.port)
            safe_print(f"  ✅ Port {self.port} released and manager shut down.")
        
        def wait_for_ready(self, timeout=10.0):
            start_time = time.time()
            attempts = 0
            
            print(f"🔍 DEBUG: Attempting to connect to 127.0.0.1:{self.port}", file=sys.stderr)
            
            while time.time() - start_time < timeout:
                attempts += 1
                elapsed = time.time() - start_time
                
                # Check if process is still alive
                if self.process and self.process.poll() is not None:
                    print(f"❌ DEBUG: Process died during wait! (attempt {attempts}, elapsed {elapsed:.2f}s)", file=sys.stderr)
                    stdout, stderr = self.process.communicate()
                    print(f"📤 STDOUT:\n{stdout}", file=sys.stderr)
                    print(f"📤 STDERR:\n{stderr}", file=sys.stderr)
                    return False
                
                try:
                    # Try multiple connection methods
                    print(f"🔌 DEBUG: Connection attempt {attempts} (elapsed {elapsed:.2f}s)", file=sys.stderr)
                    
                    # Method 1: Socket connection
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                        print(f"✅ DEBUG: Socket connection successful!", file=sys.stderr)
                        return True
                        
                except socket.timeout:
                    print(f"⏱️  DEBUG: Socket timeout (attempt {attempts})", file=sys.stderr)
                except ConnectionRefusedError:
                    print(f"🚫 DEBUG: Connection refused (attempt {attempts})", file=sys.stderr)
                except Exception as e:
                    print(f"⚠️  DEBUG: Unexpected error on attempt {attempts}: {type(e).__name__}: {e}", file=sys.stderr)
                
                time.sleep(0.2)
            
            print(f"❌ DEBUG: Timeout after {attempts} attempts over {timeout}s", file=sys.stderr)
            
            # Final check - is the process still alive?
            if self.process and self.process.poll() is None:
                print(f"🤔 DEBUG: Process is STILL ALIVE but not responding!", file=sys.stderr)
                
                # Try to see what ports are actually in use
                try:
                    import psutil
                    proc = psutil.Process(self.process.pid)
                    connections = proc.connections()
                    print(f"🔍 DEBUG: Process connections: {connections}", file=sys.stderr)
                except:
                    print(f"⚠️  DEBUG: Could not get process connections (psutil not available)", file=sys.stderr)
            
            return False
    
    def patch_flask_code(code, interactive=False, validate_only=False):
        port = find_free_port(reserve=True)
        
        print(f"\n🔍 DEBUG: Patching code for port {port}", file=sys.stderr)
        print(f"🔍 DEBUG: Original code:\n{code}", file=sys.stderr)
        
        # Try different patterns
        patterns = [
            ("app.run()", f"app.run(host='0.0.0.0', port={port}, use_reloader=False)"),
            ("app.run(debug=True)", f"app.run(host='0.0.0.0', port={port}, use_reloader=False)"),
            ("app.run(use_reloader=False)", f"app.run(host='0.0.0.0', port={port}, use_reloader=False)"),
        ]
        
        patched_code = code
        matched = False
        for old, new in patterns:
            if old in code:
                patched_code = code.replace(old, new)
                matched = True
                print(f"✅ DEBUG: Matched pattern '{old}'", file=sys.stderr)
                break
        
        if not matched:
            print(f"⚠️  DEBUG: No pattern matched! Code unchanged.", file=sys.stderr)
        
        print(f"🔍 DEBUG: Patched code:\n{patched_code}", file=sys.stderr)
        
        manager = FlaskAppManager(patched_code, port, interactive, validate_only) if interactive else None
        return patched_code, port, manager


class TestEnhancedFlaskPortFinder(unittest.TestCase):
    reserved_ports = []
    
    def tearDown(self):
        for port in list(self.reserved_ports):
            release_port(port)
        self.reserved_ports.clear()

    def test_6_flask_app_manager_full_lifecycle(self):
        """ULTRA DEBUG VERSION - Let's see everything!"""
        safe_print("\n" + "="*70 + "\n🧪 TEST 6: Flask App Manager Full Lifecycle (ULTRA DEBUG)\n" + "="*70)
        
        app_code = """
from flask import Flask
import sys

print('🔍 FLASK: Script starting...', file=sys.stderr, flush=True)

app = Flask(__name__)

print('🔍 FLASK: Flask app created', file=sys.stderr, flush=True)

@app.route('/')
def hello():
    print('🔍 FLASK: Route handler called!', file=sys.stderr, flush=True)
    return 'Success!'

print('🔍 FLASK: Route registered', file=sys.stderr, flush=True)

if __name__ == '__main__':
    print('🔍 FLASK: About to call app.run()...', file=sys.stderr, flush=True)
    app.run(use_reloader=False)
    print('🔍 FLASK: app.run() returned (should never see this)', file=sys.stderr, flush=True)
"""
        
        manager = None
        port = None
        try:
            _, port, manager = patch_flask_code(app_code, interactive=True)
            self.reserved_ports.append(port)
            self.assertIsNotNone(manager, "Manager should be created.")
            safe_print(f"  ✅ Manager created for port {port}.")
            
            success = manager.start()
            safe_print(f"  🔍 Manager.start() returned: {success}")
            
            self.assertTrue(success, "Flask app should start successfully.")
            safe_print(f"  ✅ Flask app process started on port {port}.")
            
            # Try to connect manually
            safe_print(f"  🔍 Attempting manual HTTP request...")
            try:
                response = requests.get(f"http://127.0.0.1:{port}", timeout=5)
                safe_print(f"  ✅ HTTP response: {response.status_code} - {response.text}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, 'Success!')
            except Exception as e:
                safe_print(f"  ❌ HTTP request failed: {type(e).__name__}: {e}")
                raise
                
        finally:
            if manager:
                manager.shutdown()
            elif port:
                release_port(port)
        
        safe_print("✅ TEST 6 PASSED")


if __name__ == '__main__':
    unittest.main()