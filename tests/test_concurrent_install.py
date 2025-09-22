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
from typing import Optional, List, Tuple, Dict
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.core import ConfigManager
except ImportError as e:
    safe_print(f'FATAL: Could not import omnipkg modules. Make sure this script is placed correctly. Error: {e}')
    sys.exit(1)

# Thread-safe printing and locking
print_lock = threading.Lock()
omnipkg_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    """Thread-safe wrapper around safe_print."""
    with print_lock:
        safe_print(*args, **kwargs)

def format_duration(duration_ms: float) -> str:
    """Format duration with appropriate units for clarity."""
    if duration_ms < 1:
        return f"{duration_ms * 1000:.1f}μs"
    elif duration_ms < 1000:
        return f"{duration_ms:.1f}ms"
    else:
        return f"{duration_ms / 1000:.2f}s"

def test_rich_version():
    """This function tests rich version and shows interpreter info - executed in different Python versions."""
    import rich
    import importlib.metadata
    import sys
    import json
    safe_print(_('--- Testing Rich in Python {} ---').format(sys.version[:5]), file=sys.stderr)
    safe_print(_('--- Interpreter Path: {} ---').format(sys.executable), file=sys.stderr)
    try:
        rich_version = rich.__version__
        version_source = 'rich.__version__'
    except AttributeError:
        rich_version = importlib.metadata.version('rich')
        version_source = 'importlib.metadata.version'
    result = {'python_version': sys.version[:5], 'interpreter_path': sys.executable, 'rich_version': rich_version, 'version_source': version_source, 'success': True}
    safe_print(json.dumps(result))

def run_command_isolated(cmd_args, description, python_exe=None, thread_id=None, measure_time=False):
    """Runs a command in isolation with thread-safe output and optional timing."""
    prefix = f"[T{thread_id}] " if thread_id else ""
    start_time = time.perf_counter() if measure_time else None
    
    thread_safe_print(f'{prefix}▶️  Executing: {description}')
    executable = python_exe or sys.executable
    cmd = [executable, '-m', 'omnipkg.cli'] + cmd_args
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    end_time = time.perf_counter() if measure_time else None
    duration_ms = (end_time - start_time) * 1000 if measure_time else None
    
    # Only show detailed output if there's an error or if explicitly requested
    if result.returncode != 0 or thread_id is None:  # Show details for main thread or errors
        full_output = (result.stdout + result.stderr).strip()
        for line in full_output.splitlines():
            thread_safe_print(f'{prefix}   | {line}')
    
    if result.returncode != 0:
        thread_safe_print(f'{prefix}   ⚠️  WARNING: Command finished with non-zero exit code: {result.returncode}')
    elif measure_time and duration_ms is not None:
        thread_safe_print(f'{prefix}   ✅ Completed in {format_duration(duration_ms)}')
    else:
        thread_safe_print(f'{prefix}   ✅ Completed successfully')
    
    return (result.stdout + result.stderr, result.returncode, duration_ms if measure_time else 0)

def get_current_env_id():
    """Gets the current environment ID from omnipkg config."""
    try:
        cm = ConfigManager(suppress_init_messages=True)
        return cm.env_id
    except Exception as e:
        thread_safe_print(_('⚠️  Could not get environment ID: {}').format(e))
        return None

def get_config_value(key: str) -> str:
    """Gets a specific value from the omnipkg config."""
    result = subprocess.run(['omnipkg', 'config', 'view'], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(key):
            return line.split(':', 1)[1].strip()
    return 'stable-main' if key == 'install_strategy' else ''

def ensure_dimension_exists(version: str, thread_id: int = None):
    """Ensures a specific Python version is adopted by omnipkg before use."""
    prefix = f"[T{thread_id}] " if thread_id else ""
    thread_safe_print(f'{prefix}🔍 VALIDATING: Ensuring Python {version} is adopted...')
    start_time = time.perf_counter()
    try:
        cmd = ['omnipkg', 'python', 'adopt', version]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000
        thread_safe_print(f'{prefix}   ✅ Python {version} validated in {format_duration(duration_ms)}')
        return duration_ms
    except subprocess.CalledProcessError as e:
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000
        thread_safe_print(f'{prefix}❌ FAILED TO ADOPT DIMENSION {version} after {format_duration(duration_ms)}!', file=sys.stderr)
        thread_safe_print(f'{prefix}--- Subprocess STDERR ---', file=sys.stderr)
        thread_safe_print(f'{prefix}{e.stderr}', file=sys.stderr)
        raise

def get_interpreter_path(version: str, thread_id: int = None) -> str:
    """Asks omnipkg for the location of a specific Python dimension."""
    prefix = f"[T{thread_id}] " if thread_id else ""
    start_time = time.perf_counter()
    result = subprocess.run(['omnipkg', 'info', 'python'], capture_output=True, text=True, check=True)
    end_time = time.perf_counter()
    duration_ms = (end_time - start_time) * 1000
    
    for line in result.stdout.splitlines():
        if line.strip().startswith(f'• Python {version}'):
            match = re.search(r':\s*(/\S+)', line)
            if match:
                path = match.group(1).strip()
                thread_safe_print(f'{prefix}📍 Located Python {version} at {path} ({format_duration(duration_ms)})')
                return path
    raise RuntimeError(f"Could not find managed Python {version} via 'omnipkg info python'.")

def check_package_installed(python_exe: str, package: str, version: str) -> Tuple[bool, float]:
    """Check if a specific package version is already installed."""
    start_time = time.perf_counter()
    is_installed_cmd = [
        python_exe, '-c', 
        f"import importlib.metadata; import sys; sys.exit(0) if importlib.metadata.version('{package}') == '{version}' else sys.exit(1)"
    ]
    result = subprocess.run(is_installed_cmd, capture_output=True)
    end_time = time.perf_counter()
    duration_ms = (end_time - start_time) * 1000
    return result.returncode == 0, duration_ms

def prepare_and_test_dimension(config: Tuple[str, str], thread_id: int, original_strategy: str, skip_cleanup: bool = False) -> Optional[Dict]:
    """Prepares a dimension with a specific Rich version and runs the test in isolation."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}] "
    
    timing_data = {
        'interpreter_lookup': 0,
        'swap_time': 0,
        'package_check': 0,
        'install_time': 0,
        'test_execution': 0
    }
    
    try:
        thread_safe_print(f'{prefix}🚀 DIMENSION TEST: Python {py_version} with Rich {rich_version}')
        
        # Get interpreter path (timed)
        start_time = time.perf_counter()
        python_exe = get_interpreter_path(py_version, thread_id)
        timing_data['interpreter_lookup'] = (time.perf_counter() - start_time) * 1000
        
        # Check if package is already installed (before lock acquisition)
        is_installed, check_time = check_package_installed(python_exe, 'rich', rich_version)
        timing_data['package_check'] = check_time
        
        # --- CRITICAL SECTION START ---
        with omnipkg_lock:
            thread_safe_print(f'{prefix}🔒 LOCK ACQUIRED - Modifying shared environment')
            
            # Swap Python context (timed)
            start_swap = time.perf_counter()
            _, _, swap_duration = run_command_isolated(
                ['swap', 'python', py_version], 
                f'Swapping to Python {py_version}', 
                python_exe=python_exe, 
                thread_id=thread_id,
                measure_time=True
            )
            timing_data['swap_time'] = swap_duration
            thread_safe_print(f'{prefix}✅ Context switched to Python {py_version}')
            
            # Install package if needed (timed)
            start_install = time.perf_counter()
            if is_installed:
                thread_safe_print(f'{prefix}⚡ CACHE HIT: rich=={rich_version} already installed')
                timing_data['install_time'] = 0
            else:
                thread_safe_print(f'{prefix}📦 INSTALLING: rich=={rich_version}')
                
                # Temporarily set install strategy if needed
                current_strategy = get_config_value('install_strategy')
                if current_strategy != 'latest-active':
                    run_command_isolated(['config', 'set', 'install_strategy', 'latest-active'], 
                                       'Setting install strategy', python_exe=python_exe, thread_id=thread_id)
                
                # Install the package
                _, _, install_duration = run_command_isolated(
                    ['install', f'rich=={rich_version}'], 
                    f'Installing rich=={rich_version}', 
                    python_exe=python_exe, 
                    thread_id=thread_id,
                    measure_time=True
                )
                timing_data['install_time'] = install_duration
                
                # Restore strategy
                if current_strategy != 'latest-active':
                    run_command_isolated(['config', 'set', 'install_strategy', original_strategy], 
                                       'Restoring install strategy', python_exe=python_exe, thread_id=thread_id)
            
            thread_safe_print(f'{prefix}🔓 LOCK RELEASED')
        # --- CRITICAL SECTION END ---
        
        # Execute test (timed, outside of lock)
        thread_safe_print(f'{prefix}🧪 TESTING Rich in Python {py_version}')
        start_test = time.perf_counter()
        
        cmd = [python_exe, __file__, '--test-rich']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        end_test = time.perf_counter()
        timing_data['test_execution'] = (end_test - start_test) * 1000
        
        if result.returncode != 0 or not result.stdout.strip():
            thread_safe_print(f'{prefix}❌ Test failed (return code: {result.returncode})')
            return None
            
        try:
            test_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            thread_safe_print(f'{prefix}❌ Failed to parse test output')
            return None
            
        # Add timing data to results
        test_data.update({
            'thread_id': thread_id,
            **timing_data,
            'total_time': sum(timing_data.values())
        })
        
        thread_safe_print(f'{prefix}✅ DIMENSION TEST COMPLETE:')
        thread_safe_print(f'{prefix}   Rich {test_data["rich_version"]} verified in {format_duration(timing_data["test_execution"])}')
        thread_safe_print(f'{prefix}   Total time: {format_duration(test_data["total_time"])}')
        
        return test_data
        
    except subprocess.TimeoutExpired:
        thread_safe_print(f'{prefix}❌ Test timed out after 30s')
        return None
    except Exception as e:
        thread_safe_print(f'{prefix}❌ Test failed: {e}')
        return None

def rich_multiverse_test():
    """Main orchestrator with optimized concurrent testing."""
    overall_start = time.perf_counter()
    
    # Get original context info
    original_dimension = get_config_value('python_executable')
    original_version_match = re.search(r'(\d+\.\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else '3.11'
    original_strategy = get_config_value('install_strategy')
    
    thread_safe_print('=' * 80)
    thread_safe_print('🚀 OPTIMIZED CONCURRENT RICH MULTIVERSE TEST')
    thread_safe_print('=' * 80)
    thread_safe_print(f'🏠 Starting from Python {original_version}')
    
    initial_env_id = get_current_env_id()
    if initial_env_id:
        thread_safe_print(f'📍 Environment ID: {initial_env_id}')
    
    try:
        # Test configurations
        test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
        required_versions = [config[0] for config in test_configs]
        
        # CONCURRENT dimension validation
        thread_safe_print('\n🔍 VALIDATING DIMENSIONS (Concurrent)...')
        validation_start = time.perf_counter()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(required_versions)) as executor:
            validation_futures = {
                executor.submit(ensure_dimension_exists, version, i+1): version 
                for i, version in enumerate(required_versions)
            }
            
            validation_times = {}
            for future in concurrent.futures.as_completed(validation_futures):
                version = validation_futures[future]
                try:
                    duration = future.result()
                    validation_times[version] = duration
                except Exception as exc:
                    thread_safe_print(f'❌ Validation failed for Python {version}: {exc}')
                    return False
        
        validation_end = time.perf_counter()
        total_validation_time = (validation_end - validation_start) * 1000
        avg_validation_time = sum(validation_times.values()) / len(validation_times)
        
        thread_safe_print(f'✅ All dimensions validated concurrently:')
        for version, duration in validation_times.items():
            thread_safe_print(f'   Python {version}: {format_duration(duration)}')
        thread_safe_print(f'⚡ Concurrent validation completed in {format_duration(total_validation_time)}')
        thread_safe_print(f'📊 Average per-dimension: {format_duration(avg_validation_time)}')
        
        # CONCURRENT testing
        thread_safe_print('\n🚀 LAUNCHING CONCURRENT TESTS...')
        test_start = time.perf_counter()
        test_results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(test_configs)) as executor:
            future_to_config = {
                executor.submit(prepare_and_test_dimension, config, i+1, original_strategy, True): config 
                for i, config in enumerate(test_configs)
            }
            
            for future in concurrent.futures.as_completed(future_to_config):
                config = future_to_config[future]
                try:
                    result = future.result()
                    if result:
                        test_results.append(result)
                except Exception as exc:
                    py_version, rich_version = config
                    thread_safe_print(f'❌ Test failed for Python {py_version}/Rich {rich_version}: {exc}')
        
        test_end = time.perf_counter()
        total_test_time = (test_end - test_start) * 1000
        
        # Results analysis
        test_results.sort(key=lambda x: x.get('thread_id', 0))
        
        thread_safe_print('\n📊 DETAILED TIMING BREAKDOWN:')
        thread_safe_print('=' * 80)
        
        timing_categories = ['interpreter_lookup', 'swap_time', 'package_check', 'install_time', 'test_execution']
        category_totals = {cat: 0 for cat in timing_categories}
        
        for i, result in enumerate(test_results, 1):
            thread_safe_print(f'🧵 Thread {result["thread_id"]}: Python {result["python_version"]} | Rich {result["rich_version"]}')
            thread_safe_print(f'   └─ Interpreter lookup: {format_duration(result["interpreter_lookup"])}')
            thread_safe_print(f'   └─ Context swap:       {format_duration(result["swap_time"])}')
            thread_safe_print(f'   └─ Package check:      {format_duration(result["package_check"])}')
            thread_safe_print(f'   └─ Install time:       {format_duration(result["install_time"])}')
            thread_safe_print(f'   └─ Test execution:     {format_duration(result["test_execution"])}')
            thread_safe_print(f'   └─ 📊 TOTAL:           {format_duration(result["total_time"])}')
            thread_safe_print()
            
            for cat in timing_categories:
                category_totals[cat] += result[cat]
        
        overall_end = time.perf_counter()
        total_runtime = (overall_end - overall_start) * 1000
        
        thread_safe_print('🏆 PERFORMANCE SUMMARY:')
        thread_safe_print('=' * 80)
        thread_safe_print(f'🔍 Dimension validation: {format_duration(total_validation_time)} (concurrent)')
        thread_safe_print(f'🧪 Test execution phase: {format_duration(total_test_time)} (concurrent)')
        thread_safe_print(f'⚡ Total runtime:        {format_duration(total_runtime)}')
        thread_safe_print()
        
        if test_results:
            unique_versions = set(r['rich_version'] for r in test_results)
            unique_interpreters = set(r['interpreter_path'] for r in test_results)
            
            thread_safe_print('✅ SUCCESS METRICS:')
            thread_safe_print(f'   📦 Rich versions tested: {len(unique_versions)} ({", ".join(sorted(unique_versions))})')
            thread_safe_print(f'   🐍 Python interpreters:  {len(unique_interpreters)}')
            
            avg_times = {cat: category_totals[cat] / len(test_results) for cat in timing_categories}
            thread_safe_print('\n📊 AVERAGE TIMINGS PER TEST:')
            for cat, avg_time in avg_times.items():
                thread_safe_print(f'   {cat.replace("_", " ").title()}: {format_duration(avg_time)}')
            
            # Calculate theoretical sequential time
            sequential_estimate = sum(result["total_time"] for result in test_results)
            speedup = sequential_estimate / total_test_time if total_test_time > 0 else 1
            thread_safe_print(f'\n🚀 CONCURRENCY GAINS:')
            thread_safe_print(f'   Sequential estimate: {format_duration(sequential_estimate)}')
            thread_safe_print(f'   Actual concurrent:   {format_duration(total_test_time)}')
            thread_safe_print(f'   Speedup ratio:       {speedup:.2f}x')
        
        # Skip cleanup return to original context since we're likely already there
        # and the test configs include the original version anyway
        thread_safe_print('\n✨ Cleanup skipped - staying in current optimal context')
        
        return len(test_results) >= 2 and len(set(r['rich_version'] for r in test_results)) >= 2
        
    except Exception as e:
        thread_safe_print(f'\n❌ Critical error in multiverse test: {e}', file=sys.stderr)
        return False

if __name__ == '__main__':
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        success = rich_multiverse_test()
        thread_safe_print('\n' + '=' * 80)
        if success:
            thread_safe_print('🎉🎉🎉 MULTIVERSE TEST SUCCESS! 🎉🎉🎉')
        else:
            thread_safe_print('💥💥💥 MULTIVERSE TEST FAILED! 💥💥💥')
        thread_safe_print('=' * 80)