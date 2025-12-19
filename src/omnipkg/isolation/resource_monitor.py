#!/usr/bin/env python3
"""
omnipkg Daemon Resource Monitor
Shows detailed CPU, RAM, and GPU usage for all daemon workers
Run with: python -m omnipkg.isolation.resource_monitor [--watch]
"""

import re
import subprocess
import sys
import time
from collections import defaultdict


def run_cmd(cmd):
    """Execute shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout
    except Exception as e:
        return f"Error: {e}"


def parse_ps_output():
    """Get detailed process info using ps"""
    # Get all omnipkg-related processes
    cmd = """ps -eo pid,ppid,%cpu,%mem,rss,vsz,etimes,cmd | grep -E 'worker_daemon.py|tmp.*_.*\.py|omnipkg' | grep -v grep"""
    output = run_cmd(cmd)

    processes = []
    lines = output.strip().split("\n")

    for line in lines:
        if not line:
            continue
        parts = line.split(None, 7)
        if len(parts) >= 8:
            try:
                processes.append(
                    {
                        "pid": parts[0],
                        "ppid": parts[1],
                        "cpu": float(parts[2]),
                        "mem": float(parts[3]),
                        "rss": int(parts[4]),  # RAM in KB
                        "vsz": int(parts[5]),  # Virtual memory in KB
                        "elapsed": int(parts[6]),  # Time in seconds
                        "cmd": parts[7],
                    }
                )
            except (ValueError, IndexError):
                continue

    return processes


def parse_nvidia_smi():
    """Get GPU memory usage per process"""
    cmd = (
        "nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null"
    )
    output = run_cmd(cmd)

    gpu_usage = {}
    for line in output.strip().split("\n"):
        if line and not line.startswith("Error"):
            parts = line.split(",")
            if len(parts) == 2:
                try:
                    pid = parts[0].strip()
                    mem_mb = int(parts[1].strip())
                    gpu_usage[pid] = mem_mb
                except ValueError:
                    continue

    return gpu_usage


def get_gpu_summary():
    """Get overall GPU stats"""
    cmd = "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null"
    output = run_cmd(cmd)

    if output and not output.startswith("Error"):
        parts = output.strip().split(",")
        if len(parts) == 3:
            try:
                return {
                    "util": int(parts[0].strip()),
                    "used_mb": int(parts[1].strip()),
                    "total_mb": int(parts[2].strip()),
                }
            except ValueError:
                pass

    return None


def identify_worker_type(cmd):
    """Identify what type of worker this is"""
    if "worker_daemon.py start" in cmd:
        return "PERSISTENT_WORKER"
    elif "8pkg daemon start" in cmd:
        return "DAEMON_MANAGER"

    # Extract package name from temp file
    match = re.search(r"tmp\w+_(.*?)__(.*?)\.py", cmd)
    if match:
        package = match.group(1)
        version = match.group(2)

        # Determine Python version
        if "python3.9" in cmd:
            py_ver = "3.9"
        elif "python3.10" in cmd:
            py_ver = "3.10"
        elif "python3.11" in cmd:
            py_ver = "3.11"
        else:
            py_ver = "3.x"

        return f"{package}=={version} (py{py_ver})"

    return "OTHER"


def format_memory(kb):
    """Format memory from KB to human readable"""
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f}MB"
    else:
        gb = mb / 1024
        return f"{gb:.2f}GB"


def format_time(seconds):
    """Format elapsed time"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def clear_screen():
    """Clear terminal screen"""
    print("\033[2J\033[H", end="")


def print_stats(watch_mode=False):
    """Print current statistics"""
    if watch_mode:
        clear_screen()

    print("=" * 120)
    print("🔥 OMNIPKG DAEMON RESOURCE MONITOR 🔥".center(120))
    print("=" * 120)

    # Get GPU summary
    gpu_summary = get_gpu_summary()
    if gpu_summary:
        gpu_util = gpu_summary["util"]
        gpu_used = gpu_summary["used_mb"]
        gpu_total = gpu_summary["total_mb"]
        gpu_pct = (gpu_used / gpu_total * 100) if gpu_total > 0 else 0

        print(
            f"\n🎮 GPU OVERVIEW: Utilization: {gpu_util}% | VRAM: {gpu_used}MB / {gpu_total}MB ({gpu_pct:.1f}%)"
        )

    print()

    # Get process info
    processes = parse_ps_output()
    gpu_usage = parse_nvidia_smi()

    if not processes:
        print("❌ No omnipkg daemon workers found!")
        return

    # Categorize processes
    workers = defaultdict(list)
    daemon_managers = []
    persistent_workers = []

    for proc in processes:
        worker_type = identify_worker_type(proc["cmd"])

        # Add GPU usage if available
        proc["gpu_mb"] = gpu_usage.get(proc["pid"], 0)

        if worker_type == "DAEMON_MANAGER":
            daemon_managers.append(proc)
        elif worker_type == "PERSISTENT_WORKER":
            persistent_workers.append(proc)
        else:
            workers[worker_type].append(proc)

    # Print daemon manager
    if daemon_managers:
        print("🎛️  DAEMON MANAGER:")
        print("-" * 120)
        for proc in daemon_managers:
            gpu_str = f"GPU: {proc['gpu_mb']:>4}MB" if proc["gpu_mb"] > 0 else "GPU:   --"
            print(
                f"  PID {proc['pid']:>6} | CPU: {proc['cpu']:>5.1f}% | RAM: {format_memory(proc['rss']):>8} | "
                f"VIRT: {format_memory(proc['vsz']):>8} | {gpu_str} | Running: {format_time(proc['elapsed'])}"
            )
        print()

    # Print persistent workers
    if persistent_workers:
        print("🔄 PERSISTENT WORKERS (Long-lived daemon processes):")
        print("-" * 120)
        for proc in persistent_workers:
            gpu_str = f"GPU: {proc['gpu_mb']:>4}MB" if proc["gpu_mb"] > 0 else "GPU:   --"
            print(
                f"  PID {proc['pid']:>6} | CPU: {proc['cpu']:>5.1f}% | RAM: {format_memory(proc['rss']):>8} | "
                f"VIRT: {format_memory(proc['vsz']):>8} | {gpu_str} | Uptime: {format_time(proc['elapsed'])}"
            )
        print()

    # Print active workers
    if workers:
        print("⚙️  ACTIVE WORKERS (Package-specific bubbles):")
        print("-" * 120)

        total_cpu = 0
        total_ram_mb = 0
        total_gpu_mb = 0
        worker_count = 0

        for worker_type in sorted(workers.keys()):
            if worker_type == "OTHER":
                continue

            procs = workers[worker_type]
            print(f"\n📦 {worker_type}")

            for proc in procs:
                worker_count += 1
                total_cpu += proc["cpu"]
                total_ram_mb += proc["rss"] / 1024
                total_gpu_mb += proc["gpu_mb"]

                gpu_str = f"GPU: {proc['gpu_mb']:>4}MB" if proc["gpu_mb"] > 0 else "GPU:   --"

                print(
                    f"  PID {proc['pid']:>6} | CPU: {proc['cpu']:>5.1f}% | RAM: {format_memory(proc['rss']):>8} | "
                    f"VIRT: {format_memory(proc['vsz']):>8} | {gpu_str} | Age: {format_time(proc['elapsed'])}"
                )

        # Print summary
        print()
        print("=" * 120)
        print("📊 WORKER SUMMARY STATISTICS")
        print("=" * 120)
        print(f"  Active Workers:         {worker_count}")
        print(f"  Total CPU Usage:        {total_cpu:.1f}%")
        print(f"  Total RAM:              {total_ram_mb:.1f}MB ({total_ram_mb/1024:.2f}GB)")
        print(f"  Total GPU VRAM:         {total_gpu_mb}MB ({total_gpu_mb/1024:.2f}GB)")
        if worker_count > 0:
            print(f"  Average RAM per Worker: {total_ram_mb/worker_count:.1f}MB")
            print(f"  Average GPU per Worker: {total_gpu_mb/worker_count:.1f}MB")
        print("=" * 120)

        # Print efficiency metrics
        print()
        print("🎯 EFFICIENCY METRICS:")
        print("-" * 120)
        if worker_count > 0:
            docker_efficiency = (500 * worker_count) / total_ram_mb if total_ram_mb > 0 else 0
            venv_efficiency = (100 * worker_count) / total_ram_mb if total_ram_mb > 0 else 0

            print(
                f"  💡 Memory Per Worker:    {total_ram_mb/worker_count:.1f}MB (omnipkg) vs 500MB (Docker) vs 100MB (venv)"
            )
            print(f"  🔥 vs Docker:            {docker_efficiency:.1f}x MORE EFFICIENT")
            print(f"  🔥 vs VirtualEnv:        {venv_efficiency:.1f}x MORE EFFICIENT")
            print(
                f"  🚀 Total System Footprint: Only {total_ram_mb:.1f}MB for {worker_count} parallel package versions!"
            )
        print("=" * 120)


def start_monitor(watch_mode=False):
    """Entry point for the monitor"""
    if watch_mode:
        print("Starting watch mode (Ctrl+C to exit)...")
        time.sleep(1)
        try:
            while True:
                print_stats(watch_mode=True)
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n\nExiting watch mode...")
    else:
        print_stats(watch_mode=False)
        print("\n💡 Tip: Use --watch or -w flag for live monitoring")


if __name__ == "__main__":
    watch = "--watch" in sys.argv or "-w" in sys.argv
    start_monitor(watch)
