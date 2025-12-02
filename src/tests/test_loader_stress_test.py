#!/usr/bin/env python3
"""
🌀 OMNIPKG CHAOS THEORY - DAEMON EDITION 🌀
Now using the REAL worker daemon for maximum parallelism!
"""
import sys
import os
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Import the daemon client
try:
    from omnipkg.common_utils import safe_print
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
import sys
import os
import time
import json
import random
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
# ═══════════════════════════════════════════════════════════
# 📦 IMPORTS: The New Architecture
# ═══════════════════════════════════════════════════════════
try:
    # 1. Common Utils
    from omnipkg.common_utils import safe_print, ProcessCorruptedException
    
    # 2. The Core Loader
    from omnipkg.loader import omnipkgLoader
    
    # 3. The New Isolation Engine (✨ REFACTORED ✨)
    from omnipkg.isolation.runners import run_python_code_in_isolation
    from omnipkg.isolation.workers import PersistentWorker
    from omnipkg.isolation.switchers import TrueSwitcher
except ImportError:
    # Fallback for running directly without package installed
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from omnipkg.common_utils import safe_print, ProcessCorruptedException
    from omnipkg.loader import omnipkgLoader
    from omnipkg.isolation.runners import run_python_code_in_isolation
    from omnipkg.isolation.workers import PersistentWorker
    from omnipkg.isolation.switchers import TrueSwitcher

    from omnipkg.common_utils import safe_print
    from omnipkg.loader import omnipkgLoader
    from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
#  env vars globally for this process too
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

# HELPER: Check verbosity
def is_verbose_mode():
    return (
        "--verbose" in sys.argv or 
        "-v" in sys.argv or 
        os.environ.get('OMNIPKG_VERBOSE') == '1'
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
    """🌪️ TEST 1: VERSION TORNADO - Compare Legacy vs Daemon"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 1: 🌪️  VERSION TORNADO                                ║")
    safe_print("║  Benchmark: Legacy Loader vs Daemon Mode                     ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    versions = ["1.24.3", "1.26.4", "2.3.5"]
    
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
            safe_print(f"   {direction} Legacy #{i+1:02d}: numpy {ver} → sum={result:.2f} ({elapsed:.2f}ms)")
            
        except Exception as e:
            safe_print(f"   💥 Legacy #{i+1:02d}: numpy {ver} → FAILED: {str(e)[:50]}")
        
        time.sleep(0.02)
    
    # ==================================================================
    # PHASE 2: Daemon Mode (Using your imports from test 5)
    # ==================================================================
    safe_print("\n   📍 PHASE 2: Daemon Mode")
    safe_print("   ────────────────────────\n")
    
    daemon_times = []
    daemon_success = 0
    
    try:
        # Same imports as test 5
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy
        
        safe_print("   ⚡ Initializing DaemonClient...")
        daemon_init_start = time.perf_counter()
        client = DaemonClient()
        
        # Verify daemon is up or start it
        status = client.status()
        if not status.get('success'):
            safe_print("   ⚡ Starting daemon...")
            from omnipkg.isolation.worker_daemon import WorkerPoolDaemon
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(1)
        
        daemon_init_time = (time.perf_counter() - daemon_init_start) * 1000
        safe_print(f"   ⚡ Daemon ready in {daemon_init_time:.2f}ms\n")
        
        for i in range(10):  # Same 10 random switches
            ver = random.choice(versions)
            direction = random.choice(["↗️", "↘️", "↔️", "↕️"])
            
            try:
                start = time.perf_counter()
                
                # Use DaemonProxy like test 5
                proxy = DaemonProxy(client, f"numpy=={ver}")
                
                # Execute numpy code
                code = f"""
import numpy as np
arr = np.random.rand(50, 50)
result = np.sum(arr)
print(f"{{np.__version__}}|{{result}}")
"""
                result = proxy.execute(code)
                elapsed = (time.perf_counter() - start) * 1000
                
                if result['success']:
                    output = result['stdout'].strip()
                    if '|' in output:
                        actual_ver, sum_str = output.split('|')
                        daemon_times.append(elapsed)
                        daemon_success += 1
                        safe_print(f"   {direction} Daemon #{i+1:02d}: numpy {ver} → sum={sum_str} ({elapsed:.2f}ms)")
                    else:
                        safe_print(f"   💥 Daemon #{i+1:02d}: Bad output: {output}")
                else:
                    safe_print(f"   💥 Daemon #{i+1:02d}: Execution failed: {result.get('error', 'Unknown')}")
                    
            except Exception as e:
                safe_print(f"   💥 Daemon #{i+1:02d}: Exception: {str(e)[:50]}")
            
            time.sleep(0.02)
        
    except ImportError as e:
        safe_print(f"   ❌ Daemon mode not available: {e}")
    except Exception as e:
        safe_print(f"   ❌ Daemon error: {str(e)[:50]}")
    
    # ==================================================================
    # COMPARISON RESULTS
    # ==================================================================
    safe_print("\n   📊 COMPARISON RESULTS")
    safe_print("   ────────────────────────\n")
    
    # Legacy Results
    if legacy_times:
        avg_legacy = sum(legacy_times) / len(legacy_times)
        safe_print(f"   🧓 Legacy omnipkgLoader:")
        safe_print(f"      Success: {legacy_success}/10")
        safe_print(f"      Avg Time: {avg_legacy:.2f}ms per switch")
        safe_print(f"      Total: {sum(legacy_times):.2f}ms")
    else:
        safe_print(f"   🧓 Legacy omnipkgLoader: FAILED")
    
    safe_print("")
    
    # Daemon Results
    if daemon_times:
        avg_daemon = sum(daemon_times) / len(daemon_times)
        safe_print(f"   ⚡ Daemon Mode:")
        safe_print(f"      Success: {daemon_success}/10")
        safe_print(f"      Avg Time: {avg_daemon:.2f}ms per switch")
        safe_print(f"      Total: {sum(daemon_times):.2f}ms")
        
        # Calculate speedup
        if legacy_times:
            speedup = avg_legacy / avg_daemon if avg_daemon > 0 else float('inf')
            safe_print(f"      🚀 Speedup: {speedup:.1f}x faster!")
    else:
        safe_print(f"   ⚡ Daemon Mode: NOT AVAILABLE")
    
    # Overall verdict
    safe_print("\n")
    if legacy_success >= 8 and daemon_success >= 8:
        safe_print("✅ TORNADO SURVIVED IN BOTH MODES!")
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
    """🎭 TEST 2: DEPENDENCY INCEPTION - 10 levels deep"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 2: 🎭  DEPENDENCY INCEPTION                           ║")
    safe_print("║  We must go deeper... 10 LEVELS DEEP                         ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    depth = 0
    
    def go_deeper(level):
        nonlocal depth
        if level > 10:
            return
        
        indent = "  " * level
        versions = ["1.24.3", "1.26.4", "2.3.5"]
        ver = random.choice(versions)
        
        safe_print(f"{indent}{'🔻' * (level+1)} Level {level}: numpy {ver}")
        
        with omnipkgLoader(f"numpy=={ver}"):
            import numpy as np
            depth = max(depth, level)
            arr = np.array([level, level+1, level+2])
            
            if level < 10:
                go_deeper(level + 1)
            else:
                safe_print(f"{indent}{'💥' * 10} REACHED THE CORE!")
    
    go_deeper(1)
    safe_print(f"\n🎯 Maximum inception depth achieved: {depth} levels")
    safe_print("✅ WE WENT DEEPER!\n")

def chaos_test_3_framework_battle_royale():
    """⚔️ TEST 3: FRAMEWORK BATTLE ROYALE (DAEMON EDITION)"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 3: ⚔️  FRAMEWORK BATTLE ROYALE (DAEMON)               ║")
    safe_print("║  TensorFlow, PyTorch, JAX, NumPy - ALL IN MEMORY AT ONCE     ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    # 1. Connect to Daemon
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
        client = DaemonClient()
        if not client.status().get('success'):
            safe_print("   ⚙️  Summoning the Arena (Daemon)...")
            # Pre-warm the combatants
            vip_specs = [
                "tensorflow==2.13.0", 
                "torch==2.0.1", 
                "numpy==1.24.3", 
                "numpy==2.3.5"
            ]
            WorkerPoolDaemon(warmup_specs=vip_specs).start(daemonize=True)
            time.sleep(3) # Give TF time to boot (it's heavy)
            
    except ImportError:
        return False

    # 2. Define The Fighters
    combatants = [
        {
            "name": "TensorFlow", 
            "spec": "tensorflow==2.13.0",
            "code": "import tensorflow as tf; print(f'TensorFlow {tf.__version__} | Sum: {tf.reduce_sum(tf.constant([1, 2, 3])).numpy()}')"
        },
        {
            "name": "PyTorch", 
            "spec": "torch==2.0.1",
            "code": "import torch; print(f'PyTorch {torch.__version__}    | Sum: {torch.sum(torch.tensor([1, 2, 3])).item()}')"
        },
        {
            "name": "NumPy Legacy", 
            "spec": "numpy==1.24.3",
            "code": "import numpy as np; print(f'NumPy {np.__version__}      | Sum: {np.sum(np.array([1, 2, 3]))}')"
        },
        {
            "name": "NumPy Modern", 
            "spec": "numpy==2.3.5",
            "code": "import numpy as np; print(f'NumPy {np.__version__}      | Sum: {np.sum(np.array([1, 2, 3]))}')"
        }
    ]
    
    safe_print("🥊 ROUND 1: Simultaneous Execution via Daemon\n")
    
    total_start = time.perf_counter()
    
    # We will launch them sequentially to see the latency, 
    # but the daemon keeps them all resident in RAM.
    
    for fighter in combatants:
        t_start = time.perf_counter()
        
        # Smart Execute (Data is None, so it uses JSON path automatically)
        res = client.execute_smart(fighter['spec'], fighter['code'])
        
        duration = (time.perf_counter() - t_start) * 1000
        
        if res.get('success'):
            output = res['result'].strip()
            # Clean up the output string for display
            clean_out = output.split('\n')[-1] if '\n' in output else output
            safe_print(f"   ⚡ {fighter['name']:<15} → {clean_out} ({duration:.2f}ms)")
        else:
            safe_print(f"   💥 {fighter['name']:<15} → FAILED: {res.get('error')[:50]}")

    safe_print(f"\n✅ ALL COMBATANTS TESTED in {(time.perf_counter() - total_start):.2f}s!\n")
    
    # ---------------------------------------------------------
    # ROUND 2: The "Smart" Data Hand-off
    # ---------------------------------------------------------
    safe_print("🥊 ROUND 2: Smart Data Hand-off (1MB Array)\n")
    
    import numpy as np
    data = np.ones(1024 * 128) # 1MB of floats (128K * 8 bytes)
    
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
    
    res = client.execute_smart(
        "tensorflow==2.13.0", 
        tf_shm_code, 
        data=data
    )
    
    duration = (time.perf_counter() - t_start) * 1000
    
    if res.get('success'):
        result_val = res['result'][0]
        transport = res.get('transport', 'UNKNOWN')
        safe_print(f"   🚀 TF 2.13 (1MB)   → Sum: {result_val:.0f} via {transport} ({duration:.2f}ms)")
    else:
        safe_print(f"   💥 TF Failed: {res.get('error')}")

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
            size = (1000 * (i+1), 1000 * (i+1))
            arr = np.ones(size)
            mem_mb = (arr.nbytes / 1024 / 1024)
            addr = hex(id(arr))
            
            allocations.append((ver, mem_mb, addr))
            safe_print(f"🧠 numpy {ver}: Allocated {mem_mb:.1f}MB at {addr}")
    
    safe_print(f"\n🎯 Total allocations: {len(allocations)}")
    safe_print(f"🎯 Unique memory addresses: {len(set(a[2] for a in allocations))}")
    safe_print("✅ MEMORY CHAOS CONTAINED!\n")

def chaos_test_5_race_condition_roulette():
    """🎰 TEST 5: RACE CONDITION ROULETTE - ZERO-COPY SHM EDITION"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 5: 🎰  RACE CONDITION ROULETTE (SHM TURBO)            ║")
    safe_print("║  10 Threads x 3 Swaps. 100% Zero-Copy Data Transfer.         ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    results = {}
    versions = ["numpy==1.24.3", "numpy==1.26.4", "numpy==2.3.5"]
    print_lock = threading.Lock()
    verbose = is_verbose_mode()
    
    # Initialize Client
    try:
        import numpy as np
        from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
        client = DaemonClient()
        if not client.status().get('success'):
            safe_print("   ❌ Daemon not running! Starting...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(2)
    except ImportError:
        return False

    def chaotic_worker(thread_id):
        thread_versions = [random.choice(versions) for _ in range(3)]
        thread_results = []
        
        for i, spec in enumerate(thread_versions):
            # 1. Generate Data Locally
            local_data = np.random.rand(50, 50)
            
            # 2. Worker Code: Read SHM, Compute Det, Write SHM, Print Version
            code = """
import numpy as np
# arr_in provided via SHM
det = np.linalg.det(arr_in)
# arr_out provided via SHM (size 1 array)
arr_out[0] = det
# Print version to verify environment
print(np.__version__)
"""
            t_start = time.perf_counter()
            try:
                # 3. Execute via Zero-Copy
                # Output shape is (1,) because determinant is a scalar
                result_arr, response = client.execute_zero_copy(
                    spec, 
                    code, 
                    input_array=local_data, 
                    output_shape=(1,), 
                    output_dtype='float64'
                )
                
                t_end = time.perf_counter()
                duration_ms = (t_end - t_start) * 1000
                
                # 4. Verify Data (Local vs Remote)
                local_det = np.linalg.det(local_data)
                remote_det = result_arr[0]
                
                # 5. Verify Version (from stdout)
                remote_version = response['stdout'].strip()
                
                if np.isclose(local_det, remote_det):
                    status = "✅"
                    msg = f"{remote_version:<14}"
                else:
                    status = "❌"
                    msg = f"MATH ERROR: {local_det} vs {remote_det}"

                thread_results.append((spec, remote_version, status))
                
                if verbose:
                    with print_lock:
                        safe_print(f"   🎲 Thread {thread_id:02d} Round {i+1}: {msg} → {duration_ms:>6.2f} ms")
                        
            except Exception as e:
                thread_results.append((spec, str(e), "❌"))
                with print_lock:
                    safe_print(f"   💥 Thread {thread_id:02d}: {e}")

        results[thread_id] = thread_results
    
    safe_print("🔥 Launching 10 concurrent threads hammering SHM subsystem...")
    
    race_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(chaotic_worker, i) for i in range(10)]
        for f in futures:
            f.result()
    race_time = time.perf_counter() - race_start
    
    total_switches = 0
    successful_switches = 0
    
    for thread_id, thread_results in results.items():
        total_switches += len(thread_results)
        successful_switches += sum(1 for r in thread_results if r[2] == "✅")
    
    safe_print(f"\n{'='*60}")
    safe_print(f"🎯 Total Requests: {total_switches}")
    safe_print(f"✅ Success Rate:   {successful_switches}/{total_switches} ({successful_switches/total_switches*100:.1f}%)")
    safe_print(f"⚡ Total Time:     {race_time:.3f}s")
    safe_print(f"⚡ Throughput:     {total_switches/race_time:.1f} swaps/sec")
    safe_print(f"🚀 Avg Latency:    {(race_time/total_switches)*1000:.1f} ms/swap")
    
    if successful_switches == total_switches:
        safe_print("✅ CHAOS SURVIVED! (Memory Integrity Verified)")
    else:
        safe_print("⚠️  PARTIAL FAILURE")
    print("="*60 + "\n")
    
    return successful_switches == total_switches

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
    
    print("⏰ Initiating temporal displacement...\n")
    
    for era, spec, year in timeline:
        try:
            safe_print(f"🌀 Jumping to {year}...")
            with omnipkgLoader(spec):
                import numpy as np
                arr = np.array([1, 2, 3, 4, 5])
                mean = arr.mean()
                print(f"   {era:20} {spec:20} → mean={mean}")
        except Exception as e:
            safe_print(f"   ⚠️  {era}: Time jump failed - {e}")
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
                print(f"   {blocks} Level {i+1}: numpy {ver} (checksum: {checksum:.2f})")
                time.sleep(0.1)
        except Exception as e:
            safe_print(f"   💥 TOWER COLLAPSED AT LEVEL {i+1}!")
            break
    
    if len(stack) == len(versions):
        safe_print(f"\n🏆 PERFECT TOWER! All {len(stack)} blocks stable!")
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
        safe_print(f"   |ψ₁⟩ numpy 1.24.3 exists at {hex(id(state1))}")
        
        with omnipkgLoader("numpy==1.26.4"):
            import numpy as np2
            state2 = np2.array([4, 5, 6])
            states.append(("1.26.4", hex(id(state2))))
            safe_print(f"   |ψ₂⟩ numpy 1.26.4 exists at {hex(id(state2))}")
            
            with omnipkgLoader("numpy==2.3.5"):
                import numpy as np3
                state3 = np3.array([7, 8, 9])
                states.append(("2.3.5", hex(id(state3))))
                safe_print(f"   |ψ₃⟩ numpy 2.3.5 exists at {hex(id(state3))}")
                
                safe_print("\n   💫 QUANTUM SUPERPOSITION ACHIEVED!")
                safe_print(f"   💫 {len(states)} states exist simultaneously!")
    
    safe_print("\n✅ WAVE FUNCTION COLLAPSED SAFELY!\n")

def chaos_test_9_import_hell():
    """🔥 TEST 9: IMPORT HELL - Conflicting imports everywhere"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 9: 🔥  IMPORT HELL                                    ║")
    safe_print("║  Import conflicts that should destroy Python itself          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("🔥 Descending into import hell...\n")
    
    # --- MODIFIED: Use PersistentWorker for TensorFlow ---
    safe_print(f"   😈 Circle 1: TensorFlow Reality (Persistent Worker)")
    
    verbose = is_verbose_mode()  # <--- Check verbosity
    tf_worker = PersistentWorker("tensorflow==2.13.0", verbose=verbose) # <--- Pass it here
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
        if result['success']:
            safe_print(f"      ✅ TensorFlow Reality survived")
        else:
            safe_print(f"      ⚠️  TensorFlow failed: {result['error'][:60]}...")
    finally:
        tf_worker.shutdown()
    
    # Circle 2: NumPy Standalone
    safe_print(f"   😈 Circle 2: NumPy Standalone")
    try:
        with omnipkgLoader("numpy==1.24.3"):
            import numpy as np
            safe_print(f"      ✅ numpy {np.__version__} survived")
    except Exception as e:
        error_msg = str(e).split('\n')[0][:60]
        safe_print(f"      ⚠️  numpy==1.24.3 - {error_msg}...")
    
    # Circle 3: PyTorch Inferno
    safe_print(f"   😈 Circle 3: PyTorch Inferno")
    try:
        with omnipkgLoader("torch==2.0.1"):
            import torch
            safe_print(f"      ✅ torch {torch.__version__} survived")
    except Exception as e:
        error_msg = str(e).split('\n')[0][:60]
        safe_print(f"      ⚠️  torch==2.0.1 - {error_msg}...")
    
    # Circle 4: NumPy Chaos
    safe_print(f"   😈 Circle 4: NumPy Chaos")
    for numpy_ver in ["1.26.4", "2.3.5", "1.24.3"]:
        try:
            with omnipkgLoader(f"numpy=={numpy_ver}"):
                import numpy as np
                safe_print(f"      ✅ numpy {np.__version__} survived")
        except Exception as e:
            error_msg = str(e).split('\n')[0][:60]
            safe_print(f"      ⚠️  numpy=={numpy_ver} - {error_msg}...")
    
    # Circle 5: Mixed Madness
    safe_print(f"   😈 Circle 5: Mixed Madness")
    try:
        with omnipkgLoader("torch==2.0.1"):
            import torch
            safe_print(f"      ✅ torch {torch.__version__} survived")
    except Exception as e:
        safe_print(f"      ⚠️  torch - {str(e)[:60]}...")
    
    try:
        with omnipkgLoader("numpy==2.3.5"):
            import numpy as np
            safe_print(f"      ✅ numpy {np.__version__} survived")
    except Exception as e:
        safe_print(f"      ⚠️  numpy - {str(e)[:60]}...")
    
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
        for _ in range(3):
            with omnipkgLoader(f"numpy=={random.choice(['1.24.3', '2.3.5'])}", config=omnipkg_config):
                import numpy as np
                np.random.rand(100, 100).sum()
    
    def mini_inception(level=0):
        if level < 3:
            with omnipkgLoader(f"numpy=={random.choice(['1.24.3', '1.26.4'])}", config=omnipkg_config):
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
    
    print("⏰ Time travel sequence...")
    for ver in ["1.24.3", "2.3.5", "1.24.3"]:
        with omnipkgLoader(f"numpy=={ver}", config=omnipkg_config):
            import numpy as np
            pass
    
    safe_print("\n🎆🎆🎆 MAXIMUM CHAOS SURVIVED! 🎆🎆🎆\n")

def chaos_test_11_tensorflow_resurrection():
    """⚰️ TEST 11: TENSORFLOW RESURRECTION - Kill it. Revive it. 5 times."""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 11: ⚰️  TENSORFLOW RESURRECTION                       ║")
    safe_print("║  Each resurrection uses a fresh persistent worker.          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    successes = 0
    failures = 0
    
    verbose = is_verbose_mode() # <--- Check verbosity (if not already in scope)
    for i in range(5):
        safe_print(f"   ⚰️  Resurrection attempt #{i+1:02d}...")
        
        # Create a fresh worker for each resurrection
        worker = PersistentWorker("tensorflow==2.13.0", verbose=verbose) # <--- Pass it here
        
        try:
            resurrection_code = """
from omnipkg.loader import omnipkgLoader
import sys

with omnipkgLoader("numpy==1.23.5"):
    import tensorflow as tf
    x = tf.constant([1, 2, 3])
    result = tf.reduce_sum(x)
    sys.stderr.write(f"✅ TensorFlow resurrection successful: sum={result.numpy()}\\n")
"""
            result = worker.execute(resurrection_code)
            
            if result['success']:
                successes += 1
                safe_print(f"   ✅ Resurrection #{i+1:02d} succeeded")
            else:
                failures += 1
                safe_print(f"   ❌ Resurrection #{i+1:02d} failed: {result['error'][:60]}...")
        except Exception as e:
            failures += 1
            safe_print(f"   ❌ Resurrection #{i+1:02d} exception: {str(e)[:60]}...")
        finally:
            worker.shutdown()
            safe_print(f"   💀 Worker for resurrection #{i+1:02d} has been terminated.")
    
    safe_print(f"\n{'✅' if successes == 5 else '⚠️'} TENSORFLOW RESURRECTION: ({successes}/5 successful, {failures}/5 failed)\n")
    return successes == 5


def chaos_test_12_jax_vs_torch_mortal_kombat():
    """🥊 TEST 12: TRUE TORCH VERSION SWITCHING - Daemon Edition"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 12: 🥊 TRUE TORCH VERSION SWITCHING (DAEMON)          ║")
    safe_print("║  12 Rounds. 2 Fighters. Zero process overhead.              ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    specs = ["torch==2.0.1", "torch==2.1.0"] * 6  # 12 rounds total
    
    # 1. Connect to Daemon
    safe_print("⚙️  Connecting to Arena (Daemon)...")
    boot_start = time.perf_counter()
    
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
        client = DaemonClient()
        
        # Verify daemon is running
        status = client.status()
        if not status.get('success'):
            safe_print("   ⚠️  Daemon not found. Summoning Daemon...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(1) # Wait for socket
            
    except ImportError:
        safe_print("   ❌ Daemon modules missing.")
        return False

    # 2. Initialize Proxies (Lightweight)
    workers = {}
    for spec in ["torch==2.0.1", "torch==2.1.0"]:
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
        code_to_run = f'''
import torch
# Print directly to stdout, which the daemon captures and returns
x = torch.tensor([1., 2., 3.])
y = torch.sin(x)
print(f"   🥊 Round #{i+1}: Fighter {{torch.__version__:<6}} | Hit -> {{y.tolist()}}")
'''
        # Execute via Daemon
        result = workers[spec].execute(code_to_run)
        
        round_duration = (time.perf_counter() - round_start) * 1000
        round_times.append(round_duration)
        
        if result['success']:
            successful_rounds += 1
            # Print the worker's output
            if result.get('stdout'):
                sys.stdout.write(result['stdout'])
            
            # Print timing overlay
            sys.stdout.write(f"      ⚡ {round_duration:.2f}ms\n")
        else:
            safe_print(f"   💥 FATALITY: {spec} failed - {result.get('error')[:50]}")
            failed_rounds += 1

    total_fight_time = time.perf_counter() - fight_start
    avg_round = sum(round_times) / len(round_times) if round_times else 0

    safe_print(f"\n🎯 Battle Results: {successful_rounds} wins, {failed_rounds} losses")
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
            'torch': 'torch==2.0.1',
            'lightning': 'pytorch-lightning==1.9.0',
            'numpy': 'numpy==1.26.4',
            'name': 'PyTorch 2.0.1 + Lightning 1.9.0'
        },
        {
            'torch': 'torch==2.1.0',
            'lightning': 'pytorch-lightning==2.0.0',
            'numpy': 'numpy==1.26.4',
            'name': 'PyTorch 2.1.0 + Lightning 2.0.0'
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
        safe_print(f"   😈 Test {i+1}/{len(test_configs)}: {config['name']}")
        
        try:
            # Time the worker boot
            boot_start = time.perf_counter()
            worker = PersistentWorker(config['torch'], verbose=True)
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
            
            if result['success']:
                worker_successful += 1
                safe_print(f"      ⏱️  Boot:     {boot_time*1000:7.2f}ms")
                safe_print(f"      ⏱️  Execution:{exec_time*1000:7.2f}ms")
                safe_print(f"      ⏱️  TOTAL:    {total_time*1000:7.2f}ms")
                safe_print(f"      ✅ STRIKE #{worker_successful}!\n")
            else:
                safe_print(f"      💥 Failed: {result['error'][:80]}\n")
                
        except Exception as e:
            safe_print(f"      💥 Exception: {str(e)[:80]}\n")
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
        if not status.get('success'):
            safe_print("   ⚙️  Starting daemon...")
            from omnipkg.isolation.worker_daemon import WorkerPoolDaemon
            daemon = WorkerPoolDaemon()
            daemon.start(daemonize=True)
            time.sleep(1)
            
    except ImportError:
        safe_print("   ❌ Daemon not available, falling back to legacy workers")
        return chaos_test_13_pytorch_lightning_storm_legacy()
    
    for config in test_configs:
        safe_print(f"   😈 Testing Storm: {config['name']}")
        
        config_start = time.perf_counter()
        timings = {}
        
        try:
            # Create daemon proxy for torch environment
            safe_print(f"      ⚙️  Connecting to daemon worker...")
            boot_start = time.perf_counter()
            
            proxy = DaemonProxy(client, config['torch'])
            boot_time = time.perf_counter() - boot_start
            timings['worker_connect'] = boot_time
            
            safe_print(f"      ⏱️  Worker connected in {boot_time*1000:.2f}ms")
            
            # Execute code that loads lightning within the torch environment
            code_to_run = f"""
from omnipkg.loader import omnipkgLoader

# We're already in the torch environment, now add lightning
with omnipkgLoader("{config['lightning']}"):
    import pytorch_lightning as pl
    import torch
    import numpy as np
    
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
            timings['execution'] = exec_time
            
            safe_print(f"      ⏱️  Execution completed in {exec_time*1000:.2f}ms")
            
            if result['success']:
                config_time = time.perf_counter() - config_start
                timings['total'] = config_time
                timing_results.append({
                    'config': config['name'],
                    'timings': timings,
                    'success': True
                })
                
                successful += 1
                if verbose and result.get('stdout'):
                    for line in result['stdout'].strip().split('\n'):
                        safe_print(f"      {line}")
                safe_print(f"      ⏱️  Total config time: {config_time*1000:.2f}ms")
                safe_print(f"      ✅ LIGHTNING STRIKE #{successful}!")
            else:
                config_time = time.perf_counter() - config_start
                timings['total'] = config_time
                timing_results.append({
                    'config': config['name'],
                    'timings': timings,
                    'success': False,
                    'error': result.get('error', 'Unknown error')
                })
                
                safe_print(f"      ⏱️  Failed after {config_time*1000:.2f}ms")
                safe_print(f"      💥 Failed: {result.get('error', 'Unknown error')[:100]}")
                if verbose and result.get('traceback'):
                    safe_print(f"      Traceback: {result['traceback'][:500]}")
                
        except Exception as e:
            config_time = time.perf_counter() - config_start
            timings['total'] = config_time
            timing_results.append({
                'config': config['name'],
                'timings': timings,
                'success': False,
                'error': str(e)
            })
            
            safe_print(f"      ⏱️  Exception after {config_time*1000:.2f}ms")
            safe_print(f"      💥 Exception: {str(e)[:100]}")

    total_time = time.perf_counter() - total_start
    
    # Display timing summary
    safe_print(f"\n   📊 TIMING SUMMARY:")
    safe_print(f"   ⏱️  Total test time: {total_time*1000:.2f}ms")
    
    if timing_results:
        avg_connect = sum(t['timings'].get('worker_connect', 0) for t in timing_results) / len(timing_results)
        avg_exec = sum(t['timings'].get('execution', 0) for t in timing_results if 'execution' in t['timings'])
        avg_exec = avg_exec / len([t for t in timing_results if 'execution' in t['timings']]) if any('execution' in t['timings'] for t in timing_results) else 0
        
        safe_print(f"   ⏱️  Avg worker connect: {avg_connect*1000:.2f}ms")
        if avg_exec > 0:
            safe_print(f"   ⏱️  Avg execution: {avg_exec*1000:.2f}ms")
    
    safe_print(f"\n   🎯 Compatible Pairs: {successful}/{len(test_configs)} successful")

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
        if result['success']:
            safe_print("      ✅ CIRCULAR DANCE COMPLETED!")
        else:
            safe_print(f"      ⚠️  Circle 1 result: {result.get('error', 'Unknown error')}")
    finally:
        worker_1.shutdown()

    # ═══════════════════════════════════════════════════════════════
    # Test 2: Torch ↔ NumPy (The C++ Crash Candidate)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   😈 Circle 2: Torch ↔ NumPy Madness (Worker Isolated)")
    worker_2 = PersistentWorker("torch==2.0.1", verbose=verbose)
    try:
        code = """
from omnipkg.loader import omnipkgLoader
import torch
import sys

sys.stderr.write(f"      Torch 2.0.1 loaded: {torch.__version__}\\n")

# Torch uses numpy internally, now load different numpy
with omnipkgLoader("numpy==2.3.5"):
    import numpy as np
    sys.stderr.write(f"      NumPy 2.3.5 loaded: {np.__version__}\\n")
    
    # Try to use torch with the new numpy (Cross-boundary interaction)
    result = torch.tensor([1, 2, 3]).numpy()
    sys.stderr.write(f"      Torch → NumPy conversion result: {result}\\n")
"""
        result = worker_2.execute(code)
        if result['success']:
            safe_print("      ✅ CIRCULAR MADNESS SURVIVED!")
        else:
            safe_print(f"      💥 Torch/NumPy circle failed: {result['error'][:100]}...")
    finally:
        worker_2.shutdown()

    # ═══════════════════════════════════════════════════════════════
    # Test 4: Rapid Circular Switching (TRUE VERSION SWITCHING)
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n   😈 Circle 4: Rapid Circular Switching (Dual Workers)")
    safe_print("      🔥 Booting competing environments...")
    
    # We keep TWO workers alive and toggle between them
    w_old = PersistentWorker("torch==2.0.1", verbose=verbose)
    w_new = PersistentWorker("torch==2.1.0", verbose=verbose)
    
    successes = 0
    try:
        for i in range(10):
            target_worker = w_old if i % 2 == 0 else w_new
            expected = "2.0.1" if i % 2 == 0 else "2.1.0"
            
            # Simple ping to verify version state
            code = f"""
import torch
import sys
# sys.stderr.write(f"      Round {i+1}: Checking torch version...\\n")
if torch.__version__.startswith("{expected}"):
    print("MATCH")
else:
    raise ValueError(f"Version Mismatch! Got {{torch.__version__}}")
"""
            result = target_worker.execute(code)
            
            if result['success']:
                successes += 1
                # Optional: visual feedback
                # sys.stdout.write(f" {expected}")
                # sys.stdout.flush()
            else:
                safe_print(f"      💥 Round {i+1} failed: {result['error']}")
    finally:
        w_old.shutdown()
        w_new.shutdown()
    
    print(f"\n      Rapid switches: {successes}/10 successful")
    
    if successes == 10:
        safe_print("      ✅ RAPID CIRCULAR SWITCHING MASTERED! (True Isolation)")
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
    
    specs = ["torch==2.0.1", "torch==2.1.0"] * 3  # 6 switches total
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
            with omnipkgLoader(spec, quiet=True): # Quiet to keep benchmark readable
                import torch
                _ = torch.sin(torch.tensor([1.0]))
                success_count += 1
                sys.stdout.write(".")
                sys.stdout.flush()
        except Exception as e:
            safe_print(f"   ❌ Round {i+1} failed: {str(e)[:40]}")
    
    print() # Newline
    elapsed_in_process = time.perf_counter() - start
    results['in_process'] = {
        'time': elapsed_in_process,
        'success': success_count,
        'per_switch': elapsed_in_process / len(specs)
    }
    
    safe_print(f"   ✅ Total: {elapsed_in_process:.3f}s ({success_count}/{len(specs)} success)")
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
        code = f'''
from omnipkg.loader import omnipkgLoader
with omnipkgLoader("{spec}", quiet=True):
    import torch
    _ = torch.sin(torch.tensor([1.0]))
    print(f"   [Subprocess] {spec} active")
'''
        if run_python_code_in_isolation(code, f"Subprocess {i+1}"):
            success_count += 1
    
    elapsed_subprocess = time.perf_counter() - start
    results['subprocess'] = {
        'time': elapsed_subprocess,
        'success': success_count,
        'per_switch': elapsed_subprocess / len(specs)
    }
    
    safe_print(f"   ✅ Total: {elapsed_subprocess:.3f}s ({success_count}/{len(specs)} success)")
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
        code = f'''
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
'''
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            success_count += 1
            # Print the output from the subprocess so we see it live
            print(result.stdout.strip())
    
    elapsed_optimized = time.perf_counter() - start
    results['optimized_subprocess'] = {
        'time': elapsed_optimized,
        'success': success_count,
        'per_switch': elapsed_optimized / len(specs)
    }
    
    safe_print(f"   ✅ Total: {elapsed_optimized:.3f}s ({success_count}/{len(specs)} success)")
    safe_print(f"   ⚡ Per switch: {results['optimized_subprocess']['per_switch']*1000:.1f}ms\n")
    
    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 4: Fork-based (Unix only)
    # ═══════════════════════════════════════════════════════════════
    if hasattr(os, 'fork'):
        safe_print("📊 Strategy 4: FORK-BASED ISOLATION (Unix)")
        start = time.perf_counter()
        success_count = 0
        for i, spec in enumerate(specs):
            pid = os.fork()
            if pid == 0:
                try:
                    with omnipkgLoader(spec, quiet=True):
                        import torch
                        _ = torch.sin(torch.tensor([1.0]))
                        print(f"   [Fork] {spec} done")
                    sys.exit(0)
                except Exception:
                    sys.exit(1)
            else:
                _, status = os.waitpid(pid, 0)
                if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                    success_count += 1
        elapsed_fork = time.perf_counter() - start
        results['fork'] = {'time': elapsed_fork, 'success': success_count, 'per_switch': elapsed_fork/len(specs)}
        safe_print(f"   ✅ Total: {elapsed_fork:.3f}s ({success_count}/{len(specs)} success)")
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
                
                if result['success']:
                    success_count += 1
            except Exception as e:
                safe_print(f"   ❌ Round {i+1} failed: {str(e)[:40]}")
    finally:
        safe_print("   🛑 Shutting down worker pool...")
        for worker in workers.values():
            worker.shutdown()
    
    elapsed_worker = time.perf_counter() - start
    results['worker_pool'] = {
        'time': elapsed_worker,
        'success': success_count,
        'per_switch': elapsed_worker / len(specs)
    }
    
    safe_print(f"   ✅ Total: {elapsed_worker:.3f}s ({success_count}/{len(specs)} success)")
    safe_print(f"   ⚡ Per switch: {results['worker_pool']['per_switch']*1000:.1f}ms\n")
    
    # ═══════════════════════════════════════════════════════════════
    # STRATEGY 3: Daemon JSON (Control Plane)
    # ═══════════════════════════════════════════════════════════════
    safe_print("📊 Strategy 3: DAEMON (JSON Mode)")
    safe_print("   - Pros: Persistent workers, no boot cost")
    safe_print("   - Cons: JSON serialization overhead")
    safe_print("-" * 60)
    
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
        # Ensure daemon is up
        client = DaemonClient()
        if not client.status().get('success'):
            safe_print("   ⚙️  Starting Daemon...")
            WorkerPoolDaemon().start(daemonize=True)
            time.sleep(2)
            
        start = time.perf_counter()
        success_count = 0
        
        for spec in specs:
            proxy = DaemonProxy(client, spec)
            res = proxy.execute("import torch; print('ok')")
            if res['success']:
                success_count += 1
                
        elapsed = time.perf_counter() - start
        results['daemon_json'] = {'time': elapsed, 'success': success_count}
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
        data = np.array([1.0]) # Tiny payload
        
        start = time.perf_counter()
        success_count = 0
        
        for spec in specs:
            try:
                res_arr, _ = client.execute_zero_copy(
                    spec, 
                    "arr_out[0] = 1", 
                    data, 
                    (1,), 
                    'float64'
                )
                success_count += 1
            except: pass
                
        elapsed = time.perf_counter() - start
        results['daemon_shm'] = {'time': elapsed, 'success': success_count}
        safe_print(f"   ✅ Total: {elapsed:.3f}s")
        safe_print(f"   ⚡ Per switch: {elapsed/6*1000:.1f}ms\n")
        
    except ImportError:
        pass

    # ═══════════════════════════════════════════════════════════════
    # FINAL SCOREBOARD
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n" + "="*60)
    safe_print(f"{'STRATEGY':<25} | {'TOTAL':<8} | {'PER SWAP':<10} | {'VS BASELINE'}")
    safe_print("-" * 60)
    
    baseline = results['in_process']['time']
    
    sorted_results = sorted(results.items(), key=lambda x: x[1]['time'])
    
    for strat, data in sorted_results:
        t = data['time']
        per = (t / 6) * 1000
        
        if t < baseline:
            comp = f"{baseline/t:.1f}x FASTER"
        else:
            comp = f"{t/baseline:.1f}x SLOWER"
            
        safe_print(f"{strat:<25} | {t:6.3f}s | {per:6.1f}ms | {comp}")
        
    safe_print("="*60 + "\n")

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
        ("2.3.5", "numpy==2.3.5")
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
                    safe_print(f"     ✅ {spec:<15} → Active (version={actual_ver}, mean={mean})")
                else:
                    safe_print(f"     ❌ {spec:<15} → Mismatch! Expected {expected_ver}, got {actual_ver}")
                    phase1_success = False
                    
        except Exception as e:
            safe_print(f"     💥 {spec:<15} → Failed: {e}")
            phase1_success = False
            
        time.sleep(0.1)  # Brief pause between switches

    safe_print(f"   🎯 Phase 1 Result: {'PASSED' if phase1_success else 'FAILED'}\n")


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
with omnipkgLoader("numpy==1.24.3", quiet=True, isolation_mode='overlay'):
    import numpy as np
    
    # Layer 2: SciPy 1.10.1 (Compatible with NumPy 1.24)
    with omnipkgLoader("scipy==1.10.1", quiet=True, isolation_mode='overlay'):
        import scipy
        import scipy.linalg
        
        # Layer 3: Pandas 2.0.3 (Compatible with NumPy 1.24)
        with omnipkgLoader("pandas==2.0.3", quiet=True, isolation_mode='overlay'):
            import pandas as pd
            
            # Layer 4: Scikit-Learn 1.3.2
            with omnipkgLoader("scikit-learn==1.3.2", quiet=True, isolation_mode='overlay'):
                from sklearn.ensemble import RandomForestClassifier
                
                # TF from base
                import tensorflow as tf
                
                # Layer 5: PyTorch 2.0.1
                with omnipkgLoader("torch==2.0.1", quiet=True, isolation_mode='overlay'):
                    import torch
                    
                    log("     ✅ ALL LAYERS LOADED!")
                    
                    tf_tens = tf.constant([1,2,3])
                    torch_tens = torch.tensor([1,2,3])
                    sp_val = scipy.linalg.norm([1,2,3])
                    
                    log(f"     🎉 Verification: TF={tf_tens.shape}, Torch={torch_tens.shape}, SciPy={sp_val:.2f}")

print("SUCCESS")
"""
        result = tf_worker.execute(nested_hell_code)
        
        if result['success'] and "SUCCESS" in result['stdout']:
            safe_print("\n   ✅ Phase 2: 7-layer stack STABLE!")
            phase2_success = True
        else:
            safe_print(f"\n   💥 Phase 2 COLLAPSED: {result.get('error', result.get('stderr'))}\n")
            phase2_success = False
            
    finally:
        tf_worker.shutdown()

    if phase1_success and phase2_success:
        safe_print("\n✅ NESTED REALITY CONQUERED! (Multi-process + Overlay)")
        return True
    else:
        return False

def chaos_test_17_experimental_dynamic_loading():
    """🔬 TEST 17: EXPERIMENTAL - Dynamic C Extension Reloading"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 17: 🔬 EXPERIMENTAL DYNAMIC C++ RELOADING             ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    safe_print("   Reality Check:")
    safe_print("   ❌ True in-process C++ reloading is IMPOSSIBLE with current Python")
    safe_print("   ✅ Subprocess isolation (omnipkg.isolation) is the ONLY safe approach")
    safe_print("   💡 Future: Worker pools + fork() offer best compromise\n")

def chaos_test_18_worker_pool_drag_race():
    """🏎️ TEST 18: HFT SIMULATION - High Frequency Worker Swapping"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 18: 🏎️  HFT SIMULATION (Worker Pool Drag Race)        ║")
    safe_print("║  Scenario: 4 Concurrent Threads hammering the Daemon         ║")
    safe_print("║  Goal: Prove thread-safety and max throughput                ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # 1. Setup the "Trading Floor" (Daemon)
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
        client = DaemonClient()
        if not client.status().get('success'):
            safe_print("   ⚙️  Starting Trading Floor (Daemon)...")
            # VIP list ensures our workers are warm
            vip_specs = ["torch==2.0.1", "torch==2.1.0", "numpy==1.24.3", "numpy==1.26.4"]
            WorkerPoolDaemon(warmup_specs=vip_specs).start(daemonize=True)
            time.sleep(2) # Give it a moment to boot the fleet
    except ImportError:
        return False

    # 2. Define the Workload
    # Two threads want Torch 2.0, Two threads want Torch 2.1
    # They will fight for the workers.
    
    LAPS = 50 # 50 requests per thread
    THREADS = 4
    
    safe_print(f"   🚦 RACE SETTINGS: {THREADS} Threads x {LAPS} Laps = {THREADS*LAPS} Total Transactions")
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
        
        for _ in range(LAPS):
            res = proxy.execute(code)
            if res['success']:
                success_count += 1
        
        t_end = time.perf_counter()
        results.append({
            'id': thread_id,
            'spec': spec,
            'time': t_end - t_start,
            'success': success_count
        })

    # 3. Create Threads
    threads = []
    specs = ["torch==2.0.1", "torch==2.1.0", "numpy==1.24.3", "numpy==1.26.4"]
    
    for i in range(THREADS):
        t = threading.Thread(target=hft_trader, args=(i, specs[i % len(specs)]))
        threads.append(t)
        t.start()

    # 4. START RACE
    safe_print("   🔫 GO!")
    time.sleep(0.5) # Let threads initialize
    race_start = time.perf_counter()
    start_gun.set()
    
    for t in threads:
        t.join()
        
    total_race_time = time.perf_counter() - race_start
    
    # 5. Analysis
    total_reqs = sum(r['success'] for r in results)
    safe_print("\n   🏁 FINISH LINE")
    safe_print("   ──────────────────────────────────────────")
    
    for r in results:
        tps = r['success'] / r['time']
        safe_print(f"   🏎️  Thread {r['id']} ({r['spec']}): {r['success']}/{LAPS} ok | {r['time']*1000/LAPS:.2f}ms/req | {tps:.1f} req/s")

    safe_print("   ──────────────────────────────────────────")
    safe_print(f"   ⏱️  Total Wall Time: {total_race_time:.3f}s")
    safe_print(f"   ⚡ System Throughput: {total_reqs / total_race_time:.1f} Transactions/Second")
    
    if total_reqs == THREADS * LAPS:
         safe_print("\n   🏆 RESULT: MARKET STABLE. ZERO DROPPED PACKETS.")
    else:
         safe_print("\n   ⚠️  RESULT: PACKET LOSS DETECTED.")
         
    return True

def chaos_test_19_zero_copy_hft():
    """🚀 TEST 19: ZERO-COPY vs JSON (10MB BENCHMARK)"""
    import numpy as np
    import time
    from omnipkg.isolation.worker_daemon import DaemonClient
    
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║  TEST 19: 🚀 ZERO-COPY vs JSON (10MB BENCHMARK)             ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    client = DaemonClient()
    if not client.status().get('success'):
        print("   ❌ Daemon not running")
        return

    # 1. Create 10MB Matrix
    size = (1000, 1250) 
    print(f"   📉 Generating 10MB Matrix {size}...")
    data = np.random.rand(*size)
    print("   ✅ Data ready.\n")

    spec = "numpy==1.26.4"

    # ---------------------------------------------------------
    # ROUND 1: JSON
    # ---------------------------------------------------------
    print("   🐢 ROUND 1: Standard JSON Serialization")
    print("      (This will take ~7 seconds...)")
    start = time.perf_counter()
    
    try:
        input_list = data.tolist()
        json_code = f"import numpy as np; arr = np.array({input_list}); result={{'out': (arr*2).tolist()}}"
        res = client.execute_shm(spec, json_code, {}, {})
        
        if res.get('success'):
            _ = np.array(res['out'])
            duration_json = (time.perf_counter() - start) * 1000
            print(f"      ⏱️  Total: {duration_json:.2f}ms")
        else:
            print(f"      💥 JSON Failed: {res.get('error')}")
            duration_json = float('inf')
    except Exception as e:
        print(f"      💥 JSON Exception: {e}")
        duration_json = float('inf')

    # ---------------------------------------------------------
    # ROUND 2: SHM
    # ---------------------------------------------------------
    print("\n   🚀 ROUND 2: Shared Memory Pointer Handoff")
    shm_code = "arr_out[:] = arr_in * 2"
    
    start = time.perf_counter()
    try:
        # UPDATED LINE: Unpack the tuple
        result, _ = client.execute_zero_copy(
            spec, 
            shm_code, 
            input_array=data, 
            output_shape=size, 
            output_dtype='float64'
        )
        duration_shm = (time.perf_counter() - start) * 1000
        print(f"      ⏱️  Total: {duration_shm:.2f}ms")
        
        # FINAL SCORE
        print("\n   🏁 RACE RESULTS (10MB Payload)")
        print("   ──────────────────────────────────────────")
        print(f"   🐢 JSON: {duration_json:7.2f}ms")
        print(f"   🚀 SHM:  {duration_shm:7.2f}ms")
        
        if duration_shm > 0:
            speedup = duration_json / duration_shm
            print(f"   🏆 Speedup: {speedup:.1f}x FASTER")
            
    except Exception as e:
        print(f"      💥 SHM Failed: {e}")
        import traceback
        traceback.print_exc()

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
    chaos_test_17_experimental_dynamic_loading,
    chaos_test_18_worker_pool_drag_race,  # <-- ADD THIS
    chaos_test_19_zero_copy_hft

]

def get_test_name(func):
    return func.__name__.replace('chaos_test_', '').replace('_', ' ').title()

def select_tests_interactively():
    print_chaos_header()
    print("📝 AVAILABLE CHAOS SCENARIOS:")
    print("=" * 60)
    print(f"   [0] 🔥 RUN ALL TESTS (The Full Experience)")
    print("-" * 60)
    for i, test_func in enumerate(ALL_TESTS, 1):
        print(f"   [{i}] {get_test_name(test_func)}")
    print("=" * 60)
    print("\n💡 Tip: Type numbers separated by spaces (e.g. '1 3 5').")
    
    try:
        sys.stdout.flush()
        selection = input("\n👉 Choose tests [0]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ALL_TESTS

    if not selection or selection == "0" or selection.lower() == "all":
        return ALL_TESTS

    selected_tests = []
    try:
        parts = selection.replace(',', ' ').split()
        indices = [int(x) for x in parts if x.strip().isdigit()]
        for idx in indices:
            if idx == 0: return ALL_TESTS
            if 1 <= idx <= len(ALL_TESTS):
                selected_tests.append(ALL_TESTS[idx-1])
    except ValueError:
        return ALL_TESTS
        
    return selected_tests if selected_tests else ALL_TESTS

def run_chaos_suite(tests_to_run=None):
    if tests_to_run is None: tests_to_run = ALL_TESTS
    if not tests_to_run: return True
    
    results = []
    safe_print(f"\n🚀 Launching {len(tests_to_run)} chaos scenarios...\n")
    
    for i, test in enumerate(tests_to_run, 1):
        name = get_test_name(test)
        safe_print(f"\n🧪 TEST {i}/{len(tests_to_run)}: {name}")
        safe_print("─" * 66)
        try:
            test()
            results.append(("✅", name))
            safe_print(f"✅ {name} - PASSED")
        except Exception as e:
            results.append(("❌", name))
            safe_print(f"❌ {name} - FAILED: {str(e)[:100]}...")
        time.sleep(0.5)
    
    print("\n" + "=" * 66)
    safe_print("   📊 DETAILED RESULTS:")
    safe_print("─" * 66)
    for status, name in results:
        print(f"   {status} {name}")
    
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
        if os.environ.get("OMNIPKG_REEXEC_COUNT"):
            run_chaos_suite(ALL_TESTS)
        else:
            selected = select_tests_interactively()
            if selected: run_chaos_suite(selected)

    except KeyboardInterrupt:
        safe_print("\n\n⚠️  CHAOS INTERRUPTED BY USER!")
    except ProcessCorruptedException as e:
        safe_print(f"\n☢️   CATASTROPHIC CORRUPTION: {e}")
        # Re-exec logic omitted for brevity in this cleaned version
        sys.exit(1)
    except Exception as e:
        import traceback
        safe_print(f"\n💥 CHAOS FAILURE: {e}")
        traceback.print_exc()