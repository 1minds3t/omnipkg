import sys
import os
from pathlib import Path

# --- PROJECT PATH SETUP ---
# This must come first so Python can find your modules.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# --- DETECT CURRENT PYTHON VERSION ---
CURRENT_PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
print(f"🐍 Detected current Python version: {CURRENT_PYTHON_VERSION}")

# --- BOOTSTRAP SECTION ---
# Import ONLY the necessary utilities for the bootstrap process.
from omnipkg.common_utils import ensure_python_or_relaunch, sync_context_to_runtime

# 1. Declarative script guard: Ensures this script runs on Python 3.9.
#    If not, it will relaunch this script with the correct interpreter and exit.
# 1. Declarative script guard: Ensures this script runs on the detected Python version.
#    If not, it will relaunch the script with the correct interpreter and exit.
if os.environ.get('OMNIPKG_RELAUNCHED') != '1':
    ensure_python_or_relaunch(CURRENT_PYTHON_VERSION)

# 2. Sync guard: Now that we are GUARANTEED to be running on the correct
#    interpreter, we sync omnipkg's config to match this runtime.
sync_context_to_runtime()


import sys
import os
from pathlib import Path
import json
import subprocess
import shutil
import tempfile
import time
from datetime import datetime
import re
import traceback
import importlib.util

# Ensure the project root is in the Python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Initialize i18n first
from omnipkg.i18n import _

lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

# --- Test Configuration ---
MAIN_UV_VERSION = '0.6.13'
BUBBLE_VERSIONS_TO_TEST = ['0.4.30', '0.5.11']

# --- Omnipkg Core Imports ---
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import run_command, print_header
except ImportError as e:
    print(_('❌ Failed to import omnipkg modules. Is the project structure correct? Error: {}').format(e))
    sys.exit(1)

# --- Helper Functions ---

def print_header(title):
    """Prints a formatted header to the console."""
    print('\n' + '=' * 80)
    print(_('  🚀 {}').format(title))
    print('=' * 80)

def print_subheader(title):
    """Prints a formatted subheader to the console."""
    print(_('\n--- {} ---').format(title))

def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'multiversion')
    except:
        return 'multiversion'

def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        # Use omnipkg config set command
        result = subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy],
                              capture_output=True, text=True, check=True)
        print(_('   ⚙️  Install strategy set to: {}').format(strategy))
        return True
    except Exception as e:
        print(_('   ⚠️  Failed to set install strategy: {}').format(e))
        return False

def pip_uninstall_uv():
    """Uses pip to uninstall uv from the main environment."""
    print(_('   🧹 Using pip to uninstall uv from main environment...'))
    try:
        result = subprocess.run(['pip', 'uninstall', 'uv', '-y'], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            print(_('   ✅ pip uninstall uv completed successfully'))
        else:
            print(_('   ℹ️  pip uninstall completed (uv may not have been installed)'))
        return True
    except Exception as e:
        print(_('   ⚠️  pip uninstall failed: {}').format(e))
        return False

def pip_install_uv(version):
    """Uses pip to install a specific version of uv."""
    print(_('   📦 Using pip to install uv=={}...').format(version))
    try:
        subprocess.run(['pip', 'install', f'uv=={version}'], capture_output=True, text=True, check=True)
        print(_('   ✅ pip install uv=={} completed successfully').format(version))
        return True
    except Exception as e:
        print(_('   ❌ pip install failed: {}').format(e))
        return False

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True

# --- Test Workflow Steps ---

def setup_environment():
    """Prepares the testing environment by cleaning up and setting up a baseline."""
    print_header(_('STEP 1: Environment Setup & Cleanup'))
    
    config_manager = ConfigManager()

    # Store original install strategy
    original_strategy = get_current_install_strategy(config_manager)
    print(_('   ℹ️  Current install strategy: {}').format(original_strategy))

    # Set to stable-main for consistent testing
    print(_('   ⚙️  Setting install strategy to stable-main for testing...'))
    if not set_install_strategy(config_manager, 'stable-main'):
        print(_('   ⚠️  Could not change install strategy, continuing anyway...'))

    # Refresh config after strategy change
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)

    # Clean up any existing bubbles
    print(_('   🧹 Cleaning up existing UV installations and bubbles...'))
    for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
        if bubble.is_dir():
            print(_('   🧹 Removing old bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)

    # Use pip to ensure clean main environment installation
    pip_uninstall_uv()
    if not pip_install_uv(MAIN_UV_VERSION):
        print(_('   ❌ Failed to install main environment UV version'))
        return None, original_strategy
    
    # --- THIS IS THE FIX ---
    # Tell omnipkg to update its knowledge about the package we just installed.
    force_omnipkg_rescan(omnipkg_core, 'uv')
    # --- END OF THE FIX ---
    
    print(_('✅ Environment prepared'))
    return config_manager, original_strategy

def create_test_bubbles(config_manager):
    """Create test bubbles for older UV versions"""
    print_header(_('STEP 2: Creating Test Bubbles for Older Versions'))
    omnipkg_core = OmnipkgCore(config_manager)
    
    for version in BUBBLE_VERSIONS_TO_TEST:
        print(_('   🫧 Creating bubble for uv=={}').format(version))
        try:
            omnipkg_core.smart_install([f'uv=={version}'])
            print(_('   ✅ Bubble created: uv-{}').format(version))
        except Exception as e:
            print(_('   ❌ Failed to create bubble for uv=={}: {}').format(version, e))

    return BUBBLE_VERSIONS_TO_TEST

def force_omnipkg_rescan(omnipkg_core, package_name):
    """Tells omnipkg to forcibly rescan a specific package's metadata."""
    print(f'   🧠 Forcing omnipkg KB rebuild for {package_name}...')
    try:
        # We'll use our new internal method directly for the test
        omnipkg_core.rebuild_package_kb([package_name])
        print(f'   ✅ KB rebuild for {package_name} complete.')
        return True
    except Exception as e:
        print(f'   ❌ KB rebuild for {package_name} failed: {e}')
        return False

def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    print(_('   🔍 Inspecting bubble structure: {}').format(bubble_path.name))
    if not bubble_path.exists():
        print(_("   ❌ Bubble doesn't exist: {}").format(bubble_path))
        return False
    
    dist_info = list(bubble_path.glob('uv-*.dist-info'))
    if dist_info:
        print(_('   ✅ Found dist-info: {}').format(dist_info[0].name))
    else:
        print(_('   ⚠️  No dist-info found'))
        
    scripts_dir = bubble_path / 'bin'
    if scripts_dir.exists():
        items = list(scripts_dir.iterdir())
        print(_('   ✅ Found bin directory with {} items').format(len(items)))
        uv_bin = scripts_dir / 'uv'
        if uv_bin.exists():
            print(_('   ✅ Found uv binary: {}').format(uv_bin))
            if os.access(uv_bin, os.X_OK):
                print(_('   ✅ Binary is executable'))
            else:
                print(_('   ⚠️  Binary is not executable'))
        else:
            print(_('   ⚠️  No uv binary in bin/'))
    else:
        print(_('   ⚠️  No bin directory found'))
        
    contents = list(bubble_path.iterdir())
    print(_('   📁 Bubble contents ({} items):').format(len(contents)))
    for item in sorted(contents)[:5]:
        suffix = '/' if item.is_dir() else ''
        print(_("      - {}{}").format(item.name, suffix))
    return True

def test_swapped_binary_execution(expected_version, config_manager):
    """
    Tests version swapping using omnipkgLoader.
    """
    print(_('   🔧 Testing swapped binary execution via omnipkgLoader...'))
    try:
        with omnipkgLoader(f'uv=={expected_version}', config=config_manager.config):
            print(_('   🎯 Executing: uv --version (within context)'))
            
            result = subprocess.run(['uv', '--version'], capture_output=True, text=True, timeout=10, check=True)
            actual_version = result.stdout.strip().split()[-1]
            
            print(_('   ✅ Swapped binary reported: {}').format(actual_version))
            
            if actual_version == expected_version:
                print(_('   🎯 Swapped binary test: PASSED'))
                return True
            else:
                print(_('   ❌ Version mismatch: expected {}, got {}').format(expected_version, actual_version))
                return False
    except Exception as e:
        print(_('   ❌ Swapped binary execution failed: {}').format(e))
        traceback.print_exc()
        return False

def test_main_environment_uv(config_manager):
    """Tests the main environment's uv installation as a baseline."""
    print_subheader(_('Testing Main Environment (uv=={})').format(MAIN_UV_VERSION))
    python_exe = config_manager.config.get('python_executable', sys.executable)
    uv_binary_path = Path(python_exe).parent / 'uv'
    try:
        result = subprocess.run([str(uv_binary_path), '--version'], capture_output=True, text=True, timeout=10, check=True)
        actual_version = result.stdout.strip().split()[-1]
        main_passed = actual_version == MAIN_UV_VERSION
        print(_('   ✅ Main environment version: {}').format(actual_version))
        if main_passed:
            print(_('   🎯 Main environment test: PASSED'))
        else:
            print(_('   ❌ Main environment test: FAILED (expected {}, got {})').format(MAIN_UV_VERSION, actual_version))
        return main_passed
    except Exception as e:
        print(_('   ❌ Main environment test failed: {}').format(e))
        return False

def run_comprehensive_test():
    """Main function to orchestrate the entire test suite."""
    print_header(_('🚨 OMNIPKG UV BINARY STRESS TEST 🚨'))
    original_strategy = None
    
    try:
        config_manager, original_strategy = setup_environment()
        if config_manager is None:
            return False
            
        create_test_bubbles(config_manager)
        
        print_header(_('STEP 3: Comprehensive UV Version Testing'))
        
        test_results = {}
        all_tests_passed = True
        
        main_passed = test_main_environment_uv(config_manager)
        test_results[_('main-{}').format(MAIN_UV_VERSION)] = main_passed
        all_tests_passed &= main_passed
        
        multiversion_base = Path(config_manager.config['multiversion_base'])
        
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(_('Testing Bubble (uv=={})').format(version))
            bubble_path = multiversion_base / f'uv-{version}'
            
            if not inspect_bubble_structure(bubble_path):
                test_results[_('bubble-{}').format(version)] = False
                all_tests_passed = False
                continue

            version_passed = test_swapped_binary_execution(version, config_manager)
            test_results[_('bubble-{}').format(version)] = version_passed
            all_tests_passed &= version_passed

        print_header(_('FINAL TEST RESULTS'))
        print(_('📊 Test Summary:'))

        for version_key, passed in test_results.items():
            status = _('✅ PASSED') if passed else _('❌ FAILED')
            print(_('   {}: {}').format(version_key.ljust(25), status))

        if all_tests_passed:
            print(_('\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉'))
            print(_('🔥 OMNIPKG UV BINARY HANDLING IS FULLY FUNCTIONAL! 🔥'))
        else:
            print(_('\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥'))
            print(_('🔧 Check the detailed output above for diagnostics'))
        
        return all_tests_passed
        
    except Exception as e:
        print(_('\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        print_header(_('STEP 4: Cleanup & Restoration'))
        try:
            config_manager = ConfigManager()
            omnipkg_core = OmnipkgCore(config_manager)
            
            # Clean up test bubbles
            for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
                if bubble.is_dir():
                    print(_('   🧹 Removing test bubble: {}').format(bubble.name))
                    shutil.rmtree(bubble, ignore_errors=True)

            # Restore main environment to latest version using pip
            print(_('   📦 Restoring main environment: uv=={}').format(MAIN_UV_VERSION))
            pip_uninstall_uv()
            pip_install_uv(MAIN_UV_VERSION)

            force_omnipkg_rescan(omnipkg_core, 'uv')
            
            # Restore original install strategy if it was changed
            if original_strategy and original_strategy != 'stable-main':
                restore_install_strategy(config_manager, original_strategy)
                print(_('   💡 Note: Install strategy has been restored to: {}').format(original_strategy))
            elif original_strategy == 'stable-main':
                print(_('   ℹ️  Install strategy remains at: stable-main'))
            else:
                print(_('   💡 Note: You may need to manually restore your preferred install strategy'))
                print(_('   💡 Run: omnipkg config set install_strategy <your_preferred_strategy>'))
                
            print(_('✅ Cleanup complete'))
        except Exception as e:
            print(_('⚠️  Cleanup failed: {}').format(e))
            if original_strategy and original_strategy != 'stable-main':
                print(_('   💡 You may need to manually restore install strategy: {}').format(original_strategy))
                print(_('   💡 Run: omnipkg config set install_strategy {}').format(original_strategy))

if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)