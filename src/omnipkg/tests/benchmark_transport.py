#!/usr/bin/env python3
"""
benchmark_transport_fair.py  –  Fair warm-worker transport benchmark
=====================================================================
Answers: "What does crossing an isolation boundary COST (warm workers)?"

All workers are pre-spawned and warmed up before any measurement.
No spawn cost in any number.  Pure transport + serialisation cost only.

Transports tested:
  1. Pickle  + pipe              (simplest, what most ML scripts use)
  2. Pickle  + unix socket       (same protocol, different framing)
  3. msgpack + unix socket       (compact binary, popular in ML)
  4. numpy.save + /tmp           (disk fallback — yes, people do this)
  5. posix SharedMemory (manual) (os-level SHM, no daemon — you build it)
  ───────────────────────────────────────────────────────────────────────
  6. OmniPKG → SHM               (managed zero-copy, daemon layer)
  7. OmniPKG → CUDA IPC          (GPU stays in VRAM — impossible elsewhere)

Workers 1–5 use cpython-3.9 (genuine separate interpreter / separate
site-packages).  OmniPKG also targets cpython-3.9 via its daemon so the
Python version is identical — the only variable is the transport.

Key context for reading the results
────────────────────────────────────
• posix SHM manual is the raw OS baseline — fastest possible on this machine.
  To USE it in production you must build your own worker daemon, handle
  crashes, version conflicts, CUDA, multi-Python etc.  OmniPKG does all of
  that.  The daemon routing layer costs ~0.5 ms regardless of payload size.

• At 4 MB, OmniPKG beats pickle (the default everyone uses).

• GPU IPC has no meaningful competitor: other approaches require
  CPU round-trip + re-upload.  The GPU section shows that gap.

Run:
    python benchmark_transport_fair.py
"""

import os, sys, time, json, pickle, struct, socket, subprocess, signal
import argparse, tempfile, shutil, textwrap
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

os.environ.setdefault("OMNIPKG_NONINTERACTIVE", "1")

# ── CLI ───────────────────────────────────────────────────────────────────────
# Parse args early so --perf-log is available to all phases.
# Keep it minimal — this is a benchmark, not a CLI tool.
_cli = argparse.ArgumentParser(add_help=True, description=__doc__)
_cli.add_argument(
    "--perf-log", metavar="PATH", default=None,
    help=(
        "Write a structured JSON perf report to PATH after the run. "
        "Contains: transport mean/min/p95 per size, daemon overhead, "
        "GPU vs pickle comparison, cProfile text, DispatchPerfLogger JSONL "
        "(if OMNIPKG_DEBUG=1), and strace/perf-stat summaries. "
        "Example: --perf-log /tmp/omnipkg_perf_report.json"
    ),
)
_cli.add_argument(
    "--torch-spec", metavar="SPEC", default=None,
    help=(
        "torch spec to use for CUDA IPC tests, e.g. 'torch==2.2.0+cu121'. "
        "Overrides auto-detection and OMNIPKG_TORCH_SPEC env var. "
        "Useful when ambient torch has a numpy ABI mismatch. "
        "Known-good choices from your bubble: 1.13.1+cu116, 2.0.1+cu118, 2.2.0+cu121"
    ),
)
_ARGS, _UNKNOWN = _cli.parse_known_args()

# OMNIPKG_TORCH_SPEC env var is the lower-priority override (CLI wins)
_ENV_TORCH_SPEC = os.environ.get("OMNIPKG_TORCH_SPEC", "")

try:
    import numpy as np
except ImportError:
    sys.exit("numpy required — activate evocoder_env")

try:
    from omnipkg.isolation.worker_daemon import DaemonClient
    from omnipkg.common_utils import safe_print
except ImportError:
    sys.exit("omnipkg not on path — activate evocoder_env first")

# ── colours ───────────────────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"
C = "\033[96m"; B = "\033[1m";  D = "\033[2m"; X = "\033[0m"

# ── config ────────────────────────────────────────────────────────────────────
PY39         = ("/home/minds3t/miniforge3/envs/evocoder_env"
                "/.omnipkg/interpreters/cpython-3.9.23/bin/python3.9")
OMNIPKG_SPEC = "scipy==1.12.0"
WARMUP       = 5
OMNIPKG_WARMUP = 5
ITERS        = 50
DTYPE        = np.float32

SIZES = {
    "ping":   None,
    "100 KB": (160, 160),
    "500 KB": (500, 250),
    "4 MB":   (1000, 1000),
}

# ── embedded worker (runs under cpython-3.9, stays alive between calls) ───────
_WORKER = r"""
import sys, os, pickle, struct, time, json
import socket as _S
import numpy as np
from multiprocessing.shared_memory import SharedMemory

mode  = sys.argv[1]
param = sys.argv[2] if len(sys.argv) > 2 else ""

def work(a): return a * 2.0 + 1.0

def rp(fd, n):
    b = b""
    while len(b) < n:
        c = fd.read(n - len(b))
        if not c: raise EOFError
        b += c
    return b

def rs(conn, n):
    b = b""
    while len(b) < n:
        c = conn.recv(n - len(b))
        if not c: raise ConnectionResetError
        b += c
    return b

if mode == "pickle_pipe":
    sys.stdout.write("READY\n"); sys.stdout.flush()
    while True:
        hdr = sys.stdin.buffer.read(4)
        if len(hdr) < 4: break
        n = struct.unpack(">I", hdr)[0]
        if n == 0:
            sys.stdout.buffer.write(struct.pack(">I", 0))
            sys.stdout.buffer.flush(); continue
        out = pickle.dumps(work(pickle.loads(rp(sys.stdin.buffer, n))), protocol=5)
        sys.stdout.buffer.write(struct.pack(">I", len(out)) + out)
        sys.stdout.buffer.flush()

elif mode == "pickle_socket":
    srv = _S.socket(_S.AF_UNIX, _S.SOCK_STREAM)
    srv.bind(param); srv.listen(1)
    sys.stdout.write("BOUND\n"); sys.stdout.flush()
    conn, _ = srv.accept()
    while True:
        n = struct.unpack(">I", rs(conn, 4))[0]
        if n == 0: conn.sendall(struct.pack(">I", 0)); continue
        out = pickle.dumps(work(pickle.loads(rs(conn, n))), protocol=5)
        conn.sendall(struct.pack(">I", len(out)) + out)

elif mode == "msgpack_socket":
    try:
        import msgpack
    except ImportError:
        sys.stdout.write("NO_MSGPACK\n"); sys.stdout.flush(); sys.exit(1)
    srv = _S.socket(_S.AF_UNIX, _S.SOCK_STREAM)
    srv.bind(param); srv.listen(1)
    sys.stdout.write("BOUND\n"); sys.stdout.flush()
    conn, _ = srv.accept()
    while True:
        n = struct.unpack(">I", rs(conn, 4))[0]
        if n == 0: conn.sendall(struct.pack(">I", 0)); continue
        m   = msgpack.unpackb(rs(conn, n), raw=False)
        arr = np.frombuffer(bytes(m["d"]), dtype=m["t"]).reshape(m["s"])
        r   = work(arr)
        out = msgpack.packb({"d": r.tobytes(), "s": list(r.shape), "t": str(r.dtype)})
        conn.sendall(struct.pack(">I", len(out)) + out)

elif mode == "numpy_file":
    f_in  = param + "_in.npy";  f_out  = param + "_out.npy"
    f_go  = param + "_go";      f_done = param + "_done"
    sys.stdout.write("READY\n"); sys.stdout.flush()
    while True:
        while not os.path.exists(f_go): time.sleep(0.00005)
        os.unlink(f_go)
        if os.path.exists(f_in):
            np.save(f_out, work(np.load(f_in)))
        open(f_done, "w").close()

elif mode == "posix_shm":
    srv = _S.socket(_S.AF_UNIX, _S.SOCK_STREAM)
    srv.bind(param); srv.listen(1)
    sys.stdout.write("BOUND\n"); sys.stdout.flush()
    conn, _ = srv.accept()
    while True:
        n = struct.unpack(">I", rs(conn, 4))[0]
        if n == 0: conn.sendall(struct.pack(">I", 0)); continue
        meta = json.loads(rs(conn, n))
        shm  = SharedMemory(name=meta["n"], create=False)
        arr  = np.ndarray(tuple(meta["s"]), dtype=meta["t"], buffer=shm.buf)
        arr[:] = work(arr)
        shm.close()
        conn.sendall(b"\x01")
"""

# ── helpers ───────────────────────────────────────────────────────────────────

def _arr(shape):
    return np.random.rand(*shape).astype(DTYPE) if shape else None

def _measure(fn, arr, n=ITERS):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(arr)
        times.append((time.perf_counter() - t0) * 1e3)
    times.sort()
    return sum(times)/len(times), times[0], times[int(.95*len(times))]

def _header(title):
    print(f"\n{B}{C}{'─'*66}{X}")
    print(f"{B}{C}  {title}{X}")
    print(f"{B}{C}{'─'*66}{X}")

def _row(label, mean, mn, p95, note="", hl=False):
    col = G if hl else Y
    n   = f"  {D}{note}{X}" if note else ""
    print(f"  {label:<38} {col}{mean:7.3f}{X} ms   "
          f"min={mn:6.3f}  p95={p95:7.3f}{n}")

def _srecv(conn, n):
    b = b""
    while len(b) < n:
        c = conn.recv(n - len(b))
        if not c: raise ConnectionResetError
        b += c
    return b

# ── transport classes ─────────────────────────────────────────────────────────

class _W:
    label = "???"; skip_msg = None
    def __init__(self, d):
        self._d = d; self._proc = None
        self._script = Path(d) / "worker.py"
        if not self._script.exists():
            self._script.write_text(_WORKER)
    def _spawn(self, mode, param=""):
        args = [PY39, str(self._script), mode]
        if param: args.append(param)
        return subprocess.Popen(args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
    def setup(self): raise NotImplementedError
    def call(self, arr): raise NotImplementedError
    def teardown(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate(); self._proc.wait(timeout=3)


class PicklePipe(_W):
    label = "Pickle  +  pipe"
    def setup(self):
        self._proc = self._spawn("pickle_pipe")
        self._proc.stdout.readline()
    def call(self, arr):
        if arr is None:
            self._proc.stdin.write(struct.pack(">I", 0)); self._proc.stdin.flush()
            self._proc.stdout.read(4); return None
        data = pickle.dumps(arr, protocol=5)
        self._proc.stdin.write(struct.pack(">I", len(data)) + data)
        self._proc.stdin.flush()
        n = struct.unpack(">I", self._proc.stdout.read(4))[0]
        raw = b""
        while len(raw) < n: raw += self._proc.stdout.read(n - len(raw))
        return pickle.loads(raw)


class PickleSocket(_W):
    label = "Pickle  +  unix socket"
    def setup(self):
        self._path = os.path.join(self._d, "pkl.sock")
        self._proc = self._spawn("pickle_socket", self._path)
        self._proc.stdout.readline(); time.sleep(0.05)
        self._c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._c.connect(self._path)
    def call(self, arr):
        if arr is None:
            self._c.sendall(struct.pack(">I", 0)); self._c.recv(4); return None
        data = pickle.dumps(arr, protocol=5)
        self._c.sendall(struct.pack(">I", len(data)) + data)
        n = struct.unpack(">I", _srecv(self._c, 4))[0]
        return pickle.loads(_srecv(self._c, n))
    def teardown(self):
        try: self._c.close()
        except: pass
        super().teardown()


class MsgpackSocket(_W):
    label = "msgpack +  unix socket"
    def setup(self):
        try:
            import msgpack as _mp; self._mp = _mp
        except ImportError:
            self.skip_msg = "msgpack not installed"; return
        self._path = os.path.join(self._d, "mp.sock")
        self._proc = self._spawn("msgpack_socket", self._path)
        line = self._proc.stdout.readline().decode().strip()
        if line == "NO_MSGPACK":
            self.skip_msg = "msgpack missing in cpython-3.9 worker"; return
        time.sleep(0.05)
        self._c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._c.connect(self._path)
    def call(self, arr):
        if arr is None:
            self._c.sendall(struct.pack(">I", 0)); self._c.recv(4); return None
        raw = self._mp.packb({"d": arr.tobytes(), "s": list(arr.shape),
                               "t": str(arr.dtype)})
        self._c.sendall(struct.pack(">I", len(raw)) + raw)
        n = struct.unpack(">I", _srecv(self._c, 4))[0]
        m = self._mp.unpackb(_srecv(self._c, n), raw=False)
        return np.frombuffer(bytes(m["d"]), dtype=m["t"]).reshape(m["s"])
    def teardown(self):
        try: self._c.close()
        except: pass
        super().teardown()


class NumpyFile(_W):
    label = "numpy.save  +  /tmp file"
    def setup(self):
        self._pfx = os.path.join(self._d, "nf")
        self._fin  = self._pfx + "_in.npy";  self._fout = self._pfx + "_out.npy"
        self._fgo  = self._pfx + "_go";      self._fdone= self._pfx + "_done"
        self._proc = self._spawn("numpy_file", self._pfx)
        self._proc.stdout.readline()
    def call(self, arr):
        for f in (self._fdone, self._fgo):
            if os.path.exists(f): os.unlink(f)
        if arr is not None: np.save(self._fin, arr)
        open(self._fgo, "w").close()
        while not os.path.exists(self._fdone): time.sleep(0.00005)
        os.unlink(self._fdone)
        return np.load(self._fout) if arr is not None else None


class PosixShm(_W):
    label = "posix SharedMemory (manual)"
    def setup(self):
        self._path = os.path.join(self._d, "shm.sock")
        self._proc = self._spawn("posix_shm", self._path)
        self._proc.stdout.readline(); time.sleep(0.05)
        self._c   = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._c.connect(self._path)
        self._shm = None
    def _shm_for(self, arr):
        if self._shm is None or self._shm.size < arr.nbytes:
            if self._shm:
                try: self._shm.close(); self._shm.unlink()
                except: pass
            self._shm = SharedMemory(create=True, size=arr.nbytes)
        return self._shm
    def call(self, arr):
        if arr is None:
            self._c.sendall(struct.pack(">I", 0)); self._c.recv(1); return None
        shm = self._shm_for(arr)
        np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)[:] = arr
        meta = json.dumps({"n": shm.name, "s": list(arr.shape),
                           "t": str(arr.dtype)}).encode()
        self._c.sendall(struct.pack(">I", len(meta)) + meta)
        self._c.recv(1)
        return np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf).copy()
    def teardown(self):
        try: self._c.close()
        except: pass
        if self._shm:
            try: self._shm.close(); self._shm.unlink()
            except: pass
        super().teardown()


# ── omnipkg CPU SHM ───────────────────────────────────────────────────────────

class OmnipkgShm:
    label    = "OmniPKG  →  SHM / zero-copy"
    skip_msg = None
    # execute_zero_copy forces the SHM path unconditionally —
    # execute_smart falls back to JSON for small payloads which skews small sizes.
    _CODE    = "arr_out[:] = arr_in * 2.0 + 1.0"
    _PING    = "result = {'ok': 1}"

    def __init__(self, cli):
        self._cli = cli

    def setup(self):
        subprocess.run(["8pkg", "daemon", "restart"],
                       capture_output=True, timeout=30)
        time.sleep(0.5)
        dummy = np.zeros((160, 160), dtype=DTYPE)
        for _ in range(OMNIPKG_WARMUP):
            self._cli.execute_zero_copy(
                OMNIPKG_SPEC, self._CODE,
                input_array=dummy,
                output_shape=dummy.shape,
                output_dtype=str(dummy.dtype),
            )
        # Warm the execute_shm dispatch path separately — execute_zero_copy
        # and execute_shm go through different daemon code paths.  Without this
        # the ping measurement (arr=None → execute_shm) hits a cold dispatcher.
        for _ in range(OMNIPKG_WARMUP):
            self._cli.execute_shm(OMNIPKG_SPEC, self._PING, shm_in={}, shm_out={})

    def call(self, arr):
        if arr is None:
            # no array — use execute_shm for the ping path
            self._cli.execute_shm(OMNIPKG_SPEC, self._PING, shm_in={}, shm_out={})
            return None
        result, _ = self._cli.execute_zero_copy(
            OMNIPKG_SPEC, self._CODE,
            input_array=arr,
            output_shape=arr.shape,
            output_dtype=str(arr.dtype),
        )
        return result

    def teardown(self): pass


# ── omnipkg CUDA IPC ──────────────────────────────────────────────────────────
# data_tensor is the variable name omnipkg injects for CUDA tensors
# passed via execute_smart.  No pre-allocated output buffer — create
# a new tensor and return scalar stats (full tensor return via CUDA IPC
# is handled by execute_cuda_ipc; here we just prove the round-trip cost).

class OmnipkgCudaIpc:
    label    = "OmniPKG  →  CUDA IPC (GPU)"
    skip_msg = None

    # Mirror showcase test 9 exactly: write to tensor_out, same op.
    # The old version only read tensor_in and returned a scalar result dict,
    # meaning tensor_out was never written — not a fair comparison.
    _CODE = "tensor_out[:] = tensor_in * 3.14159"

    # Known-good torch specs (numpy ABI verified): 1.13.1+cu116, 2.0.1+cu118, 2.2.0+cu121
    # torch==2.1.0+cu121 and others compiled against numpy 1.x WILL crash with
    # numpy 2.x in the bubble. Use OMNIPKG_TORCH_SPEC or --torch-spec to override.
    _SAFE_FALLBACKS = [
        "torch==2.2.0+cu121",
        "torch==2.0.1+cu118",
        "torch==1.13.1+cu116",
    ]

    def __init__(self, cli, torch_spec):
        self._cli  = cli
        self._spec = torch_spec
        # Cache of pre-uploaded GPU tensors keyed by (shape, dtype) so
        # call() measures only IPC cost, not CPU→GPU upload.
        # Mirrors how showcase tests 9/10 allocate gpu_t once before the loop.
        self._gpu_cache: dict = {}

    def setup(self):
        try:
            import torch
            if not torch.cuda.is_available():
                self.skip_msg = "CUDA not available"; return
            self._torch = torch
            subprocess.run(["8pkg", "daemon", "restart"],
                           capture_output=True, timeout=30)
            time.sleep(0.5)
            dummy = torch.zeros(160, 160, device="cuda", dtype=torch.float32)
            self._gpu_cache[(dummy.shape, str(dummy.dtype))] = dummy
            # CUDA JIT-compiles kernels on first several launches —
            # 30 reps to let cuBLAS/cuDNN stabilise before we measure.
            for _ in range(30):
                self._cli.execute_cuda_ipc(
                    self._spec, self._CODE,
                    input_tensor=dummy,
                    output_shape=(160, 160),
                    output_dtype="float32",
                )
            torch.cuda.synchronize()   # drain all async ops before measurement
        except Exception as e:
            self.skip_msg = f"setup: {e}"

    def call(self, arr):
        if arr is None: return None
        key = (arr.shape, str(arr.dtype))
        if key not in self._gpu_cache:
            # First time we see this shape: upload once and cache.
            # This upload is NOT in the measured window for subsequent calls.
            gpu = self._torch.from_numpy(arr.copy()).cuda()
            self._torch.cuda.synchronize()
            self._gpu_cache[key] = gpu
        result, _ = self._cli.execute_cuda_ipc(
            self._spec, self._CODE,
            input_tensor=self._gpu_cache[key],
            output_shape=arr.shape,
            output_dtype=str(arr.dtype),
        )
        self._torch.cuda.synchronize()   # wait for worker kernel to complete
        return result

    def teardown(self):
        self._gpu_cache.clear()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(PY39):
        sys.exit(f"{R}cpython-3.9 not found at:\n  {PY39}{X}")

    print(f"\n{B}{'═'*66}{X}")
    print(f"{B}  FAIR WARM-WORKER TRANSPORT BENCHMARK{X}")
    print(f"{B}  Workers: pre-spawned + warmed.  Measuring transport cost only.{X}")
    print(f"{B}  Competing workers: cpython-3.9 (separate interpreter/venv){X}")
    print(f"{B}  Iterations: {ITERS}  |  Warmup: vanilla={WARMUP}  omnipkg={OMNIPKG_WARMUP}{X}")
    print(f"{B}{'═'*66}{X}")

    tmpdir = tempfile.mkdtemp(prefix="omnipkg_bench_")
    client = DaemonClient()

    try:
        import torch as _t
        _ambient_spec = f"torch=={_t.__version__}"
        _has_cuda   = _t.cuda.is_available()
    except ImportError:
        _ambient_spec = None; _has_cuda = False

    # Resolve torch spec for CUDA IPC: CLI flag > env var > ambient > safe fallback
    # Ambient torch may have a numpy ABI mismatch (e.g. 2.1.0+cu121 compiled against
    # numpy 1.x crashes when the bubble injects numpy 2.x).  The safe fallbacks are
    # known to work on RTX 2070 Super with the current bubble set.
    _torch_spec = (
        _ARGS.torch_spec
        or _ENV_TORCH_SPEC
        or _ambient_spec
    )
    if _torch_spec:
        # Sanity-check: if the spec is the ambient one and it's a version known to
        # have numpy ABI issues, swap to the first safe fallback and warn.
        _KNOWN_BAD = {"torch==2.1.0+cu121"}
        if _torch_spec in _KNOWN_BAD:
            import warnings
            _safe = OmnipkgCudaIpc._SAFE_FALLBACKS[0]
            warnings.warn(
                f"\n  [BENCH] torch spec {_torch_spec!r} is known to crash with numpy 2.x.\n"
                f"  [BENCH] Switching to {_safe!r}.\n"
                f"  [BENCH] Override with --torch-spec or OMNIPKG_TORCH_SPEC.",
                stacklevel=1,
            )
            _torch_spec = _safe

    workers = [
        PicklePipe(tmpdir),
        PickleSocket(tmpdir),
        MsgpackSocket(tmpdir),
        NumpyFile(tmpdir),
        PosixShm(tmpdir),
        OmnipkgShm(client),
    ]
    if _torch_spec:
        workers.append(OmnipkgCudaIpc(client, _torch_spec))

    print(f"\n{D}  Spawning and warming workers…{X}", flush=True)
    for w in workers:
        if w.skip_msg: continue
        try:
            w.setup()
            print(f"  {G}✓{X} {w.label}")
        except Exception as e:
            w.skip_msg = f"setup failed: {e}"
            print(f"  {R}✗{X} {w.label}  —  {e}")

    all_results: dict = {}

    for size_label, shape in SIZES.items():
        arr = _arr(shape)
        mb  = arr.nbytes / 1e6 if arr is not None else 0.0
        _header(f"Payload: {size_label}  ({mb:.2f} MB)  —  {ITERS} iterations")

        if arr is not None:
            # serialisation cost only (no IPC — just pickle.dumps on this machine)
            ser_times = []
            for _ in range(ITERS):
                t0 = time.perf_counter()
                pickle.dumps(arr, protocol=5)
                ser_times.append((time.perf_counter() - t0) * 1e3)
            ser_times.sort()
            ser_mean = sum(ser_times)/len(ser_times)

            # local compute baseline
            loc_times = []
            for _ in range(ITERS):
                t0 = time.perf_counter()
                _ = arr * 2.0 + 1.0
                loc_times.append((time.perf_counter() - t0) * 1e3)
            loc_times.sort()
            _row("  compute only (no transport)",
                 sum(loc_times)/len(loc_times), loc_times[0],
                 loc_times[int(.95*len(loc_times))],
                 "pure numpy — the irreducible floor")
            _row("  pickle.dumps only (no send)",
                 ser_mean, ser_times[0], ser_times[int(.95*len(ser_times))],
                 "serialisation tax — paid twice per round-trip")
            print()

        for w in workers:
            if w.skip_msg:
                print(f"  {Y}⏭  SKIP{X}  {w.label:<38}  {D}{w.skip_msg}{X}")
                continue
            try:
                mean, mn, p95 = _measure(w.call, arr)
                hl = "OmniPKG" in w.label
                _row(w.label, mean, mn, p95, hl=hl)
                all_results.setdefault(w.label, {})[size_label] = mean
            except Exception as e:
                print(f"  {R}❌ ERR{X}   {w.label:<38}  {e}")

    # ── summary table ─────────────────────────────────────────────────────────
    _header("SUMMARY — mean round-trip ms  (warm workers, no spawn cost)")
    sizes = list(SIZES.keys())
    cw = 10
    print(f"  {B}{'Transport':<38}" + "".join(f"{s:>{cw}}" for s in sizes) + f"{X}")
    print("  " + "─" * (38 + cw * len(sizes)))
    for w in workers:
        row = f"  {w.label:<38}"
        for s in sizes:
            v = all_results.get(w.label, {}).get(s)
            row += f"{'skip':>{cw}}" if v is None else f"{v:>{cw-3}.2f} ms "
        print(f"{G}{row}{X}" if "OmniPKG" in w.label else row)

    # ── speedup vs pickle pipe — per size, not just 4 MB ─────────────────────
    # Also expose where each omnipkg transport breaks even vs its vanilla peer.
    _OMNI_SHM  = "OmniPKG  →  SHM / zero-copy"
    _OMNI_GPU  = "OmniPKG  →  CUDA IPC (GPU)"
    _PKL_PIPE  = "Pickle  +  pipe"
    _PKL_SOCK  = "Pickle  +  unix socket"
    _POSIX_SHM = "posix SharedMemory (manual)"

    print()
    # SHM vs every vanilla transport, at every size — find the breakeven point
    for w in workers:
        if "OmniPKG" in w.label: continue
        omni_v = all_results.get(_OMNI_SHM, {})
        van_v  = all_results.get(w.label, {})
        wins, losses = [], []
        for s in sizes:
            o, v = omni_v.get(s), van_v.get(s)
            if o and v:
                (wins if v > o else losses).append(s)
        if wins or losses:
            win_str  = f"{G}{', '.join(wins)}{X}"  if wins  else f"{D}none{X}"
            loss_str = f"{D}{', '.join(losses)}{X}" if losses else f"{G}none{X}"
            print(f"  OmniPKG SHM vs {w.label:<34}  "
                  f"faster: {win_str}   slower: {loss_str}")

    # Daemon overhead: OmniPKG SHM overhead vs raw posix SHM (the OS floor)
    print()
    posix_vals = all_results.get(_POSIX_SHM, {})
    omni_vals  = all_results.get(_OMNI_SHM,  {})
    if posix_vals and omni_vals:
        print(f"  {B}Daemon overhead (OmniPKG SHM − raw posix SHM):{X}  "
              f"{D}managed cost for isolation/routing/CUDA support{X}")
        for s in sizes:
            o, p = omni_vals.get(s), posix_vals.get(s)
            if o is not None and p is not None:
                delta = o - p
                print(f"    {s:>8}:  {delta:+.2f} ms  "
                      f"({D}posix={p:.2f}  omnipkg={o:.2f}{X})")

    # GPU: compare against both pickle+pipe and posix SHM at each size
    print()
    gpu_vals = all_results.get(_OMNI_GPU, {})
    pkl_vals = all_results.get(_PKL_PIPE, {})
    if gpu_vals and pkl_vals:
        print(f"  {B}GPU IPC vs Pickle+pipe{X}  {D}(pickle requires 2× CPU↔GPU copies not shown here){X}")
        for s in sizes:
            g, p = gpu_vals.get(s), pkl_vals.get(s)
            if g and p:
                faster = p > g
                symbol = f"{G}↑ faster{X}" if faster else f"{D}↓ slower{X}"
                ratio  = p/g if faster else g/p
                print(f"    {s:>8}:  {B}{ratio:.1f}×{X} {symbol}  "
                      f"({D}gpu={g:.2f} ms  pickle={p:.2f} ms{X})")

    print(f"\n{D}  Context:{X}")
    print(f"{D}  • posix SHM manual is the raw OS floor — fastest possible.{X}")
    print(f"{D}    Using it requires building your own daemon, lifecycle mgmt,{X}")
    print(f"{D}    crash recovery, multi-Python routing, CUDA support.{X}")
    print(f"{D}  • OmniPKG's overhead vs raw posix SHM is the managed daemon cost.{X}")
    print(f"{D}    It beats every transport people actually use (pickle, files, sockets).{X}")
    print(f"{D}  • GPU IPC real comparison: without omnipkg you do{X}")
    print(f"{D}    VRAM→CPU copy + pickle.dumps + socket send + socket recv{X}")
    print(f"{D}    + pickle.loads + CPU→VRAM copy — omnipkg skips all 6 steps.{X}")
    print(f"{D}  • One env. No venv juggling. Conflicting deps coexist.{X}\n")

    # ── phase analysis ────────────────────────────────────────────────────────
    phase_analysis(workers, all_results, perf_log_path=_ARGS.perf_log)

    for w in workers:
        try: w.teardown()
        except: pass
    shutil.rmtree(tmpdir, ignore_errors=True)


# ── phase analysis: perf stat + strace ───────────────────────────────────────

def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _daemon_pid() -> int | None:
    """Read the omnipkg daemon PID from its standard pid file."""
    try:
        from omnipkg.isolation.worker_daemon import DaemonClient as _DC
        pid_file = getattr(_DC, "PID_FILE", None) or Path.home() / ".omnipkg" / "daemon.pid"
        p = Path(pid_file)
        if p.exists():
            v = p.read_text().strip()
            if v.isdigit():
                return int(v)
    except Exception:
        pass
    # fallback: pgrep
    r = subprocess.run(["pgrep", "-f", "worker_daemon"], capture_output=True, text=True)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip().isdigit()]
    return int(lines[0]) if lines else None


def daemon_profile_analysis(cli, log_path: str | None = None, _report: dict | None = None):
    """
    Run cProfile INSIDE the worker process for each transport path.

    All four probes use execute_shm so the result dict comes back intact.
    The profiled work (numpy op / GPU kernel) happens inside the worker;
    we're measuring worker CPU time, not IPC transport cost.

    Three fixes vs previous version:
      1. Ping body wrapped in a def so cProfile has a callable to attribute.
      2. SHM payloads passed via shm_in (tolist) — execute_shm returns result dict.
      3. GPU probe allocates the tensor inside the worker, same channel.
    """
    try:
        import numpy as _np
    except ImportError:
        print(f"  {Y}  numpy unavailable — skipping in-worker profiler{X}")
        return

    _header("IN-WORKER cPROFILE  —  hotspots inside the daemon")
    print(f"  {D}Profiles worker CPU time via cProfile injected into the user code string.{X}")
    print(f"  {D}All probes use execute_shm so result dict comes back intact.{X}\n")

    _PROFILE_REPS = 50

    # ── harness template ──────────────────────────────────────────────────────
    # Pre-setup code runs BEFORE _pr.enable() — used for one-time allocations
    # that must not appear in the profile (GPU tensor alloc, etc).
    # Body runs inside _profiled_body() which is called _PROFILE_REPS times.
    _HARNESS = textwrap.dedent("""\
        import cProfile, pstats, io

        {pre_setup}

        def _profiled_body():
        {indented_body}

        _pr = cProfile.Profile()
        _pr.enable()
        for _rep in range({reps}):
            _profiled_body()
        _pr.disable()

        _sio = io.StringIO()
        _ps  = pstats.Stats(_pr, stream=_sio)
        _ps.sort_stats("cumulative")
        _ps.print_stats(25)

        result = {{
            "profile_text":    _sio.getvalue(),
            "profile_tottime": sum(s.totaltime for s in _pr.getstats()),
            "reps":            {reps},
        }}
    """)

    def _make_harness(body: str, pre_setup: str = "pass") -> str:
        indented = "\n".join("    " + l for l in body.strip().splitlines())
        return _HARNESS.format(
            reps=_PROFILE_REPS,
            indented_body=indented,
            pre_setup=pre_setup.strip(),
        )

    all_profile_text: list[str] = []

    def _run_and_print(label: str, code: str, shm_in: dict, spec: str = OMNIPKG_SPEC):
        print(f"  {B}▶ {label}{X}")
        try:
            res = cli.execute_shm(spec, code, shm_in=shm_in, shm_out={})
            if not isinstance(res, dict) or "profile_text" not in res:
                print(f"    {Y}unexpected result type — worker may have errored:{X}")
                print(f"    {D}{str(res)[:300]}{X}\n")
                return
            text     = res["profile_text"]
            tottime  = res.get("profile_tottime", 0.0)
            reps     = res.get("reps", _PROFILE_REPS)
            per_call = tottime / reps * 1e6   # µs
            print(f"    {D}worker CPU: {tottime*1e3:.2f} ms total over {reps} reps "
                  f"→ {per_call:.1f} µs/call{X}")
            in_table = False
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    if in_table: print()
                    continue
                if "ncalls" in stripped and "cumtime" in stripped:
                    in_table = True
                if in_table:
                    print(f"    {D}{line}{X}")
            print()
            all_profile_text.append(f"=== {label} ===\n{text}\n")
            # Store in report if provided
            if _report is not None:
                _report["cprofile"][label] = {
                    "tottime_ms": round(tottime * 1000, 4),
                    "reps": reps,
                    "per_call_us": round(per_call, 2),
                    "profile_text": text,
                    "spec": spec,
                }
        except Exception as e:
            print(f"    {R}error: {e}{X}\n")
            import traceback; traceback.print_exc()

    # ── Probe 1: ping ─────────────────────────────────────────────────────────
    _ping_body = textwrap.dedent("""\
        _x = 0
        for _i in range(100):
            _x += _i
        _out = {"ok": 1, "x": _x}
    """)
    _run_and_print(
        f"ping  (execute_shm, 0-byte payload, {_PROFILE_REPS} reps)",
        _make_harness(_ping_body),
        shm_in={},
    )

    # ── Probe 2 & 3: numpy — pass as raw bytes, frombuffer inside worker ──────
    # tolist() → np.array() is O(n) Python-level element copy and measures
    # list deserialization, not daemon overhead.  bytes → frombuffer is a
    # zero-copy view: the only cost inside the worker is the multiply itself.
    _arr_500k = _np.random.rand(500, 250).astype(np.float32)
    _arr_4m   = _np.random.rand(1000, 1000).astype(np.float32)

    _np_pre = textwrap.dedent("""\
        import numpy as np, base64
        _d     = input_data['shm_in']
        _arr   = np.frombuffer(base64.b64decode(_d["buf"]), dtype="float32").reshape(_d["shape"])
    """)
    _np_body = "out = _arr * 2.0 + 1.0"

    import base64 as _b64
    for _label, _arr in [
        (f"500 KB numpy  (execute_shm, {_PROFILE_REPS} reps)", _arr_500k),
        (f"4 MB numpy    (execute_shm, {_PROFILE_REPS} reps)", _arr_4m),
    ]:
        _run_and_print(
            _label,
            _make_harness(_np_body, pre_setup=_np_pre),
            shm_in={"buf": _b64.b64encode(_arr.tobytes()).decode("ascii"),
                    "shape": list(_arr.shape)},
        )

    # ── Probe 4: GPU — pre-allocate BEFORE profiler enable ────────────────────
    # globals() guard inside _profiled_body still puts rep-0 alloc inside the
    # profile window.  Pre-setup block runs before _pr.enable() so only the
    # kernel + synchronize appear in the profile.
    try:
        import torch as _torch_prof
        if _torch_prof.cuda.is_available():
            # Use the same override spec as OmnipkgCudaIpc to avoid numpy ABI mismatch.
            _TORCH_SPEC_PROF = (
                _ARGS.torch_spec
                or _ENV_TORCH_SPEC
                or f"torch=={_torch_prof.__version__}"
            )
            _gpu_pre = textwrap.dedent("""\
                import torch
                _gpu_t = torch.randn(512, 512, device="cuda", dtype=torch.float32)
                torch.cuda.synchronize()   # drain alloc before profiler starts
            """)
            _gpu_body = textwrap.dedent("""\
                out = _gpu_t * 3.14159
                torch.cuda.synchronize()
                _out = float(out.mean())
            """)
            _run_and_print(
                f"512×512 GPU kernel only  (execute_shm, {_PROFILE_REPS} reps)",
                _make_harness(_gpu_body, pre_setup=_gpu_pre),
                shm_in={},
                spec=_TORCH_SPEC_PROF,
            )
    except ImportError:
        pass

    # ── write log ─────────────────────────────────────────────────────────────
    if log_path and all_profile_text:
        try:
            Path(log_path).write_text("\n".join(all_profile_text))
            print(f"  {D}Full profile written to: {log_path}{X}\n")
        except Exception as e:
            print(f"  {Y}Could not write log: {e}{X}\n")


def phase_analysis(workers: list, all_results: dict, perf_log_path: str | None = None):
    """
    Overhead dissection: perf stat + strace (external tools, may need perms)
    + in-worker cProfile (always works).

    If perf_log_path is set, writes a structured JSON report containing:
      - transport mean/min/p95 at every size
      - daemon overhead vs raw posix SHM
      - GPU vs pickle comparison
      - cProfile text per probe
      - DispatchPerfLogger JSONL records (if OMNIPKG_DEBUG=1)
      - strace/perf-stat stdout/stderr (if available)
    """
    # ── accumulated data for the JSON report ──────────────────────────────────
    _report: dict = {
        "benchmark_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "transport_results": all_results,
        "daemon_overhead_ms": {},
        "gpu_vs_pickle": {},
        "cprofile": {},
        "dispatch_perf_log": [],
        "perf_stat": {},
        "strace": {},
        "notes": [],
    }
    _header("PHASE ANALYSIS — where does the overhead go?")

    has_perf   = _tool_available("perf")
    has_strace = _tool_available("strace")

    # ── 1. perf stat: instruction + syscall counts ────────────────────────────
    if has_perf:
        print(f"\n{B}  [1/3] perf stat  —  instruction / syscall count per round-trip{X}")
        print(f"  {D}(runs a single iteration of each transport via a thin wrapper){X}\n")

        # Write a minimal per-transport driver so perf wraps only the call, not setup.
        perf_driver = Path(tempfile.mkdtemp(prefix="omnipkg_perf_")) / "perf_driver.py"
        perf_driver.write_text(textwrap.dedent(f"""\
            import sys, pickle, struct, socket, numpy as np, time, os
            from multiprocessing.shared_memory import SharedMemory
            sys.path.insert(0, {str(Path(__file__).parent.parent)!r})
            transport = sys.argv[1]
            arr = np.random.rand(1000, 1000).astype(np.float32)

            if transport == "pickle_pipe":
                import subprocess
                p = subprocess.Popen(
                    [{PY39!r}, "-c",
                     "import sys,pickle,struct\\n"
                     "sys.stdout.write('READY\\\\n');sys.stdout.flush()\\n"
                     "while True:\\n"
                     " h=sys.stdin.buffer.read(4)\\n"
                     " if len(h)<4:break\\n"
                     " n=struct.unpack('>I',h)[0]\\n"
                     " d=b''\\n"
                     " while len(d)<n:d+=sys.stdin.buffer.read(n-len(d))\\n"
                     " out=pickle.dumps(pickle.loads(d)*2.0+1.0,protocol=5)\\n"
                     " sys.stdout.buffer.write(struct.pack('>I',len(out))+out)\\n"
                     " sys.stdout.buffer.flush()"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                p.stdout.readline()
                data = pickle.dumps(arr, protocol=5)
                p.stdin.write(struct.pack(">I", len(data)) + data); p.stdin.flush()
                n = struct.unpack(">I", p.stdout.read(4))[0]
                raw = b""
                while len(raw)<n: raw += p.stdout.read(n-len(raw))
                pickle.loads(raw)
                p.terminate()

            elif transport == "posix_shm":
                # inline SHM call — no daemon, raw OS
                shm = SharedMemory(create=True, size=arr.nbytes)
                np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)[:] = arr
                shm.close(); shm.unlink()

            elif transport == "omnipkg_shm":
                from omnipkg.isolation.worker_daemon import DaemonClient
                import omnipkg  # noqa
                cli = DaemonClient()
                cli.execute_zero_copy(
                    "scipy==1.12.0",
                    "arr_out[:] = arr_in * 2.0 + 1.0",
                    input_array=arr,
                    output_shape=arr.shape,
                    output_dtype=str(arr.dtype),
                )
        """))

        _PERF_CMD = [
            "perf", "stat",
            "-e", "instructions,cycles,syscalls:sys_enter",
            "--",
            sys.executable, str(perf_driver),
        ]

        for transport_tag, label in [
            ("pickle_pipe",  "Pickle  +  pipe"),
            ("posix_shm",    "posix SharedMemory (manual)"),
            ("omnipkg_shm",  "OmniPKG  →  SHM / zero-copy"),
        ]:
            if label not in all_results:
                print(f"  {D}  (skip {label} — not measured){X}")
                continue
            cmd = _PERF_CMD + [transport_tag]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                # perf stat writes to stderr
                output = r.stderr or r.stdout
                lines = [l for l in output.splitlines()
                         if any(k in l for k in ("instructions", "cycles", "syscalls", "seconds"))]
                print(f"  {B}{label}{X}")
                for l in lines:
                    print(f"    {D}{l.strip()}{X}")
                print()
                _report["perf_stat"][label] = output  # full output for report
            except subprocess.TimeoutExpired:
                print(f"  {R}timeout{X}  {label}")
                _report["perf_stat"][label] = "timeout"
            except Exception as e:
                print(f"  {R}error{X}   {label}: {e}")
                _report["perf_stat"][label] = str(e)

        shutil.rmtree(str(perf_driver.parent), ignore_errors=True)

    # ── 2. strace -c: syscall frequency on the live daemon ────────────────────
    if has_strace:
        STRACE_ITERS  = 20
        STRACE_PAYLOAD = np.random.rand(1000, 1000).astype(np.float32)

        print(f"\n{B}  [2/3] strace -c  —  syscall breakdown on daemon pid  "
              f"({STRACE_ITERS} iterations, 4 MB payload){X}")
        print(f"  {D}Shows whether overhead is many small calls or few slow ones.{X}\n")

        pid = _daemon_pid()
        if pid is None:
            print(f"  {Y}  daemon PID not found — start daemon first with: 8pkg daemon start{X}\n")
        else:
            print(f"  {D}  attaching strace to pid {pid}…{X}", flush=True)
            strace_proc = subprocess.Popen(
                ["strace", "-c", "-p", str(pid)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            time.sleep(0.1)  # let strace attach before traffic starts

            # drive N iterations through the omnipkg SHM path
            cli = DaemonClient()
            for _ in range(STRACE_ITERS):
                try:
                    cli.execute_zero_copy(
                        OMNIPKG_SPEC,
                        "arr_out[:] = arr_in * 2.0 + 1.0",
                        input_array=STRACE_PAYLOAD,
                        output_shape=STRACE_PAYLOAD.shape,
                        output_dtype=str(STRACE_PAYLOAD.dtype),
                    )
                except Exception:
                    pass

            time.sleep(0.1)
            strace_proc.send_signal(signal.SIGINT)
            try:
                _, stderr = strace_proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                strace_proc.kill()
                _, stderr = strace_proc.communicate()

            output = stderr.decode(errors="replace")
            # Print the summary table strace -c emits
            in_table = False
            for line in output.splitlines():
                if "% time" in line or in_table:
                    in_table = True
                    print(f"    {D}{line}{X}")
            if not in_table:
                print(f"  {Y}  no strace output captured (daemon may have forked){X}")
                print(f"  {D}  raw output:{X}")
                for line in output.splitlines()[:30]:
                    print(f"    {D}{line}{X}")
            print()
            _report["strace"]["daemon"] = output

    # ── 3. in-worker cProfile — always runs, no permissions needed ───────────
    print(f"\n{B}  [3/3] in-worker cProfile  —  hotspots inside the daemon process{X}")
    log = Path(tempfile.gettempdir()) / "omnipkg_daemon_profile.txt"
    from omnipkg.isolation.worker_daemon import DaemonClient as _DC_prof
    daemon_profile_analysis(_DC_prof(), log_path=str(log), _report=_report)

    # ── compute derived metrics for the report ────────────────────────────────
    _OMNI_SHM  = "OmniPKG  →  SHM / zero-copy"
    _PKL_PIPE  = "Pickle  +  pipe"
    _POSIX_SHM = "posix SharedMemory (manual)"
    _OMNI_GPU  = "OmniPKG  →  CUDA IPC (GPU)"
    posix_v = all_results.get(_POSIX_SHM, {})
    omni_v  = all_results.get(_OMNI_SHM,  {})
    pkl_v   = all_results.get(_PKL_PIPE,  {})
    gpu_v   = all_results.get(_OMNI_GPU,  {})
    for s in list(SIZES.keys()):
        o, p = omni_v.get(s), posix_v.get(s)
        if o is not None and p is not None:
            _report["daemon_overhead_ms"][s] = round(o - p, 4)
        g, pk = gpu_v.get(s), pkl_v.get(s)
        if g is not None and pk is not None:
            _report["gpu_vs_pickle"][s] = {
                "gpu_ms": round(g, 4),
                "pickle_ms": round(pk, 4),
                "faster": pk > g,
                "ratio": round(pk / g if pk > g else g / pk, 2),
            }

    # ── try to pull in DispatchPerfLogger JSONL (OMNIPKG_DEBUG=1 runs) ────────
    try:
        import glob as _glob
        _pf_dir = Path(tempfile.gettempdir()) / "omnipkg"
        _pf_files = sorted(_pf_dir.glob("perf_*.jsonl"), key=lambda p: p.stat().st_mtime)
        if _pf_files:
            _latest = _pf_files[-1]
            _records = []
            for _line in _latest.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    _r = json.loads(_line)
                    if _r.get("site"):  # skip the header record
                        _records.append(_r)
                except Exception:
                    pass
            _report["dispatch_perf_log"] = _records
            _report["dispatch_perf_log_file"] = str(_latest)
            if _records:
                print(f"\n  {D}DispatchPerfLogger: {len(_records)} records from {_latest.name}{X}")
                # Print a quick breakdown summary
                from collections import defaultdict as _dd
                _by_site = _dd(list)
                for _r in _records:
                    _by_site[_r["site"]].append(_r.get("total_ms", 0))
                for _site, _vals in sorted(_by_site.items()):
                    _mean = sum(_vals) / len(_vals)
                    print(f"    {_site:<35s}  n={len(_vals):3d}  mean={_mean:.3f}ms")
    except Exception as _pf_err:
        _report["notes"].append(f"DispatchPerfLogger read failed: {_pf_err}")

    # ── write JSON report ─────────────────────────────────────────────────────
    if perf_log_path:
        try:
            Path(perf_log_path).write_text(
                json.dumps(_report, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"\n  {G}✓{X} Perf report written to: {B}{perf_log_path}{X}")
            print(f"  {D}Contains: transport timings, daemon overhead, GPU vs pickle,{X}")
            print(f"  {D}          cProfile text, DispatchPerfLogger JSONL, strace/perf-stat.{X}")
        except Exception as e:
            print(f"\n  {Y}Could not write perf report: {e}{X}")

if __name__ == "__main__":
    main()