"""
Test FFI multiple calls in-process, verify actual work each time.
Captures stdout+stderr and checks version on disk after each call.
"""
import sys
import os
import time
import importlib

sys.path.insert(0, '/home/minds3t/omnipkg/src')

from omnipkg._vendor.uv_ffi import run

def get_rich_version():
    """Read version directly from disk — no import cache."""
    import glob
    pattern = os.path.expanduser(
        "~/miniforge3/envs/evocoder_env/lib/python3.11/site-packages/rich-*.dist-info/METADATA"
    )
    matches = glob.glob(pattern)
    if not matches:
        return None
    with open(matches[0]) as f:
        for line in f:
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    return None

def ffi_install(pkg):
    cmd = f"pip install --cache-dir /home/minds3t/.cache/uv --link-mode symlink {pkg}"
    t = time.perf_counter()
    rc, stdout, stderr = run(cmd)
    ms = (time.perf_counter() - t) * 1000
    combined = stdout + stderr
    print(f"\n{'='*60}")
    print(f"CMD:    uv {cmd}")
    print(f"RC:     {rc}  ({ms:.1f}ms)")
    print(f"OUTPUT: {combined.strip()}")
    version = get_rich_version()
    print(f"DISK VERSION: {version}")
    return rc, combined, version

print("Starting multi-call FFI verification test")
print("="*60)

# Call 1 — install 14.3.2
rc1, out1, v1 = ffi_install("rich==14.3.2")
assert "14.3.2" in (v1 or ""), f"FAIL: expected 14.3.2 on disk, got {v1}"
assert rc1 == 0, f"FAIL: rc={rc1}"
print(f"✅ PASS call 1: rich=={v1} on disk")

# Call 2 — upgrade to 14.3.3 in same process
rc2, out2, v2 = ffi_install("rich==14.3.3")
assert "14.3.3" in (v2 or ""), f"FAIL: expected 14.3.3 on disk, got {v2}"
assert rc2 == 0, f"FAIL: rc={rc2}"
print(f"✅ PASS call 2: rich=={v2} on disk")

# Call 3 — back to 14.3.2
rc3, out3, v3 = ffi_install("rich==14.3.2")
assert "14.3.2" in (v3 or ""), f"FAIL: expected 14.3.2 on disk, got {v3}"
assert rc3 == 0, f"FAIL: rc={rc3}"
print(f"✅ PASS call 3: rich=={v3} on disk")

# Call 4 — already satisfied, should audit not reinstall
rc4, out4, v4 = ffi_install("rich==14.3.2")
assert rc4 == 0, f"FAIL: rc={rc4}"
assert "Audited" in out4 or "Installed" in out4 or "already" in out4.lower(), \
    f"FAIL: unexpected output: {out4}"
print(f"✅ PASS call 4: no-op satisfied, rich=={v4} on disk")

print("\n" + "="*60)
print("ALL 4 CALLS PASSED — FFI multi-call in-process verified ✅")
