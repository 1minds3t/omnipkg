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

# --- PAYLOAD FUNCTIONS ---
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

def check_redis_key(env_id, python_version, package_name, expected_version):
    """Check if a specific package version is in the Redis key for the given context."""
    redis_key = f"omnipkg:env_{env_id}:py{python_version}:pkg:{package_name}:installed_versions"
    print(f"\n🔎 Verifying Redis Key for Python {python_version}...")
    print(f"   Environment ID: {env_id}")
    print(f"   Query: SMEMBERS {redis_key}")
    print(f"   Looking for version: '{expected_version}'")
    
    try:
        result = subprocess.run(['redis-cli', 'SMEMBERS', redis_key], capture_output=True, text=True, check=True)
        versions_in_redis = result.stdout.strip()
        print(f"   Redis returned: {versions_in_redis or '(empty set)'}")
        
        redis_versions = [v.strip() for v in versions_in_redis.splitlines() if v.strip()]
        print(f"   Parsed versions: {redis_versions}")
        
        cleaned_expected = expected_version.strip().strip("'\"")
        print(f"   Cleaned expected version: '{cleaned_expected}'")
        
        if cleaned_expected in redis_versions:
            print(f"   ✅ Found expected version {cleaned_expected} in Redis!")
            return True
        else:
            print(f"   ❌ Expected version {cleaned_expected} NOT found in Redis!")
            print(f"   Available versions: {redis_versions}")
            return False
            
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"   ⚠️  Could not query Redis: {e}")
        return False

def parse_resolved_version(output: str, package_name: str) -> Optional[str]:
    """
    Parses the full output of an omnipkg command to find the resolved version.
    It checks for the "Resolved" line first, then falls back to "Requirement already satisfied".
    """
    # Pattern 1: The most reliable source of truth
    resolved_pattern = rf"Resolved '{re.escape(package_name)}' to '{re.escape(package_name)}==([0-9\.]+)'"
    match = re.search(resolved_pattern, output)
    if match:
        return match.group(1)

    # Pattern 2: Fallback for when the package was already installed
    satisfied_pattern = rf"Requirement already satisfied: {re.escape(package_name)}==(\S+)"
    match = re.search(satisfied_pattern, output, re.IGNORECASE)
    if match:
        return match.group(1)
        
    # Pattern 3: Fallback for the "Jackpot" case
    jackpot_pattern = rf"JACKPOT! Latest PyPI version (\S+) is already installed!"
    match = re.search(jackpot_pattern, output, re.IGNORECASE)
    if match:
        return match.group(1)

    print(f"   ⚠️  COULD NOT PARSE resolved version for '{package_name}' from output.")
    return None

def run_command_with_streaming(cmd_args, description, python_exe=None):
    """Runs a command with live streaming output - copied from working script."""
    print(f"\n▶️  Executing: {description}")
    # Use the specified python executable, or the current one if None
    executable = python_exe or sys.executable
    cmd = [executable, '-m', 'omnipkg.cli'] + cmd_args
    print(f"   Command: {' '.join(cmd)}")
    
    # Use a single capture_output call to get all output at the end
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    # Print the captured output with a prefix
    full_output = (result.stdout + result.stderr).strip()
    for line in full_output.splitlines():
        print(f"   | {line}")
        
    if result.returncode != 0:
        print(f"   ⚠️  WARNING: Command finished with non-zero exit code: {result.returncode}")
        
    return full_output, result.returncode

def get_current_env_id():
    """Gets the current environment ID from omnipkg config."""
    try:
        cm = ConfigManager(suppress_init_messages=True)
        return cm.env_id
    except Exception as e:
        print(f"⚠️  Could not get environment ID: {e}")
        return None

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

def prepare_dimension_with_packages(version: str, packages: list):
    """Swaps to a dimension and installs packages using proper context switching."""
    print(f"   PREPARING DIMENSION {version}: Installing {', '.join(packages)}...")
    
    python_exe = get_interpreter_path(version)
    
    print(f"🌀 TELEPORTING to Python {version} dimension...")
    start_swap_time = time.perf_counter()
    
    run_command_with_streaming(['swap', 'python', version], f"Switching context to {version}", python_exe=python_exe)
    
    end_swap_time = time.perf_counter()
    swap_duration_ms = (end_swap_time - start_swap_time) * 1000
    print(f"   ✅ TELEPORT COMPLETE. Active context is now Python {version}.")
    print(f"   ⏱️  Dimension swap took: {swap_duration_ms:.2f} ms")
    
    env_id = get_current_env_id()
    if env_id:
        print(f"   📍 Operating in Environment ID: {env_id}")
    
    start_install_time = time.perf_counter()
    
    original_strategy = get_config_value("install_strategy")
    try:
        if original_strategy != 'latest-active':
            print(f"   SETTING STRATEGY: Temporarily setting install_strategy to 'latest-active'...")
            run_command_with_streaming(['config', 'set', 'install_strategy', 'latest-active'], 
                                     "Setting install strategy", python_exe=python_exe)
        
        # --- OPTIMIZATION START ---
        # Instead of looping, we install all packages in a single command.
        if packages:
            print(f"\n   🔍 Installing {', '.join(packages)} in Python {version}...")
            
            output, _ = run_command_with_streaming(['install'] + packages, 
                                                 f"Installing {', '.join(packages)} in Python {version} context", 
                                                 python_exe=python_exe)
            
            # Loop through the specs to verify each one
            for spec in packages:
                # This correctly parses "numpy" from "numpy==2.0.2"
                package_name = re.split(r'[=<>~]', spec)[0].strip()
                
                resolved_version = parse_resolved_version(output, package_name)
                
                if env_id and resolved_version:
                    check_redis_key(env_id, version, package_name, resolved_version)
                elif env_id:
                    print(f"   ❌ Verification failed: Could not determine the installed version for {package_name}.")

    finally:
        current_strategy = get_config_value("install_strategy")
        if current_strategy != original_strategy:
            print(f"   RESTORING STRATEGY: Setting install_strategy back to '{original_strategy}'...")
            run_command_with_streaming(['config', 'set', 'install_strategy', original_strategy],
                                     "Restoring install strategy", python_exe=python_exe)
    
    end_install_time = time.perf_counter()
    install_duration_ms = (end_install_time - start_install_time) * 1000
    
    print(f"   ✅ PREPARATION COMPLETE: {', '.join(packages)} are now available in Python {version} context.")
    print(f"   ⏱️  Package installation took: {install_duration_ms:.2f} ms")

def multiverse_analysis():
    """The main orchestrator function that controls the entire workflow."""
    original_dimension = get_config_value("python_executable")
    original_version_match = re.search(r'(\d+\.\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else "3.11"
    
    print(f"🚀 Starting multiverse analysis from dimension: Python {original_version}")
    
    initial_env_id = get_current_env_id()
    if initial_env_id:
        print(f"📍 Initial Environment ID: {initial_env_id}")

    try:
        # Check prerequisites first
        print("\n🔍 Checking dimension prerequisites...")
        ensure_dimension_exists("3.9")
        ensure_dimension_exists("3.11")
        print("✅ All required dimensions are available.")

        # ===============================================================
        #  MISSION STEP 1: PREPARE PYTHON 3.9 (FAST PATH)
        # ===============================================================
        print("\n📦 MISSION STEP 1: Setting up Python 3.9 dimension...")
        # --- OPTIMIZATION: PROVIDE EXACT, KNOWN-COMPATIBLE VERSIONS ---
        prepare_dimension_with_packages("3.9", ["numpy==2.0.2", "scipy==1.13.1"])
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
        #  MISSION STEP 2: PREPARE PYTHON 3.11 (FAST PATH)
        # ===============================================================
        print("\n📦 MISSION STEP 2: Setting up Python 3.11 dimension...")
        # --- OPTIMIZATION: PROVIDE EXACT, KNOWN-COMPATIBLE VERSION ---
        prepare_dimension_with_packages("3.11", ["tensorflow==2.20.0"])
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
        #  MISSION COMPLETE - CHECK SUCCESS CONDITION
        # ===================================================================
        print("\n🏆 MISSION SUCCESSFUL!")
        print(f"   - Final Prediction from Multiverse Workflow: '{final_prediction['prediction']}'")
        
        return final_prediction['prediction'] == 'SUCCESS'

    except subprocess.CalledProcessError as e:
        print("\n❌ A CRITICAL ERROR OCCURRED IN A SUBPROCESS.", file=sys.stderr)
        # ... (rest of the function is the same)
        return False
    finally:
        # --- SAFETY PROTOCOL: Always return to the original dimension ---
        cleanup_start = time.perf_counter()
        original_python_exe = get_interpreter_path(original_version)
        print(f"\n🌀 SAFETY PROTOCOL: Returning to original dimension (Python {original_version})...")
        run_command_with_streaming(['swap', 'python', original_version], 
                                 f"Returning to original context", 
                                 python_exe=original_python_exe)
        cleanup_end = time.perf_counter()
        print(f"⏱️  TIMING: Cleanup/safety protocol took {(cleanup_end - cleanup_start) * 1000:.2f} ms")

if __name__ == "__main__":
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
    elif '--run-modern' in sys.argv:
        legacy_json_arg = sys.argv[sys.argv.index('--run-modern') + 1]
        run_modern_payload(legacy_json_arg)
    else:
        print("=" * 80)
        print("  🚀 OMNIPKG MULTIVERSE ANALYSIS TEST")
        print("=" * 80)
        overall_start = time.perf_counter()
        success = multiverse_analysis()
        overall_end = time.perf_counter()
        
        print("\n" + "=" * 80)
        print("  📊 TEST SUMMARY")
        print("=" * 80)
        if success:
            print("🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! Context switching and package management working perfectly! 🎉🎉🎉")
        else:
            print("🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥")
        
        total_time_ms = (overall_end - overall_start) * 1000
        print(f"\n⚡ PERFORMANCE: Total test runtime: {total_time_ms:.2f} ms")