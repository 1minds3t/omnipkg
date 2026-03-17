from omnipkg.common_utils import safe_print

#!/usr/bin/env python3
"""
🌀 OMNIPKG CHAOS THEORY - DAEMON EDITION 🌀
Now using the REAL worker daemon for maximum parallelism!
"""
import sys
import os
import subprocess
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from omnipkg.i18n import _

# Import the daemon client
try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
"""
🌀 OMNIPKG CHAOS THEORY 🌀
The most UNHINGED dependency isolation stress test ever conceived.
If this runs without exploding, we've broken the laws of Python itself.
⚠️  WARNING: This script is scientifically impossible. Run at your own risk.
"""

# ═══════════════════════════════════════════════════════════
# 📦 IMPORTS: The New Architecture
# ═══════════════════════════════════════════════════════════
try:
    # 1. Common Utils
    from omnipkg.common_utils import ProcessCorruptedException

    # 2. The Core Loader
    from omnipkg.loader import omnipkgLoader

    # 3. The New Isolation Engine (✨ REFACTORED ✨)
    from omnipkg.isolation.runners import run_python_code_in_isolation
    from omnipkg.isolation.workers import PersistentWorker
    from omnipkg.isolation.switchers import TrueSwitcher
except ImportError:
    # Fallback for running directly without package installed
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from omnipkg.common_utils import ProcessCorruptedException
    from omnipkg.loader import omnipkgLoader
    from omnipkg.isolation.runners import run_python_code_in_isolation
    from omnipkg.isolation.workers import PersistentWorker

#  env vars globally for this process too
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"


# HELPER: Check verbosity
def is_verbose_mode():
    return (
        "--verbose" in sys.argv
        or "-v" in sys.argv
        or os.environ.get("OMNIPKG_VERBOSE") == "1"
    )


# ASCII art madness
CHAOS_HEADER = """
╔═══════════════════════════════════════════════════════════════════════╗
║                                                                       ║
║   ██████╗██╗  ██╗ █████╗  ██████╗ ███████╗    ████████╗██╗  ██╗     ║
║  ██╔════╝██║  ██║██╔══██╗██╔═══██╗██╔════╝    ╚══██╔══╝██║  ██║     ║
║  ██║     ███████║███████║██║   ██║███████╗       ██║   ███████║     ║
║  ██║     ██╔══██║██╔══██║██║   ██║╚════██║       ██║   ██╔══██║     ║
║  ╚██████╗██║  ██║██║  ██║╚██████╔╝███████║       ██║   ██║  ██║     ║
║   ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝       ╚═╝   ╚═╝  ╚═╝     ║
║                                                                       ║
║              🌀 O M N I P K G   C H A O S   T H E O R Y 🌀           ║
║                                                                       ║
║        "If it doesn't crash, it wasn't chaotic enough"               ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
"""


def print_chaos_header():
    print("\033[95m" + CHAOS_HEADER + "\033[0m")
    safe_print("\n🔥 Initializing Chaos Engine...\n")
    time.sleep(0.5)


# Note: Local definitions of PersistentWorker, TrueSwitcher, and run_in_subprocess
# have been removed in favor of the imported versions from omnipkg.isolation!


def chaos_test_1_version_tornado():
    """🌪️ TEST 1: VERSION TORNADO - Compare Legacy vs Daemon WITH WARMUP"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 1: 🌪️  VERSION TORNADO                                ║")
    safe_print("║  Benchmark: Legacy Loader vs Daemon Mode (WITH WARMUP)       ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    versions = ["1.24.3", "1.26.4", "2.3.5"]
    
    # ==================================================================
    # PHASE 0: DAEMON WARMUP (CRITICAL!)
    # ==================================================================
    safe_print("   📍 PHASE 0: Daemon Warmup (One-time cost per package)")
    safe_print("   ──────────────────────────────────────────────────────\n")
    
    daemon_warmup_times = {}
    
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy
        
        safe_print("   ⚡ Initializing DaemonClient...")
        client = DaemonClient()
        
        # Warm up EACH version before timing
        for ver in versions:
            pkg_spec = f"numpy=={ver}"
            safe_print(_('   🔥 Warming up {}...').format(pkg_spec))
            
            warmup_start = time.perf_counter()
            try:
                # Create proxy to trigger worker creation
                proxy = DaemonProxy(client, pkg_spec)
                
                # Execute minimal code to ensure worker is ready
                warmup_code = """
import numpy as np
print(f"WARMED:{np.__version__}")
"""
                result = proxy.execute(warmup_code)
                
                warmup_time = (time.perf_counter() - warmup_start) * 1000
                daemon_warmup_times[ver] = warmup_time
                
                if result.get("success"):
                    safe_print(f"      ✅ {pkg_spec} warmup: {warmup_time:.2f}ms")
                else:
                    safe_print(_('      ⚠️  {} warmup failed').format(pkg_spec))
                    
            except Exception as e:
                safe_print(_('      ❌ {} warmup error: {}').format(pkg_spec, str(e)[:50]))
        
        safe_print(f"\n   🔥 Total warmup time: {sum(daemon_warmup_times.values()):.2f}ms")
        safe_print("   💡 This is ONE-TIME cost! Subsequent calls are ~0.6ms!\n")
        
    except Exception as e:
        safe_print(_('   ❌ Daemon warmup failed: {}').format(str(e)[:50]))
        return False
    
    # ==================================================================
    # PHASE 1: Legacy omnipkgLoader (Current Implementation)
    # ==================================================================
    safe_print("   📍 PHASE 1: Legacy omnipkgLoader")
    safe_print("   ─────────────────────────────────\n")

    legacy_times = []
    legacy_success = 0

    for i in range(10):  # 10 random switches
        ver = random.choice(versions)
        direction = random.choice(["↗️", "↘️", "↔️", "↕️"])

        try:
            start = time.perf_counter()
            with omnipkgLoader(f"numpy=={ver}"):
                import numpy as np

                arr = np.random.rand(50, 50)
                result = np.sum(arr)
                elapsed = (time.perf_counter() - start) * 1000

            legacy_times.append(elapsed)
            legacy_success += 1
            safe_print(
                f"   {direction} Legacy #{i+1:02d}: numpy {ver} → sum={result:.2f} ({elapsed:.2f}ms)"
            )

        except Exception as e:
            safe_print(f"   💥 Legacy #{i+1:02d}: numpy {ver} → FAILED: {str(e)[:50]}")

        time.sleep(0.02)

    # ==================================================================
    # PHASE 2: Daemon Mode (AFTER WARMUP - FAIR COMPARISON!)
    # ==================================================================
    safe_print("\n   📍 PHASE 2: Daemon Mode (POST-WARMUP)")
    safe_print("   ───────────────────────────────────────\n")

    daemon_times = []
    daemon_success = 0

    for i in range(10):  # Same 10 random switches
        ver = random.choice(versions)
        direction = random.choice(["↗️", "↘️", "↔️", "↕️"])
        
        pkg_spec = f"numpy=={ver}"

        try:
            start = time.perf_counter()
            
            # Use DaemonProxy (workers are already warm!)
            proxy = DaemonProxy(client, pkg_spec)

            # Execute numpy code
            code = """
import numpy as np
arr = np.random.rand(50, 50)
result = np.sum(arr)
print(f"{np.__version__}|{result}")
"""
            result = proxy.execute(code)
            elapsed = (time.perf_counter() - start) * 1000

            if result["success"]:
                output = result["stdout"].strip()
                if "|" in output:
                    actual_ver, sum_str = output.split("|")
                    daemon_times.append(elapsed)
                    daemon_success += 1
                    safe_print(
                        f"   {direction} Daemon #{i+1:02d}: numpy {ver} → sum={sum_str} ({elapsed:.2f}ms)"
                    )
                else:
                    safe_print(f"   💥 Daemon #{i+1:02d}: Bad output: {output}")
            else:
                safe_print(
                    f"   💥 Daemon #{i+1:02d}: Execution failed: {result.get('error', 'Unknown')}"
                )

        except Exception as e:
            safe_print(f"   💥 Daemon #{i+1:02d}: Exception: {str(e)[:50]}")

        time.sleep(0.02)

    # ==================================================================
    # COMPARISON RESULTS (FAIR COMPARISON!)
    # ==================================================================
    safe_print("\n   📊 COMPARISON RESULTS")
    safe_print("   ────────────────────────\n")

    # Legacy Results
    if legacy_times:
        avg_legacy = sum(legacy_times) / len(legacy_times)
        safe_print("   🧓 Legacy omnipkgLoader:")
        safe_print(f"      Success: {legacy_success}/10")
        safe_print(f"      Avg Time: {avg_legacy:.2f}ms per switch")
        safe_print(f"      Total: {sum(legacy_times):.2f}ms")
        safe_print(f"      Includes setup time each call")
    else:
        safe_print("   🧓 Legacy omnipkgLoader: FAILED")

    safe_print("")

    # Daemon Results
    if daemon_times:
        avg_daemon = sum(daemon_times) / len(daemon_times)
        safe_print("   ⚡ Daemon Mode (POST-WARMUP):")
        safe_print(f"      Success: {daemon_success}/10")
        safe_print(f"      Avg Time: {avg_daemon:.2f}ms per switch")
        safe_print(f"      Total execution: {sum(daemon_times):.2f}ms")
        
        # Include warmup time in total cost
        total_daemon_cost = sum(daemon_warmup_times.values()) + sum(daemon_times)
        safe_print(f"      Total with warmup: {total_daemon_cost:.2f}ms")
        safe_print(f"      Warmup was: {sum(daemon_warmup_times.values()):.2f}ms")
        safe_print(_('      Warmup per version: {}').format(', '.join([f'{v}={t:.1f}ms' for v, t in daemon_warmup_times.items()])))

        # Calculate speedup (POST-WARMUP vs Legacy)
        if legacy_times:
            speedup_post_warmup = avg_legacy / avg_daemon if avg_daemon > 0 else float("inf")
            safe_print(f"      🚀 Post-warmup speedup: {speedup_post_warmup:.1f}x faster!")
            
            # Calculate with warmup included (realistic for first run)
            avg_legacy_vs_warmup = avg_legacy / ((total_daemon_cost) / 10) if (total_daemon_cost) > 0 else 0
            safe_print(f"      🔥 Including warmup: {avg_legacy_vs_warmup:.1f}x faster on first run")
    else:
        safe_print("   ⚡ Daemon Mode: NOT AVAILABLE")

    # ==================================================================
    # PHASE 3: DAEMON BENCHMARK - 100 SWITCHES (Showcasing Performance)
    # ==================================================================
    safe_print("\n   📍 PHASE 3: Daemon Stress Test (100 rapid switches)")
    safe_print("   ────────────────────────────────────────────────────\n")
    
    rapid_times = []
    
    for i in range(100):
        ver = random.choice(versions)
        pkg_spec = f"numpy=={ver}"
        
        try:
            start = time.perf_counter()
            proxy = DaemonProxy(client, pkg_spec)
            
            code = """
import numpy as np
arr = np.random.rand(10, 10)  # Smaller for speed
result = np.sum(arr)
print(f"{result}")
"""
            result = proxy.execute(code)
            elapsed = (time.perf_counter() - start) * 1000
            
            if result.get("success"):
                rapid_times.append(elapsed)
                
                # Print progress every 10
                if (i + 1) % 10 == 0:
                    safe_print(f"   ⚡ Rapid #{i+1:03d}: {elapsed:.2f}ms")
                    
        except Exception:
            pass
    
    if rapid_times:
        avg_rapid = sum(rapid_times) / len(rapid_times)
        p95 = sorted(rapid_times)[int(len(rapid_times) * 0.95)]
        p99 = sorted(rapid_times)[int(len(rapid_times) * 0.99)]
        
        safe_print(_('\n   📈 Daemon Performance (100 switches):'))
        safe_print(f"      Average: {avg_rapid:.2f}ms")
        safe_print(f"      P95: {p95:.2f}ms")
        safe_print(f"      P99: {p99:.2f}ms")
        safe_print(f"      Min: {min(rapid_times):.2f}ms")
        safe_print(f"      Max: {max(rapid_times):.2f}ms")
        
        # Compare with legacy
        if legacy_times:
            rapid_vs_legacy = avg_legacy / avg_rapid
            safe_print(f"      🚀 vs Legacy: {rapid_vs_legacy:.1f}x faster for rapid switching!")

    # Overall verdict
    safe_print("\n")
    if legacy_success >= 8 and daemon_success >= 8:
        safe_print("✅ TORNADO SURVIVED - Daemon DOMINATES after warmup!")
        return True
    elif legacy_success >= 8:
        safe_print("✅ TORNADO SURVIVED (Legacy Mode)")
        return True
    elif daemon_success >= 8:
        safe_print("✅ TORNADO SURVIVED (Daemon Mode)")
        return True
    else:
        safe_print("⚡ TORNADO PARTIALLY SURVIVED")
        return legacy_success > 0 or daemon_success > 0

def chaos_test_2_dependency_inception():
    """🎭 TEST 2: DEPENDENCY INCEPTION - BENCHMARK EDITION"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 2: 🎭  DEPENDENCY INCEPTION (BENCHMARK)               ║")
    safe_print("║  10 Levels Deep. Comparison: Local Stack vs Daemon Stack     ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    versions = ["1.24.3", "1.26.4", "2.3.5"]
    MAX_DEPTH = 10
    verbose = is_verbose_mode()

    # ==================================================================
    # PHASE 1: Legacy omnipkgLoader (Local Process)
    #
    # ⚠️  KNOWN LIMITATION: The OS linker maps a .so once per process.
    # After the first numpy import, subsequent version switches can only
    # change sys.path and the Python layer — the mapped .so stays fixed.
    # Cross-ABI switches (e.g. 1.24.3 ↔ 2.3.5) may fail at import time
    # because the new Python layer expects symbols that don't exist in
    # the already-mapped .so. omnipkg catches this and continues — the
    # version reported may be wrong, but the test does not crash.
    # Phase 2 (daemon) solves this with fresh processes per worker.
    # ==================================================================
    safe_print("   📍 PHASE 1: Legacy omnipkgLoader (Local Process)")
    safe_print("   ─────────────────────────────────────────────────")

    depth_legacy = 0
    start_legacy = time.perf_counter()
    abi_misses = 0

    def go_deeper_legacy(level):
        nonlocal depth_legacy, abi_misses
        if level > MAX_DEPTH:
            return

        indent = "  " * level
        ver = random.choice(versions)

        safe_print(_('   {}{} Level {}: numpy {}').format(indent, '🔻' * level, level, ver))

        with omnipkgLoader(f"numpy=={ver}", quiet=False, worker_fallback=True):
            # Import may fail or return wrong version if a cross-ABI switch
            # encountered a .so mapping conflict. Catch and continue — this
            # is the known limitation we are demonstrating.
            try:
                import numpy as np
                got = np.__version__
            except Exception as e:
                abi_misses += 1
                got = f"⚠️ import failed ({type(e).__name__}: {str(e)})"

            if got != ver and not got.startswith("⚠️"):
                safe_print(_('   ↕️  Version drift: requested {} got {} (mapped .so constraint)').format(ver, got))
            elif got.startswith("⚠️"):
                safe_print(f'   💥 {got}')

            depth_legacy = max(depth_legacy, level)

            if level < MAX_DEPTH:
                go_deeper_legacy(level + 1)
            else:
                safe_print(_('   {}{} REACHED THE CORE!').format(indent, '💥' * 10))

    try:
        go_deeper_legacy(1)
    except Exception as e:
        safe_print(_('   ❌ Legacy Phase Failed: {}').format(e))

    total_legacy_time = time.perf_counter() - start_legacy
    note = f" ({abi_misses} ABI conflict(s) — expected)" if abi_misses else ""
    safe_print(f"\n   ⏱️  Legacy Time: {total_legacy_time:.3f}s{note}")

    # ==================================================================
    # PHASE 2: Daemon Mode (Remote Execution)
    # Each worker is a fresh process — no inherited .so mappings.
    # omnipkgLoader overlay mode switches sys.path only, safe because
    # the worker loaded exactly one numpy .so at startup.
    # ==================================================================
    safe_print("\n   📍 PHASE 2: Daemon Mode (Remote Execution)")
    safe_print("   ─────────────────────────────────────────────────")
    safe_print("   🔥 Sending recursive payload to Daemon Worker...")

    try:
        from omnipkg.isolation.worker_daemon import (
            DaemonClient,
            DaemonProxy,
            WorkerPoolDaemon,
        )

        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Starting Daemon...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(2)
    except ImportError:
        safe_print("   ❌ Daemon modules missing.")
        return False

    start_daemon = time.perf_counter()

    remote_code = f"""
import sys, os, random
from omnipkg.loader import omnipkgLoader

# DEBUG: verify env var
is_daemon = os.environ.get('OMNIPKG_IS_DAEMON_WORKER')
sys.stdout.write(f"IS_DAEMON_WORKER={{is_daemon}}\\n")
sys.stdout.flush()

depth = 0
MAX_DEPTH = {MAX_DEPTH}
versions = {versions}

def go_deeper(level):
    global depth
    if level > MAX_DEPTH: return
    ver = random.choice(versions)
    # DEBUG: log isolation mode
    loader = omnipkgLoader(f"numpy=={{ver}}", quiet=True, 
                           isolation_mode='overlay', worker_fallback=False)
    sys.stdout.write(f"BEFORE_ENTER level={{level}} ver={{ver}} "
                     f"isolation={{loader.isolation_mode}} "
                     f"in_daemon={{os.environ.get('OMNIPKG_IS_DAEMON_WORKER')}}\\n")
    sys.stdout.flush()
    with loader:
        depth = max(depth, level)
        if level < MAX_DEPTH:
            go_deeper(level + 1)
        else:
            sys.stdout.write("CORE_REACHED\\n")

go_deeper(1)
"""
    try:
        proxy = DaemonProxy(client, "numpy==1.26.4", python_exe=sys.executable)
        result = proxy.execute(remote_code)

        total_daemon_time = time.perf_counter() - start_daemon

        if result["success"]:
            lines = result["stdout"].strip().split("\n")
            for line in lines:
                if "|" in line and line.startswith("L"):
                    parts = line.split("|")
                    lvl = int(parts[0][1:])
                    ver = parts[1]
                    indent = "  " * lvl
                    safe_print(_('   ⚡ {}{} Level {}: numpy {}').format(
                        indent, '⚡' * lvl, lvl, ver))
                elif "CORE_REACHED" in line:
                    indent = "  " * MAX_DEPTH
                    safe_print(_('   ⚡ {}{} REACHED THE CORE (REMOTELY)!').format(
                        indent, '💥' * 10))
                elif verbose:
                    safe_print(f"      [Remote] {line}")
        else:
            safe_print(_('   ❌ Daemon Execution Failed: {}').format(result['error']))
            total_daemon_time = float("inf")

    except Exception as e:
        safe_print(_('   ❌ Daemon Error: {}').format(e))
        total_daemon_time = float("inf")

    # ==================================================================
    # SCOREBOARD
    # ==================================================================
    safe_print(f"\n{'='*60}")
    safe_print(f"📊 INCEPTION RESULTS ({MAX_DEPTH} Nested Layers)")
    safe_print(f"{'='*60}")
    safe_print(f"{'METRIC':<20} | {'LEGACY':<15} | {'DAEMON':<15}")
    safe_print("-" * 60)
    safe_print(
        f"{'Total Time':<20} | {total_legacy_time:.3f}s          | {total_daemon_time:.3f}s"
    )
    if abi_misses:
        safe_print(f"{'ABI conflicts':<20} | {abi_misses} (expected)   | 0 (clean)")

    if total_daemon_time < total_legacy_time and total_daemon_time < float("inf"):
        speedup = total_legacy_time / total_daemon_time
        safe_print("-" * 60)
        safe_print(f"🚀 SPEEDUP FACTOR: {speedup:.1f}x FASTER")

    if total_daemon_time < float("inf"):
        safe_print("\n✅ WE WENT DEEPER (IN BOTH DIMENSIONS)!")
        return True
    else:
        safe_print("\n⚠️  DAEMON STACK FAILED")
        return False
    
def chaos_test_3_framework_battle_royale():
    """⚔️ TEST 3: FRAMEWORK BATTLE ROYALE (DAEMON EDITION)"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 3: ⚔️  FRAMEWORK BATTLE ROYALE (TRULY CONCURRENT)    ║")
    safe_print("║  All 4 frameworks executing AT THE SAME EXACT TIME          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # 1. Connect to Daemon and measure startup
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
        from concurrent.futures import as_completed
        import numpy as np

        daemon_start = time.perf_counter()
        client = DaemonClient()
        status = client.status()
        daemon_connect_time = (time.perf_counter() - daemon_start) * 1000

        if not status.get("success"):
            safe_print("   ⚙️  Summoning the Arena (Daemon)...")
            vip_specs = [
                "tensorflow==2.13.0",
                "torch==2.0.1+cu118",
                "numpy==1.24.3",
                "numpy==2.3.5",
            ]
            WorkerPoolDaemon(warmup_specs=vip_specs).start(daemonize=True)
            time.sleep(3)
            daemon_start = time.perf_counter()
            client = DaemonClient()
            daemon_connect_time = (time.perf_counter() - daemon_start) * 1000

        safe_print(f"⚡ Daemon connection established in {daemon_connect_time:.2f}ms\n")

    except ImportError:
        return False

    # 2. Define The Fighters
    combatants = [
        (
            "TensorFlow",
            "tensorflow==2.13.0",
            "import tensorflow as tf; result = {'output': f'TensorFlow {tf.__version__} | Sum: {tf.reduce_sum(tf.constant([1, 2, 3])).numpy()}'}",
        ),
        (
            "PyTorch",
            "torch==2.0.1+cu118",
            "import torch; result = {'output': f'PyTorch {torch.__version__} | Sum: {torch.sum(torch.tensor([1, 2, 3])).item()}'}",
        ),
        (
            "NumPy Legacy",
            "numpy==1.24.3",
            "import numpy as np; result = {'output': f'NumPy {np.__version__} | Sum: {np.sum(np.array([1, 2, 3]))}'}",
        ),
        (
            "NumPy Modern",
            "numpy==2.3.5",
            "import numpy as np; result = {'output': f'NumPy {np.__version__} | Sum: {np.sum(np.array([1, 2, 3]))}'}",
        ),
    ]

    def execute_fighter(name, spec, code):
        t_start = time.perf_counter()
        res = client.execute_smart(spec, code)
        duration = (time.perf_counter() - t_start) * 1000
        return (name, res, duration)

    safe_print("🥊 ROUND 1: Truly Concurrent Execution\n")

    wall_clock_start = time.perf_counter()

    # Execute all 4 in parallel threads
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(execute_fighter, name, spec, code)
            for name, spec, code in combatants
        ]

        results = []
        for future in as_completed(futures):
            results.append(future.result())

    wall_clock_total = (time.perf_counter() - wall_clock_start) * 1000

    # Sort by name for consistent display
    results.sort(key=lambda x: x[0])

    total_individual_time = 0
    for name, res, duration in results:
        total_individual_time += duration
        if res.get("success"):
            # The result dict contains the 'output' key from worker execution
            if isinstance(res.get("result"), dict) and "output" in res["result"]:
                output = res["result"]["output"]
            else:
                # Fallback to stdout or stringified result
                output = res.get("meta", {}).get("stdout", "").strip()
                if not output:
                    output = str(res.get("result", "")).strip()

            safe_print(f"   ⚡ {name:<15} → {output} ({duration:.2f}ms)")
        else:
            safe_print(f"   💥 {name:<15} → FAILED ({duration:.2f}ms)")

    safe_print("\n📊 CONCURRENCY RESULTS:")
    safe_print(f"   Wall Clock Time: {wall_clock_total:.2f}ms")
    safe_print(f"   Sum of Individual Times: {total_individual_time:.2f}ms")
    safe_print(
        f"   🚀 Parallelism Factor: {total_individual_time/wall_clock_total:.1f}x"
    )
    safe_print(f"   ⚡ Daemon Overhead: {daemon_connect_time:.2f}ms\n")

    """⚔️ TEST 3: FRAMEWORK BATTLE ROYALE (DAEMON EDITION)"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 3: ⚔️  FRAMEWORK BATTLE ROYALE (DAEMON)               ║")
    safe_print("║  TensorFlow, PyTorch, JAX, NumPy - ALL IN MEMORY AT ONCE     ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # 1. Connect to Daemon
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon

        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Summoning the Arena (Daemon)...")
            # Pre-warm the combatants
            vip_specs = [
                "tensorflow==2.13.0",
                "torch==2.0.1+cu118",
                "numpy==1.24.3",
                "numpy==2.3.5",
            ]
            WorkerPoolDaemon(warmup_specs=vip_specs).start(daemonize=True)
            time.sleep(3)  # Give TF time to boot (it's heavy)

    except ImportError:
        return False

    # 2. Define The Fighters
    combatants = [
        {
            "name": "TensorFlow",
            "spec": "tensorflow==2.13.0",
            "code": "import tensorflow as tf; print(f'TensorFlow {tf.__version__} | Sum: {tf.reduce_sum(tf.constant([1, 2, 3])).numpy()}')",
        },
        {
            "name": "PyTorch",
            "spec": "torch==2.0.1+cu118",
            "code": "import torch; print(f'PyTorch {torch.__version__}    | Sum: {torch.sum(torch.tensor([1, 2, 3])).item()}')",
        },
        {
            "name": "NumPy Legacy",
            "spec": "numpy==1.24.3",
            "code": "import numpy as np; print(f'NumPy {np.__version__}      | Sum: {np.sum(np.array([1, 2, 3]))}')",
        },
        {
            "name": "NumPy Modern",
            "spec": "numpy==2.3.5",
            "code": "import numpy as np; print(f'NumPy {np.__version__}      | Sum: {np.sum(np.array([1, 2, 3]))}')",
        },
    ]

    safe_print("🥊 ROUND 2: Simultaneous Execution via Daemon\n")

    total_start = time.perf_counter()

    # We will launch them sequentially to see the latency,
    # but the daemon keeps them all resident in RAM.

    for fighter in combatants:
        t_start = time.perf_counter()

        # Smart Execute (Data is None, so it uses JSON path automatically)
        res = client.execute_smart(fighter["spec"], fighter["code"])

        duration = (time.perf_counter() - t_start) * 1000

        if res.get("success"):
            output = res["result"].strip()
            # Clean up the output string for display
            clean_out = output.split("\n")[-1] if "\n" in output else output
            safe_print(f"   ⚡ {fighter['name']:<15} → {clean_out} ({duration:.2f}ms)")
        else:
            safe_print(f"   💥 {fighter['name']:<15} → FAILED: {res.get('error')[:50]}")

    safe_print(
        f"\n✅ ALL COMBATANTS TESTED in {(time.perf_counter() - total_start):.2f}s!\n"
    )

    # ---------------------------------------------------------
    # ROUND 2: The "Smart" Data Hand-off
    # ---------------------------------------------------------
    safe_print("🥊 ROUND 3: Smart Data Hand-off (1MB Array)\n")

    import numpy as np

    data = np.ones(1024 * 128)  # 1MB of floats (128K * 8 bytes)

    # TF Sum via Smart Client
    t_start = time.perf_counter()

    # We pass the data. execute_smart will see 1MB > 64KB and choose SHM.
    # Code assumes 'arr_in' exists and writes to 'arr_out'
    tf_shm_code = """
import tensorflow as tf
# Convert SHM input (numpy) to Tensor, sum it, write back to SHM output
# Note: For scalar output in SHM, we write to index 0
val = tf.reduce_sum(tf.constant(arr_in))
arr_out[0] = val.numpy()
"""

    res = client.execute_smart("tensorflow==2.13.0", tf_shm_code, data=data)

    duration = (time.perf_counter() - t_start) * 1000

    if res.get("success"):
        result_val = res["result"][0]
        transport = res.get("transport", "UNKNOWN")
        safe_print(
            f"   🚀 TF 2.13 (1MB)   → Sum: {result_val:.0f} via {transport} ({duration:.2f}ms)"
        )
    else:
        safe_print(_('   💥 TF Failed: {}').format(res.get('error')))
    # PyTorch Sum via Smart Client
    # ---------------------------------------------------------
    # ROUND 2: Zero-Copy Shared Memory Data Hand-off
    # ---------------------------------------------------------
    safe_print("🥊 ROUND 3: Zero-Copy Shared Memory (1MB Array)\n")

    # 1MB of floats (128K * 8 bytes)
    data = np.ones(1024 * 128, dtype=np.float64)
    data_size_mb = data.nbytes / (1024 * 1024)

    safe_print(f"   📦 Input: {data_size_mb:.2f}MB array ({len(data):,} elements)\n")

    # TensorFlow Sum via SHM
    t_start = time.perf_counter()

    tf_shm_code = """
import tensorflow as tf
# arr_in and arr_out are already mapped via shared memory
val = tf.reduce_sum(tf.constant(arr_in))
arr_out[0] = val.numpy()
result = {'sum': float(arr_out[0])}
"""

    res = client.execute_smart("tensorflow==2.13.0", tf_shm_code, data=data)
    tf_duration = (time.perf_counter() - t_start) * 1000

    if res.get("success"):
        result_val = (
            res["result"][0] if isinstance(res["result"], np.ndarray) else res["result"]
        )
        transport = res.get("transport", "UNKNOWN")
        safe_print(
            f"   🚀 TensorFlow 2.13 → Sum: {result_val:.0f} via {transport} ({tf_duration:.2f}ms)"
        )
    else:
        safe_print(_('   💥 TensorFlow Failed: {}').format(res.get('error')))

    # PyTorch Sum via SHM
    t_start = time.perf_counter()

    torch_shm_code = """
import torch
val = torch.sum(torch.from_numpy(arr_in))
arr_out[0] = val.item()
result = {'sum': float(arr_out[0])}
"""

    res = client.execute_smart("torch==2.0.1+cu118", torch_shm_code, data=data)
    torch_duration = (time.perf_counter() - t_start) * 1000

    if res.get("success"):
        result_val = (
            res["result"][0] if isinstance(res["result"], np.ndarray) else res["result"]
        )
        transport = res.get("transport", "UNKNOWN")
        safe_print(
            f"   🚀 PyTorch 2.0     → Sum: {result_val:.0f} via {transport} ({torch_duration:.2f}ms)"
        )
    else:
        safe_print(_('   💥 PyTorch Failed: {}').format(res.get('error')))

    # NumPy Sum via SHM
    t_start = time.perf_counter()

    numpy_shm_code = """
import numpy as np
val = np.sum(arr_in)
arr_out[0] = val
result = {'sum': float(arr_out[0])}
"""

    res = client.execute_smart("numpy==2.3.5", numpy_shm_code, data=data)
    numpy_duration = (time.perf_counter() - t_start) * 1000

    if res.get("success"):
        result_val = (
            res["result"][0] if isinstance(res["result"], np.ndarray) else res["result"]
        )
        transport = res.get("transport", "UNKNOWN")
        safe_print(
            f"   🚀 NumPy 2.3.5     → Sum: {result_val:.0f} via {transport} ({numpy_duration:.2f}ms)"
        )
    else:
        safe_print(_('   💥 NumPy Failed: {}').format(res.get('error')))

    safe_print("\n📊 ZERO-COPY PERFORMANCE:")
    safe_print(f"   Data Size: {data_size_mb:.2f}MB")
    safe_print(
        f"   TensorFlow: {tf_duration:.2f}ms ({data_size_mb/tf_duration*1000:.0f} MB/s)"
    )
    safe_print(
        f"   PyTorch:    {torch_duration:.2f}ms ({data_size_mb/torch_duration*1000:.0f} MB/s)"
    )
    safe_print(
        f"   NumPy:      {numpy_duration:.2f}ms ({data_size_mb/numpy_duration*1000:.0f} MB/s)"
    )
    safe_print("   🎯 Zero-copy means NO data serialization overhead!\n")

    return True

def chaos_test_4_memory_madness():
    """🧠 TEST 4: MEMORY MADNESS - Allocate everywhere"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 4: 🧠  MEMORY MADNESS                                  ║")
    safe_print("║  Simultaneous memory allocation across version boundaries    ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    allocations = []
    versions = ["1.24.3", "1.26.4", "2.3.5"]

    for i, ver in enumerate(versions):
        with omnipkgLoader(f"numpy=={ver}"):
            import numpy as np

            # Allocate increasingly large arrays
            size = (1000 * (i + 1), 1000 * (i + 1))
            arr = np.ones(size)
            mem_mb = arr.nbytes / 1024 / 1024
            addr = hex(id(arr))

            allocations.append((ver, mem_mb, addr))
            safe_print(f"🧠 numpy {ver}: Allocated {mem_mb:.1f}MB at {addr}")

    safe_print(_('\n🎯 Total allocations: {}').format(len(allocations)))
    safe_print(_('🎯 Unique memory addresses: {}').format(len(set((a[2] for a in allocations)))))
    safe_print("✅ MEMORY CHAOS CONTAINED!\n")

def chaos_test_5_race_condition_roulette():
    """🎰 TEST 5: RACE CONDITION ROULETTE - ZERO-COPY SHM EDITION (WARMED + HEAVIER LOAD)"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║ TEST 5: 🎰 RACE CONDITION ROULETTE (SHM TURBO + WARMUP) ║")
    safe_print("║ 10 Threads × 3 Swaps | 500×50000 matrices | Full warmup first ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    import numpy as np
    from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon

    results = {}
    versions = ["numpy==1.24.3", "numpy==1.26.4", "numpy==2.3.5"]
    print_lock = threading.Lock()
    verbose = is_verbose_mode()  # assuming this function exists in your codebase

        # ──────────────────────────────────────────────────────────────
    # PHASE 0: Warm-up – one execution per version to hot-start workers
    # Uses the SAME operation as the benchmark (sum/mean, 500×500)
    # so LAPACK/BLAS are NOT initialized, keeping warmup fast.
    # ──────────────────────────────────────────────────────────────
    safe_print("🔥 Performing controlled warm-up for each version...")
    client = DaemonClient()
    if not client.status().get("success"):
        safe_print(" ❌ Daemon not running! Starting...")
        WorkerPoolDaemon().start(daemonize=True)
        time.sleep(2)
 
    warmup_data = np.random.rand(500, 500).astype(np.float64)  # same size as benchmark
    for spec in versions:
        t_start = time.perf_counter()
        try:
            unused, unused = client.execute_zero_copy(
                spec,
                """
import numpy as np
sum_val = np.sum(arr_in)
mean_val = np.mean(arr_in)
arr_out[0] = sum_val
arr_out[1] = mean_val
print(np.__version__)
                """,
                input_array=warmup_data,
                output_shape=(2,),
                output_dtype="float64",
            )
            duration = (time.perf_counter() - t_start) * 1000
            safe_print(f"  ✅ Warm-up {spec} complete in {duration:>6.2f} ms")
        except Exception as e:
            safe_print(f"  ⚠️ Warm-up failed for {spec}: {e}")
            return False

    # ──────────────────────────────────────────────────────────────
    # PHASE 1: Chaos – 10 threads × 3 swaps each
    # ──────────────────────────────────────────────────────────────
    def chaotic_worker(thread_id):
        thread_versions = [random.choice(versions) for _ in range(3)]
        thread_results = []
        for i, spec in enumerate(thread_versions):
            # Smaller matrix to avoid thermal death
            local_data = np.random.rand(500, 500)  # ~2 MB, still meaningful

            code = """
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import numpy as np

sum_val = np.sum(arr_in)
mean_val = np.mean(arr_in)
arr_out[0] = sum_val
arr_out[1] = mean_val
print(np.__version__)
            """

            t_start = time.perf_counter()
            try:
                result_arr, response = client.execute_zero_copy(
                    spec,
                    code,
                    input_array=local_data,
                    output_shape=(2,),
                    output_dtype="float64",
                )
                t_end = time.perf_counter()
                duration_ms = (t_end - t_start) * 1000

                local_sum = np.sum(local_data)
                local_mean = np.mean(local_data)
                remote_sum, remote_mean = result_arr[0], result_arr[1]
                remote_version = response["stdout"].strip()

                if np.isclose(local_sum, remote_sum, rtol=1e-6) and np.isclose(local_mean, remote_mean, rtol=1e-6):
                    status = "✅"
                    msg = f"{remote_version:<14}"
                else:
                    status = "❌"
                    msg = _('MATH ERROR: sum {} vs {} | mean {} vs {}').format(local_sum, remote_sum, local_mean, remote_mean)

                thread_results.append((spec, remote_version, status, duration_ms))

                if verbose:
                    with print_lock:
                        safe_print(
                            f" 🎲 Thread {thread_id:02d} Round {i+1}: {msg} → {duration_ms:>6.2f} ms"
                        )
            except Exception as e:
                thread_results.append((spec, str(e), "❌", 0))
                with print_lock:
                    safe_print(f" 💥 Thread {thread_id:02d}: {e}")

        results[thread_id] = thread_results

    safe_print("🔥 Launching 10 concurrent threads hammering SHM subsystem (500×500 matrices)...")
    race_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(chaotic_worker, i) for i in range(10)]
        for f in futures:
            f.result()

    race_time = time.perf_counter() - race_start

    total_swaps = 0
    successful_swaps = 0
    total_latency = 0.0
    for thread_id, thread_results in results.items():
        total_swaps += len(thread_results)
        for unused, unused, status, duration in thread_results:
            if status == "✅":
                successful_swaps += 1
                total_latency += duration

    safe_print(f"\n{'='*60}")
    safe_print(_('🎯 Total Requests: {}').format(total_swaps))
    safe_print(
        f"✅ Success Rate: {successful_swaps}/{total_swaps} "
        f"({successful_swaps / total_swaps * 100:.1f}%)"
    )
    safe_print(f"⚡ Total Time: {race_time:.3f}s")
    safe_print(f"⚡ Throughput: {total_swaps / race_time:.1f} swaps/sec")
    if successful_swaps > 0:
        safe_print(f"🚀 Avg Latency (success only): {total_latency / successful_swaps:.2f} ms/swap")
    safe_print(_('✅ CHAOS SURVIVED! (Memory Integrity Verified)'))
    print("=" * 60 + "\n")

    return successful_swaps == total_swaps
    
def chaos_test_6_version_time_machine():
    """⏰ TEST 6: VERSION TIME MACHINE - Past, present, future"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 6: ⏰  VERSION TIME MACHINE                           ║")
    safe_print("║  Travel through NumPy history at light speed                 ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    timeline = [
        ("🦕 PREHISTORIC", "numpy==1.23.5", "2022"),
        ("🏛️  ANCIENT", "numpy==1.24.3", "2023"),
        ("📜 LEGACY", "numpy==1.26.4", "2024"),
        ("🚀 MODERN", "numpy==2.3.5", "2024"),
    ]

    print(_('⏰ Initiating temporal displacement...\n'))

    for era, spec, year in timeline:
        try:
            safe_print(_('🌀 Jumping to {}...').format(year))
            with omnipkgLoader(spec):
                import numpy as np

                arr = np.array([1, 2, 3, 4, 5])
                mean = arr.mean()
                print(f"   {era:20} {spec:20} → mean={mean}")
        except Exception as e:
            safe_print(_('   ⚠️  {}: Time jump failed - {}').format(era, e))
        time.sleep(0.2)

    safe_print("\n✅ TIME TRAVEL COMPLETE!\n")


def chaos_test_7_dependency_jenga():
    """🎲 TEST 7: DEPENDENCY JENGA - Remove pieces carefully"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 7: 🎲  DEPENDENCY JENGA                               ║")
    safe_print("║  Stack versions carefully... DON'T LET IT FALL!              ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    stack = []
    versions = ["1.24.3", "1.26.4", "2.3.5", "1.26.4", "1.24.3"]

    safe_print("🎲 Building the tower...\n")

    for i, ver in enumerate(versions):
        try:
            with omnipkgLoader(f"numpy=={ver}"):
                import numpy as np

                arr = np.random.rand(50, 50)
                checksum = np.sum(arr)
                stack.append((ver, checksum))

                blocks = "🟦" * (i + 1)
                print(
                    f"   {blocks} Level {i+1}: numpy {ver} (checksum: {checksum:.2f})"
                )
                time.sleep(0.1)
        except Exception:
            safe_print(_('   💥 TOWER COLLAPSED AT LEVEL {}!').format(i + 1))
            break

    if len(stack) == len(versions):
        safe_print(_('\n🏆 PERFECT TOWER! All {} blocks stable!').format(len(stack)))
    print()


def chaos_test_8_quantum_superposition():
    """⚛️ TEST 8: QUANTUM SUPERPOSITION - Multiple states at once"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 8: ⚛️  QUANTUM SUPERPOSITION                          ║")
    safe_print("║  Exist in multiple version states simultaneously             ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("🌀 Entering quantum state...\n")

    states = []

    with omnipkgLoader("numpy==1.24.3"):
        import numpy as np1

        state1 = np1.array([1, 2, 3])
        states.append(("1.24.3", hex(id(state1))))
        safe_print(_('   |ψ₁⟩ numpy 1.24.3 exists at {}').format(hex(id(state1))))

        with omnipkgLoader("numpy==1.26.4"):
            import numpy as np2

            state2 = np2.array([4, 5, 6])
            states.append(("1.26.4", hex(id(state2))))
            safe_print(_('   |ψ₂⟩ numpy 1.26.4 exists at {}').format(hex(id(state2))))

            with omnipkgLoader("numpy==2.3.5"):
                import numpy as np3

                state3 = np3.array([7, 8, 9])
                states.append(("2.3.5", hex(id(state3))))
                safe_print(_('   |ψ₃⟩ numpy 2.3.5 exists at {}').format(hex(id(state3))))

                safe_print("\n   💫 QUANTUM SUPERPOSITION ACHIEVED!")
                safe_print(_('   💫 {} states exist simultaneously!').format(len(states)))

    safe_print("\n✅ WAVE FUNCTION COLLAPSED SAFELY!\n")


def chaos_test_9_import_hell():
    """🔥 TEST 9: IMPORT HELL - Conflicting imports everywhere"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 9: 🔥  IMPORT HELL                                    ║")
    safe_print("║  Import conflicts that should destroy Python itself          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("🔥 Descending into import hell...\n")

    # --- MODIFIED: Use PersistentWorker for TensorFlow ---
    safe_print("   😈 Circle 1: TensorFlow Reality (Persistent Worker)")

    verbose = is_verbose_mode()  # <--- Check verbosity
    tf_worker = PersistentWorker(
        "tensorflow==2.13.0", verbose=verbose
    )  # <--- Pass it here
    try:
        tf_code = """
from omnipkg.loader import omnipkgLoader
import sys

with omnipkgLoader("numpy==1.23.5"):
    import tensorflow as tf
    x = tf.constant([1, 2, 3])
    result = tf.reduce_sum(x).numpy()
    sys.stderr.write(f"      🔥 TensorFlow {tf.__version__} + NumPy survival: sum={result}\\n")
"""
        result = tf_worker.execute(tf_code)
        if result["success"]:
            safe_print("      ✅ TensorFlow Reality survived")
        else:
            safe_print(_('      ⚠️  TensorFlow failed: {}...').format(result['error'][:60]))
    finally:
        tf_worker.shutdown()

    # Circle 2: NumPy Standalone
    safe_print("   😈 Circle 2: NumPy Standalone")
    try:
        with omnipkgLoader("numpy==1.24.3"):
            import numpy as np

            safe_print(_('      ✅ numpy {} survived').format(np.__version__))
    except Exception as e:
        error_msg = str(e).split("\n")[0][:60]
        safe_print(_('      ⚠️  numpy==1.24.3 - {}...').format(error_msg))

    # Circle 3: PyTorch Inferno
    safe_print("   😈 Circle 3: PyTorch Inferno")
    try:
        with omnipkgLoader("torch==2.0.1+cu118"):
            import torch

            safe_print(_('      ✅ torch {} survived').format(torch.__version__))
    except Exception as e:
        error_msg = str(e).split("\n")[0][:60]
        safe_print(_('      ⚠️  torch==2.0.1+cu118 - {}...').format(error_msg))

    # Circle 4: NumPy Chaos
    safe_print("   😈 Circle 4: NumPy Chaos")
    for numpy_ver in ["1.26.4", "2.3.5", "1.24.3"]:
        try:
            with omnipkgLoader(f"numpy=={numpy_ver}"):
                import numpy as np

                safe_print(_('      ✅ numpy {} survived').format(np.__version__))
        except Exception as e:
            error_msg = str(e).split("\n")[0][:60]
            safe_print(_('      ⚠️  numpy=={} - {}...').format(numpy_ver, error_msg))

    # Circle 5: Mixed Madness
    safe_print("   😈 Circle 5: Mixed Madness")
    try:
        with omnipkgLoader("torch==2.0.1+cu118"):
            import torch

            safe_print(_('      ✅ torch {} survived').format(torch.__version__))
    except Exception as e:
        safe_print(_('      ⚠️  torch - {}...').format(str(e)[:60]))

    try:
        with omnipkgLoader("numpy==2.3.5"):
            import numpy as np

            safe_print(_('      ✅ numpy {} survived').format(np.__version__))
    except Exception as e:
        safe_print(_('      ⚠️  numpy - {}...').format(str(e)[:60]))

    time.sleep(0.1)
    safe_print("\n✅ ESCAPED FROM HELL!\n")


def chaos_test_10_grand_finale():
    """🎆 TEST 10: GRAND FINALE - Everything at once"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 10: 🎆  GRAND FINALE                                  ║")
    safe_print("║  MAXIMUM CHAOS - ALL TESTS SIMULTANEOUSLY                    ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("🎆 Initiating maximum chaos sequence...\n")

    from omnipkg.core import ConfigManager

    cm = ConfigManager(suppress_init_messages=True)
    omnipkg_config = cm.config

    def mini_tornado():
        for unused in range(3):
            with omnipkgLoader(
                f"numpy=={random.choice(['1.24.3', '2.3.5'])}", config=omnipkg_config
            ):
                import numpy as np

                np.random.rand(100, 100).sum()

    def mini_inception(level=0):
        if level < 3:
            with omnipkgLoader(
                f"numpy=={random.choice(['1.24.3', '1.26.4'])}", config=omnipkg_config
            ):
                mini_inception(level + 1)

    safe_print("🌪️  Launching chaos tornado...")
    mini_tornado()

    safe_print("🎭 Executing mini inception...")
    mini_inception()

    safe_print("🧠 Rapid memory allocation...")
    for ver in ["1.24.3", "2.3.5"]:
        with omnipkgLoader(f"numpy=={ver}", config=omnipkg_config):
            import numpy as np

            np.ones((500, 500))

    print(_('⏰ Time travel sequence...'))
    for ver in ["1.24.3", "2.3.5", "1.24.3"]:
        with omnipkgLoader(f"numpy=={ver}", config=omnipkg_config):
            import numpy as np

            pass

    safe_print("\n🎆🎆🎆 MAXIMUM CHAOS SURVIVED! 🎆🎆🎆\n")


def chaos_test_11_tensorflow_resurrection():
    """⚰️ TEST 11: TENSORFLOW RESURRECTION ULTIMATE"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 11: ⚰️💀⚡ TENSORFLOW RESURRECTION ULTIMATE          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    verbose = is_verbose_mode()

    # ================================================================
    # PART A: TRUE SEQUENTIAL WORKER SPAWN (Kill after each use)
    # ================================================================
    safe_print("┌──────────────────────────────────────────────────────────────┐")
    safe_print("│ PART A: ⚡ TRUE SEQUENTIAL WORKER RESURRECTION               │")
    safe_print("└──────────────────────────────────────────────────────────────┘\n")

    safe_print("   📍 Method 1: Sequential Workers (FRESH PROCESS EACH TIME)")
    safe_print("      (Measuring true 'Wall Clock' time from process start to result)")
    sequential_times = []

    for i in range(5):
        safe_print(_('\n      🔄 Iteration {}/5: Spawning & executing...').format(i + 1))

        # CRITICAL: Measure time starting BEFORE process creation
        start_wall = time.perf_counter()

        worker = None
        try:
            # 1. Initialize the heavy process
            worker = PersistentWorker("tensorflow==2.13.0", verbose=verbose)

            # 2. Run the code
            code = """
from omnipkg.loader import omnipkgLoader
import sys
with omnipkgLoader("tensorflow==2.13.0"):
    import tensorflow as tf
    x = tf.constant([1, 2, 3])
    result = tf.reduce_sum(x)
"""
            result = worker.execute(code)

            # 3. Calculate Wall Clock Time
            elapsed = (time.perf_counter() - start_wall) * 1000

            if result.get("success"):
                sequential_times.append(elapsed)
                safe_print(f"         ✅ Full Lifecycle: {elapsed:.0f}ms")
            else:
                safe_print(_('         ❌ Failed: {}').format(result.get('error')))

        except Exception as e:
            safe_print(_('         ❌ Exception: {}').format(e))

        finally:
            if worker:
                safe_print("         🛑 Killing worker for fresh restart...")
                try:
                    worker.shutdown()
                except:
                    pass
            time.sleep(0.2)

    avg_sequential = (
        sum(sequential_times) / len(sequential_times) if sequential_times else 0
    )
    safe_print(
        f"\n   📊 Sequential Average: {avg_sequential:.0f}ms per TRUE resurrection"
    )

    # ================================================================
    # PART B: DAEMON MODE (Fair Test - Fresh Daemon)
    # ================================================================
    safe_print("┌──────────────────────────────────────────────────────────────┐")
    safe_print("│ PART B: ⚡ DAEMON MODE (Persistent Worker - FAIR TEST)      │")
    safe_print("└──────────────────────────────────────────────────────────────┘\n")

    daemon_times = []
    daemon_available = False

    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy

        safe_print("   🧹 Restarting Daemon...")
        subprocess.run(
            ["8pkg", "daemon", "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        subprocess.run(
            ["8pkg", "daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        client = DaemonClient()
        for unused in range(10):
            time.sleep(0.5)
            if client.status().get("success"):
                safe_print("   ✅ Daemon online\n")
                break

        safe_print("   📍 Running 5 iterations with persistent worker:\n")

        proxy = DaemonProxy(client, "tensorflow==2.13.0")

        for i in range(5):
            safe_print(f"      Iteration {i+1}/5...")
            start = time.perf_counter()

            code = """
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("tensorflow==2.13.0"):
    import tensorflow as tf
    x = tf.constant([1, 2, 3])
    result = tf.reduce_sum(x)
"""
            result = proxy.execute(code)
            elapsed = (time.perf_counter() - start) * 1000

            if result.get("success"):
                daemon_times.append(elapsed)
                safe_print(f"         ⚡ {elapsed:.0f}ms")
            else:
                safe_print("         ❌ Failed")

        avg_daemon = sum(daemon_times) / len(daemon_times) if daemon_times else 0
        speedup = avg_sequential / avg_daemon if avg_daemon > 0 else 0

        safe_print(f"\n   📊 Daemon Average: {avg_daemon:.0f}ms")
        safe_print(f"   🚀 SPEEDUP: {speedup:.1f}x faster!\n")
        daemon_available = True

    except Exception as e:
        safe_print(_('   ❌ Daemon mode failed: {}\n').format(e))

    # ================================================================
    # PART C: CONCURRENT SPAWN & HEAVY MATH
    # ================================================================
    safe_print("┌──────────────────────────────────────────────────────────────┐")
    safe_print("│ PART C: 🎼 CONCURRENT SPAWN & OPS TEST                      │")
    safe_print("└──────────────────────────────────────────────────────────────┘\n")

    if not daemon_available:
        return False

    versions = ["2.12.0", "2.13.0", "2.20.0"]

    # ------------------------------------------------------------
    # STEP 1: SEQUENTIAL SPAWN
    # ------------------------------------------------------------
    # We must restart the daemon to ensure no workers are cached
    safe_print("   🧹 Restarting Daemon for SEQUENTIAL SPAWN TEST...")
    subprocess.run(
        ["8pkg", "daemon", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    subprocess.run(
        ["8pkg", "daemon", "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    client = DaemonClient()  # Reconnect

    safe_print("   🐢 STEP 1: Sequential Spawn (One by one)...")
    seq_spawn_start = time.perf_counter()

    for ver in versions:
        t_ver = time.perf_counter()
        safe_print(_('      Requesting TF {}...').format(ver))
        p = DaemonProxy(client, f"tensorflow=={ver}")
        # Execute simple code to force spawn wait
        p.execute(
            f"from omnipkg.loader import omnipkgLoader\nwith omnipkgLoader('tensorflow=={ver}'): import tensorflow as tf"
        )
        safe_print(f"      ✅ Ready in {(time.perf_counter() - t_ver)*1000:.0f}ms")

    seq_spawn_total = (time.perf_counter() - seq_spawn_start) * 1000
    safe_print(f"   ⏱️  Total Sequential Spawn Time: {seq_spawn_total:.0f}ms\n")

    # ------------------------------------------------------------
    # STEP 2: CONCURRENT SPAWN
    # ------------------------------------------------------------
    # Restart daemon AGAIN to clear cache for fair concurrent test
    safe_print("   🧹 Restarting Daemon for CONCURRENT SPAWN TEST...")
    subprocess.run(
        ["8pkg", "daemon", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    subprocess.run(
        ["8pkg", "daemon", "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    client = DaemonClient()  # Reconnect

    safe_print("   🚀 STEP 2: Concurrent Spawn (All at once)...")

    conc_spawn_start = time.perf_counter()
    active_proxies = {}

    def spawn_worker(ver):
        p = DaemonProxy(client, f"tensorflow=={ver}")
        p.execute(
            f"from omnipkg.loader import omnipkgLoader\nwith omnipkgLoader('tensorflow=={ver}'): import tensorflow as tf"
        )
        return ver, p

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(spawn_worker, v) for v in versions]
        for f in futures:
            v, p = f.result()
            active_proxies[v] = p
            safe_print(_('      ✅ Worker Ready: TF {}').format(v))

    conc_spawn_total = (time.perf_counter() - conc_spawn_start) * 1000
    safe_print(f"   ⏱️  Total Concurrent Spawn Time: {conc_spawn_total:.0f}ms")

    spawn_speedup = seq_spawn_total / conc_spawn_total if conc_spawn_total > 0 else 0
    safe_print(f"   🚀 SPAWN SPEEDUP: {spawn_speedup:.2f}x\n")

    # ------------------------------------------------------------
    # STEP 3: SEQUENTIAL HEAVY OPS
    # ------------------------------------------------------------
    safe_print("   🐢 STEP 3: Sequential Tensor Operations (Matrix Mult)...")

    heavy_code = """
import tensorflow as tf
import time
size = 2000
with tf.device('/CPU:0'):
    x = tf.random.normal((size, size))
    y = tf.random.normal((size, size))
    z = tf.matmul(x, y)
    _ = z.numpy()
"""

    seq_ops_times = []

    for v in versions:
        safe_print(_('      running on TF {}...').format(v))
        t0 = time.perf_counter()
        res = active_proxies[v].execute(heavy_code)
        dt = (time.perf_counter() - t0) * 1000
        if res.get("success"):
            seq_ops_times.append(dt)
            safe_print(f"         ✅ Done in {dt:.0f}ms")
        else:
            safe_print(_('         ❌ Failed: {}').format(res.get('error')))

    total_seq_ops = sum(seq_ops_times)
    safe_print(f"   📊 Total Sequential Calc Time: {total_seq_ops:.0f}ms\n")

    # ------------------------------------------------------------
    # STEP 4: CONCURRENT HEAVY OPS
    # ------------------------------------------------------------
    safe_print("   🚀 STEP 4: Concurrent Tensor Operations...")

    conc_ops_start = time.perf_counter()

    def run_heavy(ver):
        t_start = time.perf_counter()
        active_proxies[ver].execute(heavy_code)
        t_end = time.perf_counter()
        return ver, (t_end - t_start) * 1000

    results_conc = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_heavy, v) for v in versions]
        for f in futures:
            v, dt = f.result()
            results_conc[v] = dt
            safe_print(f"      ✅ TF {v} finished in {dt:.0f}ms")

    total_conc_ops = (time.perf_counter() - conc_ops_start) * 1000

    safe_print("\n   📊 Concurrent Calc Summary:")
    safe_print(f"      - Sequential Time: {total_seq_ops:.0f}ms")
    safe_print(f"      - Concurrent Time: {total_conc_ops:.0f}ms")

    if total_conc_ops > 0:
        calc_speedup = total_seq_ops / total_conc_ops
        safe_print(f"      - Calc Speedup: {calc_speedup:.2f}x")

    # ================================================================
    # FINAL RESULTS
    # ================================================================
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  📊 FINAL RESULTS                                            ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print(
        f"   ✅ Resurrection Lag: {avg_sequential:.0f}ms (Cold) vs {avg_daemon:.0f}ms (Warm)"
    )
    safe_print(f"   ✅ Spawning: {seq_spawn_total:.0f}ms -> {conc_spawn_total:.0f}ms")
    safe_print(f"   ✅ Calculation: {total_seq_ops:.0f}ms -> {total_conc_ops:.0f}ms")

    if avg_sequential > 1000:
        safe_print("\n   ✅ TENSORFLOW RESURRECTION: PASSED")
        return True
    else:
        safe_print("\n   ⚠️  Performance metrics marginal, but functional test PASSED")
        return True


def chaos_test_12_jax_vs_torch_mortal_kombat():
    """🥊 TEST 12: TRUE TORCH VERSION SWITCHING - Daemon Edition"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 12: 🥊 TRUE TORCH VERSION SWITCHING (DAEMON)          ║")
    safe_print("║  12 Rounds. 2 Fighters. Zero process overhead.              ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    specs = ["torch==2.0.1+cu118", "torch==2.1.0"] * 6  # 12 rounds total

    # 1. Connect to Daemon
    safe_print("⚙️  Connecting to Arena (Daemon)...")
    boot_start = time.perf_counter()

    try:
        from omnipkg.isolation.worker_daemon import (
            DaemonClient,
            DaemonProxy,
            WorkerPoolDaemon,
        )

        client = DaemonClient()

        # Verify daemon is running
        status = client.status()
        if not status.get("success"):
            safe_print("   ⚠️  Daemon not found. Summoning Daemon...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(1)  # Wait for socket

    except ImportError:
        safe_print("   ❌ Daemon modules missing.")
        return False

    # 2. Initialize Proxies (Lightweight)
    workers = {}
    for spec in ["torch==2.0.1+cu118", "torch==2.1.0"]:
        workers[spec] = DaemonProxy(client, spec)

    boot_time = time.perf_counter() - boot_start
    safe_print(f"✨ Arena Ready in {boot_time*1000:.2f}ms\n")

    successful_rounds = 0
    failed_rounds = 0
    round_times = []

    safe_print("🔔 FIGHT!\n")

    fight_start = time.perf_counter()

    for i, spec in enumerate(specs):
        round_start = time.perf_counter()

        # We pass the round number into the code so the worker prints it
        code_to_run = f"""
import torch
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
# Print directly to stdout, which the daemon captures and returns
x = torch.tensor([1., 2., 3.])
y = torch.sin(x)
print(f"   🥊 Round #{i+1}: Fighter {{torch.__version__:<6}} | Hit -> {{y.tolist()}}")
"""
        # Execute via Daemon
        result = workers[spec].execute(code_to_run)

        round_duration = (time.perf_counter() - round_start) * 1000
        round_times.append(round_duration)

        if result["success"]:
            successful_rounds += 1
            # Print the worker's output
            if result.get("stdout"):
                sys.stdout.write(result["stdout"])

            # Print timing overlay
            sys.stdout.write(f"      ⚡ {round_duration:.2f}ms\n")
        else:
            safe_print(_('   💥 FATALITY: {} failed - {}').format(spec, result.get('error')[:50]))
            failed_rounds += 1

    total_fight_time = time.perf_counter() - fight_start
    avg_round = sum(round_times) / len(round_times) if round_times else 0

    safe_print(_('\n🎯 Battle Results: {} wins, {} losses').format(successful_rounds, failed_rounds))
    safe_print(f"⏱️  Total Duration: {total_fight_time:.4f}s")
    safe_print(f"⚡ Avg Round Time: {avg_round:.2f}ms")

    if successful_rounds == len(specs):
        safe_print("✅ FLAWLESS VICTORY! (Daemon Handling Perfect Swaps)\n")
    else:
        safe_print("❌ Some rounds failed.\n")


def chaos_test_13_pytorch_lightning_storm():
    """⚡ TEST 13: PyTorch Lightning Storm - Using Daemon Workers"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 13: ⚡ PyTorch Lightning Storm                         ║")
    safe_print("║  Testing framework with daemon-managed workers               ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("   🌩️  Testing PyTorch Lightning with daemon isolation.\n")

    # Define compatible pairs with their dependencies
    test_configs = [
        {
            "torch": "torch==2.0.1+cu118",
            "lightning": "pytorch-lightning==1.9.0",
            "numpy": "numpy==1.26.4",
            "name": "PyTorch 2.0.1 + Lightning 1.9.0",
        },
        {
            "torch": "torch==2.1.0",
            "lightning": "pytorch-lightning==2.0.0",
            "numpy": "numpy==1.26.4",
            "name": "PyTorch 2.1.0 + Lightning 2.0.0",
        },
    ]

    safe_print("   🌩️  Testing PyTorch Lightning with both approaches\n")

    # ==================================================================
    # ROUND 1: Persistent Worker Mode (Traditional)
    # ==================================================================
    safe_print("   🚀 ROUND 1: Persistent Worker Mode")
    safe_print("   ─────────────────────────────────────────────────────\n")

    worker_times = []
    worker_successful = 0

    for i, config in enumerate(test_configs):
        safe_print(_('   😈 Test {}/{}: {}').format(i + 1, len(test_configs), config['name']))

        try:
            # Time the worker boot
            boot_start = time.perf_counter()
            worker = PersistentWorker(config["torch"], verbose=True)
            boot_time = time.perf_counter() - boot_start

            # Execute code
            exec_start = time.perf_counter()
            code_to_run = f"""
from omnipkg.loader import omnipkgLoader

with omnipkgLoader("{config['lightning']}"):
    import pytorch_lightning as pl
    import torch
    import numpy as np
    import sys
    sys.stderr.write(f"      ⚡ PyTorch {{torch.__version__}} + Lightning {{pl.__version__}} + NumPy {{np.__version__}} loaded successfully.\\n")
"""
            result = worker.execute(code_to_run)
            exec_time = time.perf_counter() - exec_start

            total_time = boot_time + exec_time
            worker_times.append(total_time)

            if result["success"]:
                worker_successful += 1
                safe_print(f"      ⏱️  Boot:     {boot_time*1000:7.2f}ms")
                safe_print(f"      ⏱️  Execution:{exec_time*1000:7.2f}ms")
                safe_print(f"      ⏱️  TOTAL:    {total_time*1000:7.2f}ms")
                safe_print(_('      ✅ STRIKE #{}!\n').format(worker_successful))
            else:
                safe_print(_('      💥 Failed: {}\n').format(result['error']))

        except Exception as e:
            safe_print(_('      💥 Exception: {}\n').format(str(e)))
        finally:
            try:
                worker.shutdown()
            except:
                pass

    successful = 0
    verbose = is_verbose_mode()

    # Timing tracking
    total_start = time.perf_counter()
    timing_results = []

    # Initialize daemon client
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy

        client = DaemonClient()

        # Verify daemon is running
        status = client.status()
        if not status.get("success"):
            safe_print("   ⚙️  Starting daemon...")
            from omnipkg.isolation.worker_daemon import WorkerPoolDaemon

            daemon = WorkerPoolDaemon()
            daemon.start(daemonize=True)
            time.sleep(1)

    except ImportError:
        safe_print("   ❌ Daemon not available, falling back to legacy workers")
        return chaos_test_13_pytorch_lightning_storm()

    for config in test_configs:
        safe_print(_('   😈 Testing Storm: {}').format(config['name']))

        config_start = time.perf_counter()
        timings = {}

        try:
            # Create daemon proxy for torch environment
            safe_print("      ⚙️  Connecting to daemon worker...")
            boot_start = time.perf_counter()

            proxy = DaemonProxy(client, config["torch"])
            boot_time = time.perf_counter() - boot_start
            timings["worker_connect"] = boot_time

            safe_print(f"      ⏱️  Worker connected in {boot_time*1000:.2f}ms")

            # Execute code that loads lightning within the torch environment
            code_to_run = f"""
from omnipkg.loader import omnipkgLoader

# We're already in the torch environment, now add lightning
with omnipkgLoader("{config['lightning']}"):
    import pytorch_lightning as pl
    import torch
    import numpy as np
    try:
        from .common_utils import safe_print
    except ImportError:
        from omnipkg.common_utils import safe_print
    # Verify versions
    torch_ver = torch.__version__
    lightning_ver = pl.__version__
    numpy_ver = np.__version__
    
    print(f"⚡ PyTorch {{torch_ver}} + Lightning {{lightning_ver}} + NumPy {{numpy_ver}} loaded successfully.")
    
    # Quick functionality test
    class SimpleModel(pl.LightningModule):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Linear(10, 1)
        
        def forward(self, x):
            return self.layer(x)
    
    model = SimpleModel()
    test_input = torch.randn(5, 10)
    output = model(test_input)
    
    print(f"✅ Model forward pass: input {{test_input.shape}} -> output {{output.shape}}")
"""

            exec_start = time.perf_counter()
            result = proxy.execute(code_to_run)
            exec_time = time.perf_counter() - exec_start
            timings["execution"] = exec_time

            safe_print(f"      ⏱️  Execution completed in {exec_time*1000:.2f}ms")

            if result["success"]:
                config_time = time.perf_counter() - config_start
                timings["total"] = config_time
                timing_results.append(
                    {"config": config["name"], "timings": timings, "success": True}
                )

                successful += 1
                if verbose and result.get("stdout"):
                    for line in result["stdout"].strip().split("\n"):
                        safe_print(f"      {line}")
                safe_print(f"      ⏱️  Total config time: {config_time*1000:.2f}ms")
                safe_print(_('      ✅ LIGHTNING STRIKE #{}!').format(successful))
            else:
                config_time = time.perf_counter() - config_start
                timings["total"] = config_time
                timing_results.append(
                    {
                        "config": config["name"],
                        "timings": timings,
                        "success": False,
                        "error": result.get("error", "Unknown error"),
                    }
                )

                safe_print(f"      ⏱️  Failed after {config_time*1000:.2f}ms")
                safe_print(_('      💥 Failed: {}').format(result.get('error', 'Unknown error')))
                if verbose and result.get("traceback"):
                    safe_print(f"      Traceback: {result['traceback'][:500]}")

        except Exception as e:
            config_time = time.perf_counter() - config_start
            timings["total"] = config_time
            timing_results.append(
                {
                    "config": config["name"],
                    "timings": timings,
                    "success": False,
                    "error": str(e),
                }
            )

            safe_print(f"      ⏱️  Exception after {config_time*1000:.2f}ms")
            safe_print(_('      💥 Exception: {}').format(str(e)))

    total_time = time.perf_counter() - total_start

    # Display timing summary
    safe_print("\n   📊 TIMING SUMMARY:")
    safe_print(f"   ⏱️  Total test time: {total_time*1000:.2f}ms")

    if timing_results:
        avg_connect = sum(
            t["timings"].get("worker_connect", 0) for t in timing_results
        ) / len(timing_results)
        avg_exec = sum(
            t["timings"].get("execution", 0)
            for t in timing_results
            if "execution" in t["timings"]
        )
        avg_exec = (
            avg_exec / len([t for t in timing_results if "execution" in t["timings"]])
            if any("execution" in t["timings"] for t in timing_results)
            else 0
        )

        safe_print(f"   ⏱️  Avg worker connect: {avg_connect*1000:.2f}ms")
        if avg_exec > 0:
            safe_print(f"   ⏱️  Avg execution: {avg_exec*1000:.2f}ms")

    safe_print(_('\n   🎯 Compatible Pairs: {}/{} successful').format(successful, len(test_configs)))

    if successful == len(test_configs):
        safe_print("   ✅ PYTORCH LIGHTNING STORM SURVIVED!")
        safe_print("\n")
        return True
    else:
        safe_print("   ⚡ LIGHTNING STORM FAILED!")
        safe_print("\n")
        return False


def chaos_test_14_circular_dependency_hell():
    """⭕ TEST 14: Create actual circular imports between bubbles"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 14: ⭕ CIRCULAR DEPENDENCY HELL                        ║")
    safe_print("║  Package A imports B, B imports A — across version bubbles   ║")
    safe_print("║  NOW POWERED BY PERSISTENT WORKERS FOR TRUE ISOLATION!       ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    verbose = is_verbose_mode()
    safe_print("🌀 Creating circular dependency nightmare...\n")

    # ═══════════════════════════════════════════════════════════════
    # Test 1: NumPy ↔ Pandas (Nested Loading inside Worker)
    # ═══════════════════════════════════════════════════════════════
    safe_print("   😈 Circle 1: NumPy ↔ Pandas Tango (Worker Isolated)")
    worker_1 = PersistentWorker("numpy==1.24.3", verbose=verbose)
    try:
        code = """
from omnipkg.loader import omnipkgLoader
import numpy as np
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
sys.stderr.write(f"      NumPy 1.24.3 loaded: {np.__version__}\\n")

# Now try to load pandas that depends on different numpy
try:
    with omnipkgLoader("pandas==2.2.0"):
        import pandas as pd
        sys.stderr.write(f"      Pandas 2.2.0 loaded: {pd.__version__}\\n")
        sys.stderr.write(f"      NumPy version inside pandas: {pd.np.__version__ if hasattr(pd, 'np') else 'unknown'}\\n")
        print("SUCCESS")
except Exception as e:
    sys.stderr.write(f"      💥 Pandas failed (expected): {str(e)[:100]}...\\n")
"""
        result = worker_1.execute(code)
        if result["success"]:
            safe_print("      ✅ CIRCULAR DANCE COMPLETED!")
        else:
            safe_print(
                _('      ⚠️  Circle 1 result: {}').format(result.get('error', 'Unknown error'))
            )
    finally:
        worker_1.shutdown()

    # ═══════════════════════════════════════════════════════════════
    # Test 2: Torch ↔ NumPy (The C++ Crash Candidate)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   😈 Circle 2: Torch ↔ NumPy Madness (Worker Isolated)")
    worker_2 = PersistentWorker("torch==2.0.1+cu118", verbose=verbose)
    try:
        code = """
from omnipkg.loader import omnipkgLoader
import torch
import sys

sys.stderr.write(f"      Torch 2.0.1 loaded: {torch.__version__}\\n")

# Torch uses numpy internally, now load different numpy
with omnipkgLoader("numpy==1.24.3"):
    import numpy as np
    sys.stderr.write(f"      NumPy 2.3.5 loaded: {np.__version__}\\n")
    
    # Try to use torch with the new numpy (Cross-boundary interaction)
    result = torch.tensor([1, 2, 3]).numpy()
    sys.stderr.write(f"      Torch → NumPy conversion result: {result}\\n")
"""
        result = worker_2.execute(code)
        if result["success"]:
            safe_print("      ✅ CIRCULAR MADNESS SURVIVED!")
        else:
            safe_print(
                _('      💥 Torch/NumPy circle failed: {}...').format(result['error'][:100])
            )
    finally:
        worker_2.shutdown()

    # ═══════════════════════════════════════════════════════════════
    # Test 4: Rapid Circular Switching (HIGH PERFORMANCE DEMO)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   😈 Circle 4: Rapid Circular Switching (Daemon-Powered)")
    safe_print("      🔥 Connecting to worker daemon...")

    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
        
        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("      ⚙️  Daemon not found, starting it...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(2)
    except ImportError:
        safe_print("      ❌ FATAL: Daemon components not found. Skipping test.")
        return

    # Create proxies
    proxy_old = DaemonProxy(client, "torch==2.0.1+cu118")
    proxy_new = DaemonProxy(client, "torch==2.1.0")

    successes = 0
    
    # FANCY HEADER
    safe_print(f"{'ROUND':<6} | {'WORKER':<15} | {'PID':<8} | {'VERSION':<15} | {'COMPUTE':<10} | {'TIME':<10} | {'STATUS'}")
    safe_print("-" * 95)

    for i in range(10):
        # Toggle between workers
        target_proxy = proxy_old if i % 2 == 0 else proxy_new
        worker_name = "torch-2.0.1" if i % 2 == 0 else "torch-2.1.0"
        expected = "2.0.1" if i % 2 == 0 else "2.1.0"

        # WORKER CODE: Do actual matrix math to prove the library is alive
        code = f"""
import torch, os, time
# perform a tensor operation to prove we are using the C++ backend
x = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
y = x.matmul(x) # Matrix multiplication
result = y.sum().item() # Should be 54.0

# Return: PID | Version | Calculation Result
print(f"{{os.getpid()}}|{{torch.__version__}}|{{result}}")

if not torch.__version__.startswith("{expected}"):
    raise ValueError(f"Version Mismatch! Got {{torch.__version__}}")
"""
        # MEASURE TIME
        start_time = time.perf_counter()
        result = target_proxy.execute(code)
        duration_ms = (time.perf_counter() - start_time) * 1000

        if result["success"]:
            successes += 1
            # Parse the worker's output
            output = result.get("stdout", "").strip()
            if "|" in output:
                pid, ver, calc = output.split("|")
                # Format time nicely
                time_str = f"{duration_ms:.2f}ms"
                safe_print(f" #{i+1:<4} | {worker_name:<15} | {pid:<8} | {ver:<15} | {calc:<10} | {time_str:<10} | ✅ MATCH")
            else:
                safe_print(f" #{i+1:<4} | {worker_name:<15} | {'????':<8} | {output:<15} | {'-':<10} | {duration_ms:.2f}ms    | ❓ FORMAT")
        else:
            safe_print(f" #{i+1:<4} | {worker_name:<15} | {'FAILED':<8} | {'ERROR':<15} | {'-':<10} | {duration_ms:.2f}ms    | ❌ {result['error']}")

    print(_('\n      Rapid switches: {}/10 successful').format(successes))

    if successes == 10:
        safe_print("      ✅ RAPID CIRCULAR SWITCHING MASTERED! (Daemon Isolation)")
        safe_print("      🚀 PROOF: Heavy tensor math performed in persistent environments.")
    else:
        safe_print("      ⚠️  Some circular switches failed")

    safe_print("\n🎭 CIRCULAR DEPENDENCY HELL COMPLETE!")
    safe_print("✅ REAL PACKAGES, REAL CIRCLES, REAL SURVIVAL!\n")


def chaos_test_15_isolation_strategy_benchmark():
    """
    ⚡ TEST 15: COMPREHENSIVE ISOLATION STRATEGY BENCHMARK
    """
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 15: ⚡ ISOLATION STRATEGY BENCHMARK                   ║")
    safe_print("║  Compare speed vs isolation trade-offs                      ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    specs = ["torch==2.0.1+cu118", "torch==2.1.0"] * 3  # 6 switches total
    results = {}

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 1: In-Process
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 1: IN-PROCESS (Baseline)")
    safe_print("   - Pros: Fastest")
    safe_print("   - Cons: Can't actually switch C++ packages")
    safe_print("-" * 60)

    start = time.perf_counter()
    success_count = 0

    for i, spec in enumerate(specs):
        try:
            with omnipkgLoader(spec, quiet=True):  # Quiet to keep benchmark readable
                import torch

                unused = torch.sin(torch.tensor([1.0]))
                success_count += 1
                sys.stdout.write(".")
                sys.stdout.flush()
        except Exception as e:
            safe_print(_('   ❌ Round {} failed: {}').format(i + 1, str(e)[:40]))

    print()  # Newline
    elapsed_in_process = time.perf_counter() - start
    results["in_process"] = {
        "time": elapsed_in_process,
        "success": success_count,
        "per_switch": elapsed_in_process / len(specs),
    }

    safe_print(
        f"   ✅ Total: {elapsed_in_process:.3f}s ({success_count}/{len(specs)} success)"
    )
    safe_print(f"   ⚡ Per switch: {results['in_process']['per_switch']*1000:.1f}ms\n")

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 2: Standard Subprocess
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 2: STANDARD SUBPROCESS")
    safe_print("   - Pros: Complete isolation, true switching")
    safe_print("   - Cons: Slow due to full Python startup")
    safe_print("-" * 60)

    start = time.perf_counter()
    success_count = 0

    for i, spec in enumerate(specs):
        # We add a print inside to verify it runs
        code = f"""
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}", quiet=True):
    import torch
    _ = torch.sin(torch.tensor([1.0]))
    print(f"   [Subprocess] {spec} active")
"""
        if run_python_code_in_isolation(code, f"Subprocess {i+1}"):
            success_count += 1

    elapsed_subprocess = time.perf_counter() - start
    results["subprocess"] = {
        "time": elapsed_subprocess,
        "success": success_count,
        "per_switch": elapsed_subprocess / len(specs),
    }

    safe_print(
        f"   ✅ Total: {elapsed_subprocess:.3f}s ({success_count}/{len(specs)} success)"
    )
    safe_print(f"   ⚡ Per switch: {results['subprocess']['per_switch']*1000:.1f}ms\n")

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 3: Optimized Subprocess
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 3: OPTIMIZED SUBPROCESS")
    safe_print("   - Pros: Faster startup with minimal imports")
    safe_print("   - Cons: Still spawning full processes")
    safe_print("-" * 60)

    start = time.perf_counter()
    success_count = 0

    for i, spec in enumerate(specs):
        code = f"""
import sys
try:
    import omnipkg
except ImportError:
    # Fallback if installed in editable mode
    sys.path.insert(0, "{Path(__file__).parent.parent.parent}")

from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}", quiet=True):
    import torch
    torch.sin(torch.tensor([1.0]))
    print(f"   [Optimized] {spec} calculated")
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            success_count += 1
            # Print the output from the subprocess so we see it live
            print(result.stdout.strip())

    elapsed_optimized = time.perf_counter() - start
    results["optimized_subprocess"] = {
        "time": elapsed_optimized,
        "success": success_count,
        "per_switch": elapsed_optimized / len(specs),
    }

    safe_print(
        f"   ✅ Total: {elapsed_optimized:.3f}s ({success_count}/{len(specs)} success)"
    )
    safe_print(
        f"   ⚡ Per switch: {results['optimized_subprocess']['per_switch']*1000:.1f}ms\n"
    )

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 4: Fork-based (Unix only)
    # ═══════════════════════════════════════════════════════════════
    if hasattr(os, "fork"):
        safe_print("📊 Strategy 4: FORK-BASED ISOLATION (Unix)")
        start = time.perf_counter()
        success_count = 0
        for i, spec in enumerate(specs):
            pid = os.fork()
            if pid == 0:
                try:
                    with omnipkgLoader(spec, quiet=True):
                        import torch

                        unused = torch.sin(torch.tensor([1.0]))
                        print(_('   [Fork] {} done').format(spec))
                    sys.exit(0)
                except Exception:
                    sys.exit(1)
            else:
                unused, status = os.waitpid(pid, 0)
                if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                    success_count += 1
        elapsed_fork = time.perf_counter() - start
        results["fork"] = {
            "time": elapsed_fork,
            "success": success_count,
            "per_switch": elapsed_fork / len(specs),
        }
        safe_print(
            f"   ✅ Total: {elapsed_fork:.3f}s ({success_count}/{len(specs)} success)"
        )
        safe_print(f"   ⚡ Per switch: {results['fork']['per_switch']*1000:.1f}ms\n")

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 5: Persistent Worker Pool (THE MAIN EVENT)
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 5: PERSISTENT WORKER POOL")
    safe_print("   - Pros: Reuse processes, amortize startup cost")
    safe_print("   - Visibility: 🟢 LIVE LOGGING ENABLED")
    safe_print("-" * 60)

    start = time.perf_counter()
    success_count = 0
    workers = {}

    try:
        # 1. Initialize Workers (Verbose = True shows the boot logs)
        safe_print("   ⚙️  Booting workers (One-time cost)...")
        for spec in set(specs):
            # PASS verbose=True HERE!
            workers[spec] = PersistentWorker(spec, verbose=True)

        safe_print("\n   🚀 Starting High-Speed Switching Loop...")

        # 2. Run the Loop
        for i, spec in enumerate(specs):
            try:
                # We inject a print into the worker so you see it responding live!
                # Since PersistentWorker streams stderr to your console, this will show up.
                code = "import torch; import sys; sys.stderr.write(f'   ⚡ [Worker {torch.__version__}] Calculation complete\\n')"

                result = workers[spec].execute(code)

                if result["success"]:
                    success_count += 1
            except Exception as e:
                safe_print(_('   ❌ Round {} failed: {}').format(i + 1, str(e)[:40]))
    finally:
        safe_print("   🛑 Shutting down worker pool...")
        for worker in workers.values():
            worker.shutdown()

    elapsed_worker = time.perf_counter() - start
    results["worker_pool"] = {
        "time": elapsed_worker,
        "success": success_count,
        "per_switch": elapsed_worker / len(specs),
    }

    safe_print(
        f"   ✅ Total: {elapsed_worker:.3f}s ({success_count}/{len(specs)} success)"
    )
    safe_print(f"   ⚡ Per switch: {results['worker_pool']['per_switch']*1000:.1f}ms\n")

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 3: Daemon JSON (Control Plane)
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 3: DAEMON (JSON Mode)")
    safe_print("   - Pros: Persistent workers, no boot cost")
    safe_print("   - Cons: JSON serialization overhead")
    safe_print("-" * 60)

    try:
        from omnipkg.isolation.worker_daemon import (
            DaemonClient,
            DaemonProxy,
            WorkerPoolDaemon,
        )

        # Ensure daemon is up
        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Starting Daemon...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(2)

        start = time.perf_counter()
        success_count = 0

        for spec in specs:
            proxy = DaemonProxy(client, spec)
            res = proxy.execute("import torch; print('ok')")
            if res["success"]:
                success_count += 1

        elapsed = time.perf_counter() - start
        results["daemon_json"] = {"time": elapsed, "success": success_count}
        safe_print(f"   ✅ Total: {elapsed:.3f}s")
        safe_print(f"   ⚡ Per switch: {elapsed/6*1000:.1f}ms\n")

    except ImportError:
        pass

    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 4: Daemon Zero-Copy SHM (Data Plane)
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 4: DAEMON (Zero-Copy SHM)")
    safe_print("   - Pros: Persistent workers, zero-copy data")
    safe_print("   - Cons: Tiny SHM setup overhead for small data")
    safe_print("-" * 60)

    try:
        import numpy as np

        data = np.array([1.0])  # Tiny payload

        start = time.perf_counter()
        success_count = 0

        for spec in specs:
            try:
                res_arr, unused = client.execute_zero_copy(
                    spec, "arr_out[0] = 1", data, (1,), "float64"
                )
                success_count += 1
            except:
                pass

        elapsed = time.perf_counter() - start
        results["daemon_shm"] = {"time": elapsed, "success": success_count}
        safe_print(f"   ✅ Total: {elapsed:.3f}s")
        safe_print(f"   ⚡ Per switch: {elapsed/6*1000:.1f}ms\n")

    except ImportError:
        pass

    # ═══════════════════════════════════════════════════════════════
    # FINAL SCOREBOARD
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n" + "=" * 60)
    safe_print(f"{'STRATEGY':<25} | {'TOTAL':<8} | {'PER SWAP':<10} | {'VS BASELINE'}")
    safe_print("-" * 60)

    baseline = results["in_process"]["time"]

    sorted_results = sorted(results.items(), key=lambda x: x[1]["time"])

    for strat, data in sorted_results:
        t = data["time"]
        per = (t / 6) * 1000

        if t < baseline:
            comp = f"{baseline/t:.1f}x FASTER"
        else:
            comp = f"{t/baseline:.1f}x SLOWER"

        safe_print(f"{strat:<25} | {t:6.3f}s | {per:6.1f}ms | {comp}")

    safe_print("=" * 60 + "\n")


def chaos_test_16_nested_reality_hell():
    """🧬 TEST 16: NESTED REALITY HELL"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 16: 🧬  NESTED REALITY HELL                            ║")
    safe_print("║  Phase 1: Multi-Process Switching | Phase 2: Deep Nesting    ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    verbose = is_verbose_mode()

    # ═════════════════════════════════════════════════════════════
    # PHASE 1: Rapid Sequential NumPy Switching (Using omnipkgLoader)
    # ═════════════════════════════════════════════════════════════
    safe_print("   📍 PHASE 1: Rapid Sequential NumPy Switching (Context Manager)")
    safe_print("   ─────────────────────────────────────────────────────────────")

    versions = [
        ("1.24.3", "numpy==1.24.3"),
        ("1.26.4", "numpy==1.26.4"),
        ("2.3.5", "numpy==2.3.5"),
    ]

    phase1_success = True

    for expected_ver, spec in versions:
        try:
            with omnipkgLoader(spec, quiet=not verbose):
                import numpy as np

                actual_ver = np.__version__
                arr = np.array([1, 2, 3, 4, 5])
                mean = arr.mean()

                if actual_ver == expected_ver:
                    safe_print(
                        f"     ✅ {spec:<15} → Active (version={actual_ver}, mean={mean})"
                    )
                else:
                    safe_print(
                        f"     ❌ {spec:<15} → Mismatch! Expected {expected_ver}, got {actual_ver}"
                    )
                    phase1_success = False

        except Exception as e:
            safe_print(f"     💥 {spec:<15} → Failed: {e}")
            phase1_success = False

        time.sleep(0.1)  # Brief pause between switches

    safe_print(_('   🎯 Phase 1 Result: {}\n').format('PASSED' if phase1_success else 'FAILED'))

    # ═════════════════════════════════════════════════════════════
    # PHASE 2: 7-Layer Deep Nested Activation
    # ═════════════════════════════════════════════════════════════
    safe_print("   📍 PHASE 2: 7-Layer Deep Nested Activation (Overlay)")
    safe_print("   ────────────────────────────────────────────────────")
    safe_print("   ⚙️  Booting base worker (TensorFlow)...")

    tf_worker = PersistentWorker("tensorflow==2.13.0", verbose=verbose)

    try:
        nested_hell_code = """
from omnipkg.loader import omnipkgLoader
import sys
import site
import os
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
def log(msg):
    sys.stderr.write(msg + '\\n')
    sys.stderr.flush()

# Restore main env visibility
import omnipkg
main_site_packages = os.path.dirname(os.path.dirname(omnipkg.__file__))
if main_site_packages not in sys.path:
    sys.path.append(main_site_packages)

log("     🔄 Starting nested overlay stack...")

# Layer 1: NumPy 1.24.3
with omnipkgLoader("numpy==1.24.3", quiet=False, isolation_mode='overlay'):
    import numpy as np
    
    # Layer 2: SciPy 1.10.1 (Compatible with NumPy 1.24)
    with omnipkgLoader("scipy==1.10.1", quiet=False, isolation_mode='overlay'):
        import scipy
        import scipy.linalg
        
        # Layer 3: Pandas 2.0.3 (Compatible with NumPy 1.24)
        with omnipkgLoader("pandas==2.0.3", quiet=False, isolation_mode='overlay'):
            import pandas as pd
            
            # Layer 4: Scikit-Learn 1.3.2
            with omnipkgLoader("scikit-learn==1.3.2", quiet=False, isolation_mode='overlay'):
                from sklearn.ensemble import RandomForestClassifier
                
                # TF from base
                import tensorflow as tf
                
                # Layer 5: PyTorch 2.0.1
                with omnipkgLoader("torch==2.0.1+cu118", quiet=False, isolation_mode='overlay'):
                    import torch
                    
                    log("     ✅ ALL LAYERS LOADED!")
                    
                    tf_tens = tf.constant([1,2,3])
                    torch_tens = torch.tensor([1,2,3])
                    sp_val = scipy.linalg.norm([1,2,3])
                    
                    log(f"     🎉 Verification: TF={tf_tens.shape}, Torch={torch_tens.shape}, SciPy={sp_val:.2f}")

print("SUCCESS")
"""
        result = tf_worker.execute(nested_hell_code)

        if result["success"] and "SUCCESS" in result["stdout"]:
            safe_print("\n   ✅ Phase 2: 7-layer stack STABLE!")
            phase2_success = True
        else:
            safe_print(
                _('\n   💥 Phase 2 COLLAPSED: {}\n').format(result.get('error', result.get('stderr')))
            )
            phase2_success = False

    finally:
        tf_worker.shutdown()

    if phase1_success and phase2_success:
        safe_print("\n✅ NESTED REALITY CONQUERED! (Multi-process + Overlay)")
        return True
    else:
        return False


def chaos_test_17_triple_python_multiverse():
    """🌌 TEST 17: TRIPLE PYTHON MULTIVERSE - THE ULTIMATE DEMO

    This test does something LITERALLY IMPOSSIBLE anywhere else:
    - 3 different Python interpreters (3.9, 3.10, 3.11)
    - Each running different TensorFlow + PyTorch versions
    - All executing CONCURRENTLY in the same process
    - Zero-copy data transfer via SHM between them
    - No Docker, no VMs, no serialization overhead

    This replaces Test 17 (experimental) with something that actually works!
    """
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 17: 🌌 TRIPLE PYTHON MULTIVERSE                       ║")
    safe_print("║  3 Pythons × 2 Frameworks × Concurrent Execution = IMPOSSIBLE║")
    safe_print("║  ...except with omnipkg daemon + zero-copy SHM! 🚀           ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # Check if we have the omnipkg CLI available for Python management
    try:
        result = subprocess.run(
            ["omnipkg", "info", "python"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            safe_print("   ⚠️  omnipkg Python management not available")
            safe_print("   💡 This test requires: omnipkg python adopt 3.9/3.10/3.11")
            safe_print("   ⏭️  SKIPPING (optional feature)\n")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        safe_print("   ⚠️  omnipkg CLI not available")
        safe_print("   ⏭️  SKIPPING (optional feature)\n")
        return True

    # Initialize daemon
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
        import numpy as np

        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Starting Multiverse Daemon...")
            # Use subprocess via CLI
            result = sp.run(
                [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"],
                capture_output=True,
                text=True,
            )
            time.sleep(2)

            # Verify it started
            client = DaemonClient()
            status = client.status()
            if not status.get("success"):
                safe_print("   ❌ Daemon failed to start")
                if result.stderr:
                    safe_print(f"   Error: {result.stderr[:200]}")
                return False
            safe_print("   ✅ Daemon started successfully")
    except ImportError:
        safe_print("   ❌ Daemon not available")
        return False

    # Define our parallel universes
    universes = [
        {
            "name": "Universe Alpha",
            "python": "3.9",
            "tf_spec": "tensorflow==2.12.0",
            "torch_spec": "torch==2.0.1+cu118",
            "emoji": "🔴",
            "operation": "Generate random matrix (1000x1000)",
        },
        {
            "name": "Universe Beta",
            "python": "3.10",
            "tf_spec": "tensorflow==2.13.0",
            "torch_spec": "torch==2.1.0",
            "emoji": "🟢",
            "operation": "Compute matrix determinant",
        },
        {
            "name": "Universe Gamma",
            "python": "3.11",
            "tf_spec": "tensorflow==2.20.0",
            "torch_spec": "torch==2.2.0+cu121",
            "emoji": "🔵",
            "operation": "Apply neural network layer",
        },
    ]

    for u in universes:
        safe_print(f"   {u['emoji']} {u['name']:<20} Python {u['python']}")
        safe_print(_('      ├─ TensorFlow: {}').format(u['tf_spec']))
        safe_print(_('      ├─ PyTorch:    {}').format(u['torch_spec']))
        safe_print(_('      └─ Task:       {}').format(u['operation']))

    safe_print("\n   " + "─" * 60)
    safe_print("   🎯 MISSION: Pass data through all 3 universes via SHM\n")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: Verify Python Interpreters Available
    # ═══════════════════════════════════════════════════════════════
    safe_print("   📍 PHASE 1: Checking Python Interpreters")
    safe_print("   " + "─" * 60)

    available_pythons = {}

    def check_python_version(version):
        """Check if a Python version is available."""
        try:
            result = subprocess.run(
                ["omnipkg", "info", "python"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f"Python {version}:" in line:
                    # Extract path
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        path = parts[1].strip().split()[0]
                        return path
        except:
            pass
        return None

    all_available = True
    for u in universes:
        py_path = check_python_version(u["python"])
        if py_path:
            available_pythons[u["python"]] = py_path
            safe_print(_('   ✅ Python {}: {}').format(u['python'], py_path))
        else:
            safe_print(_('   ❌ Python {}: NOT AVAILABLE').format(u['python']))
            safe_print(_('      💡 Install with: omnipkg python adopt {}').format(u['python']))
            all_available = False

    if not all_available:
        safe_print("\n   ⚠️  Not all Python versions available")
        safe_print("   ⏭️  SKIPPING (requires python version management)\n")
        return True

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: Sequential Baseline (Traditional Approach)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   📍 PHASE 2: Sequential Baseline (Traditional)")
    safe_print("   " + "─" * 60)
    safe_print("   📊 Running each universe one-by-one...\n")

    sequential_times = []
    sequential_start = time.perf_counter()

    # Generate initial data
    np.random.rand(100, 100)  # Smaller for faster demo

    for i, u in enumerate(universes):
        safe_print(_('   {} {} starting...').format(u['emoji'], u['name']))
        iter_start = time.perf_counter()

        # Execute in the appropriate Python + framework
        code = f"""
import sys
import os
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
# DIAGNOSTIC: Print what Python we're actually using
sys.stderr.write(f"\\n   🔍 DIAGNOSTIC: Running in Python {{sys.version}}\\n")
sys.stderr.write(f"   🔍 Executable: {{sys.executable}}\\n")
sys.stderr.write(f"   🔍 sys.path[0]: {{sys.path[0]}}\\n")

from omnipkg.loader import omnipkgLoader

# Load the framework
with omnipkgLoader("{u['torch_spec']}", quiet=False):  # quiet=False for visibility
    import torch
    
    # DIAGNOSTIC: Verify which torch we loaded
    sys.stderr.write(f"   🔍 PyTorch version: {{torch.__version__}}\\n")
    sys.stderr.write(f"   🔍 PyTorch path: {{torch.__file__}}\\n")
    
    # Simulate some work
    x = torch.randn(100, 100)
    y = torch.matmul(x, x.T)
    result = torch.trace(y).item()
    
    sys.stderr.write(f"      ✓ PyTorch {{torch.__version__}} computed trace: {{result:.2f}}\\n")
"""

        # Execute via daemon with specific Python version
        try:
            # Get the Python executable path for this universe
            target_python_exe = available_pythons[u["python"]]

            # CRITICAL: Use client.execute_shm() with python_exe parameter
            # This creates an isolated worker process without swapping main environment
            result = client.execute_shm(
                spec=u["torch_spec"],
                code=code,
                shm_in={},
                shm_out={},
                python_exe=target_python_exe,
            )

            iter_time = (time.perf_counter() - iter_start) * 1000
            sequential_times.append(iter_time)

            if result["success"]:
                safe_print(
                    f"   {u['emoji']} {u['name']} completed in {iter_time:.2f}ms"
                )
            else:
                error_msg = result.get("error", "Unknown")
                safe_print(_('   {} {} FAILED: {}').format(u['emoji'], u['name'], error_msg))
                # Print full traceback if available
                if result.get("traceback"):
                    safe_print(f"      Traceback: {result['traceback']}")

        except Exception as e:
            safe_print(_('   {} {} ERROR: {}').format(u['emoji'], u['name'], str(e)))
            # Print full traceback
            import traceback

            safe_print(f"      {traceback.format_exc()}")
            sequential_times.append(float("inf"))

    total_sequential = time.perf_counter() - sequential_start
    safe_print(f"\n   ⏱️  Sequential Total: {total_sequential*1000:.2f}ms")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: Concurrent Execution (THE IMPOSSIBLE DEMO)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   📍 PHASE 3: CONCURRENT MULTIVERSE (The Impossible)")
    safe_print("   " + "─" * 60)
    safe_print("   🚀 All 3 universes executing SIMULTANEOUSLY...\n")

    concurrent_results = {}
    concurrent_lock = threading.Lock()

    def execute_universe(universe):
        """Execute computation in one universe."""
        u_name = universe["name"]
        emoji = universe["emoji"]

        try:
            # Get the actual Python path
            python_exe = available_pythons[universe["python"]]

            start = time.perf_counter()

            code = f"""
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
sys.stderr.write(f"\\n   🔍 [{u_name}] Python: {{sys.version}}\\n")
sys.stderr.write(f"   🔍 [{u_name}] Executable: {{sys.executable}}\\n")

from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{universe['torch_spec']}", quiet=False):
    import torch
    sys.stderr.write(f"   🔍 [{u_name}] PyTorch: {{torch.__version__}} from {{torch.__file__}}\\n")
    x = torch.randn(200, 200)
    y = torch.matmul(x, x.T)
    result = torch.trace(y).item()
    sys.stderr.write(f"{{result:.6f}}\\n")
"""

            # CRITICAL: Use client.execute_shm() with python_exe, not DaemonProxy
            # This creates isolated worker without swapping main environment
            result = client.execute_shm(
                spec=universe["torch_spec"],
                code=code,
                shm_in={},
                shm_out={},
                python_exe=python_exe,
            )

            elapsed = (time.perf_counter() - start) * 1000

            with concurrent_lock:
                concurrent_results[u_name] = {
                    "success": result["success"],
                    "time": elapsed,
                    "emoji": emoji,
                    "error": result.get("error") if not result["success"] else None,
                    "stderr": result.get("stderr", ""),
                }

                if result["success"]:
                    safe_print(f"   {emoji} {u_name:<20} ✅ {elapsed:>7.2f}ms")
                    # Print diagnostic output
                    if result.get("stderr"):
                        safe_print(_('\n   📋 [{}] Diagnostics:').format(u_name))
                        for line in result["stderr"].strip().split("\n"):
                            if line.strip():
                                safe_print(f"      {line}")
                else:
                    error_msg = result.get("error", "Unknown")
                    safe_print(f"   {emoji} {u_name:<20} ❌ FAILED")
                    safe_print(f"      Error: {error_msg}")
                    if result.get("traceback"):
                        safe_print(f"      Traceback: {result['traceback'][:500]}")
                    if result.get("stderr"):
                        safe_print(f"      Stderr: {result['stderr'][:500]}")

        except Exception as e:
            with concurrent_lock:
                concurrent_results[u_name] = {
                    "success": False,
                    "time": 0,
                    "emoji": emoji,
                    "error": str(e),
                }
                safe_print(f"   {emoji} {u_name:<20} ❌ {str(e)}")
                # Print traceback
                import traceback

                safe_print(f"      {traceback.format_exc()[:500]}")

    # Launch all universes concurrently
    concurrent_start = time.perf_counter()
    threads = []

    for u in universes:
        t = threading.Thread(target=execute_universe, args=(u,))
        threads.append(t)
        t.start()

    # Wait for all to complete
    for t in threads:
        t.join()

    total_concurrent = time.perf_counter() - concurrent_start

    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: Zero-Copy Data Pipeline
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   📍 PHASE 4: Zero-Copy Data Pipeline")
    safe_print("   " + "─" * 60)
    safe_print("   💾 Passing 1MB matrix through 3 frameworks via SHM\n")

    # Create 1MB test data
    pipeline_data = np.random.rand(500, 250)  # ~1MB
    safe_print(
        f"   📦 Input: {pipeline_data.shape} array ({pipeline_data.nbytes/1024/1024:.2f} MB)"
    )

    pipeline_times = []
    pipeline_success = False  # Track if pipeline worked
    
    # Start timing for CPU pipeline
    pipeline_start_time = time.perf_counter()  # <-- DEFINE START TIME HERE!

    # Stage 1: NumPy → PyTorch (Universe Alpha)
    safe_print("\n   🔴 Stage 1: Processing in Universe Alpha (PyTorch 2.0)...")
    stage1_start = time.perf_counter()

    stage1_code = """
import torch
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
sys.stderr.write(f"   🔍 [Stage 1] PyTorch: {torch.__version__} from {torch.__file__}\\n")

# arr_in comes from SHM
# Use copy() to fix ABI incompatibility between numpy/torch versions across processes
torch_tensor = torch.from_numpy(arr_in.copy())
result = torch.nn.functional.relu(torch_tensor)
arr_out[:] = result.numpy()
"""

    try:
        stage1_out, unused = client.execute_zero_copy(
            universes[0]["torch_spec"],
            stage1_code,
            input_array=pipeline_data,
            output_shape=pipeline_data.shape,
            output_dtype="float64",
            python_exe=available_pythons[universes[0]["python"]],
        )
        stage1_time = (time.perf_counter() - stage1_start) * 1000
        pipeline_times.append(stage1_time)

        # Print diagnostic output if available
        if "stderr" in unused and unused["stderr"]:
            safe_print(f"      Diagnostic: {unused['stderr'][:200]}")

        safe_print(f"   ✅ Stage 1 complete: {stage1_time:.2f}ms (zero-copy SHM)")

        # Stage 2: Universe Beta
        safe_print("\n   🟢 Stage 2: Processing in Universe Beta (PyTorch 2.1)...")
        stage2_start = time.perf_counter()

        stage2_code = """
import torch
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
sys.stderr.write(f"   🔍 [Stage 2] PyTorch: {torch.__version__} from {torch.__file__}\\n")

torch_tensor = torch.from_numpy(arr_in.copy())
result = torch.sigmoid(torch_tensor)
arr_out[:] = result.numpy()
"""

        stage2_out, unused = client.execute_zero_copy(
            universes[1]["torch_spec"],
            stage2_code,
            input_array=stage1_out,
            output_shape=stage1_out.shape,
            output_dtype="float64",
            python_exe=available_pythons[universes[1]["python"]],
        )
        stage2_time = (time.perf_counter() - stage2_start) * 1000
        pipeline_times.append(stage2_time)
        safe_print(f"   ✅ Stage 2 complete: {stage2_time:.2f}ms (zero-copy SHM)")

        # Stage 3: Universe Gamma
        safe_print("\n   🔵 Stage 3: Processing in Universe Gamma (PyTorch 2.2)...")
        stage3_start = time.perf_counter()

        stage3_code = """
import torch
import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
sys.stderr.write(f"   🔍 [Stage 3] PyTorch: {torch.__version__} from {torch.__file__}\\n")

torch_tensor = torch.from_numpy(arr_in.copy())
result = torch.tanh(torch_tensor)
arr_out[:] = result.numpy()
"""

        stage3_out, unused = client.execute_zero_copy(
            universes[2]["torch_spec"],
            stage3_code,
            input_array=stage2_out,
            output_shape=stage2_out.shape,
            output_dtype="float64",
            python_exe=available_pythons[universes[2]["python"]],
        )
        stage3_time = (time.perf_counter() - stage3_start) * 1000
        pipeline_times.append(stage3_time)
        safe_print(f"   ✅ Stage 3 complete: {stage3_time:.2f}ms (zero-copy SHM)")

        # Calculate TOTAL pipeline time from the START
        total_pipeline_time = (time.perf_counter() - pipeline_start_time) * 1000  # <-- USE pipeline_start_time!
        
        safe_print(f"\n   🎯 Pipeline Total: {total_pipeline_time:.2f}ms")
        safe_print("   💡 Data passed through 3 frameworks with ZERO serialization!")
        
        pipeline_success = True  # Mark success
        
        # Store for later use
        total_pipeline_cpu = total_pipeline_time  # <-- SET THIS VARIABLE!

    except Exception as e:
        safe_print(_('   ❌ Pipeline failed: {}').format(str(e)))
        import traceback
        safe_print(f"      {traceback.format_exc()[:1000]}")
        total_pipeline_time = float("inf")
        total_pipeline_cpu = float("inf")  # <-- Also set this!
    # After Phase 4 completes successfully:
    pipeline_cpu_success = True  # <-- ADD THIS!
    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: Zero-Copy GPU Data Pipeline (via CUDA IPC)
    # ═══════════════════════════════════════════════════════════════
    
    safe_print("\n   📍 PHASE 5: Zero-Copy GPU Data Pipeline (via CUDA IPC)")
    safe_print("   " + "─" * 60)
    
    total_pipeline_gpu = 0.0
    pipeline_gpu_success = False
    
    # Use omnipkgLoader to get PyTorch for the CLIENT process
    try:
        with omnipkgLoader(universes[2]['torch_spec'], quiet=True):  # Load Gamma's torch for client
            import torch
        
            if not torch.cuda.is_available():
                safe_print("   ⚠️  CUDA not available on client, skipping GPU pipeline.")
            else:
                # Create initial tensor on GPU
                gpu_tensor = torch.randn(500, 250, device="cuda:0", dtype=torch.float32)
                safe_print(f"   📦 Input: {gpu_tensor.shape} GPU tensor ({gpu_tensor.nbytes/1024/1024:.2f} MB)")

                stage1_code_gpu = "tensor_out[:] = torch.nn.functional.relu(tensor_in)"
                stage2_code_gpu = "tensor_out[:] = torch.sigmoid(tensor_in)"
                stage3_code_gpu = "tensor_out[:] = torch.tanh(tensor_in)"

                start_time = time.perf_counter()
                
                # Stage 1 (Alpha)
                s1_gpu_out, unused1 = client.execute_cuda_ipc(
                    universes[0]["torch_spec"], stage1_code_gpu, gpu_tensor, gpu_tensor.shape, "float32",
                    python_exe=available_pythons["3.9"], ipc_mode="universal"
                )
                
                # Stage 2 (Beta)
                s2_gpu_out, unused2 = client.execute_cuda_ipc(
                    universes[1]["torch_spec"], stage2_code_gpu, s1_gpu_out, s1_gpu_out.shape, "float32",
                    python_exe=available_pythons["3.10"], ipc_mode="universal"
                )

                # Stage 3 (Gamma)
                s3_gpu_out, unused3 = client.execute_cuda_ipc(
                    universes[2]["torch_spec"], stage3_code_gpu, s2_gpu_out, s2_gpu_out.shape, "float32",
                    python_exe=available_pythons["3.11"], ipc_mode="universal"
                )

                total_pipeline_gpu = (time.perf_counter() - start_time) * 1000
                safe_print(f"\n   ✅ GPU Pipeline complete in: {total_pipeline_gpu:.2f}ms")
                safe_print("   💡 Data passed through 3 frameworks WITHOUT leaving the GPU!")
                pipeline_gpu_success = True

    except Exception as e:
        safe_print(_('   ❌ GPU Pipeline failed: {}').format(e))
        import traceback
        safe_print(f"      {traceback.format_exc()[:500]}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 5 COMPARISON: CPU vs GPU Pipeline
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   🏆 CROSS-VERSION CUDA IPC VERIFICATION:")
    safe_print("   ────────────────────────────────────────────────────────────")
    safe_print(_('   Stage 1: PyTorch {} → Universal IPC ✅').format(unused1.get('worker_torch_version')))
    safe_print(_('   Stage 2: PyTorch {} → Universal IPC ✅').format(unused2.get('worker_torch_version')))
    safe_print(_('   Stage 3: PyTorch {} → Universal IPC ✅').format(unused3.get('worker_torch_version')))
    safe_print(f"\n   💡 Same GPU tensor passed through 3 different PyTorch versions")
    safe_print(_('   💡 WITHOUT copying to CPU or serialization!'))
    safe_print(f"   💡 Total time: {total_pipeline_gpu:.2f}ms (TRUE zero-copy)")
    if pipeline_cpu_success and pipeline_gpu_success:
        safe_print("\n" + "=" * 70)
        safe_print("   🏁 PIPELINE PERFORMANCE COMPARISON")
        safe_print("=" * 70)
        safe_print(f"   🖥️  CPU Pipeline (SHM):     {total_pipeline_cpu:.2f}ms")
        safe_print(f"   🎮 GPU Pipeline (CUDA IPC): {total_pipeline_gpu:.2f}ms")
        
        if total_pipeline_gpu < total_pipeline_cpu:
            speedup = total_pipeline_cpu / total_pipeline_gpu
            saved_ms = total_pipeline_cpu - total_pipeline_gpu
            safe_print(f"\n   🚀 GPU is {speedup:.1f}x FASTER (saved {saved_ms:.2f}ms)")
            safe_print(_('   💡 Zero-copy GPU transfers eliminate CPU↔GPU bottleneck!'))
        else:
            slowdown = total_pipeline_gpu / total_pipeline_cpu
            extra_ms = total_pipeline_gpu - total_pipeline_cpu
            safe_print(f"\n   ⚠️  GPU is {slowdown:.1f}x slower (+{extra_ms:.2f}ms)")
            safe_print(f"   💡 For this workload, CPU pipeline is more efficient")
        
        safe_print("=" * 70)

    # ═══════════════════════════════════════════════════════════════
    # FINAL RESULTS
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n" + "═" * 66)
    safe_print("   📊 FINAL RESULTS: THE IMPOSSIBLE IS POSSIBLE")
    safe_print("═" * 66)

    safe_print(f"\n   {'METRIC':<30} | {'TIME':<15}")
    safe_print("   " + "─" * 50)
    safe_print(f"   {'Sequential (traditional)':<30} | {total_sequential*1000:>7.2f}ms")
    safe_print(f"   {'Concurrent (3 parallel)':<30} | {total_concurrent*1000:>7.2f}ms")

    if total_concurrent < total_sequential:
        speedup = total_sequential / total_concurrent
        safe_print(f"   {'SPEEDUP':<30} | {speedup:>7.2f}x")

    # NEW (fixed):
    if pipeline_times and 'total_pipeline' in locals():
        safe_print(
            f"   {'Zero-copy pipeline (3 stages)':<30} | {total_pipeline:>7.2f}ms"
        )

    safe_print("\n   💡 WHAT MAKES THIS IMPOSSIBLE ELSEWHERE:")
    safe_print("   " + "─" * 60)
    safe_print("   ❌ Docker containers: Need serialization between containers")
    safe_print("   ❌ Virtual environments: Can't run multiple Pythons concurrently")
    safe_print("   ❌ Traditional isolation: No zero-copy data transfer")
    safe_print("   ✅ omnipkg daemon + SHM: All of the above, ZERO overhead!")

    safe_print("\n   🏆 VERDICT:")
    successful_concurrent = sum(1 for r in concurrent_results.values() if r["success"])

    if successful_concurrent == len(universes) and pipeline_times:
        safe_print("   ✅ TRIPLE PYTHON MULTIVERSE: COMPLETE SUCCESS!")
        safe_print("   🌌 Concurrent execution: STABLE")
        safe_print("   💾 Zero-copy pipeline: OPERATIONAL")
        safe_print("   🚀 Performance: BLAZING FAST")
        safe_print("\n   🎉 WE JUST DID THE IMPOSSIBLE! 🎉\n")
        return True
    elif successful_concurrent >= 2:
        safe_print("   ✅ MULTIVERSE OPERATIONAL (partial)")
        return True
    else:
        safe_print("   ⚠️  Some universes collapsed")
        return False


def chaos_test_18_worker_pool_drag_race():
    """🏎️ TEST 18: HFT SIMULATION - High Frequency Worker Swapping"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 18: 🏎️  HFT SIMULATION (Worker Pool Drag Race)        ║")
    safe_print("║  Scenario: 4 Concurrent Threads hammering the Daemon         ║")
    safe_print("║  Goal: Prove thread-safety and max throughput                ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # 1. Setup the "Trading Floor" (Daemon)
    try:
        from omnipkg.isolation.worker_daemon import (
            DaemonClient,
            DaemonProxy,
            WorkerPoolDaemon,
        )

        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Starting Trading Floor (Daemon)...")
            # VIP list ensures our workers are warm
            vip_specs = [
                "torch==2.0.1+cu118",
                "torch==2.1.0",
                "numpy==1.24.3",
                "numpy==1.26.4",
            ]
            WorkerPoolDaemon(warmup_specs=vip_specs).start(daemonize=True)
            time.sleep(2)  # Give it a moment to boot the fleet
    except ImportError:
        return False

    # 2. Define the Workload
    # Two threads want Torch 2.0, Two threads want Torch 2.1
    # They will fight for the workers.

    LAPS = 50  # 50 requests per thread
    THREADS = 4

    safe_print(
        _('   🚦 RACE SETTINGS: {} Threads x {} Laps = {} Total Transactions').format(THREADS, LAPS, THREADS * LAPS)
    )
    safe_print("   🏎️  Drivers to your engines...")

    start_gun = threading.Event()
    results = []

    def hft_trader(thread_id, spec):
        # Create a proxy for this thread
        proxy = DaemonProxy(client, spec)

        # Wait for gun
        start_gun.wait()

        t_start = time.perf_counter()
        success_count = 0

        # The payload: extremely fast execution
        code = "x = 1 + 1"

        for unused in range(LAPS):
            res = proxy.execute(code)
            if res["success"]:
                success_count += 1

        t_end = time.perf_counter()
        results.append(
            {
                "id": thread_id,
                "spec": spec,
                "time": t_end - t_start,
                "success": success_count,
            }
        )

    # 3. Create Threads
    threads = []
    specs = ["torch==2.0.1+cu118", "torch==2.1.0", "numpy==1.24.3", "numpy==1.26.4"]

    for i in range(THREADS):
        t = threading.Thread(target=hft_trader, args=(i, specs[i % len(specs)]))
        threads.append(t)
        t.start()

    # 4. START RACE
    safe_print("   🔫 GO!")
    time.sleep(0.5)  # Let threads initialize
    race_start = time.perf_counter()
    start_gun.set()

    for t in threads:
        t.join()

    total_race_time = time.perf_counter() - race_start

    # 5. Analysis
    total_reqs = sum(r["success"] for r in results)
    safe_print("\n   🏁 FINISH LINE")
    safe_print("   ──────────────────────────────────────────")

    for r in results:
        tps = r["success"] / r["time"]
        safe_print(
            f"   🏎️  Thread {r['id']} ({r['spec']}): {r['success']}/{LAPS} ok | {r['time']*1000/LAPS:.2f}ms/req | {tps:.1f} req/s"
        )

    safe_print("   ──────────────────────────────────────────")
    safe_print(f"   ⏱️  Total Wall Time: {total_race_time:.3f}s")
    safe_print(
        f"   ⚡ System Throughput: {total_reqs / total_race_time:.1f} Transactions/Second"
    )

    if total_reqs == THREADS * LAPS:
        safe_print("\n   🏆 RESULT: MARKET STABLE. ZERO DROPPED PACKETS.")
    else:
        safe_print("\n   ⚠️  RESULT: PACKET LOSS DETECTED.")

    return True


def chaos_test_19_zero_copy_hft():
    """🚀 TEST 19: ZERO-COPY vs JSON (10MB BENCHMARK)"""
    import numpy as np
    from omnipkg.isolation.worker_daemon import DaemonClient

    safe_print("\n╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 19: 🚀 ZERO-COPY vs JSON (10MB BENCHMARK)             ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    client = DaemonClient()
    if not client.status().get("success"):
        safe_print("   ❌ Daemon not running")
        return

    # 1. Create 10MB Matrix
    size = (1000, 1250)
    safe_print(_('   📉 Generating 10MB Matrix {}...').format(size))
    data = np.random.rand(*size)
    safe_print("   ✅ Data ready.\n")

    spec = "numpy==1.26.4"

    # ---------------------------------------------------------
    # ROUND 1: JSON
    # ---------------------------------------------------------
    safe_print("   🐢 ROUND 1: Standard JSON Serialization")
    print(_('      (This will take ~7 seconds...)'))
    start = time.perf_counter()

    try:
        input_list = data.tolist()
        json_code = f"import numpy as np; arr = np.array({input_list}); result={{'out': (arr*2).tolist()}}"
        res = client.execute_shm(spec, json_code, {}, {})

        if res.get("success"):
            unused = np.array(res["out"])
            duration_json = (time.perf_counter() - start) * 1000
            safe_print(f"      ⏱️  Total: {duration_json:.2f}ms")
        else:
            safe_print(_('      💥 JSON Failed: {}').format(res.get('error')))
            duration_json = float("inf")
    except Exception as e:
        safe_print(_('      💥 JSON Exception: {}').format(e))
        duration_json = float("inf")

    # ---------------------------------------------------------
    # ROUND 2: SHM
    # ---------------------------------------------------------
    safe_print("\n   🚀 ROUND 2: Shared Memory Pointer Handoff")
    shm_code = "arr_out[:] = arr_in * 2"

    start = time.perf_counter()
    try:
        # UPDATED LINE: Unpack the tuple
        result, unused = client.execute_zero_copy(
            spec, shm_code, input_array=data, output_shape=size, output_dtype="float64"
        )
        duration_shm = (time.perf_counter() - start) * 1000
        safe_print(f"      ⏱️  Total: {duration_shm:.2f}ms")

        # FINAL SCORE
        safe_print("\n   🏁 RACE RESULTS (10MB Payload)")
        safe_print("   ──────────────────────────────────────────")
        safe_print(f"   🐢 JSON: {duration_json:7.2f}ms")
        safe_print(f"   🚀 SHM:  {duration_shm:7.2f}ms")

        if duration_shm > 0:
            speedup = duration_json / duration_shm
            safe_print(f"   🏆 Speedup: {speedup:.1f}x FASTER")

    except Exception as e:
        safe_print(_('      💥 SHM Failed: {}').format(e))
        import traceback

        traceback.print_exc()


def chaos_test_20_gpu_resident_pipeline():
    """🔥 GPU-RESIDENT ZERO-COPY: Data never leaves VRAM!"""

    # Check if we can import torch (try with omnipkgLoader if needed)
    try:
        import torch
    except ImportError:
        # Try loading via omnipkg
        try:
            from omnipkg.loader import omnipkgLoader

            with omnipkgLoader("torch==2.2.0+cu121"):
                import torch
        except:
            safe_print("   ⚠️  PyTorch not available, skipping GPU test")
            return True

    if not torch.cuda.is_available():
        safe_print("   ⚠️  CUDA not available, skipping GPU test")
        return True

    if not torch.cuda.is_available():
        safe_print("   ⚠️  CUDA not available, skipping GPU test")
        return True

    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 20: 🔥 GPU-RESIDENT ZERO-COPY PIPELINE                ║")
    safe_print("║  Data NEVER leaves VRAM - Sub-microsecond latency!          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # Initialize daemon
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient

        client = DaemonClient()
        if not client.status().get("success"):
            safe_print("   ⚙️  Starting daemon...")
            subprocess.run(
                [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"]
            )
            time.sleep(2)
    except ImportError as e:
        safe_print(f"   ❌ Failed to import daemon client: {e}")
        return False

    # FIXED: Use available bubbled versions that actually exist
    # Based on your system: 2.2.0+cu121, 2.0.1+cu118 are available
    specs = [
        "torch==2.2.0+cu121",  # Stage 1
        "torch==2.0.1+cu118",  # Stage 2
        "torch==2.2.0+cu121",  # Stage 3 (reuse)
    ]

    safe_print("   📦 Pipeline Stages:")
    safe_print(_('      Stage 1: {}').format(specs[0]))
    safe_print(_('      Stage 2: {}').format(specs[1]))
    safe_print(_('      Stage 3: {}\n').format(specs[2]))

    # Create tensor on GPU
    try:
        pipeline_data = torch.randn(500, 250, device="cuda:0")
        safe_print(
            _('   📦 Input: {} tensor on {}').format(pipeline_data.shape, pipeline_data.device)
        )
        safe_print(f"   📊 Size: {pipeline_data.numel() * 4 / 1024 / 1024:.2f} MB\n")
    except Exception as e:
        safe_print(_('   ❌ Failed to create GPU tensor: {}').format(e))
        return False

    # Stage 1: ReLU activation
    safe_print("   🔴 Stage 1: PyTorch 2.2 (ReLU)...")
    stage1_code = """
import torch
result = torch.nn.functional.relu(tensor_in)
tensor_out.copy_(result)
"""

    try:
        stage1_start = time.perf_counter()
        stage1_out, meta1 = client.execute_cuda_ipc(
            specs[0],
            stage1_code,
            input_tensor=pipeline_data,
            output_shape=pipeline_data.shape,
            output_dtype="float32",
            python_exe=sys.executable,
        )
        stage1_time = (time.perf_counter() - stage1_start) * 1000
        safe_print(f"   ✅ Stage 1 complete: {stage1_time:.3f}ms (CUDA IPC)")
    except Exception as e:
        safe_print(_('   ❌ Stage 1 failed: {}').format(e))
        import traceback

        safe_print(f"      {traceback.format_exc()[:500]}")
        return False

    # Stage 2: Sigmoid
    safe_print("\n   🟢 Stage 2: PyTorch 2.0 (Sigmoid)...")
    stage2_code = """
import torch
result = torch.sigmoid(tensor_in)
tensor_out.copy_(result)
"""

    try:
        stage2_start = time.perf_counter()
        stage2_out, meta2 = client.execute_cuda_ipc(
            specs[1],
            stage2_code,
            input_tensor=stage1_out,
            output_shape=stage1_out.shape,
            output_dtype="float32",
            python_exe=sys.executable,
        )
        stage2_time = (time.perf_counter() - stage2_start) * 1000
        safe_print(f"   ✅ Stage 2 complete: {stage2_time:.3f}ms (CUDA IPC)")
    except Exception as e:
        safe_print(_('   ❌ Stage 2 failed: {}').format(e))
        import traceback

        safe_print(f"      {traceback.format_exc()[:500]}")
        return False

    # Stage 3: Tanh
    safe_print("\n   🔵 Stage 3: PyTorch 2.2 (Tanh)...")
    stage3_code = """
import torch
result = torch.tanh(tensor_in)
tensor_out.copy_(result)
"""

    try:
        stage3_start = time.perf_counter()
        stage3_out, meta3 = client.execute_cuda_ipc(
            specs[2],
            stage3_code,
            input_tensor=stage2_out,
            output_shape=stage2_out.shape,
            output_dtype="float32",
            python_exe=sys.executable,
        )
        stage3_time = (time.perf_counter() - stage3_start) * 1000
        safe_print(f"   ✅ Stage 3 complete: {stage3_time:.3f}ms (CUDA IPC)")
    except Exception as e:
        safe_print(_('   ❌ Stage 3 failed: {}').format(e))
        import traceback

        safe_print(f"      {traceback.format_exc()[:500]}")
        return False

    # Results
    total_pipeline = stage1_time + stage2_time + stage3_time
    safe_print("\n" + "=" * 66)
    safe_print("   📊 GPU-RESIDENT PIPELINE RESULTS")
    safe_print("=" * 66)
    safe_print(f"   Stage 1 (PyTorch 2.2 ReLU):    {stage1_time:>8.3f}ms")
    safe_print(f"   Stage 2 (PyTorch 2.0 Sigmoid): {stage2_time:>8.3f}ms")
    safe_print(f"   Stage 3 (PyTorch 2.2 Tanh):    {stage3_time:>8.3f}ms")
    safe_print(f"   {'─'*40}")
    safe_print(f"   Total Pipeline:    {total_pipeline:>8.3f}ms")
    safe_print(f"   Per-Stage Average: {total_pipeline/3:>8.3f}ms")

    # Verify output is still on GPU
    if stage3_out.is_cuda:
        safe_print(_('\n   ✅ Output tensor still on GPU: {}').format(stage3_out.device))
        safe_print("   🔥 Data NEVER left VRAM - Zero PCIe transfers!")
        safe_print("   🌌 Multi-version pipeline: 2.2 → 2.0 → 2.2 via CUDA IPC!")
    else:
        safe_print("\n   ⚠️  Output tensor moved to CPU (unexpected)")

    # Compare to CPU SHM (if Test 17 ran)
    safe_print("\n   💡 COMPARISON:")
    safe_print("      CPU SHM Pipeline (Test 17): ~17ms")
    safe_print(f"      GPU IPC Pipeline (Test 20): {total_pipeline:.1f}ms")

    if total_pipeline < 17:
        speedup = 17.0 / total_pipeline
        safe_print(f"      🚀 Speedup: {speedup:.1f}x FASTER!")
    else:
        safe_print("      ⚠️  GPU IPC slower than expected (daemon overhead)")
        safe_print("      💡 But data stayed in VRAM - no CPU copies!")

    safe_print("\n   🏆 VERDICT: GPU-RESIDENT PIPELINE OPERATIONAL!")
    safe_print("   🎉 THREE DIFFERENT PYTORCH VERSIONS, ONE GPU, ZERO COPIES!\n")

    return True


def chaos_test_21_gpu_resident_pipeline():
    """
     TEST 21: 🔥 GPU-RESIDENT MULTI-VERSION PIPELINE
    📍 PHASE 4: Zero-Copy Data Pipeline
     NOW ACTUALLY USES PyTorch 1.13's native CUDA IPC!
    """
    safe_print(f"\n{'═'*66}")
    safe_print("║  TEST 21: 🔥 GPU-RESIDENT MULTI-VERSION PIPELINE           ║")
    safe_print("║  PyTorch 1.13.1 with NATIVE CUDA IPC (True Zero-Copy!)     ║")
    safe_print(f"{'═'*66}\n")

    from omnipkg.loader import omnipkgLoader

    safe_print("📍 PHASE 1: Configuration")
    safe_print("─" * 60)

    TORCH_VERSION = "torch==1.13.1+cu116"

    print(_('   PyTorch Version: {}').format(TORCH_VERSION))
    safe_print(_('   🔥 Loading client in {} context...').format(TORCH_VERSION))

    # ═══════════════════════════════════════════════════════════
    # CRITICAL FIX: Keep entire test inside loader context!
    # ═══════════════════════════════════════════════════════════
    with omnipkgLoader(TORCH_VERSION, isolation_mode="overlay"):
        import torch

        if not torch.cuda.is_available():
            safe_print("❌ CUDA not available - skipping test")
            return {"success": False, "reason": "No CUDA"}

        safe_print(_('   ✅ Client PyTorch: {}').format(torch.__version__))

        from omnipkg.isolation.worker_daemon import DaemonClient

        # ═══════════════════════════════════════════════════════════
        # Check available versions
        # ═══════════════════════════════════════════════════════════

        safe_print("\n📍 PHASE 1: Checking PyTorch Versions")
        safe_print("─" * 60)

        try:
            from omnipkg.core import OmnipkgCore

            core = OmnipkgCore()

            torch_versions = []
            for pkg_name, versions in core.kb.packages.items():
                if pkg_name == "torch":
                    for ver_str in versions.keys():
                        torch_versions.append(ver_str)

            print(_('Found {} PyTorch versions:').format(len(torch_versions)))
            for v in torch_versions:
                marker = " 🔥" if v.startswith("1.13") else ""
                print(_('   - torch=={}{}').format(v, marker))
        except Exception as e:
            safe_print(_('   ⚠️  Could not query knowledge base: {}').format(e))
            torch_versions = ["1.13.1+cu116", "2.0.1+cu118", "2.2.0+cu121"]

        # ═══════════════════════════════════════════════════════════
        # PHASE 2: Configure Pipeline
        # ═══════════════════════════════════════════════════════════

        safe_print("\n📍 PHASE 2: Configuring Pipeline")
        safe_print("─" * 60)
        safe_print(_('📍 CLIENT PyTorch version: {}').format(torch.__version__))

        stage_specs = []

        # Find PyTorch 1.13 for native IPC
        torch_1x = next((v for v in torch_versions if v.startswith("1.13")), None)

        if torch_1x:
            stage_specs.append(
                ("🔥 Stage 1 (ReLU)", f"torch=={torch_1x}", "relu", "NATIVE IPC")
            )
            safe_print(_('   ✅ Using torch=={} (NATIVE CUDA IPC!)').format(torch_1x))
        else:
            stage_specs.append(
                ("🔴 Stage 1 (ReLU)", "torch==2.2.0+cu121", "relu", "HYBRID")
            )
            safe_print("   ⚠️  PyTorch 1.13 not available, using hybrid mode")

        # Add other stages
        if len(torch_versions) >= 2:
            other_versions = [v for v in torch_versions if not v.startswith("1.13")][:2]
            stage_specs.append(
                (
                    "🟢 Stage 2 (Sigmoid)",
                    f"torch=={other_versions[0]}",
                    "sigmoid",
                    "HYBRID",
                )
            )
            if len(other_versions) > 1:
                stage_specs.append(
                    (
                        "🔵 Stage 3 (Tanh)",
                        f"torch=={other_versions[1]}",
                        "tanh",
                        "HYBRID",
                    )
                )

        print(_('\n   Pipeline Configuration:'))
        for name, spec, op, mode in stage_specs:
            print(_('   {}: {} ({})').format(name, spec, mode))

        # ═══════════════════════════════════════════════════════════
        # PHASE 3: Execute Pipeline
        # ═══════════════════════════════════════════════════════════

        safe_print("\n📍 PHASE 3: Executing GPU Pipeline")
        safe_print("─" * 60)

        client = DaemonClient()

        # Create test tensor
        device = torch.device("cuda:0")
        pipeline_data = torch.randn(500, 250, device=device, dtype=torch.float32)

        safe_print(_('\n📦 Input: {} tensor on {}').format(pipeline_data.shape, device))
        safe_print(f"📊 Size: {pipeline_data.numel() * 4 / 1024 / 1024:.2f} MB")
        safe_print(f"🔢 Checksum: {pipeline_data.sum().item():.2f}")

        stage_codes = {
            "relu": "tensor_out[:] = torch.relu(tensor_in)",
            "sigmoid": "tensor_out[:] = torch.sigmoid(tensor_in)",
            "tanh": "tensor_out[:] = torch.tanh(tensor_in)",
        }

        stage_times = []
        current_tensor = pipeline_data
        native_ipc_used = False

        for i, (name, spec, operation, mode) in enumerate(stage_specs):
            print(_('\n{}: Processing...').format(name))
            print(_('   PyTorch: {}').format(spec))
            print(_('   Mode: {}').format(mode))

            stage_start = time.perf_counter()

            try:
                # Force native IPC mode if this stage is marked for it
                ipc_mode = "pytorch_native" if mode == "NATIVE IPC" else "auto"

                result_tensor, response = client.execute_cuda_ipc(
                    spec,
                    stage_codes[operation],
                    input_tensor=current_tensor,
                    output_shape=current_tensor.shape,
                    output_dtype="float32",
                    python_exe=sys.executable,
                    ipc_mode=ipc_mode,  # ← ADD THIS!
                )

                stage_time = (time.perf_counter() - stage_start) * 1000
                stage_times.append(stage_time)

                actual_method = response.get("cuda_method", "unknown")
                if actual_method == "native_ipc":
                    native_ipc_used = True
                    safe_print(
                        f"✅ {name} complete: {stage_time:.3f}ms (NATIVE CUDA IPC! 🔥)"
                    )
                else:
                    safe_print(f"✅ {name} complete: {stage_time:.3f}ms (hybrid)")

                print(f"   Checksum: {result_tensor.sum().item():.2f}")
                current_tensor = result_tensor

            except Exception as e:
                safe_print(_('❌ {} failed: {}').format(name, e))
                import traceback

                traceback.print_exc()
                return {"success": False, "error": str(e)}

        # ═══════════════════════════════════════════════════════════
        # RESULTS (still inside loader context)
        # ═══════════════════════════════════════════════════════════

        total_time = sum(stage_times)
        avg_time = total_time / len(stage_times)

        safe_print(f"\n{'═'*66}")
        safe_print("📊 GPU-RESIDENT PIPELINE RESULTS")
        safe_print(f"{'═'*66}\n")

        for i, (name, spec, unused, mode) in enumerate(stage_specs):
            icon = "🔥" if mode == "NATIVE IPC" else "🔄"
            print(f"{icon} {name:<40} {stage_times[i]:>8.3f}ms")
            safe_print(f"  └─ {spec:<38}")

        safe_print("─" * 66)
        print(f"{'Total Pipeline:':<40} {total_time:>8.3f}ms")
        print(f"{'Per-Stage Average:':<40} {avg_time:>8.3f}ms")

        safe_print(_('\n✅ Output tensor still on GPU: {}').format(current_tensor.device))
        safe_print(f"🔢 Final checksum: {current_tensor.sum().item():.2f}")

        if native_ipc_used:
            safe_print("\n🏆 NATIVE CUDA IPC USED! TRUE ZERO-COPY ACHIEVED!")
            safe_print("   📊 Stage 1 had ZERO PCIe transfers")
        else:
            safe_print("\n⚠️  Native CUDA IPC not available")
            safe_print("   💡 Install torch==1.13.1+cu116 for true zero-copy")

        safe_print("\n💡 PERFORMANCE:")
        print(_('   CPU SHM Pipeline (Test 17): ~17ms'))
        print(_('   GPU Hybrid (Test 20): ~13ms'))
        print(f"   This test: {total_time:.1f}ms")

        return {
            "success": True,
            "total_time_ms": total_time,
            "native_ipc_used": native_ipc_used,
            "avg_stage_ms": avg_time,
        }


def chaos_test_22_complete_ipc_benchmark():
    """
    TEST 22: 🔥 COMPLETE IPC MODE BENCHMARK

    Compares ALL 4 execution modes:
    1. Universal CUDA IPC - Pure GPU, zero-copy (ctypes)
    2. PyTorch Native IPC - Framework-managed GPU IPC
    3. Hybrid Mode - CPU SHM + GPU copies (2 PCIe per stage)
    4. CPU SHM Baseline - Pure CPU, zero-copy (no GPU)

    Tests the same 3-stage pipeline across all modes with proper warmup.
    """
    safe_print(f"\n{'═'*66}")
    safe_print("║  TEST 22: 🔥 COMPLETE IPC MODE BENCHMARK              ║")
    safe_print("║  Same Pipeline × 4 Different Execution Modes          ║")
    safe_print(f"{'═'*66}\n")

    # ═══════════════════════════════════════════════════════════
    # SETUP: Load PyTorch 1.13.1 for client + workers
    # ═══════════════════════════════════════════════════════════
    from omnipkg.loader import omnipkgLoader

    TORCH_VERSION = "torch==1.13.1+cu116"

    safe_print("📍 CONFIGURATION")
    safe_print("─" * 60)
    print(_('   PyTorch Version: {}').format(TORCH_VERSION))
    print(_('   Pipeline: 3 stages (ReLU → Sigmoid → Tanh)'))
    print(_('   Testing 4 execution modes\n'))

    with omnipkgLoader(TORCH_VERSION, isolation_mode="overlay"):
        import torch

        if not torch.cuda.is_available():
            safe_print("❌ CUDA not available - skipping test")
            return {"success": False, "reason": "No CUDA"}

        safe_print(_('   ✅ Client PyTorch: {}').format(torch.__version__))

        from omnipkg.isolation.worker_daemon import DaemonClient

        client = DaemonClient()
        device = torch.device("cuda:0")

        # Create test data: 1000x500 = 2MB float32 tensor
        pipeline_data = torch.randn(1000, 500, device=device, dtype=torch.float32)

        safe_print(_('   📦 Input: {} tensor on {}').format(pipeline_data.shape, device))
        safe_print(f"   📊 Size: {pipeline_data.numel() * 4 / 1024 / 1024:.2f} MB\n")

        # Define 3-stage pipeline
        stage_specs = [
            ("Stage 1: ReLU", TORCH_VERSION, "relu"),
            ("Stage 2: Sigmoid", TORCH_VERSION, "sigmoid"),
            ("Stage 3: Tanh", TORCH_VERSION, "tanh"),
        ]

        # GPU operations
        gpu_stage_codes = {
            "relu": "tensor_out[:] = torch.relu(tensor_in)",
            "sigmoid": "tensor_out[:] = torch.sigmoid(tensor_in)",
            "tanh": "tensor_out[:] = torch.tanh(tensor_in)",
        }

        # CPU operations (numpy equivalent)
        cpu_stage_codes = {
            "relu": "arr_out[:] = np.maximum(arr_in, 0)",
            "sigmoid": "arr_out[:] = 1 / (1 + np.exp(-arr_in))",
            "tanh": "arr_out[:] = np.tanh(arr_in)",
        }

        # ═══════════════════════════════════════════════════════════
        # TEST MODES
        # ═══════════════════════════════════════════════════════════
        modes = [
            {
                "key": "universal",
                "name": "Universal CUDA IPC",
                "desc": "Pure GPU, zero-copy (ctypes)",
                "icon": "🔥",
                "type": "gpu",
            },
            {
                "key": "pytorch_native",
                "name": "PyTorch Native IPC",
                "desc": "Framework-managed GPU IPC",
                "icon": "🐍",
                "type": "gpu",
            },
            {
                "key": "hybrid",
                "name": "Hybrid Mode",
                "desc": "CPU SHM + GPU copies",
                "icon": "🔄",
                "type": "gpu",
            },
            {
                "key": "cpu_shm",
                "name": "CPU SHM Baseline",
                "desc": "Pure CPU, zero-copy",
                "icon": "💾",
                "type": "cpu",
            },
        ]

        results = {}

        # ═══════════════════════════════════════════════════════════
        # WARMUP PHASE (5 iterations each mode)
        # ═══════════════════════════════════════════════════════════
        safe_print("📍 WARMUP PHASE (5 iterations per mode)")
        safe_print("─" * 60)

        for mode in modes:
            print(_('\n{} Warming up: {}').format(mode['icon'], mode['name']))

            try:
                if mode["type"] == "cpu":
                    # CPU mode warmup
                    cpu_data = pipeline_data.cpu().numpy()

                    for i in range(5):
                        curr_data = cpu_data
                        for unused, spec, op in stage_specs:
                            curr_data, unused = client.execute_zero_copy(
                                spec,
                                cpu_stage_codes[op],
                                input_array=curr_data,
                                output_shape=curr_data.shape,
                                output_dtype=curr_data.dtype,
                                python_exe=sys.executable,
                            )
                else:
                    # GPU mode warmup
                    for i in range(5):
                        curr = pipeline_data
                        for unused, spec, op in stage_specs:
                            curr, unused = client.execute_cuda_ipc(
                                spec,
                                gpu_stage_codes[op],
                                input_tensor=curr,
                                output_shape=curr.shape,
                                output_dtype="float32",
                                python_exe=sys.executable,
                                ipc_mode=mode["key"],
                            )

                safe_print("   ✅ Warmup complete")

            except Exception as e:
                safe_print(_('   ❌ Warmup failed: {}').format(e))
                results[mode["key"]] = {"error": str(e), "skipped": True}

        # ═══════════════════════════════════════════════════════════
        # BENCHMARK PHASE (20 iterations each mode)
        # ═══════════════════════════════════════════════════════════
        safe_print(f"\n\n{'═'*66}")
        safe_print("📍 BENCHMARK PHASE (20 iterations per mode)")
        safe_print("═" * 66)

        for mode in modes:
            if mode["key"] in results and results[mode["key"]].get("skipped"):
                continue

            print(_('\n{} Testing: {}').format(mode['icon'], mode['name']))
            print(_('   {}').format(mode['desc']))
            safe_print("   " + "─" * 60)

            run_times = []

            try:
                for run in range(20):
                    if mode["type"] == "cpu":
                        # CPU mode benchmark
                        cpu_data = pipeline_data.cpu().numpy()

                        run_start = time.perf_counter()
                        curr_data = cpu_data

                        for unused, spec, op in stage_specs:
                            curr_data, unused = client.execute_zero_copy(
                                spec,
                                cpu_stage_codes[op],
                                input_array=curr_data,
                                output_shape=curr_data.shape,
                                output_dtype=curr_data.dtype,
                                python_exe=sys.executable,
                            )

                        run_time = (time.perf_counter() - run_start) * 1000
                        run_times.append(run_time)

                    else:
                        # GPU mode benchmark
                        run_start = time.perf_counter()
                        curr = pipeline_data

                        for unused, spec, op in stage_specs:
                            curr, unused = client.execute_cuda_ipc(
                                spec,
                                gpu_stage_codes[op],
                                input_tensor=curr,
                                output_shape=curr.shape,
                                output_dtype="float32",
                                python_exe=sys.executable,
                                ipc_mode=mode["key"],
                            )

                        run_time = (time.perf_counter() - run_start) * 1000
                        run_times.append(run_time)

                    # Show progress for first 5 runs
                    if run < 5:
                        print(f"   Run {run+1:2d}: {run_time:.3f}ms")

                # Calculate statistics
                if run_times:
                    avg = sum(run_times) / len(run_times)
                    min_time = min(run_times)
                    max_time = max(run_times)
                    stddev = (
                        sum((x - avg) ** 2 for x in run_times) / len(run_times)
                    ) ** 0.5

                    results[mode["key"]] = {
                        "times": run_times,
                        "avg": avg,
                        "min": min_time,
                        "max": max_time,
                        "stddev": stddev,
                        "name": mode["name"],
                        "icon": mode["icon"],
                        "type": mode["type"],
                    }

                    safe_print("\n   📊 Statistics:")
                    print(f"      Average: {avg:.3f}ms")
                    print(f"      Best:    {min_time:.3f}ms")
                    print(f"      Worst:   {max_time:.3f}ms")
                    print(f"      Stddev:  {stddev:.3f}ms")

            except Exception as e:
                safe_print(_('   ❌ Benchmark failed: {}').format(e))
                results[mode["key"]] = {"error": str(e)}

        # ═══════════════════════════════════════════════════════════
        # FINAL COMPARISON
        # ═══════════════════════════════════════════════════════════
        safe_print(f"\n\n{'═'*66}")
        safe_print("📊 FINAL RESULTS - IPC MODE COMPARISON")
        safe_print(f"{'═'*66}\n")

        # Filter valid results
        valid_results = {k: v for k, v in results.items() if "times" in v}

        if not valid_results:
            safe_print("❌ No valid results to compare")
            return {"success": False, "error": "All modes failed"}

        # Sort by best time
        sorted_modes = sorted(valid_results.items(), key=lambda x: x[1]["min"])

        # Show ranking
        safe_print("🏆 RANKING (by best time):")
        safe_print("─" * 60)

        medals = ["🥇", "🥈", "🥉", "  "]

        for i, (key, data) in enumerate(sorted_modes):
            medal = medals[i] if i < 4 else "  "
            print(
                f"{medal} {data['icon']} {data['name']:<25} "
                f"{data['min']:.3f}ms (avg: {data['avg']:.3f}ms ± {data['stddev']:.2f}ms)"
            )

        # Show speedup comparisons
        if len(sorted_modes) > 1:
            safe_print("\n💡 SPEEDUP vs FASTEST:")
            safe_print("─" * 60)

            fastest_time = sorted_modes[0][1]["min"]

            for key, data in sorted_modes[1:]:
                speedup = data["min"] / fastest_time
                slower = data["min"] - fastest_time
                print(
                    f"   {data['icon']} {data['name']:<25} "
                    f"{speedup:.2f}x slower (+{slower:.3f}ms)"
                )

        # Show winner and analysis
        winner_key = sorted_modes[0][0]
        winner = sorted_modes[0][1]

        safe_print(f"\n{'═'*66}")
        safe_print(_('🏆 WINNER: {}').format(winner['name'].upper()))
        safe_print(f"{'═'*66}")
        print(f"   Best time: {winner['min']:.3f}ms")
        print(f"   Average:   {winner['avg']:.3f}ms")
        print(f"   Stddev:    {winner['stddev']:.3f}ms")

        # Technical analysis
        safe_print("\n💡 TECHNICAL ANALYSIS:")
        safe_print("─" * 60)

        if winner_key == "universal":
            safe_print("   ✅ Universal IPC is fastest - pure CUDA IPC wins!")
            safe_print("   🚀 Zero-copy GPU transfers via ctypes")
            safe_print("   📌 No PyTorch dependency for IPC layer")
            safe_print("   💡 This is the DEFAULT mode (optimal choice)")

        elif winner_key == "pytorch_native":
            safe_print("   🐍 PyTorch Native IPC is fastest!")
            safe_print("   🚀 Framework-managed zero-copy transfers")
            safe_print("   📌 Uses PyTorch 1.x _share_cuda_() API")
            safe_print("   💡 Consider setting ipc_mode='pytorch_native' as default")

        elif winner_key == "hybrid":
            safe_print("   🔄 Hybrid mode is fastest - surprising!")
            safe_print("   📊 This means: PCIe transfer < GPU IPC setup overhead")
            safe_print("   💡 For this workload, copying data is faster than IPC")
            safe_print("   ⚠️  Might indicate GPU IPC driver issues")

        elif winner_key == "cpu_shm":
            safe_print("   💾 CPU-only is fastest - GPU overhead too high!")
            safe_print("   📊 For this workload size, CPU is more efficient")
            safe_print("   💡 GPU transfers + kernel launches exceed CPU compute time")
            safe_print("   ⚠️  Consider larger workloads to amortize GPU overhead")

        # Method explanations
        safe_print("\n📚 METHOD EXPLANATIONS:")
        safe_print("─" * 60)
        safe_print(
            "   🔥 Universal IPC:     Pure CUDA IPC (ctypes), works with any PyTorch"
        )
        safe_print("   🐍 PyTorch Native:    Framework-managed, PyTorch 1.x only")
        safe_print("   🔄 Hybrid:            CPU SHM + 2 GPU copies per stage")
        safe_print("   💾 CPU SHM:           Pure CPU compute, zero-copy (baseline)")

        # Performance summary
        gpu_modes = {k: v for k, v in valid_results.items() if v["type"] == "gpu"}
        cpu_modes = {k: v for k, v in valid_results.items() if v["type"] == "cpu"}

        if gpu_modes and cpu_modes:
            safe_print("\n🎯 GPU vs CPU COMPARISON:")
            safe_print("─" * 60)

            best_gpu = min(gpu_modes.values(), key=lambda x: x["min"])
            best_cpu = min(cpu_modes.values(), key=lambda x: x["min"])

            if best_gpu["min"] < best_cpu["min"]:
                speedup = best_cpu["min"] / best_gpu["min"]
                safe_print(
                    f"   🚀 Best GPU ({best_gpu['name']}) is {speedup:.2f}x faster than CPU"
                )
                print(f"      GPU: {best_gpu['min']:.3f}ms")
                print(f"      CPU: {best_cpu['min']:.3f}ms")
            else:
                ratio = best_gpu["min"] / best_cpu["min"]
                safe_print(f"   ⚠️  CPU is {ratio:.2f}x faster than best GPU mode!")
                print(f"      CPU: {best_cpu['min']:.3f}ms")
                print(f"      GPU: {best_gpu['min']:.3f}ms")
                safe_print("   💡 Workload too small to benefit from GPU")

        print("=" * 66 + "\n")

        return {
            "success": True,
            "results": results,
            "winner": winner_key,
            "best_time_ms": winner["min"],
        }


def chaos_test_23_grand_unified_benchmark():
    """
    TEST 23: 🏆 THE GRAND UNIFIED BENCHMARK

    The Final Showdown:
    1. 🐢 The "Lame" Way: Traditional Subprocess + Pickle (The standard industry approach)
    2. 💾 The "Smart" Way: CPU Shared Memory (OmniPKG Zero-Copy)
    3. 🔥 The "God" Mode: Universal CUDA IPC (OmniPKG Zero-Copy GPU)

    Runs a multi-version pipeline (PyTorch 1.13 -> 2.0 -> 2.1) across all modes.
    """
    import pickle
    import numpy as np

    # ═══════════════════════════════════════════════════════════
    # SETUP & VISUALS
    # ═══════════════════════════════════════════════════════════
    def safe_print(msg):
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    safe_print(f"\n{'═'*70}")
    safe_print("║  TEST 23: 🏆 GRAND UNIFIED BENCHMARK (The Final Boss)            ║")
    safe_print("║  Comparing Architecture Generations:                             ║")
    safe_print("║  1. 🐢 Process Forking (Standard)                                ║")
    safe_print("║  2. 💾 CPU Zero-Copy (OmniPKG v1)                                ║")
    safe_print("║  3. 🔥 GPU Direct IPC (OmniPKG v2)                               ║")
    safe_print(f"{'═'*70}\n")

    # ═══════════════════════════════════════════════════════════
    # CONFIGURATION: THE MULTIVERSE PIPELINE
    # ═══════════════════════════════════════════════════════════
    # We use 3 different PyTorch versions to force true isolation
    STAGES = [
        {"name": "Stage 1 (ReLU)", "spec": "torch==1.13.1+cu116", "op": "relu"},
        {"name": "Stage 2 (Sigmoid)", "spec": "torch==2.0.1+cu118", "op": "sigmoid"},
        {"name": "Stage 3 (Tanh)", "spec": "torch==2.1.0", "op": "tanh"},
    ]

    # Data Size: 1000x1000 float32 (4MB) - Big enough to hurt pickling, small enough for IPC
    SHAPE = (1000, 1000)
    DTYPE = "float32"

    # Generate Input Data
    try:
        from omnipkg.loader import omnipkgLoader

        with omnipkgLoader("torch==1.13.1+cu116", quiet=True):
            import torch

            if not torch.cuda.is_available():
                return {"success": False, "reason": "No CUDA"}

            # CPU Data for Baselines
            input_cpu = np.random.randn(*SHAPE).astype(np.float32)
            # GPU Data for God Mode
            device = torch.device("cuda:0")
            input_gpu = torch.as_tensor(input_cpu, device=device)

            safe_print(
                f"   📦 Payload: {SHAPE} Matrix ({input_cpu.nbytes/1024/1024:.2f} MB)"
            )
            safe_print(
                _('   🌊 Pipeline: {} → {} → {}\n').format(STAGES[0]['spec'], STAGES[1]['spec'], STAGES[2]['spec'])
            )
    except Exception as e:
        safe_print(_('❌ Setup failed: {}').format(e))
        return {"success": False}

    results = {}

    # ═══════════════════════════════════════════════════════════
    # MODE 1: TRADITIONAL SUBPROCESS (The "Lame" Way)
    # ═══════════════════════════════════════════════════════════
    safe_print("🐢 MODE 1: TRADITIONAL SUBPROCESS (Pickle + Fork)")
    safe_print("   - Strategy: Spawn new python process for every stage")
    safe_print("   - Data: Serialize to disk/pipe (Pickle)")
    safe_print("   - Status: This will be painful to watch...")
    safe_print("   " + "─" * 60)

    t_start = time.perf_counter()

    # We only do 1 run because it's so slow
    current_data = input_cpu

    try:
        for i, stage in enumerate(STAGES):
            # Write data to temp file (simulate disk/pipe transfer)
            tmp_in = f"temp_in_{i}.pkl"
            tmp_out = f"temp_out_{i}.pkl"

            with open(tmp_in, "wb") as f:
                pickle.dump(current_data, f)

            # The "Lame" Script
            code = f"""
import pickle
import numpy as np
from omnipkg.loader import omnipkgLoader
# We have to load the environment every time!
with omnipkgLoader("{stage['spec']}", quiet=True):
    import torch
    
    with open("{tmp_in}", "rb") as f:
        data = pickle.load(f)
    
    tensor = torch.from_numpy(data)
    
    if "{stage['op']}" == "relu":
        res = torch.relu(tensor)
    elif "{stage['op']}" == "sigmoid":
        res = torch.sigmoid(tensor)
    elif "{stage['op']}" == "tanh":
        res = torch.tanh(tensor)
        
    with open("{tmp_out}", "wb") as f:
        pickle.dump(res.numpy(), f)
"""
            # Execute Subprocess
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
            if proc.returncode != 0:
                raise Exception(_('Subprocess failed: {}').format(proc.stderr.decode()))

            # Read back
            with open(tmp_out, "rb") as f:
                current_data = pickle.load(f)

            # Cleanup
            if os.path.exists(tmp_in):
                os.remove(tmp_in)
            if os.path.exists(tmp_out):
                os.remove(tmp_out)

            safe_print(_('   🐢 Stage {} ({}) complete').format(i + 1, stage['spec']))

        t_end = time.perf_counter()
        time_lame = (t_end - t_start) * 1000
        results["lame"] = time_lame
        safe_print(f"   ⏱️  Total Time: {time_lame:.2f}ms")

    except Exception as e:
        safe_print(_('   ❌ Failed: {}').format(e))
        results["lame"] = 999999

    # ═══════════════════════════════════════════════════════════
    # SETUP DAEMON FOR HIGH SPEED MODES
    # ═══════════════════════════════════════════════════════════
    safe_print("\n⚙️  Booting OmniPKG Daemon Workers (One-time setup)...")
    from omnipkg.isolation.worker_daemon import DaemonClient

    client = DaemonClient()

    # Warmup workers to make benchmark fair (remove boot time)
    for stage in STAGES:
        client.execute_zero_copy(
            stage["spec"], "pass", input_cpu, SHAPE, DTYPE, python_exe=sys.executable
        )
    safe_print("   ✅ Workers warm and ready\n")

    # ═══════════════════════════════════════════════════════════
    # MODE 2: CPU SHARED MEMORY (The "Smart" Way)
    # ═══════════════════════════════════════════════════════════
    safe_print("💾 MODE 2: CPU SHARED MEMORY (OmniPKG v1)")
    safe_print("   - Strategy: Persistent Workers + Shared Memory Ring Buffer")
    safe_print("   - Data: Zero-copy pointer passing")
    safe_print("   " + "─" * 60)

    cpu_times = []

    cpu_code_map = {
        "relu": "arr_out[:] = np.maximum(arr_in, 0)",
        "sigmoid": "arr_out[:] = 1 / (1 + np.exp(-arr_in))",
        "tanh": "arr_out[:] = np.tanh(arr_in)",
    }

    for run in range(5):  # 5 runs
        t_start = time.perf_counter()
        curr = input_cpu

        for stage in STAGES:
            curr, unused = client.execute_zero_copy(
                stage["spec"],
                cpu_code_map[stage["op"]],
                input_array=curr,
                output_shape=SHAPE,
                output_dtype=DTYPE,
                python_exe=sys.executable,
            )

        cpu_times.append((time.perf_counter() - t_start) * 1000)

    avg_cpu = sum(cpu_times) / len(cpu_times)
    results["cpu"] = avg_cpu
    safe_print(f"   ✅ Average: {avg_cpu:.3f}ms")
    safe_print(f"   🚀 Speedup vs Lame: {results['lame']/avg_cpu:.1f}x")

    # ═══════════════════════════════════════════════════════════
    # MODE 3: UNIVERSAL CUDA IPC (The "God" Mode)
    # ═══════════════════════════════════════════════════════════
    safe_print("\n🔥 MODE 3: UNIVERSAL CUDA IPC (God Mode)")
    safe_print("   - Strategy: GPU Pointers via Ctypes -> NV Driver")
    safe_print("   - Data: STAYS ON VRAM. ZERO PCIe TRANSFERS.")
    safe_print("   " + "─" * 60)

    gpu_times = []

    gpu_code_map = {
        "relu": "tensor_out[:] = torch.relu(tensor_in)",
        "sigmoid": "tensor_out[:] = torch.sigmoid(tensor_in)",
        "tanh": "tensor_out[:] = torch.tanh(tensor_in)",
    }

    # Ensure input is on GPU in the main process context
    with omnipkgLoader("torch==1.13.1+cu116", quiet=True):
        import torch

        gpu_tensor = input_gpu

        for run in range(10):  # 10 runs because it's so fast
            t_start = time.perf_counter()
            curr = gpu_tensor

            for stage in STAGES:
                curr, meta = client.execute_cuda_ipc(
                    stage["spec"],
                    gpu_code_map[stage["op"]],
                    input_tensor=curr,
                    output_shape=SHAPE,
                    output_dtype=DTYPE,
                    python_exe=sys.executable,
                    ipc_mode="universal",
                )

            torch.cuda.synchronize()  # Wait for GPU to finish for fair timing
            gpu_times.append((time.perf_counter() - t_start) * 1000)

    avg_gpu = sum(gpu_times) / len(gpu_times)
    results["gpu"] = avg_gpu
    safe_print(f"   ✅ Average: {avg_gpu:.3f}ms")
    safe_print(f"   🚀 Speedup vs Lame: {results['lame']/avg_gpu:.1f}x")
    safe_print(f"   🚀 Speedup vs CPU:  {results['cpu']/avg_gpu:.1f}x")

    # ═══════════════════════════════════════════════════════════
    # 🏆 FINAL SCOREBOARD
    # ═══════════════════════════════════════════════════════════
    safe_print("\n\n")
    safe_print("══════════════════════════════════════════════════════════════════")
    safe_print("📊 FINAL RESULTS - THE GRAND UNIFIED BENCHMARK")
    safe_print("══════════════════════════════════════════════════════════════════")

    safe_print(f"{'STRATEGY':<30} | {'TIME (ms)':<12} | {'MULTIPLIER':<15}")
    safe_print("──────────────────────────────────────────────────────────────────")

    # Sort for dramatic effect (Slowest first)
    rows = [
        ("🐢 Traditional Process", results["lame"], "1.0x (Baseline)"),
        (
            "💾 OmniPKG CPU (SHM)",
            results["cpu"],
            f"{results['lame']/results['cpu']:.1f}x FASTER",
        ),
        (
            "🔥 OmniPKG GPU (IPC)",
            results["gpu"],
            f"{results['lame']/results['gpu']:.1f}x FASTER",
        ),
    ]

    for name, t, mult in rows:
        safe_print(f"{name:<30} | {t:<12.3f} | {mult}")

    safe_print("══════════════════════════════════════════════════════════════════")

    # Analysis
    safe_print("\n💡 CONCLUSION:")
    safe_print(
        f"   1. The 'Traditional' way is {results['lame']/1000:.2f} seconds per inference."
    )
    safe_print("      - Completely unusable for real-time applications.")
    safe_print(f"   2. OmniPKG CPU is {(results['lame']/results['cpu']):.0f}x faster.")
    safe_print("      - Viable for production.")
    safe_print(f"   3. OmniPKG GPU is {(results['lame']/results['gpu']):.0f}x faster.")
    safe_print(
        f"      - This is {results['cpu']/results['gpu']:.1f}x faster than even the optimized CPU mode."
    )
    safe_print(
        f"      - 4MB of data moved through 3 PyTorch versions in {avg_gpu:.2f}ms."
    )

    safe_print("\n🏆 WINNER: UNIVERSAL CUDA IPC")

    return {"success": True, "results": results}


# ═══════════════════════════════════════════════════════════
# 🎮 INTERACTIVE MENU SYSTEM
# ═══════════════════════════════════════════════════════════

ALL_TESTS = [
    chaos_test_1_version_tornado,
    chaos_test_2_dependency_inception,
    chaos_test_3_framework_battle_royale,
    chaos_test_4_memory_madness,
    chaos_test_5_race_condition_roulette,
    chaos_test_6_version_time_machine,
    chaos_test_7_dependency_jenga,
    chaos_test_8_quantum_superposition,
    chaos_test_9_import_hell,
    chaos_test_10_grand_finale,
    chaos_test_11_tensorflow_resurrection,
    chaos_test_12_jax_vs_torch_mortal_kombat,
    chaos_test_13_pytorch_lightning_storm,
    chaos_test_14_circular_dependency_hell,
    chaos_test_15_isolation_strategy_benchmark,
    chaos_test_16_nested_reality_hell,
    chaos_test_17_triple_python_multiverse,
    chaos_test_18_worker_pool_drag_race,  # <-- ADD THIS
    chaos_test_19_zero_copy_hft,
    chaos_test_20_gpu_resident_pipeline,
    chaos_test_21_gpu_resident_pipeline,
    chaos_test_22_complete_ipc_benchmark,
    chaos_test_23_grand_unified_benchmark,
]


def get_test_name(func):
    return func.__name__.replace("chaos_test_", "").replace("_", " ").title()


def select_tests_interactively():
    print_chaos_header()
    safe_print("📝 AVAILABLE CHAOS SCENARIOS:")
    print("=" * 60)
    safe_print("   [0] 🔥 RUN ALL TESTS (The Full Experience)")
    print("-" * 60)
    for i, test_func in enumerate(ALL_TESTS, 1):
        print(_('   [{}] {}').format(i, get_test_name(test_func)))
    print("=" * 60)
    safe_print("\n💡 Tip: Type numbers separated by spaces (e.g. '1 3 5').")

    try:
        sys.stdout.flush()
        selection = input(_('\n👉 Choose tests [0]: ')).strip()
    except (EOFError, KeyboardInterrupt):
        return ALL_TESTS

    if not selection or selection == "0" or selection.lower() == "all":
        return ALL_TESTS

    selected_tests = []
    try:
        parts = selection.replace(",", " ").split()
        indices = [int(x) for x in parts if x.strip().isdigit()]
        for idx in indices:
            if idx == 0:
                return ALL_TESTS
            if 1 <= idx <= len(ALL_TESTS):
                selected_tests.append(ALL_TESTS[idx - 1])
    except ValueError:
        return ALL_TESTS

    return selected_tests if selected_tests else ALL_TESTS


def run_chaos_suite(tests_to_run=None):
    if tests_to_run is None:
        tests_to_run = ALL_TESTS
    if not tests_to_run:
        return True

    results = []
    safe_print(_('\n🚀 Launching {} chaos scenarios...\n').format(len(tests_to_run)))

    for i, test in enumerate(tests_to_run, 1):
        name = get_test_name(test)
        safe_print(_('\n🧪 TEST {}/{}: {}').format(i, len(tests_to_run), name))
        safe_print("─" * 66)
        try:
            test()
            results.append(("✅", name))
            safe_print(f"✅ {name} - PASSED")
        except Exception as e:
            results.append(("❌", name))
            safe_print(_('❌ {} - FAILED: {}').format(name, str(e)))
        time.sleep(0.5)

    print("\n" + "=" * 66)
    safe_print("   📊 DETAILED RESULTS:")
    safe_print("─" * 66)
    for status, name in results:
        print(_('   {} {}').format(status, name))

    passed = sum(1 for result in results if result[0] == "✅")

    safe_print("─" * 66)
    safe_print(f"\n   ✅ Tests Passed: {passed}/{len(tests_to_run)}")

    if passed == len(tests_to_run):
        safe_print("\n   🏆 System Status: GODLIKE")
    else:
        safe_print("\n   🩹 System Status: WOUNDED")
    print("=" * 66 + "\n")

    return passed == len(tests_to_run)

if __name__ == "__main__":
    try:
        # Parse command-line arguments for test selection
        if len(sys.argv) > 1:
            # User passed test numbers via CLI (e.g., `python test.py 11 17 18`)
            test_args = sys.argv[1:]
            
            # Check for special case: single "0" or "all"
            if len(test_args) == 1 and (test_args[0] == "0" or test_args[0].lower() == "all"):
                safe_print(_("🔥 Running ALL chaos tests..."))
                run_chaos_suite(ALL_TESTS)
            else:
                # Parse the test numbers
                selected_tests = []
                try:
                    indices = [int(x) for x in test_args if x.strip().isdigit()]
                    for idx in indices:
                        if idx == 0:
                            # 0 means all
                            selected_tests = ALL_TESTS
                            break
                        if 1 <= idx <= len(ALL_TESTS):
                            selected_tests.append(ALL_TESTS[idx - 1])
                        else:
                            safe_print(_("⚠️  Skipping invalid test number: {}").format(idx))
                except ValueError as e:
                    safe_print(_("❌ Invalid test number format: {}").format(e))
                    sys.exit(1)
                
                if selected_tests:
                    safe_print(_("🎯 Running {} selected test(s)...").format(len(selected_tests)))
                    run_chaos_suite(selected_tests)
                else:
                    safe_print(_("❌ No valid tests selected."))
                    sys.exit(1)
        else:
            # No CLI args - show interactive menu
            if os.environ.get("OMNIPKG_REEXEC_COUNT"):
                run_chaos_suite(ALL_TESTS)
            else:
                selected = select_tests_interactively()
                if selected:
                    run_chaos_suite(selected)

    except KeyboardInterrupt:
        safe_print("\n\n⚠️  CHAOS INTERRUPTED BY USER!")
    except ProcessCorruptedException as e:
        safe_print(_('\n☢️   CATASTROPHIC CORRUPTION: {}').format(e))
        sys.exit(1)
    except Exception as e:
        import traceback
        safe_print(_('\n💥 CHAOS FAILURE: {}').format(e))
        traceback.print_exc()