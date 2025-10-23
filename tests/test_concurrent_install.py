try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import os
import subprocess
import json
import re
from pathlib import Path
import time
import concurrent.futures
import threading
from omnipkg.i18n import _
from omnipkg.core import omnipkg, ConfigManager
from typing import Optional, List, Tuple, Dict, Any

try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.core import ConfigManager
except ImportError as e:
    safe_print(f'FATAL: Could not import omnipkg modules. Make sure this script is placed correctly. Error: {e}')
    sys.exit(1)

# --- Thread-safe utilities ---
print_lock = threading.Lock()
omnipkg_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    """Thread-safe wrapper around safe_print."""
    with print_lock:
        safe_print(*args, **kwargs)

def format_duration(duration_ms: float) -> str:
    """Format duration with appropriate units for clarity."""
    if duration_ms < 1:
        return f"{duration_ms * 1000:.1f}µs"
    if duration_ms < 1000:
        return f"{duration_ms:.1f}ms"
    return f"{duration_ms / 1000:.2f}s"

# --- Python Interpreter Management ---
def check_python_version_adopted(version: str, omnipkg_instance: omnipkg) -> bool:
    """Check if a specific Python version is adopted/managed."""
    python_exe_path = omnipkg_instance.config_manager.get_interpreter_for_version(version)
    return python_exe_path is not None and python_exe_path.exists()

def adopt_python_version(version: str) -> Tuple[bool, float]:
    """Adopt a Python version if not already managed."""
    safe_print(f'🐍 Adopting Python {version}...')
    start_time = time.perf_counter()
    
    cmd = ['omnipkg', 'python', 'adopt', version]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    if result.returncode != 0:
        safe_print(f'   ❌ Failed to adopt Python {version} (code {result.returncode})')
        safe_print(f'   STDOUT: {result.stdout}')
        safe_print(f'   STDERR: {result.stderr}')
        return False, duration_ms
    else:
        safe_print(f'   ✅ Python {version} adopted successfully in {format_duration(duration_ms)}')
        return True, duration_ms

def ensure_python_versions_available(test_configs: List[Tuple[str, str]], omnipkg_instance: omnipkg) -> bool:
    """Ensure all required Python versions are adopted before running tests."""
    safe_print('\n' + '=' * 80)
    safe_print('🔍 CHECKING PYTHON INTERPRETER AVAILABILITY')
    safe_print('=' * 80)
    
    required_versions = set(config[0] for config in test_configs)
    missing_versions = []
    
    # Check which versions are missing
    for version in required_versions:
        is_available = check_python_version_adopted(version, omnipkg_instance)
        if is_available:
            safe_print(f'   ✅ Python {version} is available')
        else:
            safe_print(f'   ❌ Python {version} is NOT available')
            missing_versions.append(version)
    
    # Adopt missing versions
    if missing_versions:
        safe_print(f'\n📦 Need to adopt {len(missing_versions)} Python version(s): {", ".join(missing_versions)}')
        safe_print('=' * 80)
        
        for version in missing_versions:
            success, duration = adopt_python_version(version)
            if not success:
                safe_print(f'\n❌ FATAL: Failed to adopt Python {version}. Cannot proceed with tests.')
                return False
        
        safe_print('\n✅ All required Python versions are now available!')
    else:
        safe_print('\n✅ All required Python versions are already available!')
    
    safe_print('=' * 80 + '\n')
    return True

# --- Core Test Functions ---
def test_rich_version():
    """This function is executed by the target Python interpreter to verify the rich version."""
    import rich
    import importlib.metadata
    import sys
    import json
    try:
        rich_version = rich.__version__
    except AttributeError:
        rich_version = importlib.metadata.version('rich')
    result = {'python_version': sys.version.split()[0], 'rich_version': rich_version, 'success': True}
    print(json.dumps(result))

def run_command_isolated(cmd_args: List[str], description: str, cmd_name: str, thread_id: int) -> Tuple[str, int, float]:
    """Runs a command and captures its output, returning timing info."""
    prefix = f"[T{thread_id}]"
    thread_safe_print(f'{prefix} ▶️  Executing: {description}')
    start_time = time.perf_counter()
    
    # Handle both Python interpreter calls and direct command calls (like 'omnipkg')
    if cmd_name.endswith(('python', 'python3', 'python3.9', 'python3.10', 'python3.11', 'python3.12')):
        cmd = [cmd_name, '-m', 'omnipkg.cli'] + cmd_args
    else:
        # Direct command call (e.g., 'omnipkg')
        cmd = [cmd_name] + cmd_args
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    if result.returncode != 0:
        thread_safe_print(f'{prefix}   ⚠️  WARNING: Command failed (code {result.returncode}) in {format_duration(duration_ms)}')
    else:
        thread_safe_print(f'{prefix}   ✅ Completed in {format_duration(duration_ms)}')
        
    return (result.stdout + result.stderr), result.returncode, duration_ms

def run_and_stream_install(cmd_args: List[str], description: str, cmd_name: str, thread_id: int) -> Tuple[int, float]:
    """
    Runs the install command and streams its output live for transparency.
    """
    prefix = f"[T{thread_id}]"
    install_prefix = f"[T{thread_id}|install]"
    thread_safe_print(f'{prefix} ▶️  Executing: {description} (Live Output Below)')
    start_time = time.perf_counter()
    
    # Handle both Python interpreter calls and direct command calls (like 'omnipkg')
    if cmd_name.endswith(('python', 'python3', 'python3.9', 'python3.10', 'python3.11', 'python3.12')):
        cmd = [cmd_name, '-m', 'omnipkg.cli'] + cmd_args
    else:
        # Direct command call (e.g., 'omnipkg')
        cmd = [cmd_name] + cmd_args
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                if line.strip():
                    thread_safe_print(f'{install_prefix} | {line.strip()}')
        process.wait()
        returncode = process.returncode
    except FileNotFoundError:
        thread_safe_print(f'{prefix} ❌ ERROR: Executable not found: {cmd_name}')
        return -1, 0

    duration_ms = (time.perf_counter() - start_time) * 1000
    
    if returncode != 0:
        thread_safe_print(f'{prefix}   ⚠️  WARNING: Install failed (code {returncode}) after {format_duration(duration_ms)}')
    else:
        thread_safe_print(f'{prefix}   ✅ Install completed in {format_duration(duration_ms)}')
        
    return returncode, duration_ms

def get_interpreter_path(version: str, thread_id: int) -> str:
    """Finds the path to a managed Python interpreter."""
    prefix = f"[T{thread_id}]"
    start_time = time.perf_counter()
    result = subprocess.run(['omnipkg', 'info', 'python'], capture_output=True, text=True, check=True)
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    for line in result.stdout.splitlines():
        if line.strip().startswith(f'• Python {version}'):
            match = re.search(r':\s*(/\S+)', line)
            if match:
                path = match.group(1).strip()
                thread_safe_print(f'{prefix} 📍 Located Python {version} at {path} ({format_duration(duration_ms)})')
                return path
    raise RuntimeError(f"Could not find managed Python {version}.")

def check_package_installed(python_exe: str, package: str, version: str) -> Tuple[bool, float]:
    """Checks if a package is already installed for a specific Python interpreter."""
    start_time = time.perf_counter()
    cmd = [python_exe, '-c', f"import importlib.metadata; exit(0) if importlib.metadata.version('{package}') == '{version}' else exit(1)"]
    result = subprocess.run(cmd, capture_output=True)
    duration_ms = (time.perf_counter() - start_time) * 1000
    return result.returncode == 0, duration_ms

def prepare_and_test_dimension(config: Tuple[str, str], omnipkg_instance: omnipkg, thread_id: int):
    """
    The main worker function for each thread.
    Uses subprocess calls to ensure packages are installed in the CORRECT Python version's context.
    """
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings: Dict[str, float] = {k: 0 for k in ['start', 'wait_lock_start', 'lock_acquired', 'swap_start', 'swap_end', 'install_start', 'install_end', 'lock_released', 'test_start', 'end']}
    timings['start'] = time.perf_counter()

    try:
        thread_safe_print(f'{prefix} 🚀 DIMENSION TEST: Python {py_version} with Rich {rich_version}')
        
        # === STEP 1: Get interpreter path ===
        python_exe_path = omnipkg_instance.config_manager.get_interpreter_for_version(py_version)

        if not python_exe_path:
            raise RuntimeError(f"Could not find interpreter for {py_version}")
        python_exe = str(python_exe_path)

        # === STEP 2: Check if package is installed (Direct check in target Python) ===
        is_installed, check_duration = check_package_installed(python_exe, 'rich', rich_version)
        
        # === STEP 3: Critical section (SUBPROCESS OPERATIONS) ===
        thread_safe_print(f'{prefix} ⏳ WAITING for lock...')
        timings['wait_lock_start'] = time.perf_counter()
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            thread_safe_print(f'{prefix} 🔒 LOCK ACQUIRED - Modifying shared environment')
            
            # --- SWAP CONTEXT (SUBPROCESS) ---
            timings['swap_start'] = time.perf_counter()
            swap_output, swap_returncode, swap_duration = run_command_isolated(
                ['swap', 'python', py_version],
                f"Swapping to Python {py_version}",
                'omnipkg',
                thread_id
            )
            timings['swap_end'] = time.perf_counter()
            if swap_returncode != 0:
                raise RuntimeError(f"Failed to swap to Python {py_version}")
            
            # --- INSTALL PACKAGE (SUBPROCESS to ensure correct context) ---
            install_duration = 0.0
            timings['install_start'] = time.perf_counter()
            if is_installed:
                thread_safe_print(f'{prefix} ⚡ CACHE HIT: rich=={rich_version} already installed in Python {py_version}')
            else:
                thread_safe_print(f'{prefix} 📦 INSTALLING: rich=={rich_version} for Python {py_version}')
                returncode, install_duration = run_and_stream_install(
                    ['install', f'rich=={rich_version}'],
                    f"Installing rich=={rich_version}",
                    'omnipkg',
                    thread_id
                )
                if returncode != 0:
                    raise RuntimeError(f"Failed to install rich=={rich_version} for Python {py_version}")
            timings['install_end'] = time.perf_counter()
            
            thread_safe_print(f'{prefix} 🔓 LOCK RELEASED')
            timings['lock_released'] = time.perf_counter()
        
        # === STEP 4: Run the test payload using omnipkg loader ===
        thread_safe_print(f'{prefix} 🧪 TESTING Rich in Python {py_version}')
        timings['test_start'] = time.perf_counter()
        # CRITICAL: Use omnipkg's loader to activate the specific bubbled version
        # This is the ONLY way to access bubbled packages that aren't in the main environment
        test_script = f'''
import sys
import json
from omnipkg.core import ConfigManager
from omnipkg.loader import omnipkgLoader

# Initialize config
config_manager = ConfigManager(suppress_init_messages=True)
omnipkg_config = config_manager.config

# Use omnipkgLoader context manager to activate the specific Rich version
with omnipkgLoader("rich=={rich_version}", config=omnipkg_config):
    import rich
    import importlib.metadata
    
    result = {{
        "python_version": sys.version.split()[0],
        "rich_version": importlib.metadata.version("rich"),
        "success": True
    }}
    print(json.dumps(result))
'''
        
        cmd = [python_exe, '-c', test_script]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
        
        if result.returncode != 0:
            thread_safe_print(f'{prefix} ❌ Test subprocess failed with exit code {result.returncode}!')
            thread_safe_print(f'{prefix} STDOUT: {result.stdout}')
            thread_safe_print(f'{prefix} STDERR: {result.stderr}')
            raise RuntimeError(f"Test failed for Python {py_version} with Rich {rich_version}")
        
        # Check if we got any output
        if not result.stdout.strip():
            thread_safe_print(f'{prefix} ❌ Test subprocess produced no output!')
            thread_safe_print(f'{prefix} STDERR: {result.stderr}')
            raise RuntimeError(f"Test produced no output for Python {py_version} with Rich {rich_version}")
        
        # Extract JSON from the output (omnipkg loader prints debug info to stdout)
        # Find the line that looks like JSON
        json_line = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    json.loads(line)  # Validate it's actually JSON
                    json_line = line
                    break
                except json.JSONDecodeError:
                    continue
        
        if not json_line:
            thread_safe_print(f'{prefix} ❌ No valid JSON found in output!')
            thread_safe_print(f'{prefix} STDOUT: {result.stdout}')
            thread_safe_print(f'{prefix} STDERR: {result.stderr}')
            raise RuntimeError(f"No JSON output found for Python {py_version} with Rich {rich_version}")
        
        try:
            test_data = json.loads(json_line)
        except json.JSONDecodeError as e:
            thread_safe_print(f'{prefix} ❌ Failed to parse JSON output!')
            thread_safe_print(f'{prefix} JSON LINE: {json_line}')
            raise
        
        timings['end'] = time.perf_counter()
        
        # Compile final results for this thread
        final_results = {
            'thread_id': thread_id,
            'python_version': test_data['python_version'],
            'rich_version': test_data['rich_version'],
            'timings_ms': {
                'lookup_and_check': (timings['wait_lock_start'] - timings['start']) * 1000,
                'wait_for_lock': (timings['lock_acquired'] - timings['wait_lock_start']) * 1000,
                'swap_time': (timings['swap_end'] - timings['swap_start']) * 1000,
                'install_time': install_duration,
                'total_locked_time': (timings['lock_released'] - timings['lock_acquired']) * 1000,
                'test_execution': (timings['end'] - timings['test_start']) * 1000,
                'total_thread_time': (timings['end'] - timings['start']) * 1000,
            }
        }
        thread_safe_print(f'{prefix} ✅ DIMENSION TEST COMPLETE in {format_duration(final_results["timings_ms"]["total_thread_time"])}')
        return final_results
        
    except Exception as e:
        thread_safe_print(f'{prefix} ❌ FAILED: {type(e).__name__}: {e}')
        import traceback
        thread_safe_print(traceback.format_exc())
        return None

# --- Main Orchestrator and Reporting ---
def print_final_summary(results: List[Dict], overall_start_time: float):
    """Prints a detailed final summary, including a timeline and analysis."""
    overall_duration = (time.perf_counter() - overall_start_time) * 1000
    if not results:
        thread_safe_print("No successful results to analyze.")
        return

    results.sort(key=lambda r: r['thread_id'])

    thread_safe_print('\n' + '=' * 80)
    thread_safe_print('📊 DETAILED TIMING BREAKDOWN')
    thread_safe_print('=' * 80)
    
    for res in results:
        t = res['timings_ms']
        thread_safe_print(f"🧵 Thread {res['thread_id']} (Python {res['python_version']} | Rich {res['rich_version']}) - Total: {format_duration(t['total_thread_time'])}")
        thread_safe_print(f"   ├─ Prep (Lookup/Check): {format_duration(t['lookup_and_check'])}")
        thread_safe_print(f"   ├─ Wait for Lock:       {format_duration(t['wait_for_lock'])}")
        thread_safe_print(f"   ├─ Swap Context:        {format_duration(t['swap_time'])}")
        thread_safe_print(f"   ├─ Install Package:     {format_duration(t['install_time'])}")
        thread_safe_print(f"   └─ Test Execution:      {format_duration(t['test_execution'])}")

    thread_safe_print('\n' + '=' * 80)
    thread_safe_print('⏳ CONCURRENCY TIMELINE VISUALIZATION')
    thread_safe_print('=' * 80)
    
    scale = 60 / (overall_duration / 1000)
    for res in results:
        t = res['timings_ms']
        
        prep_chars = int(t['lookup_and_check'] / 1000 * scale)
        wait_chars = int(t['wait_for_lock'] / 1000 * scale)
        work_chars = int(t['total_locked_time'] / 1000 * scale)
        test_chars = int(t['test_execution'] / 1000 * scale)
        
        timeline = (
            f"T{res['thread_id']}: "
            f"{'─' * prep_chars}"
            f"{'░' * wait_chars}"
            f"{'█' * work_chars}"
            f"{'=' * test_chars}"
        )
        thread_safe_print(timeline)
    thread_safe_print("Legend: ─ Prep | ░ Wait | █ Locked Work | = Test")

    thread_safe_print('\n' + '=' * 80)
    thread_safe_print('🔍 BOTTLENECK ANALYSIS')
    thread_safe_print('=' * 80)

    total_wait_time = sum(r['timings_ms']['wait_for_lock'] for r in results)
    total_install_time = sum(r['timings_ms']['install_time'] for r in results)
    
    if total_wait_time > 1000:
        thread_safe_print(f"🔴 High Contention: Threads spent a cumulative {format_duration(total_wait_time)} waiting for the environment lock.")
        thread_safe_print("   This indicates that environment modifications (swapping, installing) are serializing the execution.")
    
    if total_install_time > 2000:
        thread_safe_print(f"🔴 Slow Installation: A total of {format_duration(total_install_time)} was spent installing packages.")
        thread_safe_print("   This was the primary cause of the long runtime. Subsequent runs should be faster due to caching.")
    
    if total_wait_time < 1000 and total_install_time < 2000:
        thread_safe_print("🟢 Low Contention & Fast Installs: The test ran efficiently.")
        
    thread_safe_print(f"\n🏆 Total Concurrent Runtime: {format_duration(overall_duration)}")

def rich_multiverse_test():
    """Main test orchestrator."""
    safe_print("🚀 Initializing shared omnipkg core instance...")
    config_manager = ConfigManager(suppress_init_messages=True)
    shared_omnipkg_instance = omnipkg(config_manager)
    safe_print("✅ Core instance ready.")

    test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
    
    # NEW: Check and adopt Python versions before running tests
    if not ensure_python_versions_available(test_configs, shared_omnipkg_instance):
        safe_print("\n💥 ABORTING: Cannot proceed without required Python interpreters.")
        sys.exit(1)

    overall_start_time = time.perf_counter()
    thread_safe_print('=' * 80)
    thread_safe_print('🚀 CONCURRENT RICH MULTIVERSE TEST (DEBUG MODE)')
    thread_safe_print('=' * 80)

    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(test_configs)) as executor:
        future_to_config = {
            executor.submit(prepare_and_test_dimension, config, shared_omnipkg_instance, i+1): config 
            for i, config in enumerate(test_configs)
        }
        
        for future in concurrent.futures.as_completed(future_to_config):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                config = future_to_config[future]
                thread_safe_print(f"❌ Thread for {config} failed with exception: {e}")

    print_final_summary(results, overall_start_time)
    
    success = len(results) == len(test_configs)
    thread_safe_print('\n' + '=' * 80)
    thread_safe_print('🎉🎉🎉 MULTIVERSE TEST COMPLETE! 🎉🎉🎉' if success else '💥💥💥 MULTIVERSE TEST FAILED! 💥💥💥')
    thread_safe_print('=' * 80)

if __name__ == '__main__':
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        rich_multiverse_test()