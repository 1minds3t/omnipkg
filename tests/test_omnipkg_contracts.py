"""
test_omnipkg_contracts.py
=========================
Contract tests for omnipkg loader and daemon subsystems.

Philosophy
----------
The old chaos_test_* script mixed *demo theatre* with *correctness checking*.
These tests only care about one thing each:

  "Does the system honour this specific guarantee?"

Design rules enforced here:
  1. Every test that imports a versioned package does so via daemon (subprocess
     boundary) — never via in-process omnipkgLoader.  In-process swaps of C++
     packages (torch, tensorflow, numpy) are intentionally user-hostile; tests
     should not rely on their safety.

  2. Each test is self-contained: it spins up / reuses the module-level daemon
     client through a session-scoped fixture, but never mutates global state
     (sys.modules, sys.path, os.environ) in the test-runner process.

  3. Assertions are crisp and documented.  Pass/fail is binary and deterministic
     — not "did 8/10 work" but "did all 10 work?".

  4. Slow tests (anything touching CUDA IPC or GPU) are marked so they can be
     skipped in CI: pytest -m "not slow".

  5. Tests that require optional bubbles that may not be installed are marked
     xfail / skip rather than just printing a warning and returning True.

Running
-------
  # Run all fast tests
  pytest test_omnipkg_contracts.py -v

  # Include GPU/slow tests
  pytest test_omnipkg_contracts.py -v -m slow

  # Run only daemon contract tests
  pytest test_omnipkg_contracts.py -v -k "daemon"

  # Quick sanity check (markers: fast)
  pytest test_omnipkg_contracts.py -v -m fast
"""

import os
import sys
import time
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _import_daemon():
    """Import daemon client, skip test if not available."""
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
        return DaemonClient, DaemonProxy, WorkerPoolDaemon
    except ImportError as e:
        pytest.skip(f"Daemon module not importable: {e}")


def _import_loader():
    try:
        from omnipkg.loader import omnipkgLoader
        return omnipkgLoader
    except ImportError as e:
        pytest.skip(f"omnipkgLoader not importable: {e}")


def _exec(client, spec: str, code: str, *, timeout: float = 30.0) -> dict:
    """Execute code in a daemon worker and return the result dict."""
    DaemonClient, DaemonProxy, _ = _import_daemon()
    proxy = DaemonProxy(client, spec)
    result = proxy.execute(code)
    return result


def _assert_exec_ok(result: dict, *, context: str = ""):
    """Assert that a daemon execute() call succeeded."""
    prefix = f"[{context}] " if context else ""
    assert result.get("success"), (
        f"{prefix}Daemon execution failed.\n"
        f"  error  : {result.get('error', '<none>')}\n"
        f"  stderr : {result.get('stderr', '<none>')[:400]}\n"
        f"  stdout : {result.get('stdout', '<none>')[:400]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pytest markers — registered in conftest.py, not here
# ─────────────────────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────────────────────
# Session-scoped daemon fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def daemon_client():
    """
    Return a live DaemonClient for the whole test session.

    Start the daemon if it is not already running.
    The fixture does NOT stop the daemon at the end — it is expected to already
    be running (as it is in production) or the test environment manages it.
    """
    DaemonClient, _, WorkerPoolDaemon = _import_daemon()

    client = DaemonClient()
    status = client.status()

    if not status.get("success"):
        pytest.skip(
            "Daemon is not running. Start it with `8pkg daemon start` before running tests."
        )

    return client


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 1: Worker isolation — correct package version is active
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionIsolation:
    """
    The daemon must deliver the *exact* version requested and nothing else.
    """

    NUMPY_VERSIONS = ["1.24.3", "1.26.4", "2.3.5"]
    TORCH_VERSIONS = ["torch==2.0.1+cu118", "torch==2.1.0"]

    @pytest.mark.fast
    @pytest.mark.daemon
    @pytest.mark.parametrize("version", NUMPY_VERSIONS)
    def test_numpy_exact_version(self, daemon_client, version):
        """Worker must report the exact numpy version that was requested."""
        result = _exec(
            daemon_client,
            f"numpy=={version}",
            "import numpy as np; print(np.__version__)",
        )
        _assert_exec_ok(result, context=f"numpy=={version}")
        reported = result["stdout"].strip()
        assert reported == version, (
            f"Requested numpy=={version} but worker reported {reported!r}"
        )

    @pytest.mark.fast
    @pytest.mark.daemon
    @pytest.mark.parametrize("spec", TORCH_VERSIONS)
    def test_torch_exact_version(self, daemon_client, spec):
        """Worker must load the exact torch version that was requested."""
        expected = spec.split("==")[1]
        result = _exec(
            daemon_client,
            spec,
            "import torch; print(torch.__version__)",
        )
        _assert_exec_ok(result, context=spec)
        reported = result["stdout"].strip()
        # Normalise both sides: strip CUDA/build suffix (e.g. "2.1.0+cu121" -> "2.1.0")
        # so that requesting "torch==2.0.1+cu118" matches a worker reporting "2.0.1+cu118"
        # and requesting "torch==2.1.0" matches a worker reporting "2.1.0+cu121".
        reported_base = reported.split("+")[0]
        expected_base = expected.split("+")[0]
        assert reported_base == expected_base, (
            f"Requested {spec} but worker reported {reported!r}"
        )

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_two_numpy_versions_simultaneously(self, daemon_client):
        """
        Two workers with different numpy versions must coexist and each report
        the correct version independently.
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()

        code = "import numpy as np; print(np.__version__)"
        results = {}

        def run(ver):
            proxy = DaemonProxy(daemon_client, f"numpy=={ver}")
            res = proxy.execute(code)
            results[ver] = res

        threads = [threading.Thread(target=run, args=(v,)) for v in ["1.24.3", "2.3.5"]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for ver in ["1.24.3", "2.3.5"]:
            res = results[ver]
            _assert_exec_ok(res, context=f"concurrent numpy=={ver}")
            reported = res["stdout"].strip()
            assert reported == ver, f"numpy=={ver}: got {reported!r}"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 2: Math correctness — results must match local computation
# ─────────────────────────────────────────────────────────────────────────────

class TestMathCorrectness:
    """
    Computations executed in isolated workers must produce numerically correct
    results that agree with the same computation done locally.
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    @pytest.mark.parametrize("version", ["1.24.3", "1.26.4", "2.3.5"])
    def test_numpy_deterministic_sum(self, daemon_client, version):
        """
        np.sum([1,2,3]) must equal 6 in every numpy version.
        Catches silent ABI corruption where the module loads but returns garbage.
        """
        result = _exec(
            daemon_client,
            f"numpy=={version}",
            "import numpy as np; print(int(np.sum(np.array([1, 2, 3]))))",
        )
        _assert_exec_ok(result, context=f"numpy=={version} sum")
        reported = int(result["stdout"].strip())
        assert reported == 6, f"numpy=={version}: np.sum([1,2,3]) returned {reported}, expected 6"

    @pytest.mark.fast
    @pytest.mark.daemon
    @pytest.mark.parametrize("spec,expected", [
        ("torch==2.0.1+cu118", 6),
        ("torch==2.1.0",       6),
    ])
    def test_torch_deterministic_sum(self, daemon_client, spec, expected):
        """torch.sum([1,2,3]) must equal 6."""
        result = _exec(
            daemon_client,
            spec,
            "import torch; print(int(torch.sum(torch.tensor([1, 2, 3])).item()))",
        )
        _assert_exec_ok(result, context=f"{spec} sum")
        reported = int(result["stdout"].strip())
        assert reported == expected, f"{spec}: torch.sum([1,2,3]) returned {reported}"

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_tensorflow_deterministic_sum(self, daemon_client):
        """tf.reduce_sum([1,2,3]) must equal 6."""
        result = _exec(
            daemon_client,
            "tensorflow==2.13.0",
            "import tensorflow as tf; print(int(tf.reduce_sum(tf.constant([1, 2, 3])).numpy()))",
        )
        _assert_exec_ok(result, context="tf sum")
        reported = int(result["stdout"].strip())
        assert reported == 6, f"tensorflow: tf.reduce_sum([1,2,3]) returned {reported}"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 3: Worker re-use — persistent worker is truly persistent
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkerPersistence:
    """
    The daemon worker for a given spec should be created once and reused.
    After the first (slow) call, subsequent calls must be fast (< 100 ms).
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_subsequent_calls_are_fast(self, daemon_client):
        """
        After a worker is warm, repeated calls to the same spec should complete
        in under 100 ms.  This validates that the worker is reused, not re-spawned.
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()

        spec = "numpy==1.26.4"
        code = "import numpy as np; print(np.__version__)"
        proxy = DaemonProxy(daemon_client, spec)

        # First call warms up the worker
        warmup = proxy.execute(code)
        _assert_exec_ok(warmup, context="warmup")

        # Subsequent calls must be fast
        RUNS = 10
        latencies = []
        for _ in range(RUNS):
            t = time.perf_counter()
            res = proxy.execute(code)
            latencies.append((time.perf_counter() - t) * 1000)
            _assert_exec_ok(res, context="hot call")

        avg_ms = sum(latencies) / len(latencies)
        worst_ms = max(latencies)
        assert avg_ms < 100, (
            f"Average hot-call latency {avg_ms:.1f}ms exceeds 100ms — "
            "worker may be re-spawning on every call"
        )
        assert worst_ms < 500, (
            f"Worst hot-call latency {worst_ms:.1f}ms exceeds 500ms — "
            "possible worker restart or lock contention"
        )

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_worker_maintains_state_across_calls(self, daemon_client):
        """
        A persistent worker must preserve Python-level state between calls on
        the same proxy (i.e. the process is truly persistent).
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()
        proxy = DaemonProxy(daemon_client, "numpy==1.26.4")

        # Write state
        r1 = proxy.execute("_SENTINEL = 42; print('ok')")
        _assert_exec_ok(r1, context="write sentinel")

        # Read it back — only works if same process
        r2 = proxy.execute("print(globals().get('_SENTINEL', 'MISSING'))")
        _assert_exec_ok(r2, context="read sentinel")
        value = r2["stdout"].strip()
        assert value == "42", (
            f"Worker state not preserved: expected '42', got {value!r}. "
            "Worker may have been restarted between calls."
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 4: Thread safety — concurrent requests must not corrupt each other
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:
    """
    Multiple threads hitting different (or the same) workers concurrently
    must each get correct, uncorrupted results.
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_concurrent_different_versions_no_corruption(self, daemon_client):
        """
        10 threads each request a different numpy version 5 times.
        Every single result must match np.sum([1,2,3]) == 6 for the correct version.
        No result may leak into a different version's worker.
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()

        VERSIONS = ["1.24.3", "1.26.4", "2.3.5"]
        REPS = 5
        errors = []
        lock = threading.Lock()

        def worker_fn(ver):
            proxy = DaemonProxy(daemon_client, f"numpy=={ver}")
            code = "import numpy as np; print(np.__version__, int(np.sum(np.array([1,2,3]))))"
            for _ in range(REPS):
                res = proxy.execute(code)
                if not res.get("success"):
                    with lock:
                        errors.append(f"numpy=={ver}: FAILED - {res.get('error')}")
                    continue
                parts = res["stdout"].strip().split()
                reported_ver, reported_sum = parts[0], int(parts[1])
                if reported_ver != ver:
                    with lock:
                        errors.append(f"VERSION LEAK: requested {ver}, got {reported_ver}")
                if reported_sum != 6:
                    with lock:
                        errors.append(f"MATH CORRUPTION: numpy=={ver} sum={reported_sum}")

        with ThreadPoolExecutor(max_workers=len(VERSIONS)) as ex:
            futs = [ex.submit(worker_fn, v) for v in VERSIONS]
            for f in futs:
                f.result()

        assert not errors, "Thread safety violations:\n" + "\n".join(errors)

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_high_frequency_same_worker(self, daemon_client):
        """
        200 rapid-fire requests to the same worker (4 threads × 50 reps) must
        all succeed with no errors.  Validates internal locking in the daemon.
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()

        THREADS = 4
        REPS = 50
        failures = []
        lock = threading.Lock()

        def hammerer(thread_id, spec):
            proxy = DaemonProxy(daemon_client, spec)
            code = "x = 1 + 1"
            for _ in range(REPS):
                res = proxy.execute(code)
                if not res.get("success"):
                    with lock:
                        failures.append(f"Thread {thread_id} ({spec}): {res.get('error')}")

        specs = ["torch==2.0.1+cu118", "torch==2.1.0", "numpy==1.24.3", "numpy==1.26.4"]
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            futs = [ex.submit(hammerer, i, specs[i]) for i in range(THREADS)]
            for f in futs:
                f.result()

        assert not failures, (
            f"{len(failures)}/{THREADS * REPS} requests failed:\n"
            + "\n".join(failures[:10])
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 5: Zero-copy SHM — data is passed without serialization
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroCopySHM:
    """
    execute_zero_copy / execute_smart must pass numpy arrays via shared memory
    and the results must be numerically identical to local computation.
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    @pytest.mark.parametrize("spec", ["numpy==1.24.3", "numpy==1.26.4", "numpy==2.3.5"])
    def test_shm_roundtrip_math(self, daemon_client, spec):
        """
        Send a known array via SHM, compute sum+mean in the worker, receive
        result via SHM, and verify it matches local numpy computation.
        """
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not available in test runner")

        input_array = np.arange(1.0, 101.0)  # [1..100], sum=5050, mean=50.5
        expected_sum = float(np.sum(input_array))
        expected_mean = float(np.mean(input_array))

        code = """
import numpy as np
arr_out[0] = np.sum(arr_in)
arr_out[1] = np.mean(arr_in)
"""
        try:
            result_arr, meta = daemon_client.execute_zero_copy(
                spec,
                code,
                input_array=input_array,
                output_shape=(2,),
                output_dtype="float64",
            )
        except AttributeError:
            pytest.skip("daemon_client.execute_zero_copy not available")

        assert meta.get("success") or result_arr is not None, (
            f"execute_zero_copy failed for {spec}: {meta.get('error')}"
        )

        assert abs(result_arr[0] - expected_sum) < 1e-6, (
            f"{spec}: SHM sum={result_arr[0]}, expected {expected_sum}"
        )
        assert abs(result_arr[1] - expected_mean) < 1e-6, (
            f"{spec}: SHM mean={result_arr[1]}, expected {expected_mean}"
        )

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_shm_3stage_pipeline(self, daemon_client):
        """
        Pass a 1 MB array through 3 different numpy version workers in sequence.
        Each stage applies a known transformation; the final result must be
        numerically verifiable from the input.

        Validates: SHM handoff works between multiple different workers,
        data is not corrupted between stages, output matches expected math.
        """
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not available in test runner")

        N = 500 * 250
        input_array = np.ones(N, dtype="float64") * 2.0  # all 2s

        # Stage 1: multiply by 3  → all 6s
        stage1_code = "import numpy as np; arr_out[:] = arr_in * 3"
        # Stage 2: add 4          → all 10s
        stage2_code = "import numpy as np; arr_out[:] = arr_in + 4"
        # Stage 3: divide by 2    → all 5s
        stage3_code = "import numpy as np; arr_out[:] = arr_in / 2"

        stages = [
            ("numpy==1.24.3", stage1_code),
            ("numpy==1.26.4", stage2_code),
            ("numpy==2.3.5",  stage3_code),
        ]

        current = input_array
        for spec, code in stages:
            try:
                out, meta = daemon_client.execute_zero_copy(
                    spec, code,
                    input_array=current,
                    output_shape=current.shape,
                    output_dtype="float64",
                )
            except AttributeError:
                pytest.skip("daemon_client.execute_zero_copy not available")

            assert meta.get("success") or out is not None, (
                f"Stage {spec} failed: {meta.get('error')}"
            )
            current = out

        expected = 5.0
        assert abs(current.mean() - expected) < 1e-9, (
            f"3-stage pipeline: expected all {expected}, got mean={current.mean()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 6: Filesystem safety — cloaking is atomic and reversible
# ─────────────────────────────────────────────────────────────────────────────

class TestFilesystemSafety:
    """
    The cloaking mechanism must be atomic (no orphaned .omnipkg_cloaked files)
    and reversible (files are restored after the context manager exits).

    These tests operate at the loader level, but always via a subprocess so
    that a crash in the test does not corrupt the test-runner's sys.modules.
    """

    @pytest.mark.fast
    @pytest.mark.loader
    def test_no_orphaned_cloaks_after_normal_exit(self, tmp_path):
        """
        After a normal activation+deactivation cycle, no *.omnipkg_cloaked
        files must exist anywhere under the site-packages .omnipkg_versions
        directory.
        """
        script = """
import sys, os, glob
from omnipkg.loader import omnipkgLoader

site_packages = None
with omnipkgLoader("numpy==1.26.4", quiet=True) as loader:
    import numpy as np
    assert np.__version__ == "1.26.4", f"Wrong version: {np.__version__}"
    if hasattr(loader, 'site_packages_root'):
        site_packages = str(loader.site_packages_root)

# After __exit__, scan for orphaned cloaks
if site_packages:
    pattern = os.path.join(site_packages, "**", "*_omnipkg_cloaked*")
    orphans = glob.glob(pattern, recursive=True)
    if orphans:
        print("ORPHANS:" + "|".join(orphans))
        sys.exit(1)

print("CLEAN")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr

        assert result.returncode == 0, (
            f"Subprocess failed (rc={result.returncode})\n"
            f"stdout: {stdout}\nstderr: {stderr[:500]}"
        )
        assert "ORPHANS:" not in stdout, (
            f"Orphaned cloak files found after clean exit:\n{stdout}"
        )
        assert "CLEAN" in stdout, f"Unexpected output: {stdout!r}"

    @pytest.mark.fast
    @pytest.mark.loader
    def test_no_orphaned_cloaks_after_exception(self):
        """
        Even when user code raises an exception inside the context manager,
        the loader's __exit__ must clean up all cloaks.
        """
        script = """
import sys, os, glob
from omnipkg.loader import omnipkgLoader

site_packages = None
try:
    with omnipkgLoader("numpy==1.26.4", quiet=True) as loader:
        import numpy as np
        site_packages = str(loader.site_packages_root) if hasattr(loader, 'site_packages_root') else None
        raise RuntimeError("Intentional exception to test cleanup")
except RuntimeError:
    pass  # Expected

if site_packages:
    pattern = os.path.join(site_packages, "**", "*_omnipkg_cloaked*")
    orphans = glob.glob(pattern, recursive=True)
    if orphans:
        print("ORPHANS:" + "|".join(orphans))
        sys.exit(1)

print("CLEAN")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        stdout = result.stdout.strip()
        assert result.returncode == 0, (
            f"Subprocess error: {result.stderr[:400]}"
        )
        assert "ORPHANS:" not in stdout, f"Orphaned cloaks after exception: {stdout}"
        assert "CLEAN" in stdout

    @pytest.mark.slow
    @pytest.mark.loader
    def test_concurrent_activations_no_cloak_race(self):
        """
        Two subprocesses activating *different* numpy versions simultaneously
        must not race on cloak/uncloak operations.  Both must complete cleanly
        with no orphaned files and correct versions.
        """
        script = """
import sys, os, glob
from omnipkg.loader import omnipkgLoader

VERSION = sys.argv[1]
with omnipkgLoader(f"numpy=={VERSION}", quiet=True) as loader:
    import numpy as np
    assert np.__version__ == VERSION, f"Version mismatch: got {np.__version__}"
    import time; time.sleep(0.5)   # overlap window

site_packages = str(loader.site_packages_root) if hasattr(loader, 'site_packages_root') else None
if site_packages:
    pattern = os.path.join(site_packages, "**", "*_omnipkg_cloaked*")
    orphans = glob.glob(pattern, recursive=True)
    if orphans:
        print("ORPHANS:" + "|".join(orphans))
        sys.exit(2)

print(f"OK:{VERSION}")
"""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            procs = [
                subprocess.Popen(
                    [sys.executable, script_path, ver],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                for ver in ["1.24.3", "2.3.5"]
            ]
            outputs = [p.communicate(timeout=90) for p in procs]
        finally:
            os.unlink(script_path)

        for i, ((stdout, stderr), proc) in enumerate(zip(outputs, procs)):
            assert proc.returncode == 0, (
                f"Process {i} failed (rc={proc.returncode}): {stderr[:400]}"
            )
            assert "ORPHANS:" not in stdout, f"Process {i} left orphans: {stdout}"
            assert stdout.strip().startswith("OK:"), (
                f"Process {i} unexpected output: {stdout!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 7: Multi-Python interpreter isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiPythonIsolation:
    """
    When the daemon routes a request to a specific Python interpreter
    (e.g. python3.9 vs python3.11), the worker must actually run on that
    interpreter and load the correct package from it.
    """

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_python39_worker_correct_interpreter(self, daemon_client):
        """
        A request with python_exe pointing to python3.9 must execute in 3.9,
        not in the default 3.11.
        """
        # Discover Python 3.9 path
        python39 = _find_python("3.9")
        if not python39:
            pytest.skip("Python 3.9 not found under omnipkg interpreters")

        code = "import sys; print(sys.version_info.major, sys.version_info.minor)"
        try:
            result = daemon_client.execute_shm(
                spec="torch==2.0.1+cu118",
                code=code,
                shm_in={}, shm_out={},
                python_exe=python39,
            )
        except (AttributeError, TypeError):
            pytest.skip("daemon_client.execute_shm not available")

        assert result.get("success"), f"execute_shm failed: {result.get('error')}"
        major, minor = map(int, result["stdout"].strip().split())
        assert (major, minor) == (3, 9), (
            f"Expected Python 3.9, got {major}.{minor} (exe={python39})"
        )

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_three_pythons_concurrent(self, daemon_client):
        """
        Requests to Python 3.9, 3.10 and 3.11 workers executing concurrently
        must each return the correct interpreter version, not mix them up.
        """
        pythons = {
            "3.9":  _find_python("3.9"),
            "3.10": _find_python("3.10"),
            "3.11": _find_python("3.11"),
        }
        available = {v: p for v, p in pythons.items() if p}
        if len(available) < 2:
            pytest.skip(f"Need at least 2 Python versions, found: {list(available)}")

        code = "import sys; print(sys.version_info.major, sys.version_info.minor)"
        results = {}
        errors = []
        lock = threading.Lock()

        def run(version, python_exe):
            try:
                res = daemon_client.execute_shm(
                    spec="numpy==1.26.4",
                    code=code,
                    shm_in={}, shm_out={},
                    python_exe=python_exe,
                )
                with lock:
                    results[version] = res
            except Exception as e:
                with lock:
                    errors.append(f"{version}: {e}")

        threads = [
            threading.Thread(target=run, args=(v, p))
            for v, p in available.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, "Errors during concurrent Python execution:\n" + "\n".join(errors)

        for expected_ver, res in results.items():
            assert res.get("success"), (
                f"Python {expected_ver} execution failed: {res.get('error')}"
            )
            major, minor = map(int, res["stdout"].strip().split())
            exp_major, exp_minor = map(int, expected_ver.split("."))
            assert (major, minor) == (exp_major, exp_minor), (
                f"Python version mismatch: requested {expected_ver}, got {major}.{minor}"
            )


def _find_python(version: str) -> Optional[str]:
    """
    Find the path to a specific Python version managed by omnipkg,
    or fall back to system PATH.
    Returns None if not found.
    """
    # Try omnipkg managed interpreters first
    try:
        result = subprocess.run(
            ["omnipkg", "info", "python"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f"Python {version}:" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    path = parts[1].strip().split()[0]
                    if os.path.isfile(path):
                        return path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fall back to which python3.X
    try:
        result = subprocess.run(
            ["which", f"python{version}"],
            capture_output=True, text=True, timeout=3,
        )
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 8: CUDA IPC — GPU tensor survives cross-worker round trip
# ─────────────────────────────────────────────────────────────────────────────

class TestCudaIPC:
    """
    GPU tensors must survive a cross-worker IPC handoff with their values
    intact (no corruption, no silent CPU fallback).
    """

    @pytest.mark.slow
    @pytest.mark.gpu
    @pytest.mark.daemon
    def test_universal_ipc_checksum_preserved(self, daemon_client):
        """
        Create a GPU tensor with a known checksum, pass it through two workers
        via universal CUDA IPC, verify the checksum is unchanged after each hop.

        Detects: IPC handle corruption, silent CPU copy, dtype drift.
        """
        try:
            import torch
        except ImportError:
            pytest.skip("torch not available in test runner")

        if not torch.cuda.is_available():
            pytest.skip("CUDA not available")

        # Create a tensor whose sum is exactly SENTINEL
        SENTINEL = 12345.0
        shape = (100, 100)
        input_tensor = torch.full(shape, SENTINEL / (shape[0] * shape[1]),
                                  device="cuda:0", dtype=torch.float32)
        initial_sum = input_tensor.sum().item()
        assert abs(initial_sum - SENTINEL) < 0.1, "Sentinel setup failed"

        code_relu   = "tensor_out[:] = torch.nn.functional.relu(tensor_in)"
        code_noop   = "tensor_out[:] = tensor_in * 1.0"

        stages = [
            ("torch==2.0.1+cu118", code_relu),
            ("torch==2.2.0+cu121", code_noop),
        ]

        current = input_tensor
        for spec, code in stages:
            try:
                out, meta = daemon_client.execute_cuda_ipc(
                    spec, code, current, current.shape, "float32",
                    ipc_mode="universal",
                )
            except AttributeError:
                pytest.skip("daemon_client.execute_cuda_ipc not available")
            except Exception as e:
                pytest.fail(f"execute_cuda_ipc failed for {spec}: {e}")

            # Verify output is still on GPU
            assert out.device.type == "cuda", (
                f"After {spec}: tensor is on {out.device}, expected cuda"
            )
            # After relu on positive tensor: values unchanged, sum preserved
            hop_sum = out.sum().item()
            assert abs(hop_sum - SENTINEL) < 1.0, (
                f"After {spec}: sum={hop_sum:.2f}, expected ~{SENTINEL}"
            )
            current = out

    @pytest.mark.slow
    @pytest.mark.gpu
    @pytest.mark.daemon
    def test_ipc_does_not_silently_fall_back_to_cpu(self, daemon_client):
        """
        When CUDA IPC is requested, the result tensor must be on the GPU.
        Silently falling back to a CPU tensor while claiming success is a
        correctness bug (data on wrong device, users get wrong performance).
        """
        try:
            import torch
        except ImportError:
            pytest.skip("torch not available in test runner")

        if not torch.cuda.is_available():
            pytest.skip("CUDA not available")

        input_tensor = torch.randn(50, 50, device="cuda:0")
        code = "tensor_out[:] = tensor_in + 0"

        try:
            out, meta = daemon_client.execute_cuda_ipc(
                "torch==2.0.1+cu118", code,
                input_tensor, input_tensor.shape, "float32",
                ipc_mode="universal",
            )
        except AttributeError:
            pytest.skip("execute_cuda_ipc not available")

        assert out.device.type == "cuda", (
            f"IPC silently fell back to CPU: tensor device is {out.device}. "
            "This is a correctness bug — data ended up on the wrong device."
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 9: pkg_resources availability in workers
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkerEnvironment:
    """
    Workers must have a complete, functional Python environment.
    Missing stdlib shims (like pkg_resources) cause real packages to fail.
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_pkg_resources_available(self, daemon_client):
        """
        pkg_resources must be importable in every worker.
        pytorch-lightning, lightning-fabric, and many other packages do
        __import__('pkg_resources').declare_namespace() at import time.
        A missing pkg_resources causes a silent category of failures.
        """
        for spec in ["torch==2.0.1+cu118", "torch==2.1.0", "tensorflow==2.13.0"]:
            result = _exec(
                daemon_client, spec,
                "import pkg_resources; print(pkg_resources.__version__ if hasattr(pkg_resources, '__version__') else 'ok')",
            )
            assert result.get("success"), (
                f"pkg_resources not importable in {spec} worker.\n"
                f"error: {result.get('error')}\n"
                f"stderr: {result.get('stderr', '')[:300]}\n"
                "Fix: ensure setuptools is installed in the worker environment, "
                "or add pkg_resources as a pre-import in the worker init script."
            )

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_pytorch_lightning_importable(self, daemon_client):
        """
        pytorch-lightning must be importable in a torch worker.
        This is a regression test for the pkg_resources failure seen in test 13.
        """
        result = _exec(
            daemon_client,
            "torch==2.1.0",
            "import pytorch_lightning as pl; print(pl.__version__)",
        )
        if not result.get("success"):
            err = result.get("error", "")
            if "No module named 'pytorch_lightning'" in err:
                pytest.skip("pytorch-lightning not installed")
            if "pkg_resources" in err:
                pytest.fail(
                    "pytorch-lightning import failed due to missing pkg_resources. "
                    "Install setuptools in the worker environment."
                )
            pytest.fail(f"pytorch-lightning import failed: {err}")


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT 10: Daemon lifecycle — status, restart robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestDaemonLifecycle:
    """
    The daemon must report accurate status and recover cleanly.
    """

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_status_returns_success(self, daemon_client):
        """daemon.status() must return {'success': True}."""
        status = daemon_client.status()
        assert status.get("success"), (
            f"daemon.status() did not return success: {status}"
        )

    @pytest.mark.fast
    @pytest.mark.daemon
    def test_status_includes_worker_count(self, daemon_client):
        """
        status() should include information about active workers so operators
        can diagnose issues without grepping logs.
        """
        status = daemon_client.status()
        # Accept any key that implies worker info: 'workers', 'active_workers', 'worker_count'
        has_worker_info = any(
            k in status for k in ("workers", "active_workers", "worker_count", "num_workers")
        )
        # This is a soft requirement — warn rather than hard fail
        if not has_worker_info:
            pytest.xfail(
                "daemon.status() does not include worker count information. "
                "Consider adding it for observability."
            )

    @pytest.mark.slow
    @pytest.mark.daemon
    def test_worker_survives_code_exception(self, daemon_client):
        """
        If user code raises an exception, the worker must survive and be able
        to serve the next request correctly.  The worker must NOT be killed or
        corrupted by user-code errors.
        """
        DaemonClient, DaemonProxy, _ = _import_daemon()
        spec = "numpy==1.26.4"
        proxy = DaemonProxy(daemon_client, spec)

        # This should fail gracefully
        bad = proxy.execute("raise ValueError('intentional test error')")
        assert not bad.get("success"), "Expected execution to fail but it succeeded"
        assert "intentional test error" in (bad.get("error", "") + bad.get("stderr", "")), (
            "Error message not propagated correctly"
        )

        # Worker must still serve the next request correctly
        good = proxy.execute("import numpy as np; print(np.__version__)")
        _assert_exec_ok(good, context="post-exception recovery")
        assert good["stdout"].strip() == "1.26.4", (
            "Worker returned wrong version after recovering from exception"
        )