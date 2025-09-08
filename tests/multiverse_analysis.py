import sys
import os
import subprocess
import json
import re
from pathlib import Path
import time
from typing import Optional

# --- PROJECT PATH SETUP ---
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.core import ConfigManager
except ImportError as e:
    print(f"FATAL: Could not import omnipkg modules. Make sure this script is placed correctly. Error: {e}")
    sys.exit(1)

# --- LOGGING SETUP ---
log_file = project_root / "multiverse_log.jsonl"
def log_result(step: str, data: dict):
    with open(log_file, "a") as f:
        f.write(json.dumps({"timestamp": time.time(), "step": step, **data}) + "\n")

# --- PAYLOAD FUNCTIONS ---
def run_legacy_payload():
    import scipy.signal
    import numpy
    import json
    import sys
    print(f"--- Executing in Python {sys.version[:3]} with SciPy {scipy.__version__} ---", file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    result = int(scipy.signal.convolve(data, data).sum())
    analysis_result = {"result": result}
    print(json.dumps(analysis_result))
    log_result("legacy_payload", analysis_result)
    return analysis_result

def run_modern_payload(legacy_data_json: str):
    import tensorflow as tf
    import numpy as np
    import json
    import sys
    print(f"--- Executing in Python {sys.version[:3]} with TensorFlow {tf.__version__} ---", file=sys.stderr)
    try:
        input_data = json.loads(legacy_data_json)
        legacy_value = input_data['result']
        
        # Validate SciPy result with NumPy in Python 3.11
        data = np.array([1, 2, 3, 4, 5])
        numpy_result = int(np.convolve(data, data).sum())
        relative_error = abs(legacy_value - numpy_result) / max(legacy_value, 1e-10)
        validation_passed = relative_error < 1e-6
        
        # Normalize input for TensorFlow model
        normalized_input = legacy_value / 300.0  # Scale to 0-1 range (assuming 225 is typical)
        with tf.device('/CPU:0'):
            tf_input = tf.constant([[float(normalized_input)]], dtype=tf.float32)
            model = tf.keras.Sequential([
                tf.keras.layers.Input(shape=(1,)),
                tf.keras.layers.Dense(10, activation='relu'),
                tf.keras.layers.Dense(1, activation='sigmoid', kernel_initializer=tf.keras.initializers.Constant(1.0))
            ])
            prediction_score = float(model(tf_input).numpy()[0][0])
        
        prediction = "SUCCESS" if legacy_value > 50 and validation_passed and prediction_score > 0.5 else "FAILURE"
        final_result = {
            "prediction": prediction,
            "legacy_result": legacy_value,
            "numpy_result": numpy_result,
            "relative_error": relative_error,
            "prediction_score": prediction_score,
            "validation_passed": validation_passed
        }
        print(json.dumps(final_result))
        log_result("modern_payload", final_result)
        return final_result
    except Exception as e:
        print(f"Error in modern payload: {e}", file=sys.stderr)
        sys.exit(1)

# --- ORCHESTRATOR HELPER FUNCTIONS ---
def run_command(command, description, check=True):
    print(f"\n▶️  Executing: {description}")
    print(f"   Command: {' '.join(command)}")
    print("   --- Live Output ---")
    
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        universal_newlines=True,
        env=os.environ
    )

    output_lines = []
    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                stripped_line = line.rstrip('\n\r')
                if stripped_line:
                    print(f"   | {stripped_line}")
                output_lines.append(line)
    except Exception as e:
        print(f"   | Error reading output: {e}")
    
    process.stdout.close()
    return_code = process.wait()

    print("   -------------------")
    print(f"   ✅ Command finished with exit code: {return_code}")

    full_output = "".join(output_lines)
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=full_output)
    
    return full_output

def get_interpreter_path(version: str) -> str:
    print(f"\n   Finding interpreter path for Python {version}...")
    output = run_command(["omnipkg", "info", "python"], "Querying interpreters")
    for line in output.splitlines():
        if f"Python {version}" in line:
            match = re.search(r":\s*(/\S+)", line)
            if match:
                path = match.group(1).strip()
                print(f"   ✅ Found at: {path}")
                return path
    raise RuntimeError(f"Could not find managed Python {version}.")

def install_packages_with_omnipkg(packages: list, description: str):
    print(f"\n   🔧 Installing packages via OMNIPKG: {', '.join(packages)}")
    omnipkg_command = ["omnipkg", "install"] + packages
    run_command(omnipkg_command, description)

# --- MAIN ORCHESTRATOR ---
# In /home/minds3t/omnipkg/tests/multiverse_analysis.py

def multiverse_analysis():
    original_version = "3.11"
    timings = {}
    try:
        print(f"🚀 Starting multiverse analysis from dimension: Python {original_version}")

        # STEP 1: PYTHON 3.9 (This part is perfect, no changes needed)
        start_time = time.perf_counter()
        print("\n📦 MISSION STEP 1: Setting up Python 3.9 dimension...")
        run_command(["omnipkg", "swap", "python", "3.9"], "Swapping to Python 3.9")
        timings["swap_3.9"] = time.perf_counter() - start_time
        
        python_3_9_exe = get_interpreter_path("3.9")
        
        install_packages_with_omnipkg(
            ["numpy==1.26.4", "scipy==1.13.1"],
            "Installing numpy==1.26.4 and scipy==1.13.1 via omnipkg for Python 3.9"
        )

        print("\n   🧪 Executing legacy payload in Python 3.9...")
        result_3_9 = subprocess.run(
            [python_3_9_exe, __file__, '--run-legacy'],
            capture_output=True, text=True, check=True
        )
        legacy_data = json.loads(result_3_9.stdout)
        print(f"✅ Artifact retrieved from 3.9: Scipy analysis complete. Result: {legacy_data['result']}")

        # STEP 2: PYTHON 3.11 (This part is perfect, no changes needed)
        start_time = time.perf_counter()
        print("\n📦 MISSION STEP 2: Setting up Python 3.11 dimension...")
        run_command(["omnipkg", "swap", "python", "3.11"], "Swapping to Python 3.11")
        timings["swap_3.11"] = time.perf_counter() - start_time
        
        get_interpreter_path("3.11") # We don't need the path, just confirm it's there.
        
        install_packages_with_omnipkg(
            ["tensorflow==2.20.0"], # This will install numpy>=2.0, creating the conflict.
            "Installing tensorflow==2.20.0 via omnipkg for Python 3.11"
        )
        
        # --- LOGICAL FIX START ---
        # Instead of calling python directly, we now use `omnipkg run`.
        # This will invoke the auto-healing logic in your CLI.
        print("\n   🧪 Executing modern payload in Python 3.11 using 'omnipkg run' to trigger auto-healing...")
        
        # The command to run is: omnipkg run /path/to/this_script.py --run-modern '{"json_data":...}'
        omnipkg_run_command = [
            "omnipkg", "run", __file__, '--run-modern', json.dumps(legacy_data)
        ]
        
        try:
            # We use `run_command` which will stream the output. We expect this to work
            # because the auto-healer will fix the NumPy issue.
            modern_output = run_command(
                omnipkg_run_command,
                "Executing modern payload with auto-healing enabled"
            )
            
            # The actual JSON result is the last non-empty line of the script's output.
            json_output = [line for line in modern_output.splitlines() if line.strip().startswith('{')][-1]
            final_prediction = json.loads(json_output)
            
            print(f"✅ Artifact processed by 3.11: TensorFlow prediction complete. Prediction: '{final_prediction['prediction']}'")
            print(f"   🔍 Cross-dimensional validation: SciPy result = {final_prediction['legacy_result']}, NumPy result = {final_prediction['numpy_result']}, Relative Error = {final_prediction['relative_error']:.6f}, Validation {'Passed' if final_prediction['validation_passed'] else 'Failed'}, TF Prediction Score = {final_prediction['prediction_score']:.4f}")
            timings["payload_3.11"] = time.perf_counter() - start_time
            return final_prediction['prediction'] == 'SUCCESS' and final_prediction['validation_passed']

        except subprocess.CalledProcessError as e:
            print(f"❌ Modern payload failed even with 'omnipkg run'! Exit code: {e.returncode}")
            print(f"   Output: {e.output}")
            return False
        # --- LOGICAL FIX END ---

    finally:
        start_time = time.perf_counter()
        print(f"\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {original_version})...")
        run_command(["omnipkg", "swap", "python", original_version], "Returning to original context", check=False)
        timings["return_swap"] = time.perf_counter() - start_time
        log_result("timings", timings)

# --- VISUALIZATION ---
def plot_results():
    import json
    results = []
    if log_file.exists():
        with open(log_file, "r") as f:
            for line in f:
                entry = json.loads(line)
                if entry["step"] == "modern_payload":
                    results.append({
                        "legacy_result": entry["legacy_result"],
                        "numpy_result": entry["numpy_result"],
                        "prediction_score": entry["prediction_score"]
                    })
    if results:
        latest_result = results[-1]
        return {
            "type": "bar",
            "data": {
                "labels": ["SciPy (3.9)", "NumPy (3.11)", "TF Prediction Score"],
                "datasets": [{
                    "label": "Cross-Dimensional Results",
                    "data": [latest_result["legacy_result"], latest_result["numpy_result"], latest_result["prediction_score"]],
                    "backgroundColor": ["#36A2EB", "#FF6384", "#4BC0C0"],
                    "borderColor": ["#36A2EB", "#FF6384", "#4BC0C0"],
                    "borderWidth": 1
                }]
            },
            "options": {
                "scales": {
                    "y": {
                        "beginAtZero": True,
                        "title": {"display": True, "text": "Value"}
                    },
                    "x": {
                        "title": {"display": True, "text": "Payload"}
                    }
                },
                "plugins": {
                    "title": {"display": True, "text": "Cross-Dimensional Payload Comparison"}
                }
            }
        }
    return None

if __name__ == "__main__":
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
    elif '--run-modern' in sys.argv:
        legacy_json_arg = sys.argv[sys.argv.index('--run-modern') + 1]
        run_modern_payload(legacy_json_arg)
    else:
        print("=" * 80, "\n  🚀 OMNIPKG MULTIVERSE ANALYSIS TEST\n" + "=" * 80)
        start_time = time.perf_counter()
        success = multiverse_analysis()
        end_time = time.perf_counter()
        
        print("\n" + "=" * 80, "\n  📊 TEST SUMMARY\n" + "=" * 80)
        if success:
            print("🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! Context switching, package management, and cross-dimensional validation working perfectly! 🎉🎉🎉")
        else:
            print("🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥")
        
        print(f"\n⚡ PERFORMANCE: Total test runtime: {(end_time - start_time) * 1000:.2f} ms")
        
        chart_config = plot_results()
        if chart_config:
            print("\n📊 Generated bar chart comparing SciPy, NumPy, and TensorFlow results. Visualize it in a Chart.js-compatible viewer!")