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
# NEW: A dedicated lock for the adoption process to prevent race conditions on downloads
adopt_lock = threading.Lock()


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

def prepare_interpreter_dimension(py_version: str, omnipkg_instance: omnipkg, thread_id: int):
    """
    NEW: Worker function dedicated to adopting a Python interpreter if it's missing.
    This runs concurrently with other preparations and tests.
    """
    prefix = f"[T{thread_id}|Adopt]"
    try:
        # Check if interpreter already exists to avoid unnecessary work
        if omnipkg_instance.config_manager.get_interpreter_for_version(py_version):
            thread_safe_print(f'{prefix} ✅ Python {py_version} already adopted.')
            return True
        
        # Use a dedicated lock for the adoption process
        with adopt_lock:
            # Double-check after acquiring the lock in case another thread just finished
            if omnipkg_instance.config_manager.get_interpreter_for_version(py_version):
                thread_safe_print(f'{prefix} ✅ Python {py_version} was adopted by another thread.')
                return True

            thread_safe_print(f'{prefix} 🚀 ADOPTING Python {py_version}...')
            start_time = time.perf_counter()
            # The adopt method is already part of the omnipkg core class
            success = omnipkg_instance.adopt_interpreter(py_version, quiet=True) # Use quiet to avoid noisy output
            duration = (time.perf_counter() - start_time) * 1000
            
            if success:
                thread_safe_print(f'{prefix} ✅ Successfully adopted Python {py_version} in {format_duration(duration)}')
                return True
            else:
                thread_safe_print(f'{prefix} ❌ FAILED to adopt Python {py_version}.')
                return False
    except Exception as e:
        thread_safe_print(f'{prefix} ❌ FAILED with exception: {e}')
        return False

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

        # === STEP 2: Check if package is installed (using omnipkg's fast, bubble-aware check) ===
        # Note: The signature in core.py returns a status string, not a boolean.
        install_status, check_duration = omnipkg_instance.check_package_installed_fast(python_exe, 'rich', rich_version)
        is_installed = install_status is not None

        # === STEP 3: Critical section (SUBPROCESS OPERATIONS) ===
        thread_safe_print(f'{prefix} ⏳ WAITING for lock...')
        timings['wait_lock_start'] = time.perf_counter()
        with omnipkg_lock:
            timings['lock_acquired'] = time.perf_counter()
            thread_safe_print(f'{prefix} 🔒 LOCK ACQUIRED - Modifying shared environment')
            
            # --- SWAP CONTEXT (SUBPROCESS) ---
            swap_returncode, swap_duration = run_and_stream_install(
                ['swap', 'python', py_version],
                f"Swapping to Python {py_version}",
                'omnipkg',
                thread_id
            )
            if swap_returncode != 0:
                raise RuntimeError(f"Failed to swap to Python {py_version}")
            timings['swap_end'] = time.perf_counter()
            
            # --- INSTALL (SUBPROCESS) ---
            install_duration = 0.0
            timings['install_start'] = time.perf_counter()
            if is_installed:
                thread_safe_print(f'{prefix} ⚡ CACHE HIT: rich=={rich_version} already exists for Python {py_version}')
            else:
                thread_safe_print(f'{prefix} 📦 INSTALLING: rich=={rich_version} for Python {py_version}')
                install_returncode, install_duration = run_and_stream_install(
                    ['install', f'rich=={rich_version}'],
                    f"Installing rich=={rich_version}",
                    'omnipkg',
                    thread_id
                )
                if install_returncode != 0:
                    raise RuntimeError(f"Failed to install rich=={rich_version} for Python {py_version}")
            timings['install_end'] = time.perf_counter()
            
            thread_safe_print(f'{prefix} 🔓 LOCK RELEASED')
            timings['lock_released'] = time.perf_counter()
        
        # === STEP 4: Run the test payload using the correct interpreter ===
        thread_safe_print(f'{prefix} 🧪 TESTING Rich in Python {py_version}')
        timings['test_start'] = time.perf_counter()

        # [THIS IS THE FIX]
        # The test script must re-initialize a ConfigManager to read the new, swapped state from disk.
        test_script = f'''
import sys
import json
import traceback
try:
    # This will now run inside the correct Python context (e.g., 3.9)
    # and load the config that was just modified by the 'omnipkg swap' command.
    from omnipkg.core import ConfigManager
    from omnipkg.loader import omnipkgLoader

    # Initialize a new ConfigManager to get the current state from disk.
    config_manager = ConfigManager(suppress_init_messages=True)
    omnipkg_config = config_manager.config

    with omnipkgLoader("rich=={rich_version}", config=omnipkg_config):
        import rich
        import importlib.metadata
        
        result = {{
            "python_version": sys.version.split()[0],
            "rich_version": importlib.metadata.version("rich"),
            "success": True
        }}
        print(json.dumps(result))

except Exception as e:
    result = {{ "success": False, "error": str(e), "traceback": traceback.format_exc() }}
    print(json.dumps(result), file=sys.stderr)
    sys.exit(1)
'''
        
        cmd = [python_exe, '-c', test_script]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
        
        json_output = None
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                if line.strip().startswith('{{') and line.strip().endswith('}}'):
                    json_output = line
                    break
        elif result.stderr:
             for line in result.stderr.splitlines():
                if line.strip().startswith('{{') and line.strip().endswith('}}'):
                    json_output = line
                    break

        if not json_output:
            thread_safe_print(f'{{prefix}} ❌ Test subprocess failed to produce valid JSON output!')
            thread_safe_print(f'{{prefix}} STDOUT: {{result.stdout}}')
            thread_safe_print(f'{{prefix}} STDERR: {{result.stderr}}')
            raise RuntimeError(f"Test failed for Python {py_version} with Rich {rich_version}")

        test_data = json.loads(json_output)
        if not test_data.get('success'):
            thread_safe_print(f"{{prefix}} ❌ Test payload reported failure: {{test_data.get('error')}}")
            raise RuntimeError(f"Test failed for Python {py_version} with Rich {rich_version}")
        
        timings['end'] = time.perf_counter()
        
        final_results = {{
            'thread_id': thread_id,
            'python_version': test_data['python_version'],
            'rich_version': test_data['rich_version'],
            'timings_ms': {{
                'lookup_and_check': (timings['wait_lock_start'] - timings['start']) * 1000,
                'wait_for_lock': (timings['lock_acquired'] - timings['wait_lock_start']) * 1000,
                'swap_time': swap_duration,
                'install_time': install_duration,
                'total_locked_time': (timings['lock_released'] - timings['lock_acquired']) * 1000,
                'test_execution': (timings['end'] - timings['test_start']) * 1000,
                'total_thread_time': (timings['end'] - timings['start']) * 1000,
            }}
        }}
        thread_safe_print(f'{{prefix}} ✅ DIMENSION TEST COMPLETE in {{format_duration(final_results["timings_ms"]["total_thread_time"])}}')
        return final_results
        
    except Exception as e:
        thread_safe_print(f'{{prefix}} ❌ FAILED: {{type(e).__name__}}: {{e}}')
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
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(test_configs) * 2) as executor:
        # --- NEW: Concurrent Adoption Phase ---
        # First, submit all the adoption tasks.
        adoption_futures = [
            executor.submit(prepare_interpreter_dimension, config[0], shared_omnipkg_instance, i+1)
            for i, config in enumerate(test_configs)
        ]
        
        # Wait for all interpreters to be ready before starting the tests.
        # This ensures the environment is fully prepared for the testing phase.
        adoption_results = [future.result() for future in concurrent.futures.as_completed(adoption_futures)]
        
        if not all(adoption_results):
            thread_safe_print("💥💥💥 MULTIVERSE TEST FAILED: Not all interpreters could be adopted. 💥💥💥")
            return

        thread_safe_print("✅ All interpreters are adopted and ready. Starting concurrent tests.")
        
        # --- Concurrent Testing Phase (as before) ---
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