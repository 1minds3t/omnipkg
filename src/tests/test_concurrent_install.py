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


def dump_daemon_log(label: str = "DAEMON LOG DUMP", only_on_error: bool = True, max_lines: int = 50):
    """Dump last max_lines of daemon log. By default only prints if the log contains ERROR/EXCEPTION."""
    try:
        from omnipkg.isolation.worker_daemon import DAEMON_LOG_FILE
        if not _os.path.exists(DAEMON_LOG_FILE):
            if not only_on_error:
                safe_print(f"[DAEMON LOG] ❌ DOES NOT EXIST: {DAEMON_LOG_FILE}")
            return
        with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
            lines = f.readlines()
        # In only_on_error mode, skip dump entirely if no errors in last max_lines
        tail = lines[-max_lines:]
        has_error = any(
            any(kw in ln for kw in ("ERROR", "EXCEPTION", "Traceback", "❌"))
            for ln in tail
        )
        if only_on_error and not has_error:
            return
        safe_print(f"\n{'='*80}")
        safe_print(f"📋 {label}  [{len(lines)} total lines, showing last {len(tail)}]")
        safe_print(f"{'='*80}")
        for ln in tail:
            safe_print(f"[DAEMON] {ln.rstrip()}")
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


def ensure_daemon_running(interpreter_paths: list) -> bool:
    """
    Start daemon if not running, then wait until it has spawned idle workers
    for ALL of the given interpreter paths. Must be called AFTER all pythons
    are adopted so the daemon registry sees them on startup.
    """
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DAEMON_LOG_FILE
        client = DaemonClient()
        status = client.status()

        if status.get("success"):
            safe_print("   ✅ Daemon already running")
        else:
            safe_print("   🔄 Starting daemon (all pythons already adopted)...")
            _daemon_start = time.perf_counter()
            # Use Popen so we don't block — daemon start detaches itself on Windows
            proc = subprocess.Popen(
                ["8pkg", "daemon", "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give it a moment to detach then check if it came up
            for i in range(60):
                time.sleep(0.5)
                status = client.status()
                if status.get("success"):
                    _daemon_elapsed = (time.perf_counter() - _daemon_start) * 1000
                    safe_print(f"   ✅ Daemon up after {format_duration(_daemon_elapsed)}")
                    break
            else:
                safe_print("   ❌ Daemon never came up")
                dump_daemon_log("DAEMON LOG ON FAILURE", only_on_error=False)
                return False

        return True

    except Exception as e:
        import traceback
        safe_print(f"   ❌ Daemon error: {e}")
        safe_print(traceback.format_exc())
        return False


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

        # NOTE: rich.__version__ does NOT exist in rich>=13 — must use importlib.metadata
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

        # Print every field — nothing hidden
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

        # importlib.metadata — not rich.__version__
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


def print_benchmark_summary(results: list, total_time: float):
    safe_print("\n" + "=" * 100)
    safe_print("📊 PRODUCTION BENCHMARK RESULTS")
    safe_print("=" * 100)
    safe_print(f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Warmup':<15} {'Benchmark':<15}")
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
    bt = [r["benchmark_time"] for r in results]
    wt = [r["warmup_time"] for r in results]
    seq = sum(bt)
    conc = max(bt)
    safe_print(f"⏱️  Sequential: {format_duration(seq)}  |  Concurrent: {format_duration(conc)}  |  Speedup: {seq/conc:.2f}x")
    safe_print(f"   Warmup avg: {format_duration(sum(wt)/len(wt))}  |  Bench avg: {format_duration(sum(bt)/len(bt))}  |  Warmup→Hot: {sum(wt)/len(wt)/(sum(bt)/len(bt)):.1f}x")
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

    # Phase 1: Setup
    safe_print("\n📥 Phase 1: Setup")
    safe_print("-" * 100)
    for version, _ in test_configs:
        if not adopt_if_needed(version):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)

    # Print resolved interpreter paths — catch any wrong resolution immediately
    safe_print("\n🐍 Resolved interpreter paths:")
    for version, _ in test_configs:
        try:
            path = get_interpreter_path(version)
            safe_print(f"   Python {version} → {path}")
        except Exception as e:
            safe_print(f"   Python {version} → ❌ ERROR: {e}")

    # Resolve all interpreter paths AFTER adoption so daemon sees them all on start
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

    # Phase 0: Install via versioned dispatcher (8pkg39, 8pkg310, etc.)
    # This is the key test — does 8pkg3X actually install into the RIGHT interpreter?
    safe_print("\n📦 Phase 0: Install via versioned dispatcher (OMNIPKG_DEBUG=1)")
    safe_print("-" * 100)

    def _versioned_cmd(version: str) -> str:
        """Return the versioned command name: 3.9 -> 8pkg39, 3.10 -> 8pkg310, etc."""
        flat = version.replace(".", "")
        # On Windows the shim is a .bat or .exe; on Unix it is a plain symlink.
        # subprocess will find it via PATH on both platforms.
        return f"8pkg{flat}"

    def _install_via_dispatcher(version: str, pkg_spec: str, thread_id: int) -> dict:
        prefix = f"[T{thread_id}|Install|{version}]"
        cmd_name = _versioned_cmd(version)

        # Build the command.  On Windows 'shell=True' is needed so .bat shims resolve.
        if sys.platform == "win32":
            cmd = f"{cmd_name} install {pkg_spec}"
            use_shell = True
        else:
            cmd = [cmd_name, "install", pkg_spec]
            use_shell = False

        env = {**_WIN_ENV, "OMNIPKG_DEBUG": "1"}

        with print_lock:
            safe_print(f"{prefix} ▶ {cmd_name} install {pkg_spec}")

        t0 = time.perf_counter()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                shell=use_shell,
                env=env,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except FileNotFoundError:
            elapsed = (time.perf_counter() - t0) * 1000
            with print_lock:
                safe_print(f"{prefix} ❌ COMMAND NOT FOUND: {cmd_name}  ({format_duration(elapsed)})")
                safe_print(f"{prefix}    PATH={env.get('PATH', '(not set)')}")
            return {"version": version, "pkg": pkg_spec, "ok": False,
                    "error": f"FileNotFoundError: {cmd_name}", "elapsed_ms": elapsed}
        except subprocess.TimeoutExpired:
            elapsed = (time.perf_counter() - t0) * 1000
            with print_lock:
                safe_print(f"{prefix} ❌ TIMEOUT after {format_duration(elapsed)}")
            return {"version": version, "pkg": pkg_spec, "ok": False,
                    "error": "TimeoutExpired", "elapsed_ms": elapsed}

        elapsed = (time.perf_counter() - t0) * 1000
        ok = result.returncode == 0

        with print_lock:
            status = "✅" if ok else "❌"
            safe_print(f"{prefix} {status} rc={result.returncode}  time={format_duration(elapsed)}")
            # Always show stdout/stderr — this is the dispatcher debug output
            # showing WHICH interpreter actually got targeted
            if result.stdout:
                for line in result.stdout.splitlines():
                    safe_print(f"{prefix} [stdout] {line}")
            if result.stderr:
                for line in result.stderr.splitlines():
                    safe_print(f"{prefix} [stderr] {line}")
            if not ok:
                safe_print(f"{prefix} ⚠️  Install failed — daemon phase will still run so we can see where it installs")

        return {
            "version": version,
            "pkg": pkg_spec,
            "ok": ok,
            "returncode": result.returncode,
            "elapsed_ms": elapsed,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    # Run installs concurrently — one per Python version
    install_configs = [(v, f"rich=={r}") for v, r in test_configs]
    install_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(install_configs)) as executor:
        futs = {
            executor.submit(_install_via_dispatcher, ver, pkg, i + 1): (ver, pkg)
            for i, (ver, pkg) in enumerate(install_configs)
        }
        for fut in concurrent.futures.as_completed(futs):
            install_results.append(fut.result())

    # Summary
    safe_print("\n📦 Install summary:")
    for r in sorted(install_results, key=lambda x: x["version"]):
        status = "✅" if r["ok"] else "❌"
        safe_print(f"   {status} Python {r['version']} — {r['pkg']}  ({format_duration(r['elapsed_ms'])})")
    all_installed = all(r["ok"] for r in install_results)
    if not all_installed:
        safe_print("\n⚠️  Some installs failed — continuing to daemon phase to capture behaviour")

    if not ensure_daemon_running(interpreter_paths):
        safe_print("❌ Failed to start daemon")
        dump_daemon_log("DAEMON LOG ON STARTUP FAILURE")
        sys.exit(1)

    # Phase 2: Cold run (first call = daemon worker spawn + import)
    safe_print("\n🔥 Phase 2: Cold run — daemon worker spawn + first import (concurrent)")
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
        safe_print("\n❌ Warmup failed — dumping full daemon log")
        dump_daemon_log("DAEMON LOG AFTER WARMUP FAILURE")
        sys.exit(1)

    warmup_results.sort(key=lambda x: x["thread_id"])
    safe_print("\n✅ All workers warmed up!")

    # Phase 3: Warm run (worker already live, import already cached)
    safe_print("\n⚡ Phase 3: Warm run — hot worker, import already cached (concurrent)")
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
        safe_print("\n❌ Benchmark failed")
        dump_daemon_log("DAEMON LOG AFTER BENCHMARK FAILURE")
        sys.exit(1)

    benchmark_total = (time.perf_counter() - benchmark_start) * 1000
    print_benchmark_summary(benchmark_results, benchmark_total)

    # Phase 4: Verification
    safe_print("\n🔍 Phase 4: Verification (not timed)")
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

    sys.exit(0)


if __name__ == "__main__":
    main()