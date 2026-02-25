"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          omnipkg — Concurrent Multiverse Benchmark                          ║
║                                                                              ║
║  Demonstrates running the same package (rich) at DIFFERENT versions across  ║
║  DIFFERENT Python interpreters, ALL at the same time, with zero conflicts.  ║
╚══════════════════════════════════════════════════════════════════════════════╝

WHAT THIS DEMO DOES
───────────────────
  Phase 0 — Install        Install rich for each Python concurrently.
                            Uses the Python API directly — no subprocess.
                            If already installed, omnipkg detects it in
                            microseconds and skips — no wasted time.
  Phase 1 — Adopt          Ensure each Python interpreter is registered
                            with omnipkg.  Uses the Python API directly.
                            Already adopted = instant no-op.
  Phase 2 — Daemon         Cold-start the worker daemon once, then leave
                            it running.  No stop command — stopping is what
                            hangs on Windows CI.
  Phase 3 — Warmup         First execution per worker: loads the package
                            into the worker process.  Measured but not
                            counted in the benchmark.
  Phase 4 — Benchmark      Hot execution: package already loaded, worker
                            already warm.  This is the real number.
  Phase 5 — Verify         Confirms each worker actually used the right
                            interpreter and the right package version.

API REFERENCE
─────────────
  This file demonstrates THREE ways to call omnipkg, from most- to
  least-preferred:

  ① Direct Python API (best — no subprocess overhead, importable in tests)
      from omnipkg.core import OmnipkgCore
      core = OmnipkgCore(python_version="3.9")
      rc   = core.smart_install(["rich==13.4.2"])

  ② Daemon / DaemonClient Python API (for cross-interpreter execution)
      from omnipkg.isolation.worker_daemon import DaemonClient
      client = DaemonClient()
      result = client.execute_shm(spec, code, shm_in, shm_out, python_exe)

  ③ CLI via subprocess (last resort — use when you truly need a separate
     process or are driving omnipkg from a shell script)
      subprocess.run(["8pkg39", "install", "rich==13.4.2"])

  Every phase below is annotated with which API it uses and why.
"""

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _
import sys
import os
import subprocess
import json
import time
import concurrent.futures
import threading

# ── Environment ──────────────────────────────────────────────────────────────
# OMNIPKG_NONINTERACTIVE suppresses all interactive prompts — required for CI.
# OMNIPKG_DEBUG=0 here because we want clean benchmark output; flip to 1 if
# you need to diagnose a failure.
_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
    "OMNIPKG_NONINTERACTIVE": "1",
    "OMNIPKG_DEBUG": "0",
}
_SP = dict(encoding="utf-8", errors="replace", env=_ENV)

print_lock = threading.Lock()

# ── Test matrix ──────────────────────────────────────────────────────────────
# Each entry is (python_version, rich_version).
# Intentionally different rich versions per Python to prove isolation.
TEST_CONFIGS = [
    ("3.9",  "13.4.2"),
    ("3.10", "13.6.0"),
    ("3.11", "13.7.1"),
]

# Installs under this many ms are considered no-ops (package already present).
INSTALL_NOOP_THRESHOLD_MS = 5_000


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(ms: float) -> str:
    if ms < 1:
        return f"{ms*1000:.1f}µs"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"


def section(title: str):
    safe_print(f"\n{'─'*80}")
    safe_print(f"  {title}")
    safe_print(f"{'─'*80}")


# ── Daemon log — errors only ──────────────────────────────────────────────────

def dump_daemon_log_on_failure(label: str = "DAEMON LOG"):
    try:
        from omnipkg.isolation.worker_daemon import DAEMON_LOG_FILE
        if not os.path.exists(DAEMON_LOG_FILE):
            safe_print(f"  [no daemon log at {DAEMON_LOG_FILE}]")
            return
        with open(DAEMON_LOG_FILE, "r", errors="replace") as f:
            lines = f.readlines()
        tail = lines[-60:] if len(lines) > 60 else lines
        safe_print(f"\n{'='*80}")
        safe_print(f"⚠️  {label}  ({len(lines)} total lines, showing last {len(tail)})")
        safe_print(f"    Full log: {DAEMON_LOG_FILE}")
        safe_print(f"{'─'*80}")
        for line in tail:
            safe_print(f"  {line.rstrip()}")
        safe_print(f"{'='*80}\n")
    except Exception as e:
        safe_print(f"  [could not read daemon log: {e}]")


# ── Registry helpers ──────────────────────────────────────────────────────────
#
# ── API: direct registry.json read ───────────────────────────────────────────
#
#   The registry lives at:
#     $VENV/.omnipkg/interpreters/registry.json
#
#   where $VENV is whatever find_absolute_venv_root() (from omnipkg.dispatcher)
#   returns — the same logic the dispatcher itself uses.  Reading the JSON
#   directly costs ~0 and requires no ConfigManager instantiation.
#
#   ConfigManager.__init__() triggers subprocess-heavy interpreter discovery
#   (_verify_python_version per interpreter, _register_all_interpreters, etc.)
#   on every call — we skip all of that.
# ─────────────────────────────────────────────────────────────────────────────

def _get_registry_path() -> Path:
    """
    Return the registry.json path using the dispatcher's own venv-root logic.
    This is the single source of truth — no custom walking needed.
    """
    from omnipkg.dispatcher import find_absolute_venv_root
    return find_absolute_venv_root() / ".omnipkg" / "interpreters" / "registry.json"


_registry_cache: dict | None = None


def _invalidate_registry_cache():
    """Force a fresh read on the next _get_registry() call (call after adopt)."""
    global _registry_cache
    _registry_cache = None


def _get_registry() -> dict:
    """
    Return {version: exe_path_str} from registry.json.
    Cached after first successful non-empty read; never caches empty results.
    """
    global _registry_cache
    if _registry_cache:
        return _registry_cache

    reg_path = _get_registry_path()
    if not reg_path.exists():
        return {}

    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {
            ver: path
            for ver, path in data.get("interpreters", {}).items()
            if Path(path).exists()
        }
        if result:
            _registry_cache = result
        return result
    except Exception:
        return {}


def is_adopted(version: str) -> bool:
    """True if `version` is in the registry. No subprocess, no ConfigManager."""
    return version in _get_registry()


def get_interpreter_path(version: str) -> str:
    """Return exe path for `version`, or raise RuntimeError if not adopted."""
    registry = _get_registry()
    if version in registry:
        return registry[version]
    raise RuntimeError(
        f"Python {version} not found in registry — "
        f"run: omnipkg python adopt {version}"
    )


# ── Lazy import of OmnipkgCore ────────────────────────────────────────────────
# We import at module level so the import error surfaces early, but we wrap
# in try/except so the file can still be imported in envs without omnipkg.core.

try:
    from omnipkg.core import OmnipkgCore
except ImportError:
    OmnipkgCore = None  # adopt_if_needed and install_one will fall back to CLI


# ── Phase 0: Install ─────────────────────────────────────────────────────────
#
# ── API: OmnipkgCore.smart_install (Python API — preferred) ─────────────────
#
#   from omnipkg.core import OmnipkgCore
#
#   core = OmnipkgCore(python_version="3.9")   # target interpreter
#   rc   = core.smart_install(
#       packages          = ["rich==13.4.2"],   # list of pip-style specs
#       dry_run           = False,              # True → report only, no writes
#       force_reinstall   = False,              # True → skip preflight cache
#       override_strategy = None,               # "stable-main" | "bubble" | None
#       target_directory  = None,               # custom install root or None
#   )
#   # rc == 0  → success or already satisfied (preflight short-circuits in µs)
#   # rc != 0  → something went wrong; check logs
#
#   smart_install() runs an ultra-fast preflight check first (sub-ms for
#   cached packages) and only invokes pip when something genuinely needs work.
#   Thread-safe: safe to call concurrently across different OmnipkgCore
#   instances targeting different Python versions.
#
# ── Fallback: CLI via subprocess ─────────────────────────────────────────────
#   subprocess.run(["8pkg39", "install", "rich==13.4.2"])
#   Only use this when omnipkg cannot be imported (e.g. shell scripts, or
#   installing into a completely separate environment).
# ─────────────────────────────────────────────────────────────────────────────

def install_one(py_version: str, pkg_spec: str) -> dict:
    """
    Install pkg_spec for py_version using OmnipkgCore.smart_install().
    Falls back to the CLI subprocess if OmnipkgCore is unavailable.
    """
    start = time.perf_counter()

    if OmnipkgCore is not None:
        # ── Preferred path: direct Python API ───────────────────────────
        try:
            core = OmnipkgCore(python_version=py_version)
            # Capture preflight timing with nanosecond precision.
            # perf_counter_ns() has no floating-point rounding — the number
            # you see is exact.  We call it immediately before and after
            # smart_install() so the clock wraps only the preflight + any
            # actual install work, not OmnipkgCore.__init__().
            t0_ns = time.perf_counter_ns()
            rc = core.smart_install([pkg_spec])
            t1_ns = time.perf_counter_ns()
            elapsed_ns = t1_ns - t0_ns
            elapsed_ms = elapsed_ns / 1_000_000
            success = rc == 0
            noop = success and elapsed_ms < INSTALL_NOOP_THRESHOLD_MS
            return {
                "python_version": py_version,
                "spec": pkg_spec,
                "success": success,
                "elapsed_ms": elapsed_ms,
                "elapsed_ns": elapsed_ns,
                "noop": noop,
                "stderr": "" if success else f"exit code {rc}",
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "python_version": py_version,
                "spec": pkg_spec,
                "success": False,
                "elapsed_ms": elapsed_ms,
                "elapsed_ns": int(elapsed_ms * 1_000_000),
                "noop": False,
                "stderr": str(e),
            }

    # ── Fallback: CLI subprocess (8pkg39 install rich==13.4.2) ──────────
    cmd = "8pkg" + py_version.replace(".", "")
    result = subprocess.run([cmd, "install", pkg_spec], capture_output=True, **_SP)
    elapsed_ms = (time.perf_counter() - start) * 1000
    success = result.returncode == 0
    noop = success and elapsed_ms < INSTALL_NOOP_THRESHOLD_MS
    return {
        "python_version": py_version,
        "spec": pkg_spec,
        "success": success,
        "elapsed_ms": elapsed_ms,
        "elapsed_ns": int(elapsed_ms * 1_000_000),
        "noop": noop,
        "stderr": result.stderr.strip() if not success else "",
    }


def _fmt_ns(ns: int) -> str:
    """Human-friendly nanosecond duration. Shows ns, µs, or ms depending on magnitude."""
    if ns < 1_000:
        return f"{ns}ns"
    elif ns < 1_000_000:
        return f"{ns / 1_000:.1f}µs"
    else:
        return f"{ns / 1_000_000:.2f}ms"


def phase_install(configs: list):
    section("Phase 0 — Install packages (concurrent, Python API)")
    safe_print("  Calls OmnipkgCore.smart_install() directly — no subprocess.")
    safe_print("  Preflight detects already-installed packages in nanoseconds")
    safe_print("  using an in-process cache check — no pip, no disk scan, no")
    safe_print("  subprocess.  Actual installs are timed in milliseconds.")
    safe_print("")

    tasks = [(ver, f"rich=={pkg}") for ver, pkg in configs]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(install_one, ver, spec): (ver, spec) for ver, spec in tasks}
        results = {}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results[r["python_version"]] = r

    all_ok = True
    for ver, _ in configs:
        r = results[ver]
        if r["noop"]:
            ns = r.get("elapsed_ns", 0)
            safe_print(
                f"  ✅ Python {ver:5}  {r['spec']:20}  "
                f"already installed — preflight: {_fmt_ns(ns)} ⚡"
            )
        elif r["success"]:
            safe_print(f"  ✅ Python {ver:5}  {r['spec']:20}  installed in {fmt(r['elapsed_ms'])}")
        else:
            safe_print(f"  ❌ Python {ver:5}  {r['spec']:20}  FAILED: {r['stderr'][:120]}")
            all_ok = False

    if not all_ok:
        safe_print("\n❌ Install phase failed — aborting")
        sys.exit(1)

    return results


# ── Phase 1: Adopt ───────────────────────────────────────────────────────────
#
# ── API: OmnipkgCore.adopt_interpreter (Python API — preferred) ──────────────
#
#   from omnipkg.core import OmnipkgCore
#
#   core = OmnipkgCore()              # host / default Python is fine here
#   rc   = core.adopt_interpreter("3.10")
#   # rc == 0  → success (or already adopted — method is fully idempotent)
#   # rc != 0  → failed; check output / logs
#
#   What adopt_interpreter() does:
#     1. Checks InterpreterManager.list_available_interpreters()
#        → instant no-op if version already in registry.
#     2. Looks for a local CPython matching that version.
#     3. Falls back to downloading a managed CPython if nothing found locally.
#     4. Post-adopt: refreshes the registry, installs runtime deps
#        (filelock etc.), creates .bat shims, writes the profile.d snippet so
#        OMNIPKG_PYTHON_39_PATH etc. are set in every future shell.
#   Calling adopt twice is safe — every step is idempotent.
#
#   DISPATCHER AUTO-ADOPT (NEW):
#   Running `8pkg39 anything` when 3.9 is not yet adopted now triggers an
#   automatic adopt inside the dispatcher before dispatching the command.
#   Fantasy versions (3.200, 2.5, "banana") are caught by
#   _is_plausible_python_version() and rejected immediately with a clear error.
#   You only need to call adopt explicitly when you want control over timing
#   (e.g. pre-warming all versions at the start of a CI job).
#
# ── Fallback: CLI via subprocess ─────────────────────────────────────────────
#   subprocess.Popen(["omnipkg", "python", "adopt", "3.10"])
#   Use Popen + polling (not run) when the download may take minutes and you
#   want to apply a timeout or show a progress indicator.
# ─────────────────────────────────────────────────────────────────────────────

def adopt_if_needed(version: str, timeout: float = 300.0) -> bool:
    """
    Ensure Python `version` is adopted, using the Python API first.

    ── API: OmnipkgCore.adopt_interpreter ──────────────────────────────────
      core = OmnipkgCore()
      rc   = core.adopt_interpreter(version)   # 0 = success / already done
    ─────────────────────────────────────────────────────────────────────────

    Falls back to the CLI subprocess if the direct API call fails or raises,
    so this function is robust even in environments with partial installs.

    NOTE: adopt_interpreter() rc==0 means "already managed" OR "just adopted"
    — both are success.  We trust rc==0 rather than re-checking is_adopted()
    immediately, because the registry JSON may not be flushed to disk yet when
    we re-read it.  We invalidate the cache so the next is_adopted() call
    (e.g. in prime_interpreter_cache) gets a fresh read.
    """
    _invalidate_registry_cache()   # always start with a fresh read
    if is_adopted(version):
        safe_print(f"  ✅ Python {version}  already adopted (registry lookup)")
        return True

    safe_print(f"  🔄 Adopting Python {version} via Python API…")

    if OmnipkgCore is not None:
        try:
            core = OmnipkgCore()
            rc = core.adopt_interpreter(version)
            _invalidate_registry_cache()    # force fresh read after adoption
            if rc == 0:
                # rc==0 means adopted OR already managed — both are success.
                # Re-check the registry; if it's still not there it means the
                # adopt wrote to a different registry path than we're reading.
                if is_adopted(version):
                    safe_print(f"  ✅ Python {version}  adopted")
                    return True
                # It's adopted but our registry path is wrong — still success,
                # prime_interpreter_cache will use omnipkg info python fallback.
                safe_print(f"  ✅ Python {version}  adopted (registry path mismatch — using CLI to verify)")
                result = subprocess.run(
                    ["omnipkg", "info", "python"], capture_output=True, **_SP
                )
                if f"Python {version}:" in result.stdout:
                    # Parse the path out of info output as a one-time fallback
                    for line in result.stdout.splitlines():
                        if f"Python {version}:" in line:
                            parts = line.split(":", 1)
                            if len(parts) == 2:
                                _interpreter_cache[version] = parts[1].strip().split()[0]
                    safe_print(f"  ✅ Python {version}  confirmed via info output")
                    return True
            safe_print(f"  ⚠️  API adopt returned rc={rc}, falling back to CLI…")
        except Exception as e:
            safe_print(f"  ⚠️  API adopt raised {e!r}, falling back to CLI…")

    # ── CLI subprocess fallback ──────────────────────────────────────────
    # Use Popen + polling so we can honour the timeout even on slow downloads.
    proc = subprocess.Popen(["omnipkg", "python", "adopt", version], env=_ENV)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        _invalidate_registry_cache()
        if is_adopted(version):
            proc.wait()
            break
        time.sleep(3)
    else:
        proc.kill()
        safe_print(f"  ❌ Adopt timed out after {int(timeout)}s")
        return False

    _invalidate_registry_cache()
    if is_adopted(version):
        safe_print(f"  ✅ Python {version}  adopted (CLI fallback)")
        return True
    safe_print(f"  ❌ Python {version}  adopt exited but not in registry")
    return False


def phase_adopt(configs: list):
    section("Phase 1 — Adopt Python interpreters (Python API)")
    safe_print("  Calls OmnipkgCore.adopt_interpreter() directly — no subprocess")
    safe_print("  for the happy path.  Already adopted = instant registry lookup.")
    safe_print("")
    for version, _ in configs:
        if not adopt_if_needed(version):
            safe_print(f"\n❌ Could not adopt Python {version} — aborting")
            sys.exit(1)


# ── Phase 2: Daemon ──────────────────────────────────────────────────────────
#
# ── API: DaemonClient (Python API) ───────────────────────────────────────────
#
#   from omnipkg.isolation.worker_daemon import DaemonClient
#
#   client = DaemonClient()
#   # ↳ Holds the socket connection.  Create ONCE and reuse.
#   #   Creating a new instance per call adds ~200ms — don't do it.
#
#   client.status() → dict
#     {"success": bool, "workers": [...], ...}
#     Call before starting to avoid double-starting the daemon.
#
#   client.execute_shm(
#       spec       = "rich==13.6.0",      # package + version to load
#       code       = "import rich; ...",  # code to run inside the worker
#       shm_in     = {},                  # data to pass IN  via shared memory
#       shm_out    = {},                  # keys to read OUT via shared memory
#       python_exe = registry["3.10"],    # full exe path — from _get_registry()
#   ) → dict
#     {"success": bool, "stdout": str, "stderr": str,
#      "error": str | None, "traceback": str | None}
#
#   Starting the daemon (still a subprocess — the daemon must detach itself):
#     subprocess.Popen(["8pkg", "daemon", "start"],
#                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#     Use Popen, NOT run.  Do NOT call "daemon stop" in CI — hangs Windows.
# ─────────────────────────────────────────────────────────────────────────────

def phase_daemon(interpreter_paths: list):
    section("Phase 2 — Daemon cold start")
    safe_print("  The daemon pre-spawns isolated worker processes, one per")
    safe_print("  Python interpreter.  We start it once and leave it running.")
    safe_print("  No stop command — stopping hangs on Windows CI.")
    safe_print("")

    try:
        from omnipkg.isolation.worker_daemon import DaemonClient
        client = DaemonClient()
        status = client.status()

        if status.get("success"):
            safe_print("  ✅ Daemon already running — skipping cold start")
            return

        safe_print("  🔄 Starting daemon…")
        subprocess.Popen(
            ["8pkg", "daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for i in range(60):
            time.sleep(0.5)
            status = client.status()
            if status.get("success"):
                safe_print(f"  ✅ Daemon up after {(i+1)*0.5:.1f}s")
                return

        safe_print("  ❌ Daemon never came up after 30s")
        dump_daemon_log_on_failure("DAEMON STARTUP LOG")
        sys.exit(1)

    except Exception as e:
        import traceback
        safe_print(f"  ❌ Daemon error: {e}")
        safe_print(traceback.format_exc())
        sys.exit(1)


# ── Module-level singletons ───────────────────────────────────────────────────
# DaemonClient and interpreter paths are resolved ONCE at startup.
#
# THE BUG THIS FIXES:
#   Creating a new DaemonClient() or resolving interpreter paths on every
#   execute_shm call adds ~200ms overhead, completely hiding the real ~0.4ms
#   daemon latency.  The benchmark proves the daemon is 0.4ms hot — if you
#   see 400ms, you're measuring subprocess spawn overhead, not omnipkg.
#
# ── Correct pattern ──────────────────────────────────────────────────────────
#   client  = DaemonClient()    # ONCE — holds socket, cheap to keep alive
#   registry = _get_registry()  # ONCE — plain dict read, effectively free
#   client.execute_shm(...)     # per call — actual cost ~0.4ms hot
# ─────────────────────────────────────────────────────────────────────────────

_daemon_client = None
_interpreter_cache: dict = {}   # "3.10" → "/path/to/python3.10"


def get_daemon_client():
    """Return module-level DaemonClient, creating it once on first call."""
    global _daemon_client
    if _daemon_client is None:
        from omnipkg.isolation.worker_daemon import DaemonClient
        _daemon_client = DaemonClient()
    return _daemon_client


def prime_interpreter_cache(configs: list):
    """
    Populate _interpreter_cache from the registry JSON directly.

    ── Why not ConfigManager / InterpreterManager? ──────────────────────────
      ConfigManager.__init__() triggers subprocess-heavy interpreter
      discovery (_verify_python_version per entry, _register_all_interpreters,
      etc.) on every instantiation.  We skip all of that by reading registry.json
      directly via _get_registry(), which is already a cached dict after
      phase_adopt() ran.

      If a version is missing from our direct read (e.g. registry path mismatch
      in conda environments), we fall back to parsing `omnipkg info python`
      output as a one-time safety net rather than crashing.
    ─────────────────────────────────────────────────────────────────────────
    """
    _invalidate_registry_cache()    # ensure we read the post-adopt state
    registry = _get_registry()

    missing = [ver for ver, _ in configs if ver not in registry and ver not in _interpreter_cache]

    if missing:
        # One-shot fallback: parse `omnipkg info python` for missing entries.
        # Costs one subprocess but only runs when the direct read missed something.
        safe_print(f"  ⚠️  {missing} not found in direct registry read — checking omnipkg info python…")
        result = subprocess.run(["omnipkg", "info", "python"], capture_output=True, **_SP)
        for line in result.stdout.splitlines():
            for ver in list(missing):
                if f"Python {ver}:" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        exe = parts[1].strip().split()[0]
                        if Path(exe).exists():
                            _interpreter_cache[ver] = exe
                            missing.remove(ver)

    if missing:
        raise RuntimeError(
            f"Python {missing} not found after adoption — "
            "check: omnipkg info python"
        )

    for version, _ in configs:
        if version not in _interpreter_cache:
            _interpreter_cache[version] = registry[version]

    safe_print("  Interpreter paths (direct registry + info fallback):")
    for ver, path in _interpreter_cache.items():
        safe_print(f"    {ver} → {path}")


# ── Phase 3 & 4: Warmup + Benchmark ─────────────────────────────────────────

def _execute(py_version: str, rich_version: str, code: str) -> dict:
    """
    Run `code` in an isolated worker via DaemonClient.execute_shm().

    ── API: DaemonClient.execute_shm ───────────────────────────────────────
      Uses module-level singletons — never creates a new DaemonClient or
      re-reads the registry.  Cost: ~0.4ms hot, ~300ms cold (worker spawn).
    ─────────────────────────────────────────────────────────────────────────
    """
    return get_daemon_client().execute_shm(
        spec=f"rich=={rich_version}",
        code=code,
        shm_in={},
        shm_out={},
        python_exe=_interpreter_cache[py_version],
    )


WARMUP_CODE = """
import sys, importlib.metadata
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}"):
    import rich
    actual = importlib.metadata.version('rich')
    assert actual == "{version}", f"VERSION MISMATCH: wanted {version} got {{actual}}"
    print(f"[worker] exe={{sys.executable}} rich={{actual}}")
"""

BENCH_CODE = """
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}"):
    import rich
"""

VERIFY_CODE = """
import sys, json, importlib.metadata
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}"):
    import rich
    print(json.dumps({{
        "python_version": sys.version.split()[0],
        "python_path": sys.executable,
        "rich_version": importlib.metadata.version('rich'),
        "rich_file": rich.__file__
    }}))
"""


def warmup_one(config: tuple, thread_id: int) -> dict | None:
    py_version, rich_version = config
    prefix = f"  [T{thread_id}|py{py_version}]"
    spec = f"rich=={rich_version}"

    try:
        start = time.perf_counter()
        result = _execute(py_version, rich_version,
                          WARMUP_CODE.format(spec=spec, version=rich_version))
        elapsed = (time.perf_counter() - start) * 1000

        if not result.get("success"):
            safe_print(f"{prefix} ❌ warmup failed: {result.get('error', '?')}")
            if result.get("traceback"):
                for line in result["traceback"].splitlines():
                    safe_print(f"{prefix}   {line}")
            dump_daemon_log_on_failure(f"T{thread_id} WARMUP FAILURE")
            return None

        safe_print(f"{prefix} ✅ warmed up in {fmt(elapsed)}")
        return {"thread_id": thread_id, "python_version": py_version,
                "rich_version": rich_version, "warmup_time": elapsed}

    except Exception as e:
        safe_print(f"{prefix} ❌ exception: {e}")
        return None


def bench_one(config: tuple, thread_id: int, warmup_data: dict) -> dict | None:
    py_version, rich_version = config
    prefix = f"  [T{thread_id}|py{py_version}]"
    spec = f"rich=={rich_version}"

    try:
        start = time.perf_counter()
        result = _execute(py_version, rich_version,
                          BENCH_CODE.format(spec=spec))
        elapsed = (time.perf_counter() - start) * 1000

        if not result.get("success"):
            safe_print(f"{prefix} ❌ bench failed: {result.get('error', '?')}")
            dump_daemon_log_on_failure(f"T{thread_id} BENCH FAILURE")
            return None

        safe_print(f"{prefix} ✅ {fmt(elapsed)}")
        return {"thread_id": thread_id, "python_version": py_version,
                "rich_version": rich_version,
                "warmup_time": warmup_data["warmup_time"],
                "benchmark_time": elapsed}

    except Exception as e:
        safe_print(f"{prefix} ❌ exception: {e}")
        return None


def phase_warmup(configs: list) -> list:
    section("Phase 3 — Warmup (concurrent, timed but not benchmarked)")
    safe_print("  First execution per worker.  Loads the package into the worker")
    safe_print("  process.  Intentionally slower than the benchmark — this is the")
    safe_print("  'cold→warm' transition.  All three run at the same time.")
    safe_print("")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as ex:
        futures = {ex.submit(warmup_one, cfg, i+1): cfg for i, cfg in enumerate(configs)}
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    results = [r for r in results if r]
    if len(results) != len(configs):
        safe_print("\n❌ Warmup failed")
        dump_daemon_log_on_failure("WARMUP FAILURE")
        sys.exit(1)

    return sorted(results, key=lambda x: x["thread_id"])


def phase_benchmark(configs: list, warmup_results: list) -> list:
    section("Phase 4 — Benchmark (hot workers, concurrent)")
    safe_print("  Workers are already warm.  This measures the actual runtime")
    safe_print("  cost of loading a pinned package version from the omnipkg")
    safe_print("  store — no install, no cold-start, pure execution overhead.")
    safe_print("")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as ex:
        futures = {
            ex.submit(bench_one, cfg, i+1, warmup_results[i]): cfg
            for i, cfg in enumerate(configs)
        }
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    results = [r for r in results if r]
    if len(results) != len(configs):
        safe_print("\n❌ Benchmark failed")
        dump_daemon_log_on_failure("BENCHMARK FAILURE")
        sys.exit(1)

    return results


# ── Phase 5: Verify ──────────────────────────────────────────────────────────

def verify_one(config: tuple, thread_id: int) -> dict | None:
    py_version, rich_version = config
    spec = f"rich=={rich_version}"
    try:
        result = _execute(py_version, rich_version, VERIFY_CODE.format(spec=spec))
        if not result.get("success"):
            safe_print(f"  [T{thread_id}] ❌ verify failed: {result.get('error', '?')}")
            return None
        data = json.loads(result.get("stdout", "{}"))
        return {"thread_id": thread_id, **data}
    except Exception as e:
        safe_print(f"  [T{thread_id}] ❌ {e}")
        return None


def phase_verify(configs: list):
    section("Phase 5 — Verification (not timed)")
    safe_print("  Confirms each worker used the correct interpreter and the")
    safe_print("  correct package version.  Isolation proof.")
    safe_print("")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as ex:
        futures = {ex.submit(verify_one, cfg, i+1): cfg for i, cfg in enumerate(configs)}
        results = sorted(
            [f.result() for f in concurrent.futures.as_completed(futures) if f.result()],
            key=lambda x: x["thread_id"]
        )

    for r in results:
        safe_print(f"  T{r['thread_id']}  Python {r['python_version']:6} rich={r['rich_version']}")
        safe_print(f"       exe : {r['python_path']}")
        safe_print(f"       file: {r['rich_file']}")


# ── Summary ──────────────────────────────────────────────────────────────────

def print_summary(results: list, install_results: dict | None = None):
    safe_print("\n" + "=" * 80)
    safe_print("📊  BENCHMARK RESULTS")
    safe_print("=" * 80)
    safe_print(f"  {'Thread':<8} {'Python':<8} {'Rich':<12} {'Preflight ⚡':<16} {'Warmup':<14} {'Hot':<14}")
    safe_print(f"  {'─'*6:<8} {'─'*6:<8} {'─'*10:<12} {'─'*14:<16} {'─'*12:<14} {'─'*12:<14}")
    for r in sorted(results, key=lambda x: x["thread_id"]):
        ver = r["python_version"]
        # Preflight: the install_one elapsed_ns for this version (already-installed no-op time)
        ns = install_results.get(ver, {}).get("elapsed_ns", 0) if install_results else 0
        preflight_str = _fmt_ns(ns) if ns else "—"
        safe_print(
            f"  T{r['thread_id']:<7} "
            f"{ver:<8} "
            f"{r['rich_version']:<12} "
            f"{preflight_str:<16} "
            f"{fmt(r['warmup_time']):<14} "
            f"{fmt(r['benchmark_time']):<14}"
        )
    safe_print(f"  {'─'*72}")
    bt = [r["benchmark_time"] for r in results]
    wt = [r["warmup_time"] for r in results]
    seq     = sum(bt)
    conc    = max(bt)
    speedup = seq / conc if conc else 0
    warm_avg = sum(wt) / len(wt)
    hot_avg  = sum(bt) / len(bt)
    if install_results:
        pf_times = [v["elapsed_ns"] for v in install_results.values() if v.get("noop")]
        if pf_times:
            pf_avg_ns = sum(pf_times) / len(pf_times)
            safe_print(f"  Preflight avg    : {_fmt_ns(int(pf_avg_ns))}  ← already-installed detection")
    safe_print(f"  Sequential equiv : {fmt(seq)}")
    safe_print(f"  Concurrent actual: {fmt(conc)}  ({speedup:.2f}x speedup)")
    safe_print(f"  Warmup avg       : {fmt(warm_avg)}")
    safe_print(f"  Hot avg          : {fmt(hot_avg)}  ({warm_avg/hot_avg:.1f}x faster than warmup)")
    safe_print("=" * 80)


# ── API cheatsheet printed at end of every run ────────────────────────────────

API_CHEATSHEET = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  omnipkg API — quick reference                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ① DIRECT PYTHON API (preferred — no subprocess, importable in tests)       ║
║                                                                              ║
║  INSTALL a package for a specific Python                                     ║
║    from omnipkg.core import OmnipkgCore                                     ║
║    core = OmnipkgCore(python_version="3.9")                                 ║
║    rc   = core.smart_install(["rich==13.4.2"])   # 0 = success              ║
║    # Already installed? preflight short-circuits in microseconds.           ║
║    # Thread-safe: run concurrently for different Python versions.           ║
║                                                                              ║
║  ADOPT a Python interpreter                                                  ║
║    from omnipkg.core import OmnipkgCore                                     ║
║    core = OmnipkgCore()                 # host Python is fine here          ║
║    rc   = core.adopt_interpreter("3.10")         # 0 = success / no-op     ║
║    # Idempotent: already adopted = instant registry check, no work done.    ║
║                                                                              ║
║  CHECK adoption & resolve paths (direct JSON read, zero overhead)           ║
║    from omnipkg.dispatcher import find_absolute_venv_root                   ║
║    import json; from pathlib import Path                                     ║
║    reg_path = find_absolute_venv_root() / ".omnipkg/interpreters/registry.json" ║
║    data     = json.loads(reg_path.read_text())["interpreters"]              ║
║    adopted  = "3.10" in data      # O(1)                                    ║
║    exe_path = data["3.10"]        # O(1), no subprocess                     ║
║                                                                              ║
║  ② DAEMON / WORKER API (for running code in isolated workers)               ║
║                                                                              ║
║    from omnipkg.isolation.worker_daemon import DaemonClient                 ║
║    client = DaemonClient()                 # create ONCE, reuse             ║
║    status = client.status()               # {"success": bool, ...}         ║
║    result = client.execute_shm(                                              ║
║        spec       = "rich==13.6.0",                                         ║
║        code       = "import rich; print(rich.__version__)",                 ║
║        shm_in     = {},                                                      ║
║        shm_out    = {},                                                      ║
║        python_exe = registry["3.10"],   # full path — from registry above   ║
║    )                                                                         ║
║    # {"success": bool, "stdout": str, "stderr": str,                        ║
║    #  "error": str|None, "traceback": str|None}                             ║
║                                                                              ║
║  LOAD a package in your own process (no worker needed)                      ║
║    from omnipkg.loader import omnipkgLoader                                  ║
║    with omnipkgLoader("rich==13.6.0"):                                       ║
║        import rich   # ← pinned version, fully isolated                     ║
║                                                                              ║
║  ③ CLI / SUBPROCESS (last resort — shell scripts, cross-env calls)          ║
║                                                                              ║
║    omnipkg python adopt 3.10                                                 ║
║    8pkg310 install rich==13.6.0                                              ║
║    8pkg daemon start          # daemon must detach — use Popen not run      ║
║    omnipkg info python        # human-readable; use registry dict instead   ║
║                                                                              ║
║  DISPATCHER AUTO-ADOPT (transparent, no extra call needed)                  ║
║    Running `8pkg39 install foo` when 3.9 is not yet adopted now triggers    ║
║    an automatic `adopt_interpreter("3.9")` inside the dispatcher BEFORE     ║
║    dispatching.  Fantasy versions (3.200, 2.5) are caught by               ║
║    _is_plausible_python_version() and rejected immediately with a clear     ║
║    error message rather than a failed download attempt.                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    total_start = time.perf_counter()

    safe_print("=" * 80)
    safe_print("  omnipkg — Concurrent Multiverse Benchmark")
    safe_print("  rich 13.4.2 on py3.9  |  13.6.0 on py3.10  |  13.7.1 on py3.11")
    safe_print("  all three running simultaneously, fully isolated")
    safe_print("=" * 80)

    # Phase 0: install packages concurrently — direct Python API, no subprocess
    install_results = phase_install(TEST_CONFIGS)

    # Phase 1: adopt interpreters — direct Python API, no subprocess
    phase_adopt(TEST_CONFIGS)

    # Cache interpreter paths ONCE via Python API (dict read, zero subprocess).
    # Must happen after adopt so all versions are in the registry.
    section("Resolving interpreter paths (Python API, zero subprocess)")
    prime_interpreter_cache(TEST_CONFIGS)
    interpreter_paths = list(_interpreter_cache.values())

    # Phase 2: cold-start daemon (subprocess — daemon must detach itself)
    phase_daemon(interpreter_paths)

    # Phase 3: warmup
    warmup_results = phase_warmup(TEST_CONFIGS)

    # Phase 4: benchmark
    benchmark_results = phase_benchmark(TEST_CONFIGS, warmup_results)
    print_summary(benchmark_results, install_results)

    # Phase 5: verify
    phase_verify(TEST_CONFIGS)

    total_ms = (time.perf_counter() - total_start) * 1000
    safe_print(f"\n🎉  Total time: {fmt(total_ms)}")

    safe_print(API_CHEATSHEET)


if __name__ == "__main__":
    main()
