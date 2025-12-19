from omnipkg.common_utils import safe_print

"""
Concurrent Worker Spawn Controller with Two-Phase Locking

This implements surgical locking where only filesystem operations block,
while memory operations happen asynchronously.

Key Innovation:
- Phase 1 (FS_READY): Filesystem cleanup done, next worker can start
- Phase 2 (READY): Memory cleanup done, worker fully operational
- Global lock only held during Phase 1 (cloaking/uncloaking)
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class WorkerPhase(Enum):
    SPAWNING = "spawning"
    FS_READY = "fs_ready"  # Filesystem safe, can spawn next
    READY = "ready"  # Fully ready
    FAILED = "failed"


@dataclass
class WorkerState:
    package_spec: str
    phase: WorkerPhase
    process: Optional[any] = None
    spawn_time: float = 0.0
    fs_ready_time: float = 0.0
    full_ready_time: float = 0.0
    error: Optional[str] = None


class ConcurrentSpawnController:
    """
    Controls concurrent worker spawning with surgical locking.

    Only Phase 1 (filesystem ops) needs global coordination.
    Phase 2 (memory ops) can happen in parallel.
    """

    def __init__(self):
        # Phase 1 lock: Only ONE worker can do filesystem ops at a time
        self._fs_lock = threading.Lock()

        # Worker registry (no lock needed, only modified by controller thread)
        self._workers: Dict[str, WorkerState] = {}

        # Spawn queue for parallel requests
        self._spawn_queue = []
        self._spawn_queue_lock = threading.Lock()
        self._spawn_event = threading.Event()

        # Controller thread
        self._controller_thread = None
        self._shutdown = False

    def start(self):
        """Start the controller thread."""
        if self._controller_thread is None:
            self._controller_thread = threading.Thread(
                target=self._controller_loop, daemon=True, name="SpawnController"
            )
            self._controller_thread.start()

    def spawn_worker(self, package_spec: str, timeout: float = 30.0) -> WorkerState:
        """
        Spawn a worker with two-phase handshake.

        Returns immediately after Phase 1 (filesystem safe).
        Phase 2 completes asynchronously.
        """
        self.start()  # Ensure controller is running

        # Check if already exists
        if package_spec in self._workers:
            worker = self._workers[package_spec]
            if worker.phase in (WorkerPhase.FS_READY, WorkerPhase.READY):
                return worker

        # Add to spawn queue
        spawn_request = {
            "package_spec": package_spec,
            "ready_event": threading.Event(),
            "result": None,
        }

        with self._spawn_queue_lock:
            self._spawn_queue.append(spawn_request)
            self._spawn_event.set()

        # Wait for Phase 1 completion
        if not spawn_request["ready_event"].wait(timeout):
            raise TimeoutError(f"Worker spawn timed out: {package_spec}")

        worker = spawn_request["result"]
        if worker.phase == WorkerPhase.FAILED:
            raise RuntimeError(f"Worker spawn failed: {worker.error}")

        return worker

    def _controller_loop(self):
        """
        Controller thread that processes spawn requests.

        Key: Only holds fs_lock during Phase 1, releases immediately.
        """
        while not self._shutdown:
            # Wait for spawn requests
            self._spawn_event.wait(timeout=1.0)
            self._spawn_event.clear()

            # Process all queued requests
            while True:
                request = None
                with self._spawn_queue_lock:
                    if self._spawn_queue:
                        request = self._spawn_queue.pop(0)
                    else:
                        break

                if request:
                    self._spawn_worker_internal(request)

    def _spawn_worker_internal(self, request: dict):
        """
        Internal spawn logic with two-phase handshake.
        """
        package_spec = request["package_spec"]
        ready_event = request["ready_event"]

        start_time = time.perf_counter()

        worker = WorkerState(
            package_spec=package_spec, phase=WorkerPhase.SPAWNING, spawn_time=start_time
        )

        self._workers[package_spec] = worker

        try:
            # ═══════════════════════════════════════════════════════
            # PHASE 1: FILESYSTEM OPERATIONS (LOCKED)
            # ═══════════════════════════════════════════════════════
            with self._fs_lock:
                safe_print(f"🔒 [SPAWN] Acquired FS lock for {package_spec}")

                # Spawn process
                process = self._create_daemon_process(package_spec)
                worker.process = process

                # Wait for FS_READY signal
                fs_ready = self._wait_for_phase(process, WorkerPhase.FS_READY, timeout=15.0)

                if not fs_ready:
                    raise RuntimeError("Phase 1 timeout - filesystem cleanup failed")

                worker.phase = WorkerPhase.FS_READY
                worker.fs_ready_time = time.perf_counter()

                fs_duration = (worker.fs_ready_time - start_time) * 1000
                safe_print(f"✅ [SPAWN] Phase 1 done: {package_spec} ({fs_duration:.0f}ms)")

            # ═══════════════════════════════════════════════════════
            # LOCK RELEASED - NEXT WORKER CAN START CLOAKING
            # ═══════════════════════════════════════════════════════

            # Signal caller that filesystem is safe
            request["result"] = worker
            ready_event.set()

            # ═══════════════════════════════════════════════════════
            # PHASE 2: MEMORY OPERATIONS (ASYNC, NO LOCK)
            # ═══════════════════════════════════════════════════════
            safe_print(f"🧠 [SPAWN] Phase 2 starting for {package_spec} (non-blocking)...")

            # Wait for final READY signal (doesn't block other spawns)
            full_ready = self._wait_for_phase(process, WorkerPhase.READY, timeout=10.0)

            if full_ready:
                worker.phase = WorkerPhase.READY
                worker.full_ready_time = time.perf_counter()

                total_duration = (worker.full_ready_time - start_time) * 1000
                phase2_duration = (worker.full_ready_time - worker.fs_ready_time) * 1000

                safe_print(
                    f"✅ [SPAWN] Phase 2 done: {package_spec} "
                    f"(total: {total_duration:.0f}ms, phase2: {phase2_duration:.0f}ms)"
                )
            else:
                safe_print(
                    f"⚠️  [SPAWN] Phase 2 timeout: {package_spec} (worker functional but slower)"
                )

        except Exception as e:
            worker.phase = WorkerPhase.FAILED
            worker.error = str(e)
            request["result"] = worker
            ready_event.set()
            safe_print(f"❌ [SPAWN] Failed: {package_spec} - {e}")

    def _create_daemon_process(self, package_spec: str):
        """Create the actual daemon process (placeholder)."""
        # This would be your actual DaemonProxy creation
        # For now, simulating with a mock


try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

    # In real code, this would be:
    # from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy
    # client = DaemonClient()
    # return DaemonProxy(client, package_spec)
    # return {"spec": package_spec}  # Mock

    def _wait_for_phase(self, process, target_phase: WorkerPhase, timeout: float) -> bool:
        """
        Wait for a specific phase signal from the worker.

        In real implementation, this would read from the process stdout.
        """
        # Mock implementation - in real code, read JSON from process.stdout
        # and check for {'status': 'FS_READY'} or {'status': 'READY'}

        if target_phase == WorkerPhase.FS_READY:
            time.sleep(0.5)  # Simulate filesystem cleanup time
            return True
        elif target_phase == WorkerPhase.READY:
            time.sleep(0.3)  # Simulate memory cleanup time
            return True

        return False

    def get_stats(self) -> dict:
        """Get spawn statistics."""
        stats = {
            "total_workers": len(self._workers),
            "by_phase": {},
            "average_fs_time_ms": 0.0,
            "average_total_time_ms": 0.0,
        }

        for phase in WorkerPhase:
            stats["by_phase"][phase.value] = sum(
                1 for w in self._workers.values() if w.phase == phase
            )

        ready_workers = [
            w
            for w in self._workers.values()
            if w.phase in (WorkerPhase.FS_READY, WorkerPhase.READY)
        ]

        if ready_workers:
            avg_fs = sum((w.fs_ready_time - w.spawn_time) * 1000 for w in ready_workers) / len(
                ready_workers
            )

            stats["average_fs_time_ms"] = avg_fs

            fully_ready = [w for w in ready_workers if w.phase == WorkerPhase.READY]
            if fully_ready:
                avg_total = sum(
                    (w.full_ready_time - w.spawn_time) * 1000 for w in fully_ready
                ) / len(fully_ready)
                stats["average_total_time_ms"] = avg_total

        return stats


# ═══════════════════════════════════════════════════════════════
# USAGE EXAMPLE
# ═══════════════════════════════════════════════════════════════


def demo_concurrent_spawning():
    """
    Demonstrate concurrent worker spawning with surgical locking.
    """
    safe_print("╔══════════════════════════════════════════════════════════════╗")
    safe_print("║  🧪 CONCURRENT SPAWN STRESS TEST                            ║")
    safe_print("╚══════════════════════════════════════════════════════════════╝\n")

    controller = ConcurrentSpawnController()

    # Spawn 3 workers in parallel
    packages = ["tensorflow==2.12.0", "tensorflow==2.13.0", "tensorflow==2.20.0"]

    start_time = time.perf_counter()

    def spawn_worker(pkg):
        """Spawn a single worker."""
        worker_start = time.perf_counter()
        try:
            worker = controller.spawn_worker(pkg)
            elapsed = (time.perf_counter() - worker_start) * 1000
            safe_print(f"   ✅ Spawned {pkg} in {elapsed:.0f}ms (Phase 1)")
            return worker
        except Exception as e:
            safe_print(f"   ❌ Failed to spawn {pkg}: {e}")
            return None

    # Launch all spawns in parallel
    threads = []
    for pkg in packages:
        thread = threading.Thread(target=spawn_worker, args=(pkg,))
        threads.append(thread)
        thread.start()

    # Wait for all Phase 1 completions
    for thread in threads:
        thread.join()

    elapsed = (time.perf_counter() - start_time) * 1000

    safe_print(f"\n📊 All workers Phase 1 complete in {elapsed:.0f}ms")
    safe_print("\n📈 Statistics:")

    stats = controller.get_stats()
    print(f"   - Total workers: {stats['total_workers']}")
    print(f"   - Average FS time: {stats['average_fs_time_ms']:.0f}ms")
    print(f"   - Average total time: {stats['average_total_time_ms']:.0f}ms")

    safe_print("\n🎯 Key Insight:")
    print(f"   - Sequential FS time would be: {stats['average_fs_time_ms'] * 3:.0f}ms")
    print(f"   - Actual parallel time: {elapsed:.0f}ms")
    print(f"   - Speedup: {(stats['average_fs_time_ms'] * 3 / elapsed):.1f}x")


if __name__ == "__main__":
    demo_concurrent_spawning()
