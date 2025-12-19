from omnipkg.common_utils import safe_print

#!/usr/bin/env python3
"""
Test native CUDA IPC by running the CLIENT in the same torch context as the worker.
This is the CORRECT way to test - the client needs to have torch 1.13.1 loaded
to create the IPC handles.
"""

from omnipkg.loader import omnipkgLoader
from omnipkg.isolation.worker_daemon import DaemonClient
import time

print("=" * 70)
safe_print("🔥 Testing NATIVE CUDA IPC - Client in Correct Context")
print("=" * 70)

# Load PyTorch 1.13.1+cu116 - this is what the worker will also load
with omnipkgLoader("torch==1.13.1+cu116", isolation_mode="overlay"):
    import torch
    import sys
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

    safe_print(f"\n📦 Client PyTorch: {torch.__version__}")
    print(f"   CUDA available: {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        safe_print("❌ CUDA not available")
        sys.exit(1)

    # Create test tensor
    safe_print("\n🧪 Creating test tensor...")
    input_tensor = torch.randn(500, 250, device="cuda")
    print(f"   Shape: {input_tensor.shape}")
    print(f"   Device: {input_tensor.device}")
    print(f"   Checksum: {input_tensor.sum().item():.2f}")

    # Test native IPC detection in client
    safe_print("\n🔍 Testing native IPC detection in client...")
    storage = input_tensor.storage()
    print(f"   Storage type: {type(storage)}")
    print(f"   Has _share_cuda_: {hasattr(storage, '_share_cuda_')}")

    if hasattr(storage, "_share_cuda_"):
        try:
            ipc_data = storage._share_cuda_()
            safe_print("   ✅ Client can create IPC handles!")
            print(f"   IPC data length: {len(ipc_data)}")
        except Exception as e:
            safe_print(f"   ❌ Failed to create IPC handle: {e}")
            sys.exit(1)
    else:
        safe_print("   ❌ _share_cuda_() not available")
        sys.exit(1)

    # Now test with daemon client
    safe_print("\n🔥 Testing with daemon client...")
    client = DaemonClient(auto_start=True)

    # Simple ReLU operation
    code = """
tensor_out[:] = torch.relu(tensor_in)
result = {'status': 'ok'}
"""

    start = time.time()
    output_tensor, response = client.execute_cuda_ipc(
        "torch==1.13.1+cu116", code, input_tensor, input_tensor.shape, "float32"
    )
    elapsed = (time.time() - start) * 1000

    safe_print("\n📊 Results:")
    print(f"   Elapsed: {elapsed:.3f}ms")
    print(f"   Method: {response.get('cuda_method', 'unknown')}")
    print(f"   Output shape: {output_tensor.shape}")
    print(f"   Output device: {output_tensor.device}")
    print(f"   Output checksum: {output_tensor.sum().item():.2f}")

    if response.get("cuda_method") == "native_ipc":
        safe_print("\n🔥🔥🔥 SUCCESS! NATIVE IPC WORKING!")
    else:
        safe_print(f"\n⚠️  Fell back to {response.get('cuda_method')}")

print("\n" + "=" * 70)
