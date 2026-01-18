#!/usr/bin/env python3
"""
🌌 OMNIPKG DEMO: THE MULTIVERSE GAUNTLET
========================================
A comprehensive stress test of the OmniPkg Daemon Architecture.

Features:
1.  🔥 GPU IPC: Zero-copy tensor sharing between processes.
2.  🌌 Multiverse: Sequential execution across Python 3.9, 3.10, and 3.11.
3.  🧪 Multi-Torch: PyTorch 1.13, 2.0, and 2.1 sharing the same VRAM.
4.  ⚡ High Frequency: Sub-2ms context switching.
5.  🧵 Concurrency: Thread-safe dispatch for parallel reads.
6.  🛡️ Optimistic Concurrency: CAS-based simulation for concurrent writes.

Scenario:
- ACT 1 (Pipeline): Data is mutated sequentially by 3 different runtimes.
- ACT 2 (Swarm): Data is read concurrently by 3 different runtimes.
- ACT 3 (Gauntlet): Random-access stress test of the control plane.
- ACT 4 (Crucible): Concurrent WRITE contention with retry logic.
"""

import sys
import time
import os
import threading
import subprocess
import random
from omnipkg.i18n import _

# Ensure we can find omnipkg (Force SRC usage to pick up local .so build)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

try:
    from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon, SharedStateMonitor
    from omnipkg.loader import omnipkgLoader
except ImportError:
    print("❌ Error: Could not import omnipkg. Run this from the project root.")
    sys.exit(1)

# 🔧 CONFIGURATION: HARDCODED PATHS FOR PRODUCTION DEMO
UNIVERSES = [
    {
        "name": "Alpha (Py3.9)", 
        "exe": "/home/minds3t/miniconda3/envs/evocoder_env/.omnipkg/interpreters/cpython-3.9.23/bin/python3.9", 
        "spec": "torch==2.0.1+cu118"
    },
    {
        "name": "Beta  (Py3.10)",  
        "exe": "/home/minds3t/miniconda3/envs/evocoder_env/.omnipkg/interpreters/cpython-3.10.18/bin/python3.10", 
        "spec": "torch==2.1.0"
    },
    {
        "name": "Gamma (Py3.11)", 
        "exe": "/home/minds3t/miniconda3/envs/evocoder_env/bin/python3.11", 
        "spec": "torch==1.13.1+cu116"
    }
]

def main():
    # 🧹 PRE-FLIGHT CLEANUP
    print(_('🧹 CLEARING PROCESSES...'))
    os.system("pkill -f omnipkg.isolation.worker_daemon")
    os.system("pkill -f omnipkg_stdin")
    time.sleep(1)

    # 1. BOOT DAEMON
    print(_('⚙️  BOOTING DAEMON...'))
    
    # 🔥 FIX: Pass PYTHONPATH to daemon so it finds 'src/omnipkg/isolation/omnipkg_atomic.so'
    env = os.environ.copy()
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    
    subprocess.Popen([sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"], env=env)
    time.sleep(2)
    print(_('⚙️  BOOTING DAEMON...'))
    client = DaemonClient()

    # 2. WARMUP
    print(_('🔥 WARMING UP THE FLEET...'))
    for u in UNIVERSES:
        if not os.path.exists(u["exe"]):
            print(_('❌ Error: Python interpreter not found: {}').format(u['exe']))
            sys.exit(1)
        client.execute_shm(u["spec"], "pass", {}, {}, python_exe=u["exe"])
    print(_('✅ Fleet Ready.'))

    # 3. GENESIS
    with omnipkgLoader("torch==2.0.1+cu118", quiet=True):
        import torch
        if not torch.cuda.is_available():
            print(_('❌ Error: CUDA not available.'))
            sys.exit(1)

        # 250MB Tensor
        data = torch.zeros(10000, 6250, device="cuda:0") 
        print(_('\n📦 GENESIS: Created 250MB Tensor at {}').format(hex(data.data_ptr())))
        
        # Initialize Control Block
        monitor = SharedStateMonitor("gauntlet_control", create=True)
        print(_('🛡️  CONTROL BLOCK: Initialized at /dev/shm/gauntlet_control'))
        
        # =========================================================================
        # ACT 1: THE PIPELINE (Sequential)
        # =========================================================================
        print(_('\n🎬 ACT 1: THE PIPELINE (Sequential Hand-off)'))
        print(_('{}').format('=' * 60))
        
        current_tensor = data
        t_start = time.perf_counter()

        for u in UNIVERSES:
            res, meta = client.execute_cuda_ipc(
                spec=u["spec"],
                code="tensor_out[:] = tensor_in + 1.0; torch.cuda.synchronize()",
                input_tensor=current_tensor,
                output_shape=current_tensor.shape,
                output_dtype="float32",
                ipc_mode="universal",
                python_exe=u["exe"]
            )
            current_tensor = res
            print(f"   ➡️  Passed through {u['name']}... New Value: {res[0,0].item()}")

        t_pipe = (time.perf_counter() - t_start) * 1000
        print(f"   ✅ Pipeline Complete in {t_pipe:.2f}ms")
        print(_('   🏁 Final Checksum: {} (Expected 3.0)').format(current_tensor[0, 0].item()))

        # =========================================================================
        # ACT 2: THE SWARM (Concurrent Read)
        # =========================================================================
        print(_('\n🎬 ACT 2: THE SWARM (Concurrent Read)'))
        print(_('{}').format('=' * 60))
        
        # Thread Lock to prevent ctypes race condition in client library setup
        client_lock = threading.Lock()
        
        def analyzer(u):
            code = "res = tensor_in.mean().item(); torch.cuda.synchronize(); print(f'MEAN:{res}')"
            t0 = time.perf_counter()
            
            # We lock ONLY the dispatch configuration. GPU access is concurrent.
            with client_lock:
                res, meta = client.execute_cuda_ipc(
                    spec=u["spec"], code=code,
                    input_tensor=current_tensor,
                    output_shape=(1,), output_dtype="float32",
                    ipc_mode="universal", python_exe=u["exe"]
                )
                
            dt = (time.perf_counter() - t0) * 1000
            val = meta.get("stdout", "").strip().split(":")[-1]
            print(f"   ⚡ {u['name']} read data in {dt:.2f}ms | Mean: {val}")

        threads = []
        t_swarm_start = time.perf_counter()
        for u in UNIVERSES:
            t = threading.Thread(target=analyzer, args=(u,))
            threads.append(t)
            t.start()
        for t in threads: t.join()
        
        print(_('   ✅ Swarm Complete.'))

        # =========================================================================
        # ACT 3: THE GAUNTLET (High Frequency)
        # =========================================================================
        print(_('\n🎬 ACT 3: THE GAUNTLET (Rapid Random Access)'))
        print(_('{}').format('=' * 60))
        
        count = 50
        print(_('   🔄 Executing {} random swaps...').format(count))
        
        t_gauntlet = time.perf_counter()
        
        for i in range(count):
            u = random.choice(UNIVERSES)
            client.execute_cuda_ipc(
                spec=u["spec"],
                code="tensor_out[:] = tensor_in; torch.cuda.synchronize()",
                input_tensor=current_tensor,
                output_shape=current_tensor.shape,
                output_dtype="float32",
                ipc_mode="universal",
                python_exe=u["exe"]
            )
            if i % 10 == 0: sys.stdout.write(".")
            sys.stdout.flush()

        # =========================================================================
        # ACT 4: THE CRUCIBLE (Concurrent Writes with CAS)
        # =========================================================================
        print(_('\n🎬 ACT 4: THE CRUCIBLE (Concurrent Writes + Contention)'))
        print(_('{}').format('=' * 60))
        print(_('   ⚔️  3 Universes fighting to increment the tensor.'))
        print("   🛡️  Using SharedStateMonitor for arbitration.")

        total_increments = 15 # 5 per universe
        
        def fighter(u, target_count, stats):
            local_success = 0
            while local_success < target_count:
                try:
                    # CRITICAL FIX: Use IN-PLACE addition (add_) to modify shared memory.
                    # We also copy to tensor_out just to satisfy the IPC return contract.
                    code = "tensor_in.add_(1.0); tensor_out[:] = tensor_in; torch.cuda.synchronize()"
                    
                    res, meta, retries = client.execute_optimistic_write(
                        spec=u["spec"],
                        code_template=code,
                        control_block_name="gauntlet_control",
                        tensor_in=current_tensor,
                        python_exe=u["exe"]
                    )
                    
                    if retries > 0:
                        stats['collisions'] += retries
                        # print(f"      ⚠️ {u['name']} collided {retries} times")
                    
                    local_success += 1
                    stats['success'] += 1
                    sys.stdout.write(f" [{u['name'][0]}]") # Visual progress
                    sys.stdout.flush()
                    
                except Exception as e:
                    print(_('\n❌ {} DIED: {}').format(u['name'], e))
                    break

        threads = []
        stats = {'success': 0, 'collisions': 0}
        t_crucible = time.perf_counter()
        
        # Reset tensor to 0 for clean counting
        current_tensor.zero_()
        
        for u in UNIVERSES:
            t = threading.Thread(target=fighter, args=(u, 5, stats))
            threads.append(t)
            t.start()
            
        for t in threads: t.join()
        
        dt_crucible = (time.perf_counter() - t_crucible) * 1000
        final_val = current_tensor[0,0].item()
        
        print(f"\n\n   ✅ Crucible Complete in {dt_crucible:.2f}ms")
        print(_('   📊 Stats:'))
        print(_('      - Successful Writes: {}/{}').format(stats['success'], total_increments))
        print(_('      - Collisions/Retries: {}').format(stats['collisions']))
        print(_('      - Final Tensor Value: {} (Expected {})').format(final_val, float(total_increments)))
        
        if final_val == float(total_increments):
            print(_('   🏆 DATA INTEGRITY VERIFIED'))
        else:
            print(_('   ❌ DATA CORRUPTION DETECTED (Got {})').format(final_val))

        # Clean up
        monitor.close()
        monitor.unlink()

if __name__ == "__main__":
    main()