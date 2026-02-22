from omnipkg.common_utils import safe_print
import sys
import subprocess
import json
import time
import concurrent.futures
import threading
import importlib.metadata
from omnipkg.i18n import _

print_lock = threading.Lock()

# Windows subprocess environment — UTF-8, unbuffered, non-interactive
import os as _os
_WIN_ENV = {
    **_os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
    "OMNIPKG_NONINTERACTIVE": "1",
    "OMNIPKG_DEBUG": "1",  # Always on so we see what interpreter is used
}
_SP = dict(encoding="utf-8", errors="replace", env=_WIN_ENV)


def format_duration(ms: float) -> str:
    if ms < 1:
        return f"{ms*1000:.1f}µs"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"


def _run_info_python() -> str:
    result = subprocess.run(
        ["omnipkg", "info", "python"],
        capture_output=True,
        **_SP,
    )
    return result.stdout or ""


def verify_registry_contains(version: str) -> bool:
    try:
        output = _run_info_python()
        for line in output.splitlines():
            if f"Python {version}:" in line:
                return True
    except Exception:
        return False
    return False


def get_interpreter_path(version: str) -> str:
    try:
        output = _run_info_python()
    except Exception as e:
        raise RuntimeError(_('Failed to query omnipkg: {}').format(e)) from e
    for line in output.splitlines():
        if f"Python {version}:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                path_part = parts[1].strip().split()[0]
                return path_part
    raise RuntimeError(_('Python {} not found in registry').format(version))


def adopt_if_needed(version: str, poll_interval: float = 5.0, timeout: float = 300.0) -> bool:
    if verify_registry_contains(version):
        safe_print(_('   ✅ Python {} already available.').format(version))
        return True

    safe_print(_('   🚀 Adopting Python {} (streaming live output)...').format(version))

    proc = subprocess.Popen(
        ["omnipkg", "python", "adopt", version],
        env=_WIN_ENV,
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if verify_registry_contains(version):
            if rc is None:
                proc.wait()
            safe_print(_('   ✅ Python {} confirmed in registry.').format(version))
            return True
        if rc is not None:
            if rc == 0:
                time.sleep(1)
                if verify_registry_contains(version):
                    safe_print(_('   ✅ Python {} confirmed in registry.').format(version))
                    return True
            safe_print(_('   ❌ Adopt process exited (code {}) but Python {} not in registry.').format(rc, version))
            return False
        safe_print(_('   ⏳ Waiting for Python {}... (polling registry)').format(version))
        time.sleep(poll_interval)

    proc.kill()
    safe_print(_('   ❌ Adopt timed out after {}s').format(int(timeout)))
    return False


def ensure_daemon_running() -> bool:
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DAEMON_LOG_FILE
        client = DaemonClient()
        status = client.status()
        if status.get("success"):
            safe_print("   ✅ Daemon already running")
            try:
                import os
                if os.path.exists(DAEMON_LOG_FILE):
                    safe_print(f"   [DAEMON LOG tail] {DAEMON_LOG_FILE}")
                    with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
                        tail = f.read()[-2000:]
                    safe_print(tail)
            except Exception:
                pass
            return True

        safe_print("   🔄 Starting daemon...")
        result = subprocess.run(
            ["8pkg", "daemon", "start"],
            capture_output=False,
            timeout=30,
        )
        safe_print(f"   [DEBUG] daemon start exited with code: {result.returncode}")

        import os
        if os.path.exists(DAEMON_LOG_FILE):
            safe_print(f"   [DEBUG] === {DAEMON_LOG_FILE} ===")
            with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
                safe_print(f.read())
        else:
            safe_print(f"   [DEBUG] No log file found at {DAEMON_LOG_FILE}")

        for _ in range(60):
            time.sleep(0.5)
            status = client.status()
            if status.get("success"):
                safe_print("   ✅ Daemon started successfully")
                return True

        safe_print("   ❌ Failed to start daemon")
        return False
    except Exception as e:
        import traceback
        safe_print(f"   ❌ Daemon error: {e}")
        safe_print(traceback.format_exc())
        return False


def _install_rich_into_interpreter(prefix: str, python_exe: str, rich_version: str) -> None:
    """
    Explicitly pip-install rich=={rich_version} INTO the target interpreter.
    This bypasses omnipkg and ensures the correct interpreter gets the package.
    Prints full command + output so nothing is hidden.
    """
    # Use the versioned 8pkg alias if available, otherwise fall back to pip via python_exe
    # We show the EXACT command being run with no hiding
    cmd = [python_exe, "-m", "pip", "install",
           f"rich=={rich_version}",
           "--quiet", "--no-cache-dir"]

    safe_print(f"{prefix} 🔧 [INSTALL CMD] {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=_WIN_ENV,
    )

    if result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            safe_print(f"{prefix}   [pip stdout] {line}")
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            safe_print(f"{prefix}   [pip stderr] {line}")

    if result.returncode != 0:
        raise RuntimeError(f"pip install failed (rc={result.returncode}) for {python_exe}")

    safe_print(f"{prefix} ✅ [INSTALL DONE] rich=={rich_version} into {python_exe}")


def _verify_rich_in_interpreter(prefix: str, python_exe: str, rich_version: str) -> str:
    """
    Run a tiny subprocess using EXACTLY python_exe to confirm rich is the right version.
    Returns the actual version string found.
    Prints full command + output.
    """
    check_code = (
        "import importlib.metadata; "
        "print(importlib.metadata.version('rich'))"
    )
    cmd = [python_exe, "-c", check_code]
    safe_print(f"{prefix} 🔍 [VERSION CHECK CMD] {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=_WIN_ENV,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    safe_print(f"{prefix}   [version check stdout] '{stdout}'")
    if stderr:
        safe_print(f"{prefix}   [version check stderr] '{stderr}'")

    if result.returncode != 0:
        raise RuntimeError(f"Version check subprocess failed (rc={result.returncode})")

    return stdout


def warmup_worker(config: tuple, thread_id: int) -> dict:
    py_version, rich_version = config
    prefix = f"[T{thread_id}|Warmup]"

    try:
        python_exe = get_interpreter_path(py_version)

        safe_print(f"{prefix} ══════════════════════════════════════════")
        safe_print(f"{prefix} 🎯 TARGET:      Python {py_version} + Rich {rich_version}")
        safe_print(f"{prefix} 🐍 INTERPRETER: {python_exe}")
        safe_print(f"{prefix} 🧵 THREAD ID:   {thread_id} (os tid={threading.get_ident()})")

        # ── Step 1: Confirm which Python this interpreter IS ──────────────────
        py_check_cmd = [python_exe, "-c", "import sys; print(sys.version); print(sys.executable)"]
        safe_print(f"{prefix} 🔍 [PYTHON CHECK] {' '.join(py_check_cmd)}")
        py_check = subprocess.run(py_check_cmd, capture_output=True, encoding="utf-8",
                                  errors="replace", env=_WIN_ENV)
        for line in py_check.stdout.strip().splitlines():
            safe_print(f"{prefix}   [python check] {line}")

        # ── Step 2: Install rich into THIS interpreter directly via pip ────────
        safe_print(f"{prefix} 📦 Installing rich=={rich_version} directly into {python_exe} ...")
        _install_rich_into_interpreter(prefix, python_exe, rich_version)

        # ── Step 3: Verify version using importlib.metadata via subprocess ─────
        # NOTE: rich.__version__ does NOT exist in recent releases — use importlib.metadata
        safe_print(f"{prefix} 🔬 Verifying rich version via importlib.metadata (not __version__)...")
        actual_version = _verify_rich_in_interpreter(prefix, python_exe, rich_version)
        safe_print(f"{prefix} 📌 Confirmed: rich=={actual_version} in {python_exe}")

        if actual_version != rich_version:
            raise RuntimeError(
                f"Version mismatch! Wanted {rich_version} but found {actual_version} "
                f"in {python_exe}"
            )

        # ── Step 4: Now run via daemon, passing python_exe explicitly ──────────
        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()

        safe_print(f"{prefix} 🔥 Warming up via daemon (python_exe={python_exe})...")
        start = time.perf_counter()

        # FIXED: use importlib.metadata.version() NOT rich.__version__
        warmup_code = f"""
import sys, os
import importlib.metadata

print(f"[WORKER:{thread_id}] THREAD={thread_id} interpreter={{sys.executable}}")
print(f"[WORKER:{thread_id}] Python version: {{sys.version}}")
print(f"[WORKER:{thread_id}] Rich version to load: {rich_version}")

from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich
    # rich.__version__ does NOT exist in rich >= 13.x — use importlib.metadata
    actual = importlib.metadata.version('rich')
    print(f"[WORKER:{thread_id}] Loaded Rich from: {{rich.__file__}}")
    print(f"[WORKER:{thread_id}] Rich version (importlib.metadata): {{actual}}")
    if actual != "{rich_version}":
        raise RuntimeError(f"VERSION MISMATCH in daemon worker: wanted {rich_version} got {{actual}} — interpreter={{sys.executable}}")
"""

        safe_print(f"{prefix} 🔍 Executing warmup code via daemon:")
        safe_print(f"{prefix}   spec=rich=={rich_version}")
        safe_print(f"{prefix}   python_exe={python_exe}")

        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=warmup_code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )

        elapsed = (time.perf_counter() - start) * 1000

        safe_print(f"{prefix} 🔍 Daemon result: {json.dumps(result, indent=2)}")

        if not result.get("success"):
            safe_print(f"{prefix} ❌ Daemon warmup FAILED: {result.get('error')}")
            safe_print(f"{prefix} === FULL RESULT ===")
            for k, v in result.items():
                safe_print(f"{prefix}   {k}: {v}")
            return None

        if result.get("stdout"):
            safe_print(f"{prefix} 📤 Worker stdout:")
            for line in result["stdout"].splitlines():
                safe_print(f"{prefix}   {line}")

        safe_print(f"{prefix} ✅ Warmed up in {format_duration(elapsed)}")
        safe_print(f"{prefix} ══════════════════════════════════════════")

        return {
            "thread_id": thread_id,
            "python_version": py_version,
            "rich_version": rich_version,
            "warmup_time": elapsed,
        }

    except Exception as e:
        safe_print(f"{prefix} ❌ EXCEPTION: {e}")
        import traceback
        safe_print(traceback.format_exc())
        return None


def benchmark_execution(config: tuple, thread_id: int, warmup_data: dict) -> dict:
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"

    try:
        python_exe = get_interpreter_path(py_version)

        safe_print(f"{prefix} ⚡ Benchmarking Python {py_version} + Rich {rich_version} via {python_exe}")

        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()

        benchmark_code = f"""
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich
"""

        start = time.perf_counter()

        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=benchmark_code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )

        elapsed = (time.perf_counter() - start) * 1000

        if not result.get("success"):
            raise RuntimeError(_('Benchmark execution failed: {}').format(result.get('error')))

        safe_print(f"{prefix} ✅ Benchmark: {format_duration(elapsed)}")

        return {
            "thread_id": thread_id,
            "python_version": py_version,
            "rich_version": rich_version,
            "warmup_time": warmup_data["warmup_time"],
            "benchmark_time": elapsed,
        }

    except Exception as e:
        safe_print(f"{prefix} ❌ {e}")
        return None


def verify_execution(config: tuple, thread_id: int) -> dict:
    py_version, rich_version = config
    prefix = f"[T{thread_id}|Verify]"

    try:
        python_exe = get_interpreter_path(py_version)

        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()

        # FIXED: use importlib.metadata.version() NOT rich.__version__
        verify_code = f"""
import sys
import json
import importlib.metadata

from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich

    result = {{
        "python_version": sys.version.split()[0],
        "python_path": sys.executable,
        "rich_version": importlib.metadata.version('rich'),
        "rich_file": rich.__file__
    }}
    print(json.dumps(result))
"""

        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=verify_code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )

        if not result.get("success"):
            raise RuntimeError(_('Verification failed: {}').format(result.get('error')))

        data = json.loads(result.get("stdout", "{}"))

        safe_print(f"{prefix} ✅ Python {data['python_version']} + Rich {data['rich_version']}")
        safe_print(f"{prefix}    exe:  {data['python_path']}")
        safe_print(f"{prefix}    file: {data['rich_file']}")

        return {
            "thread_id": thread_id,
            "python_version": data["python_version"],
            "python_path": data["python_path"],
            "rich_version": data["rich_version"],
            "rich_file": data["rich_file"],
        }

    except Exception as e:
        safe_print(f"{prefix} ❌ {e}")
        return None


def print_benchmark_summary(results: list, total_time: float):
    safe_print("\n" + "=" * 100)
    safe_print("📊 PRODUCTION BENCHMARK RESULTS")
    safe_print("=" * 100)

    safe_print(
        f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Warmup':<15} {'Benchmark':<15}"
    )
    safe_print("-" * 100)

    for r in sorted(results, key=lambda x: x["thread_id"]):
        safe_print(
            f"T{r['thread_id']:<7} "
            f"{r['python_version']:<12} "
            f"{r['rich_version']:<10} "
            f"{format_duration(r['warmup_time']):<15} "
            f"{format_duration(r['benchmark_time']):<15}"
        )

    safe_print("-" * 100)

    benchmark_times = [r["benchmark_time"] for r in results]
    warmup_times = [r["warmup_time"] for r in results]

    sequential_time = sum(benchmark_times)
    concurrent_time = max(benchmark_times)

    avg_benchmark = sum(benchmark_times) / len(benchmark_times)
    avg_warmup = sum(warmup_times) / len(warmup_times)
    min_benchmark = min(benchmark_times)
    max_benchmark = max(benchmark_times)

    safe_print(f"⏱️  Sequential time (sum of all):   {format_duration(sequential_time)}")
    safe_print(f"⏱️  Concurrent time (longest one):  {format_duration(concurrent_time)}")
    safe_print("=" * 100)

    safe_print("\n🎯 PERFORMANCE METRICS:")
    safe_print("-" * 100)
    safe_print(f"   Warmup (cold start):     {format_duration(avg_warmup)} avg")
    safe_print(f"   Benchmark (hot workers): {format_duration(avg_benchmark)} avg")
    safe_print(f"   Range:                   {format_duration(min_benchmark)} - {format_duration(max_benchmark)}")
    safe_print(f"   Speedup (warmup→hot):    {avg_warmup / avg_benchmark:.1f}x")
    speedup = sequential_time / concurrent_time
    safe_print(f"   Concurrent speedup:      {speedup:.2f}x")
    safe_print("-" * 100)


def print_verification_summary(results: list):
    safe_print("\n" + "=" * 100)
    safe_print("🔍 VERIFICATION RESULTS (Proof of Correctness)")
    safe_print("=" * 100)

    for r in sorted(results, key=lambda x: x["thread_id"]):
        safe_print(f"\nT{r['thread_id']}: Python {r['python_version']}")
        safe_print(f"     Executable: {r['python_path']}")
        safe_print(f"     Rich {r['rich_version']}")
        safe_print(f"     Loaded from: {r['rich_file']}")

    safe_print("\n" + "=" * 100)


def main():
    start_time = time.perf_counter()

    safe_print("=" * 100)
    safe_print("🚀 CONCURRENT RICH MULTIVERSE - PRODUCTION BENCHMARK")
    safe_print("=" * 100)
    safe_print("\n💡 Production benchmark protocol:")
    safe_print("   1. WARMUP: Spawn workers, install packages (timing discarded)")
    safe_print("   2. BENCHMARK: Pure execution with hot workers (THIS IS THE METRIC)")
    safe_print("   3. VERIFICATION: Prove correctness (optional, not timed)")
    safe_print("=" * 100)

    test_configs = [
        ("3.9",  "13.4.2"),
        ("3.10", "13.6.0"),
        ("3.11", "13.7.1"),
    ]

    # Phase 1: Setup
    safe_print("\n📥 Phase 1: Setup")
    safe_print("-" * 100)

    # Print all interpreter paths up front so we can see what's resolved
    for version, _ in test_configs:
        if not adopt_if_needed(version):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)

    safe_print("\n🐍 Resolved interpreter paths:")
    for version, _ in test_configs:
        try:
            path = get_interpreter_path(version)
            safe_print(f"   Python {version} → {path}")
        except Exception as e:
            safe_print(f"   Python {version} → ERROR: {e}")

    if not ensure_daemon_running():
        safe_print("❌ Failed to start daemon")
        sys.exit(1)

    # Phase 2: Warmup (concurrent)
    safe_print("\n🔥 Phase 2: Warmup (concurrent worker spawn + package install)")
    safe_print("-" * 100)

    warmup_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(warmup_worker, config, i + 1): config
            for i, config in enumerate(test_configs)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                warmup_results.append(result)

    if len(warmup_results) != len(test_configs):
        safe_print("\n❌ Warmup failed for some workers")
        sys.exit(1)

    warmup_results.sort(key=lambda x: x["thread_id"])
    safe_print("\n✅ All workers warmed up successfully!")

    # Phase 3: Benchmark
    safe_print("\n⚡ Phase 3: PRODUCTION BENCHMARK (hot workers, concurrent execution)")
    safe_print("-" * 100)

    benchmark_results = []
    benchmark_start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(benchmark_execution, config, i + 1, warmup_results[i]): config
            for i, config in enumerate(test_configs)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                benchmark_results.append(result)

    if len(benchmark_results) != len(test_configs):
        safe_print("\n❌ Benchmark failed for some workers")
        sys.exit(1)

    benchmark_total = (time.perf_counter() - benchmark_start) * 1000
    print_benchmark_summary(benchmark_results, benchmark_total)

    # Phase 4: Verification
    safe_print("\n🔍 Phase 4: Verification (proving correctness - NOT timed)")
    safe_print("-" * 100)

    verify_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(verify_execution, config, i + 1): config
            for i, config in enumerate(test_configs)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                verify_results.append(result)

    if len(verify_results) == len(test_configs):
        print_verification_summary(verify_results)

    total_time = (time.perf_counter() - start_time) * 1000

    safe_print("\n🎉 BENCHMARK COMPLETE!")
    safe_print(f"\n⏱️  Total test duration: {format_duration(total_time)}")
    safe_print("\n🚀 This is IMPOSSIBLE with traditional Python environments!")

    sys.exit(0)


if __name__ == "__main__":
    main()