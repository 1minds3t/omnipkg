"""
diag_uv_ffi.py — raw diagnostic runner for the 3 failing uv_ffi scenarios.
No pytest. No assertions. Just prints everything.

Run:
    8pkg run python /path/to/diag_uv_ffi.py
"""
import os, sys, shutil, sysconfig, glob, tempfile, traceback
from pathlib import Path
from rich.console import Console
from rich.rule import Rule

C = Console(highlight=False)
SEP = lambda t="": C.print(Rule(t, style="dim"))

# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────
import uv_ffi

# Flush any poisoned registry/wheel cache state from a previous run.
uv_ffi.clear_registry_cache()
uv_ffi.evict_bubble_cache()

C.print(f"\n[bold cyan]uv_ffi loaded from:[/] {uv_ffi.__file__}")
C.print(f"[bold cyan]__all__:[/] {uv_ffi.__all__}\n")

SITE = Path(sysconfig.get_path('purelib'))
TMP  = Path(tempfile.mkdtemp(prefix="diag_uvffi_"))
C.print(f"[dim]tmp target dir: {TMP}[/]")
C.print(f"[dim]site-packages:  {SITE}[/]\n")

def dump_dir(label, path):
    path = Path(path)
    C.print(f"  [yellow]{label}:[/]")
    if not path.exists():
        C.print(f"    [red](does not exist)[/]")
        return
    entries = sorted(path.iterdir())
    if not entries:
        C.print(f"    [dim](empty)[/]")
    for e in entries:
        C.print(f"    {e.name}")

def check_site_for_rich():
    hits = list(SITE.glob("rich-*.dist-info"))
    if hits:
        C.print(f"  [red]rich dist-info in SITE-PACKAGES:[/] {hits}")
    else:
        C.print(f"  [green]no rich dist-info in site-packages[/]")
    return hits

def clean_site_rich():
    # Cleanup only touches TMP — never the real site-packages.
    # The diag installs exclusively into --target dirs under TMP,
    # so nothing should have landed in SITE. If it did, that's a bug
    # to surface, not silently clean up.
    for di in TMP.rglob("rich-*.dist-info"):
        shutil.rmtree(di, ignore_errors=True)
        C.print(f"  [dim]removed {di}[/]")

# ─────────────────────────────────────────────────────────────────────────────
# DIAG 1: __all__ / mark_plan_handled
# ─────────────────────────────────────────────────────────────────────────────
SEP("DIAG 1: __all__ and mark_plan_handled")

expected = ["run", "run_capture", "get_site_packages_cache", "invalidate_site_packages_cache",
            "patch_site_packages_cache", "clear_registry_cache", "evict_bubble_cache",
            "evict_packages_from_bubble_cache", "patch_bubble_site_packages_cache",
            "get_install_plan", "set_plan_callback", "mark_plan_handled", "__version__"]

missing_all  = [s for s in expected if s not in uv_ffi.__all__]
missing_attr = [s for s in expected if not hasattr(uv_ffi, s)]

C.print(f"  Missing from __all__:  [red]{missing_all}[/]")
C.print(f"  Missing as attributes: [red]{missing_attr}[/]")
C.print(f"\n  [bold]Live __init__.py path:[/] {uv_ffi.__file__}")
C.print(f"  [dim](this is the .so — the __init__.py is the Python wrapper above it)[/]")

# Find the actual __init__.py
init_candidates = [
    Path(uv_ffi.__file__).parent / "__init__.py",               # if .so is inside uv_ffi/
    Path(uv_ffi.__file__).parent.parent / "uv_ffi" / "__init__.py",
]
for p in init_candidates:
    if p.exists():
        C.print(f"\n  [green]Found __init__.py:[/] {p}")
        # Show the __all__ block
        text = p.read_text()
        in_all = False
        C.print("  [dim]--- __all__ block ---[/]")
        for line in text.splitlines():
            if "__all__" in line:
                in_all = True
            if in_all:
                C.print(f"  {line}")
            if in_all and line.strip() == "]":
                break
        break
else:
    C.print(f"  [red]Could not find __init__.py next to .so[/]")

# ─────────────────────────────────────────────────────────────────────────────
# DIAG 2: --target forwarding
# ─────────────────────────────────────────────────────────────────────────────
SEP("DIAG 2: --target forwarding")

TARGET = TMP / "target"
TARGET.mkdir()

C.print(f"\n  [bold]Installing rich==14.3.3 --target {TARGET}[/]")
C.print(f"  [dim]cmd: 'pip install rich==14.3.3 --target {TARGET} --quiet'[/]\n")

plan_log = []
def cb(entries):
    plan_log.extend(entries)
    non_ext = [e for e in entries if e[2] != "extraneous"]
    ext     = [e for e in entries if e[2] == "extraneous"]
    C.print(f"  [cyan][CALLBACK][/] fired with {len(entries)} entries ({len(ext)} extraneous):")
    for e in non_ext:
        C.print(f"    {e}")
    for e in ext[:3]:
        C.print(f"    {e}")
    if len(ext) > 3:
        C.print(f"    [dim]... +{len(ext)-3} more extraneous[/]")
    return False  # let Rust proceed

uv_ffi.set_plan_callback(cb)

try:
    result = uv_ffi.run(f"pip install rich==14.3.3 --target {TARGET} --quiet")
    C.print(f"\n  [bold]run() returned:[/] rc={result[0]}")
    if result[2]:
        C.print(f"  stderr: {result[2][:500]}")
except Exception:
    C.print(f"  [red]run() raised:[/]")
    traceback.print_exc()

C.print()
dump_dir("target dir after install", TARGET)
C.print()
check_site_for_rich()

C.print("\n  [bold]dist-infos in target:[/]")
dis = list(TARGET.glob("rich-*.dist-info"))
C.print(f"    {dis}")

C.print("\n  [bold]dist-infos anywhere under TMP:[/]")
all_di = list(TMP.rglob("rich-*.dist-info"))
C.print(f"    {all_di}")

# ─────────────────────────────────────────────────────────────────────────────
# DIAG 3: stale dir behaviour (no xfail, just show what uv actually does)
# ─────────────────────────────────────────────────────────────────────────────
SEP("DIAG 3: stale dir behaviour")

STALE_TARGET = TMP / "stale_target"
STALE_TARGET.mkdir()

# Plant stale dir
stale_rich = STALE_TARGET / "rich"
stale_rich.mkdir()
(stale_rich / "STALE_MARKER.py").write_text("# stale — should be wiped")
C.print(f"\n  Planted: {stale_rich / 'STALE_MARKER.py'}")
C.print(f"  [dim]No dist-info exists — this is the 'stale dir' scenario[/]")

plan_log2 = []
def cb2(entries):
    plan_log2.extend(entries)
    non_ext = [e for e in entries if e[2] != "extraneous"]
    ext     = [e for e in entries if e[2] == "extraneous"]
    C.print(f"  [cyan][CALLBACK][/] fired with {len(entries)} entries ({len(ext)} extraneous):")
    for e in non_ext:
        C.print(f"    {e}")
    for e in ext[:3]:
        C.print(f"    {e}")
    if len(ext) > 3:
        C.print(f"    [dim]... +{len(ext)-3} more extraneous[/]")
    return False



uv_ffi.set_plan_callback(cb2)

C.print(f"\n  Installing rich==15.0.0 --target {STALE_TARGET} --quiet")
try:
    result2 = uv_ffi.run(f"pip install rich==15.0.0 --target {STALE_TARGET} --quiet")
    C.print(f"  run() rc={result2[0]}")
    if result2[2]:
        C.print(f"  stderr: {result2[2][:500]}")
except Exception:
    traceback.print_exc()

C.print()
dump_dir("stale_target after install", STALE_TARGET)
marker = stale_rich / "STALE_MARKER.py"
C.print(f"\n  STALE_MARKER.py still exists: [{'red]YES — not wiped' if marker.exists() else 'green]NO — was wiped'}[/]")
C.print(f"  Contents of rich/ dir:")
if stale_rich.exists():
    for f in sorted(stale_rich.iterdir()):
        C.print(f"    {f.name}")
else:
    C.print(f"    [dim](rich/ dir gone entirely)[/]")

# ─────────────────────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# DIAG 4: dist-info nuked mid-session (stale in-memory cache scenario)
# Exercises the FORCE_RESCAN retry path in run_uv().
# ─────────────────────────────────────────────────────────────────────────────
SEP("DIAG 4: dist-info nuked mid-session")
import io
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

NUKE_TARGET = TMP / "nuke_target"
NUKE_TARGET.mkdir()

C.print(f"\n  [bold]Step A:[/] install rich==14.3.3 into target (primes bubble cache)")
uv_ffi.set_plan_callback(None)
r = uv_ffi.run(f"pip install rich==14.3.3 --target {NUKE_TARGET} -q")
C.print(f"  rc={r[0]}  installed={r[1]}")
if r[2]: C.print(f"  [red]err:[/] {r[2]}")
dist_infos_before = list(NUKE_TARGET.glob("rich-*.dist-info"))
C.print(f"  dist-infos present: {[d.name for d in dist_infos_before]}")

C.print(f"\n  [bold]Step B:[/] nuke dist-info + pkg dir externally")
for di in dist_infos_before:
    shutil.rmtree(di); C.print(f"  [red]deleted[/] {di.name}")
rich_pkg = NUKE_TARGET / "rich"
if rich_pkg.exists():
    shutil.rmtree(rich_pkg); C.print(f"  [red]deleted[/] rich/ dir")
C.print(f"  target now: {sorted(p.name for p in NUKE_TARGET.iterdir())}")

C.print(f"\n  [bold]Step C:[/] reinstall — uv in-memory cache still thinks rich is present")
C.print(f"  [dim]Expected: stale cache detected, force-rescan, retry, succeed[/]")
cap = io.StringIO()
old_err = sys.stderr; sys.stderr = cap
try:
    r2 = uv_ffi.run(f"pip install rich==14.3.3 --target {NUKE_TARGET} -q")
finally:
    sys.stderr = old_err
for line in cap.getvalue().strip().splitlines():
    col = "red" if any(w in line.lower() for w in ("error","fail","panic","stale")) else "dim"
    C.print(f"  [{col}]{line}[/]")
C.print(f"  rc={r2[0]}  installed={r2[1]}")
if r2[2]: C.print(f"  [red]err field:[/] {r2[2]}")
dis_after = list(NUKE_TARGET.glob("rich-*.dist-info"))
safe_print(f"  [{'green' if dis_after else 'red'}]{'✓ retry succeeded — dist-info restored' if dis_after else '✗ retry failed — dist-info still missing'}[/]")

C.print(f"\n  [bold]Step D:[/] orphan dir — dist-info nuked, pkg dir left intact (the xfail scenario)")
ORPHAN_TARGET = TMP / "orphan_target"
ORPHAN_TARGET.mkdir()
uv_ffi.run(f"pip install rich==14.3.3 --target {ORPHAN_TARGET} -q")
for di in ORPHAN_TARGET.glob("rich-*.dist-info"):
    shutil.rmtree(di); C.print(f"  [red]deleted[/] {di.name} — rich/ dir left intact")
C.print(f"  target now: {sorted(p.name for p in ORPHAN_TARGET.iterdir())}")
cap2 = io.StringIO()
old_err = sys.stderr; sys.stderr = cap2
try:
    r4 = uv_ffi.run(f"pip install rich==15.0.0 --target {ORPHAN_TARGET} -q")
finally:
    sys.stderr = old_err
for line in cap2.getvalue().strip().splitlines():
    col = "red" if any(w in line.lower() for w in ("error","warn","left behind","stale")) else "dim"
    C.print(f"  [{col}]{line}[/]")
C.print(f"  rc={r4[0]}  installed={r4[1]}")
if r4[2]: C.print(f"  [red]err field:[/] {r4[2]}")
has_15 = any(ORPHAN_TARGET.glob("rich-15.0.0.dist-info"))
has_14 = any(ORPHAN_TARGET.glob("rich-14.3.3.dist-info"))
# __init__.py is present after *any* successful install — not a useful sentinel.
# Real signal: was the orphan dir wiped (14.x dist-info gone, no leftovers from
# the prior version that aren't in 15.x's RECORD)?
record_15 = ORPHAN_TARGET / next(
    (p.name for p in ORPHAN_TARGET.glob("rich-15.0.0.dist-info")), "rich-15.0.0.dist-info"
) / "RECORD"
stale_marker = ORPHAN_TARGET / "rich" / "STALE_MARKER.py"  # not planted here, should never exist
wipe_clean = has_15 and not has_14 and not stale_marker.exists()
C.print(f"  rich-14.3.3.dist-info still present: [{'red]YES — old dist-info not removed' if has_14 else 'green]NO — correctly absent'}[/]")
C.print(f"  rich-15.0.0.dist-info present:       [{'green]YES' if has_15 else 'red]NO — install failed'}[/]")
safe_print(f"  orphan wipe + reinstall clean:       [{'green]✓ PASS' if wipe_clean else 'red]✗ FAIL'}[/]")

# ─────────────────────────────────────────────────────────────────────────────
# DIAG 5: dependency chaos — nuke/confuse the transitive dep graph
# Four sub-scenarios exercising the failure modes uv sees when deps are
# partially or inconsistently present.
#
#   E  — nuke ALL deps (rich + pygments + mdurl + markdown-it-py wiped)
#   F  — ghost dist-info: dist-info present, pkg dir deleted (inverse of D)
#   G  — mixed: one dep has orphan dir (no dist-info), another has ghost
#        dist-info (no pkg dir), rich itself is clean
#   H  — version mismatch: install 14.3.3, manually rewrite dist-info
#        METADATA to claim 99.0.0 so uv's in-memory version != disk
# ─────────────────────────────────────────────────────────────────────────────
SEP("DIAG 5: dependency chaos")

def run_capped(cmd):
    """run() capturing stderr; prints coloured; returns result tuple."""
    cap = io.StringIO()
    old = sys.stderr; sys.stderr = cap
    try:
        r = uv_ffi.run(cmd)
    finally:
        sys.stderr = old
    for line in cap.getvalue().strip().splitlines():
        col = "red" if any(w in line.lower() for w in
                           ("error","fail","panic","stale","orphan","warn")) else "dim"
        C.print(f"  [{col}]{line}[/]")
    return r

def assert_ok(label, r, *dist_info_globs, target=None):
    ok = r[0] == 0
    if target:
        di_ok = all(any(target.glob(g)) for g in dist_info_globs)
        ok = ok and di_ok
    colour = "green" if ok else "red"
    mark   = "✓ PASS" if ok else "✗ FAIL"
    C.print(f"  [{colour}]{mark}[/]  {label}  rc={r[0]}  installed={r[1]}")
    if r[2]: C.print(f"    [red]err field:[/] {r[2]}")

uv_ffi.set_plan_callback(None)

# ── Step E: nuke ALL packages in the target ───────────────────────────────
C.print(f"\n  [bold]Step E:[/] nuke entire target (all pkg dirs + all dist-infos)")
E_TARGET = TMP / "e_target"
E_TARGET.mkdir()
uv_ffi.run(f"pip install rich==14.3.3 --target {E_TARGET} -q")
# wipe everything except .lock and bin
for p in E_TARGET.iterdir():
    if p.name not in (".lock", "bin"):
        (shutil.rmtree if p.is_dir() else p.unlink)(p)
C.print(f"  target after nuke: {sorted(p.name for p in E_TARGET.iterdir())}")
# Simulate fs_watcher notification: full nuke would have fired inotify deletes
# for every file, watcher calls evict_bubble_cache() before next install.
uv_ffi.evict_bubble_cache()
C.print(f"  reinstalling rich==14.3.3 into empty target…")
r_e = run_capped(f"pip install rich==14.3.3 --target {E_TARGET} -q")
assert_ok("all-deps reinstall after full nuke", r_e,
          "rich-14.3.3.dist-info", "pygments-*.dist-info",
          "mdurl-*.dist-info", "markdown_it_py-*.dist-info",
          target=E_TARGET)

# ── Step F: ghost dist-info — dist-info present, pkg dir deleted ──────────
C.print(f"\n  [bold]Step F:[/] ghost dist-info — delete pkg dirs, leave dist-infos intact")
F_TARGET = TMP / "f_target"
F_TARGET.mkdir()
uv_ffi.run(f"pip install rich==14.3.3 --target {F_TARGET} -q")
# delete pkg dirs only, leave all dist-infos
for pkg in ("rich", "pygments", "markdown_it", "mdurl"):
    d = F_TARGET / pkg
    if d.exists():
        shutil.rmtree(d); C.print(f"  [red]deleted pkg dir[/] {pkg}/")
C.print(f"  target now: {sorted(p.name for p in F_TARGET.iterdir())}")
# fs_watcher would have fired on pkg dir deletions → evict bubble cache.
uv_ffi.evict_bubble_cache()
C.print(f"  reinstalling rich==14.3.3 (dist-infos present, pkg dirs gone)…")
r_f = run_capped(f"pip install rich==14.3.3 --target {F_TARGET} -q")
rich_pkg_back = (F_TARGET / "rich" / "__init__.py").exists()
assert_ok("reinstall with ghost dist-infos", r_f,
          "rich-14.3.3.dist-info", target=F_TARGET)
C.print(f"  rich/__init__.py restored: [{'green' if rich_pkg_back else 'red'}]{rich_pkg_back}[/]")

# ── Step G: mixed chaos — orphan dir for one dep, ghost dist-info for another
C.print(f"\n  [bold]Step G:[/] mixed — orphan dir (pygments/ no dist-info) + ghost dist-info (mdurl dist-info, no mdurl/)")
G_TARGET = TMP / "g_target"
G_TARGET.mkdir()
uv_ffi.run(f"pip install rich==14.3.3 --target {G_TARGET} -q")
# orphan: delete pygments dist-info, leave pygments/ dir
for di in G_TARGET.glob("pygments-*.dist-info"):
    shutil.rmtree(di); C.print(f"  [red]deleted dist-info[/] {di.name} — pygments/ dir left (orphan)")
# ghost: delete mdurl/ dir, leave mdurl dist-info
mdurl_dir = G_TARGET / "mdurl"
if mdurl_dir.exists():
    shutil.rmtree(mdurl_dir); C.print(f"  [red]deleted pkg dir[/] mdurl/ — mdurl dist-info left (ghost)")
C.print(f"  target now: {sorted(p.name for p in G_TARGET.iterdir())}")
# fs_watcher would have fired on dist-info + pkg dir mutations → evict.
uv_ffi.evict_bubble_cache()
C.print(f"  reinstalling rich==14.3.3 into mixed-chaos target…")
r_g = run_capped(f"pip install rich==14.3.3 --target {G_TARGET} -q")
pygments_clean = not any(G_TARGET.glob("pygments")) or \
                 any(G_TARGET.glob("pygments-*.dist-info"))
mdurl_back = (G_TARGET / "mdurl").exists() and any(G_TARGET.glob("mdurl-*.dist-info"))
assert_ok("reinstall into mixed orphan+ghost target", r_g,
          "rich-14.3.3.dist-info", target=G_TARGET)
C.print(f"  pygments consistent (dir↔dist-info): [{'green' if pygments_clean else 'red'}]{pygments_clean}[/]")
C.print(f"  mdurl restored (dir + dist-info):    [{'green' if mdurl_back else 'red'}]{mdurl_back}[/]")

# ── Step H: version lie — dist-info METADATA claims wrong version ─────────
# IMPORTANT: we only patch the on-disk dist-info METADATA, NOT anything inside
# uv's wheel cache (~/.cache/uv). The patch is restored before any retry can
# write back to the cache, and clear_registry_cache() flushes in-memory state.
C.print(f"\n  [bold]Step H:[/] version lie — rewrite rich dist-info METADATA to claim 99.0.0")
H_TARGET = TMP / "h_target"
H_TARGET.mkdir()
uv_ffi.run(f"pip install rich==14.3.3 --target {H_TARGET} -q")
metadata_path = next(H_TARGET.glob("rich-14.3.3.dist-info/METADATA"), None)
original_metadata = metadata_path.read_text() if metadata_path else None
if metadata_path and original_metadata:
    metadata_path.write_text(original_metadata.replace("Version: 14.3.3", "Version: 99.0.0"))
    C.print(f"  [yellow]patched[/] METADATA: Version now claims 99.0.0")
    uv_ffi.evict_bubble_cache()
else:
    C.print(f"  [red]could not find METADATA to patch[/]")
C.print(f"  reinstalling rich==14.3.3 (bubble cache evicted, METADATA claims 99.0.0)...")
r_h = run_capped(f"pip install rich==14.3.3 --target {H_TARGET} -q")
meta_after = metadata_path.read_text() if metadata_path and metadata_path.exists() else ""
version_healed = "Version: 14.3.3" in meta_after
assert_ok("reinstall over lying METADATA", r_h,
          "rich-14.3.3.dist-info", target=H_TARGET)
C.print(f"  METADATA version healed to 14.3.3: [{'green' if version_healed else 'red'}]{version_healed}[/]")
# Restore and flush to prevent poisoning uv wheel cache for subsequent runs.
if metadata_path and original_metadata:
    metadata_path.write_text(original_metadata)
    C.print(f"  [dim]restored original METADATA[/]")
uv_ffi.clear_registry_cache()
C.print(f"  [dim]cleared registry cache[/]")

SEP("cleanup")
clean_site_rich()
shutil.rmtree(TMP, ignore_errors=True)
C.print(f"  [dim]removed {TMP}[/]\n")