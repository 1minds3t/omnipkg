import sys
import time
import socket
import threading
import unittest
import requests
import random
from textwrap import dedent
from pathlib import Path
from omnipkg.i18n import _

# --- Import Logic ---
try:
    from omnipkg.utils.flask_port_finder import (
        find_free_port,
        release_port,
        patch_flask_code,
        FlaskAppManager,
        safe_print,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from omnipkg.utils.flask_port_finder import (
        find_free_port,
        release_port,
        patch_flask_code,
        FlaskAppManager,
        safe_print,
    )

def print_banner(title):
    safe_print(_('\n{}\n🎬 SCENARIO: {}\n{}').format('=' * 70, title, '=' * 70))

def print_proof(label, msg):
    safe_print(_('   🔎 PROOF [{}]: {}').format(label, msg))

class TestFlaskPortFinderUltimate(unittest.TestCase):
    
    def setUp(self):
        self.managers = []
        self.reserved_ports = []

    def tearDown(self):
        safe_print(_('\n   🧹 [Cleanup Phase]'))
        for manager in self.managers:
            # We check if the manager has a process and is running before killing
            if hasattr(manager, 'is_running') and manager.is_running:
                try:
                    manager.shutdown()
                except Exception as e:
                    safe_print(_('      ⚠️ Warning during shutdown: {}').format(e))
        
        # Release ports safely
        for port in self.reserved_ports:
            release_port(port)

    def test_1_concurrent_stress_test(self):
        """
        Visualizes 10 threads grabbing ports simultaneously.
        """
        print_banner("The 'Matrix' Concurrency Test")
        print(_('   Goal: Prove 10 threads cannot accidentally grab the same port.'))
        
        results = []
        lock = threading.Lock()
        
        # Start high to avoid 5000/8080 conflicts
        start_search = 8000

        def greedy_worker(thread_id):
            p = find_free_port(start_port=start_search, reserve=True)
            with lock:
                results.append((thread_id, p))
                self.reserved_ports.append(p)
            time.sleep(0.01) 

        safe_print(_('   🚀 Launching 10 threads (Starting search at {})...').format(start_search))
        threads = [threading.Thread(target=greedy_worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        results.sort(key=lambda x: x[1])

        safe_print(_('\n   📊 Allocation Visualization:'))
        unique_ports = set()
        for t_id, port in results:
            safe_print(_('      🧵 Thread {} ➔ Secured Port {}').format(t_id, port))
            unique_ports.add(port)
        
        print("")
        if len(unique_ports) == 10:
            print_proof("SUCCESS", _('10 Threads = {} Unique Ports.').format(len(unique_ports)))
        else:
            self.fail(_('❌ Collision Detected! Only {} unique ports.').format(len(unique_ports)))

    def test_2_smart_conflict_interception(self):
        """
        The 'Traffic Cop' Test.
        """
        print_banner("The 'Traffic Cop' Interception Test")

        # --- STEP 1: Find a random "Home" for App A ---
        random_start = random.randint(6000, 7000)
        port_a = find_free_port(start_port=random_start, reserve=True)
        self.reserved_ports.append(port_a)
        
        safe_print(_("1️⃣  Phase 1: Establish the 'Incumbent' (App A)"))
        print(_('    Selected Arbitrary Port: {}').format(port_a))
        
        code_a = dedent(f"""
            from flask import Flask
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
            app = Flask('app_a')
            @app.route('/')
            def idx(): return "I am App A (Incumbent)"
            if __name__ == '__main__':
                app.run(port={port_a})
        """)
        
        # FIX: Removed 'interactive=True'. The class only needs code and port.
        manager_a = FlaskAppManager(code_a, port_a)
        self.managers.append(manager_a)
        
        manager_a.start()
        manager_a.wait_for_ready()
        print_proof("STATUS", f"App A is RUNNING on Port {port_a}")

        # --- STEP 2: The Intruder (App B) ---
        safe_print(_("\n2️⃣  Phase 2: The 'Intruder' (App B)"))
        print(_('    User script explicitly requests: app.run(port={})').format(port_a))

        code_b = dedent(f"""
            from flask import Flask
            app = Flask('app_b')
            @app.route('/')
            def idx(): return "I am App B (The Challenger)"
            if __name__ == '__main__':
                # INTENTIONAL CONFLICT:
                app.run(port={port_a}) 
        """)

        # --- STEP 3: The Magic Patch ---
        safe_print(_('\n3️⃣  Phase 3: Omnipkg Intervention'))
        
        # Here we DO use interactive=True because this is the helper function, not the class
        patched_code_b, port_b, manager_b = patch_flask_code(code_b, interactive=True)
        self.managers.append(manager_b)
        self.reserved_ports.append(port_b)

        if port_b == port_a:
            self.fail("❌ Auto-patcher failed! It assigned the BUSY port.")
        
        print_proof("INTERCEPTION", _('Detected Port {} is BUSY.').format(port_a))
        print_proof("PATCHING", _('Rewrote App B to use Port {} instead.').format(port_b))

        # --- STEP 4: Double Validation ---
        safe_print(_('\n4️⃣  Phase 4: Co-Existence Verification'))
        manager_b.start()
        manager_b.wait_for_ready()

        # Check App A
        try:
            resp_a = requests.get(f"http://127.0.0.1:{port_a}", timeout=2).text
            safe_print(_('    ✅ App A (Port {}): {}').format(port_a, resp_a))
            self.assertIn("Incumbent", resp_a)
        except Exception as e:
            self.fail(_('❌ App A died! Error: {}').format(e))

        # Check App B
        try:
            resp_b = requests.get(f"http://127.0.0.1:{port_b}", timeout=2).text
            safe_print(_('    ✅ App B (Port {}): {}').format(port_b, resp_b))
            self.assertIn("Challenger", resp_b)
        except Exception as e:
            self.fail(_('❌ App B failed to start! Error: {}').format(e))

        print_proof("CONCLUSION", "Both apps are alive on different ports.")

if __name__ == '__main__':
    unittest.main(verbosity=2)