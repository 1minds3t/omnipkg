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
    """Runs a non-install omnipkg command and captures output."""
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
    safe_print(f"{prefix} {status} {' '.join(args[:2])} ({format_duration(duration_ms)})")
    
    if result.returncode != 0:
        safe_print(f"{prefix} ❌ COMMAND FAILED: {' '.join(cmd)}")
        if result.stdout:
            safe_print(f"{prefix} STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            safe_print(f"{prefix} STDERR:\n{result.stderr.strip()}")
    
    return result.returncode, result.stdout, result.stderr, duration_ms

def run_and_stream_install(python_exe: str, args: list, thread_id: int) -> tuple[int, float]:
    """Runs `omnipkg install` and streams its output live."""
    prefix = f"[T{thread_id}]"
    install_prefix = f"  {prefix}|install"
    safe_print(f"{prefix} 📦 Installing {' '.join(args[1:])} (Live Output Below)")
    start_time = time.perf_counter()
    
    cmd = [python_exe, '-m', 'omnipkg.cli'] + args
    
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True, 
        encoding='utf-8', 
        errors='replace'
    )

    if process.stdout:
        for line in iter(process.stdout.readline, ''):
            safe_print(f"{install_prefix} | {line.strip()}")
    
    returncode = process.wait()
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    status = "✅" if returncode == 0 else "❌"
    safe_print(f"{prefix} {status} Install finished in {format_duration(duration_ms)} with code {returncode}")
    
    return returncode, duration_ms

def verify_registry_contains(version: str) -> bool:
    """Verify registry contains the version."""
    try:
        result = subprocess.run(
            ['omnipkg', 'info', 'python'],
            capture_output=True, text=True, check=True
        )
        for line in result.stdout.splitlines():
            if f'Python {version}:' in line:
                return True
    except subprocess.CalledProcessError:
        return False
    return False

def get_interpreter_path(version: str) -> str:
    """Get interpreter path from omnipkg."""
    result = subprocess.run(
        ['omnipkg', 'info', 'python'],
        capture_output=True, text=True, check=True
    )
    for line in result.stdout.splitlines():
        if f'Python {version}:' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                path_part = parts[1].strip().split()[0]
                return path_part
    raise RuntimeError(f"Python {version} not found in registry")

def adopt_if_needed(version: str, thread_id: int) -> bool:
    """Adopt Python version if not already present, with locking."""
    prefix = f"[T{thread_id}|Adopt]"
    if verify_registry_contains(version):
        safe_print(f"{prefix} ✅ Python {version} already available.")
        return True
    
    with omnipkg_lock:
        if verify_registry_contains(version):
            safe_print(f"{prefix} ✅ Python {version} adopted by another thread.")
            return True
        
        safe_print(f"{prefix} 🚀 Adopting Python {version}...")
        result = subprocess.run(
            ['omnipkg', 'python', 'adopt', version],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            safe_print(f"{prefix} ❌ Adoption failed")
            safe_print(f"{prefix} STDOUT: {result.stdout}")
            safe_print(f"{prefix} STDERR: {result.stderr}")
            return False
        
        safe_print(f"{prefix} ✅ Adopted and verified Python {version}")
        return True

def test_dimension(config: tuple, thread_id: int) -> dict:
    """Test one Python+Rich combination."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings = {
        'start': time.perf_counter(), 'wait_start': 0, 'lock_acquired': 0,
        'lock_released': 0, 'test_start': 0, 'end': 0
    }
    
    try:
        safe_print(f"{prefix} 🚀 Testing Python {py_version} with Rich {rich_version}")
        
        python_exe = get_interpreter_path(py_version)
        safe_print(f"{prefix} 📍 Using: {python_exe}")
        
        safe_print(f"{prefix} ⏳ Waiting for lock...")
        timings['wait_start'] = time.perf_counter()
        
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            safe_print(f"{prefix} 🔒 LOCK ACQUIRED")
            
            safe_print(f"{prefix} 🔄 Swapping to Python {py_version}")
            swap_code, _, swap_stderr, swap_time = run_omnipkg_cli(
                sys.executable, ['swap', 'python', py_version], thread_id
            )
            if swap_code != 0:
                raise RuntimeError(f"Swap failed with exit code {swap_code}: {swap_stderr}")
            
            install_code, install_time = run_and_stream_install(
                python_exe, ['install', f'rich=={rich_version}'], thread_id
            )
            if install_code != 0:
                raise RuntimeError(f"omnipkg install failed with exit code {install_code}")
            
            safe_print(f"{prefix} 🔓 LOCK RELEASED")
            timings['lock_released'] = time.perf_counter()
        
        safe_print(f"{prefix} 🧪 Testing Rich import...")
        timings['test_start'] = time.perf_counter()
        
        # --- THE FIX: Define variables BEFORE the try block ---
        test_script = f"""
import sys, json, traceback
from pathlib import Path

# --- FIX: Define these variables BEFORE try block so they're always available ---
bubble_path_str = "not determined"
main_env_rich_version = "not-installed"

try:
    from omnipkg.core import ConfigManager
    from omnipkg.loader import omnipkgLoader
    
    config_manager = ConfigManager(suppress_init_messages=True)
    
    # Check what's in the main environment for debugging
    try:
        import importlib.metadata
        main_env_rich_version = importlib.metadata.version('rich')
    except importlib.metadata.PackageNotFoundError:
        pass

    # Define the bubble path for the error message
    bubble_path_str = str(Path(config_manager.config['multiversion_base']) / 'rich-{rich_version}')

    with omnipkgLoader("rich=={rich_version}", config=config_manager.config):
        import rich, importlib.metadata
        
        rich_version_actual = importlib.metadata.version('rich')
        if rich_version_actual != "{rich_version}":
            raise RuntimeError(f"Version mismatch: expected {rich_version}, but loaded {{rich_version_actual}}")
        
        result = {{
            "success": True, "python_version": sys.version.split()[0],
            "python_path": sys.executable, "rich_version": rich_version_actual,
            "rich_file": rich.__file__
        }}
        print("JSON_START\\n" + json.dumps(result) + "\\nJSON_END")
        
except Exception as e:
    error_message = str(e)
    if "not available" in error_message: # Auto-enhance error
        error_message = (f"Package rich=={rich_version} not available\\n"
                         f"  Bubble not found: {{bubble_path_str}}\\n"
                         f"  Main env has: {{main_env_rich_version}}\\n"
                         f"  Hint: Try 'omnipkg install rich=={rich_version}'")
    
    error_result = {{"success": False, "error": error_message, "traceback": traceback.format_exc()}}
    print("JSON_START\\n" + json.dumps(error_result) + "\\nJSON_END", file=sys.stderr)
    sys.exit(1)
"""
        
        cmd = [python_exe, '-c', test_script]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        json_output = None
        output_source = result.stdout if result.returncode == 0 else result.stderr
        
        if "JSON_START" in output_source and "JSON_END" in output_source:
            json_output = output_source.split("JSON_START")[1].split("JSON_END")[0].strip()
        
        if not json_output:
            raise RuntimeError(f"Test failed to produce JSON. STDOUT: {result.stdout} STDERR: {result.stderr}")
        
        test_data = json.loads(json_output)
        
        if not test_data.get('success'):
            raise RuntimeError(test_data.get('error'))
        
        timings['end'] = time.perf_counter()
        
        safe_print(f"{prefix} ✅ VERIFIED:")
        safe_print(f"{prefix}    Python: {test_data['python_version']} ({test_data['python_path']})")
        safe_print(f"{prefix}    Rich: {test_data['rich_version']} (from {test_data['rich_file']})")
        
        return {
            'thread_id': thread_id, 'python_version': test_data['python_version'],
            'python_path': test_data['python_path'], 'rich_version': test_data['rich_version'],
            'rich_file': test_data['rich_file'],
            'timings_ms': {'wait': (timings['lock_acquired'] - timings['wait_start']) * 1000,
                           'swap': swap_time, 'install': install_time,
                           'test': (timings['end'] - timings['test_start']) * 1000,
                           'total': (timings['end'] - timings['start']) * 1000}
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
    
    safe_print(f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Wait':<8} {'Swap':<8} {'Install':<10} {'Test':<8} {'Total':<10}")
    safe_print("-" * 100)
    
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
    
    test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
    
    safe_print("\n📥 Phase 1: Adopting interpreters (sequential for safety)...")
    for version, _ in test_configs:
        if not adopt_if_needed(version, 0):
            safe_print(f"❌ Failed to adopt Python {version}")
            sys.exit(1)
    
    safe_print("\n✅ All interpreters ready. Starting concurrent tests...\n")
    
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
    
    total_time = (time.perf_counter() - start_time) * 1000
    
    print_summary(results, total_time)
    
    success = len(results) == len(test_configs)
    safe_print("\n" + ("🎉 ALL TESTS PASSED!" if success else "❌ SOME TESTS FAILED"))
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()