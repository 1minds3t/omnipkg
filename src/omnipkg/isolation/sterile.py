import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from typing import List, Optional, Tuple

# Optional dependency check
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)


class IdleMonitor:
    """
    Monitors a process and kills it if it stays idle (low CPU) for too long.
    Prevents zombie processes waiting for input that will never come.
    """

    def __init__(self, process: subprocess.Popen, idle_threshold=300.0, cpu_threshold=1.0):
        self.process = process
        self.idle_threshold = idle_threshold
        self.cpu_threshold = cpu_threshold
        self.should_stop = threading.Event()
        self.was_killed = False
        self.monitor_thread = None

    def start(self):
        if not PSUTIL_AVAILABLE:
            return
        self.monitor_thread = threading.Thread(target=self._loop, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        self.should_stop.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1.0)

    def _loop(self):
        import time

        try:
            ps = psutil.Process(self.process.pid)
            idle_start = None
            time.sleep(1)  # Warmup

            while not self.should_stop.is_set() and self.process.poll() is None:
                try:
                    cpu = ps.cpu_percent(interval=1.0)
                    if cpu < self.cpu_threshold:
                        if not idle_start:
                            idle_start = time.time()
                        elif time.time() - idle_start > self.idle_threshold:
                            self._kill_tree(ps)
                            self.was_killed = True
                            return
                    else:
                        idle_start = None
                except:
                    break
        except:
            pass

    def _kill_tree(self, ps):
        try:
            for child in ps.children(recursive=True):
                child.kill()
            ps.kill()
        except:
            pass


class SterileExecutor:
    """
    Runs commands in a highly isolated shell to prevent terminal corruption.
    Uses 'stty sane' to restore terminal if a C++ library crashes hard.
    """

    def __init__(self, enable_idle_detection=True, idle_threshold=60.0):
        self.enable_idle = enable_idle_detection and PSUTIL_AVAILABLE
        self.idle_threshold = idle_threshold

    def run(
        self,
        cmd: List[str],
        timeout: int = 600,
        cwd: Optional[str] = None,
        env: dict = None,
    ) -> Tuple[str, str, int]:

        # 1. Create a temporary Python wrapper script
        # This wrapper handles the signals inside the isolated process
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as script:
            script_path = script.name

            # We inject a script that runs the command and captures output
            # wrapping it in JSON to ensure safe transport back to parent
            wrapper_code = f"""
import subprocess, sys, json, os, signal

# Ignore interrupts in wrapper, let child handle or die
signal.signal(signal.SIGINT, signal.SIG_IGN)

cmd = {json.dumps(cmd)}
cwd = {json.dumps(cwd)}
env = os.environ.copy()
env.update({json.dumps(env or {})})

try:
    # Ensure sane terminal before starting
    subprocess.run(['stty', 'sane'], stderr=subprocess.DEVNULL)
    
    proc = subprocess.run(
        cmd, 
        cwd=cwd, 
        env=env,
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        text=True, 
        errors='replace'
    )
    
    print(json.dumps({{
        'stdout': proc.stdout, 
        'stderr': proc.stderr, 
        'code': proc.returncode
    }}))

except Exception as e:
    print(json.dumps({{'stdout': '', 'stderr': str(e), 'code': 1}}))

finally:
    # Nuclear terminal reset
    subprocess.run(['stty', 'sane'], stderr=subprocess.DEVNULL)
"""
            script.write(wrapper_code)

        # 2. Execute the wrapper
        monitor = None
        try:
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
            )

            if self.enable_idle:
                monitor = IdleMonitor(process, self.idle_threshold)
                monitor.start()

            stdout, stderr = process.communicate(timeout=timeout)

            # 3. Parse result
            try:
                result = json.loads(stdout)
                return result["stdout"], result["stderr"], result["code"]
            except json.JSONDecodeError:
                # If JSON fails, something crashed hard
                return stdout, stderr, process.returncode

        except subprocess.TimeoutExpired:
            process.kill()
            return "", "TIMEOUT", 124
        finally:
            if monitor:
                monitor.stop()
            if os.path.exists(script_path):
                os.unlink(script_path)
