from omnipkg.common_utils import safe_print
#!/usr/bin/env python3
"""
omnipkg Daemon Resource Monitor - Windows + Unix compatible
Uses psutil for cross-platform process info instead of ps/grep.
"""

import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from omnipkg.i18n import _

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from omnipkg.isolation.worker_daemon import DaemonClient, WorkerPoolDaemon
except ImportError:
    DaemonClient = None
    WorkerPoolDaemon = None

IS_WINDOWS = sys.platform == "win32"

# ========================================
# BENCHMARK BASELINES
# ========================================
BASE_OVERHEAD = {"docker": 400, "venv": 35, "conda": 150, "pyenv": 50}
PACKAGE_FOOTPRINTS = {
    "rich": 15, "click": 10, "requests": 20, "pydantic": 25,
    "numpy": 30, "pandas": 60, "scipy": 50, "matplotlib": 45,
    "torch": 350, "tensorflow": 400, "jax": 300, "transformers": 200,
}
BASELINE_METRICS = {
    "docker": {"base_overhead_mb": 400, "startup_ms": 800, "context_switch_overhead": 0.5},
    "venv":   {"base_overhead_mb": 35,  "startup_ms": 150, "context_switch_overhead": 0.1},
    "conda":  {"base_overhead_mb": 150, "startup_ms": 350, "context_switch_overhead": 0.15},
    "pyenv":  {"base_overhead_mb": 50,  "startup_ms": 200, "context_switch_overhead": 0.12},
}


def estimate_package_memory(worker_name):
    w = worker_name.lower()
    for pkg, mb in PACKAGE_FOOTPRINTS.items():
        if pkg in w:
            return mb
    return 40


def get_daemon_worker_info():
    """Connect to daemon and get PID-to-spec mapping."""
    if not DaemonClient or not WorkerPoolDaemon or not WorkerPoolDaemon.is_running():
        return {}
    client = DaemonClient()
    client.auto_start = False
    status = client.status()
    if not status.get("success"):
        return {}
    pid_map = {}
    for spec_key, info in status.get("worker_details", {}).items():
        pid = info.get("pid")
        if not pid:
            continue

        # Use the explicit fields added in _get_status (preferred)
        pkg_spec = info.get("pkg_spec") or spec_key.split("::")[0]
        python_exe = info.get("python_exe", "")

        # Extract python version from python_exe path
        py_ver = "?.?"
        import re
        for search_str in [python_exe, spec_key]:
            if not search_str:
                continue
            m = re.search(r"cpython[\-_](3\.\d+)", str(search_str), re.IGNORECASE)
            if m:
                py_ver = m.group(1)
                break
            m = re.search(r"python(3\.\d+)", str(search_str), re.IGNORECASE)
            if m:
                py_ver = m.group(1)
                break
            m = re.search(r"/Versions/(3\.\d+)/", str(search_str))
            if m:
                py_ver = m.group(1)
                break
            m = re.search(r"python3[\-_](\d+)", str(search_str), re.IGNORECASE)
            if m:
                py_ver = "3." + m.group(1)
                break

        pid_map[str(pid)] = f"{pkg_spec} (py{py_ver})"
    return pid_map


def _is_omnipkg_process(cmdline):
    cmd_str = " ".join(cmdline).lower()
    return any(kw in cmd_str for kw in [
        "worker_daemon", "omnipkg", "8pkg", "_idle", "omnipkg.isolation", "omnipkgloader",
    ])


def get_processes():
    """Cross-platform process collection via psutil, fallback to ps on Unix."""
    if HAS_PSUTIL:
        procs = []
        now = time.time()
        for p in psutil.process_iter(["pid", "ppid", "cmdline", "create_time",
                                       "cpu_percent", "memory_info", "memory_percent"]):
            try:
                info = p.info
                cmdline = info["cmdline"] or []
                if not _is_omnipkg_process(cmdline):
                    continue
                mem = info["memory_info"]
                rss_kb = (mem.rss // 1024) if mem else 0
                vsz_kb = (getattr(mem, "vms", mem.rss) // 1024) if mem else 0
                elapsed = int(now - (info["create_time"] or now))
                procs.append({
                    "pid":     str(info["pid"]),
                    "ppid":    str(info["ppid"] or 0),
                    "cpu":     info["cpu_percent"] or 0.0,
                    "mem":     info["memory_percent"] or 0.0,
                    "rss":     rss_kb,
                    "vsz":     vsz_kb,
                    "elapsed": elapsed,
                    "cmd":     " ".join(cmdline),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return procs

    if IS_WINDOWS:
        return []

    # Unix fallback
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid,ppid,%cpu,%mem,rss,vsz,etimes,cmd"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        procs = []
        for line in r.stdout.strip().splitlines():
            if not any(kw in line for kw in ["worker_daemon", "omnipkg", "_idle"]):
                continue
            parts = line.split(None, 7)
            if len(parts) >= 8:
                try:
                    procs.append({
                        "pid": parts[0], "ppid": parts[1],
                        "cpu": float(parts[2]), "mem": float(parts[3]),
                        "rss": int(parts[4]), "vsz": int(parts[5]),
                        "elapsed": int(parts[6]), "cmd": parts[7],
                    })
                except (ValueError, IndexError):
                    continue
        return procs
    except Exception:
        return []


def _run_nvidia(args):
    try:
        r = subprocess.run(["nvidia-smi"] + args, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=3)
        return r.stdout
    except Exception:
        return ""


def parse_nvidia_smi():
    out = _run_nvidia(["--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"])
    gpu = {}
    for line in out.strip().splitlines():
        parts = line.split(",")
        if len(parts) == 2:
            try:
                gpu[parts[0].strip()] = int(parts[1].strip())
            except ValueError:
                pass
    return gpu


def get_gpu_summary():
    out = _run_nvidia(["--query-gpu=utilization.gpu,memory.used,memory.total",
                       "--format=csv,noheader,nounits"])
    if out:
        parts = out.strip().split(",")
        if len(parts) == 3:
            try:
                return {"util": int(parts[0].strip()),
                        "used_mb": int(parts[1].strip()),
                        "total_mb": int(parts[2].strip())}
            except ValueError:
                pass
    return None


def _extract_python_version(cmd: str, exe: str = "") -> str:
    """
    Pull pythonX.Y or cpython-X.Y out of a command string or exe path.
    Handles macOS framework paths like Python.app/Contents/MacOS/Python
    by parsing the Versions/X.Y segment in the path.
    """
    for s in [exe, cmd]:
        if not s:
            continue
        # cpython-3.9.23 or cpython-3.11.9 style (managed interpreter paths)
        m = re.search(r"cpython[\-_](3\.\d+)", s, re.IGNORECASE)
        if m:
            return m.group(1)
        # python3.11 with explicit minor version
        m = re.search(r"python(3\.\d+)", s, re.IGNORECASE)
        if m:
            return m.group(1)
        # macOS framework path: .../Versions/3.11/...
        m = re.search(r"/Versions/(3\.\d+)/", s)
        if m:
            return m.group(1)
        # python3-11 or python3_11 separator style
        m = re.search(r"python3[\-_](\d+)", s, re.IGNORECASE)
        if m:
            return "3." + m.group(1)
    return "3.x"

def identify_worker_type(proc, pid_map):
    pid = proc["pid"]
    cmd = proc["cmd"]
    if pid in pid_map:
        return pid_map[pid]
    cmd_low = cmd.lower()
    if ("worker_daemon" in cmd_low and "start" in cmd_low) or        ("omnipkg.isolation.worker_daemon" in cmd_low and "start" in cmd_low) or        "8pkg daemon start" in cmd_low:
        return "DAEMON_MANAGER"
    if "_idle" in cmd_low:
        return "IDLE_WORKER_PY" + _extract_python_version(cmd, proc.get("exe", ""))
    m = re.search(r"tmp\w+_(.*?)__(.*?)\.py", cmd)
    if m:
        return f"{m.group(1).replace('_','=')}=={m.group(2)} (py{_extract_python_version(cmd)})"
    return "OTHER"


def format_memory(kb):
    mb = kb / 1024
    return f"{mb:.1f}MB" if mb < 1024 else f"{mb/1024:.2f}GB"


def format_time(seconds):
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60}s"
    return f"{seconds//3600}h {(seconds%3600)//60}m"


def clear_screen():
    if IS_WINDOWS:
        os.system("cls")
    else:
        print("\x1b[2J\x1b[H", end="")


def calculate_efficiency_metrics(workers_dict, total_ram_mb, total_gpu_mb, avg_startup_ms=None):
    if not workers_dict or total_ram_mb == 0:
        return {}
    worker_count = sum(len(p) for p in workers_dict.values())
    if worker_count == 0:
        return {}
    actual_mb_per_worker = total_ram_mb / worker_count
    estimated_startup_ms = avg_startup_ms or 5.0
    baseline_totals = {s: 0 for s in BASELINE_METRICS}
    for wt, procs in workers_dict.items():
        pkg_memory = estimate_package_memory(wt)
        for sol, bl in BASELINE_METRICS.items():
            baseline_totals[sol] += (bl["base_overhead_mb"] + pkg_memory) * len(procs)
    metrics = {}
    for sol, bl in BASELINE_METRICS.items():
        bt = baseline_totals[sol]
        metrics[sol] = {
            "memory_ratio":           bt / total_ram_mb if total_ram_mb > 0 else 0,
            "memory_saved_mb":        bt - total_ram_mb,
            "startup_ratio":          bl["startup_ms"] / estimated_startup_ms,
            "startup_saved_ms":       (bl["startup_ms"] - estimated_startup_ms) * worker_count,
            "baseline_mb_per_worker": bt / worker_count,
            "baseline_total_mb":      bt,
        }
    return {"per_worker": actual_mb_per_worker, "comparisons": metrics,
            "worker_count": worker_count, "total_ram_mb": total_ram_mb}


def print_stats(watch_mode=False):
    if watch_mode:
        clear_screen()

    if not HAS_PSUTIL and IS_WINDOWS:
        safe_print("❌ psutil required on Windows. Install it: 8pkg install psutil")
        return
    if not HAS_PSUTIL:
        safe_print("⚠️  psutil not installed — some info may be missing. Install: 8pkg install psutil")

    print("=" * 120)
    safe_print("🔥 OMNIPKG DAEMON RESOURCE MONITOR 🔥".center(120))
    print("=" * 120)

    gpu_summary = get_gpu_summary()
    if gpu_summary:
        g = gpu_summary
        pct = (g["used_mb"] / g["total_mb"] * 100) if g["total_mb"] else 0
        print(f"\n🎮 GPU: {g['util']}% util | VRAM {g['used_mb']}MB / {g['total_mb']}MB ({pct:.1f}%)")

    print()
    pid_map   = get_daemon_worker_info()
    processes = get_processes()
    gpu_usage = parse_nvidia_smi()

    if not processes:
        safe_print(_("❌ No omnipkg daemon processes found!"))
        safe_print("💡 Tip: Use --watch or -w flag for live monitoring")
        return

    workers                 = defaultdict(list)
    daemon_managers         = []
    idle_workers_by_version = defaultdict(list)

    for proc in processes:
        proc["gpu_mb"] = gpu_usage.get(proc["pid"], 0)
        wt = identify_worker_type(proc, pid_map)
        if wt == "DAEMON_MANAGER":
            daemon_managers.append(proc)
        elif wt.startswith("IDLE_WORKER_PY"):
            idle_workers_by_version[wt.replace("IDLE_WORKER_PY", "")].append(proc)
        elif wt != "OTHER":
            workers[wt].append(proc)

    if daemon_managers:
        safe_print("🎛️  DAEMON MANAGER:")
        print("-" * 120)
        for p in daemon_managers:
            g = f"GPU: {p['gpu_mb']:>4}MB" if p["gpu_mb"] else "GPU:   --"
            print(f"  PID {p['pid']:>6} | CPU: {p['cpu']:>5.1f}% | RAM: {format_memory(p['rss']):>8} | "
                  f"VIRT: {format_memory(p['vsz']):>8} | {g} | Running: {format_time(p['elapsed'])}")
        print()

    total_cpu = total_ram_mb = total_gpu_mb = worker_count = idle_count = 0
    for procs in idle_workers_by_version.values():
        for p in procs:
            idle_count   += 1
            total_ram_mb += p["rss"] / 1024
            total_gpu_mb += p["gpu_mb"]
    if workers:
        safe_print("⚙️  ACTIVE WORKERS (Package-specific bubbles):")
        print("-" * 120)
        for wt in sorted(workers):
            safe_print(f"\n📦 {wt}")
            for p in workers[wt]:
                worker_count += 1
                total_cpu    += p["cpu"]
                total_ram_mb += p["rss"] / 1024
                total_gpu_mb += p["gpu_mb"]
                g = f"GPU: {p['gpu_mb']:>4}MB" if p["gpu_mb"] else "GPU:   --"
                print(f"  PID {p['pid']:>6} | CPU: {p['cpu']:>5.1f}% | RAM: {format_memory(p['rss']):>8} | "
                      f"VIRT: {format_memory(p['vsz']):>8} | {g} | Age: {format_time(p['elapsed'])}")

    idle_workers = [p for procs in idle_workers_by_version.values() for p in procs]
    if idle_workers_by_version:
        safe_print("\n💤 IDLE WORKERS (Ready to be assigned):")
        print("-" * 120)
        for pv in sorted(idle_workers_by_version):
            procs = idle_workers_by_version[pv]
            s = "s" if len(procs) != 1 else ""
            safe_print(f"\n🐍 Python {pv} ({len(procs)} worker{s})")
            for p in procs:
                g = f"GPU: {p['gpu_mb']:>4}MB" if p["gpu_mb"] else "GPU:   --"
                print(f"  PID {p['pid']:>6} | CPU: {p['cpu']:>5.1f}% | RAM: {format_memory(p['rss']):>8} | "
                      f"VIRT: {format_memory(p['vsz']):>8} | {g} | Age: {format_time(p['elapsed'])}")

    print()
    print("=" * 120)
    safe_print("📊 WORKER SUMMARY")
    print("=" * 120)
    print(f"  Active Workers:  {worker_count}")
    print(f"  Idle Workers:    {idle_count}")
    print(f"  Total CPU:       {total_cpu:.1f}%")
    print(f"  Total RAM:       {total_ram_mb:.1f}MB ({total_ram_mb/1024:.2f}GB)")
    print(f"  Total GPU VRAM:  {total_gpu_mb}MB")
    if worker_count > 0:
        print(f"  Avg RAM/worker:  {total_ram_mb/worker_count:.1f}MB")

    if worker_count > 0:
        print()
        safe_print("🎯 EFFICIENCY vs Traditional Solutions:")
        print("-" * 120)
        eff = calculate_efficiency_metrics(workers, total_ram_mb, total_gpu_mb)
        safe_print(f"  omnipkg: {eff['per_worker']:.1f}MB per worker (RSS)")
        print()
        for sol, comp in eff["comparisons"].items():
            r = comp["memory_ratio"]
            saved = comp["memory_saved_mb"]
            sr = comp["startup_ratio"]
            if r > 1.0:
                safe_print(f"  🔥 vs {sol.upper():<8}: {r:.1f}x more efficient (saves {saved:.0f}MB, {sr:.0f}x faster startup)")
            else:
                safe_print(f"  ⚖️  vs {sol.upper():<8}: {1/r:.1f}x overhead (+{abs(saved):.0f}MB)")
        print()

    print("=" * 120)

    if idle_workers_by_version and not watch_mode:
        # ── STALE WORKER DETECTION ─────────────────────────────────────────
        stale = [(pv, p) for pv, procs in idle_workers_by_version.items()
                 for p in procs if p["elapsed"] > 86400]
        # ── FAT WORKER DETECTION (loaded heavy packages) ───────────────────
        fat = [(pv, p) for pv, procs in idle_workers_by_version.items()
               for p in procs if p["rss"] / 1024 > 150]

        if stale:
            print()
            safe_print(f"⚠️  STALE WORKERS: {len(stale)} idle >24 hours")
            print("-" * 120)
            for pv, p in stale:
                print(f"  Python {pv} | PID {p['pid']:>6} | RAM: {format_memory(p['rss']):>8} | Age: {format_time(p['elapsed'])}")

        if fat:
            print()
            safe_print(f"🐘 FAT IDLE WORKERS: {len(fat)} using >150MB (loaded heavy packages, should be evicted)")
            print("-" * 120)
            for pv, p in fat:
                print(f"  Python {pv} | PID {p['pid']:>6} | RAM: {format_memory(p['rss']):>8} | Age: {format_time(p['elapsed'])}")

        # When running via daemon CLI worker, stdin is a pipe not a TTY.
        # Check the env var set by the C dispatcher instead.
        _is_tty = sys.stdin.isatty() or os.environ.get("_OMNIPKG_ISATTY") == "1"
        if _is_tty:
            try:
                # ── BUILD FULL KILL MENU ────────────────────────────────────
                # Collect ALL workers (active + idle) into a numbered list
                all_killable = []
                for wt, procs in sorted(workers.items()):
                    for p in procs:
                        all_killable.append(("active", wt, p))
                for pv, procs in sorted(idle_workers_by_version.items()):
                    for p in procs:
                        all_killable.append(("idle", f"Python {pv}", p))

                print()
                print("=" * 120)
                safe_print("🎯 INTERACTIVE KILL MENU")
                print("=" * 120)
                safe_print("  Enter PIDs to kill, or use shortcuts:")
                safe_print("  [a] = kill all stale (>24h)  [f] = kill all fat (>150MB)")
                safe_print("  [A] = kill ALL idle           [q] = quit / do nothing")
                safe_print("  Or enter comma-separated PIDs: 1234,5678")
                print("-" * 120)
                for i, (kind, label, p) in enumerate(all_killable):
                    ram_mb = p["rss"] / 1024
                    flag = ""
                    if p["elapsed"] > 86400:
                        flag += " ⚠️ STALE"
                    if ram_mb > 150:
                        flag += " 🐘 FAT"
                    kind_icon = "⚙️ " if kind == "active" else "💤"
                    print(f"  [{i+1:>2}] {kind_icon} {label:<25} "
                          f"PID {p['pid']:>6} | RAM: {ram_mb:>7.1f}MB | "
                          f"Age: {format_time(p['elapsed'])}{flag}")
                print("-" * 120)

                resp = input("\n  Kill> ").strip().lower()

                to_kill = []
                if resp in ("q", "", "n", "no"):
                    print("  No action taken.")
                elif resp == "a":
                    to_kill = [p for _, _, p in all_killable if p["elapsed"] > 86400]
                    safe_print(f"  Killing {len(to_kill)} stale workers...")
                elif resp == "f":
                    to_kill = [p for _, _, p in all_killable if p["rss"] / 1024 > 150]
                    safe_print(f"  Killing {len(to_kill)} fat workers...")
                elif resp == "a" or resp.upper() == "A":
                    to_kill = [p for kind, _, p in all_killable if kind == "idle"]
                    safe_print(f"  Killing ALL {len(to_kill)} idle workers...")
                else:
                    # Parse comma-separated PIDs or menu numbers
                    requested = [x.strip() for x in resp.replace(" ", ",").split(",") if x.strip()]
                    pid_lookup = {p["pid"]: p for _, _, p in all_killable}
                    num_lookup = {str(i+1): p for i, (_, _, p) in enumerate(all_killable)}
                    for r in requested:
                        if r in pid_lookup:
                            to_kill.append(pid_lookup[r])
                        elif r in num_lookup:
                            to_kill.append(num_lookup[r])
                        else:
                            print(f"  ⚠️  Unknown PID/number: {r}")

                if to_kill:
                    killed = 0
                    for p in to_kill:
                        try:
                            if IS_WINDOWS:
                                subprocess.run(["taskkill", "/F", "/PID", p["pid"]],
                                               capture_output=True, check=False)
                            else:
                                import signal as _sig
                                os.kill(int(p["pid"]), _sig.SIGTERM)
                            print(f"  ✅ Killed PID {p['pid']} ({format_memory(p['rss'])})")
                            killed += 1
                        except Exception as e:
                            print(f"  ❌ Failed PID {p['pid']}: {e}")
                    safe_print(f"\n  Done: killed {killed}/{len(to_kill)} workers")
                    if killed > 0:
                        safe_print("  💡 Daemon will replenish idle pool automatically")

            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
        else:
            if stale:
                safe_print("  💡 Run: 8pkg daemon restart")
        print("=" * 120)


def start_monitor(watch_mode=False):
    if watch_mode:
        print(_("Starting watch mode (Ctrl+C to exit)..."))
        time.sleep(1)
        try:
            while True:
                print_stats(watch_mode=True)
                time.sleep(2)
        except KeyboardInterrupt:
            print(_("\n\nExiting watch mode..."))
    else:
        print_stats(watch_mode=False)
        safe_print("\n💡 Tip: Use --watch or -w flag for live monitoring")


if __name__ == "__main__":
    watch = "--watch" in sys.argv or "-w" in sys.argv
    start_monitor(watch)