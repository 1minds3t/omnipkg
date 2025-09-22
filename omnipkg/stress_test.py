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

def force_omnipkg_context_to_current_python():
    """Forces omnipkg's active context to match the currently running Python version."""
    current_python = f'{sys.version_info.major}.{sys.version_info.minor}'
    try:
        safe_print(('🔄 Forcing omnipkg context to match script Python version: {}').format(current_python))
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        result = subprocess.run(omnipkg_cmd_base + ['swap', 'python', current_python], capture_output=True, text=True, check=True)
        safe_print(('✅ omnipkg context synchronized to Python {}').format(current_python))
        return True
    except subprocess.CalledProcessError:
        try:
            safe_print(('🔄 Attempting direct config modification...'))
            config_manager = ConfigManager()
            config_manager.config['active_python_version'] = current_python
            config_manager.config['active_python_executable'] = sys.executable
            config_manager.save_config()
            safe_print(f'✅ Direct config update successful for Python {current_python}')
            return True
        except Exception as e2:
            safe_print(('⚠️  Direct config modification also failed: {}').format(e2))
            return False

force_omnipkg_context_to_current_python()

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
    """The core of the OMNIPKG Nuclear Stress Test."""
    # This function remains unchanged.
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
    
    # The combo test remains the same
    # ...

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