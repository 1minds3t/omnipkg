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
from omnipkg.i18n import _
from typing import Optional
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.core import ConfigManager
except ImportError as e:
    safe_print(f'FATAL: Could not import omnipkg modules. Make sure this script is placed correctly. Error: {e}')
    sys.exit(1)

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

def run_command_with_streaming(cmd_args, description, python_exe=None):
    """Runs a command with live streaming output."""
    safe_print(_('\n▶️  Executing: {}').format(description))
    executable = python_exe or sys.executable
    cmd = [executable, '-m', 'omnipkg.cli'] + cmd_args
    safe_print(_('   Command: {}').format(' '.join(cmd)))
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    full_output = (result.stdout + result.stderr).strip()
    for line in full_output.splitlines():
        safe_print(_('   | {}').format(line))
    if result.returncode != 0:
        safe_print(f'   ⚠️  WARNING: Command finished with non-zero exit code: {result.returncode}')
    return (full_output, result.returncode)

def get_current_env_id():
    """Gets the current environment ID from omnipkg config."""
    try:
        cm = ConfigManager(suppress_init_messages=True)
        return cm.env_id
    except Exception as e:
        safe_print(_('⚠️  Could not get environment ID: {}').format(e))
        return None

def get_config_value(key: str) -> str:
    """Gets a specific value from the omnipkg config."""
    result = subprocess.run(['omnipkg', 'config', 'view'], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(key):
            return line.split(':', 1)[1].strip()
    return 'stable-main' if key == 'install_strategy' else ''

def ensure_dimension_exists(version: str):
    """Ensures a specific Python version is adopted by omnipkg before use."""
    safe_print(_('   VALIDATING DIMENSION: Ensuring Python {} is adopted...').format(version))
    try:
        cmd = ['omnipkg', 'python', 'adopt', version]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        safe_print(_('   ✅ VALIDATION COMPLETE: Python {} is available.').format(version))
    except subprocess.CalledProcessError as e:
        safe_print(_('❌ FAILED TO ADOPT DIMENSION {}!').format(version), file=sys.stderr)
        safe_print(_('--- Subprocess STDERR ---'), file=sys.stderr)
        safe_print(e.stderr, file=sys.stderr)
        raise

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python dimension."""
    safe_print(_('   LOCKING ON to Python {} dimension...').format(version))
    result = subprocess.run(['omnipkg', 'info', 'python'], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(f'• Python {version}'):
            match = re.search(':\\s*(/\\S+)', line)
            if match:
                path = match.group(1).strip()
                safe_print(_('   LOCK CONFIRMED: Target is at {}').format(path))
                return path
    raise RuntimeError(_("Could not find managed Python {} via 'omnipkg info python'.").format(version))

def prepare_dimension_with_rich(version: str, rich_version: str):
    """Swaps to a dimension and installs a specific rich version."""
    safe_print(_('   PREPARING DIMENSION {}: Installing rich=={}...').format(version, rich_version))
    python_exe = get_interpreter_path(version)
    safe_print(_('🌀 TELEPORTING to Python {} dimension...').format(version))
    start_swap_time = time.perf_counter()
    run_command_with_streaming(['swap', 'python', version], _('Switching context to {}').format(version), python_exe=python_exe)
    end_swap_time = time.perf_counter()
    swap_duration_ms = (end_swap_time - start_swap_time) * 1000
    safe_print(_('   ✅ TELEPORT COMPLETE. Active context is now Python {}.').format(version))
    safe_print(f'   ⏱️  Dimension swap took: {swap_duration_ms:.2f} ms')
    env_id = get_current_env_id()
    if env_id:
        safe_print(_('   📍 Operating in Environment ID: {}').format(env_id))
    start_install_time = time.perf_counter()
    original_strategy = get_config_value('install_strategy')
    try:
        if original_strategy != 'latest-active':
            safe_print(_("   SETTING STRATEGY: Temporarily setting install_strategy to 'latest-active'..."))
            run_command_with_streaming(['config', 'set', 'install_strategy', 'latest-active'], 'Setting install strategy', python_exe=python_exe)
        safe_print(_('\n   🎨 Installing rich=={} in Python {}...').format(rich_version, version))
        output, _ = run_command_with_streaming(['install', f'rich=={rich_version}'], _('Installing rich=={} in Python {} context').format(rich_version, version), python_exe=python_exe)
    finally:
        current_strategy = get_config_value('install_strategy')
        if current_strategy != original_strategy:
            safe_print(_("   RESTORING STRATEGY: Setting install_strategy back to '{}'...").format(original_strategy))
            run_command_with_streaming(['config', 'set', 'install_strategy', original_strategy], 'Restoring install strategy', python_exe=python_exe)
    end_install_time = time.perf_counter()
    install_duration_ms = (end_install_time - start_install_time) * 1000
    safe_print(_('   ✅ PREPARATION COMPLETE: rich=={} is now available in Python {} context.').format(rich_version, version))
    safe_print(f'   ⏱️  Package installation took: {install_duration_ms:.2f} ms')

def rich_multiverse_test():
    """Main orchestrator that tests Rich versions across multiple Python dimensions."""
    original_dimension = get_config_value('python_executable')
    original_version_match = re.search('(\\d+\\.\\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else '3.11'
    safe_print(f'🎨 Starting Rich multiverse test from dimension: Python {original_version}')
    initial_env_id = get_current_env_id()
    if initial_env_id:
        safe_print(_('📍 Initial Environment ID: {}').format(initial_env_id))
    test_results = []
    try:
        safe_print(_('\n🔍 Checking dimension prerequisites...'))
        ensure_dimension_exists('3.9')
        ensure_dimension_exists('3.10')
        ensure_dimension_exists('3.11')
        safe_print(_('✅ All required dimensions are available.'))
        test_configs = [('3.9', '13.4.2'), ('3.10', '13.6.0'), ('3.11', '13.7.1')]
        for py_version, rich_version in test_configs:
            safe_print(f'\n📦 TESTING DIMENSION: Python {py_version} with Rich {rich_version}...')
            prepare_dimension_with_rich(py_version, rich_version)
            python_exe = get_interpreter_path(py_version)
            safe_print(_('   🧪 EXECUTING Rich test in Python {} dimension...').format(py_version))
            safe_print(_('   📍 Using interpreter: {}').format(python_exe))
            start_time = time.perf_counter()
            try:
                cmd = [python_exe, __file__, '--test-rich']
                safe_print(_('   🎯 Running command: {}').format(' '.join(cmd)))
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    safe_print(f'   ❌ Rich test failed with return code {result.returncode}')
                    safe_print(_('   STDOUT: {}').format(result.stdout))
                    safe_print(_('   STDERR: {}').format(result.stderr))
                    continue
                if not result.stdout.strip():
                    safe_print(_('   ❌ Rich test returned empty output'))
                    continue
            except subprocess.TimeoutExpired:
                safe_print(_('   ❌ Rich test timed out after 30 seconds - SKIPPING'))
                continue
            except Exception as e:
                safe_print(f'   ❌ Rich test failed with exception: {e}')
                continue
            end_time = time.perf_counter()
            try:
                test_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                safe_print(_('   ❌ Failed to parse JSON output: {}').format(result.stdout))
                continue
            test_data['execution_time_ms'] = (end_time - start_time) * 1000
            test_results.append(test_data)
            safe_print(_('✅ Rich test complete in Python {}:').format(py_version))
            safe_print(_('   - Rich Version: {}').format(test_data['rich_version']))
            safe_print(_('   - Interpreter: {}').format(test_data['interpreter_path']))
            safe_print(f"   ⏱️  Execution took: {test_data['execution_time_ms']:.2f} ms")
        safe_print(_('\n🏆 MULTIVERSE RICH TEST COMPLETE!'))
        safe_print(_('\n📊 RESULTS SUMMARY:'))
        safe_print('=' * 80)
        for i, result in enumerate(test_results, 1):
            safe_print(_('Test {}: Python {} | Rich {}').format(i, result['python_version'], result['rich_version']))
            safe_print(_('   Interpreter: {}').format(result['interpreter_path']))
            safe_print(f"   Execution Time: {result['execution_time_ms']:.2f} ms")
            safe_print()
        if test_results:
            unique_versions = set((r['rich_version'] for r in test_results))
            unique_interpreters = set((r['interpreter_path'] for r in test_results))
            safe_print(_('✅ Verified {} different Rich versions: {}').format(len(unique_versions), list(unique_versions)))
            safe_print(_('✅ Verified {} different Python interpreters used').format(len(unique_interpreters)))
        return len(test_results) >= 2 and len(set((r['rich_version'] for r in test_results))) >= 2
    except subprocess.CalledProcessError as e:
        safe_print(_('\n❌ A CRITICAL ERROR OCCURRED IN A SUBPROCESS.'), file=sys.stderr)
        safe_print(f'Return code: {e.returncode}', file=sys.stderr)
        safe_print(_('STDOUT:'), file=sys.stderr)
        safe_print(e.stdout, file=sys.stderr)
        safe_print(_('STDERR:'), file=sys.stderr)
        safe_print(e.stderr, file=sys.stderr)
        return False
    finally:
        cleanup_start = time.perf_counter()
        original_python_exe = get_interpreter_path(original_version)
        safe_print(_('\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {})...').format(original_version))
        run_command_with_streaming(['swap', 'python', original_version], f'Returning to original context', python_exe=original_python_exe)
        cleanup_end = time.perf_counter()
        safe_print(f'⏱️  TIMING: Cleanup/safety protocol took {(cleanup_end - cleanup_start) * 1000:.2f} ms')
if __name__ == '__main__':
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        safe_print('=' * 80)
        safe_print(_('  🎨 RICH MULTIVERSE VERSION TEST'))
        safe_print('=' * 80)
        overall_start = time.perf_counter()
        success = rich_multiverse_test()
        overall_end = time.perf_counter()
        safe_print('\n' + '=' * 80)
        safe_print(_('  📊 TEST SUMMARY'))
        safe_print('=' * 80)
        if success:
            safe_print(_('🎉🎉🎉 RICH MULTIVERSE TEST COMPLETE! Different Rich versions confirmed across Python interpreters! 🎉🎉🎉'))
        else:
            safe_print('🔥🔥🔥 RICH MULTIVERSE TEST FAILED! Check the output above for issues. 🔥🔥🔥')
        total_time_ms = (overall_end - overall_start) * 1000
        safe_print(f'\n⚡ PERFORMANCE: Total test runtime: {total_time_ms:.2f} ms')