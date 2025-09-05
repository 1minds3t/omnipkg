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
    print(_('❌ Failed to import omnipkg modules. Is it installed correctly? Error: {}').format(e), flush=True)
    sys.exit(1)

def force_omnipkg_context_to_current_python():
    """
    Forces omnipkg's active context to match the currently running Python version.
    """
    current_python = f'{sys.version_info.major}.{sys.version_info.minor}'
    try:
        print(_('🔄 Forcing omnipkg context to match script Python version: {}').format(current_python))
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        result = subprocess.run(omnipkg_cmd_base + ['swap', 'python', current_python], capture_output=True, text=True, check=True)
        print(_('✅ omnipkg context synchronized to Python {}').format(current_python))
        return True
    except subprocess.CalledProcessError as e:
        print(_('⚠️  Could not synchronize omnipkg context via CLI: {}').format(e))
        print(_('   CLI output: {}').format(e.stdout))
        print(_('   CLI error: {}').format(e.stderr))
        try:
            print(_('🔄 Attempting direct config modification...'))
            config_manager = ConfigManager()
            python_exe = sys.executable
            config_manager.config['active_python_version'] = current_python
            config_manager.config['active_python_executable'] = python_exe
            config_manager.save_config()
            print(f'✅ Direct config update successful for Python {current_python}')
            return True
        except Exception as e2:
            print(_('⚠️  Direct config modification also failed: {}').format(e2))
            print('   Proceeding anyway - this may cause issues with bubble operations')
            return False
    except Exception as e:
        print(_('⚠️  Unexpected error synchronizing omnipkg context: {}').format(e))
        print('   Proceeding anyway - this may cause issues with bubble operations')
        return False
force_omnipkg_context_to_current_python()

def print_with_flush(message):
    """Print with immediate flush to avoid buffering issues"""
    print(message, flush=True)

def run_subprocess_with_output(cmd, description='', show_output=True):
    """
    Run subprocess with real-time output and proper buffering
    Returns (success, stdout, stderr)
    """
    print_with_flush(f'   🔄 {description}...')
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, universal_newlines=True, bufsize=1)
        stdout_lines = []
        stderr_lines = []
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output and show_output:
                print_with_flush(f'      {output.strip()}')
            if output:
                stdout_lines.append(output)
        stderr_output = process.stderr.read()
        if stderr_output and show_output:
            print_with_flush(_('   ⚠️  stderr: {}').format(stderr_output.strip()))
        stderr_lines.append(stderr_output)
        returncode = process.poll()
        stdout = ''.join(stdout_lines)
        stderr = ''.join(stderr_lines)
        return (returncode == 0, stdout, stderr)
    except Exception as e:
        print_with_flush(_('   ❌ Subprocess failed: {}').format(e))
        return (False, '', str(e))

def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'multiversion')
    except:
        return 'multiversion'

def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        success, stdout, stderr = run_subprocess_with_output(['omnipkg', 'config', 'set', 'install_strategy', strategy], _('Setting install strategy to {}').format(strategy), show_output=False)
        if success:
            print_with_flush(_('   ⚙️  Install strategy set to: {}').format(strategy))
            return True
        else:
            print_with_flush(_('   ⚠️  Failed to set install strategy: {}').format(stderr))
            return False
    except Exception as e:
        print_with_flush(_('   ⚠️  Failed to set install strategy: {}').format(e))
        return False

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print_with_flush(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True

def get_installed_versions():
    """Get currently installed versions of numpy and scipy"""
    versions = {}
    packages = ['numpy', 'scipy']
    try:
        success, stdout, stderr = run_subprocess_with_output(['pip', 'list', '--format=freeze'], 'Getting installed package versions', show_output=False)
        if success:
            for line in stdout.splitlines():
                if '==' in line:
                    pkg_name, version = line.split('==', 1)
                    if pkg_name.lower() in packages:
                        versions[pkg_name.lower()] = version
                        print_with_flush(_('   📋 Found installed: {}=={}').format(pkg_name, version))
        return versions
    except Exception as e:
        print_with_flush(_('   ⚠️  Could not get installed versions: {}').format(e))
        return {}

def pip_clean_packages():
    """
    Aggressively cleans numpy and scipy by first manually deleting their directories
    and then running pip uninstall as a fallback to guarantee a clean state.
    """
    print_with_flush(_('   🧹 Aggressively cleaning numpy and scipy from main environment...'))
    packages = ['numpy', 'scipy']
    try:
        config_manager = ConfigManager()
        site_packages = Path(config_manager.config['site_packages_path'])
    except Exception as e:
        print_with_flush(_('   ❌ Could not determine site-packages path: {}').format(e))
        return False
    for package in packages:
        print_with_flush(f"   🗑️  Forcefully deleting directories for '{package}'...")
        if (site_packages / package).is_dir():
            shutil.rmtree(site_packages / package, ignore_errors=True)
            print_with_flush(_('      - Removed {}').format(site_packages / package))
        for dist_info in site_packages.glob(f'{package}-*.dist-info'):
            shutil.rmtree(dist_info, ignore_errors=True)
            print_with_flush(_('      - Removed {}').format(dist_info))
    for package in packages:
        run_subprocess_with_output(['pip', 'uninstall', package, '-y'], f'Running pip uninstall for {package} (as final check)')
    print_with_flush('   ✅ Aggressive clean complete.')
    return True

def omnipkg_install_baseline():
    """Use omnipkg to install baseline versions"""
    print_with_flush(_('   📦 Using omnipkg to install baseline numpy==1.26.4 and scipy==1.16.1...'))
    packages = ['numpy==1.26.4', 'scipy==1.16.1']
    try:
        success, stdout, stderr = run_subprocess_with_output(['omnipkg', 'install'] + packages, 'Installing baseline packages with omnipkg')
        if success:
            print_with_flush(_('   ✅ omnipkg install baseline packages completed successfully'))
            print_with_flush(_('   📚 Knowledge base automatically synced during install'))
            return True
        else:
            print_with_flush(_('   ❌ omnipkg install failed: {}').format(stderr))
            return False
    except Exception as e:
        print_with_flush(_('   ❌ omnipkg install failed: {}').format(e))
        return False

def restore_original_versions(original_versions):
    """Restore original package versions if they were captured"""
    if not original_versions:
        print_with_flush(_('   ℹ️  No original versions to restore - leaving packages uninstalled'))
        return True
    print_with_flush(_('   🔄 Restoring original package versions...'))
    packages_to_restore = []
    for pkg, version in original_versions.items():
        packages_to_restore.append(f'{pkg}=={version}')
        print_with_flush(_('   📦 Will restore: {}=={}').format(pkg, version))
    if packages_to_restore:
        try:
            success, stdout, stderr = run_subprocess_with_output(['pip', 'install'] + packages_to_restore, 'Restoring original package versions')
            if success:
                print_with_flush(_('   ✅ Original versions restored successfully'))
                return True
            else:
                print_with_flush(_('   ⚠️  Failed to restore original versions: {}').format(stderr))
                print_with_flush(_('   💡 You may need to manually reinstall: {}').format(' '.join(packages_to_restore)))
                return False
        except Exception as e:
            print_with_flush(_('   ⚠️  Failed to restore original versions: {}').format(e))
            print_with_flush(_('   💡 You may need to manually reinstall: {}').format(' '.join(packages_to_restore)))
            return False
    return True

def setup():
    """Ensures the environment is clean before the test."""
    print_header(_('STEP 1: Preparing a Clean Test Environment'))
    sys.stdout.flush()
    config_manager = ConfigManager()
    original_strategy = get_current_install_strategy(config_manager)
    print_with_flush(_('   ℹ️  Current install strategy: {}').format(original_strategy))
    print_with_flush(_('   📋 Capturing original package versions...'))
    original_versions = get_installed_versions()
    print_with_flush(_('   ⚙️  Setting install strategy to stable-main for testing...'))
    if not set_install_strategy(config_manager, 'stable-main'):
        print_with_flush(_('   ⚠️  Could not change install strategy, continuing anyway...'))
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    packages_to_test = ['numpy', 'scipy']
    print_with_flush(_('   🫧 Removing existing test bubbles with omnipkg...'))
    for pkg in packages_to_test:
        existing_bubbles = list(omnipkg_core.multiversion_base.glob(f'{pkg}-*'))
        for bubble in existing_bubbles:
            if bubble.is_dir():
                bubble_name = bubble.name
                print_with_flush(_('   🧹 Removing bubble: {} with omnipkg uninstall').format(bubble_name))
                try:
                    version = bubble_name.split('-', 1)[1] if '-' in bubble_name else None
                    if version:
                        success, stdout, stderr = run_subprocess_with_output(['omnipkg', 'uninstall', f'{pkg}=={version}'], _('Uninstalling bubble {}').format(bubble_name), show_output=False)
                    if bubble.exists():
                        shutil.rmtree(bubble, ignore_errors=True)
                        print_with_flush(_('   🧹 Manually removed {}').format(bubble_name))
                except Exception as e:
                    print_with_flush(_('   ⚠️  Using manual removal for {}: {}').format(bubble_name, e))
                    shutil.rmtree(bubble, ignore_errors=True)
    site_packages = Path(omnipkg_core.config['site_packages_path'])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f'{canonical_pkg_name}._omnipkg_cloaked', f'{canonical_pkg_name}-*.dist-info.omnipkg_cloaked*']:
            for cloaked in site_packages.glob(cloaked_pattern):
                print_with_flush(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
        for cloaked in site_packages.glob(f'{canonical_pkg_name}.*_test_harness_cloaked*'):
            print_with_flush(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)
    print_with_flush(_('   - Setting main environment to a known good state...'))
    print_with_flush(_('   🗑️ Ensuring clean numpy and scipy installations for baseline test...'))
    if not pip_clean_packages():
        print_with_flush(_('   ❌ Failed to clean packages with pip'))
        return (None, original_strategy, original_versions)
    if not omnipkg_install_baseline():
        print_with_flush(_('   ❌ Failed to install baseline packages'))
        return (None, original_strategy, original_versions)
    print_with_flush(_('✅ Environment is clean and ready for testing.'))
    return (config_manager, original_strategy, original_versions)

def run_test():
    """The core of the OMNIPKG Nuclear Stress Test."""
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config
    print_with_flush(_('\n💥 NUMPY VERSION JUGGLING:'))
    for numpy_ver in ['1.24.3', '1.26.4']:
        print_with_flush(_('\n⚡ Switching to numpy=={}').format(numpy_ver))
        try:
            with omnipkgLoader(f'numpy=={numpy_ver}', config=omnipkg_config):
                import numpy as np
                print_with_flush(_('   ✅ Version: {}').format(np.__version__))
                print_with_flush(_('   🔢 Array sum: {}').format(np.array([1, 2, 3]).sum()))
                if np.__version__ != numpy_ver:
                    print_with_flush(_('   ⚠️ WARNING: Expected {}, got {}!').format(numpy_ver, np.__version__))
                else:
                    print_with_flush(_('   🎯 Version verification: PASSED'))
        except Exception as e:
            print_with_flush(_('   ❌ Activation/Test failed for numpy=={}: {}!').format(numpy_ver, e))
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
    print_with_flush(_('\n\n🔥 SCIPY C-EXTENSION TEST:'))
    for scipy_ver in ['1.12.0', '1.16.1']:
        print_with_flush(_('\n🌋 Switching to scipy=={}').format(scipy_ver))
        try:
            with omnipkgLoader(f'scipy=={scipy_ver}', config=omnipkg_config):
                import scipy as sp
                import scipy.sparse
                import scipy.linalg
                print_with_flush(_('   ✅ Version: {}').format(sp.__version__))
                print_with_flush(_('   ♻️ Sparse matrix: {} non-zeros').format(sp.sparse.eye(3).nnz))
                print_with_flush(_('   📐 Linalg det: {}').format(sp.linalg.det([[0, 2], [1, 1]])))
                if sp.__version__ != scipy_ver:
                    print_with_flush(_('   ⚠️ WARNING: Expected {}, got {}!').format(scipy_ver, sp.__version__))
                else:
                    print_with_flush(_('   🎯 Version verification: PASSED'))
        except Exception as e:
            print_with_flush(_('   ❌ Activation/Test failed for scipy=={}: {}!').format(scipy_ver, e))
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
    print_with_flush(_('\n\n🤯 NUMPY + SCIPY VERSION MIXING:'))
    combos = [('1.24.3', '1.12.0'), ('1.26.4', '1.16.1')]
    temp_script_path = Path(os.getcwd()) / 'omnipkg_combo_test.py'
    for np_ver, sp_ver in combos:
        print_with_flush(_('\n🌀 COMBO: numpy=={} + scipy=={}').format(np_ver, sp_ver))
        config_json_str = json.dumps(omnipkg_config)
        temp_script_content = f'''\nimport sys\nimport os\nimport json  # To load config\nimport importlib\nfrom importlib.metadata import version as get_version, PackageNotFoundError\nfrom pathlib import Path\n\n# Ensure omnipkg's root is in sys.path for importing its modules\nsys.path.insert(0, r"{ROOT_DIR.parent}")\n\n# Load config in the subprocess\nsubprocess_config = json.loads('{config_json_str}')\n\ndef run_combo_test():\n    # Retrieve bubble paths from the loaded config in the subprocess\n    numpy_bubble_path = Path(subprocess_config['multiversion_base']) / f"numpy-{np_ver}"\n    scipy_bubble_path = Path(subprocess_config['multiversion_base']) / f"scipy-{sp_ver}"\n\n    # Manually construct PYTHONPATH for this specific test as it was originally designed\n    # by prepending bubble paths to sys.path in this subprocess.\n    bubble_paths_to_add = []\n    if numpy_bubble_path.is_dir():\n        bubble_paths_to_add.append(str(numpy_bubble_path))\n    if scipy_bubble_path.is_dir():\n        bubble_paths_to_add.append(str(scipy_bubble_path))\n        \n    # Prepend bubble paths to sys.path for this subprocess\n    sys.path = bubble_paths_to_add + sys.path \n    \n    print("🔍 Python path (first 5 entries):", flush=True)\n    for idx, path in enumerate(sys.path[:5]):\n        print(f"   {{idx}}: {{path}}", flush=True)\n\n    try:\n        import numpy as np\n        import scipy as sp\n        import scipy.sparse\n        \n        print(f"   🧪 numpy: {{np.__version__}}, scipy: {{sp.__version__}}", flush=True)\n        print(f"   📍 numpy location: {{np.__file__}}", flush=True)\n        print(f"   📍 scipy location: {{sp.__file__}}", flush=True)\n        \n        result = np.array([1,2,3]) @ sp.sparse.eye(3).toarray()\n        print(f"   🔗 Compatibility check: {{result}}", flush=True)\n        \n        # Version validation\n        np_ok = False\n        sp_ok = False\n        try:\n            if get_version('numpy') == "{np_ver}":\n                np_ok = True\n            else:\n                print(f"   ❌ Numpy version mismatch! Expected {np_ver}, got {{get_version('numpy')}}", file=sys.stderr, flush=True)\n        except PackageNotFoundError:\n            print(f"   ❌ Numpy not found in subprocess!", file=sys.stderr, flush=True)\n\n        try:\n            if get_version('scipy') == "{sp_ver}":\n                sp_ok = True\n            else:\n                print(f"   ❌ Scipy version mismatch! Expected {sp_ver}, got {{get_version('scipy')}}", file=sys.stderr, flush=True)\n        except PackageNotFoundError:\n            print(f"   ❌ Scipy not found in subprocess!", file=sys.stderr, flush=True)\n\n        if np_ok and sp_ok:\n            print(f"   🎯 Version verification: BOTH PASSED!", flush=True)\n            sys.exit(0)\n        else:\n            sys.exit(1)\n\n    except Exception as e:\n        print(f"   ❌ Test failed in subprocess: {{e}}", file=sys.stderr, flush=True)\n        import traceback\n        traceback.print_exc(file=sys.stderr)\n        sys.stderr.flush()\n        sys.exit(1)\n\nif __name__ == "__main__":\n    run_combo_test()\n'''
        try:
            with open(temp_script_path, 'w') as f:
                f.write(temp_script_content)
            success, stdout, stderr = run_subprocess_with_output([sys.executable, str(temp_script_path)], f'Running combo test for numpy=={np_ver} + scipy=={sp_ver}')
            if not success:
                print_with_flush(_('   ❌ Subprocess test failed for combo numpy=={} + scipy=={}').format(np_ver, sp_ver))
                if stderr:
                    print_with_flush(_('   💥 Error: {}').format(stderr))
                sys.exit(1)
        except Exception as e:
            print_with_flush(_('   ❌ An unexpected error occurred during combo test subprocess setup: {}').format(e))
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1)
        finally:
            if temp_script_path.exists():
                os.remove(temp_script_path)
    print_with_flush(_('\n\n🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇'))

def cleanup(original_versions):
    """Cleans up all bubbles created during the test."""
    print_header(_('STEP 3: Cleaning Up Test Environment'))
    sys.stdout.flush()
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    packages_to_test = ['numpy', 'scipy']
    print_with_flush(_('   🫧 Removing test bubbles with omnipkg...'))
    test_packages = ['numpy==1.24.3', 'scipy==1.12.0']
    for pkg_spec in test_packages:
        try:
            success, stdout, stderr = run_subprocess_with_output(['omnipkg', 'uninstall', pkg_spec, '-y'], _('Uninstalling test bubble {}').format(pkg_spec), show_output=False)
            if success:
                print_with_flush(_('   ✅ omnipkg uninstall {} completed').format(pkg_spec))
            else:
                print_with_flush(_('   ℹ️  omnipkg uninstall {} completed (may not have existed)').format(pkg_spec))
        except Exception as e:
            print_with_flush(_('   ⚠️  omnipkg uninstall failed for {}: {}').format(pkg_spec, e))
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f'{pkg}-*'):
            if bubble.is_dir():
                print_with_flush(_('   🧹 Removing remaining test bubble: {}').format(bubble.name))
                shutil.rmtree(bubble, ignore_errors=True)
    site_packages = Path(omnipkg_core.config['site_packages_path'])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f'{canonical_pkg_name}._omnipkg_cloaked', f'{canonical_pkg_name}-*.dist-info.omnipkg_cloaked*']:
            for cloaked in site_packages.glob(cloaked_pattern):
                print_with_flush(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
        for cloaked in site_packages.glob(f'{canonical_pkg_name}.*_test_harness_cloaked*'):
            print_with_flush(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
            shutil.rmtree(cloaked, ignore_errors=True)
    print_with_flush(_('   🧹 Cleaning main environment packages...'))
    pip_clean_packages()
    restore_original_versions(original_versions)
    print_with_flush(_('\n✅ Cleanup complete. Your environment is restored.'))

def run():
    """Main entry point for the stress test, called by the CLI."""
    original_strategy = None
    original_versions = {}
    try:
        result = setup()
        if result[0] is None:
            return False
        config_manager, original_strategy, original_versions = result
        print_header(_('STEP 2: Creating Test Bubbles with omnipkg'))
        sys.stdout.flush()
        omnipkg_core = OmnipkgCore(config_manager)
        packages_to_bubble = ['numpy==1.24.3', 'scipy==1.12.0', 'numpy==1.26.4', 'scipy==1.16.1']
        for pkg in packages_to_bubble:
            print_with_flush(_('\n--- Creating bubble for {} ---').format(pkg))
            sys.stdout.flush()
            omnipkg_core.smart_install([pkg])
            sys.stdout.flush()
            time.sleep(1)
        print_header(_('STEP 3: Executing the Nuclear Test'))
        sys.stdout.flush()
        run_test()
        return True
    except Exception as e:
        print_with_flush(_('\n❌ An error occurred during the stress test: {}').format(e))
        import traceback
        traceback.print_exc()
        sys.stderr.flush()
        return False
    finally:
        print_header(_('STEP 4: Cleanup & Restoration'))
        sys.stdout.flush()
        try:
            cleanup(original_versions)
            if original_strategy and original_strategy != 'stable-main':
                config_manager = ConfigManager()
                restore_install_strategy(config_manager, original_strategy)
                print_with_flush(_('   💡 Note: Install strategy has been restored to: {}').format(original_strategy))
            elif original_strategy == 'stable-main':
                print_with_flush(_('   ℹ️  Install strategy remains at: stable-main'))
            else:
                print_with_flush(_('   💡 Note: You may need to manually restore your preferred install strategy'))
                print_with_flush(_('   💡 Run: omnipkg config set install_strategy <your_preferred_strategy>'))
        except Exception as e:
            print_with_flush(_('⚠️  Cleanup failed: {}').format(e))
            if original_strategy and original_strategy != 'stable-main':
                print_with_flush(_('   💡 You may need to manually restore install strategy: {}').format(original_strategy))
                print_with_flush(_('   💡 Run: omnipkg config set install_strategy {}').format(original_strategy))
if __name__ == '__main__':
    success = run()
    sys.exit(0 if success else 1)