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
import logging
import sys

# Configure logging to both file and stdout
LOG_FILE = OMNIPKG_DIR / "web_bridge.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Dependency Checks ---
try:
    import psutil
    HAS_SYS_DEPS = True
except ImportError:
    HAS_SYS_DEPS = False

try:
    from flask import Flask, request, jsonify, make_response
    from flask_cors import CORS
    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

# --- Configuration ---
PRIMARY_DASHBOARD = "https://1minds3t.echo-universe.ts.net/omnipkg"
ALLOWED_ORIGINS = {
    "https://1minds3t.echo-universe.ts.net",
    "https://omnipkg.1minds3t.workers.dev",
    "https://omnipkg.pages.dev",
    "http://localhost:8085",      # ✅ Your mkdocs
    "http://127.0.0.1:8085",      # ✅ Same but 127.0.0.1
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5000",      # ⭐ ADD THIS - bridge itself
    "http://127.0.0.1:5000",      # ⭐ ADD THIS
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

def find_free_port(start_port=5000, max_port=65535):
    """Finds an available port starting from start_port."""
    for port in range(start_port, min(max_port, start_port + 1000)):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError(f"No free ports found between {start_port} and {start_port + 1000}")

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
        full_command = [sys.executable, "-m", "omnipkg", *args]
        
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
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds"
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
    CORS(app, origins=list(ALLOWED_ORIGINS))
    
    # Silence standard Flask logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    @app.route('/health', methods=['GET', 'OPTIONS'])
    def health():
        origin = request.headers.get('Origin')
        logger.info(f"Health check from origin: {origin}")
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
        return corsify_response(jsonify({
            "status": "connected", 
            "port": port, 
            "version": "2.0.9"
        }), origin)

    @app.route('/run', methods=['POST', 'OPTIONS'])
    def run_command():
        origin = request.headers.get('Origin')
        logger.info(f"Run command from origin: {origin}")
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
        
        data = request.json
        cmd = data.get('command', '')
        logger.info(f"⚡ Executing: {cmd}")
        output = execute_omnipkg_command(cmd)
        logger.info(f"✅ Command completed")
        return corsify_response(jsonify({"output": output}), origin)
    
    return app

def run_bridge_server():
    """The entry point for the background process."""
    if not HAS_WEB_DEPS:
        print("❌ Flask missing. Cannot start server.")
        sys.exit(1)

    try:
        port = find_free_port(5000)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    
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
        cmd = [sys.executable, "-m", "omnipkg.apis.local_bridge"]
        
        # Cross-platform detachment logic
        kwargs = {}
        if os.name == 'nt':
            # Windows: Create new process group and detach
            # 0x00000008 is DETACHED_PROCESS, 0x00000200 is CREATE_NEW_PROCESS_GROUP
            kwargs['creationflags'] = 0x00000008 | 0x00000200
        else:
            # Unix/Mac: Start new session
            kwargs['start_new_session'] = True

        try:
            with open(self.log_file, 'w') as log:
                process = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=log,
                    cwd=Path.home(),
                    **kwargs
                )
            
            self.pid_file.write_text(str(process.pid))
            time.sleep(1.5)
            
            if self.is_running():
                port = self._get_port()
                url = f"{PRIMARY_DASHBOARD}#{port}"
                print("="*60)
                print("✅ Web bridge started successfully")
                print(f"🔗 Local Port: {port}")
                print(f"📊 PID: {process.pid}")
                print(f"🌍 Dashboard: {url}")
                print("="*60)
                webbrowser.open(url)
                return 0
            else:
                print("❌ Failed to start. Check logs.")
                return 1
        except Exception as e:
            print(f"❌ Launch error: {e}")
            return 1
    
    def stop(self):
        """Stop the web bridge safely across platforms."""
        if not self.is_running():
            print("⚠️  Web bridge is not running")
            return 0
        
        try:
            pid = int(self.pid_file.read_text())
            print(f"🛑 Stopping web bridge (PID: {pid})...")
            
            # --- START OF FIX ---
            if os.name == 'nt':
                # Windows: Force kill tree
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                # Linux/Mac: Standard kill
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                if self.is_running():
                    os.kill(pid, signal.SIGKILL)
            # --- END OF FIX ---

            if self.pid_file.exists(): self.pid_file.unlink()
            print("✅ Web bridge stopped")
            return 0
        except Exception as e:
            print(f"❌ Error stopping: {e}")
            if self.pid_file.exists(): self.pid_file.unlink()
            return 1
    
    def restart(self):
        """Restart the web bridge."""
        print("🔄 Restarting web bridge...")
        self.stop()
        time.sleep(1)
        return self.start()
    
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
                # Try using tail if available (Unix/Mac)
                subprocess.run(["tail", "-f", str(self.log_file)], check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                # Windows/No-tail fallback: Python polling
                try:
                    with open(self.log_file, "r") as f:
                        # Move to end of file
                        f.seek(0, 2)
                        while True:
                            line = f.readline()
                            if line:
                                print(line, end='')
                            else:
                                time.sleep(0.5)
                except KeyboardInterrupt:
                    pass
            except KeyboardInterrupt:
                pass
            print("\n✅ Stopped following logs")
            return 0
        else:
            try:
                # Try using tail if available (Unix)
                subprocess.run(["tail", "-n", str(lines), str(self.log_file)], check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
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
            os.kill(pid, 0)  # Signal 0 checks if process exists
            return True
        except (OSError, ValueError):
            return False
    
    def _get_port(self):
        """Retrieve port from log file."""
        if not self.log_file.exists(): 
            return 5000
        try:
            with open(self.log_file) as f:
                for line in f:
                    if "Local Port:" in line:
                        return int(line.split("Local Port:")[-1].strip())
        except Exception:
            pass
        return 5000
    
    def _format_uptime(self, seconds):
        """Format uptime in human-readable format."""
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{int(h)}h {int(m)}m {int(s)}s"

# ==========================================
# PART 3: Main Execution
# ==========================================

if __name__ == "__main__":
    # If this file is run directly (python -m omnipkg.apis.local_bridge), 
    # it implies we are the background process trying to start the server.
    run_bridge_server()