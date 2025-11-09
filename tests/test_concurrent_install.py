# filename: tests/test_concurrent_install.py
import sys
import subprocess
import json
from pathlib import Path
import time
import concurrent.futures
import threading

print_lock = threading.Lock()
omnipkg_lock = threading.Lock()

def safe_print(*args):
    with print_lock:
        print(*args, flush=True)

def run_omnipkg_cli(python_exe: str, args: list, thread_id: int) -> tuple[int, float]:
    """Run omnipkg CLI in the CORRECT Python context via subprocess."""
    prefix = f"[T{thread_id}]"
    start = time.perf_counter()
    
    cmd = [python_exe, '-m', 'omnipkg.cli'] + args
    safe_print(f"{prefix} Running: {' '.join(args[:3])}")
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    
    duration_ms = (time.perf_counter() - start) * 1000
    
    if result.returncode != 0:
        safe_print(f"{prefix} ❌ Command failed: {result.stderr[:200]}")
    
    return result.returncode, duration_ms

def get_interpreter_path(version: str) -> str:
    """Get interpreter path from omnipkg (using system Python)."""
    result = subprocess.run(
        ['omnipkg', 'info', 'python'],
        capture_output=True,
        text=True,
        check=True
    )
    
    for line in result.stdout.splitlines():
        if f'Python {version}:' in line:
            # Extract path from line like: "• Python 3.9: /path/to/python3.9"
            path = line.split(':', 1)[1].strip().split()[0]
            return path
    
    raise RuntimeError(f"Python {version} not found")

def adopt_if_needed(version: str, thread_id: int) -> bool:
    """Adopt Python version if not already present."""
    prefix = f"[T{thread_id}|Adopt]"
    
    try:
        # Check if already adopted
        get_interpreter_path(version)
        safe_print(f"{prefix} ✅ Python {version} already adopted")
        return True
    except RuntimeError:
        # Need to adopt
        safe_print(f"{prefix} 🚀 Adopting Python {version}...")
        result = subprocess.run(
            ['omnipkg', 'python', 'adopt', version],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            safe_print(f"{prefix} ✅ Adopted Python {version}")
            return True
        else:
            safe_print(f"{prefix} ❌ Failed to adopt Python {version}")
            return False

def test_dimension(config: tuple, thread_id: int) -> dict:
    """Test one Python+Rich combination using SUBPROCESS calls only."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings = {}
    timings['start'] = time.perf_counter()
    
    try:
        safe_print(f"{prefix} 🚀 Testing Python {py_version} with Rich {rich_version}")
        
        # Get interpreter path (this is fast, hits cache)
        python_exe = get_interpreter_path(py_version)
        
        # CRITICAL: Lock for environment modification
        safe_print(f"{prefix} ⏳ Waiting for lock...")
        timings['wait_start'] = time.perf_counter()
        
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            safe_print(f"{prefix} 🔒 LOCK ACQUIRED")
            
            # STEP 1: Swap context using SUBPROCESS with correct Python
            safe_print(f"{prefix} 🔄 Swapping to Python {py_version}")
            swap_code, swap_time = run_omnipkg_cli(
                python_exe,
                ['swap', 'python', py_version],
                thread_id
            )
            if swap_code != 0:
                raise RuntimeError(f"Swap failed")
            
            # STEP 2: Install using SUBPROCESS with correct Python
            safe_print(f"{prefix} 📦 Installing rich=={rich_version}")
            install_code, install_time = run_omnipkg_cli(
                python_exe,
                ['install', f'rich=={rich_version}'],
                thread_id
            )
            if install_code != 0:
                raise RuntimeError(f"Install failed")
            
            timings['lock_released'] = time.perf_counter()
            safe_print(f"{prefix} 🔓 LOCK RELEASED")
        
        # STEP 3: Test the installation
        safe_print(f"{prefix} 🧪 Testing Rich import...")
        timings['test_start'] = time.perf_counter()
        
        test_script = f"""
import json
import importlib.metadata
with open('/dev/null', 'w'):  # Suppress omnipkg init messages
    from omnipkg.loader import omnipkgLoader
    from omnipkg.core import ConfigManager
    config = ConfigManager(suppress_init_messages=True).config
    
with omnipkgLoader("rich=={rich_version}", config=config):
    import rich
    version = importlib.metadata.version('rich')
    print(json.dumps({{"success": True, "version": version}}))
"""
        
        result = subprocess.run(
            [python_exe, '-c', test_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            safe_print(f"{prefix} ❌ Test failed: {result.stderr[:200]}")
            raise RuntimeError("Test execution failed")
        
        test_data = json.loads(result.stdout.strip())
        timings['end'] = time.perf_counter()
        
        safe_print(f"{prefix} ✅ Test passed! Rich {test_data['version']}")
        
        return {
            'thread_id': thread_id,
            'python_version': py_version,
            'rich_version': test_data['version'],
            'swap_time_ms': swap_time,
            'install_time_ms': install_time,
            'total_time_ms': (timings['end'] - timings['start']) * 1000
        }
        
    except Exception as e:
        safe_print(f"{prefix} ❌ FAILED: {e}")
        return None

def main():
    """Main test orchestrator."""
    safe_print("=" * 80)
    safe_print("🚀 CONCURRENT RICH MULTIVERSE TEST")
    safe_print("=" * 80)
    
    test_configs = [
        ('3.9', '13.4.2'),
        ('3.10', '13.6.0'),
        ('3.11', '13.7.1')
    ]
    
    # Phase 1: Adopt all interpreters sequentially (safer)
    safe_print("\n📥 Phase 1: Adopting interpreters...")
    for version, _ in test_configs:
        if not adopt_if_needed(version, 0):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)
    
    safe_print("\n✅ All interpreters ready. Starting concurrent tests...")
    
    # Phase 2: Run tests concurrently
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(test_dimension, config, i+1): config
            for i, config in enumerate(test_configs)
        }
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    
    # Report
    safe_print("\n" + "=" * 80)
    safe_print("📊 RESULTS")
    safe_print("=" * 80)
    for r in sorted(results, key=lambda x: x['thread_id']):
        safe_print(f"Thread {r['thread_id']}: Python {r['python_version']} | "
                  f"Rich {r['rich_version']} | Total: {r['total_time_ms']:.0f}ms")
    
    success = len(results) == len(test_configs)
    safe_print("\n" + ("🎉 ALL TESTS PASSED!" if success else "❌ SOME TESTS FAILED"))
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()