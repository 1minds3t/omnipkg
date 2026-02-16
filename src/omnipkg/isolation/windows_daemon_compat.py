"""
Windows Daemon Compatibility Layer
===================================
On Windows, we FAKE the daemon by using PersistentWorker directly.
This avoids the infinite spawn bug while keeping the daemon API intact.
"""
import sys
import os
import time
import tempfile
from pathlib import Path
from typing import Optional, Dict

# Import the real PersistentWorker
try:
    from omnipkg.isolation.worker_daemon import PersistentWorker
except ImportError:
    # Fallback for testing
    PersistentWorker = None

# Paths
OMNIPKG_TEMP_DIR = Path(tempfile.gettempdir()) / "omnipkg"
FAKE_DAEMON_PID_FILE = OMNIPKG_TEMP_DIR / "daemon.pid"
FAKE_DAEMON_SOCKET_FILE = OMNIPKG_TEMP_DIR / "daemon_connection.txt"


class WindowsFakeDaemon:
    """
    Fake daemon that uses PersistentWorker pool directly.
    Mimics the daemon API but doesn't spawn any background processes.
    """
    
    def __init__(self):
        self.workers: Dict[str, PersistentWorker] = {}  # {spec: PersistentWorker}
        self.running = False
        
    def start(self, daemonize=True, wait_for_ready=False):
        """Fake start - just create fake PID file."""
        os.makedirs(OMNIPKG_TEMP_DIR, exist_ok=True)
        
        # Write fake PID file
        FAKE_DAEMON_PID_FILE.write_text(str(os.getpid()))
        
        # Write fake socket file
        FAKE_DAEMON_SOCKET_FILE.write_text("tcp://127.0.0.1:5678")
        
        self.running = True
        
        print("✅ Daemon started (Windows fake mode)", file=sys.stderr)
        return True
    
    def stop(self):
        """Fake stop - cleanup workers and remove PID file."""
        # Shutdown all workers
        for spec, worker in self.workers.items():
            try:
                worker.shutdown()
            except:
                pass
        
        self.workers.clear()
        
        # Remove fake files
        try:
            FAKE_DAEMON_PID_FILE.unlink()
        except:
            pass
        
        try:
            FAKE_DAEMON_SOCKET_FILE.unlink()
        except:
            pass
        
        self.running = False
        return True
    
    def is_running(self):
        """Check if fake daemon is "running"."""
        return FAKE_DAEMON_PID_FILE.exists()
    
    def execute_shm(self, spec: str, code: str, shm_in: str, shm_out: str, python_exe: str = None):
        """
        Execute code using PersistentWorker instead of daemon.
        This is what DaemonClient.execute_shm() calls.
        """
        if python_exe is None:
            python_exe = sys.executable
        
        # Get or create worker for this spec
        worker_key = f"{python_exe}::{spec}"
        
        if worker_key not in self.workers:
            # Create new worker
            self.workers[worker_key] = PersistentWorker(
                package_spec=spec,
                python_exe=python_exe,
                verbose=False,
                defer_setup=False
            )
        
        worker = self.workers[worker_key]
        
        # Execute code in worker
        try:
            result = worker.execute(code)
            return {
                "success": result.get("success", False),
                "data": result.get("stdout", ""),
                "error": result.get("error", ""),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    def status(self):
        """Return fake daemon status."""
        return {
            "success": True,
            "running": self.running,
            "workers": len(self.workers),
            "mode": "Windows Fake Daemon",
        }


# Global fake daemon instance
_fake_daemon: Optional[WindowsFakeDaemon] = None


def get_fake_daemon() -> WindowsFakeDaemon:
    """Get or create the global fake daemon."""
    global _fake_daemon
    if _fake_daemon is None:
        _fake_daemon = WindowsFakeDaemon()
    return _fake_daemon


class WindowsDaemonClient:
    """
    Drop-in replacement for DaemonClient on Windows.
    Uses WindowsFakeDaemon instead of real socket connection.
    """
    
    def __init__(self, socket_path=None, timeout=300.0, auto_start=True):
        self.timeout = timeout
        self.auto_start = auto_start
        self.daemon = get_fake_daemon()
        
        # Auto-start if requested and not running
        if auto_start and not self.daemon.is_running():
            self.daemon.start()
    
    def execute_shm(self, spec, code, shm_in, shm_out, python_exe=None):
        """Execute code in worker (fake daemon mode)."""
        # Auto-start if not running
        if not self.daemon.is_running() and self.auto_start:
            self.daemon.start()
        
        return self.daemon.execute_shm(spec, code, shm_in, shm_out, python_exe)
    
    def status(self):
        """Get daemon status."""
        if not self.daemon.is_running():
            return {"success": False, "error": "Daemon not running"}
        return self.daemon.status()
    
    def shutdown(self):
        """Shutdown daemon."""
        return {"success": self.daemon.stop()}
    
    def get_idle_config(self):
        """Get idle pool config (not used in fake mode)."""
        return {"success": True, "config": {}}
    
    def set_idle_config(self, python_exe, count):
        """Set idle pool config (not used in fake mode)."""
        return {"success": True}


def cli_start():
    """Fake CLI start for Windows."""
    daemon = get_fake_daemon()
    
    if daemon.is_running():
        print("⚠️  Daemon is already running (fake mode).")
        return
    
    print("🚀 Starting OmniPkg Worker Daemon (Windows fake mode)...", end=" ", flush=True)
    daemon.start()


def cli_stop():
    """Fake CLI stop for Windows."""
    daemon = get_fake_daemon()
    
    if not daemon.is_running():
        print("❌ Daemon not running")
        return
    
    daemon.stop()
    print("✅ Daemon stopped")


def cli_status():
    """Fake CLI status for Windows."""
    daemon = get_fake_daemon()
    
    if not daemon.is_running():
        print("❌ Daemon not running")
        return
    
    status = daemon.status()
    print(f"✅ Daemon running (fake mode)")
    print(f"   Workers: {status['workers']}")
    print(f"   Mode: {status['mode']}")


def cli_logs(follow=False, tail_lines=50):
    """Fake logs (no logs in fake mode)."""
    print("ℹ️  No logs available in Windows fake daemon mode")
    print("   Workers run in-process, check stdout/stderr of your application")


def cli_idle_config(python_version=None, count=None):
    """Fake idle config (not used in fake mode)."""
    print("ℹ️  Idle pool configuration not used in Windows fake daemon mode")
    print("   Workers are created on-demand and reused per spec")


# Export the same API as worker_daemon.py
__all__ = [
    'WindowsDaemonClient',
    'cli_start',
    'cli_stop', 
    'cli_status',
    'cli_logs',
    'cli_idle_config',
]
