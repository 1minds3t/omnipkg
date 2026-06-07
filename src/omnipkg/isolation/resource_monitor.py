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


def get_idle_policy() -> dict:
    """
    Fetch the live idle-worker config from the daemon.

    Returns a dict keyed by display version string (e.g. "3.11") whose
    values are (target_count, python_exe).  Returns {} if the daemon is
    unreachable or the import failed.
    """
    if not DaemonClient or not WorkerPoolDaemon or not WorkerPoolDaemon.is_running():
        return {}
    try:
        client = DaemonClient()
        client.auto_start = False
        result = client.get_idle_config()
        if not result.get("success"):
            return {}
        policy = {}
        for exe, count in result.get("config", {}).items():
            ver = _extract_python_version(exe, exe)
            policy[ver] = (count, exe)
        return policy
    except Exception:
        return {}


def _set_idle_for_version(ver: str, exe: str, count: int) -> bool:
    """Push a single idle-count change to the daemon. Returns True on success."""
    if not DaemonClient:
        return False
    try:
        client = DaemonClient()
        client.auto_start = False
        result = client.set_idle_config(exe, count)
        return bool(result.get("success"))
    except Exception:
        return False


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


def _do_kill(to_kill: list):
    """Kill a list of process dicts; return (killed, total)."""
    killed = 0
    for p in to_kill:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/PID", p["pid"]],
                               capture_output=True, check=False)
            else:
                import signal as _sig
                os.kill(int(p["pid"]), _sig.SIGTERM)
            safe_print(f"  ✅ Killed PID {p['pid']} ({format_memory(p['rss'])})")
            killed += 1
        except Exception as e:
            safe_print(f"  ❌ Failed PID {p['pid']}: {e}")
    return killed, len(to_kill)


def _menu_kill(workers: dict, idle_workers_by_version: dict):
    """Interactive kill sub-menu (one-time process kill, not config change)."""
    all_killable = []
    for wt, procs in sorted(workers.items()):
        for p in procs:
            all_killable.append(("active", wt, p))
    for pv, procs in sorted(idle_workers_by_version.items()):
        for p in procs:
            all_killable.append(("idle", f"Python {pv}", p))

    if not all_killable:
        safe_print("  No killable workers found.")
        return

    print()
    print("=" * 120)
    safe_print("💀 KILL WORKERS  (one-time — daemon will replenish per your idle policy)")
    print("=" * 120)
    safe_print("  NOTE: This kills the process NOW but does not change your idle policy.")
    safe_print("        The daemon will respawn workers to match your configured counts.")
    safe_print("        To permanently reduce idle RAM → use [i] Configure idle policy instead.")
    print()
    safe_print("  Shortcuts:  [a] stale (>24h)  [f] fat (>150MB)  [A] ALL idle  [q] back")
    safe_print("  Or enter comma-separated numbers or PIDs from the list below.")
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

    resp = input("\n  Kill> ").strip()

    to_kill = []
    rl = resp.lower()
    if rl in ("q", "", "b", "back"):
        return
    elif rl == "a":
        to_kill = [p for _, _, p in all_killable if p["elapsed"] > 86400]
        safe_print(f"  Killing {len(to_kill)} stale workers...")
    elif rl == "f":
        to_kill = [p for _, _, p in all_killable if p["rss"] / 1024 > 150]
        safe_print(f"  Killing {len(to_kill)} fat workers...")
    elif resp.upper() == "A":
        to_kill = [p for kind, _, p in all_killable if kind == "idle"]
        safe_print(f"  Killing ALL {len(to_kill)} idle workers...")
    else:
        requested = [x.strip() for x in resp.replace(" ", ",").split(",") if x.strip()]
        pid_lookup = {p["pid"]: p for _, _, p in all_killable}
        num_lookup = {str(i + 1): p for i, (_, _, p) in enumerate(all_killable)}
        for r in requested:
            if r in pid_lookup:
                to_kill.append(pid_lookup[r])
            elif r in num_lookup:
                to_kill.append(num_lookup[r])
            else:
                safe_print(f"  ⚠️  Unknown: {r}")

    if to_kill:
        killed, total = _do_kill(to_kill)
        safe_print(f"\n  Done: {killed}/{total} workers killed")
        if killed > 0:
            safe_print("  💡 Daemon will replenish idle pool per your policy.")
            safe_print("     To reduce that replenishment → [i] Configure idle policy")


def _menu_idle_config(idle_workers_by_version: dict, idle_policy: dict):
    """
    Interactive idle-policy configurator.

    Shows current policy, lets the user edit counts per-version, and
    pushes changes to the daemon via set_idle_config.  Explains the
    RAM trade-off inline so users can make an informed decision.
    """
    if not idle_policy:
        safe_print("  ⚠️  Could not read idle policy from daemon (is it running?)")
        safe_print("  💡  8pkg daemon start  →  then re-open the monitor")
        return

    # Merge versions from live workers and configured policy
    all_versions = sorted(set(list(idle_policy.keys()) + list(idle_workers_by_version.keys())))

    while True:
        print()
        print("=" * 120)
        safe_print("⚙️  CONFIGURE IDLE WORKER POLICY")
        print("=" * 120)
        safe_print("  How many warm Python workers to keep running at all times.")
        print()
        safe_print("  TRADE-OFF:")
        safe_print("    More workers  →  instant 8pkg commands, higher idle RAM")
        safe_print("    Fewer workers →  first command slightly slower (~150–600ms cold start)")
        safe_print("    0 workers     →  no idle RAM, but every invocation pays the startup cost")
        print()
        safe_print("  RECOMMENDATION:")
        safe_print("    - 32 GB RAM machine  → keep 2 for your active version, 1 for others")
        safe_print("    - 16 GB RAM machine   → keep 1 for your active version, 0 for others")
        safe_print("    - 8 GB RAM machine    → 0 for everything, or 1 for your main version only")
        print("-" * 120)
        print(f"  {'#':<4} {'Version':<10} {'Target':>8}   {'Live workers':>14}   {'Est. idle RAM':>14}")
        safe_print(f"  {'─'*4} {'─'*10} {'─'*8}   {'─'*14}   {'─'*14}")
        for i, ver in enumerate(all_versions):
            count, exe = idle_policy.get(ver, (0, ""))
            live = len(idle_workers_by_version.get(ver, []))
            est_mb = count * 90
            print(f"  [{i+1:<2}] Python {ver:<6} {count:>8}   {live:>14} live   ~{est_mb:>10} MB")
        print("-" * 120)
        total_target = sum(idle_policy.get(v, (0,))[0] for v in all_versions)
        print(f"  {'':4} {'TOTAL':<10} {total_target:>8}                        ~{total_target*90:>10} MB")
        print()
        safe_print("  Options:")
        safe_print("    Enter a number [1–N] to edit that version's count")
        safe_print("    [d] smart defaults  (2 for your active Python, 1 for rest)")
        safe_print("    [0] disable ALL idle workers (max RAM savings)")
        safe_print("    [q] back / done")
        print("-" * 120)

        resp = input("\n  Config> ").strip().lower()

        if resp in ("q", "b", "back", ""):
            break

        elif resp == "0":
            print()
            confirm = input("  ⚠️  Disable ALL idle workers? This saves ~{:.0f} MB but slows cold starts. [y/N] ".format(
                total_target * 90)).strip().lower()
            if confirm == "y":
                changed = 0
                for ver in all_versions:
                    unused, exe = idle_policy.get(ver, (0, ""))
                    if exe and _set_idle_for_version(ver, exe, 0):
                        idle_policy[ver] = (0, exe)
                        changed += 1
                safe_print(f"  ✅ Disabled idle workers for {changed} version(s).")
                safe_print("  💡 Workers still running will be reaped when they time out.")
            else:
                safe_print("  No change.")

        elif resp == "d":
            # Smart defaults: detect active python from own executable
            own_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            changed = 0
            for ver in all_versions:
                unused, exe = idle_policy.get(ver, (0, ""))
                if not exe:
                    continue
                new_count = 2 if ver == own_ver else 1
                if _set_idle_for_version(ver, exe, new_count):
                    idle_policy[ver] = (new_count, exe)
                    safe_print(f"  ✅ Python {ver}: {new_count} worker(s)")
                    changed += 1
            if changed:
                safe_print(f"\n  Smart defaults applied to {changed} version(s).")
                safe_print(f"  (Active Python: {own_ver} → 2, others → 1)")
            else:
                safe_print("  ⚠️  Could not apply defaults — daemon may be unreachable.")

        else:
            # Try to parse as a menu number
            try:
                idx = int(resp) - 1
                if not (0 <= idx < len(all_versions)):
                    raise ValueError
            except ValueError:
                safe_print(f"  ⚠️  Unknown option: {resp!r}")
                continue

            ver = all_versions[idx]
            current_count, exe = idle_policy.get(ver, (0, ""))
            if not exe:
                safe_print(f"  ⚠️  No executable path known for Python {ver}. "
                            f"Run 'omnipkg daemon idle --python {ver} --count N' from CLI.")
                continue

            print()
            safe_print(f"  Python {ver}  (current: {current_count} worker(s)  ≈ {current_count*90} MB idle)")
            safe_print(f"  exe: {exe}")
            print()
            safe_print("  Enter new count (0 = disabled, recommended max = 4):")
            raw = input(f"  Count [{current_count}]> ").strip()
            if raw == "":
                safe_print("  No change.")
                continue
            try:
                new_count = int(raw)
                if new_count < 0:
                    raise ValueError
            except ValueError:
                safe_print(f"  ⚠️  Invalid count: {raw!r}  (must be 0 or a positive integer)")
                continue

            delta_mb = (new_count - current_count) * 90
            sign = "+" if delta_mb >= 0 else ""
            safe_print(f"  Change: {current_count} → {new_count} workers  ({sign}{delta_mb} MB estimated idle RAM)")
            if new_count == 0:
                safe_print("  ℹ️   Workers for this version will drain after the current idle timeout.")
            confirm = input("  Apply? [Y/n] ").strip().lower()
            if confirm in ("", "y", "yes"):
                if _set_idle_for_version(ver, exe, new_count):
                    idle_policy[ver] = (new_count, exe)
                    safe_print(f"  ✅ Python {ver}: set to {new_count} worker(s).")
                else:
                    safe_print(f"  ❌ Failed to update — is the daemon running?")
            else:
                safe_print("  Cancelled.")


def _run_interactive_controls(
    workers: dict,
    idle_workers_by_version: dict,
    idle_policy: dict,
    stale: list,
    fat: list,
):
    """Top-level daemon management menu shown after the stats block."""
    while True:
        print()
        print("=" * 120)
        safe_print("🎛️  DAEMON CONTROLS")
        print("=" * 120)
        alerts = []
        if stale:
            alerts.append(f"⚠️  {len(stale)} stale worker(s) (>24h)")
        if fat:
            alerts.append(f"🐘 {len(fat)} fat worker(s) (>150 MB)")
        if alerts:
            safe_print("  Alerts:  " + "   ".join(alerts))
            print()
        safe_print("  [i]  Configure idle policy  ← change persistent RAM usage")
        safe_print("  [k]  Kill workers            ← one-time process kill")
        safe_print("  [q]  Quit / do nothing")
        print("-" * 120)

        resp = input("\n  > ").strip().lower()

        if resp in ("q", "", "n", "no"):
            break
        elif resp == "i":
            _menu_idle_config(idle_workers_by_version, idle_policy)
        elif resp == "k":
            _menu_kill(workers, idle_workers_by_version)
        else:
            safe_print(f"  ⚠️  Unknown option: {resp!r}")


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
        safe_print(f"\n🎮 GPU: {g['util']}% util | VRAM {g['used_mb']}MB / {g['total_mb']}MB ({pct:.1f}%)")

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

    # ── IDLE POLICY BLOCK ─────────────────────────────────────────────────
    idle_policy = get_idle_policy()
    if idle_policy:
        print()
        print("=" * 120)
        safe_print("⚙️  IDLE WORKER POLICY  (persistent — survives daemon restarts)")
        print("=" * 120)
        safe_print("  Workers kept warm in the background so your first 8pkg command is instant.")
        safe_print("  Each sleeping idle worker costs ~75–120 MB RSS.  Set count=0 to disable a version.")
        print("-" * 120)
        policy_total_workers = 0
        for ver in sorted(idle_policy):
            count, exe = idle_policy[ver]
            policy_total_workers += count
            # Show actual RAM for this version if we have live data
            actual_ram_mb = None
            if ver in idle_workers_by_version:
                actual_ram_mb = sum(p["rss"] for p in idle_workers_by_version[ver]) / 1024
            ram_str = f"  (actual: {actual_ram_mb:.0f} MB)" if actual_ram_mb is not None else ""
            status = "✅" if count > 0 else "🔇"
            print(f"  {status}  Python {ver:<6}  target: {count} worker(s){ram_str}")
        print("-" * 120)
        estimated_mb = policy_total_workers * 90
        print(f"  Configured total:  {policy_total_workers} worker(s)  ≈ {estimated_mb} MB baseline idle RAM")
        print()
        safe_print("  💡 Change via CLI:")
        safe_print("       8pkg daemon idle --python 3.11 --count 2   # keep 2 warm for 3.11")
        safe_print("       8pkg daemon idle --python 3.9  --count 0   # disable for 3.9")
        safe_print("       8pkg daemon idle --python all              # show full config")
        print("=" * 120)

    if idle_workers_by_version and not watch_mode:
        # ── STALE / FAT DETECTION ─────────────────────────────────────────
        stale = [(pv, p) for pv, procs in idle_workers_by_version.items()
                 for p in procs if p["elapsed"] > 86400]
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

        # Use is_interactive_session() — the single source of truth that respects
        # OMNIPKG_NONINTERACTIVE, CI, _OMNIPKG_ISATTY, Docker, and -y/--non-interactive.
        from omnipkg.common_utils import is_interactive_session
        if is_interactive_session():
            try:
                _run_interactive_controls(
                    workers, idle_workers_by_version, idle_policy, stale, fat
                )
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