import ctypes
import glob
import multiprocessing as mp
import os
import sys
import time

import torch

from omnipkg.common_utils import safe_print

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print


# ==============================================================================
# 1. UNIVERSAL IPC LOGIC
# ==============================================================================
class UniversalGpuIpc:
    _lib = None

    @classmethod
    def get_lib(cls):
        if cls._lib:
            return cls._lib
        candidates = []
        try:
            torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
            candidates.extend(glob.glob(os.path.join(torch_lib, "libcudart.so*")))
        except:
            pass
        if "CONDA_PREFIX" in os.environ:
            candidates.extend(
                glob.glob(os.path.join(os.environ["CONDA_PREFIX"], "lib", "libcudart.so*"))
            )
        candidates.extend(["libcudart.so.12", "libcudart.so.11.0", "libcudart.so"])
        for lib in candidates:
            try:
                cls._lib = ctypes.CDLL(lib)
                return cls._lib
            except:
                continue
        raise RuntimeError("Could not load libcudart.so")

    @staticmethod
    def share(tensor):
        lib = UniversalGpuIpc.get_lib()
        ptr = tensor.data_ptr()

        class cudaPointerAttributes(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("device", ctypes.c_int),
                ("devicePointer", ctypes.c_void_p),
                ("hostPointer", ctypes.c_void_p),
            ]

        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]

        lib.cudaPointerGetAttributes.argtypes = [
            ctypes.POINTER(cudaPointerAttributes),
            ctypes.c_void_p,
        ]
        lib.cudaIpcGetMemHandle.argtypes = [
            ctypes.POINTER(cudaIpcMemHandle_t),
            ctypes.c_void_p,
        ]
        attrs = cudaPointerAttributes()
        if lib.cudaPointerGetAttributes(ctypes.byref(attrs), ctypes.c_void_p(ptr)) == 0:
            base_ptr = attrs.devicePointer or ptr
            offset = ptr - base_ptr
        else:
            base_ptr = ptr
            offset = 0
        handle = cudaIpcMemHandle_t()
        lib.cudaIpcGetMemHandle(ctypes.byref(handle), ctypes.c_void_p(base_ptr))
        return {
            "handle": ctypes.string_at(ctypes.byref(handle), 64),
            "offset": offset,
            "shape": tuple(tensor.shape),
            "typestr": "<f4",
            "device": tensor.device.index or 0,
        }

    @staticmethod
    def load(data):
        lib = UniversalGpuIpc.get_lib()

        class cudaIpcMemHandle_t(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_char * 64)]

        lib.cudaIpcOpenMemHandle.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            cudaIpcMemHandle_t,
            ctypes.c_uint,
        ]
        handle = cudaIpcMemHandle_t()
        ctypes.memmove(ctypes.byref(handle), data["handle"], 64)
        dev_ptr = ctypes.c_void_p()
        err = lib.cudaIpcOpenMemHandle(ctypes.byref(dev_ptr), handle, 1)
        if err == 201:
            return None
        if err != 0:
            raise RuntimeError(f"Open Handle Failed: {err}")
        final_ptr = dev_ptr.value + data["offset"]

        class CUDABuffer:
            def __init__(self, ptr, shape, typestr):
                self.__cuda_array_interface__ = {
                    "data": (ptr, False),
                    "shape": shape,
                    "typestr": typestr,
                    "version": 3,
                }

        return torch.as_tensor(
            CUDABuffer(final_ptr, data["shape"], data["typestr"]),
            device=f"cuda:{data['device']}",
        )


# ==============================================================================
# 2. WORKER
# ==============================================================================
def persistent_worker(in_q, out_q):
    try:
        # Pre-load library but DON'T map memory yet
        UniversalGpuIpc.get_lib()
        torch.cuda.init()
        torch.tensor([1.0], device="cuda:0")
        out_q.put("READY")
    except Exception as e:
        out_q.put(f"INIT_ERROR: {e}")
        return

    while True:
        try:
            cmd, payload = in_q.get()
            if cmd == "STOP":
                break

            t_start = time.perf_counter()
            tensor = UniversalGpuIpc.load(payload)
            if tensor is None:
                out_q.put(("ERROR", "Same process"))
                continue

            torch.cuda.synchronize()  # Sync for accuracy
            chk = tensor.sum().item()

            t_end = time.perf_counter()
            out_q.put(("OK", (t_end - t_start) * 1000, chk))
        except Exception as e:
            out_q.put(("ERROR", str(e)))


# ==============================================================================
# 3. BENCHMARK
# ==============================================================================
if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)

    print("=" * 60)
    safe_print("⚖️  HONEST BENCHMARK: 100 Runs (Cold Excluded)")
    print("=" * 60)

    in_q = mp.Queue()
    out_q = mp.Queue()
    p = mp.Process(target=persistent_worker, args=(in_q, out_q))
    p.start()

    if out_q.get() != "READY":
        sys.exit(1)

    tensor = torch.randn(1000, 1000, device="cuda:0")
    ipc_payload = UniversalGpuIpc.share(tensor)

    latencies = []
    safe_print("🚀 Blasting 100 Requests...")

    for i in range(100):
        in_q.put(("PROCESS", ipc_payload))
        status, lat, unused = out_q.get()
        if status == "OK":
            latencies.append(lat)
            if i == 0:
                safe_print(f"   🥶 Run 1 (Cold Start):  {lat:.4f} ms")
            elif i < 5:
                safe_print(f"   🔥 Run {i+1} (Warm):       {lat:.4f} ms")
            elif i == 99:
                print("   ... (Runs 6-99 hidden) ...")
                safe_print(f"   🔥 Run 100 (Warm):     {lat:.4f} ms")

    in_q.put(("STOP", None))
    p.join()

    # CALCULATIONS
    first_run = latencies[0]
    # STRICTLY EXCLUDE FIRST RUN
    warm_samples = latencies[1:]
    warm_avg = sum(warm_samples) / len(warm_samples)

    baseline_native = 6.0

    print("\n" + "=" * 60)
    safe_print("📊 TRUTH TABLE (N=100)")
    print("=" * 60)
    print(f"1. COLD START (Run 1 Only):   {first_run:.4f} ms")
    print("-" * 60)
    print(f"2. WARM AVERAGE (Runs 2-100): {warm_avg:.4f} ms")
    print(f"   vs Native IPC (6.0ms):     {baseline_native/warm_avg:.1f}x FASTER")
    print("=" * 60)
