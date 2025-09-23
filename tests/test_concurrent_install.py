# filename: rich_multiverse_debug.py
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
    print(json.dumps(result)) # Use standard print for subprocess stdout

def run_command_isolated(cmd_args: List[str], description: str, python_exe: str, thread_id: int) -> Tuple[str, int, float]:
    """Runs a command and captures its output, returning timing info."""
    prefix = f"[T{thread_id}]"
    thread_safe_print(f'{prefix} ▶️  Executing: {description}')
    start_time = time.perf_counter()
    
    cmd = [python_exe, '-m', 'omnipkg.cli'] + cmd_args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    if result.returncode != 0:
        thread_safe_print(f'{prefix}   ⚠️  WARNING: Command failed (code {result.returncode}) in {format_duration(duration_ms)}')
        # Uncomment the line below for full error output on failure
        # thread_safe_print(f'{prefix}      | {(result.stderr or result.stdout).strip()}')
    else:
        thread_safe_print(f'{prefix}   ✅ Completed in {format_duration(duration_ms)}')
        
    return (result.stdout + result.stderr), result.returncode, duration_ms

def run_and_stream_install(cmd_args: List[str], description: str, python_exe: str, thread_id: int) -> Tuple[int, float]:
    """
    NEW: Runs the install command and streams its output live for transparency.
    This is crucial for debugging slow installations.
    """
    prefix = f"[T{thread_id}]"
    install_prefix = f"[T{thread_id}|install]"
    thread_safe_print(f'{prefix} ▶️  Executing: {description} (Live Output Below)')
    start_time = time.perf_counter()
    
    cmd = [python_exe, '-m', 'omnipkg.cli'] + cmd_args
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                thread_safe_print(f'{install_prefix} | {line.strip()}')
        process.wait()
        returncode = process.returncode
    except FileNotFoundError:
        thread_safe_print(f'{prefix} ❌ ERROR: Executable not found: {python_exe}')
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
    # Use the system's default omnipkg to get info
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
    (CORRECTED) The main worker function for each thread, now using the
    shared in-process omnipkg instance for all operations.
    """
    py_version, rich_version = config
    prefix = f"[T{thread_id}]"
    
    timings: Dict[str, float] = {k: 0 for k in ['start', 'wait_lock_start', 'lock_acquired', 'install_start', 'install_end', 'lock_released', 'test_start', 'end']}
    timings['start'] = time.perf_counter()

    try:
        thread_safe_print(f'{prefix} 🚀 DIMENSION TEST: Python {py_version} with Rich {rich_version}')
        
        # === STEP 1: Get interpreter path (HITS THE CACHE) ===
        # This now calls the method on the shared instance.
        python_exe_path = omnipkg_instance.config_manager.get_interpreter_for_version(py_version)

        if not python_exe_path:
            raise RuntimeError(f"Could not find interpreter for {py_version}")
        python_exe = str(python_exe_path)

        # === STEP 2: Check if package is installed (HITS THE CACHE) ===
        is_installed, check_duration = omnipkg_instance.check_package_installed_fast(python_exe, 'rich', rich_version)
        
        # === STEP 3: Critical section (IN-PROCESS) ===
        thread_safe_print(f'{prefix} ⏳ WAITING for lock...')
        timings['wait_lock_start'] = time.perf_counter()
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            thread_safe_print(f'{prefix} 🔒 LOCK ACQUIRED - Modifying shared environment')
            
            # --- SWAP CONTEXT (IN-PROCESS) ---
            swap_start = time.perf_counter()
            omnipkg_instance.switch_active_python(py_version)
            swap_duration = (time.perf_counter() - swap_start) * 1000
            thread_safe_print(f'{prefix} ✅ Context switched to Python {py_version} in {format_duration(swap_duration)}')
            
            # --- INSTALL PACKAGE (IN-PROCESS) ---
            install_duration = 0.0
            timings['install_start'] = time.perf_counter()
            if is_installed:
                thread_safe_print(f'{prefix} ⚡ CACHE HIT: rich=={rich_version} already installed')
            else:
                thread_safe_print(f'{prefix} 📦 INSTALLING: rich=={rich_version}')
                omnipkg_instance.smart_install([f'rich=={rich_version}'])
                install_duration = (time.perf_counter() - timings['install_start']) * 1000
            timings['install_end'] = time.perf_counter()
            
            thread_safe_print(f'{prefix} 🔓 LOCK RELEASED')
            timings['lock_released'] = time.perf_counter()
        
        # === STEP 4: Run the test payload (This MUST be a subprocess) ===
        thread_safe_print(f'{prefix} 🧪 TESTING Rich in Python {py_version}')
        timings['test_start'] = time.perf_counter()
        # The test itself still needs to run in the target interpreter's context.
        cmd = [python_exe, __file__, '--test-rich']
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        test_data = json.loads(result.stdout)
        timings['end'] = time.perf_counter()
        
        # Compile final results for this thread
        final_results = {
            'thread_id': thread_id,
            'python_version': test_data['python_version'],
            'rich_version': test_data['rich_version'],
            'timings_ms': {
                'lookup_and_check': (timings['wait_lock_start'] - timings['start']) * 1000,
                'wait_for_lock': (timings['lock_acquired'] - timings['wait_lock_start']) * 1000,
                'swap_time': swap_duration,
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
        # Add traceback for easier debugging
        import traceback
        thread_safe_print(traceback.format_exc())
        return None

# --- Main Orchestrator and Reporting ---
def print_final_summary(results: List[Dict], overall_start_time: float):
    """NEW: Prints a much more detailed final summary, including a timeline and analysis."""
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
    
    scale = 60 / (overall_duration / 1000) # characters per second
    for res in results:
        t = res['timings_ms']
        
        prep_chars = int(t['lookup_and_check'] / 1000 * scale)
        wait_chars = int(t['wait_for_lock'] / 1000 * scale)
        work_chars = int(t['total_locked_time'] / 1000 * scale)
        test_chars = int(t['test_execution'] / 1000 * scale)
        
        timeline = (
            f"T{res['thread_id']}: "
            f"{'─' * prep_chars}"  # Prep
            f"{'░' * wait_chars}"  # Waiting for lock
            f"{'█' * work_chars}"  # Locked work (swap + install)
            f"{'=' * test_chars}"   # Test execution
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
    print("🚀 Initializing shared omnipkg core instance...")
    config_manager = ConfigManager(suppress_init_messages=True)
    shared_omnipkg_instance = omnipkg(config_manager)
    print("✅ Core instance ready.")

    overall_start_time = time.perf_counter()
    thread_safe_print('=' * 80)
    thread_safe_print('🚀 CONCURRENT RICH MULTIVERSE TEST (DEBUG MODE)')
    thread_safe_print('=' * 80)

    test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
    results = []  # Initialize results list
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(test_configs)) as executor:
        future_to_config = {
            # Pass the SAME instance to every thread
            executor.submit(prepare_and_test_dimension, config, shared_omnipkg_instance, i+1): config 
            for i, config in enumerate(test_configs)
        }
        
        # Collect results from completed futures
        for future in concurrent.futures.as_completed(future_to_config):
            try:
                result = future.result()
                if result:  # Only add successful results
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
    # This allows the script to call itself to run the isolated test function
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        rich_multiverse_test()