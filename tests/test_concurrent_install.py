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

def get_interpreter_path(version: str) -> str:
    """Get interpreter path from omnipkg (fast, no subprocess)."""
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

def verify_registry_contains(version: str) -> bool:
    """Fast check if version exists in registry."""
    try:
        result = subprocess.run(
            ['omnipkg', 'info', 'python'],
            capture_output=True, text=True, check=True
        )
        return f'Python {version}:' in result.stdout
    except subprocess.CalledProcessError:
        return False

def check_bubble_exists_fast(python_exe: str, package: str, version: str) -> tuple[bool, float]:
    """Ultra-fast bubble existence check without imports."""
    start = time.perf_counter()
    
    # Direct filesystem check - much faster than subprocess
    check_script = f"""
from pathlib import Path
from omnipkg.core import ConfigManager
import sys

config = ConfigManager(suppress_init_messages=True)
bubble_path = Path(config.config['multiversion_base']) / '{package}-{version}'
sys.exit(0 if bubble_path.exists() else 1)
"""
    
    result = subprocess.run(
        [python_exe, '-c', check_script],
        capture_output=True,
        timeout=5
    )
    
    duration = (time.perf_counter() - start) * 1000
    return result.returncode == 0, duration

def adopt_interpreter(version: str) -> bool:
    """Adopt Python version if not already present."""
    prefix = "[PREP]"
    if verify_registry_contains(version):
        safe_print(f"{prefix} ✅ Python {version} already available.")
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
    
    safe_print(f"{prefix} ✅ Adopted Python {version}")
    return True

def install_package_safe(python_exe: str, package_spec: str, version: str) -> tuple[bool, float]:
    """Install a package with proper locking for Windows CI safety."""
    prefix = "[PREP]"
    package_name, package_version = package_spec.split('==')
    
    # Check if already exists (fast check)
    exists, check_time = check_bubble_exists_fast(python_exe, package_name, package_version)
    if exists:
        safe_print(f"{prefix} ⚡ {package_spec} bubble already exists ({format_duration(check_time)})")
        return True, check_time
    
    safe_print(f"{prefix} 📦 Installing {package_spec} for Python {version}...")
    
    start = time.perf_counter()
    
    # CRITICAL: Lock during install for Windows CI safety
    with omnipkg_lock:
        # Swap context to target Python
        swap_result = subprocess.run(
            [sys.executable, '-m', 'omnipkg.cli', 'swap', 'python', version],
            capture_output=True, text=True
        )
        
        if swap_result.returncode != 0:
            safe_print(f"{prefix} ❌ Failed to swap to Python {version}")
            return False, 0
        
        # Install package
        cmd = [python_exe, '-m', 'omnipkg.cli', 'install', package_spec]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            safe_print(f"{prefix} ❌ Install failed: {result.stderr}")
            return False, 0
    
    duration = (time.perf_counter() - start) * 1000
    safe_print(f"{prefix} ✅ Installed {package_spec} ({format_duration(duration)})")
    return True, duration

def prepare_environment(test_configs: list) -> dict:
    """Phase 1: Prepare all interpreters and packages sequentially (Windows-safe)."""
    safe_print("\n" + "=" * 100)
    safe_print("📋 PHASE 1: ENVIRONMENT PREPARATION (Sequential for Safety)")
    safe_print("=" * 100)
    
    prep_start = time.perf_counter()
    prep_timings = {'adopt': [], 'check': [], 'install': []}
    interpreter_paths = {}
    
    # Step 1: Adopt all interpreters
    safe_print("\n🔧 Step 1.1: Adopting Python Interpreters...")
    for version, _ in test_configs:
        adopt_start = time.perf_counter()
        if not adopt_interpreter(version):
            raise RuntimeError(f"Failed to adopt Python {version}")
        prep_timings['adopt'].append((time.perf_counter() - adopt_start) * 1000)
        
        # Get and cache interpreter path
        interpreter_paths[version] = get_interpreter_path(version)
        safe_print(f"[PREP] 📍 Python {version}: {interpreter_paths[version]}")
    
    # Step 2: Install all packages with locking
    safe_print("\n🔧 Step 1.2: Installing Packages (Thread-Safe)...")
    for py_version, rich_version in test_configs:
        python_exe = interpreter_paths[py_version]
        package_spec = f'rich=={rich_version}'
        
        success, duration = install_package_safe(python_exe, package_spec, py_version)
        if not success:
            raise RuntimeError(f"Failed to install {package_spec}")
        
        if duration < 100:  # Was cached
            prep_timings['check'].append(duration)
        else:  # Was installed
            prep_timings['install'].append(duration)
    
    prep_total = (time.perf_counter() - prep_start) * 1000
    
    safe_print("\n" + "-" * 100)
    safe_print("📊 Preparation Summary:")
    if prep_timings['adopt']:
        safe_print(f"  Adoptions:     {len(prep_timings['adopt'])} x ~{sum(prep_timings['adopt'])/len(prep_timings['adopt']):.1f}ms = {sum(prep_timings['adopt']):.1f}ms total")
    if prep_timings['check']:
        safe_print(f"  Cache Checks:  {len(prep_timings['check'])} x ~{sum(prep_timings['check'])/len(prep_timings['check']):.1f}ms = {sum(prep_timings['check']):.1f}ms total")
    if prep_timings['install']:
        safe_print(f"  Installs:      {len(prep_timings['install'])} x ~{sum(prep_timings['install'])/len(prep_timings['install']):.1f}ms = {sum(prep_timings['install']):.1f}ms total")
    safe_print(f"  Total Prep:    {format_duration(prep_total)}")
    safe_print("=" * 100)
    
    return {
        'interpreter_paths': interpreter_paths,
        'prep_time': prep_total,
        'prep_timings': prep_timings
    }

def run_lockless_test(config: tuple, thread_id: int, prep_data: dict) -> dict:
    """Phase 2: Ultra-fast concurrent testing WITHOUT locks (safe because env is pre-prepared)."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings = {'start': time.perf_counter(), 'check_start': 0, 'test_start': 0, 'end': 0}
    
    try:
        safe_print(f"{prefix} 🚀 Testing Python {py_version} with Rich {rich_version}")
        
        python_exe = prep_data['interpreter_paths'][py_version]
        
        # Quick verify bubble exists (no lock needed - read-only)
        timings['check_start'] = time.perf_counter()
        exists, check_time = check_bubble_exists_fast(python_exe, 'rich', rich_version)
        if not exists:
            raise RuntimeError(f"Bubble for rich=={rich_version} not found after prep!")
        safe_print(f"{prefix} ⚡ Bubble verified in {format_duration(check_time)}")
        
        # Run test using omnipkgLoader (NO SWAPPING, NO LOCKING)
        safe_print(f"{prefix} 🧪 Testing with omnipkgLoader...")
        timings['test_start'] = time.perf_counter()
        
        # KEY: Use omnipkgLoader which doesn't modify global state
        test_script = f"""
import sys, json, traceback
from omnipkg.core import ConfigManager
from omnipkg.loader import omnipkgLoader

try:
    config_manager = ConfigManager(suppress_init_messages=True)
    
    # omnipkgLoader is thread-safe - it only modifies sys.path locally
    with omnipkgLoader("rich=={rich_version}", config=config_manager.config):
        import rich
        import importlib.metadata
        
        rich_version_actual = importlib.metadata.version('rich')
        if rich_version_actual != "{rich_version}":
            raise RuntimeError(f"Version mismatch: expected {rich_version}, got {{rich_version_actual}}")
        
        result = {{
            "success": True,
            "python_version": sys.version.split()[0],
            "python_path": sys.executable,
            "rich_version": rich_version_actual,
            "rich_file": rich.__file__
        }}
        print("JSON_START\\n" + json.dumps(result) + "\\nJSON_END")
        
except Exception as e:
    error_result = {{
        "success": False,
        "error": str(e),
        "traceback": traceback.format_exc()
    }}
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
            raise RuntimeError(f"No JSON output. STDOUT: {result.stdout} STDERR: {result.stderr}")
        
        test_data = json.loads(json_output)
        
        if not test_data.get('success'):
            raise RuntimeError(test_data.get('error'))
        
        timings['end'] = time.perf_counter()
        
        safe_print(f"{prefix} ✅ VERIFIED: Python {test_data['python_version']}, Rich {test_data['rich_version']}")
        
        return {
            'thread_id': thread_id,
            'python_version': test_data['python_version'],
            'python_path': test_data['python_path'],
            'rich_version': test_data['rich_version'],
            'rich_file': test_data['rich_file'],
            'timings_ms': {
                'check': check_time,
                'test': (timings['end'] - timings['test_start']) * 1000,
                'total': (timings['end'] - timings['start']) * 1000
            }
        }
        
    except Exception as e:
        safe_print(f"{prefix} ❌ FAILED: {e}")
        import traceback
        safe_print(f"{prefix} {traceback.format_exc()}")
        return None

def print_summary(results: list, prep_time: float, test_time: float):
    """Print detailed summary table."""
    safe_print("\n" + "=" * 100)
    safe_print("📊 DETAILED RESULTS")
    safe_print("=" * 100)
    
    safe_print(f"{'Thread':<8} {'Python':<12} {'Rich':<10} {'Check':<10} {'Test':<10} {'Total':<10}")
    safe_print("-" * 100)
    
    for r in sorted(results, key=lambda x: x['thread_id']):
        t = r['timings_ms']
        safe_print(
            f"T{r['thread_id']:<7} "
            f"{r['python_version']:<12} "
            f"{r['rich_version']:<10} "
            f"{format_duration(t['check']):<10} "
            f"{format_duration(t['test']):<10} "
            f"{format_duration(t['total']):<10}"
        )
    
    safe_print("-" * 100)
    safe_print(f"⏱️  Phase 1 (Prep):       {format_duration(prep_time)}")
    safe_print(f"⏱️  Phase 2 (Testing):    {format_duration(test_time)}")
    safe_print(f"⏱️  Total:                {format_duration(prep_time + test_time)}")
    
    # Show concurrent efficiency
    sequential_test_time = sum(r['timings_ms']['total'] for r in results)
    speedup = sequential_test_time / test_time if test_time > 0 else 1.0
    safe_print(f"⚡  Test Phase Speedup:   {speedup:.2f}x (vs sequential)")
    safe_print("=" * 100)
    
    safe_print("\n🔍 VERIFICATION:")
    safe_print("-" * 100)
    for r in sorted(results, key=lambda x: x['thread_id']):
        safe_print(f"T{r['thread_id']}: {r['python_path']}")
        safe_print(f"     └─ Rich {r['rich_version']} from: {r['rich_file']}")
    safe_print("-" * 100)

def main():
    """Main test orchestrator: Safe prep + Fast concurrent execution."""
    total_start = time.perf_counter()
    
    safe_print("=" * 100)
    safe_print("🚀 HYBRID CONCURRENT TEST: Safe Prep + Fast Execution")
    safe_print("=" * 100)
    
    test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
    
    # Phase 1: Sequential preparation with locking (Windows-safe)
    try:
        prep_data = prepare_environment(test_configs)
    except Exception as e:
        safe_print(f"\n❌ Preparation failed: {e}")
        sys.exit(1)
    
    # Phase 2: Concurrent testing WITHOUT locks (safe because env is ready)
    safe_print("\n" + "=" * 100)
    safe_print("🧪 PHASE 2: LOCK-FREE CONCURRENT TESTING")
    safe_print("   (Safe because packages are pre-installed and omnipkgLoader is thread-safe)")
    safe_print("=" * 100)
    
    test_start = time.perf_counter()
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_lockless_test, config, i+1, prep_data): config
            for i, config in enumerate(test_configs)
        }
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    
    test_time = (time.perf_counter() - test_start) * 1000
    
    print_summary(results, prep_data['prep_time'], test_time)
    
    success = len(results) == len(test_configs)
    safe_print("\n" + ("🎉 ALL TESTS PASSED!" if success else "❌ SOME TESTS FAILED"))
    
    total_time = (time.perf_counter() - total_start) * 1000
    safe_print(f"\n✨ Grand Total: {format_duration(total_time)}")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
