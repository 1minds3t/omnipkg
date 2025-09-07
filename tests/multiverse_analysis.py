import sys
import os
import subprocess
import json
import re
from pathlib import Path
import time

# --- PAYLOAD FUNCTIONS ---
# (These remain unchanged)
def run_legacy_payload():
    """This function's code will be executed by the Python 3.9 interpreter."""
    import scipy.signal
    import numpy
    import json
    import sys

    print(f"--- Executing in Python {sys.version[:3]} with SciPy {scipy.__version__} ---", file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    analysis_result = {"result": int(scipy.signal.convolve(data, data).sum())}
    print(json.dumps(analysis_result))

def run_modern_payload(legacy_data_json: str):
    """This function's code will be executed by the Python 3.11 interpreter."""
    import tensorflow as tf
    import json
    import sys

    print(f"--- Executing in Python {sys.version[:3]} with TensorFlow {tf.__version__} ---", file=sys.stderr)
    input_data = json.loads(legacy_data_json)
    legacy_value = input_data['result']
    prediction = "SUCCESS" if legacy_value > 50 else "FAILURE"
    final_result = {"prediction": prediction}
    print(json.dumps(final_result))

# --- ORCHESTRATOR FUNCTIONS ---

def get_config_value(key: str) -> str:
    """Gets a specific value from the omnipkg config."""
    result = subprocess.run(["omnipkg", "config", "view"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(key):
            return line.split(":", 1)[1].strip()
    return "stable-main" if key == "install_strategy" else ""

def ensure_dimension_exists(version: str):
    """Ensures a specific Python version is adopted by omnipkg before use."""
    print(f"   VALIDATING DIMENSION: Ensuring Python {version} is adopted...")
    try:
        cmd = ["omnipkg", "python", "adopt", version]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"   ✅ VALIDATION COMPLETE: Python {version} is available.")
    except subprocess.CalledProcessError as e:
        print(f"❌ FAILED TO ADOPT DIMENSION {version}!", file=sys.stderr)
        print("--- Subprocess STDERR ---", file=sys.stderr); print(e.stderr, file=sys.stderr)
        raise

def swap_dimension(version: str):
    """Swaps the global omnipkg context to the target dimension and measures the time taken."""
    print(f"\n🌀 TELEPORTING to Python {version} dimension...")
    
    start_time = time.perf_counter()
    
    subprocess.run(["omnipkg", "swap", "python", version], check=True, capture_output=True)
    
    end_time = time.perf_counter()
    duration_ms = (end_time - start_time) * 1000
    
    print(f"   ✅ TELEPORT COMPLETE. Active context is now Python {version}.")
    print(f"   ⏱️  Dimension swap took: {duration_ms:.2f} ms")

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python dimension."""
    print(f"   LOCKING ON to Python {version} dimension...")
    result = subprocess.run(["omnipkg", "info", "python"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith(f"• Python {version}"):
            match = re.search(r":\s*(/\S+)", line)
            if match:
                path = match.group(1).strip()
                print(f"   LOCK CONFIRMED: Target is at {path}")
                return path
    raise RuntimeError(f"Could not find managed Python {version} via 'omnipkg info python'.")

def prepare_dimension_in_context(packages: list):
    """Installs packages into the CURRENTLY ACTIVE dimension (must be called after swap)."""
    print(f"   PREPARING CURRENT DIMENSION: Installing {', '.join(packages)}...")
    
    start_time = time.perf_counter()
    
    original_strategy = get_config_value("install_strategy")
    try:
        if original_strategy != 'latest-active':
            subprocess.run(["omnipkg", "config", "set", "install_strategy", "latest-active"], check=True, capture_output=True)
        
        cmd = ["omnipkg", "install"] + packages
        subprocess.run(cmd, check=True) 
        
    finally:
        current_strategy = get_config_value("install_strategy")
        if current_strategy != original_strategy:
            print(f"   RESTORING STRATEGY: Setting install_strategy back to '{original_strategy}'...")
            subprocess.run(["omnipkg", "config", "set", "install_strategy", original_strategy], check=True, capture_output=True)
    
    end_time = time.perf_counter()
    duration_ms = (end_time - start_time) * 1000
    
    print(f"   PREPARATION COMPLETE: {', '.join(packages)} are now available in current context.")
    print(f"   ⏱️  Package installation took: {duration_ms:.2f} ms")

def multiverse_analysis():
    """The main orchestrator function that controls the entire workflow."""
    original_dimension = get_config_value("python_executable")
    original_version_match = re.search(r'(\d+\.\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else "3.11"
    
    print(f"🚀 Starting multiverse analysis from dimension: Python {original_version}")

    try:
        # Check prerequisites first
        print("\n🔍 Checking dimension prerequisites...")
        ensure_dimension_exists("3.9")
        ensure_dimension_exists("3.11")
        print("✅ All required dimensions are available.")

        # ===============================================================
        #  MISSION STEP 1: JUMP TO PYTHON 3.9 DIMENSION & PREPARE IT
        # ===============================================================
        swap_dimension("3.9")  # CRITICAL: Swap BEFORE preparing
        prepare_dimension_in_context(["numpy", "scipy"])  # Now installs in 3.9 context
        python_3_9_exe = get_interpreter_path("3.9")

        print("   EXECUTING PAYLOAD in 3.9 dimension...")
        start_time = time.perf_counter()
        cmd = [python_3_9_exe, __file__, '--run-legacy']
        result_3_9 = subprocess.run(cmd, capture_output=True, text=True, check=True)
        end_time = time.perf_counter()
        legacy_data = json.loads(result_3_9.stdout)
        print("✅ Artifact retrieved from 3.9: Scipy analysis complete.")
        print(f"   - Result: {legacy_data['result']}")
        print(f"   ⏱️  3.9 payload execution took: {(end_time - start_time) * 1000:.2f} ms")
        
        # ===============================================================
        #  MISSION STEP 2: JUMP TO PYTHON 3.11 DIMENSION & PREPARE IT
        # ===============================================================
        swap_dimension("3.11")  # CRITICAL: Swap BEFORE preparing
        prepare_dimension_in_context(["tensorflow"])  # Now installs in 3.11 context
        python_3_11_exe = get_interpreter_path("3.11")

        print("   EXECUTING PAYLOAD in 3.11 dimension...")
        start_time = time.perf_counter()
        cmd = [python_3_11_exe, __file__, '--run-modern', json.dumps(legacy_data)]
        result_3_11 = subprocess.run(cmd, capture_output=True, text=True, check=True)
        end_time = time.perf_counter()
        final_prediction = json.loads(result_3_11.stdout)
        print("✅ Artifact processed by 3.11: TensorFlow prediction complete.")
        print(f"   ⏱️  3.11 payload execution took: {(end_time - start_time) * 1000:.2f} ms")

        # ===================================================================
        #  MISSION COMPLETE
        # ===================================================================
        print("\n🏆 MISSION SUCCESSFUL!")
        print(f"   - Final Prediction from Multiverse Workflow: '{final_prediction['prediction']}'")

    except subprocess.CalledProcessError as e:
        print("\n❌ A CRITICAL ERROR OCCURRED IN A SUBPROCESS.", file=sys.stderr)
        print(f"COMMAND: {' '.join(e.cmd)}", file=sys.stderr)
        print(f"EXIT CODE: {e.returncode}", file=sys.stderr)
        print("\n--- STDOUT ---", file=sys.stderr); print(e.stdout, file=sys.stderr)
        print("\n--- STDERR ---", file=sys.stderr); print(e.stderr, file=sys.stderr)
        sys.exit(1)
    finally:
        # --- SAFETY PROTOCOL: Always return to the original dimension ---
        active_dimension_path = get_config_value("python_executable")
        if original_dimension not in active_dimension_path:
            print(f"\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {original_version})...")
            swap_dimension(original_version)

if __name__ == "__main__":
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
    elif '--run-modern' in sys.argv:
        legacy_json_arg = sys.argv[sys.argv.index('--run-modern') + 1]
        run_modern_payload(legacy_json_arg)
    else:
        multiverse_analysis()