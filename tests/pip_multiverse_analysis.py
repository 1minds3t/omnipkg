# Final, clean version of tests/multiverse_analysis.py

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

# --- PAYLOAD FUNCTIONS (Unchanged) ---
def run_legacy_payload():
    import scipy.signal
    import numpy
    import json
    import sys
    print(f"--- Executing in Python {sys.version[:3]} with SciPy {scipy.__version__} ---", file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    analysis_result = {"result": int(scipy.signal.convolve(data, data).sum())}
    print(json.dumps(analysis_result))

def run_modern_payload(legacy_data_json: str):
    import tensorflow as tf
    import json
    import sys
    print(f"--- Executing in Python {sys.version[:3]} with TensorFlow {tf.__version__} ---", file=sys.stderr)
    input_data = json.loads(legacy_data_json)
    legacy_value = input_data['result']
    prediction = "SUCCESS" if legacy_value > 50 else "FAILURE"
    final_result = {"prediction": prediction}
    print(json.dumps(final_result))

# --- ORCHESTRATOR HELPER FUNCTIONS ---

def run_command(command, description, check=True, force_output=False):
    """
    Runs a command, provides live streaming output, AND returns the full output.
    """
    print(f"\n▶️  Executing: {description}")
    print(f"   Command: {' '.join(command)}")
    print("   --- Live Output ---")
    
    # For pip commands, we want to force verbose output and disable progress bars
    env = os.environ.copy()
    if force_output and 'pip' in ' '.join(command):
        # Force pip to show output even in non-interactive mode
        env['PIP_PROGRESS_BAR'] = 'off'
        # Add verbose flags if not already present
        if '-v' not in command and '--verbose' not in command:
            command = command + ['-v']
    
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # Merge stderr into stdout
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        universal_newlines=True,
        env=env
    )

    # Create a list to capture all output lines
    output_lines = []

    # Read, print, and capture output line by line
    try:
        for line in iter(process.stdout.readline, ''):
            if line:  # Only process non-empty lines
                stripped_line = line.rstrip('\n\r')
                if stripped_line:  # Only print non-empty stripped lines
                    print(f"   | {stripped_line}")
                output_lines.append(line)
    except Exception as e:
        print(f"   | Error reading output: {e}")
    
    process.stdout.close()
    return_code = process.wait()

    print("   -------------------")
    print(f"   ✅ Command finished with exit code: {return_code}")

    # Join the captured lines into a single string for parsing
    full_output = "".join(output_lines)

    if check and return_code != 0:
        # The output has already been printed, so we just raise the error
        raise subprocess.CalledProcessError(return_code, command, output=full_output)
    
    # Return the full captured output for functions that need it
    return full_output

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python interpreter."""
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

def install_packages_with_output(python_exe: str, packages: list, description: str):
    """Install packages with forced verbose output."""
    print(f"\n   Installing packages: {', '.join(packages)}")
    
    # Build the pip command with explicit verbose flags
    pip_command = [
        python_exe, "-u", "-m", "pip", "install", 
        "--verbose",  # Force verbose output
        "--no-cache-dir",  # Disable cache to see download progress
        "--progress-bar", "on"  # Force progress bar on
    ] + packages
    
    run_command(pip_command, description, force_output=True)

# --- MAIN ORCHESTRATOR ---

def multiverse_analysis():
    original_version = "3.11" 
    try:
        print(f"🚀 Starting multiverse analysis from dimension: Python {original_version}")

        # === STEP 1: PYTHON 3.9 ===
        print("\n📦 MISSION STEP 1: Setting up Python 3.9 dimension...")
        run_command(["omnipkg", "swap", "python", "3.9"], "Swapping to Python 3.9")
        python_3_9_exe = get_interpreter_path("3.9")
        
        # Use the new installation function with forced output
        install_packages_with_output(
            python_3_9_exe, 
            ["numpy<2", "scipy"], 
            "Installing packages for 3.9 with detailed output"
        )

        print("\n   Executing legacy payload in Python 3.9...")
        result_3_9 = subprocess.run([python_3_9_exe, __file__, '--run-legacy'], capture_output=True, text=True, check=True)
        legacy_data = json.loads(result_3_9.stdout)
        print(f"✅ Artifact retrieved from 3.9: Scipy analysis complete. Result: {legacy_data['result']}")

        # === STEP 2: PYTHON 3.11 ===
        print("\n📦 MISSION STEP 2: Setting up Python 3.11 dimension...")
        run_command(["omnipkg", "swap", "python", "3.11"], "Swapping to Python 3.11")
        python_3_11_exe = get_interpreter_path("3.11")
        
        # Use the new installation function with forced output
        install_packages_with_output(
            python_3_11_exe, 
            ["tensorflow"], 
            "Installing packages for 3.11 with detailed output"
        )
        
        print("\n   Executing modern payload in Python 3.11...")
        result_3_11 = subprocess.run([python_3_11_exe, __file__, '--run-modern', json.dumps(legacy_data)], capture_output=True, text=True, check=True)
        final_prediction = json.loads(result_3_11.stdout)
        print(f"✅ Artifact processed by 3.11: TensorFlow prediction complete. Prediction: '{final_prediction['prediction']}'")

        return final_prediction['prediction'] == 'SUCCESS'

    finally:
        # --- SAFETY PROTOCOL ---
        print(f"\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {original_version})...")
        run_command(["omnipkg", "swap", "python", original_version], "Returning to original context", check=False)

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
            print("🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! Context switching and package management working perfectly! 🎉🎉🎉")
        else:
            print("🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥")
        
        print(f"\n⚡ PERFORMANCE: Total test runtime: {(end_time - start_time) * 1000:.2f} ms")