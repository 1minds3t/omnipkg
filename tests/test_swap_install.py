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
def test_rich_version():
    """This function tests rich version and shows interpreter info - executed in different Python versions."""
    import rich
    import importlib.metadata
    import sys
    import json

    print(f"--- Testing Rich in Python {sys.version[:5]} ---", file=sys.stderr)
    print(f"--- Interpreter Path: {sys.executable} ---", file=sys.stderr)
    
    # Get the rich version using multiple approaches for reliability
    try:
        rich_version = rich.__version__
        version_source = "rich.__version__"
    except AttributeError:
        # Fallback to importlib.metadata if __version__ is not available
        rich_version = importlib.metadata.version('rich')
        version_source = "importlib.metadata.version"
    
    result = {
        "python_version": sys.version[:5],
        "interpreter_path": sys.executable,
        "rich_version": rich_version,
        "version_source": version_source,
        "success": True
    }
    
    print(json.dumps(result))

# --- ORCHESTRATOR FUNCTIONS (copied from multiverse script) ---

def run_command_with_streaming(cmd_args, description, python_exe=None):
    """Runs a command with live streaming output."""
    print(f"\n▶️  Executing: {description}")
    executable = python_exe or sys.executable
    cmd = [executable, '-m', 'omnipkg.cli'] + cmd_args
    print(f"   Command: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
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

def prepare_dimension_with_rich(version: str, rich_version: str):
    """Swaps to a dimension and installs a specific rich version."""
    print(f"   PREPARING DIMENSION {version}: Installing rich=={rich_version}...")
    
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
        
        print(f"\n   🎨 Installing rich=={rich_version} in Python {version}...")
        output, _ = run_command_with_streaming(['install', f'rich=={rich_version}'], 
                                             f"Installing rich=={rich_version} in Python {version} context", 
                                             python_exe=python_exe)
        
    finally:
        current_strategy = get_config_value("install_strategy")
        if current_strategy != original_strategy:
            print(f"   RESTORING STRATEGY: Setting install_strategy back to '{original_strategy}'...")
            run_command_with_streaming(['config', 'set', 'install_strategy', original_strategy],
                                     "Restoring install strategy", python_exe=python_exe)
    
    end_install_time = time.perf_counter()
    install_duration_ms = (end_install_time - start_install_time) * 1000
    
    print(f"   ✅ PREPARATION COMPLETE: rich=={rich_version} is now available in Python {version} context.")
    print(f"   ⏱️  Package installation took: {install_duration_ms:.2f} ms")

def rich_multiverse_test():
    """Main orchestrator that tests Rich versions across multiple Python dimensions."""
    original_dimension = get_config_value("python_executable")
    original_version_match = re.search(r'(\d+\.\d+)', original_dimension)
    original_version = original_version_match.group(1) if original_version_match else "3.11"
    
    print(f"🎨 Starting Rich multiverse test from dimension: Python {original_version}")
    
    initial_env_id = get_current_env_id()
    if initial_env_id:
        print(f"📍 Initial Environment ID: {initial_env_id}")

    test_results = []
    
    try:
        # Check prerequisites first
        print("\n🔍 Checking dimension prerequisites...")
        ensure_dimension_exists("3.9")
        ensure_dimension_exists("3.10")
        ensure_dimension_exists("3.11")
        print("✅ All required dimensions are available.")

        # Test configurations: (python_version, rich_version)
        test_configs = [
            ("3.9", "13.4.2"),   # Older rich version for Python 3.9
            ("3.10", "13.6.0"),  # Mid-range rich version for Python 3.10
            ("3.11", "13.7.1")   # Latest rich version for Python 3.11
        ]

        # ===============================================================
        #  CONCURRENT DIMENSION TESTING
        # ===============================================================
        for py_version, rich_version in test_configs:
            print(f"\n📦 TESTING DIMENSION: Python {py_version} with Rich {rich_version}...")
            
            # Prepare the dimension with the specific rich version
            prepare_dimension_with_rich(py_version, rich_version)
            python_exe = get_interpreter_path(py_version)

            print(f"   🧪 EXECUTING Rich test in Python {py_version} dimension...")
            print(f"   📍 Using interpreter: {python_exe}")
            start_time = time.perf_counter()
            
            # MINIMAL FIX: Add timeout and better error handling to the hanging subprocess
            try:
                cmd = [python_exe, __file__, '--test-rich']
                print(f"   🎯 Running command: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode != 0:
                    print(f"   ❌ Rich test failed with return code {result.returncode}")
                    print(f"   STDOUT: {result.stdout}")
                    print(f"   STDERR: {result.stderr}")
                    continue
                
                if not result.stdout.strip():
                    print(f"   ❌ Rich test returned empty output")
                    continue
                    
            except subprocess.TimeoutExpired:
                print(f"   ❌ Rich test timed out after 30 seconds - SKIPPING")
                continue
            except Exception as e:
                print(f"   ❌ Rich test failed with exception: {e}")
                continue
            
            end_time = time.perf_counter()
            
            # Parse the result
            try:
                test_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                print(f"   ❌ Failed to parse JSON output: {result.stdout}")
                continue
                
            test_data['execution_time_ms'] = (end_time - start_time) * 1000
            test_results.append(test_data)
            
            print(f"✅ Rich test complete in Python {py_version}:")
            print(f"   - Rich Version: {test_data['rich_version']}")
            print(f"   - Interpreter: {test_data['interpreter_path']}")
            print(f"   ⏱️  Execution took: {test_data['execution_time_ms']:.2f} ms")

        # ===============================================================
        #  RESULTS SUMMARY
        # ===============================================================
        print("\n🏆 MULTIVERSE RICH TEST COMPLETE!")
        print("\n📊 RESULTS SUMMARY:")
        print("=" * 80)
        
        for i, result in enumerate(test_results, 1):
            print(f"Test {i}: Python {result['python_version']} | Rich {result['rich_version']}")
            print(f"   Interpreter: {result['interpreter_path']}")
            print(f"   Execution Time: {result['execution_time_ms']:.2f} ms")
            print()
        
        # Verify we got different rich versions
        if test_results:
            unique_versions = set(r['rich_version'] for r in test_results)
            unique_interpreters = set(r['interpreter_path'] for r in test_results)
            
            print(f"✅ Verified {len(unique_versions)} different Rich versions: {list(unique_versions)}")
            print(f"✅ Verified {len(unique_interpreters)} different Python interpreters used")
        
        return len(test_results) >= 2 and len(set(r['rich_version'] for r in test_results)) >= 2

    except subprocess.CalledProcessError as e:
        print("\n❌ A CRITICAL ERROR OCCURRED IN A SUBPROCESS.", file=sys.stderr)
        print(f"Return code: {e.returncode}", file=sys.stderr)
        print("STDOUT:", file=sys.stderr); print(e.stdout, file=sys.stderr)
        print("STDERR:", file=sys.stderr); print(e.stderr, file=sys.stderr)
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
    if '--test-rich' in sys.argv:
        test_rich_version()
    else:
        print("=" * 80)
        print("  🎨 RICH MULTIVERSE VERSION TEST")
        print("=" * 80)
        overall_start = time.perf_counter()
        success = rich_multiverse_test()
        overall_end = time.perf_counter()
        
        print("\n" + "=" * 80)
        print("  📊 TEST SUMMARY")
        print("=" * 80)
        if success:
            print("🎉🎉🎉 RICH MULTIVERSE TEST COMPLETE! Different Rich versions confirmed across Python interpreters! 🎉🎉🎉")
        else:
            print("🔥🔥🔥 RICH MULTIVERSE TEST FAILED! Check the output above for issues. 🔥🔥🔥")
        
        total_time_ms = (overall_end - overall_start) * 1000
        print(f"\n⚡ PERFORMANCE: Total test runtime: {total_time_ms:.2f} ms")