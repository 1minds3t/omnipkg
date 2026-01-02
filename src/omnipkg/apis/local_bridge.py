import sys
import os
import signal
import logging
import threading
import webbrowser
import subprocess
import shlex
import time
from pathlib import Path
from flask import Flask, request, jsonify, make_response

# --- Imports & Configuration ---
try:
    from flask_cors import CORS
    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

try:
    from omnipkg.utils.flask_port_finder import find_free_port
except ImportError:
    def find_free_port(start_port=5000, **kwargs): return start_port

ALLOWED_ORIGIN = "https://omnipkg.1minds3t.workers.dev"
PID_FILE = Path.home() / ".omnipkg" / "web_bridge.pid"

# --- FLASK APP LOGIC (Same as before) ---

def build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, Private-Network-Access-Request")
    response.headers.add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.headers.add("Access-Control-Allow-Private-Network", "true")
    return response

def corsify_actual_response(response):
    response.headers.add("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    response.headers.add("Access-Control-Allow-Private-Network", "true")
    return response

def execute_omnipkg_command(cmd_str):
    try:
        args = shlex.split(cmd_str)
        # Security: Allow only specific commands if needed, or leave open for dev
        full_command = [sys.executable, "-m", "omnipkg"] + args
        
        # Windows: specific flags to prevent popping up new CMD windows
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=60,
            env=os.environ.copy(),
            startupinfo=startupinfo
        )
        if result.returncode == 0:
            return result.stdout
        return f"Error ({result.returncode}):\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        return f"System Error: {str(e)}"

def create_app(port):
    app = Flask(__name__)
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    @app.route('/health', methods=['GET', 'OPTIONS'])
    def health():
        if request.method == "OPTIONS": return build_cors_preflight_response()
        return corsify_actual_response(jsonify({"status": "connected", "port": port, "version": "2.0.8"}))

    @app.route('/run', methods=['POST', 'OPTIONS'])
    def run_command():
        if request.method == "OPTIONS": return build_cors_preflight_response()
        data = request.json
        cmd = data.get('command', '')
        print(f"⚡ Web Request: omnipkg {cmd}")
        output = execute_omnipkg_command(cmd)
        return corsify_actual_response(jsonify({"output": output}))
    return app

# --- DAEMON MANAGEMENT LOGIC ---

def save_pid(pid):
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))

def get_pid():
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except:
            return None
    return None

def start_daemon():
    """Starts this script as a detached background process."""
    if get_pid():
        print("⚠️  Web Bridge is already running.")
        return

    # Path to this script
    script_path = os.path.abspath(__file__)
    
    # Platform specific flags for detaching
    if os.name == 'nt':
        # Windows detached process
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            [sys.executable, script_path, "--server-mode"],
            creationflags=creationflags,
            close_fds=True
        )
    else:
        # Linux/Mac detached process (nohup style)
        process = subprocess.Popen(
            [sys.executable, script_path, "--server-mode"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )

    save_pid(process.pid)
    print(f"🚀 OmniPkg Web Bridge started in background (PID: {process.pid})")
    
    # We can't know the port immediately in daemon mode easily without a complex handshake, 
    # so we assume it finds one shortly.
    print(f"🌍 Dashboard: {ALLOWED_ORIGIN}")
    webbrowser.open(ALLOWED_ORIGIN)

def stop_daemon():
    pid = get_pid()
    if not pid:
        print("❌ No active Web Bridge found.")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"✅ Stopped Web Bridge (PID: {pid})")
    except ProcessLookupError:
        print("⚠️  Process not found (already stopped?)")
    except Exception as e:
        print(f"❌ Error stopping process: {e}")
    
    if PID_FILE.exists():
        PID_FILE.unlink()

def run_server_blocking():
    """The actual server logic (what runs inside the daemon)."""
    if not HAS_WEB_DEPS:
        return # Can't log, we are detached

    try:
        port = find_free_port(start_port=5000, max_attempts=50, reserve=True)
        app = create_app(port)
        
        # Optional: Write port to file if you want the CLI to read it later
        
        app.run(port=port, threaded=True)
    except Exception as e:
        pass # Logging to file recommended here for debugging daemons

if __name__ == "__main__":
    if "--server-mode" in sys.argv:
        # This is the background process
        run_server_blocking()
    elif "--stop" in sys.argv:
        stop_daemon()
    elif "--daemon" in sys.argv:
        start_daemon()
    else:
        # Foreground mode (for debugging)
        print("Running in foreground...")
        run_server_blocking()