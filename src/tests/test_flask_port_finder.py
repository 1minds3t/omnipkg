#!/usr/bin/env python3
import sys
import os
import unittest
import requests
from pathlib import Path

# Add project root to path so we can import omnipkg
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omnipkg.utils.flask_port_finder import (
    find_free_port,
    release_port,
    patch_flask_code,
    safe_print
)

class TestEnhancedFlaskPortFinder(unittest.TestCase):
    reserved_ports = []
    
    def tearDown(self):
        for port in list(self.reserved_ports):
            release_port(port)
        self.reserved_ports.clear()

    def test_basic_allocation(self):
        safe_print("\n🧪 TEST: Basic Allocation")
        port = find_free_port(reserve=True)
        self.reserved_ports.append(port)
        self.assertIsNotNone(port)
        safe_print(f"  ✅ Found port: {port}")

    def test_real_server(self):
        safe_print("\n🧪 TEST: Real Flask Server")
        app_code = """
from flask import Flask
app = Flask(__name__)
@app.route('/')
def hello(): return 'Working!'
if __name__ == '__main__':
    app.run()
"""
        _, port, manager = patch_flask_code(app_code, interactive=True)
        self.reserved_ports.append(port)
        
        safe_print(f"  🚀 Starting on {port}...")
        if not manager.start():
            self.fail("Failed to start process")

        try:
            if not manager.wait_for_ready(timeout=10.0):
                self.fail("Server timed out")
            
            resp = requests.get(f"http://127.0.0.1:{port}", timeout=2)
            self.assertEqual(resp.text, 'Working!')
            safe_print("  ✅ HTTP 200 OK")
        finally:
            manager.shutdown()

if __name__ == '__main__':
    unittest.main()