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
    # Fallback if your internal util isn't available in this context
    def find_free_port(start_port=5000, **kwargs): return start_port

ALLOWED_ORIGIN = "https://omnipkg.1minds3t.workers.dev"
PID_FILE = Path.home() / ".omnipkg" / "web_bridge.pid"

# --- SECURITY: COMMAND VALIDATION ---

# 1. ALLOWED: Management, Swapping, Viewing, Syncing
WEB_ALLOWED_COMMANDS = {
    # Info & Status
    'list', 'info', 'status', 'doctor', 'config', 'check',
    
    # Environment Management
    'swap', 'python', 'revert', 'rebuild-kb',
    
    # Sync & Repair (Safe metadata operations)
    'reset',         # Allowed: Rebuilds DB/Cache (Non-destructive to packages)
    'heal',          # Allowed: Fixes broken environments
    
    # Installation
    'install', 'install-with-deps',
    
    # Demos
    'demo', 'stress-test'
}

# 2. BLOCKED: Destructive / Arbitrary Execution / Resource Heavy
WEB_BLOCKED_COMMANDS = {
    'run',           # CRITICAL: Arbitrary script execution (RCE)
    'shell',         # CRITICAL: Shell access
    'exec',          # CRITICAL: Command execution
    'uninstall',     # Safety: Prevent accidental deletion of packages
    'prune',         # Safety: Bulk deletion of environments
    'upgrade',       # Safety: Can break Omnipkg itself or the environment
    'reset-config'   # Safety: Wipes user preferences/config file
}

def validate_command(cmd_str):
    """
    Validates command against the security whitelist.
    Returns: (is_valid, error_message_or_cleaned_cmd)
    """
    if not cmd_str or not cmd_str.strip():
        return False, "Empty command."
    
    # Split safely
    parts = shlex.split(cmd_str)
    
    # Filter out flags (starting with -) to find the real command keyword
    clean_parts = [p for p in parts if not p.startswith('-')]
    
    if not clean_parts:
        return False, "No command found."

    primary_command = clean_parts[0].lower()

    # --- SPECIAL HANDLING: DAEMON ---
    # Allow monitoring, block control (start/stop)
    if primary_command == 'daemon':
        if len(clean_parts) < 2:
            return False, "⚠️ Please specify a daemon command (status, monitor, logs)."
        
        sub_command = clean_parts[1].lower()
        
        # Safe read-only daemon commands
        if sub_command in ['status', 'monitor', 'logs']:
            return True, ""
            
        # Dangerous control commands
        if sub_command in ['start', 'stop', 'restart']:
            return False, f"⛔ Security: Daemon lifecycle '{sub_command}' is blocked via Web Bridge.\nPlease manage the daemon process directly from your terminal."
        
        return False, f"⚠️ Unknown daemon subcommand '{sub_command}'."

    # --- STANDARD HANDLING ---
    if primary_command in WEB_BLOCKED_COMMANDS:
        return False, f"⛔ Security: The command '{primary_command}' is disabled in the Web Interface.\nPlease run this directly in your terminal."
        
    if primary_command in WEB_ALLOWED_COMMANDS:
        return True, ""

    return False, f"⚠️ Unknown command '{primary_command}'."

# --- FLASK APP LOGIC ---

def build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, Private-Network-Access-Request")
    response.headers.add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    # CRITICAL for Chrome 130+ Localhost access
    response.headers.add("Access-Control-Allow-Private-Network", "true")
    return response

def corsify_actual_response(response):
    response.headers.add("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    response.headers.add("Access-Control-Allow-Private-Network", "true")
    return response

def execute_omnipkg_command(cmd_str):
    # 1. Validate Security First
    is_valid, msg = validate_command(cmd_str)
    if not is_valid:
        return msg

    # 2. Execute if valid
    try:
        args = shlex.split(cmd_str)
        # Use sys.executable to ensure we use the same python env
        full_command = [sys.executable, "-m", "omnipkg"] + args
        
        # Windows: prevent popping up new CMD windows
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=120, # Increased timeout for heavy ops like 'reset'
            env=os.environ.copy(),
            startupinfo=startupinfo
        )
        if result.returncode == 0:
            return result.stdout or "(Command completed successfully)"
            
        return f"Error ({result.returncode}):\n{result.stderr}\n{result.stdout}"
    except subprocess.TimeoutExpired:
        return "⏱️ Command timed out (120s limit for web requests)."
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
        return 

    try:
        # Try to find a port, fallback to 5000 if utility fails
        try:
            port = find_free_port(start_port=5000, max_attempts=50, reserve=True)
        except:
            port = 5000
            
        app = create_app(port)
        app.run(port=port, threaded=True)
    except Exception as e:
        pass 

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