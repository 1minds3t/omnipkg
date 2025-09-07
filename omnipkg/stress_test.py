import sys
import os
import json
import subprocess
import shutil
import tempfile
import time
import re
import importlib
import traceback
import importlib.util
from datetime import datetime
from pathlib import Path
from importlib.metadata import version as get_pkg_version, PathDistribution

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from omnipkg.i18n import _
lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

try:
    from omnipkg.core import omnipkg as OmnipkgCore, ConfigManager
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import run_command, print_header
except ImportError as e:
    print(f'❌ Failed to import omnipkg modules. Is it installed correctly? Error: {e}', flush=True)
    sys.exit(1)

def force_omnipkg_context_to_current_python():
    """
    Forces omnipkg's active context to match the currently running Python version.
    """
    current_python = f'{sys.version_info.major}.{sys.version_info.minor}'
    try:
        print(f'🔄 Forcing omnipkg context to match script Python version: {current_python}')
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        result = subprocess.run(omnipkg_cmd_base + ['swap', 'python', current_python], 
                              capture_output=True, text=True, check=True)
        print(f'✅ omnipkg context synchronized to Python {current_python}')
        return True
    except subprocess.CalledProcessError as e:
        print(f'⚠️  Could not synchronize omnipkg context via CLI: {e}')
        print(f'   CLI output: {e.stdout}')
        print(f'   CLI error: {e.stderr}')
        try:
            print(f'🔄 Attempting direct config modification...')
            config_manager = ConfigManager()
            python_exe = sys.executable
            config_manager.config['active_python_version'] = current_python
            config_manager.config['active_python_executable'] = python_exe
            config_manager.save_config()
            print(f'✅ Direct config update successful for Python {current_python}')
            return True
        except Exception as e2:
            print(f'⚠️  Direct config modification also failed: {e2}')
            print('   Proceeding anyway - this may cause issues with bubble operations')
            return False
    except Exception as e:
        print(f'⚠️  Unexpected error synchronizing omnipkg context: {e}')
        print('   Proceeding anyway - this may cause issues with bubble operations')
        return False

force_omnipkg_context_to_current_python()

def print_with_flush(message):
    """Print with immediate flush to avoid buffering issues"""
    print(message, flush=True)

def show_progress_dots():
    """Show progress dots for long operations"""
    print('   ', end='', flush=True)
    for i in range(3):
        time.sleep(0.5)
        print('.', end='', flush=True)
    print(' ', flush=True)

def run_subprocess_with_output(cmd, description='', show_output=True, timeout_hint=None):
    """
    Run subprocess with improved real-time output streaming
    Returns (success, stdout, stderr)
    """
    print_with_flush(f'   🔄 {description}...')
    if timeout_hint:
        print_with_flush(f'   ⏱️  Expected duration: ~{timeout_hint} seconds')
    
    try:
        # Use line buffering and ensure proper text handling
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,  # Merge stderr to stdout for better streaming
            text=True, 
            universal_newlines=True, 
            bufsize=0,  # Unbuffered
            env=dict(os.environ, PYTHONUNBUFFERED='1')  # Force Python unbuffered
        )
        
        stdout_lines = []
        last_output_time = time.time()
        
        while True:
            # Non-blocking read with small timeout
            try:
                line = process.stdout.readline()
                if line:
                    line = line.rstrip()
                    if show_output and line.strip():
                        print_with_flush(f'      {line}')
                    stdout_lines.append(line + '\n')
                    last_output_time = time.time()
                elif process.poll() is not None:
                    # Process finished
                    break
                else:
                    # No output but process still running
                    current_time = time.time()
                    if current_time - last_output_time > 5:  # 5 seconds without output
                        print_with_flush('   🔄 Installation in progress (this may take a while)...')
                        last_output_time = current_time
                    time.sleep(0.1)  # Small sleep to prevent busy waiting
            except Exception as e:
                print_with_flush(f'   ⚠️  Read error: {e}')
                break
        
        returncode = process.wait()
        stdout = ''.join(stdout_lines)
        
        return (returncode == 0, stdout, '')
        
    except Exception as e:
        print_with_flush(f'   ❌ Subprocess failed: {e}')
        return (False, '', str(e))

def run_subprocess_with_streaming(cmd, description='', use_heartbeat=False):
    """
    Alternative streaming function that appears to be missing from the original code
    Returns (success, stdout, stderr, returncode)
    """
    print_with_flush(f'   🔄 {description}...')
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            universal_newlines=True,
            bufsize=0,
            env=dict(os.environ, PYTHONUNBUFFERED='1')
        )
        
        stdout_lines = []
        last_output_time = time.time()
        
        while True:
            line = process.stdout.readline()
            if line:
                line = line.rstrip()
                if line.strip():
                    print_with_flush(f'      {line}')
                stdout_lines.append(line + '\n')
                last_output_time = time.time()
            elif process.poll() is not None:
                break
            else:
                if use_heartbeat:
                    current_time = time.time()
                    if current_time - last_output_time > 5:
                        print_with_flush('   ⚡ Still working...')
                        last_output_time = current_time
                time.sleep(0.1)
        
        returncode = process.wait()
        stdout = ''.join(stdout_lines)
        
        return (returncode == 0, stdout, '', returncode)
        
    except Exception as e:
        print_with_flush(f'   ❌ Subprocess failed: {e}')
        return (False, '', str(e), 1)

def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'multiversion')
    except:
        return 'multiversion'

def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        success, stdout, stderr = run_subprocess_with_output(
            ['omnipkg', 'config', 'set', 'install_strategy', strategy], 
            f'Setting install strategy to {strategy}', 
            show_output=False
        )
        if success:
            print_with_flush(f'   ⚙️  Install strategy set to: {strategy}')
            return True
        else:
            print_with_flush(f'   ⚠️  Failed to set install strategy: {stderr}')
            return False
    except Exception as e:
        print_with_flush(f'   ⚠️  Failed to set install strategy: {e}')
        return False

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print_with_flush(f'   🔄 Restoring original install strategy: {original_strategy}')
        return set_install_strategy(config_manager, original_strategy)
    return True

def get_installed_versions():
    """Get currently installed versions of numpy and scipy"""
    versions = {}
    packages = ['numpy', 'scipy']
    try:
        success, stdout, stderr = run_subprocess_with_output(
            ['pip', 'list', '--format=freeze'], 
            'Getting installed package versions', 
            show_output=False
        )
        if success:
            for line in stdout.splitlines():
                if '==' in line:
                    pkg_name, version = line.split('==', 1)
                    if pkg_name.lower() in packages:
                        versions[pkg_name.lower()] = version
                        print_with_flush(f'   📋 Found installed: {pkg_name}=={version}')
        return versions
    except Exception as e:
        print_with_flush(f'   ⚠️  Could not get installed versions: {e}')
        return {}

def pip_clean_packages():
    """
    Aggressively cleans numpy and scipy by first manually deleting their directories
    and then running pip uninstall as a fallback to guarantee a clean state.
    """
    print_with_flush(f'   🧹 Aggressively cleaning numpy and scipy from main environment...')
    packages = ['numpy', 'scipy']
    
    try:
        config_manager = ConfigManager()
        site_packages = Path(config_manager.config['site_packages_path'])
    except Exception as e:
        print_with_flush(f'   ❌ Could not determine site-packages path: {e}')
        return False
    
    for package in packages:
        print_with_flush(f"   🗑️  Forcefully deleting directories for '{package}'...")
        
        if (site_packages / package).is_dir():
            shutil.rmtree(site_packages / package, ignore_errors=True)
            print_with_flush(f'      - Removed {site_packages / package}')
        
        for dist_info in site_packages.glob(f'{package}-*.dist-info'):
            shutil.rmtree(dist_info, ignore_errors=True)
            print_with_flush(f'      - Removed {dist_info}')
    
    for package in packages:
        run_subprocess_with_output(
            ['pip', 'uninstall', package, '-y'], 
            f'Running pip uninstall for {package} (as final check)'
        )
    
    print_with_flush('   ✅ Aggressive clean complete.')
    return True

def omnipkg_install_baseline():
    """Use omnipkg to install baseline versions"""
    print_with_flush(f'   📦 Using omnipkg to install baseline numpy==1.26.4 and scipy==1.16.1...')
    packages = ['numpy==1.26.4', 'scipy==1.16.1']
    
    try:
        success, stdout, stderr = run_subprocess_with_output(
            ['omnipkg', 'install'] + packages, 
            'Installing baseline packages with omnipkg',
            timeout_hint=60
        )
        if success:
            print_with_flush(f'   ✅ omnipkg install baseline packages completed successfully')
            print_with_flush(f'   📚 Knowledge base automatically synced during install')
            return True
        else:
            print_with_flush(f'   ❌ omnipkg install failed: {stderr}')
            return False
    except Exception as e:
        print_with_flush(f'   ❌ omnipkg install failed: {e}')
        return False

def restore_original_versions(original_versions):
    """Restore original package versions if they were captured"""
    if not original_versions:
        print_with_flush(f'   ℹ️  No original versions to restore - leaving packages uninstalled')
        return True
    
    print_with_flush(f'   🔄 Restoring original package versions...')
    packages_to_restore = []
    
    for pkg, version in original_versions.items():
        packages_to_restore.append(f'{pkg}=={version}')
        print_with_flush(f'   📦 Will restore: {pkg}=={version}')
    
    if packages_to_restore:
        try:
            success, stdout, stderr = run_subprocess_with_output(
                ['pip', 'install'] + packages_to_restore, 
                'Restoring original package versions',
                timeout_hint=30
            )
            if success:
                print_with_flush(f'   ✅ Original versions restored successfully')
                return True
            else:
                print_with_flush(f'   ⚠️  Failed to restore original versions: {stderr}')
                print_with_flush(f'   💡 You may need to manually reinstall: {" ".join(packages_to_restore)}')
                return False
        except Exception as e:
            print_with_flush(f'   ⚠️  Failed to restore original versions: {e}')
            print_with_flush(f'   💡 You may need to manually reinstall: {" ".join(packages_to_restore)}')
            return False
    
    return True

def setup():
    """Ensures the environment is clean before the test."""
    print_header('STEP 1: Preparing a Clean Test Environment')
    sys.stdout.flush()
    
    config_manager = ConfigManager()
    original_strategy = get_current_install_strategy(config_manager)
    print_with_flush(f'   ℹ️  Current install strategy: {original_strategy}')
    
    print_with_flush(f'   📋 Capturing original package versions...')
    original_versions = get_installed_versions()
    
    print_with_flush(f'   ⚙️  Setting install strategy to stable-main for testing...')
    if not set_install_strategy(config_manager, 'stable-main'):
        print_with_flush(f'   ⚠️  Could not change install strategy, continuing anyway...')
    
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    packages_to_test = ['numpy', 'scipy']
    
    print_with_flush(f'   🫧 Removing existing test bubbles with omnipkg...')
    for pkg in packages_to_test:
        existing_bubbles = list(omnipkg_core.multiversion_base.glob(f'{pkg}-*'))
        for bubble in existing_bubbles:
            if bubble.is_dir():
                bubble_name = bubble.name
                print_with_flush(f'   🧹 Removing bubble: {bubble_name} with omnipkg uninstall')
                try:
                    version = bubble_name.split('-', 1)[1] if '-' in bubble_name else None
                    if version:
                        success, stdout, stderr = run_subprocess_with_output(
                            ['omnipkg', 'uninstall', f'{pkg}=={version}'], 
                            f'Uninstalling bubble {bubble_name}', 
                            show_output=False
                        )
                    if bubble.exists():
                        shutil.rmtree(bubble, ignore_errors=True)
                        print_with_flush(f'   🧹 Manually removed {bubble_name}')
                except Exception as e:
                    print_with_flush(f'   ⚠️  Using manual removal for {bubble_name}: {e}')
                    shutil.rmtree(bubble, ignore_errors=True)
    
    # Clean cloaked packages
    site_packages = Path(omnipkg_core.config['site_packages_path'])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f'{canonical_pkg_name}.*_omnipkg_cloaked*', 
                              f'{canonical_pkg_name}-*.dist-info.*omnipkg_cloaked*']:
            for cloaked in site_packages.glob(cloaked_pattern):
                print_with_flush(f'   🧹 Removing residual cloaked: {cloaked.name}')
                shutil.rmtree(cloaked, ignore_errors=True)
        
        for cloaked in site_packages.glob(f'{canonical_pkg_name}.*_test_harness_cloaked*'):
            print_with_flush(f'   🧹 Removing test harness residual cloaked: {cloaked.name}')
            shutil.rmtree(cloaked, ignore_errors=True)
    
    print_with_flush(f'   - Setting main environment to a known good state...')
    print_with_flush(f'   🗑️ Ensuring clean numpy and scipy installations for baseline test...')
    
    if not pip_clean_packages():
        print_with_flush(f'   ❌ Failed to clean packages with pip')
        return (None, original_strategy, original_versions)
    
    if not omnipkg_install_baseline():
        print_with_flush(f'   ❌ Failed to install baseline packages')
        return (None, original_strategy, original_versions)
    
    print_with_flush('✅ Environment is clean and ready for testing.')
    return (config_manager, original_strategy, original_versions)

def run_test():
    """The core of the OMNIPKG Nuclear Stress Test."""
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config
    
    print_with_flush(f'\n💥 NUMPY VERSION JUGGLING:')
    
    for numpy_ver in ['1.24.3', '1.26.4']:
        print_with_flush(f'\n⚡ Switching to numpy=={numpy_ver}')
        start_time = time.perf_counter()
        
        try:
            with omnipkgLoader(f'numpy=={numpy_ver}', config=omnipkg_config):
                import numpy as np
                activation_time = time.perf_counter() - start_time
                
                print_with_flush(f'   ✅ Version: {np.__version__}')
                print_with_flush(f'   🔢 Array sum: {np.array([1, 2, 3]).sum()}')
                print_with_flush(f'   ⚡ Activation time: {activation_time*1000:.2f}ms')
                
                if np.__version__ != numpy_ver:
                    print_with_flush(f'   ⚠️ WARNING: Expected {numpy_ver}, got {np.__version__}!')
                else:
                    print_with_flush(f'   🎯 Version verification: PASSED')
        except Exception as e:
            print_with_flush(f'   ❌ Activation/Test failed for numpy=={numpy_ver}: {e}!')
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
    
    print_with_flush(f'\n\n🔥 SCIPY C-EXTENSION TEST:')
    
    for scipy_ver in ['1.12.0', '1.16.1']:
        print_with_flush(f'\n🌋 Switching to scipy=={scipy_ver}')
        start_time = time.perf_counter()
        
        try:
            with omnipkgLoader(f'scipy=={scipy_ver}', config=omnipkg_config):
                import scipy as sp
                import scipy.sparse
                import scipy.linalg
                activation_time = time.perf_counter() - start_time
                
                print_with_flush(f'   ✅ Version: {sp.__version__}')
                print_with_flush(f'   ♻️ Sparse matrix: {sp.sparse.eye(3).nnz} non-zeros')
                print_with_flush(f'   📐 Linalg det: {sp.linalg.det([[0, 2], [1, 1]])}')
                print_with_flush(f'   ⚡ Activation time: {activation_time*1000:.2f}ms')
                
                if sp.__version__ != scipy_ver:
                    print_with_flush(f'   ⚠️ WARNING: Expected {scipy_ver}, got {sp.__version__}!')
                else:
                    print_with_flush(f'   🎯 Version verification: PASSED')
        except Exception as e:
            print_with_flush(f'   ❌ Activation/Test failed for scipy=={scipy_ver}: {e}!')
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
    
    print_with_flush(f'\n\n🤯 NUMPY + SCIPY VERSION MIXING:')
    combos = [('1.24.3', '1.12.0'), ('1.26.4', '1.16.1')]
    temp_script_path = Path(os.getcwd()) / 'omnipkg_combo_test.py'
    
    for np_ver, sp_ver in combos:
        print_with_flush(f'\n🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}')
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
    
    print("🔍 Python path (first 5 entries):", flush=True)
    for idx, path in enumerate(sys.path[:5]):
        print(f"   {{idx}}: {{path}}", flush=True)

    try:
        import numpy as np
        import scipy as sp
        import scipy.sparse
        
        setup_time = time.perf_counter() - start_time
        
        print(f"   🧪 numpy: {{np.__version__}}, scipy: {{sp.__version__}}", flush=True)
        print(f"   📍 numpy location: {{np.__file__}}", flush=True)
        print(f"   📍 scipy location: {{sp.__file__}}", flush=True)
        print(f"   ⚡ Setup time: {{setup_time*1000:.2f}}ms", flush=True)
        
        result = np.array([1,2,3]) @ sp.sparse.eye(3).toarray()
        print(f"   🔗 Compatibility check: {{result}}", flush=True)
        
        # Version validation
        np_ok = False
        sp_ok = False
        try:
            if get_version('numpy') == "{np_ver}":
                np_ok = True
            else:
                print(f"   ❌ Numpy version mismatch! Expected {np_ver}, got {{get_version('numpy')}}", file=sys.stderr, flush=True)
        except PackageNotFoundError:
            print(f"   ❌ Numpy not found in subprocess!", file=sys.stderr, flush=True)

        try:
            if get_version('scipy') == "{sp_ver}":
                sp_ok = True
            else:
                print(f"   ❌ Scipy version mismatch! Expected {sp_ver}, got {{get_version('scipy')}}", file=sys.stderr, flush=True)
        except PackageNotFoundError:
            print(f"   ❌ Scipy not found in subprocess!", file=sys.stderr, flush=True)

        if np_ok and sp_ok:
            total_time = time.perf_counter() - start_time
            print(f"   🎯 Version verification: BOTH PASSED!", flush=True)
            print(f"   ⚡ Total combo time: {{total_time*1000:.2f}}ms", flush=True)
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"   ❌ Test failed in subprocess: {{e}}", file=sys.stderr, flush=True)
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
            print_with_flush(f'   ⚡ Total combo execution: {combo_total_time*1000:.2f}ms')
            
            if not success:
                print_with_flush(f'   ❌ Subprocess test failed for combo numpy=={np_ver} + scipy=={sp_ver}')
                if stderr:
                    print_with_flush(f'   💥 Error: {stderr}')
                sys.exit(1)
                
        except Exception as e:
            print_with_flush(f'   ❌ An unexpected error occurred during combo test subprocess setup: {e}')
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1)
        finally:
            if temp_script_path.exists():
                os.remove(temp_script_path)
    
    print_with_flush('\n\n🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇')

def cleanup(original_versions):
    """Cleans up all bubbles created during the test."""
    print_header('STEP 3: Cleaning Up Test Environment')
    sys.stdout.flush()
    
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    packages_to_test = ['numpy', 'scipy']
    
    print_with_flush(f'   🫧 Removing test bubbles with omnipkg...')
    test_packages = ['numpy==1.24.3', 'scipy==1.12.0']
    
    for pkg_spec in test_packages:
        try:
            success, stdout, stderr = run_subprocess_with_output(
                ['omnipkg', 'uninstall', pkg_spec, '-y'], 
                f'Uninstalling test bubble {pkg_spec}', 
                show_output=False
            )
            if success:
                print_with_flush(f'   ✅ omnipkg uninstall {pkg_spec} completed')
            else:
                print_with_flush(f'   ℹ️  omnipkg uninstall {pkg_spec} completed (may not have existed)')
        except Exception as e:
            print_with_flush(f'   ⚠️  omnipkg uninstall failed for {pkg_spec}: {e}')
    
    # Manual cleanup of any remaining bubbles
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f'{pkg}-*'):
            if bubble.is_dir():
                print_with_flush(f'   🧹 Removing remaining test bubble: {bubble.name}')
                shutil.rmtree(bubble, ignore_errors=True)
    
    # Clean cloaked packages
    site_packages = Path(omnipkg_core.config['site_packages_path'])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f'{canonical_pkg_name}.*_omnipkg_cloaked*', 
                              f'{canonical_pkg_name}-*.dist-info.*omnipkg_cloaked*']:
            for cloaked in site_packages.glob(cloaked_pattern):
                print_with_flush(f'   🧹 Removing residual cloaked: {cloaked.name}')
                shutil.rmtree(cloaked, ignore_errors=True)
        
        for cloaked in site_packages.glob(f'{canonical_pkg_name}.*_test_harness_cloaked*'):
            print_with_flush(f'   🧹 Removing test harness residual cloaked: {cloaked.name}')
            shutil.rmtree(cloaked, ignore_errors=True)
    
    print_with_flush(f'   🧹 Cleaning main environment packages...')
    pip_clean_packages()
    restore_original_versions(original_versions)
    
    print_with_flush(f'\n✅ Cleanup complete. Your environment is restored.')

def run():
    """Main entry point for the stress test, called by the CLI."""
    original_strategy = None
    original_versions = {}
    
    try:
        result = setup()
        if result[0] is None:
            # Setup failed, exit gracefully. Error messages were already printed.
            return False
        
        config_manager, original_strategy, original_versions = result
        
        print_header('STEP 2: Creating Test Bubbles with omnipkg')
        sys.stdout.flush()
        
 # NOTE: The baseline versions (numpy 1.26.4, scipy 1.16.1) are already installed
        # in the main environment by the setup() function. We only need to create
        # bubbles for the *alternate* versions we plan to switch to.
        packages_to_bubble = ['numpy==1.24.3', 'scipy==1.12.0']
        
        for pkg in packages_to_bubble:
            print_with_flush(f'\n--- Creating bubble for {pkg} ---')
            
            # Use your streaming function to create the bubble with a timeout hint
            success, stdout, stderr = run_subprocess_with_output(
                ['omnipkg', 'install', pkg],
                f"Creating bubble for {pkg}",
                timeout_hint=60  # Give user an idea of expected time
            )

            # If any bubble fails to create, we must stop the test
            if not success:
                print_with_flush(f"   ❌ Critical error: Failed to create bubble for {pkg}. Aborting test.")
                return False

        # --- Execute the Core Test Logic ---
        print_header('STEP 3: Executing the Nuclear Test')
        sys.stdout.flush()
        run_test()
        
        # If the script successfully reaches this point, the test has passed.
        return True

    except Exception as e:
        print_with_flush(f'\n❌ A critical error occurred during the stress test: {e}')
        import traceback
        traceback.print_exc()
        sys.stderr.flush()
        return False
        
    finally:
        # --- Cleanup and Restoration ---
        # This block is guaranteed to run, whether the `try` block succeeds or fails.
        # This ensures your environment is always returned to its original state.
        print_header('STEP 4: Cleanup & Restoration')
        sys.stdout.flush()
        try:
            # Call the cleanup function to remove bubbles and test packages
            cleanup(original_versions)
            
            # Restore the original install strategy that was captured at the start
            if original_strategy and original_strategy != 'stable-main':
                config_manager = ConfigManager()
                restore_install_strategy(config_manager, original_strategy)
                print_with_flush(f'   💡 Note: Install strategy has been restored to: {original_strategy}')
            elif original_strategy == 'stable-main':
                print_with_flush(f'   ℹ️  Install strategy remains at: stable-main')
            else:
                print_with_flush('   💡 Note: You may need to manually restore your preferred install strategy')
                
        except Exception as e:
            print_with_flush(f'⚠️  CRITICAL: The cleanup process itself failed: {e}')
            if original_strategy and original_strategy != 'stable-main':
                print_with_flush(f'   💡 You may need to manually restore install strategy: {original_strategy}')

# --- Main Execution Block ---
if __name__ == '__main__':
    """
    This is the entry point when the script is run directly from the command line.
    It calls the main `run()` function and sets the system exit code based on
    the success or failure of the test.
    """
    success = run()
    sys.exit(0 if success else 1)