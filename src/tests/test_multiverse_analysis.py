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
import tempfile

# Use the system's temporary directory for logs, which is always writable.
# We give it a predictable name so we can find it later if needed for debugging.
temp_dir = Path(tempfile.gettempdir())
log_file = temp_dir / 'omnipkg_multiverse_log.jsonl'

# Ensure the log file is clean before we start a new run
if log_file.exists():
    log_file.unlink()

def log_result(step: str, data: dict):
    with open(log_file, 'a') as f:
        f.write(json.dumps({'timestamp': time.time(), 'step': step, **data}) + '\n')

def run_legacy_payload():
    import scipy.signal
    import numpy
    import json
    import sys
    safe_print(f'--- Executing in Python {sys.version[:3]} with SciPy {scipy.__version__} ---', file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    result = int(scipy.signal.convolve(data, data).sum())
    analysis_result = {'result': result}
    safe_print(json.dumps(analysis_result))
    log_result('legacy_payload', analysis_result)
    return analysis_result

def run_modern_payload(legacy_data_json: str):
    import tensorflow as tf
    import numpy as np
    import json
    import sys
    safe_print(f'--- Executing in Python {sys.version[:3]} with TensorFlow {tf.__version__} ---', file=sys.stderr)
    try:
        input_data = json.loads(legacy_data_json)
        legacy_value = input_data['result']
        data = np.array([1, 2, 3, 4, 5])
        numpy_result = int(np.convolve(data, data).sum())
        relative_error = abs(legacy_value - numpy_result) / max(legacy_value, 1e-10)
        validation_passed = relative_error < 1e-06
        normalized_input = legacy_value / 300.0
        with tf.device('/CPU:0'):
            tf_input = tf.constant([[float(normalized_input)]], dtype=tf.float32)
            model = tf.keras.Sequential([tf.keras.layers.Input(shape=(1,)), tf.keras.layers.Dense(10, activation='relu'), tf.keras.layers.Dense(1, activation='sigmoid', kernel_initializer=tf.keras.initializers.Constant(1.0))])
            prediction_score = float(model(tf_input).numpy()[0][0])
        prediction = 'SUCCESS' if legacy_value > 50 and validation_passed and (prediction_score > 0.5) else 'FAILURE'
        final_result = {'prediction': prediction, 'legacy_result': legacy_value, 'numpy_result': numpy_result, 'relative_error': relative_error, 'prediction_score': prediction_score, 'validation_passed': validation_passed}
        safe_print(json.dumps(final_result))
        log_result('modern_payload', final_result)
        return final_result
    except Exception as e:
        safe_print(_('Error in modern payload: {}').format(e), file=sys.stderr)
        sys.exit(1)

def run_command(command, description, check=True):
    safe_print(_('\n▶️  Executing: {}').format(description))
    safe_print(_('   Command: {}').format(' '.join(command)))
    safe_print(_('   --- Live Output ---'))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, universal_newlines=True, env=os.environ)
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

def install_packages_with_omnipkg(packages: list, description: str):
    safe_print(_('\n   🔧 Installing packages via OMNIPKG: {}').format(', '.join(packages)))
    omnipkg_command = ['omnipkg', 'install'] + packages
    run_command(omnipkg_command, description)

def multiverse_analysis():
    original_version = '3.11'
    timings = {}
    try:
        safe_print(f'🚀 Starting multiverse analysis from dimension: Python {original_version}')
        start_time = time.perf_counter()
        safe_print(_('\n📦 MISSION STEP 1: Setting up Python 3.9 dimension...'))
        run_command(['omnipkg', 'swap', 'python', '3.9'], 'Swapping to Python 3.9')
        timings['swap_3.9'] = time.perf_counter() - start_time
        python_3_9_exe = get_interpreter_path('3.9')
        install_packages_with_omnipkg(['numpy==1.26.4', 'scipy==1.13.1'], 'Installing numpy==1.26.4 and scipy==1.13.1 via omnipkg for Python 3.9')
        safe_print(_('\n   🧪 Executing legacy payload in Python 3.9...'))
        result_3_9 = subprocess.run([python_3_9_exe, __file__, '--run-legacy'], capture_output=True, text=True, check=True)
        legacy_data = json.loads(result_3_9.stdout)
        safe_print(f"✅ Artifact retrieved from 3.9: Scipy analysis complete. Result: {legacy_data['result']}")
        start_time = time.perf_counter()
        safe_print(_('\n📦 MISSION STEP 2: Setting up Python 3.11 dimension...'))
        run_command(['omnipkg', 'swap', 'python', '3.11'], 'Swapping to Python 3.11')
        timings['swap_3.11'] = time.perf_counter() - start_time
        get_interpreter_path('3.11')
        install_packages_with_omnipkg(['tensorflow==2.20.0'], 'Installing tensorflow==2.20.0 via omnipkg for Python 3.11')
        safe_print(_("\n   🧪 Executing modern payload in Python 3.11 using 'omnipkg run' to trigger auto-healing..."))
        omnipkg_run_command = ['omnipkg', 'run', __file__, '--run-modern', json.dumps(legacy_data)]
        try:
            modern_output = run_command(omnipkg_run_command, 'Executing modern payload with auto-healing enabled')
            json_output = [line for line in modern_output.splitlines() if line.strip().startswith('{')][-1]
            final_prediction = json.loads(json_output)
            safe_print(_("✅ Artifact processed by 3.11: TensorFlow prediction complete. Prediction: '{}'").format(final_prediction['prediction']))
            safe_print(f"   🔍 Cross-dimensional validation: SciPy result = {final_prediction['legacy_result']}, NumPy result = {final_prediction['numpy_result']}, Relative Error = {final_prediction['relative_error']:.6f}, Validation {('Passed' if final_prediction['validation_passed'] else 'Failed')}, TF Prediction Score = {final_prediction['prediction_score']:.4f}")
            timings['payload_3.11'] = time.perf_counter() - start_time
            return final_prediction['prediction'] == 'SUCCESS' and final_prediction['validation_passed']
        except subprocess.CalledProcessError as e:
            safe_print(f"❌ Modern payload failed even with 'omnipkg run'! Exit code: {e.returncode}")
            safe_print(_('   Output: {}').format(e.output))
            return False
    finally:
        start_time = time.perf_counter()
        safe_print(_('\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {})...').format(original_version))
        run_command(['omnipkg', 'swap', 'python', original_version], 'Returning to original context', check=False)
        timings['return_swap'] = time.perf_counter() - start_time
        log_result('timings', timings)

def plot_results():
    import json
    results = []
    if log_file.exists():
        with open(log_file, 'r') as f:
            for line in f:
                entry = json.loads(line)
                if entry['step'] == 'modern_payload':
                    results.append({'legacy_result': entry['legacy_result'], 'numpy_result': entry['numpy_result'], 'prediction_score': entry['prediction_score']})
    if results:
        latest_result = results[-1]
        return {'type': 'bar', 'data': {'labels': ['SciPy (3.9)', 'NumPy (3.11)', 'TF Prediction Score'], 'datasets': [{'label': 'Cross-Dimensional Results', 'data': [latest_result['legacy_result'], latest_result['numpy_result'], latest_result['prediction_score']], 'backgroundColor': ['#36A2EB', '#FF6384', '#4BC0C0'], 'borderColor': ['#36A2EB', '#FF6384', '#4BC0C0'], 'borderWidth': 1}]}, 'options': {'scales': {'y': {'beginAtZero': True, 'title': {'display': True, 'text': 'Value'}}, 'x': {'title': {'display': True, 'text': 'Payload'}}}, 'plugins': {'title': {'display': True, 'text': 'Cross-Dimensional Payload Comparison'}}}}
    return None
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
            safe_print(_('🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! Context switching, package management, and cross-dimensional validation working perfectly! 🎉🎉🎉'))
        else:
            safe_print('🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥')
        safe_print(f'\n⚡ PERFORMANCE: Total test runtime: {(end_time - start_time) * 1000:.2f} ms')
        chart_config = plot_results()
        if chart_config:
            safe_print(_('\n📊 Generated bar chart comparing SciPy, NumPy, and TensorFlow results. Visualize it in a Chart.js-compatible viewer!'))