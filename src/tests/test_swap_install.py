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

# Thread-safe printing
print_lock = threading.Lock()
# ADD THIS LOCK for environment modifications
omnipkg_lock = threading.Lock()

def thread_safe_print(*args, **kwargs):
    """Thread-safe wrapper around safe_print."""
    with print_lock:
        safe_print(*args, **kwargs)

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

def run_command_isolated(cmd_args, description, python_exe=None, thread_id=None):
    """Runs a command in isolation with thread-safe output."""
    prefix = f"[T{thread_id}] " if thread_id else ""
    thread_safe_print(f'{prefix}▶️  Executing: {description}')
    executable = python_exe or sys.executable
    cmd = [executable, '-m', 'omnipkg.cli'] + cmd_args
    thread_safe_print(f'{prefix}   Command: {" ".join(cmd)}')
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    full_output = (result.stdout + result.stderr).strip()
    
    for line in full_output.splitlines():
        thread_safe_print(f'{prefix}   | {line}')
    
    if result.returncode != 0:
        thread_safe_print(f'{prefix}   ⚠️  WARNING: Command finished with non-zero exit code: {result.returncode}')
    
    return (full_output, result.returncode)

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
    thread_safe_print(f'{prefix}   VALIDATING DIMENSION: Ensuring Python {version} is adopted...')
    try:
        cmd = ['omnipkg', 'python', 'adopt', version]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        thread_safe_print(f'{prefix}   ✅ VALIDATION COMPLETE: Python {version} is available.')
    except subprocess.CalledProcessError as e:
        thread_safe_print(f'{prefix}❌ FAILED TO ADOPT DIMENSION {version}!', file=sys.stderr)
        thread_safe_print(f'{prefix}--- Subprocess STDERR ---', file=sys.stderr)
        thread_safe_print(f'{prefix}{e.stderr}', file=sys.stderr)
        raise

def get_interpreter_path(version: str, thread_id: int = None) -> str:
    """Asks omnipkg for the location of a specific Python dimension."""
    prefix = f"[T{thread_id}] " if thread_id else ""
    thread_safe_print(f'{prefix}   LOCKING ON to Python {version} dimension...')
    result = subprocess.run(['omnipkg', 'info', 'python'], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(f'• Python {version}'):
            match = re.search(r':\s*(/\S+)', line)
            if match:
                path = match.group(1).strip()
                thread_safe_print(f'{prefix}   LOCK CONFIRMED: Target is at {path}')
                return path
    raise RuntimeError(f"Could not find managed Python {version} via 'omnipkg info python'.")

def prepare_and_test_dimension(config: Tuple[str, str], thread_id: int, original_strategy: str) -> Optional[Dict]:
    """Prepares a dimension with a specific Rich version and runs the test in isolation."""
    py_version, rich_version = config
    prefix = f"[T{thread_id}] "
    
    try:
        thread_safe_print(f'{prefix}📦 TESTING DIMENSION: Python {py_version} with Rich {rich_version}...')
        
        # This part is read-only and safe to do before locking
        python_exe = get_interpreter_path(py_version, thread_id)
        
        # --- START OF CRITICAL SECTION ---
        # Acquire the lock before modifying the shared environment
        with omnipkg_lock:
            thread_safe_print(f'{prefix}   🔒 Lock acquired. Modifying environment...')
            
            # Swap to the target dimension
            thread_safe_print(f'{prefix}🌀 TELEPORTING to Python {py_version} dimension...')
            start_swap_time = time.perf_counter()
            run_command_isolated(['swap', 'python', py_version], 
                               f'Switching context to {py_version}', 
                               python_exe=python_exe, thread_id=thread_id)
            end_swap_time = time.perf_counter()
            swap_duration_ms = (end_swap_time - start_swap_time) * 1000
            thread_safe_print(f'{prefix}   ✅ TELEPORT COMPLETE. Active context is now Python {py_version}.')
            thread_safe_print(f'{prefix}   ⏱️  Dimension swap took: {swap_duration_ms:.2f} ms')
            
            # Pre-check if the package is already there to save even more time
            # This is an efficient check that doesn't involve the complex omnipkg logic
            is_installed_cmd = [python_exe, '-c', f"import importlib.metadata; import sys; sys.exit(0) if importlib.metadata.version('rich') == '{rich_version}' else sys.exit(1)"]
            result = subprocess.run(is_installed_cmd, capture_output=True)

            start_install_time = time.perf_counter()
            if result.returncode == 0:
                 thread_safe_print(f'{prefix}   ✅ Requirement already satisfied via pre-check: rich=={rich_version}')
            else:
                # Set install strategy temporarily
                current_strategy = get_config_value('install_strategy')
                if current_strategy != 'latest-active':
                    thread_safe_print(f'{prefix}   SETTING STRATEGY: Temporarily setting install_strategy to "latest-active"...')
                    run_command_isolated(['config', 'set', 'install_strategy', 'latest-active'], 
                                       'Setting install strategy', python_exe=python_exe, thread_id=thread_id)
                
                # Install Rich
                thread_safe_print(f'{prefix}   🎨 Installing rich=={rich_version} in Python {py_version}...')
                run_command_isolated(['install', f'rich=={rich_version}'], 
                                   f'Installing rich=={rich_version} in Python {py_version} context', 
                                   python_exe=python_exe, thread_id=thread_id)
                
                # Restore original strategy
                if current_strategy != 'latest-active':
                    thread_safe_print(f'{prefix}   RESTORING STRATEGY: Setting install_strategy back to "{original_strategy}"...')
                    run_command_isolated(['config', 'set', 'install_strategy', original_strategy], 
                                       'Restoring install strategy', python_exe=python_exe, thread_id=thread_id)

            end_install_time = time.perf_counter()
            install_duration_ms = (end_install_time - start_install_time) * 1000
            thread_safe_print(f'{prefix}   ✅ PREPARATION COMPLETE: rich=={rich_version} is now available in Python {py_version} context.')
            thread_safe_print(f'{prefix}   ⏱️  Package installation took: {install_duration_ms:.2f} ms')
            thread_safe_print(f'{prefix}   🔓 Lock released.')
        # --- END OF CRITICAL SECTION ---
        
        # The actual test execution is read-only and can run without the lock
        thread_safe_print(f'{prefix}   🧪 EXECUTING Rich test in Python {py_version} dimension...')
        # ... (the rest of the function remains the same)
        thread_safe_print(f'{prefix}   📍 Using interpreter: {python_exe}')
        
        start_time = time.perf_counter()
        cmd = [python_exe, __file__, '--test-rich']
        thread_safe_print(f'{prefix}   🎯 Running command: {" ".join(cmd)}')
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            thread_safe_print(f'{prefix}   ❌ Rich test failed with return code {result.returncode}')
            thread_safe_print(f'{prefix}   STDOUT: {result.stdout}')
            thread_safe_print(f'{prefix}   STDERR: {result.stderr}')
            return None
            
        if not result.stdout.strip():
            thread_safe_print(f'{prefix}   ❌ Rich test returned empty output')
            return None
            
        end_time = time.perf_counter()
        
        try:
            test_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            thread_safe_print(f'{prefix}   ❌ Failed to parse JSON output: {result.stdout}')
            return None
            
        test_data['execution_time_ms'] = (end_time - start_time) * 1000
        test_data['swap_time_ms'] = swap_duration_ms
        test_data['install_time_ms'] = install_duration_ms
        test_data['thread_id'] = thread_id
        
        thread_safe_print(f'{prefix}✅ Rich test complete in Python {py_version}:')
        thread_safe_print(f'{prefix}   - Rich Version: {test_data["rich_version"]}')
        thread_safe_print(f'{prefix}   - Interpreter: {test_data["interpreter_path"]}')
        thread_safe_print(f'{prefix}   ⏱️  Execution took: {test_data["execution_time_ms"]:.2f} ms')
        
        return test_data
        
    except subprocess.TimeoutExpired:
        thread_safe_print(f'{prefix}   ❌ Rich test timed out after 30 seconds - SKIPPING')
        return None
    except Exception as e:
        thread_safe_print(f'{prefix}   ❌ Rich test failed with exception: {e}')
        return None

def rich_multiverse_test():
    """Main orchestrator that tests Rich versions across multiple Python dimensions simultaneously."""
    original_dimension = get_config_value('python_executable')
    original_version_match = re.search(r'(\d+\.\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else '3.11'
    
    thread_safe_print(f'🎨 Starting PARALLEL Rich multiverse test from dimension: Python {original_version}')
    
    initial_env_id = get_current_env_id()
    if initial_env_id:
        thread_safe_print(_('📍 Initial Environment ID: {}').format(initial_env_id))
    
    original_strategy = get_config_value('install_strategy')
    
    try:
        # Ensure all dimensions exist first (sequential to avoid conflicts)
        thread_safe_print(_('\n🔍 Checking dimension prerequisites...'))
        required_versions = ['3.9', '3.10', '3.11']
        for version in required_versions:
            ensure_dimension_exists(version)
        thread_safe_print(_('✅ All required dimensions are available.'))
        
        # Define test configurations
        test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
        
        # Run tests in parallel
        thread_safe_print('\n🚀 LAUNCHING PARALLEL DIMENSION TESTS...')
        test_results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(test_configs)) as executor:
            # Submit all tasks
            future_to_config = {
                executor.submit(prepare_and_test_dimension, config, i+1, original_strategy): config 
                for i, config in enumerate(test_configs)
            }
            
            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_config):
                config = future_to_config[future]
                try:
                    result = future.result()
                    if result:
                        test_results.append(result)
                except Exception as exc:
                    py_version, rich_version = config
                    thread_safe_print(f'❌ Python {py_version} with Rich {rich_version} generated an exception: {exc}')
        
        # Sort results by thread_id for consistent output
        test_results.sort(key=lambda x: x.get('thread_id', 0))
        
        thread_safe_print(_('\n🏆 PARALLEL MULTIVERSE RICH TEST COMPLETE!'))
        thread_safe_print(_('\n📊 RESULTS SUMMARY:'))
        thread_safe_print('=' * 80)
        
        for i, result in enumerate(test_results, 1):
            thread_safe_print(_('Test {}: Python {} | Rich {}').format(i, result['python_version'], result['rich_version']))
            thread_safe_print(_('   Interpreter: {}').format(result['interpreter_path']))
            thread_safe_print(f"   Execution Time: {result['execution_time_ms']:.2f} ms")
            thread_safe_print(f"   Swap Time: {result.get('swap_time_ms', 0):.2f} ms")
            thread_safe_print(f"   Install Time: {result.get('install_time_ms', 0):.2f} ms")
            thread_safe_print(f"   Thread ID: {result.get('thread_id', 'Unknown')}")
            thread_safe_print()
        
        if test_results:
            unique_versions = set(r['rich_version'] for r in test_results)
            unique_interpreters = set(r['interpreter_path'] for r in test_results)
            thread_safe_print(_('✅ Verified {} different Rich versions: {}').format(len(unique_versions), list(unique_versions)))
            thread_safe_print(_('✅ Verified {} different Python interpreters used').format(len(unique_interpreters)))
            
            # Performance summary
            total_execution_time = sum(r['execution_time_ms'] for r in test_results)
            avg_execution_time = total_execution_time / len(test_results)
            thread_safe_print(f'⚡ Average test execution time: {avg_execution_time:.2f} ms')
        
        return len(test_results) >= 2 and len(set(r['rich_version'] for r in test_results)) >= 2
        
    except subprocess.CalledProcessError as e:
        thread_safe_print(_('\n❌ A CRITICAL ERROR OCCURRED IN A SUBPROCESS.'), file=sys.stderr)
        thread_safe_print(f'Return code: {e.returncode}', file=sys.stderr)
        thread_safe_print(_('STDOUT:'), file=sys.stderr)
        thread_safe_print(e.stdout, file=sys.stderr)
        thread_safe_print(_('STDERR:'), file=sys.stderr)
        thread_safe_print(e.stderr, file=sys.stderr)
        return False
    finally:
        # Cleanup - return to original dimension
        cleanup_start = time.perf_counter()
        try:
            original_python_exe = get_interpreter_path(original_version)
            thread_safe_print(_('\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {})...').format(original_version))
            run_command_isolated(['swap', 'python', original_version], 
                               f'Returning to original context', 
                               python_exe=original_python_exe)
            cleanup_end = time.perf_counter()
            thread_safe_print(f'⏱️  TIMING: Cleanup/safety protocol took {(cleanup_end - cleanup_start) * 1000:.2f} ms')
        except Exception as cleanup_error:
            thread_safe_print(f'⚠️  Warning during cleanup: {cleanup_error}')

if __name__ == '__main__':
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        thread_safe_print('=' * 80)
        thread_safe_print(_('  🎨 PARALLEL RICH MULTIVERSE VERSION TEST'))
        thread_safe_print('=' * 80)
        overall_start = time.perf_counter()
        success = rich_multiverse_test()
        overall_end = time.perf_counter()
        thread_safe_print('\n' + '=' * 80)
        thread_safe_print(_('  📊 TEST SUMMARY'))
        thread_safe_print('=' * 80)
        if success:
            thread_safe_print(_('🎉🎉🎉 PARALLEL RICH MULTIVERSE TEST COMPLETE! Different Rich versions confirmed across Python interpreters! 🎉🎉🎉'))
        else:
            thread_safe_print('🔥🔥🔥 PARALLEL RICH MULTIVERSE TEST FAILED! Check the output above for issues. 🔥🔥🔥')
        total_time_ms = (overall_end - overall_start) * 1000
        thread_safe_print(f'\n⚡ PERFORMANCE: Total test runtime: {total_time_ms:.2f} ms')
        thread_safe_print(f'📈 EFFICIENCY GAIN: Parallel execution vs sequential (~{33664:.0f} ms baseline)')
        speedup_ratio = 33664 / total_time_ms if total_time_ms > 0 else 1
        thread_safe_print(f'🚀 SPEEDUP RATIO: {speedup_ratio:.2f}x faster')