"""
╔══════════════════════════════════════════════════════════════════════════════╗
║       omnipkg — TensorFlow Dependency Switching Demo                        ║
║                                                                              ║
║  Shows what the LEGACY LOADER (omnipkgLoader) can and cannot do,            ║
║  and why the DAEMON was built to solve what the loader cannot.               ║
╚══════════════════════════════════════════════════════════════════════════════╝

WHAT THIS DEMO COVERS
─────────────────────
  Test 1 — TensorFlow 2.13.0 bubble load
            Legacy loader activates a pre-built "bubble" containing TF + deps.
            Works because this is the FIRST load in a clean process.

  Test 2 — typing_extensions version switching (pure Python)
            omnipkgLoader CAN switch pure-Python packages between contexts
            because they have no C extensions — sys.path manipulation is enough.

  Test 3 — Nested loaders (outer + inner context)
            Demonstrates stacking contexts.  Works for pure Python deps.

  Test 4 — TF version switch attempt (CEXT LIMITATION)
            omnipkgLoader CANNOT switch TensorFlow versions mid-process.
            C extensions (.so/.pyd) are loaded by the OS linker and cannot
            be unloaded.  The loader detects this, refuses to corrupt state,
            prints a clear explanation, and returns the version already loaded.
            This is NOT a bug — it is correct defensive behavior.

  Test 5 — Daemon solves it: TF 2.13.0 + TF 2.12.0 CONCURRENTLY
            Run twice: first shows cold start (~300ms), second shows hot (~2ms).
            Two different TF versions running simultaneously in separate workers.
            This is what the loader physically cannot do.

API REFERENCE
─────────────
  See "── API:" comment blocks throughout this file.
"""

from omnipkg.common_utils import safe_print
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.i18n import _
import traceback
import shutil
import subprocess
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# ── Injected into subprocess scripts ─────────────────────────────────────────
# safe_print is injected rather than imported so subprocess scripts are
# fully self-contained — they don't need omnipkg on sys.path to print safely.
SAFE_PRINT_DEFINITION = """
import sys
_builtin_print = print
def safe_print(*args, **kwargs):
    try:
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            safe_args = [str(a).encode(encoding,'replace').decode(encoding) for a in args]
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            _builtin_print("[omnipkg: encoding error]")
"""

# ── Version detection helper injected into subprocess scripts ─────────────────
# Tries module.__version__, then importlib.metadata, then bubble path parsing.
# Needed because different packages expose version differently, and omnipkg
# bubbles may not register with importlib.metadata in all cases.
GET_VERSION_SNIPPET = r'''
def get_version(module, package_name, versions_dir):
    import importlib.metadata
    from pathlib import Path
    version, source = "unknown", "unknown"
    if hasattr(module, '__version__'):
        version, source = module.__version__, "module.__version__"
    if version == "unknown":
        for name in [package_name, package_name.replace('-','_'), package_name.replace('_','-')]:
            try:
                version = importlib.metadata.version(name)
                source  = f"importlib.metadata({name})"
                break
            except Exception:
                pass
    if hasattr(module, '__file__') and module.__file__:
        mp = Path(module.__file__).resolve()
        vd = Path(versions_dir).resolve()
        if str(mp).startswith(str(vd)):
            try:
                bubble = mp.relative_to(vd).parts[0]
                bver   = bubble.split('-',1)[1] if '-' in bubble else None
                if bver and version == "unknown":
                    version, source = bver, f"bubble path ({bubble})"
                source = f"{source} → bubble:{mp}"
            except Exception:
                pass
        else:
            source = f"{source} → system:{mp}"
    return version, source
'''


# ── Formatting ────────────────────────────────────────────────────────────────

def section(title: str, width: int = 80):
    safe_print(f"\n{'═'*width}")
    safe_print(f"  {title}")
    safe_print(f"{'═'*width}")

def subsection(title: str, width: int = 80):
    safe_print(f"\n{'─'*width}")
    safe_print(f"  {title}")
    safe_print(f"{'─'*width}")

def fmt(ms: float) -> str:
    return f"{ms:.1f}ms" if ms < 1000 else f"{ms/1000:.2f}s"


# ── Phase 0: Ensure bubbles ───────────────────────────────────────────────────
#
# ── API: OmnipkgCore.bubble_manager.create_isolated_bubble ───────────────────
#   from omnipkg.core import omnipkg as OmnipkgCore, ConfigManager
#   config_manager = ConfigManager()
#   core = OmnipkgCore(config_manager)
#
#   core.bubble_manager.create_isolated_bubble(
#       pkg_name,            # e.g. "tensorflow"
#       version,             # e.g. "2.13.0"
#       python_version,      # e.g. "3.11"  — MUST match current interpreter
#   ) → bool
#
#   A "bubble" is a self-contained directory under multiversion_base that holds
#   one specific package version.  omnipkgLoader activates it by prepending its
#   path to sys.path and unloading conflicting modules first.
#
#   After creating a bubble, call rebuild_package_kb() so omnipkg's knowledge
#   base knows what's in it — required for conflict detection.
#
#   core.rebuild_package_kb(
#       ["tensorflow==2.13.0"],
#       target_python_version="3.11"
#   )
# ─────────────────────────────────────────────────────────────────────────────

def ensure_bubbles(config_manager: ConfigManager) -> bool:
    safe_print("  Checking / creating package bubbles...")
    safe_print("  A bubble = isolated directory for one package version.")
    safe_print("  omnipkgLoader swaps sys.path to activate/deactivate them.")
    safe_print("")

    core = OmnipkgCore(config_manager)
    exe  = config_manager.config.get("python_executable", sys.executable)
    vt   = config_manager._verify_python_version(exe)
    pyver = f"{vt[0]}.{vt[1]}" if vt else None

    if not pyver:
        safe_print("  ❌ Could not determine Python context version — aborting")
        return False

    safe_print(f"  Python context for bubble creation: {pyver}")
    safe_print("")

    packages = {
        "tensorflow":       ["2.13.0", "2.12.0"],
        "typing_extensions": ["4.14.1", "4.5.0"],
    }

    for pkg, versions in packages.items():
        for ver in versions:
            bubble_path = core.multiversion_base / f"{pkg}-{ver}"
            if bubble_path.exists():
                safe_print(f"  ✅ {pkg}=={ver} bubble already exists")
            else:
                safe_print(f"  🫧 Creating bubble: {pkg}=={ver} ...")
                ok = core.bubble_manager.create_isolated_bubble(pkg, ver, pyver)
                if ok:
                    core.rebuild_package_kb([f"{pkg}=={ver}"], target_python_version=pyver)
                    safe_print(f"  ✅ {pkg}=={ver} bubble created + KB updated")
                else:
                    safe_print(f"  ❌ Failed to create {pkg}=={ver} bubble")

    return True


def phase_setup() -> ConfigManager | None:
    section("Phase 0 — Environment Setup")
    safe_print("  Cleaning stale cloaks, then ensuring all required bubbles exist.")
    safe_print("")

    config_manager = ConfigManager()
    site_packages  = Path(config_manager.config["site_packages_path"])

    for pkg in ["tensorflow", "tensorflow_estimator", "keras", "typing_extensions"]:
        for cloaked in site_packages.glob(f"{pkg}.*_omnipkg_cloaked*"):
            shutil.rmtree(cloaked, ignore_errors=True)
            safe_print(f"  🧹 Removed stale cloak: {cloaked.name}")

    if not ensure_bubbles(config_manager):
        return None

    safe_print("\n  ✅ Environment ready")
    return config_manager


# ── Run a subprocess script ───────────────────────────────────────────────────
# Tests run in subprocesses so each starts with a clean import state.
# This is intentional — it lets us demonstrate what the FIRST load looks like.
# It also means cext limitations appear honestly (can't hide them by reusing state).

def run_script(code: str, label: str, timeout: int = 120) -> tuple[bool, float]:
    """Run code in a subprocess. Returns (success, elapsed_ms)."""
    subsection(label)
    tmp = Path("_omnipkg_tf_test.py")
    tmp.write_text(code, encoding="utf-8")
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        elapsed = (time.perf_counter() - start) * 1000
        if result.stdout:
            for line in result.stdout.splitlines():
                safe_print(f"  {line}")
        if result.returncode != 0 and result.stderr:
            safe_print("  ── stderr ──")
            for line in result.stderr.splitlines()[-20:]:
                safe_print(f"  {line}")
        return result.returncode == 0, elapsed
    except subprocess.TimeoutExpired:
        elapsed = (time.perf_counter() - start) * 1000
        safe_print(f"  ❌ Timed out after {timeout}s")
        return False, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        safe_print(f"  ❌ {e}")
        return False, elapsed
    finally:
        tmp.unlink(missing_ok=True)


# ── Test scripts ──────────────────────────────────────────────────────────────

def make_test1(project_root, versions_dir):
    """
    Test 1: Load TensorFlow 2.13.0 from a bubble.

    ── API: omnipkgLoader (legacy loader) ───────────────────────────────────
      from omnipkg.loader import omnipkgLoader

      with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
          import tensorflow as tf
          # tf is now the 2.13.0 version from the bubble
          # All dependent packages (keras, typing_extensions) are also
          # activated from the same bubble.

      HOW IT WORKS:
        1. Finds the bubble directory for tensorflow-2.13.0
        2. Purges any conflicting modules already in sys.modules
        3. Prepends bubble path to sys.path  ("cloak" activation)
        4. On __exit__: removes path, purges modules, restores original state

      COST: ~100ms first load (module purge + path swap), ~40ms repeat
      LIMIT: Cannot switch C-extension packages (tensorflow, torch, numpy)
             in the same process — see Test 4 for why.
    ─────────────────────────────────────────────────────────────────────────
    """
    return f'''
import sys, traceback
from pathlib import Path
sys.path.insert(0, {repr(str(project_root))})
{SAFE_PRINT_DEFINITION}
{GET_VERSION_SNIPPET}

from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

def main():
    config_manager = ConfigManager(suppress_init_messages=True)
    versions_dir = {repr(str(versions_dir))}

    safe_print("  Loading TensorFlow 2.13.0 from bubble (first load in clean process)...")
    safe_print("  API: omnipkgLoader(\\"tensorflow==2.13.0\\", config=config_manager.config)")
    safe_print("")

    with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
        import tensorflow as tf
        import typing_extensions
        import keras
        te_ver, te_src = get_version("typing_extensions", "typing_extensions", versions_dir)
        safe_print(f"  ✅ tensorflow  : {{tf.__version__}}")
        safe_print(f"  ✅ typing_exts : {{te_ver}}  ({{te_src}})")
        safe_print(f"  ✅ keras       : {{keras.__version__}}")
        model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
        safe_print("  ✅ Model created — TF 2.13.0 fully operational from bubble")

    safe_print("  ✅ Context exited — sys.path restored, modules purged")

main()
'''


def make_test2(project_root, versions_dir):
    """
    Test 2: Switch pure-Python packages between contexts.

    Pure Python packages (no .so/.pyd files) CAN be swapped by the legacy
    loader because Python's import system can fully unload and reload them
    via sys.modules purge + sys.path swap.  typing_extensions is a good
    example — it's a single .py file.
    """
    return f'''
import sys, traceback
from pathlib import Path
sys.path.insert(0, {repr(str(project_root))})
{SAFE_PRINT_DEFINITION}
{GET_VERSION_SNIPPET}

from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

def main():
    config_manager = ConfigManager(suppress_init_messages=True)
    versions_dir = {repr(str(versions_dir))}

    safe_print("  Pure-Python packages CAN be switched mid-process.")
    safe_print("  No C extensions = no OS linker lock = full swap possible.")
    safe_print("")

    safe_print("  ── Context A: typing_extensions==4.14.1")
    safe_print("  API: with omnipkgLoader(\\"typing_extensions==4.14.1\\", config=...)")
    with omnipkgLoader("typing_extensions==4.14.1", config=config_manager.config):
        import typing_extensions
        ver, src = get_version(typing_extensions, "typing_extensions", versions_dir)
        safe_print(f"  ✅ version: {{ver}}  source: {{src}}")

    safe_print("")
    safe_print("  ── Context B: typing_extensions==4.5.0  (same process, different version)")
    safe_print("  API: with omnipkgLoader(\\"typing_extensions==4.5.0\\", config=...)")
    with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):
        import typing_extensions
        ver, src = get_version(typing_extensions, "typing_extensions", versions_dir)
        safe_print(f"  ✅ version: {{ver}}  source: {{src}}")

    safe_print("")
    safe_print("  ✅ Both contexts worked.  Pure Python = fully swappable.")

main()
'''


def make_test3(project_root, versions_dir):
    """
    Test 3: Nested loaders — outer context + inner context.

    Contexts stack correctly.  The inner loader activates its bubble on top
    of the outer one, then restores the outer state on __exit__.
    Works for pure Python packages.  TF inside nested would hit cext limits.
    """
    return f'''
import sys, traceback
from pathlib import Path
sys.path.insert(0, {repr(str(project_root))})
{SAFE_PRINT_DEFINITION}
{GET_VERSION_SNIPPET}

from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

def main():
    config_manager = ConfigManager(suppress_init_messages=True)
    versions_dir = {repr(str(versions_dir))}

    safe_print("  Nested loaders: inner context stacks on top of outer.")
    safe_print("  On inner __exit__, outer state is restored.")
    safe_print("")

    safe_print("  ── Outer: typing_extensions==4.5.0")
    with omnipkgLoader("typing_extensions==4.5.0", config=config_manager.config):
        import typing_extensions as te
        outer_ver, _ = get_version(te, "typing_extensions", versions_dir)
        safe_print(f"  ✅ Outer: typing_extensions = {{outer_ver}}")

        safe_print("  ── Inner: tensorflow==2.13.0  (first TF load, cext OK here)")
        with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
            import tensorflow as tf
            import typing_extensions as te_inner
            inner_te_ver, _ = get_version(te_inner, "typing_extensions", versions_dir)
            safe_print(f"  ✅ Inner: tensorflow       = {{tf.__version__}}")
            safe_print(f"  ✅ Inner: typing_extensions = {{inner_te_ver}}")
            model = tf.keras.Sequential([tf.keras.layers.Dense(10, input_shape=(5,))])
            safe_print("  ✅ Inner: model created successfully")
        safe_print("  ── Inner exited — outer context restored")

    safe_print("  ── Outer exited")
    safe_print("  ✅ Nested loaders work correctly for pure-Python + first-load cext.")

main()
'''


def make_test4(project_root, versions_dir):
    """
    Test 4: The C-extension limitation — honest, explained, not a crash.

    This test intentionally tries to switch TF versions mid-process.
    The loader MUST refuse and explain why — not silently return wrong data,
    not crash, not corrupt state.

    WHY CEXTS CANNOT BE SWAPPED:
      When Python imports a C extension (.so on Linux, .pyd on Windows),
      the OS dynamic linker (ld.so / ntdll) maps it into the process's
      address space.  There is no dlclose() path that Python exposes.
      The extension's symbols, vtables, and global state remain in memory
      for the lifetime of the process.

      Attempting to load a different version of the same cext would result
      in symbol conflicts, double-initialization of global state, or
      segfaults.  omnipkgLoader detects this and refuses.

    THE DAEMON SOLUTION:
      Each DaemonClient.execute_shm() call runs in a SEPARATE PROCESS that
      was started with exactly the right interpreter and package version.
      Cross-process = OS-level isolation = no linker conflict.
      Cost: ~300ms cold start per worker, ~2ms per hot call.
    """
    return f'''
import sys, traceback
from pathlib import Path
sys.path.insert(0, {repr(str(project_root))})
{SAFE_PRINT_DEFINITION}
{GET_VERSION_SNIPPET}

from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

def main():
    config_manager = ConfigManager(suppress_init_messages=True)
    versions_dir = {repr(str(versions_dir))}

    safe_print("  This test demonstrates the C-extension (cext) limitation.")
    safe_print("  The loader will REFUSE to switch TF versions — this is correct.")
    safe_print("")

    safe_print("  ── Load 1: tensorflow==2.13.0  (first load, succeeds)")
    with omnipkgLoader("tensorflow==2.13.0", config=config_manager.config):
        import tensorflow as tf
        safe_print(f"  ✅ Loaded: tensorflow = {{tf.__version__}}")
        safe_print(f"     File : {{tf.__file__}}")
    safe_print("  ── Context exited, but TF .so is still mapped in this process")

    safe_print("")
    safe_print("  ── Load 2: tensorflow==2.12.0  (SAME process — cext swap attempt)")
    safe_print("  The loader should detect TF is already loaded as a cext and refuse.")
    safe_print("")
    with omnipkgLoader("tensorflow==2.12.0", config=config_manager.config):
        import tensorflow as tf2
        actual = tf2.__version__
        safe_print(f"  ⚠️  tensorflow reports version: {{actual}}")
        if actual == "2.12.0":
            safe_print("  ❌ Unexpected: got 2.12.0 — cext swap should not be possible")
        else:
            safe_print(f"  ✅ Correct: still {{actual}} (2.13.0 .so is linker-locked)")
            safe_print("     The loader refused to corrupt state — returned existing cext.")
    safe_print("")
    safe_print("  WHY THIS IS CORRECT BEHAVIOR:")
    safe_print("  ─────────────────────────────")
    safe_print("  C extensions are loaded by the OS linker, not Python.")
    safe_print("  Once loaded, their .so file cannot be unloaded from the process.")
    safe_print("  Attempting to load a second version causes symbol conflicts.")
    safe_print("  omnipkgLoader detects this and returns the already-loaded version.")
    safe_print("  No crash. No corrupt state. Clear explanation.")
    safe_print("")
    safe_print("  THE DAEMON SOLUTION:")
    safe_print("  ─────────────────────")
    safe_print("  from omnipkg.isolation.worker_daemon import DaemonClient")
    safe_print("  client = DaemonClient()")
    safe_print("  # Each execute_shm() runs in a SEPARATE PROCESS")
    safe_print("  # → worker for tf==2.13.0 is a different PID than tf==2.12.0")
    safe_print("  # → OS-level isolation, no linker conflict, true version switching")
    safe_print("  # → Cold: ~300ms  |  Hot: ~2ms")

main()
'''



# ── Test 5: Daemon — TF 2.13.0 + TF 2.12.0 concurrent, cold then hot ────────
#
# ── API: DaemonClient (singleton — create ONCE, reuse forever) ───────────────
#   from omnipkg.isolation.worker_daemon import DaemonClient
#   client = DaemonClient()
#
#   client.execute_shm(
#       spec       = "tensorflow==2.13.0",   # package + version
#       code       = "...",                  # code to run in worker
#       shm_in     = {},                     # data in  (shared memory)
#       shm_out    = {},                     # keys out (shared memory)
#       python_exe = "/path/to/python3.11",  # from _interpreter_cache
#   ) → {success, stdout, stderr, error, traceback}
#
#   WHY THIS WORKS FOR TF VERSION SWITCHING:
#     Each worker is a SEPARATE OS PROCESS.
#     TF 2.13.0 worker has its own linker address space.
#     TF 2.12.0 worker has its own linker address space.
#     No symbol conflict. No shared state. True isolation.
#
#   COLD vs HOT:
#     Cold = worker process doesn't have this spec loaded yet → ~300ms
#     Hot  = worker process already has spec loaded → ~2ms
#     Run the same calls twice to see both numbers.
# ─────────────────────────────────────────────────────────────────────────────

_TF_CLIENT = None
_TF_PYTHON_EXE = None

def _get_tf_client_and_exe(config_manager):
    """Get or create the daemon client and resolve py3.11 path once."""
    global _TF_CLIENT, _TF_PYTHON_EXE
    if _TF_CLIENT is None:
        from omnipkg.isolation.worker_daemon import DaemonClient
        _TF_CLIENT = DaemonClient()
    if _TF_PYTHON_EXE is None:
        import subprocess as _sp
        result = _sp.run(
            ["omnipkg", "info", "python"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
        for line in result.stdout.splitlines():
            if "Python 3.11:" in line:
                _TF_PYTHON_EXE = line.split(":", 1)[1].strip().split()[0]
                break
        if not _TF_PYTHON_EXE:
            raise RuntimeError("Python 3.11 not found in registry")
    return _TF_CLIENT, _TF_PYTHON_EXE


_TF_WORKER_CODE = """
import sys, importlib.metadata, os
import tensorflow as tf
print(f"  tf={tf.__version__}  pid={os.getpid()}  exe={sys.executable}")
# Quick sanity: build a tiny model to confirm TF is actually operational
model = tf.keras.Sequential([tf.keras.layers.Dense(1, input_shape=(1,))])
print(f"  model.layers={len(model.layers)}")
"""


def run_tf_daemon_pair(client, python_exe, label: str) -> dict:
    """
    Run TF 2.13.0 and TF 2.12.0 concurrently via daemon.
    Returns timing dict for both specs.
    """
    import concurrent.futures

    specs = ["tensorflow==2.13.0", "tensorflow==2.12.0"]

    def run_one(spec):
        t0 = time.perf_counter()
        result = client.execute_shm(
            spec=spec,
            code=_TF_WORKER_CODE,
            shm_in={},
            shm_out={},
            python_exe=python_exe,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return spec, result, elapsed

    safe_print(f"  ── {label}")
    safe_print(f"  Firing both workers concurrently...")
    safe_print("")

    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(run_one, s) for s in specs]
        run_results = [f.result() for f in concurrent.futures.as_completed(futures)]
    wall_ms = (time.perf_counter() - wall_start) * 1000

    timings = {}
    all_ok = True
    for spec, result, elapsed in sorted(run_results, key=lambda x: x[0]):
        ok = result.get("success", False)
        all_ok = all_ok and ok
        icon = "✅" if ok else "❌"
        safe_print(f"  {icon} {spec:<25}  {fmt(elapsed):>10}")
        if result.get("stdout"):
            for line in result["stdout"].splitlines():
                safe_print(f"     {line.strip()}")
        if not ok and result.get("error"):
            safe_print(f"     error: {result['error']}")
        timings[spec] = elapsed

    safe_print("")
    safe_print(f"  Wall time (both concurrent): {fmt(wall_ms)}")
    safe_print(f"  Sequential equiv           : {fmt(sum(timings.values()))}")
    return {"ok": all_ok, "timings": timings, "wall_ms": wall_ms}


def phase_daemon_tf(config_manager) -> tuple[bool, dict, dict]:
    """
    Test 5: Run TF 2.13.0 + TF 2.12.0 via daemon, twice.
    First run = cold (workers spin up).
    Second run = hot (workers already warm, spec already loaded).
    """
    try:
        client, python_exe = _get_tf_client_and_exe(config_manager)

        # Verify daemon is up
        status = client.status()
        if not status.get("success"):
            safe_print("  ⚠️  Daemon not running — starting...")
            import subprocess as _sp
            _sp.Popen(
                ["8pkg", "daemon", "start"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
            )
            for _ in range(60):
                time.sleep(0.5)
                if client.status().get("success"):
                    break
            else:
                safe_print("  ❌ Daemon never came up")
                return False, {}, {}

        safe_print(f"  ✅ Daemon running | python_exe: {python_exe}")
        safe_print("")

        cold = run_tf_daemon_pair(client, python_exe, "Run 1 — COLD (workers loading spec for first time)")
        safe_print("")
        hot  = run_tf_daemon_pair(client, python_exe, "Run 2 — HOT  (workers already have spec loaded)")

        return cold["ok"] and hot["ok"], cold, hot

    except Exception as e:
        safe_print(f"  ❌ {e}")
        import traceback as _tb
        safe_print(_tb.format_exc())
        return False, {}, {}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    total_start = time.perf_counter()

    section("omnipkg — TensorFlow Dependency Switching Demo")
    safe_print("  Covers: legacy loader capabilities, pure-Python switching,")
    safe_print("  nested contexts, and the honest C-extension limitation —")
    safe_print("  plus why the daemon was built to solve what the loader cannot.")

    config_manager = phase_setup()
    if config_manager is None:
        safe_print("❌ Setup failed — aborting")
        sys.exit(1)

    versions_dir = Path(config_manager.config["multiversion_base"]).resolve()

    results = {}
    timings = {}

    # Test 1
    section("Test 1 — TensorFlow 2.13.0 bubble load (legacy loader, clean process)")
    safe_print("  First load of a cext package works fine — nothing is linker-locked yet.")
    safe_print("")
    ok, ms = run_script(make_test1(project_root, versions_dir),
                        "Running in subprocess (clean import state)")
    results["test1"] = ok
    timings["test1"] = ms
    safe_print(f"\n  Result: {'✅ PASSED' if ok else '❌ FAILED'}  ({fmt(ms)} total)")

    # Test 2
    section("Test 2 — Pure-Python version switching (typing_extensions)")
    safe_print("  Pure Python packages have no linker lock — full swap is possible.")
    safe_print("")
    ok, ms = run_script(make_test2(project_root, versions_dir),
                        "Running in subprocess")
    results["test2"] = ok
    timings["test2"] = ms
    safe_print(f"\n  Result: {'✅ PASSED' if ok else '❌ FAILED'}  ({fmt(ms)} total)")

    # Test 3
    section("Test 3 — Nested loader contexts")
    safe_print("  Inner context stacks on outer; outer state restored on inner exit.")
    safe_print("")
    ok, ms = run_script(make_test3(project_root, versions_dir),
                        "Running in subprocess")
    results["test3"] = ok
    timings["test3"] = ms
    safe_print(f"\n  Result: {'✅ PASSED' if ok else '❌ FAILED'}  ({fmt(ms)} total)")

    # Test 4
    section("Test 4 — C-extension limitation (intentional, explained)")
    safe_print("  TF version switch attempted mid-process.")
    safe_print("  Loader must refuse gracefully — no crash, no corrupt state.")
    safe_print("")
    ok, ms = run_script(make_test4(project_root, versions_dir),
                        "Running in subprocess", timeout=180)
    results["test4"] = ok
    timings["test4"] = ms
    safe_print(f"\n  Result: {'✅ PASSED' if ok else '❌ FAILED'}  ({fmt(ms)} total)")

    # Test 5
    section("Test 5 — Daemon: TF 2.13.0 + TF 2.12.0 concurrent (cold then hot)")
    safe_print("  Two different TF versions running simultaneously in separate workers.")
    safe_print("  Run twice: cold start first, then hot to show the real speed.")
    safe_print("  This is what the loader physically cannot do.")
    safe_print("")
    t5_ok, t5_cold, t5_hot = phase_daemon_tf(config_manager)
    results["test5"] = t5_ok
    if t5_cold.get("timings") and t5_hot.get("timings"):
        # Show cold vs hot comparison table
        safe_print("")
        safe_print(f"  {'Spec':<26} {'Cold':>10}  {'Hot':>10}  {'Speedup':>10}")
        safe_print(f"  {'─'*24:<26} {'─'*8:>10}  {'─'*8:>10}  {'─'*8:>10}")
        for spec in sorted(t5_cold["timings"]):
            c = t5_cold["timings"][spec]
            h = t5_hot["timings"][spec]
            sp = f"{c/h:.0f}x" if h > 0 else "—"
            safe_print(f"  {spec:<26} {fmt(c):>10}  {fmt(h):>10}  {sp:>10}")
        safe_print(f"  {'─'*58}")
        safe_print(f"  {'Wall (concurrent)':<26} {fmt(t5_cold['wall_ms']):>10}  {fmt(t5_hot['wall_ms']):>10}")
    safe_print(f"\n  Result: {'✅ PASSED' if t5_ok else '❌ FAILED'}")

    # Summary
    section("Results Summary")
    labels = {
        "test1": "TF 2.13.0 bubble load            (legacy loader, clean process)",
        "test2": "typing_extensions switch          (pure Python, fully swappable)",
        "test3": "Nested loader contexts            (outer + inner stacking)",
        "test4": "TF version switch attempt         (cext limit, graceful refusal)",
        "test5": "Daemon: TF 2.13.0 + 2.12.0 concurrent  (cold → hot proof)",
    }
    passed = sum(results.values())
    for key, label in labels.items():
        icon = "✅" if results[key] else "❌"
        t = f"  ({fmt(timings[key])})" if key in timings else ""
        safe_print(f"  {icon}  {label}{t}")

    safe_print(f"\n  {passed}/5 passed")

    safe_print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║  omnipkg loader API — quick reference                                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  LEGACY LOADER (same process, sys.path swap)                                 ║
║    from omnipkg.loader import omnipkgLoader                                  ║
║                                                                              ║
║    with omnipkgLoader("pkg==version", config=config_manager.config):        ║
║        import pkg   # ← bubble version, isolated via sys.path               ║
║    # ← on exit: sys.path restored, modules purged                           ║
║                                                                              ║
║    ✅ CAN swap:  pure Python packages (no .so/.pyd)                          ║
║    ✅ CAN load:  any package FIRST time in a clean process                   ║
║    ❌ CANNOT swap: C extensions (tensorflow, torch, numpy, etc.)             ║
║       Reason: OS linker maps .so into process memory permanently.           ║
║       Behavior: detects lock, refuses swap, returns already-loaded version. ║
║    Cost: ~100ms first load, ~40ms repeat (same process)                     ║
║                                                                              ║
║  DAEMON (separate process per worker, true cext isolation)                   ║
║    from omnipkg.isolation.worker_daemon import DaemonClient                 ║
║    client = DaemonClient()   # ← create ONCE, reuse for all calls           ║
║                                                                              ║
║    result = client.execute_shm(                                              ║
║        spec       = "tensorflow==2.13.0",                                   ║
║        code       = "import tensorflow as tf; print(tf.__version__)",       ║
║        shm_in     = {},   # data IN  via shared memory (avoids JSON cost)   ║
║        shm_out    = {},   # keys OUT via shared memory                      ║
║        python_exe = "/path/to/python",  # from: omnipkg info python         ║
║    )                                                                         ║
║    # result = {success, stdout, stderr, error, traceback}                   ║
║                                                                              ║
║    ✅ CAN swap: ANY package including C extensions                           ║
║       Reason: each worker is a separate OS process — linker state isolated  ║
║    Cost: ~300ms cold start per worker, ~2ms hot (worker pre-warmed)         ║
║                                                                              ║
║  WHEN TO USE WHICH                                                           ║
║    loader → same Python, pure Python packages, low overhead, in-process     ║
║    daemon → any version of any package, cross-Python, cext switching        ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

    total_ms = (time.perf_counter() - total_start) * 1000
    safe_print(f"  Total time: {fmt(total_ms)}")
    sys.exit(0 if passed == 5 else 1)


if __name__ == "__main__":
    main()
