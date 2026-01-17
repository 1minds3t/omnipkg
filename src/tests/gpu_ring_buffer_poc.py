import os
import sys
import shutil
import torch
import time
from torch.utils.cpp_extension import load_inline

def setup_cuda_env():
    if "CUDA_HOME" in os.environ: return
    nvcc_path = shutil.which("nvcc")
    if not nvcc_path:
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            candidate = os.path.join(conda_prefix, "bin", "nvcc")
            if os.path.exists(candidate): nvcc_path = candidate
    if not nvcc_path and os.path.exists("/usr/local/cuda/bin/nvcc"):
        nvcc_path = "/usr/local/cuda/bin/nvcc"
    if not nvcc_path:
        print("❌ Error: nvcc not found."); sys.exit(1)
    cuda_home = os.path.dirname(os.path.dirname(nvcc_path))
    os.environ["CUDA_HOME"] = cuda_home
    os.environ["PATH"] = os.path.join(cuda_home, "bin") + os.pathsep + os.environ["PATH"]

setup_cuda_env()

cuda_source = """
#include <torch/extension.h>
#include <cuda_runtime.h>

struct GPUControlBlock {
    int ticket_counter;
    int now_serving;
    int head_index;
    int total_writes;
    int tail_index;
    int total_reads;
    int debug_counter;  // For timeout detection
};

__global__ void producer_kernel(
    float* ring_buffer,
    float* input_data,
    int* ctrl_raw,
    int buffer_capacity,
    int packet_size
) {
    GPUControlBlock* ctrl = (GPUControlBlock*)ctrl_raw;
    int tid = threadIdx.x;
    
    __shared__ int my_ticket;
    __shared__ int write_offset;

    if (tid == 0) {
        my_ticket = atomicAdd(&ctrl->ticket_counter, 1);
        while (atomicAdd(&ctrl->now_serving, 0) != my_ticket) {}
    }
    __syncthreads(); 

    if (tid == 0) {
        write_offset = ctrl->head_index;
        ctrl->head_index = (ctrl->head_index + packet_size) % buffer_capacity;
    }
    __syncthreads();

    for (int i = tid; i < packet_size; i += blockDim.x) {
        int dest_idx = (write_offset + i) % buffer_capacity;
        ring_buffer[dest_idx] = input_data[i] + (float)my_ticket; 
    }
    
    __threadfence_system();
    __syncthreads();
    
    if (tid == 0) {
        atomicAdd(&ctrl->total_writes, 1);
        atomicAdd(&ctrl->now_serving, 1);
    }
}

__global__ void consumer_kernel(
    float* ring_buffer,
    float* output_sink,
    int* ctrl_raw,
    int buffer_capacity,
    int packet_size,
    int target_reads,
    int max_iterations
) {
    GPUControlBlock* ctrl = (GPUControlBlock*)ctrl_raw;
    int tid = threadIdx.x;
    int iteration = 0;
    
    while (iteration < max_iterations) {
        __shared__ int available_writes;
        __shared__ int current_reads;
        __shared__ int my_batch_id;
        __shared__ int read_offset;

        if (tid == 0) {
            available_writes = atomicAdd(&ctrl->total_writes, 0);
            current_reads = atomicAdd(&ctrl->total_reads, 0);
            my_batch_id = -1;
            
            // Debug counter
            atomicAdd(&ctrl->debug_counter, 1);
        }
        __syncthreads();

        // Stop condition: we've read everything we need
        if (current_reads >= target_reads) {
            break;
        }

        // Is there data to read?
        if (available_writes > current_reads) {
            if (tid == 0) {
                int old = atomicAdd(&ctrl->total_reads, 1);
                if (old < target_reads) {
                    my_batch_id = old;
                    read_offset = (my_batch_id * packet_size) % buffer_capacity;
                } else {
                    atomicAdd(&ctrl->total_reads, -1);
                    my_batch_id = -1;
                }
            }
            __syncthreads();

            if (my_batch_id != -1) {
                __threadfence_system(); 
                
                for (int i = tid; i < packet_size; i += blockDim.x) {
                    int src_idx = (read_offset + i) % buffer_capacity;
                    output_sink[my_batch_id * packet_size + i] = ring_buffer[src_idx];
                }
            }
            __syncthreads();
        }
        
        iteration++;
    }
}

void launch_producers(
    torch::Tensor ring_buffer, torch::Tensor input_data, torch::Tensor ctrl_block,
    int buffer_capacity, int packet_size, int num_writers
) {
    float* d_ring = ring_buffer.data_ptr<float>();
    float* d_input = input_data.data_ptr<float>();
    int* d_ctrl = ctrl_block.data_ptr<int>();
    producer_kernel<<<num_writers, 128>>>(d_ring, d_input, d_ctrl, buffer_capacity, packet_size);
}

void launch_consumers(
    torch::Tensor ring_buffer, torch::Tensor output_sink, torch::Tensor ctrl_block,
    int buffer_capacity, int packet_size, int num_readers, int target_reads, int max_iterations
) {
    float* d_ring = ring_buffer.data_ptr<float>();
    float* d_out = output_sink.data_ptr<float>();
    int* d_ctrl = ctrl_block.data_ptr<int>();
    consumer_kernel<<<num_readers, 128>>>(d_ring, d_out, d_ctrl, buffer_capacity, packet_size, target_reads, max_iterations);
}
"""

def main():
    print("\n🏭 COMPILING DEBUG PRODUCER/CONSUMER SYSTEM...")
    try:
        module = load_inline(
            name='gpu_mpmc_v3_debug',
            cpp_sources="void launch_producers(torch::Tensor ring_buffer, torch::Tensor input_data, torch::Tensor ctrl_block, int buffer_capacity, int packet_size, int num_writers); void launch_consumers(torch::Tensor ring_buffer, torch::Tensor output_sink, torch::Tensor ctrl_block, int buffer_capacity, int packet_size, int num_readers, int target_reads, int max_iterations);",
            cuda_sources=cuda_source,
            functions=['launch_producers', 'launch_consumers'],
            with_cuda=True,
            extra_cuda_cflags=["-O3"]
        )
    except Exception as e: 
        print(f"❌ Compile Error: {e}")
        return

    BUFFER_SIZE = 1024 * 1024 * 10
    PACKET_SIZE = 1024
    NUM_WRITERS = 1000
    NUM_READERS = 10
    TOTAL_OPS   = NUM_WRITERS
    MAX_ITERATIONS = 1000000  # Safety timeout

    ring_buffer = torch.zeros(BUFFER_SIZE, device='cuda', dtype=torch.float32)
    # [ticket, serving, head, total_writes, tail, total_reads, debug_counter]
    ctrl_block = torch.zeros(7, device='cuda', dtype=torch.int32)
    input_data = torch.ones(PACKET_SIZE, device='cuda', dtype=torch.float32)
    output_sink = torch.zeros(TOTAL_OPS * PACKET_SIZE, device='cuda', dtype=torch.float32)

    print(f"\n⚡ STEP 1: Launching {NUM_WRITERS} producers...")
    torch.cuda.synchronize()
    
    # Launch producers FIRST and wait for them to complete
    module.launch_producers(
        ring_buffer, input_data, ctrl_block, 
        BUFFER_SIZE, PACKET_SIZE, 
        NUM_WRITERS
    )
    torch.cuda.synchronize()
    
    stats = ctrl_block.cpu().numpy()
    print(f"   ✓ Producers finished: {stats[3]} writes committed")

    print(f"\n⚡ STEP 2: Launching {NUM_READERS} consumers...")
    start_t = time.perf_counter()
    
    module.launch_consumers(
        ring_buffer, output_sink, ctrl_block, 
        BUFFER_SIZE, PACKET_SIZE, 
        NUM_READERS, TOTAL_OPS, MAX_ITERATIONS
    )
    
    torch.cuda.synchronize()
    end_t = time.perf_counter()

    stats = ctrl_block.cpu().numpy()
    writes = stats[3]
    reads = stats[5]
    debug_count = stats[6]
    
    print("\n🏁 RESULTS:")
    print(f"   Time Taken:   {(end_t - start_t)*1000:.2f} ms")
    print(f"   Throughput:   {TOTAL_OPS / (end_t - start_t):.0f} packets/sec")
    print(f"   Writes Committed: {writes}")
    print(f"   Reads Completed:  {reads}")
    print(f"   Consumer Loop Iterations: {debug_count}")
    
    sample = output_sink[0:5].cpu().numpy()
    print(f"   Sample Data Read: {sample}")
    
    if writes == reads == NUM_WRITERS:
        print("✅ SUCCESS: Full Producer-Consumer Pipeline Verified!")
    else:
        print(f"❌ FAILURE: Mismatch - writes={writes}, reads={reads}, expected={NUM_WRITERS}")
        if debug_count >= MAX_ITERATIONS * NUM_READERS:
            print("⚠️  TIMEOUT: Consumers hit iteration limit (possible deadlock)")

if __name__ == "__main__":
    main()