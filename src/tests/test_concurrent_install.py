"""
omnipkg - Concurrent Multiverse Production Benchmark
File: test_concurrent_install.py
Strength: Industrial / CI-Grade
"""

import os
import sys
import time
import json
import threading
import subprocess
import concurrent.futures
from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

# --- THREAD SAFETY & OUTPUT CONTROL ---
print_lock = threading.Lock()

# --- PLATFORM CONSTANTS & ENVIRONMENT ---
_IS_WINDOWS = os.name == 'nt'
_WIN_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
    "OMNIPKG_NONINTERACTIVE": "1",
    "OMNIPKG_DEBUG": "1",
}

# Critical: Windows must use shell=True to find .bat wrappers in the PATH
_SP = {
    "encoding": "utf-8", 
    "errors": "replace", 
    "env": _WIN_ENV, 
    "shell": _IS_WINDOWS
}

# --- UTILITY HELPERS ---

def format_duration(ms: float) -> str:
    """High-precision duration formatting for benchmarks."""
    if ms < 0.001:
        return f"{ms*1000000:.1f}ns"
    if ms < 1:
        return f"{ms*1000:.1f}µs"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"

def dump_daemon_log(label: str = "DAEMON LOG DUMP", limit: int = 50):
    """
    Defensive log dumping. 
    Only shows the last N lines on failure to prevent terminal overflow.
    """
    try:
        from omnipkg.isolation.worker_daemon import DAEMON_LOG_FILE
        with print_lock:
            safe_print(f"\n{'='*100}")
            safe_print(f"📋 {label} (Trailing {limit} lines)")
            safe_print(f"{'='*100}")
            safe_print(f"[LOG PATH] {DAEMON_LOG_FILE}")
            
            if not os.path.exists(DAEMON_LOG_FILE):
                safe_print(f"[LOG FILE] ❌ NOT FOUND")
                return

            with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
                lines = f.readlines()
            
            safe_print(f"[LOG SIZE] {len(lines)} lines total")
            safe_print(f"{'─'*100}")
            for i, line in enumerate(lines[-limit:], 1):
                safe_print(f"[LOG:L-{limit-i:02d}] {line.rstrip()}")
            safe_print(f"{'='*100}\n")
    except Exception as e:
        safe_print(f"[LOG DUMP ERROR] {e}")

def _run_omnipkg_cmd(args: list) -> str:
    """Internal wrapper for executing omnipkg CLI commands."""
    try:
        result = subprocess.run(args, capture_output=True, **_SP)
        return result.stdout or ""
    except Exception as e:
        safe_print(f"   ❌ CLI Error: {e}")
        return ""

def verify_registry_contains(version: str) -> bool:
    """Check if the specific Python version is registered in the omnipkg database."""
    output = _run_omnipkg_cmd(["omnipkg", "info", "python"])
    for line in output.splitlines():
        if f"Python {version}:" in line:
            return True
    return False

def get_interpreter_path(version: str) -> str:
    """Retrieve the absolute path to a managed Python interpreter."""
    output = _run_omnipkg_cmd(["omnipkg", "info", "python"])
    for line in output.splitlines():
        if f"Python {version}:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                # Extract path, ignoring status flags
                return parts[1].strip().split()[0]
    raise RuntimeError(_('Python {} not found in registry').format(version))

# --- PHASED WORKFLOW LOGIC ---

def phase_adopt(version: str) -> float:
    """PHASE 1: Python Adoption. Times the system binding."""
    start = time.perf_counter()
    if verify_registry_contains(version):
        safe_print(f"   ✅ Python {version} already present.")
        return 0.0
    
    safe_print(f"   🚀 Adopting Python {version}...")
    subprocess.run(["omnipkg", "python", "adopt", version], **_SP)
    
    # Verification loop
    for _ in range(10):
        if verify_registry_contains(version):
            return (time.perf_counter() - start) * 1000
        time.sleep(1)
    
    raise RuntimeError(f"Failed to confirm adoption of Python {version}")

def phase_install(py_ver: str, pkg_spec: str) -> dict:
    """PHASE 2: Installation & Bubble Sync. Detects No-Op / Cache hits."""
    start = time.perf_counter()
    safe_print(f"   📦 Syncing {pkg_spec} for Python {py_ver}...")
    
    # Using 'omnipkg install' which is the production entry point for bubbling
    subprocess.run(["omnipkg", "install", pkg_spec], **_SP)
    
    elapsed = (time.perf_counter() - start) * 1000
    # Requirement: Consider under 5s a No-Op (Already cached)
    is_noop = elapsed < 5000 
    
    return {"elapsed": elapsed, "is_noop": is_noop}

def phase_daemon_start():
    """PHASE 3: Daemon Orchestration."""
    safe_print("   🔄 Initializing Daemon Service...")
    subprocess.run(["omnipkg", "daemon", "start"], **_SP)
    
    # Wait for daemon ready signal
    from omnipkg.isolation.worker_daemon import DaemonClient
    client = DaemonClient()
    for _ in range(20):
        if client.status().get("success"):
            safe_print("   ✅ Daemon ready.")
            return
        time.sleep(0.5)
    
    dump_daemon_log("DAEMON STARTUP FAILURE")
    raise RuntimeError("Daemon failed to respond to status query.")

# --- CONCURRENT WORKER LOGIC ---

def run_multiverse_worker(config: tuple, thread_id: int, label: str) -> dict:
    """
    Executes a high-performance worker task via the daemon.
    Restores full stdout/stderr/traceback logging for deep debugging.
    """
    py_version, rich_version = config
    prefix = f"[T{thread_id}|{label}]"

    try:
        python_exe = get_interpreter_path(py_version)
        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()

        # Complex internal code for worker verification
        code = f"""
import sys, importlib.metadata
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich
    v = importlib.metadata.version('rich')
    print(f"WORKER_OK:{{v}}")
    print(f"EXE:{{sys.executable}}")
    print(f"FILE:{{rich.__file__}}")
    assert v == "{rich_version}", f"Version Mismatch: Expected {rich_version}, got {{v}}"
"""
        start = time.perf_counter()
        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )
        elapsed = (time.perf_counter() - start) * 1000

        with print_lock:
            # Exhaustive output restoration
            if result.get("stdout"):
                for line in result["stdout"].splitlines():
                    if "WORKER_OK" not in line: # Filter noisy success flags
                        safe_print(f"{prefix} [OUT] {line}")
            
            if not result.get("success"):
                safe_print(f"{prefix} ❌ EXECUTION FAILED")
                if result.get("error"):
                    safe_print(f"{prefix} [ERR] {result['error']}")
                if result.get("traceback"):
                    safe_print(f"{prefix} [TRACE]\n{result['traceback']}")
                return None

        return {
            "thread_id": thread_id,
            "py": py_version,
            "rich": rich_version,
            "time": elapsed,
            "success": True
        }

    except Exception as e:
        with print_lock:
            safe_print(f"{prefix} ❌ SYSTEM EXCEPTION: {e}")
        return None

# --- MAIN BENCHMARK ENGINE ---

def main():
    overall_start = time.perf_counter()
    test_configs = [
        ("3.9",  "13.4.2"),
        ("3.10", "13.6.0"),
        ("3.11", "13.7.1"),
    ]

    safe_print("=" * 110)
    safe_print("🌠 omnipkg CONCURRENT MULTIVERSE PRODUCTION BENCHMARK")
    safe_print("=" * 110)

    # PHASE 1: ADOPT
    safe_print("\n[PHASE 1] INTERPRETER ADOPTION (Sequential)")
    safe_print("-" * 110)
    adopt_results = {}
    for py, _ in test_configs:
        t = phase_adopt(py)
        adopt_results[py] = t
        safe_print(f"   ✅ Python {py:<5} : {format_duration(t)}")

    # PHASE 2: INSTALL
    safe_print("\n[PHASE 2] BUBBLE SYNCHRONIZATION & NO-OP CHECK")
    safe_print("-" * 110)
    install_metrics = []
    for py, rich in test_configs:
        res = phase_install(py, f"rich=={rich}")
        install_metrics.append({"py": py, "rich": rich, **res})
        note = " (NO-OP/CACHED)" if res["is_noop"] else " (FULL INSTALL)"
        safe_print(f"   ✅ rich {rich:<8} for Py {py:<5} : {format_duration(res['elapsed'])}{note}")

    # PHASE 3: DAEMON
    safe_print("\n[PHASE 3] DAEMON SERVICE INITIALIZATION")
    safe_print("-" * 110)
    phase_daemon_start()

    # PHASE 4: COLD START
    safe_print("\n[PHASE 4] CONCURRENT COLD SPAWN (Process Creation)")
    safe_print("-" * 110)
    cold_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(run_multiverse_worker, cfg, i+1, "COLD"): cfg 
                   for i, cfg in enumerate(test_configs)}
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res: cold_results.append(res)

    if len(cold_results) < 3:
        safe_print("\n❌ CRITICAL: Cold Phase failed to initialize all workers.")
        dump_daemon_log("COLD SPAWN FAILURE", limit=50)
        sys.exit(1)

    for r in sorted(cold_results, key=lambda x: x["thread_id"]):
        safe_print(f"   T{r['thread_id']} Spawn Time : {format_duration(r['time'])}")

    # PHASE 5: HOT BENCHMARK
    safe_print("\n[PHASE 5] CONCURRENT HOT BENCHMARK (Sub-ms Context Switch)")
    safe_print("-" * 110)
    hot_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(run_multiverse_worker, cfg, i+1, "HOT"): cfg 
                   for i, cfg in enumerate(test_configs)}
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res: hot_results.append(res)

    if len(hot_results) < 3:
        safe_print("\n❌ CRITICAL: Hot Phase failed.")
        dump_daemon_log("HOT BENCHMARK FAILURE", limit=50)
        sys.exit(1)

    # --- FINAL PRODUCTION SUMMARY ---
    
    safe_print("\n" + "=" * 110)
    safe_print(f"{'THREAD':<8} | {'PYTHON':<10} | {'RICH':<12} | {'COLD SPAWN':<18} | {'HOT SWITCH':<18} | {'GAIN'}")
    safe_print("-" * 110)
    
    cold_map = {r['thread_id']: r for r in cold_results}
    hot_map = {r['thread_id']: r for r in hot_results}
    
    for i in range(1, 4):
        c, h = cold_map[i], hot_map[i]
        gain = c['time'] / h['time']
        safe_print(f"T{i:<7} | {c['py']:<10} | {c['rich']:<12} | {format_duration(c['time']):<18} | {format_duration(h['time']):<18} | {gain:.1f}x")

    safe_print("-" * 110)
    
    avg_cold = sum(r['time'] for r in cold_results) / 3
    avg_hot = sum(r['time'] for r in hot_results) / 3
    total_wall = (time.perf_counter() - overall_start) * 1000
    
    safe_print(f"📈 BENCHMARK METRICS:")
    safe_print(f"   • Mean Cold Launch    : {format_duration(avg_cold)}")
    safe_print(f"   • Mean Hot Switching : {format_duration(avg_hot)}")
    safe_print(f"   • Optimization Ratio : {avg_cold/avg_hot:.1f}x Faster")
    safe_print(f"   • Total Runtime      : {format_duration(total_wall)}")
    safe_print("=" * 110)

    # Fail-safe Performance Alert
    if avg_hot > 150: # Trigger log dump if performance degrades below CI threshold
        safe_print("\n⚠️ PERFORMANCE WARNING: Hot switching exceeded 150ms. Inspecting Daemon logs.")
        dump_daemon_log("PERFORMANCE DEGRADATION LOG", limit=30)
    
    safe_print("\n🎉 MULTIVERSE BENCHMARK SUCCESSFUL.")
    sys.exit(0)

if __name__ == "__main__":
    main()