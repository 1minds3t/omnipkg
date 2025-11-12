import sys
import os
import subprocess
import json
import re
from pathlib import Path
import time
import traceback
try:
    from omnipkg.common_utils import safe_print
except ImportError:
    # Fallback if run in a weird context before omnipkg is in path
    def safe_print(*args, **kwargs):
        print(*args, **kwargs)

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.common_utils import sync_context_to_runtime

# This guard ensures the bootstrap logic ONLY runs for the main orchestrator process.
# Any subprocesses spawned by this script (including self-invocations for payloads)
# will inherit the 'OMNIPKG_MAIN_ORCHESTRATOR_PID' and skip this block.
# --- NEW ROBUST BOOTSTRAP SECTION ---
# This guard ensures the bootstrap logic ONLY runs for the main orchestrator process.
if 'OMNIPKG_MAIN_ORCHESTRATOR_PID' not in os.environ:
    os.environ['OMNIPKG_MAIN_ORCHESTRATOR_PID'] = str(os.getpid())

    print("--- BOOTSTRAP: Main orchestrator process detected. Forcing Python 3.11 context. ---")
    
    # Check if we are already running on Python 3.11
    if sys.version_info[:2] != (3, 11):
        print(f"   - Current Python is {sys.version_info.major}.{sys.version_info.minor}. Relaunching is required.")
        
        # We cannot trust the omnipkg config. We must find 3.11 ourselves.
        # This is a simplified, direct way to find the executable.
        try:
            from omnipkg.core import ConfigManager
            cm = ConfigManager(suppress_init_messages=True)
            omnipkg_instance = OmnipkgCore(config_manager=cm)
            target_exe = omnipkg_instance.interpreter_manager.config_manager.get_interpreter_for_version("3.11")
            
            if not target_exe or not target_exe.exists():
                print("   - Python 3.11 not found, attempting to adopt it first...")
                if omnipkg_instance.adopt_interpreter("3.11") != 0:
                    raise RuntimeError("Failed to adopt Python 3.11 for the test orchestrator.")
                target_exe = omnipkg_instance.interpreter_manager.config_manager.get_interpreter_for_version("3.11")

            if not target_exe or not target_exe.exists():
                 raise RuntimeError("Could not find a managed Python 3.11 to run the test.")

            print(f"   - Found Python 3.11 at: {target_exe}")
            print("   - Relaunching orchestrator...")
            
            # Relaunch THIS script using the found 3.11 executable.
            # This completely bypasses the user's current swapped context.
            os.execve(str(target_exe), [str(target_exe)] + sys.argv, os.environ)

        except Exception as e:
            print(f"FATAL BOOTSTRAP ERROR: Could not relaunch into Python 3.11. Error: {e}")
            sys.exit(1)
            
    # If we reach here, we are guaranteed to be running under Python 3.11.
    # Now, sync the omnipkg configuration to this reality.
    print("--- BOOTSTRAP: Now running in Python 3.11. Aligning omnipkg context. ---")
    sync_context_to_runtime()

# NOW we can import the rest after bootstrap
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
# --- START OF THE FIX ---
except ImportError as e:
    print(f"FATAL: Could not import omnipkg modules after bootstrap. Is the project installed? Error: {e}")
    sys.exit(1)
# --- END OF THE FIX ---

# --- PAYLOAD FUNCTIONS (These run in separate processes) ---
def run_legacy_payload():
    """Simulates a legacy task requiring older numpy/scipy."""
    import scipy.signal
    import numpy
    import json
    import sys
    print(f"--- Executing in Python {sys.version.split()[0]} with SciPy {scipy.__version__} & NumPy {numpy.__version__} ---", file=sys.stderr)
    data = numpy.array([1, 2, 3, 4, 5])
    analysis_result = {"result": int(scipy.signal.convolve(data, data).sum())}
    print(json.dumps(analysis_result))

def run_modern_payload(legacy_data_json: str):
    """Simulates a modern task requiring TensorFlow and its dependencies."""
    import tensorflow as tf
    import json
    import sys
    print(f"--- Executing in Python {sys.version.split()[0]} with TensorFlow {tf.__version__} ---", file=sys.stderr)
    input_data = json.loads(legacy_data_json)
    legacy_value = input_data['result']
    # Simple logic: if the legacy result is what we expect (~225), predict SUCCESS
    prediction = "SUCCESS" if legacy_value > 200 else "FAILURE"
    final_result = {"prediction": prediction}
    print(json.dumps(final_result))

# --- ORCHESTRATOR HELPER FUNCTIONS ---
def run_command_with_isolated_context(command, description, check=True):
    """
    Runs a command with isolated omnipkg context (no auto-alignment).
    This prevents the parent script's context from interfering with subcommands.
    """
    safe_print(f"\n>> Executing: {description}")
    print(f" Command: {' '.join(command)}")
    print(" --- Live Output ---")
    
    # Create a clean environment that prevents context auto-alignment
    env = os.environ.copy()
    
    # Remove any context forcing variables from parent process
    env.pop('OMNIPKG_FORCE_CONTEXT', None)
    env.pop('OMNIPKG_RELAUNCHED', None)
    
    # Set flags to disable auto-alignment in subprocesses
    env['OMNIPKG_DISABLE_AUTO_ALIGN'] = '1'
    env['OMNIPKG_SUBPROCESS_MODE'] = '1'
    
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        universal_newlines=True,
        env=env
    )
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        if stripped_line:
            print(f" | {stripped_line}")
        output_lines.append(line)
    process.stdout.close()
    return_code = process.wait()
    print(" -------------------")
    safe_print(f" [OK] Command finished with exit code: {return_code}")
    full_output = "".join(output_lines)
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=full_output)
    return full_output

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python interpreter."""
    print(f"\n Finding interpreter path for Python {version}...")
    output = run_command_with_isolated_context(["omnipkg", "info", "python"], "Querying interpreters")
    for line in output.splitlines():
        if re.search(f"Python {version}", line):
            match = re.search(r":\s*(/\S+)", line)
            if match:
                path = match.group(1).strip()
                safe_print(f" [OK] Found at: {path}")
                return path
    raise RuntimeError(f"Could not find managed Python {version}.")

def install_packages_with_omnipkg(packages: list, description: str):
    """Uses omnipkg to install packages into the current context."""
    safe_print(f"\n [TOOL] {description}")
    run_command_with_isolated_context(["omnipkg", "install"] + packages, f"Installing {' '.join(packages)}")

# --- MAIN ORCHESTRATOR ---
def multiverse_analysis():
    original_version = "3.11"
    safe_print(f"[START] Starting multiverse analysis from dimension: Python {original_version}")
    
    # === STEP 1: PYTHON 3.9 CONTEXT ===
    safe_print("\n[STEP 1] MISSION STEP 1: Setting up Python 3.9 dimension...")
    run_command_with_isolated_context(["omnipkg", "swap", "python", "3.9"], "Swapping to Python 3.9 context")
    python_3_9_exe = get_interpreter_path("3.9")
    install_packages_with_omnipkg(
        ["numpy<2", "scipy"],
        "Installing legacy packages for Python 3.9"
    )
    
    safe_print("\n [TEST] Executing legacy payload in Python 3.9...")
    result_3_9 = subprocess.run(
        [python_3_9_exe, __file__, '--run-legacy'], 
        capture_output=True, 
        text=True, 
        check=False
    )
    
    # Debug output to see what we actually got
    print(f"DEBUG: Exit code: {result_3_9.returncode}")
    print(f"DEBUG: Stdout: '{result_3_9.stdout}'")
    print(f"DEBUG: Stderr: '{result_3_9.stderr}'")
    
    if result_3_9.returncode != 0:
        safe_print(f"[ERROR] Legacy payload failed with exit code {result_3_9.returncode}")
        print(f"Stderr: {result_3_9.stderr}")
        raise RuntimeError(f"Legacy payload execution failed")
    
    if not result_3_9.stdout.strip():
        raise RuntimeError("Legacy payload produced no output")
    
    # Try to extract JSON from the last line that looks like JSON
    json_lines = [line.strip() for line in result_3_9.stdout.splitlines() if line.strip().startswith('{')]
    if not json_lines:
        raise RuntimeError(f"No JSON output found in legacy payload. Output was: {result_3_9.stdout}")
    
    legacy_data = json.loads(json_lines[-1])
    safe_print(f"[OK] Artifact retrieved from 3.9: Scipy analysis complete. Result: {legacy_data['result']}")
    
    # === STEP 2: PYTHON 3.11 CONTEXT ===
    safe_print("\n[STEP 2] MISSION STEP 2: Setting up Python 3.11 dimension...")
    run_command_with_isolated_context(["omnipkg", "swap", "python", "3.11"], "Swapping back to Python 3.11 context")
    install_packages_with_omnipkg(
        ["tensorflow"],
        "Installing modern packages for Python 3.11"
    )
    
    safe_print("\n [TEST] Executing modern payload using 'omnipkg run' to trigger auto-healing...")
    omnipkg_run_command = [
        "omnipkg", "run", __file__, '--run-modern', json.dumps(legacy_data)
    ]
    modern_output = run_command_with_isolated_context(
        omnipkg_run_command,
        "Executing modern payload with auto-healing enabled"
    )
    
    # The actual JSON result is the last line of the script's output that is a JSON object.
    json_output = [line for line in modern_output.splitlines() if line.strip().startswith('{')][-1]
    final_prediction = json.loads(json_output)
    safe_print(f"[OK] Artifact processed by 3.11: TensorFlow prediction complete. Prediction: '{final_prediction['prediction']}'")
    
    return final_prediction['prediction'] == 'SUCCESS'

if __name__ == "__main__":
    # This logic allows the script to call itself to run the payloads.
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
        sys.exit(0)
    elif '--run-modern' in sys.argv:
        json_arg_index = sys.argv.index('--run-modern') + 1
        run_modern_payload(sys.argv[json_arg_index])
        sys.exit(0)
    
    # Main orchestrator logic starts here when no flags are present...
    safe_print("=" * 80 + "\n [START] OMNIPKG MULTIVERSE ANALYSIS TEST\n" + "=" * 80)
    start_time = time.perf_counter()
    success = False
    
    try:
        success = multiverse_analysis()
    except Exception as e:
        safe_print(f"\n[ERROR] An error occurred during the analysis: {e}")
        traceback.print_exc()
    
    end_time = time.perf_counter()
    safe_print("\n" + "=" * 80 + "\n [SUMMARY] TEST SUMMARY\n" + "=" * 80)
    if success:
        safe_print("[SUCCESS] MULTIVERSE ANALYSIS COMPLETE! Context switching, package management, and auto-healing working perfectly!")
    else:
        safe_print("[FAILED] MULTIVERSE ANALYSIS FAILED! Check the output above for issues.")
    safe_print(f"\n[PERFORMANCE] Total test runtime: {(end_time - start_time):.2f} seconds")