import os
import sys
import shutil
import torch
import time
from torch.utils.cpp_extension import load_inline

# ═══════════════════════════════════════════════════════════════
# 0. SETUP: FIND NVCC (The Compiler)
# ═══════════════════════════════════════════════════════════════
def setup_cuda_env():
    # 1. Check if already set
    if "CUDA_HOME" in os.environ:
        return

    # 2. Try to find nvcc in PATH
    nvcc_path = shutil.which("nvcc")
    
    # 3. If not in PATH, check Conda environment
    if not nvcc_path:
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidate = os.path.join(conda_prefix, "bin", "nvcc")
            if os.path.exists(candidate):
                nvcc_path = candidate

    # 4. If still missing, check standard locations
    if not nvcc_path:
        for p in ["/usr/local/cuda/bin/nvcc", "/usr/bin/nvcc"]:
            if os.path.exists(p):
                nvcc_path = p
                break

    if not nvcc_path:
        print("❌ Critical Error: 'nvcc' (CUDA Compiler) not found.")
        print("   If you are in Conda, install it: conda install -c nvidia cuda-nvcc")
        print("   Or set CUDA_HOME manually.")
        sys.exit(1)

    # Set CUDA_HOME based on nvcc location
    # nvcc is usually in $CUDA_HOME/bin/nvcc
    cuda_home = os.path.dirname(os.path.dirname(nvcc_path))
    os.environ["CUDA_HOME"] = cuda_home
    
    # Add to PATH just in case
    os.environ["PATH"] = os.path.join(cuda_home, "bin") + os.pathsep + os.environ["PATH"]
    
    print(f"🔧 Found NVCC at: {nvcc_path}")
    print(f"🔧 Set CUDA_HOME to: {cuda_home}")

setup_cuda_env()

# ═══════════════════════════════════════════════════════════════
# 1. THE CUDA SOURCE (Hardware Logic + Host Launcher)
# ═══════════════════════════════════════════════════════════════
cuda_source = """
#include <torch/extension.h>
#include <cuda_runtime.h>

// ---------------------------------------------------------
// GPU CONTROL BLOCK (Resides in VRAM)
// ---------------------------------------------------------
struct GPUControlBlock {
    int ticket_counter;  // "Take a number"
    int now_serving;     // "Now serving number X"
    int head_index;      // Current write position in Ring Buffer
    int total_writes;    // Stats: count successes
};

// ---------------------------------------------------------
// THE RING BUFFER WRITER KERNEL (Run on GPU)
// ---------------------------------------------------------
__global__ void gpu_ring_write_kernel(
    float* ring_buffer,       // The massive data storage
    float* input_data,        // Data we want to write (per block)
    int* ctrl_raw,            // Raw pointer to control block tensor
    int buffer_capacity,      // Total floats in ring buffer
    int packet_size           // Floats per write
) {
    // Cast raw int pointer to struct
    GPUControlBlock* ctrl = (GPUControlBlock*)ctrl_raw;

    int tid = threadIdx.x;
    __shared__ int my_ticket;
    __shared__ int write_offset;

    // 1. ACQUIRE LOCK (TICKET SYSTEM)
    if (tid == 0) {
        // Take a ticket
        my_ticket = atomicAdd(&ctrl->ticket_counter, 1);
        
        // Spin until served
        while (atomicAdd(&ctrl->now_serving, 0) != my_ticket) {
            // Busy wait (fastest on GPU)
        }
    }
    
    __syncthreads(); 

    // 2. CRITICAL SECTION
    if (tid == 0) {
        write_offset = ctrl->head_index;
        ctrl->head_index = (ctrl->head_index + packet_size) % buffer_capacity;
        atomicAdd(&ctrl->total_writes, 1);
    }
    __syncthreads();

    // 3. WRITE DATA
    for (int i = tid; i < packet_size; i += blockDim.x) {
        int dest_idx = (write_offset + i) % buffer_capacity;
        ring_buffer[dest_idx] = input_data[i] + (float)my_ticket; 
    }
    
    __threadfence(); // Flush L2

    // 4. RELEASE LOCK
    __syncthreads();
    
    if (tid == 0) {
        atomicAdd(&ctrl->now_serving, 1);
    }
}

// ---------------------------------------------------------
// HOST LAUNCHER (Callable from Python)
// ---------------------------------------------------------
void launch_gpu_ring_write(
    torch::Tensor ring_buffer,
    torch::Tensor input_data,
    torch::Tensor ctrl_block,
    int buffer_capacity,
    int packet_size,
    int num_writers
) {
    // Extract raw pointers from PyTorch Tensors
    float* d_ring = ring_buffer.data_ptr<float>();
    float* d_input = input_data.data_ptr<float>();
    int* d_ctrl = ctrl_block.data_ptr<int>();

    // Launch Config
    // One block per "Worker/Writer"
    // 128 threads per block to copy data in parallel
    dim3 grid(num_writers);
    dim3 block(128);

    gpu_ring_write_kernel<<<grid, block>>>(
        d_ring, d_input, d_ctrl, buffer_capacity, packet_size
    );
}
"""

def main():
    print("🚀 COMPILING GPU KERNEL (JIT)...")
    
    try:
        module = load_inline(
            name='gpu_ring_v2',
            cpp_sources="void launch_gpu_ring_write(torch::Tensor ring_buffer, torch::Tensor input_data, torch::Tensor ctrl_block, int buffer_capacity, int packet_size, int num_writers);",
            cuda_sources=cuda_source,
            functions=['launch_gpu_ring_write'],
            with_cuda=True,
            extra_cuda_cflags=["-O3"]
        )
    except Exception as e:
        print(f"\n❌ Compilation Failed. Details:\n{e}")
        return
    
    # 🔧 CONFIGURATION
    BUFFER_SIZE = 1024 * 1024  # 1 Million floats
    PACKET_SIZE = 1024         # Write 1024 floats per transaction
    NUM_WRITERS = 1000         # 1000 Concurrent blocks trying to write
    
    print(f"📦 Setup: Ring Buffer Size: {BUFFER_SIZE}")
    print(f"⚡ Action: {NUM_WRITERS} concurrent writers fighting for the lock")

    ring_buffer = torch.zeros(BUFFER_SIZE, device='cuda', dtype=torch.float32)
    
    # Control block: [ticket, serving, head, stats]
    ctrl_block = torch.zeros(4, device='cuda', dtype=torch.int32)
    
    input_data = torch.ones(PACKET_SIZE, device='cuda', dtype=torch.float32)

    # Warmup
    torch.cuda.synchronize()
    start_t = time.perf_counter()
    
    # EXECUTE ON GPU
    module.launch_gpu_ring_write(
        ring_buffer, 
        input_data, 
        ctrl_block, 
        BUFFER_SIZE, 
        PACKET_SIZE,
        NUM_WRITERS
    )
    
    torch.cuda.synchronize()
    end_t = time.perf_counter()
    
    # VERIFY
    stats = ctrl_block.cpu().numpy()
    ticket_count = stats[0]
    served_count = stats[1]
    total_writes = stats[3]
    
    print("\n🏁 RESULTS:")
    print(f"   Time Taken: {(end_t - start_t)*1000:.2f} ms")
    print(f"   Throughput: {NUM_WRITERS / (end_t - start_t):.0f} transactions/sec")
    print(f"   Total Writes: {total_writes} / {NUM_WRITERS}")
    
    if ticket_count == served_count == NUM_WRITERS:
        print("✅ SUCCESS: Perfect serialization. No deadlocks.")
    else:
        print(f"❌ FAILURE: Mismatch (Tickets: {ticket_count}, Served: {served_count})")

if __name__ == "__main__":
    main()