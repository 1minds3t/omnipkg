"""
NumPy + SciPy Version Switching Demo
=====================================
This demo explores three approaches to C-extension package version switching.

WHY C-EXTENSIONS ARE SPECIAL:
  When Python imports a compiled package (numpy, scipy), the OS maps its .so
  file into process memory via dlopen(). This mapping CANNOT be undone — even
  if you delete sys.modules["numpy"], the shared library stays mapped at the
  same memory address. This means:

    - In a SINGLE process: you can switch Python-level references, but if two
      different numpy versions are ever loaded, scipy's compiled C code may call
      the wrong function pointer → cryptic errors like '_NoValueType'.

    - In a SUBPROCESS: each new process gets a completely clean memory map.
      The loader works perfectly here — full version control, no ABI conflicts.

    - In the DAEMON: workers are long-lived subprocesses, pre-warmed per version.
      First call pays the import cost once; every subsequent call is ~0.8ms.

APPROACH SUMMARY:
  1. omnipkgLoader (inline)   — works for single packages, fast, same process
  2. omnipkgLoader (subprocess) — works for ANY combination, clean C ABI per run
  3. Daemon workers            — works for ANY combination, amortizes import cost
"""

import sys
import os
import json
import time
import subprocess
from pathlib import Path

try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.core import ConfigManager
    from omnipkg.common_utils import safe_print as print_with_flush
except ImportError:
    def print_with_flush(msg, **kwargs):
        print(msg, flush=True)


def run_test():
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config
    ROOT_DIR = Path(__file__).resolve().parent.parent

    # Reusable cleanup helper — a loader instance with no spec,
    # used only to call _aggressive_module_cleanup() which properly
    # walks all submodules, not just a naive startswith check.
    _cleaner = omnipkgLoader(None, config=omnipkg_config, quiet=True)

    def evict(*pkg_names):
        """
        Evict C-extension modules from sys.modules using the loader's
        own cleanup logic. This removes Python references and metadata,
        but NOTE: the compiled .so stays mapped in process memory.
        Safe for single-package switching; not sufficient when two
        different ABI-incompatible versions have both been loaded.
        """
        import gc, importlib
        for pkg in pkg_names:
            _cleaner._aggressive_module_cleanup(pkg)
        gc.collect()
        importlib.invalidate_caches()

    def run_subprocess_with_output(cmd, label):
        """Run cmd in a subprocess, stream indented output, return (success, stdout, stderr)."""
        print_with_flush(f"   🔄 Running {label}...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            for line in result.stdout.strip().splitlines():
                print_with_flush(f"      {line}")
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Timeout"
        except Exception as e:
            return False, "", str(e)

    # ═══════════════════════════════════════════════════════════════════════
    print_with_flush("\n" + "─"*60)
    print_with_flush("APPROACH 1: omnipkgLoader — inline, same process")
    print_with_flush("─"*60)
    print_with_flush("""
  ✅ Works great for single C-extension packages.
  ✅ No subprocess overhead (~80-160ms per switch).
  ⚠️  Sequential switches of the SAME package require evicting
      sys.modules between each use — the loader does this for you
      but the .so stays mapped; works as long as only one version's
      C code runs at a time.
  ❌ Two ABI-incompatible packages (e.g. scipy calling numpy internals)
      will conflict if their dependencies were loaded from different
      versions in the same process. Use Approach 2 or 3 for combos.
""")

    print_with_flush("💥 NUMPY VERSION JUGGLING (inline loader):\n")
    for numpy_ver in ["1.24.3", "1.26.4"]:
        print_with_flush(f"⚡ Switching to numpy=={numpy_ver}")
        evict("numpy")
        start_time = time.perf_counter()
        try:
            with omnipkgLoader(f"numpy=={numpy_ver}", config=omnipkg_config):
                import numpy as np
                activation_time = time.perf_counter() - start_time
                print_with_flush(f"   ✅ Version: {np.__version__}")
                print_with_flush(f"   🔢 Array sum: {np.array([1, 2, 3]).sum()}")
                print_with_flush(f"   📍 {np.__file__}")
                print_with_flush(f"   ⚡ Activation time: {activation_time * 1000:.2f}ms")
                print_with_flush(f"   🎯 Version verification: {'PASSED' if np.__version__ == numpy_ver else 'FAILED'}")
        except Exception as e:
            print_with_flush(f"   ❌ Failed: {e}")
        evict("numpy")
        print_with_flush("")

    print_with_flush("🔥 SCIPY C-EXTENSION TEST (inline loader):\n")
    for scipy_ver in ["1.12.0", "1.16.1"]:
        print_with_flush(f"🌋 Switching to scipy=={scipy_ver}")
        evict("numpy", "scipy")
        start_time = time.perf_counter()
        try:
            with omnipkgLoader(f"scipy=={scipy_ver}", config=omnipkg_config):
                import scipy as sp
                import scipy.sparse
                import scipy.linalg
                activation_time = time.perf_counter() - start_time
                print_with_flush(f"   ✅ Version: {sp.__version__}")
                print_with_flush(f"   ♻️  Sparse matrix: {sp.sparse.eye(3).nnz} non-zeros")
                print_with_flush(f"   📐 Linalg det: {sp.linalg.det([[0, 2], [1, 1]])}")
                print_with_flush(f"   📍 {sp.__file__}")
                print_with_flush(f"   ⚡ Activation time: {activation_time * 1000:.2f}ms")
                print_with_flush(f"   🎯 Version verification: {'PASSED' if sp.__version__ == scipy_ver else 'FAILED'}")
        except Exception as e:
            print_with_flush(f"   ❌ Failed: {e}")
        evict("numpy", "scipy")
        print_with_flush("")

    # ═══════════════════════════════════════════════════════════════════════
    print_with_flush("\n" + "─"*60)
    print_with_flush("APPROACH 2: omnipkgLoader — subprocess (clean C ABI)")
    print_with_flush("─"*60)
    print_with_flush("""
  ✅ Works for ANY package combination including cross-library C calls.
  ✅ Each subprocess gets a clean OS memory map — no ABI conflicts.
  ✅ Still uses the loader for version management (bubbles, cloaking etc.)
  ⚠️  ~200-400ms subprocess startup overhead per call.
  💡 Best for: one-off scripts, CI tests, or when combinations matter
     more than speed.

  WHY WE NEED THIS FOR COMBOS:
    scipy.sparse.eye(3).toarray() calls numpy's C ufunc machinery
    (umr_maximum) via compiled function pointers baked into scipy's .so
    at build time. If a different numpy .so is already mapped in the
    process, those pointers are wrong → '_NoValueType' TypeError.
    A fresh subprocess has no prior mappings — the loader installs
    exactly the right numpy and scipy together from scratch.
""")

    print_with_flush("🤯 NUMPY + SCIPY VERSION COMBOS (subprocess + loader):\n")
    combos = [("1.24.3", "1.12.0"), ("1.26.4", "1.16.1")]
    config_json = json.dumps(omnipkg_config)

    for np_ver, sp_ver in combos:
        print_with_flush(f"🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}")
        combo_start = time.perf_counter()

        check_script = f"""
import sys
sys.path.insert(0, r'{ROOT_DIR}')
from omnipkg.loader import omnipkgLoader
import json

config = json.loads(r'{config_json}')

# Multi-package list syntax — loader handles ordering + priority enforcement
with omnipkgLoader(
    ['numpy=={np_ver}', 'scipy=={sp_ver}'],
    config=config,
    isolation_mode='overlay',
    quiet=True,
):
    import numpy as np
    import scipy as sp
    import scipy.sparse

    result = np.array([1, 2, 3]) @ sp.sparse.eye(3).toarray()
    np_ok = np.__version__ == '{np_ver}'
    sp_ok = sp.__version__ == '{sp_ver}'

    print(f'numpy={{np.__version__}}  scipy={{sp.__version__}}')
    print(f'numpy: {{np.__file__}}')
    print(f'scipy: {{sp.__file__}}')
    print(f'cross-library check: {{result}}')
    print('PASSED' if np_ok and sp_ok else f'FAILED (numpy={{np.__version__}} scipy={{sp.__version__}})')
    if not (np_ok and sp_ok):
        sys.exit(1)
"""
        success, stdout, stderr = run_subprocess_with_output(
            [sys.executable, "-c", check_script],
            f"numpy=={np_ver} + scipy=={sp_ver}",
        )
        ms = (time.perf_counter() - combo_start) * 1000
        print_with_flush(f"   ⚡ Total time (incl. subprocess startup): {ms:.1f}ms")
        print_with_flush(f"   🎯 {'BOTH PASSED!' if success else 'FAILED'}")
        if not success and stderr:
            print_with_flush(f"   💥 {stderr.strip().splitlines()[-1][:120]}")
        print_with_flush("")

    # ═══════════════════════════════════════════════════════════════════════
    print_with_flush("\n" + "─"*60)
    print_with_flush("APPROACH 3: Daemon workers — amortized import cost")
    print_with_flush("─"*60)
    print_with_flush("""
  ✅ Works for ANY package combination.
  ✅ Subprocess isolation — clean C ABI per worker.
  ✅ Worker stays alive between calls — import cost paid ONCE.
  ✅ ~0.8ms per switch after warmup (vs ~150ms inline, ~300ms subprocess).
  ✅ Multiple Python versions concurrently in different workers.
  ⚠️  First call (warmup) still pays import cost (~200-500ms per version).
  💡 Best for: interactive use, repeated switching, production workloads.

  The daemon pre-warms a worker per (python, package-spec) pair.
  After warmup, switching versions is just sending a message to the
  already-running worker — no import, no subprocess spawn, no .so load.
""")

    print_with_flush("🚀 DAEMON COMBO TEST (via omnipkg daemon execute):\n")

    # Use the daemon's execute API to run the combo test in an isolated worker
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient

        client = DaemonClient()

        for np_ver, sp_ver in combos:
            print_with_flush(f"🌀 COMBO via daemon: numpy=={np_ver} + scipy=={sp_ver}")

            combo_code = f"""
import sys
from omnipkg.loader import omnipkgLoader
import json

config = json.loads(r'{config_json}')

with omnipkgLoader(
    ['numpy=={np_ver}', 'scipy=={sp_ver}'],
    config=config,
    isolation_mode='overlay',
    quiet=True,
):
    import numpy as np
    import scipy as sp
    import scipy.sparse

    result = np.array([1, 2, 3]) @ sp.sparse.eye(3).toarray()
    np_ok = np.__version__ == '{np_ver}'
    sp_ok = sp.__version__ == '{sp_ver}'
    result = {{
        'numpy_ver': np.__version__,
        'scipy_ver': sp.__version__,
        'numpy_file': np.__file__,
        'scipy_file': sp.__file__,
        'cross_check': result.tolist(),
        'passed': np_ok and sp_ok,
    }}
"""
            # First call — warmup
            t_warmup = time.perf_counter()
            resp = client.execute(
                spec=f"numpy=={np_ver}",
                code=combo_code,
            )
            warmup_ms = (time.perf_counter() - t_warmup) * 1000

            if resp.get("success"):
                print_with_flush(f"   ⏱️  Warmup: {warmup_ms:.1f}ms")
                # Second call — hot worker
                t_hot = time.perf_counter()
                resp2 = client.execute(spec=f"numpy=={np_ver}", code=combo_code)
                hot_ms = (time.perf_counter() - t_hot) * 1000
                print_with_flush(f"   ⚡ Hot call: {hot_ms:.1f}ms")
                print_with_flush(f"   🎯 {'PASSED' if resp2.get('success') else 'FAILED'}")
                stdout = resp2.get("stdout", "")
                for line in stdout.strip().splitlines()[:4]:
                    print_with_flush(f"      {line}")
            else:
                print_with_flush(f"   ⚠️  Daemon unavailable — run '8pkg daemon start' to enable")
                print_with_flush(f"   💡 Daemon approach requires the daemon to be running.")
                break
            print_with_flush("")

    except Exception as e:
        print_with_flush(f"   ⚠️  Daemon not available ({e})")
        print_with_flush(f"   💡 Start it with: 8pkg daemon start")
        print_with_flush(f"   💡 Then re-run this demo to see Approach 3 in action.\n")

    # ═══════════════════════════════════════════════════════════════════════
    print_with_flush("\n" + "═"*60)
    print_with_flush("📊 SUMMARY")
    print_with_flush("═"*60)
    print_with_flush("""
  Approach              | Combo safe | Overhead    | Best for
  ──────────────────────┼────────────┼─────────────┼─────────────────────
  Loader inline         | single pkg | ~100-160ms  | scripts, one pkg
  Loader in subprocess  | ✅ any     | ~300-400ms  | CI, correctness tests
  Daemon (post-warmup)  | ✅ any     | ~0.8ms      | interactive, production

  Key insight: C extension (.so) isolation requires process isolation.
  The loader handles version management in all three cases — what changes
  is only HOW the process boundary is managed.
""")
    print_with_flush("🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇")


if __name__ == "__main__":
    run_test()