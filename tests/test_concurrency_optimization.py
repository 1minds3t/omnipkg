import sys
import threading
import time

def cpu_bound_work(n):
    """Simulate CPU-intensive work"""
    total = 0
    for i in range(n):
        total += i ** 2
    return total

def benchmark_comparison(num_threads=3, iterations=10_000_000):
    # Test 1: Sequential execution
    print("=" * 60)
    print("Test 1: Sequential Execution (baseline)")
    print("=" * 60)
    start = time.perf_counter()
    for _ in range(num_threads):
        cpu_bound_work(iterations)
    sequential_time = time.perf_counter() - start
    print(f"Sequential time: {sequential_time:.2f}s")
    
    # Test 2: Threaded execution
    print("\n" + "=" * 60)
    print("Test 2: Threaded Execution")
    print("=" * 60)
    start = time.perf_counter()
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=cpu_bound_work, args=(iterations,))
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    
    threaded_time = time.perf_counter() - start
    print(f"Threaded time: {threaded_time:.2f}s")
    
    # Calculate real speedup
    speedup = sequential_time / threaded_time
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"GIL enabled: {getattr(sys, '_is_gil_enabled', lambda: True)()}")
    print(f"Sequential: {sequential_time:.2f}s")
    print(f"Threaded:   {threaded_time:.2f}s")
    print(f"Real speedup: {speedup:.2f}x")
    
    if speedup < 1.1:
        safe_print("⚠️  Threading provides NO speedup (GIL is serializing)")
    elif speedup >= 2.5:
        safe_print("✅ Threading provides significant speedup (GIL-free!)")
    else:
        safe_print("⚡ Threading provides partial speedup")

if __name__ == '__main__':
    benchmark_comparison()
