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
from omnipkg.i18n import _
import time
from typing import Optional
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.core import ConfigManager
except ImportError as e:
    safe_print(f'FATAL: Could not import omnipkg modules. Make sure this script is placed correctly. Error: {e}')
    sys.exit(1)

def run_legacy_payload():
    import scipy.signal
    import numpy
    import json
    import sys
    safe_print(f'--- Executing in Python {sys.version[:3]} with SciPy {scipy.__version__} ---', file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    analysis_result = {'result': int(scipy.signal.convolve(data, data).sum())}
    safe_print(json.dumps(analysis_result))

def run_modern_payload(legacy_data_json: str):
    import tensorflow as tf
    import json
    import sys
    safe_print(f'--- Executing in Python {sys.version[:3]} with TensorFlow {tf.__version__} ---', file=sys.stderr)
    input_data = json.loads(legacy_data_json)
    legacy_value = input_data['result']
    prediction = 'SUCCESS' if legacy_value > 50 else 'FAILURE'
    final_result = {'prediction': prediction}
    safe_print(json.dumps(final_result))

def run_command(command, description, check=True, force_output=False):
    """
    Runs a command, provides live streaming output, AND returns the full output.
    """
    safe_print(_('\n▶️  Executing: {}').format(description))
    safe_print(_('   Command: {}').format(' '.join(command)))
    safe_print(_('   --- Live Output ---'))
    env = os.environ.copy()
    if force_output and 'pip' in ' '.join(command):
        env['PIP_PROGRESS_BAR'] = 'off'
        if '-v' not in command and '--verbose' not in command:
            command = command + ['-v']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, universal_newlines=True, env=env)
    output_lines = []
    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                stripped_line = line.rstrip('\n\r')
                if stripped_line:
                    safe_print(_('   | {}').format(stripped_line))
                output_lines.append(line)
    except Exception as e:
        safe_print(_('   | Error reading output: {}').format(e))
    process.stdout.close()
    return_code = process.wait()
    safe_print('   -------------------')
    safe_print(f'   ✅ Command finished with exit code: {return_code}')
    full_output = ''.join(output_lines)
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=full_output)
    return full_output

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python interpreter."""
    safe_print(f'\n   Finding interpreter path for Python {version}...')
    output = run_command(['omnipkg', 'info', 'python'], 'Querying interpreters')
    for line in output.splitlines():
        if f'Python {version}' in line:
            match = re.search(':\\s*(/\\S+)', line)
            if match:
                path = match.group(1).strip()
                safe_print(_('   ✅ Found at: {}').format(path))
                return path
    raise RuntimeError(_('Could not find managed Python {}.').format(version))

def install_packages_with_output(python_exe: str, packages: list, description: str):
    """Install packages with forced verbose output."""
    safe_print(_('\n   Installing packages: {}').format(', '.join(packages)))
    pip_command = [python_exe, '-u', '-m', 'pip', 'install', '--verbose', '--no-cache-dir', '--progress-bar', 'on'] + packages
    run_command(pip_command, description, force_output=True)

def multiverse_analysis():
    original_version = '3.11'
    try:
        safe_print(f'🚀 Starting multiverse analysis from dimension: Python {original_version}')
        safe_print(_('\n📦 MISSION STEP 1: Setting up Python 3.9 dimension...'))
        run_command(['omnipkg', 'swap', 'python', '3.9'], 'Swapping to Python 3.9')
        python_3_9_exe = get_interpreter_path('3.9')
        install_packages_with_output(python_3_9_exe, ['numpy<2', 'scipy'], 'Installing packages for 3.9 with detailed output')
        safe_print(_('\n   Executing legacy payload in Python 3.9...'))
        result_3_9 = subprocess.run([python_3_9_exe, __file__, '--run-legacy'], capture_output=True, text=True, check=True)
        legacy_data = json.loads(result_3_9.stdout)
        safe_print(f"✅ Artifact retrieved from 3.9: Scipy analysis complete. Result: {legacy_data['result']}")
        safe_print(_('\n📦 MISSION STEP 2: Setting up Python 3.11 dimension...'))
        run_command(['omnipkg', 'swap', 'python', '3.11'], 'Swapping to Python 3.11')
        python_3_11_exe = get_interpreter_path('3.11')
        install_packages_with_output(python_3_11_exe, ['tensorflow'], 'Installing packages for 3.11 with detailed output')
        safe_print(_('\n   Executing modern payload in Python 3.11...'))
        result_3_11 = subprocess.run([python_3_11_exe, __file__, '--run-modern', json.dumps(legacy_data)], capture_output=True, text=True, check=True)
        final_prediction = json.loads(result_3_11.stdout)
        safe_print(_("✅ Artifact processed by 3.11: TensorFlow prediction complete. Prediction: '{}'").format(final_prediction['prediction']))
        return final_prediction['prediction'] == 'SUCCESS'
    finally:
        safe_print(_('\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {})...').format(original_version))
        run_command(['omnipkg', 'swap', 'python', original_version], 'Returning to original context', check=False)
if __name__ == '__main__':
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
    elif '--run-modern' in sys.argv:
        legacy_json_arg = sys.argv[sys.argv.index('--run-modern') + 1]
        run_modern_payload(legacy_json_arg)
    else:
        safe_print('=' * 80, '\n  🚀 OMNIPKG MULTIVERSE ANALYSIS TEST\n' + '=' * 80)
        start_time = time.perf_counter()
        success = multiverse_analysis()
        end_time = time.perf_counter()
        safe_print('\n' + '=' * 80, '\n  📊 TEST SUMMARY\n' + '=' * 80)
        if success:
            safe_print(_('🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! Context switching and package management working perfectly! 🎉🎉🎉'))
        else:
            safe_print('🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥')
        safe_print(f'\n⚡ PERFORMANCE: Total test runtime: {(end_time - start_time) * 1000:.2f} ms')