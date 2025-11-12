try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import os
import json
import subprocess
import shutil
import time
from pathlib import Path
from importlib.metadata import version as get_pkg_version

# Assuming omnipkg modules are in the path
from omnipkg.core import omnipkg as OmnipkgCore, ConfigManager
from omnipkg.loader import omnipkgLoader
from omnipkg.common_utils import run_command, print_header

# --- [Helper functions from original script] ---

def print_with_flush(message):
    """Print with immediate flush."""
    safe_print(message, flush=True)

def run_subprocess_with_output(cmd, description='', show_output=True, timeout_hint=None):
    """Run subprocess with improved real-time output streaming."""
    print_with_flush(f'   🔄 {description}...')
    if timeout_hint:
        print_with_flush(('   ⏱️  Expected duration: ~{} seconds').format(timeout_hint))
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, universal_newlines=True, bufsize=1, encoding='utf-8')
        stdout_lines = []
        for line in process.stdout:
            if show_output and line.strip():
                print_with_flush(f'      {line.strip()}')
            stdout_lines.append(line)
        returncode = process.wait()
        stdout = ''.join(stdout_lines)
        return (returncode == 0, stdout, '')
    except Exception as e:
        print_with_flush(('   ❌ Subprocess failed: {}').format(e))
        return (False, '', str(e))

def omnipkg_clean_packages():
    """Uses omnipkg to cleanly uninstall numpy and scipy."""
    print_with_flush('   🧹 Using omnipkg to cleanly uninstall numpy and scipy...')
    for package in ['numpy', 'scipy']:
        run_subprocess_with_output(['omnipkg', 'uninstall', package, '-y'], f'Uninstalling {package} with omnipkg')
    print_with_flush('   ✅ Omnipkg clean complete.')
    return True

def omnipkg_install_baseline():
    """Use omnipkg to install baseline versions."""
    print_with_flush(('   📦 Using omnipkg to install baseline numpy==1.26.4 and scipy==1.16.1...'))
    packages = ['numpy==1.26.4', 'scipy==1.16.1']
    success, _, stderr = run_subprocess_with_output(['omnipkg', 'install'] + packages, 'Installing baseline packages', timeout_hint=60)
    if success:
        print_with_flush(('   ✅ omnipkg install baseline packages completed successfully'))
        return True
    else:
        print_with_flush(('   ❌ omnipkg install failed: {}').format(stderr))
        return False

### NEW AND IMPROVED PACKAGE CHECKER ###
# This function is inspired by your much more reliable example script.
def check_package_installed(python_exe: str, package: str, version: str):
    """
    Check if a specific package version is installed in the target python environment
    by running an isolated subprocess.
    """
    command = [
        python_exe, '-c',
        f"import importlib.metadata; import sys; "
        f"sys.exit(0) if importlib.metadata.version('{package}') == '{version}' else sys.exit(1)"
    ]
    result = subprocess.run(command, capture_output=True)
    return result.returncode == 0

### MODIFIED SETUP FUNCTION ###
def setup():
    """Prepares the environment, skipping setup if packages already exist."""
    print_header('STEP 1: Preparing Test Environment')
    sys.stdout.flush()

    config_manager = ConfigManager()
    # Get the Python executable that omnipkg considers active. This is the key change.
    python_exe = config_manager.config.get('active_python_executable', sys.executable)
    
    baseline_packages = {'numpy': '1.26.4', 'scipy': '1.16.1'}
    
    print_with_flush(f"   🧐 Checking for baseline packages in active env ({python_exe})...")
    
    # Use the new, robust check for each package
    all_installed = True
    for pkg, version in baseline_packages.items():
        if check_package_installed(python_exe, pkg, version):
            print_with_flush(f"      ✅ Found {pkg}=={version}")
        else:
            print_with_flush(f"      ❌ Did not find {pkg}=={version}")
            all_installed = False
            # No need to check further if one is missing
            break

    if all_installed:
        print_with_flush("\n   ✅ All baseline packages already installed. Skipping setup.")
        # Return dummy values as original_versions isn't needed when skipping
        return (config_manager, "skipped", {})

    # If not all packages are found, proceed with the original full setup
    print_with_flush("\n   ⚠️  Baseline packages not found or versions mismatch. Proceeding with full setup...")
    
    if not omnipkg_clean_packages():
        print_with_flush(f'   ❌ Failed to clean active packages with omnipkg')
        return (None, "error", {})

    if not omnipkg_install_baseline():
        print_with_flush(('   ❌ Failed to install baseline packages'))
        return (None, "error", {})

    print_with_flush('✅ Environment is clean and ready for testing.')
    return (config_manager, "completed", {})

def run_test():
    """The core of the OMNIPKG Nuclear Stress Test with combo testing."""
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config
    ROOT_DIR = Path(__file__).resolve().parent.parent

    print_with_flush(('\n💥 NUMPY VERSION JUGGLING:'))
    for numpy_ver in ['1.24.3', '1.26.4']:
        print_with_flush(('\n⚡ Switching to numpy=={}').format(numpy_ver))
        start_time = time.perf_counter()
        try:
            with omnipkgLoader(f'numpy=={numpy_ver}', config=omnipkg_config):
                import numpy as np
                activation_time = time.perf_counter() - start_time
                print_with_flush(('   ✅ Version: {}').format(np.__version__))
                print_with_flush(('   🔢 Array sum: {}').format(np.array([1, 2, 3]).sum()))
                print_with_flush(f'   ⚡ Activation time: {activation_time * 1000:.2f}ms')
                if np.__version__ != numpy_ver:
                    print_with_flush(('   ⚠️ WARNING: Expected {}, got {}!').format(numpy_ver, np.__version__))
                else:
                    print_with_flush(f'   🎯 Version verification: PASSED')
        except Exception as e:
            print_with_flush(f'   ❌ Activation/Test failed for numpy=={numpy_ver}: {e}!')

    print_with_flush(('\n\n🔥 SCIPY C-EXTENSION TEST:'))
    for scipy_ver in ['1.12.0', '1.16.1']:
        print_with_flush(('\n🌋 Switching to scipy=={}').format(scipy_ver))
        start_time = time.perf_counter()
        try:
            with omnipkgLoader(f'scipy=={scipy_ver}', config=omnipkg_config):
                import scipy as sp
                import scipy.sparse
                import scipy.linalg
                activation_time = time.perf_counter() - start_time
                print_with_flush(('   ✅ Version: {}').format(sp.__version__))
                print_with_flush(('   ♻️ Sparse matrix: {} non-zeros').format(sp.sparse.eye(3).nnz))
                print_with_flush(('   📐 Linalg det: {}').format(sp.linalg.det([[0, 2], [1, 1]])))
                print_with_flush(f'   ⚡ Activation time: {activation_time * 1000:.2f}ms')
                if sp.__version__ != scipy_ver:
                    print_with_flush(('   ⚠️ WARNING: Expected {}, got {}!').format(scipy_ver, sp.__version__))
                else:
                    print_with_flush(f'   🎯 Version verification: PASSED')
        except Exception as e:
            print_with_flush(f'   ❌ Activation/Test failed for scipy=={scipy_ver}: {e}!')

    # ADD THE COMBO TEST BACK HERE
    print_with_flush(('\n\n🤯 NUMPY + SCIPY VERSION MIXING:'))
    combos = [('1.24.3', '1.12.0'), ('1.26.4', '1.16.1')]
    temp_script_path = Path(os.getcwd()) / 'omnipkg_combo_test.py'
    
    for np_ver, sp_ver in combos:
        print_with_flush(('\\n🌀 COMBO: numpy=={} + scipy=={}').format(np_ver, sp_ver))
        combo_start_time = time.perf_counter()
        config_json_str = json.dumps(omnipkg_config)
        
        temp_script_content = f'''
import sys
import os
import json  # To load config
import importlib
import time
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

# Ensure omnipkg's root is in sys.path for importing its modules
sys.path.insert(0, r"{ROOT_DIR}")

# Load config in the subprocess
subprocess_config = json.loads('{config_json_str}')

def run_combo_test():
    start_time = time.perf_counter()
    
    # Retrieve bubble paths from the loaded config in the subprocess
    numpy_bubble_path = Path(subprocess_config['multiversion_base']) / f"numpy-{np_ver}"
    scipy_bubble_path = Path(subprocess_config['multiversion_base']) / f"scipy-{sp_ver}"

    # Manually construct PYTHONPATH for this specific test as it was originally designed
    # by prepending bubble paths to sys.path in this subprocess.
    bubble_paths_to_add = []
    if numpy_bubble_path.is_dir():
        bubble_paths_to_add.append(str(numpy_bubble_path))
    if scipy_bubble_path.is_dir():
        bubble_paths_to_add.append(str(scipy_bubble_path))
        
    # Prepend bubble paths to sys.path for this subprocess
    sys.path = bubble_paths_to_add + sys.path 
    
    safe_print("🔍 Python path (first 5 entries):", flush=True)
    for idx, path in enumerate(sys.path[:5]):
        print(f"   {{idx}}: {{path}}", flush=True)

    try:
        import numpy as np
        import scipy as sp
        import scipy.sparse
        
        setup_time = time.perf_counter() - start_time
        
        safe_print(f"   🧪 numpy: {{np.__version__}}, scipy: {{sp.__version__}}", flush=True)
        safe_print(f"   📍 numpy location: {{np.__file__}}", flush=True)
        safe_print(f"   📍 scipy location: {{sp.__file__}}", flush=True)
        safe_print(f"   ⚡ Setup time: {{setup_time*1000:.2f}}ms", flush=True)
        
        result = np.array([1,2,3]) @ sp.sparse.eye(3).toarray()
        safe_print(f"   🔗 Compatibility check: {{result}}", flush=True)
        
        # Version validation
        np_ok = False
        sp_ok = False
        try:
            if get_version('numpy') == "{np_ver}":
                np_ok = True
            else:
                safe_print(f"   ❌ Numpy version mismatch! Expected {np_ver}, got {{get_version('numpy')}}", file=sys.stderr, flush=True)
        except PackageNotFoundError:
            safe_print(f"   ❌ Numpy not found in subprocess!", file=sys.stderr, flush=True)

        try:
            if get_version('scipy') == "{sp_ver}":
                sp_ok = True
            else:
                safe_print(f"   ❌ Scipy version mismatch! Expected {sp_ver}, got {{get_version('scipy')}}", file=sys.stderr, flush=True)
        except PackageNotFoundError:
            safe_print(f"   ❌ Scipy not found in subprocess!", file=sys.stderr, flush=True)

        if np_ok and sp_ok:
            total_time = time.perf_counter() - start_time
            safe_print(f"   🎯 Version verification: BOTH PASSED!", flush=True)
            safe_print(f"   ⚡ Total combo time: {{total_time*1000:.2f}}ms", flush=True)
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        safe_print(f"   ❌ Test failed in subprocess: {{e}}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)

if __name__ == "__main__":
    run_combo_test()
'''
        try:
            with open(temp_script_path, 'w') as f:
                f.write(temp_script_content)
            
            success, stdout, stderr = run_subprocess_with_output(
                [sys.executable, str(temp_script_path)], 
                f'Running combo test for numpy=={np_ver} + scipy=={sp_ver}'
            )
            
            combo_total_time = time.perf_counter() - combo_start_time
            print_with_flush(f'   ⚡ Total combo execution: {combo_total_time * 1000:.2f}ms')
            
            if not success:
                print_with_flush(f'   ❌ Subprocess test failed for combo numpy=={np_ver} + scipy=={sp_ver}')
                if stderr:
                    print_with_flush(('   💥 Error: {}').format(stderr))
                sys.exit(1)
        except Exception as e:
            print_with_flush(('   ❌ An unexpected error occurred during combo test subprocess setup: {}').format(e))
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1)
        finally:
            if temp_script_path.exists():
                os.remove(temp_script_path)
    
    print_with_flush('\n\n🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇')


### MODIFIED CLEANUP FUNCTION ###
def cleanup(original_versions):
    """Skips cleanup to leave packages installed for faster re-testing."""
    print_header('STEP 4: Cleanup Phase')
    sys.stdout.flush()
    print_with_flush('   ✅ Cleanup skipped as requested.')
    print_with_flush('   💡 Packages and bubbles remain installed for faster subsequent test runs.')


def run():
    """Main entry point for the stress test."""
    original_versions = {}
    try:
        result = setup()
        if result[0] is None:
            return False
        
        config_manager, setup_status, original_versions = result
        
        # Only create bubbles if the full setup ran.
        if setup_status == "completed":
            print_header('STEP 2: Creating Test Bubbles with omnipkg')
            sys.stdout.flush()
            packages_to_bubble = ['numpy==1.24.3', 'scipy==1.12.0']
            for pkg in packages_to_bubble:
                print_with_flush(f'\n--- Creating bubble for {pkg} ---')
                success, _, _ = run_subprocess_with_output(['omnipkg', 'install', pkg], f'Creating bubble for {pkg}', timeout_hint=60)
                if not success:
                    print_with_flush(f'   ❌ Critical error: Failed to create bubble for {pkg}. Aborting test.')
                    return False
        
        print_header('STEP 3: Executing the Nuclear Test')
        sys.stdout.flush()
        run_test()
        return True
    except Exception as e:
        print_with_flush(('\n❌ A critical error occurred during the stress test: {}').format(e))
        import traceback
        traceback.print_exc()
        return False
    finally:
        # The modified cleanup will now be called here.
        cleanup(original_versions)

if __name__ == '__main__':
    success = run()
    sys.exit(0 if success else 1)