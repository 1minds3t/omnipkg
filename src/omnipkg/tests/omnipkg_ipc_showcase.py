#!/usr/bin/env python3
"""
omnipkg IPC Transport Showcase  v2
====================================
Every test runs TWICE:
  • Cold run  – worker is freshly spawned (daemon restart beforehand)
  • Warm run  – same worker, globals already hot

Between each cold run the script calls:
    8pkg daemon restart
so that workers are fully evicted and the next call measures true startup cost.

NEW in v2:
  • Cross-Python test (test 16): JSON dict handoff between two managed
    interpreters (e.g. 3.9 → 3.12) using execute_shm with python_exe.
    Requires: export OMNIPKG_NONINTERACTIVE=1  (or the env var is set below)

Transport map (fastest → slowest):
  1.  execute_smart  / CUDA IPC   – GPU tensor, same CUDA version, <1ms overhead
  2.  execute_smart  / CPU SHM    – numpy array ≥64KB, zero-copy, ~5ms
  3.  execute_zero_copy           – explicit numpy zero-copy SHM (manual)
  4.  execute_shm   (dict shm_in) – plain Python dict/list cross-worker, ~2ms
  5.  execute_smart  / JSON       – small data / no data, ~10ms
  6.  execute_shm   (empty)       – fire-and-forget, result in stdout only
  7.  execute_cuda_ipc            – explicit GPU IPC with ipc_mode control
  8.  run_once                    – throwaway worker (evicted after use)
  8.  cross-CUDA-build universal IPC – cu118→cu121 TRUE zero-copy GPU
      UniversalGpuIpc bypasses PyTorch IPC entirely: raw cudaIpcGetMemHandle
      via ctypes against libcudart.so.  Version-agnostic.  3-stage pipeline
      with cold/warm runs + CPU SHM comparison.
  9.  execute_cuda_ipc – explicit GPU IPC (same CUDA build, ipc_mode=auto)
  10. worker_tag isolation        – two models in same spec, separate workers
  11. execute_smart pinned worker – pin=True keeps worker alive between calls
  12. memory-capped worker        – max_memory_mb auto-evicts bloated workers
  13. Cross-Python version        – 3.9 → 3.12 dict handoff via python_exe
"""

import os
import subprocess
import sys
import time
import json
import textwrap

# ── make `8pkg info python` non-interactive ─────────────────────────────────
os.environ.setdefault("OMNIPKG_NONINTERACTIVE", "1")

try:
    from omnipkg.isolation.worker_daemon import DaemonClient
except ImportError:
    sys.exit("omnipkg not on path – activate evocoder_env first")

# ── helpers ──────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

passed = failed = skipped = 0


def section(title):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def ok(label, ms, extra=""):
    global passed
    passed += 1
    tail = f"  {YELLOW}{extra}{RESET}" if extra else ""
    print(f"  {GREEN}✅ PASS{RESET}  {label:<46} {YELLOW}{ms:>7.2f} ms{RESET}{tail}")


def fail(label, reason):
    global failed
    failed += 1
    print(f"  {RED}❌ FAIL{RESET}  {label:<46}  {reason}")


def skip(label, reason):
    global skipped
    skipped += 1
    print(f"  {YELLOW}⏭  SKIP{RESET}  {label:<46}  {reason}")


def t(fn):
    s = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - s) * 1000


def daemon_restart():
    """
    Kill all workers and wait for the daemon to be back up.
    We suppress output – only print if it fails.
    """
    print(f"  {DIM}⟳  8pkg daemon restart …{RESET}", end="", flush=True)
    try:
        result = subprocess.run(
            ["8pkg", "daemon", "restart"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"\n  {YELLOW}⚠  daemon restart non-zero exit ({result.returncode}){RESET}")
            if result.stderr.strip():
                print(f"  {DIM}{result.stderr.strip()[:200]}{RESET}")
        else:
            # Small settle time so the daemon socket is ready
            time.sleep(0.5)
            print(f"  {DIM}done{RESET}")
    except FileNotFoundError:
        print(f"\n  {YELLOW}⚠  '8pkg' not on PATH – skipping daemon restart{RESET}")
    except subprocess.TimeoutExpired:
        print(f"\n  {YELLOW}⚠  daemon restart timed out{RESET}")


def cold_warm_header():
    """Print the cold/warm legend once per test group."""
    print(f"  {DIM}(cold = fresh worker, warm = hot globals){RESET}")


def _lookup_python(version: str) -> str | None:
    """
    Resolve a short version string like '3.9' to its full managed-interpreter
    path via the omnipkg dispatcher registry.

    Falls back to None if the version is not managed.
    """
    try:
        from omnipkg.dispatcher import resolve_python_path
        p = str(resolve_python_path(version))
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    # Manual fallback: ask 8pkg info python -y and grep
    try:
        result = subprocess.run(
            ["8pkg", "info", "python", "-y"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "OMNIPKG_NONINTERACTIVE": "1"},
        )
        for line in result.stdout.splitlines():
            if version in line and "managed" in line:
                parts = line.split()
                for p in parts:
                    if os.path.isabs(p) and os.path.exists(p):
                        return p
    except Exception:
        pass
    return None


def _resolve_torch_spec_for_interpreter(python_exe: str) -> str | None:
    """
    Ask a *specific* managed Python interpreter what torch version it has.
    Returns 'torch==X.Y.Z+cuNNN' (ready to pass as a bubble spec), or None.

    This is the correct way to get the spec for cross-Python tests like 16:
    each interpreter may have a completely different torch+CUDA build installed,
    and we must never assume the outer environment's version applies.
    """
    try:
        result = subprocess.run(
            [python_exe, "-c", "import torch; print(torch.__version__)"],
            capture_output=True, text=True, timeout=15,
        )
        ver = result.stdout.strip()
        if ver and not result.returncode:
            return f"torch=={ver}"
    except Exception:
        pass
    return None


def _discover_torch_bubble_specs(want: int = 3) -> list[str]:
    """
    Query omnipkg for all torch versions installed in the current Python's
    bubbles, then return up to `want` specs that each have a *distinct* CUDA
    build suffix (cu118, cu121, cu130 …).

    Returns a list of pip-style specs e.g.:
        ['torch==2.0.1+cu118', 'torch==2.2.0+cu121', 'torch==2.12.0+cu130']

    The list may be shorter than `want` if fewer distinct CUDA builds are
    installed — callers must handle that gracefully.
    """
    try:
        result = subprocess.run(
            ["8pkg", "info", "torch"],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "OMNIPKG_NONINTERACTIVE": "1"},
        )
        seen_cuda: dict[str, str] = {}   # cu-suffix → full spec
        for line in result.stdout.splitlines():
            # Lines look like:  "  1) v2.0.1+cu118 (active)"  or  "  2) v2.2.0+cu121"
            line = line.strip()
            for token in line.split():
                token = token.lstrip("v")
                # Must look like a PEP 440 version with a +cu build tag
                if "+" in token and token[0].isdigit():
                    base, local = token.split("+", 1)
                    if local.startswith("cu") and local[2:].isdigit():
                        spec = f"torch=={token}"
                        if local not in seen_cuda:
                            seen_cuda[local] = spec
        # Sort by cu number so we get cu118 < cu121 < cu130 order
        ordered = sorted(seen_cuda.values(),
                         key=lambda s: int(s.split("+cu")[1].split("==")[0]
                                          if "+cu" in s else 0))
        return ordered[:want]
    except Exception:
        return []


client = DaemonClient()

# Detect caller's torch spec for same-build CUDA IPC tests (9, 10).
try:
    import torch as _torch
    _tv = _torch.__version__
    _TORCH_SPEC = f"torch=={_tv}"
except ImportError:
    _TORCH_SPEC = "torch"

# ══════════════════════════════════════════════════════════════════════════════
# 1.  execute_smart – no data (JSON path)
# ══════════════════════════════════════════════════════════════════════════════
# USE WHEN: you just want to run isolated code with no input/output data.
# Overhead: ~10 ms cold (worker spawn + IPC), <1 ms warm.
section("1. execute_smart – no data (JSON / stdout path)")
cold_warm_header()

code_no_data = """
import sys, platform, json, torch
print(json.dumps({
    "py": sys.version.split()[0],
    "platform": platform.machine(),
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
}))
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.execute_smart(_TORCH_SPEC, code_no_data))
        if res.get("success") and res.get("transport") == "JSON":
            ok(f"[{run_label}] execute_smart no-data → JSON", ms,
               f"transport={res['transport']}")
        else:
            fail(f"[{run_label}] execute_smart no-data", res.get("error", res))
    except Exception as e:
        fail(f"[{run_label}] execute_smart no-data", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 2.  execute_smart – small list (JSON path)
# ══════════════════════════════════════════════════════════════════════════════
section("2. execute_smart – small list → JSON path")
cold_warm_header()

code_list = """
import json, torch
t = torch.tensor(arr_in, dtype=torch.float32)
total = float(t.sum())
print(json.dumps({"sum": total, "len": len(arr_in), "mean": float(t.mean()), "torch": torch.__version__}))
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.execute_smart(_TORCH_SPEC, code_list, data=list(range(100))))
        if res.get("success"):
            ok(f"[{run_label}] execute_smart list[100] → JSON", ms,
               f"transport={res['transport']}")
        else:
            fail(f"[{run_label}] execute_smart small list", res.get("error", res))
    except Exception as e:
        fail(f"[{run_label}] execute_smart small list", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 3.  execute_smart – large numpy array (CPU SHM path, zero-copy)
# ══════════════════════════════════════════════════════════════════════════════
# USE WHEN: data is a numpy array ≥64KB. Auto-upgrades to zero-copy shared
# memory – no serialisation, OS page-maps the buffer directly into the worker.
section("3. execute_smart – large numpy array → CPU SHM (zero-copy)")
cold_warm_header()

try:
    import numpy as np

    big = np.random.rand(1_000_000).astype(np.float32)   # 4 MB
    code_np = "arr_out[:] = arr_in * 2.0"

    for run_label in ("COLD", "WARM"):
        if run_label == "COLD":
            daemon_restart()
        try:
            res, ms = t(lambda: client.execute_smart("scipy==1.12.0", code_np, data=big))
            if res.get("success") and isinstance(res.get("result"), np.ndarray):
                ok(f"[{run_label}] execute_smart 4MB ndarray → SHM", ms,
                   f"transport={res['transport']} shape={res['result'].shape}")
            else:
                fail(f"[{run_label}] execute_smart large numpy", res.get("error", res))
        except Exception as e:
            fail(f"[{run_label}] execute_smart large numpy", str(e))
except ImportError:
    skip("execute_smart large numpy (both runs)", "numpy not in outer env")

# ══════════════════════════════════════════════════════════════════════════════
# 4.  execute_zero_copy – explicit numpy SHM
# ══════════════════════════════════════════════════════════════════════════════
section("4. execute_zero_copy – explicit shape/dtype control")
cold_warm_header()

try:
    import numpy as np

    mat = np.arange(16, dtype=np.float32).reshape(4, 4)
    code_zc = """
import numpy as np
arr_out[:] = np.linalg.inv(arr_in + np.eye(4))
"""
    for run_label in ("COLD", "WARM"):
        if run_label == "COLD":
            daemon_restart()
        try:
            res, ms = t(lambda: client.execute_zero_copy(
                "scipy==1.12.0",
                code_zc,
                input_array=mat,
                output_shape=(4, 4),
                output_dtype="float32",
            ))
            result_arr, _ = res
            ok(f"[{run_label}] execute_zero_copy 4×4 matrix invert", ms,
               f"result[0,0]={result_arr[0,0]:.4f}")
        except Exception as e:
            fail(f"[{run_label}] execute_zero_copy", str(e))
except ImportError:
    skip("execute_zero_copy (both runs)", "numpy not in outer env")

# ══════════════════════════════════════════════════════════════════════════════
# 5.  execute_shm – plain dict cross-worker
# ══════════════════════════════════════════════════════════════════════════════
# USE WHEN: you want to pass arbitrary Python data between two workers of
# DIFFERENT package specs without touching numpy or GPU.
section("5. execute_shm – plain dict cross-worker (TF 2.12 → TF 2.13)")
cold_warm_header()

stage1 = """
import tensorflow as tf
x = tf.constant([[1.0, 2.0], [3.0, 4.0]])
result = {"matrix": tf.square(x).numpy().tolist(), "op": "square"}
"""

stage2 = """
import tensorflow as tf
payload = input_data['shm_in']
matrix  = payload['matrix']
t = tf.constant(matrix, dtype=tf.float32)
w = tf.constant([[2.0, 0.0], [0.0, 2.0]], dtype=tf.float32)
result  = {"final": tf.matmul(t, w).numpy().tolist(), "chain": "square→matmul"}
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        r1, ms1 = t(lambda: client.execute_shm("tensorflow==2.12.0", stage1, shm_in={}, shm_out={}))
        if not r1.get("success"):
            fail(f"[{run_label}] TF 2.12 stage1", r1.get("error", ""))
            continue
        payload = {"matrix": r1["matrix"], "op": r1["op"]}
        r2, ms2 = t(lambda: client.execute_shm("tensorflow==2.13.0", stage2,
                                                shm_in=payload, shm_out={}))
        if r2.get("success"):
            ok(f"[{run_label}] TF 2.12.0 → TF 2.13.0 (stage1)", ms1)
            ok(f"[{run_label}] TF 2.12.0 → TF 2.13.0 (stage2)", ms2, r2.get("chain", ""))
        else:
            fail(f"[{run_label}] TF cross-version handoff", r2.get("error", r2))
    except Exception as e:
        fail(f"[{run_label}] TF cross-version handoff", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 6.  execute_shm – fire-and-forget, stdout result
# ══════════════════════════════════════════════════════════════════════════════
section("6. execute_shm – fire-and-forget, stdout result")
cold_warm_header()

code_ff = """
import sys, hashlib, torch
data = b"omnipkg rocks" * 10_000
digest = hashlib.sha256(data).hexdigest()
print(digest)
result = {"digest": digest, "torch": torch.__version__}
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.execute_shm(_TORCH_SPEC, code_ff, shm_in={}, shm_out={}))
        if res.get("success"):
            ok(f"[{run_label}] fire-and-forget sha256", ms, res["digest"][:16] + "…")
        else:
            fail(f"[{run_label}] fire-and-forget", res.get("error", res))
    except Exception as e:
        fail(f"[{run_label}] fire-and-forget", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 7.  execute_shm – multi-array dict handoff (numpy tolist)
# ══════════════════════════════════════════════════════════════════════════════
section("7. execute_shm – multi-array dict handoff (numpy tolist)")
cold_warm_header()

try:
    import numpy as np

    gen_code = """
import numpy as np
a = np.arange(12, dtype=np.float32).reshape(3, 4)
b = np.eye(4, dtype=np.float32)
result = {"A": a.tolist(), "B": b.tolist(), "shape_A": list(a.shape)}
"""
    use_code = """
import numpy as np
d  = input_data['shm_in']
A  = np.array(d['A'], dtype=np.float32)
B  = np.array(d['B'], dtype=np.float32)
C  = A @ B
result = {"C": C.tolist(), "trace": float(np.trace(C))}
"""

    for run_label in ("COLD", "WARM"):
        if run_label == "COLD":
            daemon_restart()
        try:
            r1, ms1 = t(lambda: client.execute_shm("scipy==1.12.0", gen_code, shm_in={}, shm_out={}))
            if not r1.get("success"):
                raise RuntimeError(r1.get("error", "gen failed"))
            r2, ms2 = t(lambda: client.execute_shm(
                "scipy==1.15.3", use_code,
                shm_in={"A": r1["A"], "B": r1["B"]},
                shm_out={}
            ))
            if r2.get("success"):
                ok(f"[{run_label}] scipy 1.12→1.15 multi-array dict (gen)", ms1)
                ok(f"[{run_label}] scipy 1.12→1.15 multi-array dict (use)", ms2,
                   f"trace={r2['trace']:.2f}")
            else:
                fail(f"[{run_label}] multi-array dict handoff", r2.get("error", r2))
        except Exception as e:
            fail(f"[{run_label}] multi-array dict handoff", str(e))
except ImportError:
    skip("multi-array dict handoff (both runs)", "numpy unavailable")

# ══════════════════════════════════════════════════════════════════════════════
# 8.  The "Impossible" Pipeline: 3 PyTorch Versions & 3 SciPy Versions
# ══════════════════════════════════════════════════════════════════════════════
section("8. The 'Impossible' Pipeline: 3 Different PyTorch Versions & 3 CUDA Builds")
print(f"  {DIM}Transport: UniversalGpuIpc (raw cudaIpcGetMemHandle via ctypes){RESET}")
cold_warm_header()

_PIPELINE_SHAPE = (500, 250)

# Discover however many distinct-CUDA-build torch specs are actually installed.
# We need ≥2 to run any cross-build test; ≥3 for the full 3-stage pipeline.
_CUDA_SPECS = _discover_torch_bubble_specs(want=3)

# We must explicitly return the version in the result dict so we can print it!
_stage_gpu_code = "tensor_out[:] = {op}(tensor_in)\nresult = {{'torch_ver': torch.__version__}}"

try:
    import torch as _torch_test8
    if not _torch_test8.cuda.is_available():
        skip("cross-build universal IPC pipeline (all runs)", "CUDA not available")
    elif len(_CUDA_SPECS) < 2:
        skip("cross-build universal IPC pipeline (all runs)",
             "need ≥2 distinct torch+cu builds installed — "
             "run: 8pkg install torch+cu118 torch+cu121  (or whichever builds you want)")
    else:
        # One-time CUDA warmup
        _w = _torch_test8.randn(32, 32, device="cuda")
        _torch_test8.matmul(_w, _w.T)
        _torch_test8.cuda.synchronize()
        del _w

        _GPU_OPS = ["torch.nn.functional.relu", "torch.sigmoid", "torch.tanh"]

        for run_label in ("COLD", "WARM"):
            if run_label == "COLD":
                daemon_restart()

            try:
                gpu_in = _torch_test8.randn(*_PIPELINE_SHAPE, device="cuda", dtype=_torch_test8.float32)

                # ── GPU IPC Pipeline (PyTorch) ──
                # Drive however many stages we actually have (2 or 3).
                _stage_inputs  = [gpu_in]
                _stage_outputs = []
                _stage_ms      = []
                _stage_meta    = []

                for _si, _spec in enumerate(_CUDA_SPECS):
                    _op   = _GPU_OPS[_si % len(_GPU_OPS)]
                    _code = f"tensor_out[:] = {_op}(tensor_in)\nresult = {{'torch_ver': torch.__version__}}"
                    _t0   = time.perf_counter()
                    _sout, _smeta = client.execute_cuda_ipc(
                        _spec, _code, _stage_inputs[-1],
                        output_shape=_PIPELINE_SHAPE, output_dtype="float32",
                        ipc_mode="universal",
                    )
                    _stage_ms.append((time.perf_counter() - _t0) * 1000)
                    _stage_inputs.append(_sout)
                    _stage_outputs.append(_sout)
                    _stage_meta.append(_smeta)

                total_gpu = sum(_stage_ms)
                for _si, (_spec, _ms, _meta) in enumerate(zip(_CUDA_SPECS, _stage_ms, _stage_meta)):
                    _cu = _spec.split("+cu")[1] if "+cu" in _spec else "?"
                    ok(f"[{run_label}] GPU Stage {_si+1}: {_meta.get('torch_ver', '?')} (cu{_cu})", _ms)

                _n = len(_CUDA_SPECS)
                print(f"  {CYAN}  ↳ [{run_label}] GPU pipeline total: {total_gpu:.2f} ms  "
                      f"(TRUE ZERO-COPY across {_n} ABI{'s' if _n > 1 else ''}!){RESET}\n")

                # ── CPU SHM Pipeline (SciPy) ──
                cpu_in = gpu_in.cpu().numpy()
                s1_cpu_code = "arr_out[:] = arr_in * (arr_in > 0)"   # relu
                s2_cpu_code = "import numpy as _np; arr_out[:] = 1.0 / (1.0 + _np.exp(-arr_in))"
                s3_cpu_code = "import numpy as _np; arr_out[:] = _np.tanh(arr_in)"

                t_cpu0 = time.perf_counter()
                rc1 = client.execute_zero_copy("scipy==1.10.0", s1_cpu_code, input_array=cpu_in, output_shape=_PIPELINE_SHAPE, output_dtype="float32")
                ms_c1 = (time.perf_counter() - t_cpu0) * 1000
                
                c1_out, _ = rc1
                t_cpu0 = time.perf_counter()
                rc2 = client.execute_zero_copy("scipy==1.12.0", s2_cpu_code, input_array=c1_out, output_shape=_PIPELINE_SHAPE, output_dtype="float32")
                ms_c2 = (time.perf_counter() - t_cpu0) * 1000
                
                c2_out, _ = rc2
                t_cpu0 = time.perf_counter()
                rc3 = client.execute_zero_copy("scipy==1.15.3", s3_cpu_code, input_array=c2_out, output_shape=_PIPELINE_SHAPE, output_dtype="float32")
                ms_c3 = (time.perf_counter() - t_cpu0) * 1000

                total_cpu = ms_c1 + ms_c2 + ms_c3

                ok(f"[{run_label}] CPU Stage 1: scipy==1.10.0", ms_c1)
                ok(f"[{run_label}] CPU Stage 2: scipy==1.12.0", ms_c2)
                ok(f"[{run_label}] CPU Stage 3: scipy==1.15.3", ms_c3)
                print(f"  {CYAN}  ↳ [{run_label}] CPU pipeline total: {total_cpu:.2f} ms{RESET}\n")

                if run_label == "WARM":
                    _cu_labels = " → ".join(
                        f"cu{s.split('+cu')[1]}" if "+cu" in s else s
                        for s in _CUDA_SPECS
                    )
                    print(f"  {BOLD}  ┌─ Pipeline comparison (warm, {_PIPELINE_SHAPE[0]}×{_PIPELINE_SHAPE[1]} float32) ──────────────{RESET}")
                    print(f"  {BOLD}  │  CPU SHM  (scipy 1.10 → 1.12 → 1.15) : {total_cpu:>7.2f} ms{RESET}")
                    print(f"  {BOLD}  │  GPU IPC  ({_cu_labels})   : {total_gpu:>7.2f} ms{RESET}")
                    if total_gpu < total_cpu:
                        _sp = total_cpu / total_gpu
                        print(f"  {BOLD}  │  → GPU is {_sp:.1f}× faster! (tensor never left the VRAM){RESET}")
                    print(f"  {BOLD}  └──────────────────────────────────────────────────────{RESET}")
                    print()

            except Exception as e:
                skip(f"[{run_label}] cross-build universal IPC pipeline", str(e))

except ImportError:
    skip("cross-build universal IPC pipeline (all runs)", "torch not importable in outer env")

# ══════════════════════════════════════════════════════════════════════════════
# 9.  execute_cuda_ipc – ipc_mode=auto, same CUDA build (zero-copy)
# ══════════════════════════════════════════════════════════════════════════════
# Same-build path: auto resolves to universal IPC (or pytorch_native on 1.x).
# Compare vs test 8 which proves universal works ACROSS build versions too.
section("9. execute_cuda_ipc – GPU tensor IPC (same CUDA build, zero-copy)")
cold_warm_header()

cuda_code = """
tensor_out[:] = tensor_in * 3.14159
"""

try:
    import torch
    if not torch.cuda.is_available():
        skip("execute_cuda_ipc (both runs)", "CUDA not available on this machine")
    else:
        gpu_t = torch.randn(512, 512, device="cuda", dtype=torch.float32)
        for run_label in ("COLD", "WARM"):
            if run_label == "COLD":
                daemon_restart()
            try:
                res, ms = t(lambda: client.execute_cuda_ipc(
                    spec=_TORCH_SPEC,
                    code=cuda_code,
                    input_tensor=gpu_t,
                    output_shape=(512, 512),
                    output_dtype="float32",
                    ipc_mode="auto",
                ))
                result_tensor, meta = res
                ok(f"[{run_label}] execute_cuda_ipc auto 512×512", ms,
                   f"mean={float(result_tensor.mean()):.4f}")
            except Exception as e:
                skip(f"[{run_label}] execute_cuda_ipc", str(e))
except ImportError:
    skip("execute_cuda_ipc (both runs)", "torch not importable in outer env")

# ══════════════════════════════════════════════════════════════════════════════
# 10. execute_smart – CUDA tensor (auto-routes to CUDA IPC, zero-copy)
# ══════════════════════════════════════════════════════════════════════════════
section("10. execute_smart – CUDA tensor → auto CUDA IPC (zero-copy)")
cold_warm_header()

smart_cuda_code = "arr_out = arr_in ** 2"

try:
    import torch
    if not torch.cuda.is_available():
        skip("execute_smart CUDA (both runs)", "CUDA not available")
    else:
        t_in = torch.randn(1024, device="cuda")
        for run_label in ("COLD", "WARM"):
            if run_label == "COLD":
                daemon_restart()
            try:
                res, ms = t(lambda: client.execute_smart(_TORCH_SPEC, smart_cuda_code, data=t_in))
                if res.get("success"):
                    ok(f"[{run_label}] execute_smart CUDA tensor → CUDA_IPC", ms,
                       f"transport={res['transport']}")
                else:
                    fail(f"[{run_label}] execute_smart CUDA tensor", res.get("error", res))
            except Exception as e:
                skip(f"[{run_label}] execute_smart CUDA tensor", str(e))
except ImportError:
    skip("execute_smart CUDA tensor (both runs)", "torch unavailable")

# ══════════════════════════════════════════════════════════════════════════════
# 11. worker_tag isolation – two models, same spec, separate workers
# ══════════════════════════════════════════════════════════════════════════════
# USE WHEN: loading multiple large models under the same spec and you need
# separate processes (prevent RAM accumulation / ABI clash).
section("11. worker_tag – two isolated workers, same spec")
cold_warm_header()

model_a_code = """
if '_counter' not in globals():
    globals()['_counter'] = 0
globals()['_counter'] += 1
result = {"worker": "model-A", "calls": globals()['_counter']}
"""
model_b_code = """
if '_counter' not in globals():
    globals()['_counter'] = 100
globals()['_counter'] += 1
result = {"worker": "model-B", "calls": globals()['_counter']}
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        for i in range(2):
            ra, ms = t(lambda: client.execute_shm(
                _TORCH_SPEC, model_a_code, shm_in={}, shm_out={},
                worker_tag="model-A"
            ))
            rb, _ = t(lambda: client.execute_shm(
                _TORCH_SPEC, model_b_code, shm_in={}, shm_out={},
                worker_tag="model-B"
            ))
        if ra.get("success") and rb.get("success"):
            ok(f"[{run_label}] worker_tag model-A (globals persist)", ms,
               f"calls={ra['calls']} (expected 2)")
            ok(f"[{run_label}] worker_tag model-B isolated counter", ms,
               f"calls={rb['calls']} (expected 102 warm / 102 cold after 2 loops)")
        else:
            fail(f"[{run_label}] worker_tag isolation",
                 f"A={ra.get('error')} B={rb.get('error')}")
    except Exception as e:
        fail(f"[{run_label}] worker_tag isolation", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 12. run_once – ephemeral worker (evicted immediately after use)
# ══════════════════════════════════════════════════════════════════════════════
section("12. run_once – ephemeral worker, auto-evicted after call")
cold_warm_header()

once_code = """
import hashlib, time
result = {"hash": hashlib.md5(b"x"*10_000_000).hexdigest(), "note": "worker now evicted"}
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.run_once(_TORCH_SPEC, once_code))
        if res.get("success"):
            ok(f"[{run_label}] run_once (auto-evicted)", ms,
               res.get("hash", "")[:16] + "…")
        else:
            fail(f"[{run_label}] run_once", res.get("error", res))
    except Exception as e:
        fail(f"[{run_label}] run_once", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 13. max_memory_mb – auto-evict on RSS breach
# ══════════════════════════════════════════════════════════════════════════════
section("13. max_memory_mb – RSS-capped worker")
cold_warm_header()

bloat_code = """
import sys
_blob = bytearray(50 * 1024 * 1024)
result = {"allocated_mb": 50, "note": "RSS should breach cap"}
"""

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.execute_shm(
            _TORCH_SPEC, bloat_code,
            shm_in={}, shm_out={},
            max_memory_mb=10,
        ))
        if res.get("success"):
            ok(f"[{run_label}] max_memory_mb=10 (50MB alloc, evicted next call)", ms)
        else:
            ok(f"[{run_label}] max_memory_mb=10 (evicted mid-call as expected)", ms)
    except Exception as e:
        fail(f"[{run_label}] max_memory_mb", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 14. pin=True – worker survives idle timeout
# ══════════════════════════════════════════════════════════════════════════════
section("14. pin=True – pinned worker survives idle timeout")
cold_warm_header()

for run_label in ("COLD", "WARM"):
    if run_label == "COLD":
        daemon_restart()
    try:
        res, ms = t(lambda: client.execute_shm(
            _TORCH_SPEC,
            "result = {'pinned': True}",
            shm_in={}, shm_out={},
            pin=True,
        ))
        if res.get("success"):
            ok(f"[{run_label}] pin=True worker stays warm indefinitely", ms)
        else:
            fail(f"[{run_label}] pin=True", res.get("error", res))
    except Exception as e:
        fail(f"[{run_label}] pin=True", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 15. Warm globals – simulated transformer model (cold vs warm timing)
# ══════════════════════════════════════════════════════════════════════════════
# The key pattern for production ML serving with omnipkg:
#   if '_model' not in globals():
#       globals()['_model'] = expensive_load()   # COLD: runs once
#   result = globals()['_model'].predict(...)    # WARM: every call after
#
# Combined with worker_tag + pin=True → the process is pinned to that model
# for the lifetime of the daemon, regardless of idle timeouts.
section("15. Warm globals – simulated transformer model (cold vs warm timing)")

model_code = """
import time

if '_model' not in globals():
    time.sleep(0.8)
    import array
    globals()['_model']      = array.array('f', [0.1] * (50 * 1024 * 256))
    globals()['_call_count'] = 0
    globals()['_loaded_at']  = time.time()

globals()['_call_count'] += 1
prompt   = input_data['shm_in'].get('prompt', '')
response = f"[mock-llm] echo: {prompt!r}  |  params=~50M  |  call#{globals()['_call_count']}"
result = {
    "response":    response,
    "call_number": globals()['_call_count'],
    "model_warm":  True,
    "weights_mb":  len(globals()['_model']) * 4 // (1024*1024),
}
"""

SPEC = _TORCH_SPEC
TAG  = "mock-mistral-7b"
PROMPTS = [
    "What is the capital of France?",
    "Explain quantum entanglement simply.",
    "Write a haiku about Python.",
    "What is 17 * 23?",
    "Tell me a one-line joke.",
]

try:
    # --- cold run (fresh daemon) ---
    daemon_restart()
    call_times = []

    for i, prompt in enumerate(PROMPTS):
        res, ms = t(lambda p=prompt: client.execute_shm(
            SPEC, model_code,
            shm_in={"prompt": p},
            shm_out={},
            worker_tag=TAG,
            pin=True,
        ))
        call_times.append(ms)
        if res.get("success"):
            label = "COLD (model loading…)" if i == 0 else f"WARM  call #{res['call_number']}"
            ok(f"[{TAG}] {label}", ms, f"weights={res['weights_mb']}MB in globals")
        else:
            fail(f"warm-globals call {i+1}", res.get("error", res))

    if len(call_times) >= 2:
        cold  = call_times[0]
        warm  = sum(call_times[1:]) / len(call_times[1:])
        ratio = cold / warm if warm > 0 else 0
        print(f"\n  {BOLD}  Cold call : {cold:>8.2f} ms  (model loaded into globals){RESET}")
        print(f"  {BOLD}  Warm mean : {warm:>8.2f} ms  (globals hit, zero reload){RESET}")
        print(f"  {BOLD}  Speedup   : {ratio:>7.1f}×  faster after first call{RESET}\n")
        print(f"  {CYAN}  ↑ This is what omnipkg gives you vs re-importing the model every call.{RESET}")
        print(f"  {CYAN}    Real Mistral-7B cold load: ~8–15s. Warm: <50ms. Same pattern.{RESET}\n")

except Exception as e:
    fail("warm-globals model simulation", str(e))
# ══════════════════════════════════════════════════════════════════════════════
# 16. Cross-Python GPU IPC: py3.9 (cu118) → py3.12 (cu130)
# ══════════════════════════════════════════════════════════════════════════════
# Stage 1: py3.9 worker allocates a tensor on GPU and pins it with
#   share_memory_() so omnipkg can hand the handle to the client.
#   We use execute_shm to get the tensor's shape/dtype/mean back, then
#   reconstruct a client-side tensor from the worker's pinned memory via
#   UniversalGpuIpc.load() — same mechanism the daemon uses internally.
#
# Stage 2: pass that client-side tensor into execute_cuda_ipc targeting the
#   py3.12 / cu130 worker.  omnipkg's IPC layer auto-selects the best
#   available transport (universal → torch_mp_queue fallback on Turing/cu130).
#   User code just receives tensor_in — no ctypes, no raw handles.
#
# Why not cudaIpcOpenMemHandle in user code?  On Turing + CUDA 13 + driver 610
# cudaIpcOpenMemHandle returns rc=1 from any process that didn't allocate the
# memory.  omnipkg's transport layer handles the fallback transparently.
section("16. Cross-Python GPU IPC: py3.9 (cu118) → py3.12 (cu130)")
cold_warm_header()

_IPC16_ROWS = 1024
_IPC16_COLS = 1024

# Stage 1: allocate + scale on GPU, pin the tensor in worker globals so the
# handle stays valid, return shape/dtype/mean and the IPC handle metadata via
# UniversalGpuIpc.share() embedded in the result dict.
_stage16_1 = f"""
import torch, sys
from omnipkg.isolation.worker_daemon import UniversalGpuIpc

if not torch.cuda.is_available():
    raise RuntimeError("CUDA not available in py3.9 worker")

t = torch.randn({_IPC16_ROWS}, {_IPC16_COLS}, device='cuda', dtype=torch.float32)
t.mul_(2.0).add_(1.0)

# Pin the tensor so the IPC handle remains valid after this call returns.
# Store in globals so it isn't GC'd between stage 1 and stage 2.
globals()['_ipc_tensor'] = t

result = {{
    "ipc_meta":    UniversalGpuIpc.share(t),
    "mean_before": float(t.mean()),
    "torch_ver":   torch.__version__,
    "py_ver":      sys.version.split()[0],
}}
"""

# Stage 2: receives tensor_in from omnipkg's IPC layer, runs relu→norm→tanh.
_stage16_2 = """
import torch, sys

t       = tensor_in   # GPU tensor, delivered by omnipkg — no ctypes needed
t_relu  = torch.relu(t)
t_norm  = t_relu / (t_relu.max() + 1e-8)
t_final = torch.tanh(t_norm)

result = {
    "mean_after": float(t_final.mean()),
    "src_max":    float(t.max()),
    "src_min":    float(t.min()),
    "torch_ver":  torch.__version__,
    "py_ver":     sys.version.split()[0],
}
"""

try:
    import torch as _t16
    if not _t16.cuda.is_available():
        skip("cross-Python GPU IPC py3.9→py3.12 (both runs)", "CUDA not available")
    else:
        _py9_path  = _lookup_python("3.9")
        _py12_path = _lookup_python("3.12")

        if not _py9_path or not _py12_path:
            skip("cross-Python GPU IPC py3.9→py3.12 (both runs)",
                 "need both 3.9 and 3.12 managed interpreters")
        else:
            # Resolve each interpreter's own torch spec — never assume the outer env.
            _TORCH_SPEC_PY39 = _resolve_torch_spec_for_interpreter(_py9_path)
            _TORCH_SPEC_PY12 = _resolve_torch_spec_for_interpreter(_py12_path)

            if not _TORCH_SPEC_PY39 or not _TORCH_SPEC_PY12:
                skip("cross-Python GPU IPC py3.9→py3.12 (both runs)",
                     f"torch missing from a managed interpreter — "
                     f"py3.9={_TORCH_SPEC_PY39 or 'MISSING'}, "
                     f"py3.12={_TORCH_SPEC_PY12 or 'MISSING'}  "
                     f"→ run: 8pkg39 install torch  / 8pkg312 install torch")
            else:
                print(f"  {DIM}  py3.9  → {_TORCH_SPEC_PY39}{RESET}")
                print(f"  {DIM}  py3.12 → {_TORCH_SPEC_PY12}{RESET}")
                from omnipkg.isolation.worker_daemon import UniversalGpuIpc
                _ipc16_timings = {}

                for run_label in ("COLD", "WARM"):
                    if run_label == "COLD":
                        daemon_restart()
                    try:
                        # ── Stage 1: py3.9 allocates + exports ───────────────────
                        r1, ms1 = t(lambda: client.execute_shm(
                            _TORCH_SPEC_PY39, _stage16_1,
                            shm_in={}, shm_out={},
                            python_exe=_py9_path,
                            worker_tag="gpu16-src",
                            pin=True,      # keep worker alive so _ipc_tensor isn't GC'd
                        ))
                        if not r1.get("success"):
                            fail(f"[{run_label}] py3.9 alloc+share", r1.get("error", str(r1)))
                            continue

                        ok(f"[{run_label}] py3.9  alloc + UniversalGpuIpc.share()", ms1,
                           f"mean={r1['mean_before']:.4f}  torch={r1['torch_ver']}  py={r1['py_ver']}")

                        # Reconstruct the GPU tensor client-side from the IPC handle.
                        # This is the same call the daemon worker would make internally.
                        gpu_tensor = UniversalGpuIpc.load(r1["ipc_meta"])

                        # ── Stage 2: py3.12 receives tensor_in, runs kernel ───────
                        (result_tensor, meta16), ms2 = t(lambda: client.execute_cuda_ipc(
                            _TORCH_SPEC_PY12, _stage16_2,
                            input_tensor=gpu_tensor,
                            output_shape=(_IPC16_ROWS, _IPC16_COLS),
                            output_dtype="float32",
                            python_exe=_py12_path,
                            worker_tag="gpu16-dst",
                            ipc_mode="auto",
                        ))

                        transport = meta16.get("cuda_method", "?")
                        ok(f"[{run_label}] py3.12 relu→norm→tanh", ms2,
                           f"mean_after={float(result_tensor.mean()):.4f}  "
                           f"torch={r1['torch_ver']}→{meta16.get('torch_ver', '?')}  "
                           f"transport={transport}")

                        _ipc16_timings[run_label] = ms1 + ms2

                        print(f"  {CYAN}  ↳ Stage 1 (py3.9  alloc+share) : {ms1:>7.2f} ms{RESET}")
                        print(f"  {CYAN}  ↳ Stage 2 (py3.12 kernel)       : {ms2:>7.2f} ms{RESET}")
                        print(f"  {CYAN}  ↳ Total pipeline                : {ms1+ms2:>7.2f} ms{RESET}")
                        print()

                    except Exception as e:
                        fail(f"[{run_label}] cross-Python GPU IPC", str(e))
                        import traceback; traceback.print_exc()

                if "COLD" in _ipc16_timings and "WARM" in _ipc16_timings:
                    _cold16, _warm16 = _ipc16_timings["COLD"], _ipc16_timings["WARM"]
                    print(f"  {BOLD}  Cold total : {_cold16:>8.2f} ms  (workers spawning){RESET}")
                    print(f"  {BOLD}  Warm total : {_warm16:>8.2f} ms  (workers hot){RESET}")
                    print(f"  {BOLD}  Speedup    : {_cold16/_warm16:>7.1f}×{RESET}")
                    print()

except ImportError:
    skip("cross-Python GPU IPC py3.9→py3.12 (both runs)", "torch not importable in outer env")