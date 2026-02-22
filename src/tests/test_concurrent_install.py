from omnipkg.common_utils import safe_print
import sys
import subprocess
import json
import time
import concurrent.futures
import threading
from omnipkg.i18n import _

print_lock = threading.Lock()

import os as _os
_WIN_ENV = {
    **_os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
    "OMNIPKG_NONINTERACTIVE": "1",
    "OMNIPKG_DEBUG": "1",
}
_SP = dict(encoding="utf-8", errors="replace", env=_WIN_ENV)


def format_duration(ms: float) -> str:
    if ms < 1:
        return f"{ms*1000:.1f}µs"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"


def dump_daemon_log(label: str = "DAEMON LOG DUMP"):
    """Dump the FULL daemon log to stdout, line by line, nothing hidden, no truncation."""
    try:
        from omnipkg.isolation.worker_daemon import DAEMON_LOG_FILE
        safe_print(f"\n{'='*80}")
        safe_print(f"📋 {label}")
        safe_print(f"{'='*80}")
        safe_print(f"[LOG FILE PATH] {DAEMON_LOG_FILE}")
        if not _os.path.exists(DAEMON_LOG_FILE):
            safe_print(f"[LOG FILE] ❌ DOES NOT EXIST: {DAEMON_LOG_FILE}")
            return
        with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
            lines = f.readlines()
        safe_print(f"[LOG FILE] {len(lines)} lines total")
        safe_print(f"{'─'*80}")
        for i, line in enumerate(lines, 1):
            safe_print(f"[LOG:{i:04d}] {line.rstrip()}")
        safe_print(f"{'='*80}\n")
    except Exception as e:
        safe_print(f"[LOG DUMP ERROR] {e}")
        import traceback
        safe_print(traceback.format_exc())


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

    safe_print(_('   🚀 Adopting Python {}...').format(version))
    proc = subprocess.Popen(["omnipkg", "python", "adopt", version], env=_WIN_ENV)
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
            safe_print(_('   ❌ Adopt exited (rc={}) but {} not in registry.').format(rc, version))
            return False
        safe_print(_('   ⏳ Waiting for Python {}...').format(version))
        time.sleep(poll_interval)

    proc.kill()
    safe_print(_('   ❌ Adopt timed out after {}s').format(int(timeout)))
    return False


def daemon_restart() -> bool:
    """
    Stop and restart the daemon so the next run gets cold worker spawn times
    but NOT cold install times (packages already on disk from the discard run).
    This gives us a fair, reproducible cold-spawn baseline.
    """
    safe_print("   🔄 Restarting daemon for cold-spawn baseline...")
    try:
        subprocess.run(["omnipkg", "daemon", "stop"], capture_output=True, **_SP, timeout=15)
        time.sleep(1)
        subprocess.run(["omnipkg", "daemon", "start"], capture_output=True, **_SP, timeout=15)
        # Wait for it to come up
        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()
        for i in range(20):
            time.sleep(0.5)
            try:
                status = client.status()
                if status.get("success"):
                    safe_print(f"   ✅ Daemon restarted after {(i+1)*0.5:.1f}s")
                    return True
            except Exception:
                pass
        safe_print("   ❌ Daemon never came back up after restart")
        return False
    except Exception as e:
        safe_print(f"   ❌ Daemon restart failed: {e}")
        return False


def ensure_daemon_running() -> bool:
    """Start daemon if not running, wait for it to be ready."""
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()
        status = client.status()

        if status.get("success"):
            safe_print("   ✅ Daemon already running")
            return True

        safe_print("   🔄 Starting daemon...")
        proc = subprocess.Popen(
            ["omnipkg", "daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i in range(60):
            time.sleep(0.5)
            status = client.status()
            if status.get("success"):
                safe_print(f"   ✅ Daemon up after {(i+1)*0.5:.1f}s")
                return True

        safe_print("   ❌ Daemon never came up")
        dump_daemon_log("DAEMON LOG ON FAILURE")
        return False

    except Exception as e:
        import traceback
        safe_print(f"   ❌ Daemon error: {e}")
        safe_print(traceback.format_exc())
        return False


def run_concurrent_warmup(test_configs: list, label: str) -> list:
    """
    Concurrently warm up (or re-warm) all workers.
    Returns list of result dicts, or exits on failure.
    """
    safe_print(f"\n🔥 {label}")
    safe_print("-" * 100)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(warmup_worker, config, i + 1): config
            for i, config in enumerate(test_configs)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    if len(results) != len(test_configs):
        safe_print(f"\n❌ {label} failed — dumping full daemon log")
        dump_daemon_log(f"DAEMON LOG AFTER {label} FAILURE")
        sys.exit(1)

    results.sort(key=lambda x: x["thread_id"])
    safe_print(f"\n✅ All workers ready!")
    return results


def warmup_worker(config: tuple, thread_id: int) -> dict:
    py_version, rich_version = config
    prefix = f"[T{thread_id}|Warmup]"

    try:
        python_exe = get_interpreter_path(py_version)

        safe_print(f"{prefix} 🐍 interpreter : {python_exe}")
        safe_print(f"{prefix} 🎯 target      : Python {py_version} + rich=={rich_version}")
        safe_print(f"{prefix} 🧵 thread ident: {threading.get_ident()}")

        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()

        safe_print(f"{prefix} 🔥 Warming up...")
        start = time.perf_counter()

        warmup_code = f"""
import sys, importlib.metadata
print(f"[WORKER:{thread_id}] exe={{sys.executable}}")
print(f"[WORKER:{thread_id}] py={{sys.version}}")
print(f"[WORKER:{thread_id}] rich target={rich_version}")

from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich
    actual = importlib.metadata.version('rich')
    print(f"[WORKER:{thread_id}] rich.__file__={{rich.__file__}}")
    print(f"[WORKER:{thread_id}] rich version={{actual}}")
    assert actual == "{rich_version}", f"VERSION MISMATCH: wanted {rich_version} got {{actual}} in {{sys.executable}}"
"""

        safe_print(f"{prefix} 📤 execute_shm spec=rich=={rich_version} python_exe={python_exe}")

        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=warmup_code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )

        elapsed = (time.perf_counter() - start) * 1000

        safe_print(f"{prefix} 📥 status : {result.get('status')}")
        safe_print(f"{prefix} 📥 success: {result.get('success')}")
        if result.get("stdout"):
            for line in result["stdout"].splitlines():
                safe_print(f"{prefix} [stdout] {line}")
        if result.get("stderr"):
            for line in result["stderr"].splitlines():
                safe_print(f"{prefix} [stderr] {line}")
        if result.get("error"):
            safe_print(f"{prefix} [error ] {result['error']}")
        if result.get("traceback"):
            for line in result["traceback"].splitlines():
                safe_print(f"{prefix} [trace ] {line}")

        if not result.get("success"):
            safe_print(f"{prefix} ❌ FAILED — dumping full daemon log")
            dump_daemon_log(f"DAEMON LOG AFTER T{thread_id} FAILURE")
            return None

        safe_print(f"{prefix} ✅ warmed up in {format_duration(elapsed)}")
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
        dump_daemon_log(f"DAEMON LOG AFTER T{thread_id} EXCEPTION")
        return None


def benchmark_execution(config: tuple, thread_id: int, warmup_data: dict) -> dict:
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"

    try:
        python_exe = get_interpreter_path(py_version)
        safe_print(f"{prefix} ⚡ bench Python {py_version} + rich=={rich_version} via {python_exe}")

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
            safe_print(f"{prefix} ❌ failed: {result.get('error')}")
            dump_daemon_log(f"DAEMON LOG AFTER BENCH T{thread_id} FAILURE")
            return None

        safe_print(f"{prefix} ✅ {format_duration(elapsed)}")
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

        verify_code = f"""
import sys, json, importlib.metadata
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("rich=={rich_version}"):
    import rich
    print(json.dumps({{
        "python_version": sys.version.split()[0],
        "python_path": sys.executable,
        "rich_version": importlib.metadata.version('rich'),
        "rich_file": rich.__file__
    }}))
"""
        result = client.execute_shm(
            spec=f"rich=={rich_version}",
            code=verify_code,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )

        if not result.get("success"):
            raise RuntimeError(f"Verification failed: {result.get('error')}")

        data = json.loads(result.get("stdout", "{}"))
        safe_print(f"{prefix} ✅ Python {data['python_version']} + rich {data['rich_version']}")
        safe_print(f"{prefix}    exe : {data['python_path']}")
        safe_print(f"{prefix}    file: {data['rich_file']}")
        return {"thread_id": thread_id, **data}

    except Exception as e:
        safe_print(f"{prefix} ❌ {e}")
        return None


def print_benchmark_summary(cold_results: list, hot_results: list):
    safe_print("\n" + "=" * 100)
    safe_print("📊 BENCHMARK RESULTS")
    safe_print("=" * 100)
    safe_print(f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Cold (restart)':<18} {'Hot (cached)':<15} {'Speedup':<10}")
    safe_print("-" * 100)

    for cold, hot in zip(
        sorted(cold_results, key=lambda x: x["thread_id"]),
        sorted(hot_results,  key=lambda x: x["thread_id"]),
    ):
        speedup = cold["warmup_time"] / hot["benchmark_time"] if hot["benchmark_time"] > 0 else 0
        safe_print(
            f"T{cold['thread_id']:<7} "
            f"{cold['python_version']:<12} "
            f"{cold['rich_version']:<10} "
            f"{format_duration(cold['warmup_time']):<18} "
            f"{format_duration(hot['benchmark_time']):<15} "
            f"{speedup:.0f}x"
        )

    safe_print("-" * 100)
    cold_times = [r["warmup_time"]    for r in cold_results]
    hot_times  = [r["benchmark_time"] for r in hot_results]
    cold_conc  = max(cold_times)
    hot_conc   = max(hot_times)
    safe_print(
        f"⏱️  Cold concurrent: {format_duration(cold_conc)}  "
        f"Hot concurrent: {format_duration(hot_conc)}  "
        f"Speedup: {cold_conc/hot_conc:.0f}x"
    )
    safe_print(
        f"   Cold avg: {format_duration(sum(cold_times)/len(cold_times))}  "
        f"Hot avg: {format_duration(sum(hot_times)/len(hot_times))}"
    )
    safe_print("=" * 100)


def main():
    start_time = time.perf_counter()

    safe_print("=" * 100)
    safe_print("🚀 CONCURRENT RICH MULTIVERSE - PRODUCTION BENCHMARK")
    safe_print("=" * 100)

    test_configs = [
        ("3.9",  "13.4.2"),
        ("3.10", "13.6.0"),
        ("3.11", "13.7.1"),
    ]

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1: Setup — adopt interpreters
    # ─────────────────────────────────────────────────────────────────────────
    safe_print("\n📥 Phase 1: Setup")
    safe_print("-" * 100)
    for version, _ in test_configs:
        if not adopt_if_needed(version):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)

    safe_print("\n🐍 Resolved interpreter paths:")
    interpreter_paths = []
    for version, _ in test_configs:
        try:
            path = get_interpreter_path(version)
            interpreter_paths.append(path)
            safe_print(f"   Python {version} → {path}")
        except Exception as e:
            safe_print(f"   Python {version} → ❌ ERROR: {e}")
            sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: DISCARD RUN — first-ever run includes package install time.
    # We run it to get packages on disk, but discard the numbers as unfair.
    # ─────────────────────────────────────────────────────────────────────────
    safe_print("\n🗑️  Phase 2: Discard Run (first install — numbers not representative)")
    safe_print("-" * 100)
    safe_print("   ℹ️  This run installs packages into each interpreter's bubble.")
    safe_print("   ℹ️  Numbers are discarded — only used to prime the package cache.")

    if not ensure_daemon_running():
        safe_print("❌ Failed to start daemon")
        dump_daemon_log("DAEMON LOG ON STARTUP FAILURE")
        sys.exit(1)

    run_concurrent_warmup(test_configs, "Discard Run (concurrent install + warmup)")
    dump_daemon_log("DAEMON LOG AFTER DISCARD RUN")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3: DAEMON RESTART — clear worker pool so next run pays cold spawn
    # cost only (packages already on disk, no install overhead).
    # ─────────────────────────────────────────────────────────────────────────
    safe_print("\n🔁 Phase 3: Daemon Restart (cold-spawn baseline)")
    safe_print("-" * 100)
    safe_print("   ℹ️  Packages are already installed. Restarting daemon clears")
    safe_print("   ℹ️  the worker pool so Phase 4 measures pure spawn time only.")

    if not daemon_restart():
        safe_print("❌ Failed to restart daemon — cannot produce fair benchmark")
        dump_daemon_log("DAEMON LOG AFTER RESTART FAILURE")
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 4: COLD BENCHMARK — spawn fresh workers (no install), measure that
    # ─────────────────────────────────────────────────────────────────────────
    cold_results = run_concurrent_warmup(
        test_configs,
        "Phase 4: Cold Benchmark (concurrent, workers fresh after restart)"
    )
    dump_daemon_log("DAEMON LOG AFTER COLD BENCHMARK")

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 5: HOT BENCHMARK — workers already warm, measure dispatch overhead
    # ─────────────────────────────────────────────────────────────────────────
    safe_print("\n⚡ Phase 5: Hot Benchmark (workers already warm)")
    safe_print("-" * 100)

    hot_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(benchmark_execution, config, i + 1, cold_results[i]): config
            for i, config in enumerate(test_configs)
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                hot_results.append(result)

    if len(hot_results) != len(test_configs):
        safe_print("\n❌ Hot benchmark failed")
        dump_daemon_log("DAEMON LOG AFTER HOT BENCHMARK FAILURE")
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 6: Results + Verification
    # ─────────────────────────────────────────────────────────────────────────
    print_benchmark_summary(cold_results, hot_results)

    safe_print("\n🔍 Phase 6: Verification (proving correctness — not timed)")
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
        safe_print("\n" + "=" * 100)
        safe_print("🔍 VERIFICATION RESULTS")
        safe_print("=" * 100)
        for r in sorted(verify_results, key=lambda x: x["thread_id"]):
            safe_print(f"  T{r['thread_id']}: Python {r['python_version']}  rich={r['rich_version']}")
            safe_print(f"         exe : {r['python_path']}")
            safe_print(f"         file: {r['rich_file']}")

    total_time = (time.perf_counter() - start_time) * 1000
    safe_print(f"\n🎉 DONE  total={format_duration(total_time)}")

    dump_daemon_log("DAEMON LOG AT END OF RUN")

    sys.exit(0)


if __name__ == "__main__":
    main()