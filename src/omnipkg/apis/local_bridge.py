import sys
import os
import signal
import logging
import subprocess
import shlex
import time
import socket
import webbrowser
import re
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
    from flask_cors import CORS
    HAS_WEB_DEPS = True
except ImportError:
    HAS_WEB_DEPS = False

# --- Configuration ---
DEV_MODE = os.environ.get("OMNIPKG_DEV_MODE", "0") == "1"

PRIMARY_DASHBOARD = "https://1minds3t.echo-universe.ts.net/omnipkg/"
# 🔒 SECURITY: Only allow requests from the actual Frontend UI
ALLOWED_ORIGINS = {
    # 1. Your Public Tailscale Funnel (The main UI)
    "https://1minds3t.echo-universe.ts.net",
    "http://127.0.0.1:8085/",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:8085",
    # 2. Cloudflare Pages (Static docs fallback)
    "https://omnipkg.pages.dev",
    "https://omnipkg.workers.dev",
}
if DEV_MODE:
    ALLOWED_ORIGINS.update({
        "http://localhost:8085",
        "http://127.0.0.1:8085",
    })

OMNIPKG_DIR = Path.home() / ".omnipkg"
PID_FILE = OMNIPKG_DIR / "web_bridge.pid"
LOG_FILE = OMNIPKG_DIR / "web_bridge.log"

# --- Security: Command Allowlist with Argument Rules ---
COMMAND_RULES = {
    # Read-only commands (always safe)
    'list': {
        'allowed_flags': ['--verbose', '-v', '--json'],
        'allow_args': False
    },
    'info': {
        'allowed_flags': ['--verbose', '-v', '--json'],
        'allow_args': True,  # Package names
        'arg_pattern': r'^[a-zA-Z0-9_-]+$'
    },
    'status': {
        'allowed_flags': ['--verbose', '-v', '--json'],
        'allow_args': False
    },
    'doctor': {
        'allowed_flags': ['--verbose', '-v', '--json'],
        'allow_args': False
    },
    'config': {
        'allowed_flags': ['--list', '--get', '--set'],
        'allow_args': True,
        'arg_pattern': r'^[a-zA-Z0-9_.-]+$'
    },
    'check': {
        'allowed_flags': ['--verbose', '-v'],
        'allow_args': False
    },
    
    # Modification commands (need non-interactive flags)
    'demo': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': True,
        'arg_pattern': r'^\d{1,2}$',  # Demo numbers 1-99
        'auto_flags': ['--non-interactive']  # Auto-inject this flag
    },
    'stress-test': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': False,
        'auto_flags': ['--yes']
    },
    'swap': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': True,
        'arg_pattern': r'^(python|node|rust|go)$'
    },
    'python': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v', '--list'],
        'allow_args': True,
        'arg_pattern': r'^\d+\.\d+(\.\d+)?$'  # Version like 3.11 or 3.11.5
    },
    'revert': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': False,
        'auto_flags': ['--yes']
    },
    'rebuild-kb': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': False
    },
    'reset': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': False,
        'auto_flags': ['--yes']
    },
    'heal': {
        'allowed_flags': ['--non-interactive', '-y', '--yes', '--verbose', '-v'],
        'allow_args': False
    },
    'install': {
        'allowed_flags': ['--upgrade', '--force', '--force-reinstall', '--no-deps', 
                         '--verbose', '-v', '--non-interactive', '-y', '--yes'],
        'allow_args': True,
        'arg_pattern': r'^[a-zA-Z0-9_-]+$',  # Only package names, no URLs
        'blocked_patterns': [r'://', r'git\+', r'\.\.', r'^/']
    },
    'install-with-deps': {
        'allowed_flags': ['--upgrade', '--force', '--force-reinstall', 
                         '--verbose', '-v', '--non-interactive', '-y', '--yes'],
        'allow_args': True,
        'arg_pattern': r'^[a-zA-Z0-9_-]+$',
        'blocked_patterns': [r'://', r'git\+', r'\.\.', r'^/']
    },
    'prune': {
        'allowed_flags': ['--keep-latest', '--force', '--non-interactive', 
                         '-y', '--yes', '--verbose', '-v'],
        'allow_args': True,
        'arg_pattern': r'^\d+$',  # Number of versions to keep
        'auto_flags': ['--yes']
    }
}

# Commands that are completely blocked from web access
BLOCKED_COMMANDS = {
    'run', 'shell', 'exec', 'uninstall', 'upgrade', 'reset-config', 'daemon'
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
    """
    🔒 ENHANCED VALIDATOR v4 - Non-Interactive Friendly
    
    Security features:
    - Whitelist-based command validation
    - Argument pattern matching
    - Shell injection prevention
    - URL install blocking
    - Path traversal prevention
    
    New features:
    - Supports numeric arguments (for demo selection)
    - Allows non-interactive flags
    - Auto-injects required flags for safety
    """
    if not cmd_str or not cmd_str.strip():
        return False, "Empty command.", None, []
    
    clean_str = cmd_str.strip()
    
    # Strip common prefixes
    if clean_str.lower().startswith("8pkg "):
        clean_str = clean_str[5:].strip()
    elif clean_str.lower().startswith("omnipkg "):
        clean_str = clean_str[8:].strip()
    
    # Remove any piping/chaining attempts
    clean_str = clean_str.split('|')[0].split(';')[0].split('&')[0].strip()
    
    try:
        parts = shlex.split(clean_str)
    except ValueError as e:
        return False, f"⛔ Invalid shell syntax: {e}", None, []
    
    if not parts:
        return False, "No command found.", None, []

    primary_command = parts[0].lower()
    
    # Check if command is blocked
    if primary_command in BLOCKED_COMMANDS:
        return False, f"⛔ Security: '{primary_command}' is disabled via Web.", None, []
    
    # Check if command is in our rules
    if primary_command not in COMMAND_RULES:
        return False, f"⚠️ Unknown command '{primary_command}'.", None, []
    
    rules = COMMAND_RULES[primary_command]
    
    # Validate each argument
    for arg in parts[1:]:
        # Check if it's a flag
        if arg.startswith('-'):
            if arg not in rules.get('allowed_flags', []):
                return False, f"⛔ Flag '{arg}' not allowed for '{primary_command}'.", None, []
        else:
            # It's a positional argument
            if not rules.get('allow_args', False):
                return False, f"⛔ Command '{primary_command}' doesn't accept arguments.", None, []
            
            # Check against pattern
            pattern = rules.get('arg_pattern')
            if pattern and not re.match(pattern, arg):
                return False, f"⛔ Invalid argument format: '{arg}'.", None, []
            
            # Check blocked patterns (for install commands)
            blocked = rules.get('blocked_patterns', [])
            for blocked_pattern in blocked:
                if re.search(blocked_pattern, arg):
                    return False, f"⛔ Security: Argument contains blocked pattern.", None, []
    
    # Get flags to auto-inject
    auto_flags = rules.get('auto_flags', [])
    
    return True, "", clean_str, auto_flags

def execute_omnipkg_command(cmd_str):
    """
    Executes validated commands with streaming output (generator function).
    Yields output line-by-line in real-time.
    """
    is_valid, msg, cleaned_cmd, auto_flags = clean_and_validate(cmd_str)
    if not is_valid:
        yield sanitize_output(msg)
        return

    try:
        args = shlex.split(cleaned_cmd)
        
        # 🤖 AUTO-FLAG INJECTION for safety
        for flag in auto_flags:
            if flag not in args:
                args.append(flag)
                logger.info(f"🔧 Auto-injected flag: {flag}")
        
        full_command = [sys.executable, "-m", "omnipkg", *args]
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["OMNIPKG_WEB_MODE"] = "1"
        env["OMNIPKG_NONINTERACTIVE"] = "1"
        env["CI"] = "1"

        # Use Popen for streaming
        process = subprocess.Popen(
            full_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            env=env,
            startupinfo=startupinfo,
            cwd=Path.home()
        )
        
        # Stream output line by line
        for line in process.stdout:
            yield sanitize_output(line)
        
        process.wait(timeout=180)
        
        if process.returncode != 0:
            yield f"\n⚠️ Exit Code {process.returncode}\n"
        
    except subprocess.TimeoutExpired:
        process.kill()
        yield "\n⚠️ Error: Command timed out (exceeded 3 minutes).\n"
    except Exception as e:
        yield sanitize_output(f"\nSystem Error: {str(e)}\n")

def sanitize_output(text):
    """
    🛡️ Removes sensitive information from command outputs.
    
    Redacts:
    - User paths (Windows and Unix)
    - Private IP addresses
    - API keys/tokens
    - Limits output length
    """
    if not text:
        return text
    
    # Redact Windows user paths
    text = re.sub(r'[A-Za-z]:\\Users\\[^\\]+', r'[USER]', text)
    
    # Redact Unix home paths
    text = re.sub(r'/home/[^/\s]+', r'/home/[USER]', text)
    
    # Redact private IPs
    text = re.sub(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]', text)
    text = re.sub(r'\b192\.168\.\d{1,3}\.\d{1,3}\b', '[IP]', text)
    text = re.sub(r'\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b', '[IP]', text)
    
    # Redact secrets
    text = re.sub(
        r'(api[_-]?key|token|password|secret)[\s:=]+[\w-]{16,}', 
        r'\1=[REDACTED]', 
        text, 
        flags=re.IGNORECASE
    )
    
    # Truncate if too long
    MAX_LENGTH = 10000
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH] + "\n\n... [Output truncated for safety]"
    
    return text

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
    """Creates the Flask application with telemetry support."""
    import sqlite3
    import json
    from datetime import datetime
    
    app = Flask(__name__)
    CORS(app, origins=list(ALLOWED_ORIGINS))
    
    # Silence standard Flask logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Initialize telemetry DB
    DB_FILE = OMNIPKG_DIR / "telemetry.db"
    def init_db():
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    event_name TEXT,
                    page TEXT,
                    meta TEXT
                )
            ''')
    
    try:
        init_db()
        logger.info(f"📊 Telemetry DB initialized at {DB_FILE}")
    except Exception as e:
        logger.error(f"Failed to init DB: {e}")

    @app.before_request
    def enforce_origin():
        if request.method == "OPTIONS":
            return
        
        origin = request.headers.get('Origin')
        
        # 🔓 DEV MODE: Allow missing Origin for curl/testing
        if DEV_MODE and not origin:
            logger.info("⚠️ DEV MODE: Allowing request without Origin header")
            return
        
        # 🔓 TAILSCALE PROXY: Requests from Tailscale proxy don't have Origin
        if not origin and request.headers.get('X-Forwarded-For'):
            logger.info("🔒 Tailscale proxy request detected - allowing")
            return
        
        # Block requests with NO Origin in production
        if not origin and not DEV_MODE:
            return jsonify({
                "error": "❌ Direct API access prohibited.",
                "message": "You must use the OmniPkg Web UI to interact with this bridge.",
                "url": PRIMARY_DASHBOARD
            }), 403
    
        # Validate origin if present
        if origin:
            clean_origin = origin.rstrip("/")
            if clean_origin not in ALLOWED_ORIGINS:
                return jsonify({
                    "error": "❌ Unauthorized Origin.",
                    "message": f"Origin '{clean_origin}' is not whitelisted."
                }), 403

    @app.route('/health', methods=['GET', 'OPTIONS'])
    def health():
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
        
        # Log health check telemetry
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT INTO telemetry (timestamp, event_type, event_name, page, meta) VALUES (?, ?, ?, ?, ?)",
                    (datetime.utcnow().isoformat(), "health_check", "ping", "bridge", json.dumps({"port": port}))
                )
        except Exception as e:
            logger.error(f"DB Error: {e}")
        
        return corsify_response(jsonify({
            "status": "connected", 
            "port": port, 
            "version": "4.0.0"
        }), origin)

    @app.route('/run', methods=['POST', 'OPTIONS'])
    def run_command():
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
        
        data = request.json
        cmd = data.get('command', '')
        logger.info(f"⚡ Executing: {cmd}")
        
        # Log to telemetry
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT INTO telemetry (timestamp, event_type, event_name, page, meta) VALUES (?, ?, ?, ?, ?)",
                    (datetime.utcnow().isoformat(), "command_exec", cmd.split()[0] if cmd.split() else "unknown", "local_bridge", json.dumps({"full_cmd": cmd}))
                )
        except Exception as e:
            logger.error(f"DB Error: {e}")
        
        # Stream response using Server-Sent Events format
        def generate():
            for line in execute_omnipkg_command(cmd):
                yield f"data: {json.dumps({'line': line})}\n\n"
            yield "data: {\"done\": true}\n\n"
        
        response = make_response(generate())
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        return corsify_response(response, origin)

    @app.route('/install-omnipkg', methods=['POST', 'OPTIONS'])
    def install_omnipkg():
        """
        🔒 HARDCODED INSTALLATION ENDPOINT
        Only installs omnipkg from PyPI. No user arguments accepted.
        """
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
        
        logger.info("🔧 Installing omnipkg from PyPI...")
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "omnipkg"],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                output = sanitize_output(result.stdout or "✅ omnipkg installed successfully")
            else:
                output = f"❌ Installation failed:\n{sanitize_output(result.stderr)}"
            
            # Log to telemetry
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute(
                        "INSERT INTO telemetry (timestamp, event_type, event_name, page, meta) VALUES (?, ?, ?, ?, ?)",
                        (datetime.utcnow().isoformat(), "install", "omnipkg", "local_bridge", json.dumps({"success": result.returncode == 0}))
                    )
            except Exception as e:
                logger.error(f"DB Error: {e}")
            
            return corsify_response(jsonify({"output": output}), origin)
            
        except subprocess.TimeoutExpired:
            return corsify_response(jsonify({"output": "❌ Installation timed out"}), origin)
        except Exception as e:
            return corsify_response(jsonify({"output": f"❌ System Error: {sanitize_output(str(e))}"}), origin)

    @app.route('/telemetry', methods=['POST', 'OPTIONS'])
    def telemetry():
        """Receives telemetry data from Cloudflare worker."""
        origin = request.headers.get('Origin')
        if request.method == "OPTIONS": 
            return corsify_response(make_response(), origin)
            
        try:
            data = request.json
            event_type = data.get('event_type', 'unknown')
            event_name = data.get('event_name', 'unknown')
            page = data.get('page', 'unknown')
            meta = json.dumps(data.get('metadata', {}))
            
            # 1. Save locally
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT INTO telemetry (timestamp, event_type, event_name, page, meta) VALUES (?, ?, ?, ?, ?)",
                    (datetime.utcnow().isoformat(), event_type, event_name, page, meta)
                )
            
            logger.info(f"📡 TELEMETRY: [{event_type}] {event_name}")
            
            # 2. Forward to Cloudflare Worker (fire and forget)
            try:
                import requests
                requests.post(
                    'https://omnipkg.1minds3t.workers.dev/analytics/track',
                    json=data,
                    timeout=2
                )
                logger.info("☁️ Telemetry forwarded to Cloudflare")
            except Exception as e:
                logger.warning(f"Failed to forward to Cloudflare: {e}")
            
            return corsify_response(jsonify({"status": "saved"}), origin)
        except Exception as e:
            logger.error(f"Telemetry save failed: {e}")
            return corsify_response(jsonify({"error": str(e)}), origin)
    
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
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)  # ← CHANGE TO 0.0.0.0

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
            kwargs['creationflags'] = 0x00000008 | 0x00000200
        else:
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
            
            if os.name == 'nt':
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                if self.is_running():
                    os.kill(pid, signal.SIGKILL)

            if self.pid_file.exists(): 
                self.pid_file.unlink()
            print("✅ Web bridge stopped")
            return 0
        except Exception as e:
            print(f"❌ Error stopping: {e}")
            if self.pid_file.exists(): 
                self.pid_file.unlink()
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
                subprocess.run(["tail", "-f", str(self.log_file)], check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
                try:
                    with open(self.log_file, "r") as f:
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
                subprocess.run(["tail", "-n", str(lines), str(self.log_file)], check=True)
            except (FileNotFoundError, subprocess.CalledProcessError):
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
            os.kill(pid, 0)
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

# Configure logging
log_handlers = [logging.StreamHandler(sys.stdout)]

try:
    # Ensure directory exists before creating log file
    OMNIPKG_DIR.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(LOG_FILE))
except Exception:
    # If directory creation fails (e.g. permissions), fail silently 
    # and only log to stdout. Do not crash the CLI.
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

# ==========================================
# PART 3: Main Execution
# ==========================================

if __name__ == "__main__":
    run_bridge_server()