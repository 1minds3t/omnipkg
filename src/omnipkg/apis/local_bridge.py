import sys
import os
import signal
import logging
import subprocess
import shlex
import time
import socket
import webbrowser
from pathlib import Path
from contextlib import closing

# --- Dependency Checks ---
try:
    import psutil
    HAS_SYS_DEPS = True
except ImportError:
    HAS_SYS_DEPS = False

try:
    from flask import Flask, request, jsonify, make_response
    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

# --- Configuration ---
PRIMARY_DASHBOARD = "https://1minds3t.echo-universe.ts.net/omnipkg"
ALLOWED_ORIGINS = {
    "https://1minds3t.echo-universe.ts.net",
    "https://omnipkg.1minds3t.workers.dev",
    "https://omnipkg.echo-universe.ts.net",
    "http://localhost:8085",
    "http://127.0.0.1:8085"
}

# Standardized paths
OMNIPKG_DIR = Path.home() / ".omnipkg"
PID_FILE = OMNIPKG_DIR / "web_bridge.pid"
LOG_FILE = OMNIPKG_DIR / "web_bridge.log"

# --- Security: Command Allowlist ---
WEB_ALLOWED_COMMANDS = {
    'list', 'info', 'status', 'doctor', 'config', 'check',
    'swap', 'python', 'revert', 'rebuild-kb',
    'reset', 'heal', 'install', 'install-with-deps',
    'demo', 'stress-test'
}

WEB_BLOCKED_COMMANDS = {
    'run', 'shell', 'exec', 'uninstall', 'prune', 'upgrade', 'reset-config'
}

# ==========================================
# PART 1: Server Logic (Flask & Execution)
# ==========================================

def find_free_port(start_port=5000):
    """Finds an available port starting from start_port."""
    port = start_port
    while port < 65535:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return port
        port += 1
    return 5000

def clean_and_validate(cmd_str):
    """Sanitizes and validates incoming web commands."""
    if not cmd_str or not cmd_str.strip():
        return False, "Empty command.", None
    
    clean_str = cmd_str.strip()
    # Strip common prefixes
    if clean_str.lower().startswith("8pkg "):
        clean_str = clean_str[5:].strip()
    elif clean_str.lower().startswith("omnipkg "):
        clean_str = clean_str[8:].strip()
    
    parts = shlex.split(clean_str)
    clean_parts = [p for p in parts if not p.startswith('-')]
    
    if not clean_parts:
        return False, "No command found.", None

    primary_command = clean_parts[0].lower()

    if primary_command == 'daemon':
        return False, "⛔ Daemon control via web is restricted.", None

    if primary_command in WEB_BLOCKED_COMMANDS:
        return False, f"⛔ Security: '{primary_command}' is disabled via Web.", None
        
    if primary_command in WEB_ALLOWED_COMMANDS:
        return True, "", clean_str

    return False, f"⚠️ Unknown command '{primary_command}'.", None

def execute_omnipkg_command(cmd_str):
    """Executes the validated command via subprocess."""
    is_valid, msg, cleaned_cmd = clean_and_validate(cmd_str)
    if not is_valid:
        return msg

    try:
        args = shlex.split(cleaned_cmd)
        # Always run using the current sys.executable to ensure same venv/environment
        full_command = [sys.executable, "-m", "omnipkg"] + args
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            startupinfo=startupinfo,
            cwd=Path.home() 
        )
        
        if result.returncode == 0:
            return result.stdout or "(Command completed successfully)"
        return f"Error ({result.returncode}):\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        return f"System Error: {str(e)}"

def corsify_response(response, origin):
    """Adds CORS headers if the origin is allowed."""
    clean_origin = origin.rstrip("/") if origin else ""
    if clean_origin in ALLOWED_ORIGINS:
        response.headers.add("Access-Control-Allow-Origin", clean_origin)
        response.headers.add("Access-Control-Allow-Headers", "Content-Type, Private-Network-Access-Request")
        response.headers.add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        response.headers.add("Access-Control-Allow-Private-Network", "true")
    return response

def create_app(port):
    """Creates the Flask application."""
    app = Flask(__name__)
    # Silence standard Flask logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    @app.route('/health', methods=['GET', 'OPTIONS'])
    def health():
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": return corsify_response(make_response(), origin)
        return corsify_response(jsonify({"status": "connected", "port": port, "version": "2.0.9"}), origin)

    @app.route('/run', methods=['POST', 'OPTIONS'])
    def run_command():
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": return corsify_response(make_response(), origin)
        
        data = request.json
        cmd = data.get('command', '')
        print(f"⚡ Web Request: {cmd}", flush=True)
        output = execute_omnipkg_command(cmd)
        return corsify_response(jsonify({"output": output}), origin)
    
    return app

def run_bridge_server():
    """The entry point for the background process."""
    if not HAS_WEB_DEPS:
        print("❌ Flask missing. Cannot start server.")
        sys.exit(1)

    port = find_free_port(5000)
    
    # Print the port to stdout so the manager can read it (if it wants to)
    # But primarily we log it to file via stdout redirection in the Manager
    print(f"Local Port: {port}", flush=True)
    
    app = create_app(port)
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

# ==========================================
# PART 2: Manager Logic (CLI Control)
# ==========================================

class WebBridgeManager:
    """Manages the web bridge as a background service."""
    
    def __init__(self):
        self.pid_file = PID_FILE
        self.log_file = LOG_FILE
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
    
    def start(self):
        """Start the web bridge in background."""
        if not HAS_WEB_DEPS:
            print("❌ Dependencies missing. Please run: pip install flask flask-cors")
            return 1

        if self.is_running():
            port = self._get_port()
            print(f"✅ Web bridge already running on port {port}")
            print(f"🌍 Dashboard: {PRIMARY_DASHBOARD}#{port}")
            return 0
        
        print("🚀 Starting web bridge...")
        
        # Start this very file as a module in a new process
        cmd = [sys.executable, "-m", "omnipkg.apis.local_bridge"]
        
        with open(self.log_file, 'w') as log:
            process = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=log,
                start_new_session=True,  # Detach from parent
                cwd=Path.home()
            )
        
        self.pid_file.write_text(str(process.pid))
        
        # Wait a moment and verify
        time.sleep(1.5)
        
        if self.is_running():
            port = self._get_port()
            dashboard_url = f"{PRIMARY_DASHBOARD}#{port}"
            
            print("="*60)
            print("✅ Web bridge started successfully")
            print(f"🔗 Local Port: {port}")
            print(f"📊 PID: {process.pid}")
            print(f"🌍 Dashboard: {dashboard_url}")
            print("="*60)
            print("\n💡 Commands:")
            print("  8pkg web status  - Check status")
            print("  8pkg web logs    - View logs")
            print("  8pkg web stop    - Stop bridge")
            
            webbrowser.open(dashboard_url)
            return 0
        else:
            print("❌ Failed to start web bridge. Check logs:")
            print(f"   cat {self.log_file}")
            return 1
    
    def stop(self):
        """Stop the web bridge."""
        if not self.is_running():
            print("⚠️  Web bridge is not running")
            return 0
        
        pid = int(self.pid_file.read_text())
        
        try:
            print(f"🛑 Stopping web bridge (PID: {pid})...")
            os.kill(pid, signal.SIGTERM)
            
            # Wait for graceful shutdown
            for _ in range(10):
                if not self.is_running():
                    break
                time.sleep(0.5)
            
            # Force kill if needed
            if self.is_running():
                os.kill(pid, signal.SIGKILL)
            
            if self.pid_file.exists():
                self.pid_file.unlink()
            
            print("✅ Web bridge stopped")
            return 0
        except ProcessLookupError:
            if self.pid_file.exists(): self.pid_file.unlink()
            print("✅ Web bridge stopped (process was already dead)")
            return 0
        except Exception as e:
            print(f"❌ Error stopping web bridge: {e}")
            return 1
    
    def status(self):
        """Check web bridge status."""
        if not self.is_running():
            print("❌ Web bridge is not running")
            print(f"\n💡 Start with: 8pkg web start")
            return 1
        
        if not HAS_SYS_DEPS:
            print("⚠️  'psutil' not installed. Limited status info available.")
            pid = int(self.pid_file.read_text())
            port = self._get_port()
            print(f"✅ Running (PID: {pid}, Port: {port})")
            return 0

        pid = int(self.pid_file.read_text())
        port = self._get_port()
        
        try:
            process = psutil.Process(pid)
            mem_info = process.memory_info()
            uptime = time.time() - process.create_time()
            
            print("="*60)
            print("✅ Web Bridge Status: RUNNING")
            print("="*60)
            print(f"🔗 Port:        {port}")
            print(f"📊 PID:         {pid}")
            print(f"💾 Memory:      {mem_info.rss / 1024 / 1024:.1f} MB")
            print(f"⏱️  Uptime:      {self._format_uptime(uptime)}")
            print(f"🌍 Dashboard:   {PRIMARY_DASHBOARD}#{port}")
            print("="*60)
            return 0
        except psutil.NoSuchProcess:
            print("⚠️  PID file exists but process is dead. Cleaning up...")
            self.pid_file.unlink()
            return 1
    
    def show_logs(self, follow=False, lines=50):
        """Display web bridge logs."""
        if not self.log_file.exists():
            print(f"❌ Log file not found: {self.log_file}")
            return 1
        
        if follow:
            print(f"📝 Following logs (Ctrl+C to stop)...\n")
            try:
                subprocess.run(["tail", "-f", str(self.log_file)])
            except KeyboardInterrupt:
                print("\n✅ Stopped following logs")
            return 0
        else:
            try:
                # Try using tail if available (Unix)
                subprocess.run(["tail", "-n", str(lines), str(self.log_file)])
            except FileNotFoundError:
                # Fallback for Windows or missing tail
                with open(self.log_file) as f:
                    all_lines = f.readlines()
                    print("".join(all_lines[-lines:]))
            return 0

    def is_running(self):
        """Check if web bridge is running."""
        if not self.pid_file.exists():
            return False
        try:
            pid = int(self.pid_file.read_text())
            # Basic check: send signal 0 to check if process exists
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False
    
    def _get_port(self):
        """Retrieve port from log file."""
        if not self.log_file.exists(): return 5000
        try:
            with open(self.log_file) as f:
                for line in f:
                    if "Local Port:" in line:
                        return int(line.split("Local Port:")[-1].strip())
        except:
            pass
        return 5000
    
    def _format_uptime(self, seconds):
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{int(h)}h {int(m)}m {int(s)}s"

import sys
import os
import signal
import subprocess
import shlex
import time
import socket
import json
import webbrowser
import logging
from pathlib import Path
from contextlib import closing
from datetime import datetime

# --- Dependency Checks ---
try:
    import psutil
    HAS_SYS_DEPS = True
except ImportError:
    HAS_SYS_DEPS = False

try:
    from flask import Flask, request, jsonify, send_from_directory, make_response
    from flask_cors import CORS
    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

# --- Configuration ---
# We serve the compiled MkDocs site. 
# You should run `mkdocs build -d ~/.omnipkg/site` to put the HTML there.
DOCS_DIR = Path.home() / ".omnipkg" / "site"
STATS_FILE = Path.home() / ".omnipkg" / "usage_stats.json"
PID_FILE = Path.home() / ".omnipkg" / "web_bridge.pid"
LOG_FILE = Path.home() / ".omnipkg" / "web_bridge.log"

ALLOWED_ORIGINS = {
    "http://localhost:8085",
    "http://127.0.0.1:8085"
}

WEB_ALLOWED_COMMANDS = {
    'list', 'info', 'status', 'doctor', 'config', 'check',
    'swap', 'python', 'revert', 'rebuild-kb', 'reset', 'heal', 
    'install', 'demo', 'stress-test'
}

# ==========================================
# Telemetry Logic (Privacy Safe)
# ==========================================
def record_telemetry(data):
    """
    Appends anonymous usage data to a local JSON file.
    It does NOT collect IP addresses or environment variables.
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event": data.get("event", "unknown"),
        "details": data.get("details", {})
    }
    
    try:
        # Simple Append-only log
        with open(STATS_FILE, 'a') as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"Stats Error: {e}")

# ==========================================
# Server Logic
# ==========================================

def execute_omnipkg_command(cmd_str):
    # (Same validation logic as before)
    clean_str = cmd_str.strip()
    if clean_str.lower().startswith("8pkg "): clean_str = clean_str[5:].strip()
    elif clean_str.lower().startswith("omnipkg "): clean_str = clean_str[8:].strip()
    
    parts = shlex.split(clean_str)
    if not parts or parts[0] not in WEB_ALLOWED_COMMANDS:
        return "⛔ Command not allowed via web."

    try:
        args = shlex.split(clean_str)
        full_command = [sys.executable, "-m", "omnipkg"] + args
        
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        result = subprocess.run(
            full_command, capture_output=True, text=True, timeout=60, env=env, cwd=Path.home()
        )
        return result.stdout or result.stderr
    except Exception as e:
        return f"System Error: {str(e)}"

def create_app(port):
    # Set static_folder to the MkDocs output directory
    app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="/")
    CORS(app) # Enable CORS for safety
    
    # Disable loud logs
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    # 1. Serve the Documentation (MkDocs)
    @app.route('/')
    def index():
        if not DOCS_DIR.exists():
            return "<h1>Docs not found</h1><p>Please run: <code>mkdocs build -d ~/.omnipkg/site</code></p>"
        return send_from_directory(DOCS_DIR, 'index.html')

    @app.route('/<path:path>')
    def serve_static(path):
        return send_from_directory(DOCS_DIR, path)

    # 2. Command Endpoint
    @app.route('/run', methods=['POST'])
    def run_command():
        data = request.json
        cmd = data.get('command', '')
        output = execute_omnipkg_command(cmd)
        return jsonify({"output": output})

    # 3. Telemetry Endpoint
    @app.route('/telemetry', methods=['POST'])
    def telemetry():
        data = request.json
        record_telemetry(data)
        return jsonify({"status": "recorded"})
    
    return app

def find_free_port(start=8085):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', start))
    sock.close()
    return start if result != 0 else find_free_port(start + 1)

def run_bridge_server():
    if not HAS_WEB_DEPS: return
    port = find_free_port()
    print(f"Local Port: {port}", flush=True)
    app = create_app(port)
    app.run(host="127.0.0.1", port=port, threaded=True)

# ==========================================
# Manager Logic
# ==========================================

class WebBridgeManager:
    def __init__(self):
        self.pid_file = PID_FILE
        self.log_file = LOG_FILE
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        # Ensure site dir exists to prevent crash
        DOCS_DIR.mkdir(parents=True, exist_ok=True)

    def start(self):
        # 1. Auto-build docs if missing (Optional convenience)
        if not (DOCS_DIR / "index.html").exists():
            print("⚙️  Building documentation for the first time...")
            try:
                subprocess.run(["mkdocs", "build", "-d", str(DOCS_DIR)], check=True)
            except Exception:
                print("⚠️  Could not build docs. Ensure 'mkdocs' is installed.")

        # 2. Start Server
        if self.is_running():
            print("✅ Already running.")
            return

        print("🚀 Starting Local Docs Bridge...")
        cmd = [sys.executable, "-m", "omnipkg.apis.local_bridge"]
        
        with open(self.log_file, 'w') as log:
            process = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
        
        self.pid_file.write_text(str(process.pid))
        time.sleep(1)
        
        port = self._get_port()
        url = f"http://localhost:{port}"
        print(f"✅ Docs available at: {url}")
        webbrowser.open(url)

    def is_running(self):
        if not self.pid_file.exists(): return False
        try:
            os.kill(int(self.pid_file.read_text()), 0)
            return True
        except: return False

    def _get_port(self):
        try:
            with open(self.log_file) as f:
                for line in f:
                    if "Local Port:" in line: return int(line.split(":")[-1].strip())
        except: pass
        return 8085

# ==========================================
# PART 3: Main Execution
# ==========================================

if __name__ == "__main__":
    # If this file is run directly (python -m omnipkg.apis.local_bridge), 
    # it implies we are the background process trying to start the server.
    run_bridge_server()