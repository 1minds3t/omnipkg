#!/usr/bin/env python3
"""
bench_tag_reuse.py
==================
Proves (or disproves) that the daemon:
  1. Keeps the same worker alive across multiple calls with the same tag (HOT_REUSED)
  2. Spawns a fresh worker for a different tag (COLD_BOOT)
  3. Persists worker globals (like _CACHED_MODEL) across calls — not just the process

Why the benchmark uses unique per-run tags
------------------------------------------
Workers outlive a single script invocation — that's the whole point.
If we used fixed tag names like "bench-tag-A", the second run would always
hit a warm worker from the first run and falsely report "already hot".

Each invocation generates a unique run-ID and embeds it in the tags,
guaranteeing round 1 is always a true cold boot regardless of history.

Use --reuse-tag <tag> to explicitly prove globals survive script restarts.
"""

import sys, os, time, argparse, json, uuid
from pathlib import Path

sys.path.insert(0, str(Path.home() / "omnipkg" / "src"))
sys.path.insert(0, str(Path.home() / "omnipkg"))

from omnipkg.isolation.worker_daemon import DaemonClient

WORKER_CODE = """
import sys, json, time, os

call_id = {call_id!r}

if '_BOOT_CALL' not in globals():
    import numpy as np
    globals()['_BOOT_CALL']  = call_id
    globals()['_BOOT_TIME']  = time.time()
    globals()['_np']         = np
    globals()['_call_count'] = 1
    boot_status   = 'COLD_BOOT'
    numpy_version = np.__version__
else:
    globals()['_call_count'] += 1
    boot_status   = 'HOT_REUSED'
    numpy_version = globals()['_np'].__version__

result = {{
    'call_id':       call_id,
    'boot_status':   boot_status,
    'boot_call':     globals()['_BOOT_CALL'],
    'call_count':    globals()['_call_count'],
    'worker_pid':    os.getpid(),
    'numpy_version': numpy_version,
}}
print(json.dumps(result))
"""

def do_call(client, spec, tag, call_id, python_exe):
    t0 = time.perf_counter()
    res = client.execute_smart(
        spec=spec, code=WORKER_CODE.format(call_id=call_id),
        data=[], python_exe=python_exe, worker_tag=tag,
    )
    ms = (time.perf_counter() - t0) * 1000
    if not res.get("success"):
        return ms, None
    try:
        return ms, json.loads((res.get("result") or res.get("stdout") or "").strip())
    except Exception:
        return ms, None

def fmt(ms):
    return f"{ms:6.1f} ms" if ms < 1000 else f"{ms/1000:5.2f} s "

SEP = "─" * 72

def row(label, ms, d, want_status=None, want_pid=None):
    if d is None:
        print(f"  {label:<34} {fmt(ms)}  ❌ ERROR"); return
    ok = ((want_status is None or d['boot_status']==want_status) and
          (want_pid    is None or d['worker_pid'] ==want_pid))
    note = f"{d['boot_status']}  pid={d['worker_pid']}  calls={d['call_count']}"
    if want_status and d['boot_status'] != want_status: note += f"  (wanted {want_status})"
    if want_pid    and d['worker_pid']  != want_pid:    note += "  (wanted same PID)"
    print(f"  {label:<34} {fmt(ms)}  {'✅' if ok else '❌'} {note}")

def run_fresh(spec, python_exe):
    run_id = uuid.uuid4().hex[:8]
    tag_a, tag_b = f"bench-{run_id}-A", f"bench-{run_id}-B"
    client = DaemonClient()

    print(f"\n{'═'*72}")
    print(f"  omnipkg daemon — tag reuse benchmark  (run-id: {run_id})")
    print(f"  spec      : {spec}")
    print(f"  tag A     : {tag_a}  ← unique, guaranteed cold")
    print(f"  tag B     : {tag_b}  ← unique, guaranteed cold")
    print(f"{'═'*72}\n")

    print("Round 1 — COLD BOOT  (tag A, brand new tag, must spawn worker)")
    ms1, d1 = do_call(client, spec, tag_a, "r1-cold", python_exe)
    if d1: print(f"  PID={d1['worker_pid']}  boot_call={d1['boot_call']}  numpy={d1['numpy_version']}")
    print(f"  ⏱  {fmt(ms1)}\n"); time.sleep(0.3)

    print("Round 2 — HOT REUSE  (tag A, same worker, globals must persist)")
    ms2, d2 = do_call(client, spec, tag_a, "r2-hot", python_exe)
    if d2:
        same = d1 and d2['worker_pid']==d1['worker_pid']
        hot  = d2['boot_status']=='HOT_REUSED'
        print(f"  PID={d2['worker_pid']}  {'✅ same PID' if same else '❌ DIFFERENT PID'}")
        print(f"  {'✅ HOT_REUSED — globals persisted!' if hot else '❌ COLD_BOOT — globals were reset!'}")
        print(f"  boot_call={d2['boot_call']}  (should be 'r1-cold')")
    print(f"  ⏱  {fmt(ms2)}\n"); time.sleep(0.3)

    print("Round 3 — NEW TAG    (tag B, must be a completely separate worker)")
    ms3, d3 = do_call(client, spec, tag_b, "r3-newtag", python_exe)
    if d3:
        diff = d1 and d3['worker_pid']!=d1['worker_pid']
        cold = d3['boot_status']=='COLD_BOOT'
        print(f"  PID={d3['worker_pid']}  {'✅ different PID' if diff else '❌ SAME PID — tags not isolated!'}")
        print(f"  {'✅ COLD_BOOT — fresh isolated worker' if cold else '❌ HOT_REUSED — leaked globals from tag A!'}")
    print(f"  ⏱  {fmt(ms3)}\n"); time.sleep(0.3)

    print("Round 4 — HOT REUSE  (tag A again, must still be warm after tag B was created)")
    ms4, d4 = do_call(client, spec, tag_a, "r4-stillhot", python_exe)
    if d4:
        same = d1 and d4['worker_pid']==d1['worker_pid']
        hot  = d4['boot_status']=='HOT_REUSED'
        print(f"  PID={d4['worker_pid']}  {'✅ same' if same else '❌ DIFFERENT'}  call #{d4['call_count']} for this worker")
        print(f"  {'✅ HOT_REUSED — still warm' if hot else '❌ COLD_BOOT — was evicted while tag B ran!'}")
    print(f"  ⏱  {fmt(ms4)}\n")

    print(SEP)
    print(f"  {'Round':<34} {'Time':>9}  Result")
    print(SEP)
    pid_a = d1['worker_pid'] if d1 else None
    row("R1 cold boot  (tag A)", ms1, d1, "COLD_BOOT")
    row("R2 hot reuse  (tag A)", ms2, d2, "HOT_REUSED", pid_a)
    row("R3 new tag    (tag B)", ms3, d3, "COLD_BOOT")
    row("R4 still warm (tag A)", ms4, d4, "HOT_REUSED", pid_a)
    print(SEP)

    cold_ms = [m for m,d in [(ms1,d1),(ms3,d3)] if d and d['boot_status']=='COLD_BOOT']
    hot_ms  = [m for m,d in [(ms2,d2),(ms4,d4)] if d and d['boot_status']=='HOT_REUSED']
    if cold_ms and hot_ms:
        speedup = (sum(cold_ms)/len(cold_ms)) / (sum(hot_ms)/len(hot_ms))
        print(f"\n  Cold avg : {fmt(sum(cold_ms)/len(cold_ms))}")
        print(f"  Hot  avg : {fmt(sum(hot_ms)/len(hot_ms))}")
        print(f"  Speedup  : {speedup:.0f}×")

    r2ok = d2 and d2['boot_status']=='HOT_REUSED' and d1 and d2['worker_pid']==d1['worker_pid']
    r3ok = d3 and d3['boot_status']=='COLD_BOOT'  and d1 and d3['worker_pid']!=d1['worker_pid']
    r4ok = d4 and d4['boot_status']=='HOT_REUSED' and d1 and d4['worker_pid']==d1['worker_pid']

    print()
    if r2ok and r3ok and r4ok:
        print("  🎉 PASS — tag isolation and globals persistence confirmed.")
        print(f"\n  To also verify workers survive a full script restart, run:")
        print(f"    python bench_tag_reuse.py --reuse-tag {tag_a} --spec {spec}")
        return 0
    else:
        print("  ❌ FAIL")
        if not r2ok: print("     R2: expected HOT_REUSED on same PID — globals not persisting between calls")
        if not r3ok: print("     R3: expected COLD_BOOT on different PID — tags not isolated")
        if not r4ok: print("     R4: expected HOT_REUSED on same PID — worker was evicted")
        return 1

def run_reuse(spec, python_exe, existing_tag):
    client = DaemonClient()
    print(f"\n{'═'*72}")
    print(f"  omnipkg daemon — cross-invocation persistence check")
    print(f"  spec      : {spec}")
    print(f"  reuse-tag : {existing_tag}")
    print(f"  Expecting HOT_REUSED if the daemon kept the worker alive.")
    print(f"{'═'*72}\n")

    print("Call 1 — was the worker kept alive since the previous script run?")
    ms1, d1 = do_call(client, spec, existing_tag, "xrun-1", python_exe)
    if d1:
        hot = d1['boot_status']=='HOT_REUSED'
        print(f"  PID={d1['worker_pid']}  boot_status={d1['boot_status']}")
        print(f"  {'✅ HOT_REUSED — worker survived script restart!' if hot else '⚠️  COLD_BOOT — worker was recycled (daemon restart / idle timeout)'}")
        print(f"  boot_call={d1['boot_call']}  call_count={d1['call_count']}")
    print(f"  ⏱  {fmt(ms1)}\n")

    print("Call 2 — same session, must be HOT_REUSED")
    ms2, d2 = do_call(client, spec, existing_tag, "xrun-2", python_exe)
    if d2:
        print(f"  {'✅' if d2['boot_status']=='HOT_REUSED' else '❌'} {d2['boot_status']}  call #{d2['call_count']}")
    print(f"  ⏱  {fmt(ms2)}\n")

    if d1 and d1['boot_status']=='HOT_REUSED':
        print("  🎉 CONFIRMED — globals persisted across separate Python process invocations.")
    else:
        print("  ⚠️  Worker was cold — it was recycled since the last run.")
        print("      Normal if: daemon was restarted, or idle timeout was hit.")

def main():
    p = argparse.ArgumentParser(description="Benchmark omnipkg daemon tag isolation and persistence")
    p.add_argument("--python-version", default=None, help='e.g. "3.11"')
    p.add_argument("--spec", default="numpy==1.26.4")
    p.add_argument("--reuse-tag", default=None,
                   help="Probe an existing tag from a prior run to verify cross-invocation persistence")
    args = p.parse_args()

    if args.reuse_tag:
        run_reuse(args.spec, args.python_version, args.reuse_tag)
    else:
        sys.exit(run_fresh(args.spec, args.python_version))

if __name__ == "__main__":
    main()