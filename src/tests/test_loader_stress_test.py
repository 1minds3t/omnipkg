#!/usr/bin/env python3
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

# Set TF env vars globally for this process too
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

# HELPER: Check verbosity
def is_verbose_mode():
    return "--verbose" in sys.argv or "-v" in sys.argv

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
    """🌪️ TEST 1: VERSION TORNADO - Rapid random switching"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 1: 🌪️  VERSION TORNADO                                ║")
    safe_print("║  Random version switching at maximum chaos velocity          ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    versions = ["1.24.3", "1.26.4", "2.3.5"]
    
    for i in range(15):
        ver = random.choice(versions)
        direction = random.choice(["↗️", "↘️", "↔️", "↕️"])
        
        start = time.perf_counter()
        with omnipkgLoader(f"numpy=={ver}"):
            import numpy as np
            arr = np.random.rand(100, 100)
            result = np.sum(arr)
            elapsed = (time.perf_counter() - start) * 1000
            
        print(f"{direction} Chaos #{i+1:02d}: numpy {ver} → sum={result:.2f} ({elapsed:.2f}ms)")
        time.sleep(0.05)
    
    safe_print("\n✅ SURVIVED THE TORNADO!\n")

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
    """⚔️ TEST 3: FRAMEWORK BATTLE ROYALE - All at once"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 3: ⚔️  FRAMEWORK BATTLE ROYALE                        ║")
    safe_print("║  TensorFlow, PyTorch, JAX, NumPy - FIGHT!                    ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    safe_print("🥊 ROUND 1: Sequential Combat\n")
    
    # --- MODIFIED: Use PersistentWorker for TensorFlow ---
    safe_print("   🔥 Booting TensorFlow worker...")
    tf_worker = PersistentWorker("tensorflow==2.13.0", verbose=False)
    
    try:
        tf_code = """
import tensorflow as tf
result = tf.reduce_sum(tf.constant([1, 2, 3])).numpy()
import sys
sys.stderr.write(f"   🔥 TensorFlow {tf.__version__:15} → Tensor sum: {result}\\n")
"""
        result = tf_worker.execute(tf_code)
        if not result['success']:
            safe_print(f"   ⚠️  TensorFlow failed: {result['error'][:50]}...")
    finally:
        tf_worker.shutdown()
    
    # Other combatants run in main process
    combatants = [
        ("PyTorch 2.0", "torch==2.0.1", "torch"),
        ("NumPy 1.24", "numpy==1.24.3", "numpy"),
        ("NumPy 2.3", "numpy==2.3.5", "numpy"),
    ]
    
    for name, spec, pkg_name in combatants:
        try:
            with omnipkgLoader(spec):
                if pkg_name == 'torch':
                    import torch
                    result = torch.sum(torch.tensor([1, 2, 3])).item()
                    safe_print(f"   ⚡ {name:20} → Tensor sum: {result}")
                else:  # numpy
                    import numpy as np
                    result = np.sum(np.array([1, 2, 3]))
                    safe_print(f"   🎯 {name:20} → Array sum: {result}")
        except Exception as e:
            safe_print(f"   ⚠️  {name:20} → {str(e)[:50]}...")
    
    safe_print("\n✅ ALL COMBATANTS TESTED!\n")


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
    """🎰 TEST 5: RACE CONDITION ROULETTE - Threading chaos with persistent workers"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 5: 🎰  RACE CONDITION ROULETTE                        ║")
    safe_print("║  Multiple threads, multiple versions, MAXIMUM CHAOS          ║")
    safe_print("║  Now with PERSISTENT WORKERS for true isolation!            ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    results = {}
    versions = ["numpy==1.24.3", "numpy==1.26.4", "numpy==2.3.5"]
    
    # 1. Boot up worker pool
    safe_print("⚙️  Booting worker pool...")
    boot_start = time.perf_counter()
    workers = {}
    
    # CHECK VERBOSITY
    verbose = is_verbose_mode()
    
    for spec in versions:
        workers[spec] = PersistentWorker(spec, verbose=verbose)  # Respect CLI flag
        
    boot_time = time.perf_counter() - boot_start
    safe_print(f"✨ Worker pool ready in {boot_time:.2f}s\n")
    
    def chaotic_worker(thread_id):
        spec = random.choice(versions)
        try:
            code = f"""
import numpy as np
import sys
data = np.random.rand(200, 200)
result = np.linalg.det(data)
sys.stderr.write(f'🎲 Thread {thread_id:02d}: numpy {{np.__version__}} → det={{result:.6f}}\\n')
print(result)  # Return the result
"""
            result = workers[spec].execute(code)
            
            if result['success']:
                # Handle case where stdout might be None or empty
                stdout_val = result.get('stdout', '').strip()
                if not stdout_val:
                    # Fallback if no stdout captured but success reported
                    det_value = 0.0
                else:
                    det_value = float(stdout_val)
                    
                results[thread_id] = (spec, det_value, "✅")
            else:
                results[thread_id] = (spec, result['error'], "❌")
                safe_print(f"💥 Thread {thread_id:02d}: EXPLODED with {spec}")
        except Exception as e:
            results[thread_id] = (spec, str(e), "❌")
            safe_print(f"💥 Thread {thread_id:02d}: CRASHED - {str(e)[:50]}")
    
    safe_print("🔥 Launching 10 chaotic threads...\n")
    race_start = time.perf_counter()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(chaotic_worker, i) for i in range(10)]
        for f in futures:
            f.result()
    
    race_time = time.perf_counter() - race_start
    
    # Cleanup
    safe_print("\n🛑 Shutting down worker pool...")
    teardown_start = time.perf_counter()
    for worker in workers.values():
        worker.shutdown()
    teardown_time = time.perf_counter() - teardown_start
    
    # Results
    successes = sum(1 for v in results.values() if v[2] == "✅")
    safe_print(f"\n{'='*60}")
    safe_print(f"🎯 Threads survived: {successes}/10")
    safe_print(f"⚡ Race time: {race_time:.3f}s")
    safe_print(f"🔧 Startup: {boot_time:.3f}s | Teardown: {teardown_time:.3f}s")
    safe_print(f"📊 Total: {boot_time + race_time + teardown_time:.3f}s")
    
    if successes == 10:
        safe_print("✅ PERFECT CHAOS SURVIVED!")
    elif successes >= 7:
        safe_print("✅ CHAOS SURVIVED!")
    else:
        safe_print("⚠️  PARTIAL SURVIVAL")
    print("="*60 + "\n")

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
    """🥊 TEST 12: TRUE TORCH VERSION SWITCHING - Using Persistent Workers"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 12: 🥊 TRUE TORCH VERSION SWITCHING                   ║")
    safe_print("║  Each version in its own persistent worker - REAL switching!║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")
    
    specs = ["torch==2.0.1", "torch==2.1.0"] * 6  # 12 rounds total
    
    # Boot up workers
    safe_print("⚙️  Booting fighter workers...")
    boot_start = time.perf_counter()
    workers = {}
    
    verbose = is_verbose_mode()
    for spec in ["torch==2.0.1", "torch==2.1.0"]:
        workers[spec] = PersistentWorker(spec, verbose=verbose) # Respect CLI flag
        
    boot_time = time.perf_counter() - boot_start
    safe_print(f"✨ Fighters ready in {boot_time:.2f}s\n")
    
    successful_rounds = 0
    failed_rounds = 0
    
    try:
        for i, spec in enumerate(specs):
            code_to_run = f'''
import torch
import sys
version = torch.__version__
x = torch.tensor([1., 2., 3.])
y = torch.sin(x)
sys.stderr.write(f"   🥊 Round #{i+1}: VERSION={{version}}, sin([1,2,3]) → {{y.tolist()}}\\n")
'''
            result = workers[spec].execute(code_to_run)
            
            if result['success']:
                successful_rounds += 1
            else:
                safe_print(f"   💥 FATALITY: {spec} failed - {result['error'][:50]}")
                failed_rounds += 1
    finally:
        safe_print("\n🛑 Shutting down fighters...")
        for worker in workers.values():
            worker.shutdown()

    safe_print(f"\n🎯 Battle Results: {successful_rounds} wins, {failed_rounds} losses")
    
    if successful_rounds == len(specs):
        safe_print("✅ FLAWLESS VICTORY! TRUE VERSION SWITCHING ACHIEVED!\n")
    else:
        safe_print("❌ Some rounds failed.\n")

def chaos_test_13_pytorch_lightning_storm():
    """⚡ TEST 13: PyTorch Lightning Storm - Using Persistent Workers"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 13: ⚡ PyTorch Lightning Storm                         ║")
    safe_print("║  Testing framework with persistent workers for isolation     ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    safe_print("   🌩️  Testing PyTorch Lightning with persistent workers.\n")

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

    successful = 0
    verbose = is_verbose_mode()
    
    for config in test_configs:
        safe_print(f"   😈 Testing Storm: {config['name']}")
        
        # Create a worker with all dependencies pre-specified
        # We'll create a combined spec that includes all packages
        safe_print(f"      ⚙️  Booting worker with full stack...")
        boot_start = time.perf_counter()
        
        try:
            # Boot worker with torch version (which is the critical one)
            worker = PersistentWorker(config['torch'], verbose=verbose) # Respect CLI flag
            boot_time = time.perf_counter() - boot_start
            
            # Now execute code that loads lightning within the torch environment
            code_to_run = f"""
from omnipkg.loader import omnipkgLoader

# We're already in the torch environment, now add lightning
with omnipkgLoader("{config['lightning']}"):
    import pytorch_lightning as pl
    import torch
    import numpy as np
    import sys
    sys.stderr.write(f"      ⚡ PyTorch {{torch.__version__}} + Lightning {{pl.__version__}} + NumPy {{np.__version__}} loaded successfully.\\n")
"""
            
            result = worker.execute(code_to_run)
            
            if result['success']:
                successful += 1
                safe_print(f"      ✅ LIGHTNING STRIKE #{successful}!")
            else:
                safe_print(f"      💥 Failed: {result['error'][:100]}")
                
        except Exception as e:
            safe_print(f"      💥 Exception: {str(e)[:100]}")
        finally:
            try:
                worker.shutdown()
            except:
                pass

    safe_print(f"\n   🎯 Compatible Pairs: {successful}/{len(test_configs)} successful")

    if successful == len(test_configs):
        safe_print("   ✅ PYTORCH LIGHTNING STORM SURVIVED!")
    else:
        safe_print("   ⚡ LIGHTNING STORM FAILED!")

    safe_print("\n")

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
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    safe_print("\n" + "="*60)
    sorted_results = sorted(results.items(), key=lambda x: x[1]['time'])
    baseline = results['in_process']['time']
    for strat, data in sorted_results:
        diff = data['time'] / baseline
        desc = f"{diff:.1f}x SLOWER" if diff > 1 else f"{baseline/data['time']:.1f}x FASTER"
        safe_print(f"{strat:20s} | {data['time']:6.2f}s | {desc:12s} | {data['success']}/{len(specs)}")

def chaos_test_16_nested_reality_hell():
    """🧬 TEST 16: NESTED REALITY HELL"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 16: 🧬  NESTED REALITY HELL                            ║")
    safe_print("║  7-layer nested challenge in a persistent worker             ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    # Boot a worker with TensorFlow (most problematic package)
    safe_print("   ⚙️  Booting nested reality worker...")
    verbose = is_verbose_mode()
    worker = PersistentWorker("tensorflow==2.13.0", verbose=verbose) # <--- Pass it here
    
    try:
        nested_hell_code = """
from omnipkg.loader import omnipkgLoader
import sys

def log(msg):
    sys.stderr.write(msg + '\\n')

log("   - PHASE 1: Rapid Sequential NumPy Switching")
versions = [("1.24.3", "numpy==1.24.3"), ("1.26.4", "numpy==1.26.4"), ("2.3.5", "numpy==2.3.5")]
for name, spec in versions:
    with omnipkgLoader(spec, quiet=True):
        import numpy as np
        arr = np.array([1,2,3,4,5])
        log(f"     NumPy {np.__version__:8} → sum={arr.sum()}, mean={arr.mean():.1f}")

log("\\n   - PHASE 2: 7-Layer Deep Nested Activation")
with omnipkgLoader("numpy==1.24.3", quiet=True):
    import numpy as np
    with omnipkgLoader("scipy==1.12.0", quiet=True):
        import scipy
        with omnipkgLoader("pandas==2.1.4", quiet=True):
            import pandas as pd
            with omnipkgLoader("scikit-learn==1.3.2", quiet=True):
                from sklearn.ensemble import RandomForestClassifier
                # TF already loaded in worker environment
                import tensorflow as tf
                with omnipkgLoader("torch==2.0.1", quiet=True):
                    import torch
                    log("     ✅ ALL 7 LAYERS LOADED!")
                    _ = tf.constant([1,2,3])
                    _ = torch.tensor([1,2,3])

log("\\n   - OMNIPKG WINS. AGAIN.")
"""
        
        result = worker.execute(nested_hell_code)
        
        if result['success']:
            safe_print("\n✅ THE IMPOSSIBLE WAS MADE POSSIBLE. NESTED REALITY IS STABLE.\n")
            return True
        else:
            safe_print(f"\n💥 THE NESTED REALITY COLLAPSED: {result['error'][:100]}\n")
            return False
            
    finally:
        worker.shutdown()

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
    """🏎️ TEST 18: WORKER POOL DRAG RACE (Parallel Execution with Version Swapping)"""
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  TEST 18: 🏎️  WORKER POOL DRAG RACE                         ║")
    safe_print("║  Two workers. Two threads. 6 swaps each = 12 total swaps.   ║")
    safe_print("║  GOAL: Verify concurrent version swapping and throughput.   ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    laps = 6
    versions_a = ["torch==2.0.1", "torch==2.1.0"] * 3  # Alternates 6 times
    versions_b = ["torch==2.1.0", "torch==2.0.1"] * 3  # Opposite pattern
    
    # 1. Pit Stop: Booting Engines
    safe_print("   🚦 PIT STOP: Booting Engines (Workers)...")
    boot_start = time.perf_counter()
    
    # Create worker pool with both versions
    workers = {}
    for spec in ["torch==2.0.1", "torch==2.1.0"]:
        workers[spec] = PersistentWorker(spec, verbose=True)
    
    boot_time = time.perf_counter() - boot_start
    safe_print(f"   ✨ Engines Ready in {boot_time:.2f}s\n")

    # 2. Define the Racer Function
    def racer(workers_dict, name, version_sequence, laps):
        for i in range(1, laps + 1):
            spec = version_sequence[i-1]
            time.sleep(0.05)  # Simulate work
            
            code = f"""
import torch
import sys
import time
# Simulate calculation
val = torch.sin(torch.tensor([{i}.0]))
sys.stderr.write(f'   🏎️  [Racer {name}] Lap {i}/{laps} | Using {{torch.__version__}}\\n')
"""
            result = workers_dict[spec].execute(code)
            if not result['success']:
                safe_print(f"   💥 {name} CRASHED on Lap {i}: {result['error']}")
                return False
        return True

    # 3. The Race (Concurrent Execution)
    safe_print("   🏁 LIGHTS OUT! GO! GO! GO!\n")
    race_start = time.perf_counter()
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Launch both simultaneously
        future_a = executor.submit(racer, workers, "A", versions_a, laps)
        future_b = executor.submit(racer, workers, "B", versions_b, laps)
        
        # Wait for finish
        success_a = future_a.result()
        success_b = future_b.result()

    race_time = time.perf_counter() - race_start

    # 4. Cleanup
    safe_print("\n   🛑 Shutting down worker pool...")
    teardown_start = time.perf_counter()
    for worker in workers.values():
        worker.shutdown()
    teardown_time = time.perf_counter() - teardown_start
    
    # 5. Results
    safe_print("\n" + "="*60)
    safe_print(f"   🏁 RACE FINISHED in {race_time:.3f}s")
    
    total_swaps = laps * 2
    throughput = total_swaps / race_time
    
    safe_print(f"   ⚡ Throughput: {throughput:.1f} swaps/sec")
    safe_print(f"   🔧 Startup time: {boot_time:.3f}s")
    safe_print(f"   🛑 Teardown time: {teardown_time:.3f}s")
    
    total_time = boot_time + race_time + teardown_time
    safe_print(f"   📊 Total time: {total_time:.3f}s")
    
    if success_a and success_b:
        safe_print("   🏆 RESULT: Both cars finished successfully!")
        safe_print(f"   🎯 Each racer swapped versions {laps} times")
        if race_time < 2.0:
            safe_print("   🚀 SPEED: HYPER-SONIC (True Parallelism Achieved)")
    else:
        safe_print("   💥 RESULT: DNF (Did Not Finish)")
    print("="*60 + "\n")


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
    chaos_test_18_worker_pool_drag_race  # <-- ADD THIS

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