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

def format_duration(ms: float) -> str:
    """Format duration for readability."""
    if ms < 1:
        return f"{ms*1000:.1f}µs"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"

def run_omnipkg_cli(python_exe: str, args: list, thread_id: int) -> tuple[int, str, str, float]:
    """Run omnipkg CLI and ALWAYS return stdout/stderr."""
    prefix = f"[T{thread_id}]"
    start = time.perf_counter()
    
    cmd = [python_exe, '-m', 'omnipkg.cli'] + args
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    
    duration_ms = (time.perf_counter() - start) * 1000
    
    status = "✅" if result.returncode == 0 else "❌"
    safe_print(f"{prefix} {status} {' '.join(args[:3])} ({format_duration(duration_ms)})")
    
    # ALWAYS show output on failure
    if result.returncode != 0:
        safe_print(f"{prefix} ❌ COMMAND FAILED: {' '.join(cmd)}")
        if result.stdout:
            safe_print(f"{prefix} STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            safe_print(f"{prefix} STDERR:\n{result.stderr.strip()}")
    
    return result.returncode, result.stdout, result.stderr, duration_ms  # Return stderr too!


def verify_registry_contains(version: str, max_attempts: int = 5, delay: float = 0.5) -> bool:
    """Verify registry contains the version WITH DEBUGGING."""
    for attempt in range(max_attempts):
        try:
            result = subprocess.run(
                ['omnipkg', 'info', 'python'],
                capture_output=True,
                text=True,
                check=True
            )
            
            # DEBUG: Show what's actually in the registry
            if attempt == 0:
                safe_print(f"[DEBUG] Registry contents:")
                for line in result.stdout.splitlines():
                    if 'Python' in line:
                        safe_print(f"[DEBUG]   {line}")
            
            for line in result.stdout.splitlines():
                if f'Python {version}:' in line:
                    safe_print(f"[DEBUG] ✅ Found Python {version} in registry")
                    return True
            
            safe_print(f"[DEBUG] ❌ Attempt {attempt+1}/{max_attempts}: Python {version} NOT in registry")
            
            if attempt < max_attempts - 1:
                time.sleep(delay)
        except subprocess.CalledProcessError as e:
            safe_print(f"[DEBUG] ❌ Registry check failed: {e}")
            if attempt < max_attempts - 1:
                time.sleep(delay)
    
    return False

def get_interpreter_path(version: str) -> str:
    """Get interpreter path from omnipkg."""
    result = subprocess.run(
        ['omnipkg', 'info', 'python'],
        capture_output=True,
        text=True,
        check=True
    )
    
    for line in result.stdout.splitlines():
        if f'Python {version}:' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                path_part = parts[1].strip().split()[0]
                return path_part
    
    raise RuntimeError(f"Python {version} not found")

def adopt_if_needed(version: str, thread_id: int) -> bool:
    """
    Adopt Python version if not already present.
    CRITICAL: Use a lock and verify registry after adoption to prevent race conditions.
    """
    prefix = f"[T{thread_id}|Adopt]"
    
    # Check if already exists (without lock, fast path)
    try:
        path = get_interpreter_path(version)
        safe_print(f"{prefix} ✅ Python {version} already available at {path}")
        return True
    except RuntimeError:
        pass
    
    # Need to adopt - use lock to prevent concurrent adoptions from interfering
    with omnipkg_lock:
        # Double-check after acquiring lock (another thread might have adopted it)
        try:
            path = get_interpreter_path(version)
            safe_print(f"{prefix} ✅ Python {version} at {path} (adopted by another thread)")
            return True
        except RuntimeError:
            pass
        
        # Actually perform adoption
        safe_print(f"{prefix} 🚀 Adopting Python {version}...")
        result = subprocess.run(
            ['omnipkg', 'python', 'adopt', version],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            safe_print(f"{prefix} ❌ Adoption failed")
            safe_print(f"{prefix} STDOUT: {result.stdout}")
            safe_print(f"{prefix} STDERR: {result.stderr}")
            return False
        
        # CRITICAL: Verify the adoption actually completed and registry was updated
        safe_print(f"{prefix} 🔍 Verifying registry contains Python {version}...")
        if not verify_registry_contains(version, max_attempts=10, delay=0.5):
            safe_print(f"{prefix} ❌ Adoption succeeded but interpreter not in registry after 5 seconds")
            safe_print(f"{prefix} 🔄 Attempting manual registry refresh...")
            
            # Try to manually trigger a registry refresh
            subprocess.run(
                ['omnipkg', 'info', 'python'],
                capture_output=True,
                text=True
            )
            
            # Check one more time
            if not verify_registry_contains(version, max_attempts=2, delay=0.5):
                safe_print(f"{prefix} ❌ Registry verification failed")
                return False
        
        safe_print(f"{prefix} ✅ Adopted and verified Python {version}")
        return True

def test_dimension(config: tuple, thread_id: int, skip_swap: bool = False) -> dict:
    """Test one Python+Rich combination."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings = {
        'start': time.perf_counter(),
        'wait_start': 0,
        'lock_acquired': 0,
        'swap_end': 0,
        'install_end': 0,
        'lock_released': 0,
        'test_start': 0,
        'end': 0
    }
    
    try:
        safe_print(f"{prefix} 🚀 Testing Python {py_version} with Rich {rich_version}")
        
        # Get interpreter path
        python_exe = get_interpreter_path(py_version)
        safe_print(f"{prefix} 📍 Using: {python_exe}")
        
        # Lock for environment modification
        safe_print(f"{prefix} ⏳ Waiting for lock...")
        timings['wait_start'] = time.perf_counter()
        
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            safe_print(f"{prefix} 🔒 LOCK ACQUIRED")
            
            # STEP 1: Swap context (WITH FULL ERROR INFO)
            swap_time = 0
            if not skip_swap:
                safe_print(f"{prefix} 🔄 Swapping to Python {py_version}")
                swap_code, swap_stdout, swap_stderr, swap_time = run_omnipkg_cli(
                    sys.executable,
                    ['swap', 'python', py_version],
                    thread_id
                )
                if swap_code != 0:
                    safe_print(f"{prefix} ❌ SWAP FAILED - Full diagnostics:")
                    safe_print(f"{prefix}    Command: {sys.executable} -m omnipkg.cli swap python {py_version}")
                    safe_print(f"{prefix}    Exit code: {swap_code}")
                    safe_print(f"{prefix}    STDOUT: {swap_stdout}")
                    safe_print(f"{prefix}    STDERR: {swap_stderr}")
                    raise RuntimeError(f"Swap failed with exit code {swap_code}: {swap_stderr}")
            
            timings['swap_end'] = time.perf_counter()
            
            # STEP 2: Install (WITH FULL ERROR INFO)
            safe_print(f"{prefix} 📦 Installing rich=={rich_version}")
            install_code, install_stdout, install_stderr, install_time = run_omnipkg_cli(
                python_exe,
                ['install', f'rich=={rich_version}'],
                thread_id
            )
            if install_code != 0:
                safe_print(f"{prefix} ❌ INSTALL FAILED:")
                safe_print(f"{prefix}    Command: {python_exe} -m omnipkg.cli install rich=={rich_version}")
                safe_print(f"{prefix}    Exit code: {install_code}")
                safe_print(f"{prefix}    STDOUT: {install_stdout}")
                safe_print(f"{prefix}    STDERR: {install_stderr}")
                raise RuntimeError(f"Install failed with exit code {install_code}: {install_stderr}")
            
            safe_print(f"{prefix} 🔓 LOCK RELEASED")
            timings['lock_released'] = time.perf_counter()
        
        # --- THIS BLOCK IS NOW CORRECTLY INDENTED (OUTSIDE THE 'with' BLOCK) ---
        # STEP 3: Test import with explicit version proof
        safe_print(f"{prefix} 🧪 Testing Rich import...")
        timings['test_start'] = time.perf_counter()
        
        test_script = f"""
import sys
import json
import traceback

try:
    python_path = sys.executable
    python_version = sys.version.split()[0]
    
    from omnipkg.core import ConfigManager
    config_manager = ConfigManager(suppress_init_messages=True)
    
    from omnipkg.loader import omnipkgLoader
    
    with omnipkgLoader("rich=={rich_version}", config=config_manager.config):
        import rich
        import importlib.metadata
        
        rich_version_actual = importlib.metadata.version('rich')
        rich_file = rich.__file__
        
        # ✅ ACCEPT BOTH: Verify version matches, regardless of bubble location
        if rich_version_actual != "{rich_version}":
            raise RuntimeError(f"Version mismatch: expected {rich_version}, got {{rich_version_actual}}")
        
        result = {{
            "success": True,
            "python_version": python_version,
            "python_path": python_path,
            "rich_version": rich_version_actual,
            "rich_file": rich_file
        }}
        print("JSON_START")
        print(json.dumps(result))
        print("JSON_END")
        
except Exception as e:
    error_result = {{
        "success": False,
        "error": str(e),
        "traceback": traceback.format_exc(),
        "python_version": sys.version.split()[0],
        "python_path": sys.executable
    }}
    print("JSON_START", file=sys.stderr)
    print(json.dumps(error_result), file=sys.stderr)
    print("JSON_END", file=sys.stderr)
    sys.exit(1)
"""
        
        cmd = [python_exe, '-c', test_script]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Extract JSON
        json_output = None
        output_source = result.stdout if result.returncode == 0 else result.stderr
        
        if "JSON_START" in output_source and "JSON_END" in output_source:
            json_section = output_source.split("JSON_START")[1].split("JSON_END")[0].strip()
            json_output = json_section
        
        if not json_output:
            safe_print(f"{prefix} ❌ No JSON output")
            safe_print(f"{prefix} STDOUT: {result.stdout}")
            safe_print(f"{prefix} STDERR: {result.stderr}")
            raise RuntimeError("Test failed to produce JSON")
        
        test_data = json.loads(json_output)
        
        if not test_data.get('success'):
            safe_print(f"{prefix} ❌ Test reported failure: {test_data.get('error')}")
            raise RuntimeError(test_data.get('error'))
        
        timings['end'] = time.perf_counter()
        
        # Show proof
        safe_print(f"{prefix} ✅ VERIFIED:")
        safe_print(f"{prefix}    Python: {test_data['python_version']} ({test_data['python_path']})")
        safe_print(f"{prefix}    Rich: {test_data['rich_version']} (from {test_data['rich_file']})")
        
        return {
            'thread_id': thread_id,
            'python_version': test_data['python_version'],
            'python_path': test_data['python_path'],
            'rich_version': test_data['rich_version'],
            'rich_file': test_data['rich_file'],
            'timings_ms': {
                'wait': (timings['lock_acquired'] - timings['wait_start']) * 1000,
                'swap': swap_time,
                'install': install_time,
                'test': (timings['end'] - timings['test_start']) * 1000,
                'total': (timings['end'] - timings['start']) * 1000
            }
        }
        
    except Exception as e:
        safe_print(f"{prefix} ❌ FAILED: {e}")
        import traceback
        safe_print(f"{prefix} {traceback.format_exc()}")
        return None

def print_summary(results: list, total_time: float):
    """Print detailed summary table."""
    safe_print("\n" + "=" * 100)
    safe_print("📊 DETAILED RESULTS")
    safe_print("=" * 100)
    
    # Header
    safe_print(f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Wait':<8} {'Swap':<8} {'Install':<10} {'Test':<8} {'Total':<10}")
    safe_print("-" * 100)
    
    # Sort by thread ID
    for r in sorted(results, key=lambda x: x['thread_id']):
        t = r['timings_ms']
        safe_print(
            f"T{r['thread_id']:<7} "
            f"{r['python_version']:<12} "
            f"{r['rich_version']:<10} "
            f"{format_duration(t['wait']):<8} "
            f"{format_duration(t['swap']):<8} "
            f"{format_duration(t['install']):<10} "
            f"{format_duration(t['test']):<8} "
            f"{format_duration(t['total']):<10}"
        )
    
    safe_print("-" * 100)
    safe_print(f"⏱️  Total concurrent runtime: {format_duration(total_time)}")
    safe_print("=" * 100)
    
    # Verification table
    safe_print("\n🔍 VERIFICATION - Actual Python Executables Used:")
    safe_print("-" * 100)
    for r in sorted(results, key=lambda x: x['thread_id']):
        safe_print(f"T{r['thread_id']}: {r['python_path']}")
        safe_print(f"     └─ Rich loaded from: {r['rich_file']}")
    safe_print("-" * 100)

def main():
    """Main test orchestrator."""
    start_time = time.perf_counter()
    
    safe_print("=" * 100)
    safe_print("🚀 CONCURRENT RICH MULTIVERSE TEST")
    safe_print("=" * 100)
    
    test_configs = [
        ('3.9', '13.4.2'),
        ('3.10', '13.6.0'),
        ('3.11', '13.7.1')
    ]
    
    # Phase 1: Adopt all interpreters SEQUENTIALLY with verification
    # This prevents registry corruption from concurrent adoptions
    safe_print("\n📥 Phase 1: Adopting interpreters (sequential for safety)...")
    for version, _ in test_configs:
        if not adopt_if_needed(version, 0):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)
    
    safe_print("\n✅ All interpreters ready. Starting concurrent tests...\n")
    
    # Phase 2: Run tests concurrently
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(test_dimension, config, i+1, skip_swap=False): config
            for i, config in enumerate(test_configs)
        }
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    
    total_time = (time.perf_counter() - start_time) * 1000
    
    # Print summary
    print_summary(results, total_time)
    
    success = len(results) == len(test_configs)
    safe_print("\n" + ("🎉 ALL TESTS PASSED!" if success else "❌ SOME TESTS FAILED"))
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()