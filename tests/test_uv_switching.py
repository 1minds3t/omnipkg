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

# Ensure the project root is in the Python path to allow for omnipkg imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# --- Test Configuration ---
MAIN_UV_VERSION = '0.6.13'
BUBBLE_VERSIONS_TO_TEST = ['0.4.30', '0.5.11']

# --- Internationalization Setup ---
from omnipkg.i18n import _
lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

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

def set_install_strategy(config_manager, strategy):
    """Sets the omnipkg install strategy via the CLI."""
    try:
        subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy], capture_output=True, text=True, check=True)
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
            # It's not an error if it wasn't installed
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

# --- Test Workflow Steps ---

def setup_environment():
    """Prepares the testing environment by cleaning up and setting up a baseline."""
    print_header('STEP 1: Environment Setup & Cleanup')
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager)
    print(_('   🧹 Cleaning up existing UV installations...'))
    pip_uninstall_uv()
    # Clean any leftover bubbles from previous runs
    for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
        shutil.rmtree(bubble, ignore_errors=True)
    
    print(_('   📦 Establishing stable main environment: uv=={}').format(MAIN_UV_VERSION))
    if not pip_install_uv(MAIN_UV_VERSION):
        return (None, None)
    
    # Use 'stable-main' to ensure omnipkg creates bubbles for other versions
    set_install_strategy(config_manager, 'stable-main')
    
    print(_('   🫧 Creating all required test bubbles...'))
    for version in BUBBLE_VERSIONS_TO_TEST:
        print(f'      -> Installing bubble for uv=={version}')
        omnipkg_core.smart_install([f'uv=={version}'])
    
    print(_('✅ Environment prepared'))
    return (ConfigManager(), 'stable-main')

def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    print(_('   🔍 Inspecting bubble structure: {}').format(bubble_path.name))
    if not bubble_path.exists():
        print(_("   ❌ Bubble doesn't exist: {}").format(bubble_path))
        return False
    
    # Check for key components of a binary package bubble
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
    for item in sorted(contents)[:5]: # Print first 5 items
        print(_('      - {}{}').format(item.name, '/' if item.is_dir() else ''))
    return True

def test_swapped_binary_execution(expected_version):
    """
    Tests version swapping using omnipkgLoader.context, which will
    measure and print the activation/deactivation times.
    """
    print(_('   🔧 Testing swapped binary execution via omnipkgLoader...'))
    try:
        # This context manager activates the bubble, modifies the PATH,
        # times the operation, and handles cleanup.
        with omnipkgLoader(f'uv=={expected_version}'):
            print(_('   🎯 Executing: uv --version (within context)'))
            
            # Inside the context, 'uv' should resolve to the bubbled version's binary
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

def test_main_environment_uv(config_manager: ConfigManager):
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
            print(_(f'   ❌ Main environment test: FAILED (expected {MAIN_UV_VERSION}, got {actual_version})'))
        return main_passed
    except Exception as e:
        print(_('   ❌ Main environment test failed: {}').format(e))
        return False

def run_comprehensive_test():
    """Main function to orchestrate the entire test suite."""
    print_header('🚨 OMNIPKG UV BINARY STRESS TEST 🚨')
    original_strategy = 'multiversion'
    try:
        config_manager, original_strategy = setup_environment()
        if config_manager is None:
            return False
            
        multiversion_base = Path(config_manager.config['multiversion_base'])
        print_header('STEP 3: Comprehensive UV Version Testing')
        
        test_results = {}
        
        # 1. Test the main, stable version
        main_passed = test_main_environment_uv(config_manager)
        test_results[f'main-{MAIN_UV_VERSION}'] = main_passed
        
        # 2. Test each bubbled version
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(_('Testing Bubble (uv=={})').format(version))
            bubble_path = multiversion_base / f'uv-{version}'
            
            # First, verify the bubble structure on disk is correct
            if not inspect_bubble_structure(bubble_path):
                test_results[f'bubble-{version}'] = False
                continue

            # Second, test the dynamic swapping and version check
            version_passed = test_swapped_binary_execution(version)
            test_results[f'bubble-{version}'] = version_passed

        print_header('FINAL TEST RESULTS')
        print(_('📊 Test Summary:'))
        all_tests_passed = True
        for version_key, passed in test_results.items():
            status = '✅ PASSED' if passed else '❌ FAILED'
            print(f'   {version_key:<25}: {status}')
            if not passed:
                all_tests_passed = False

        if all_tests_passed:
            print(_('\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉'))
            print(_('🔥 OMNIPKG UV BINARY HANDLING IS FULLY FUNCTIONAL! 🔥'))
        else:
            print(_('\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥'))
        
        return all_tests_passed
        
    except Exception as e:
        print(_('\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup & Restoration')
        try:
            config_manager = ConfigManager()
            omnipkg_core = OmnipkgCore(config_manager)
            for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
                if bubble.is_dir():
                    print(_('   🧹 Removing test bubble: {}').format(bubble.name))
                    shutil.rmtree(bubble, ignore_errors=True)

            if original_strategy and original_strategy != 'stable-main':
                print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
                set_install_strategy(config_manager, original_strategy)
            else:
                print(_('   ℹ️  Install strategy remains at: stable-main'))
            print(_('✅ Cleanup complete'))
        except Exception as e:
            print(_('⚠️  Cleanup failed: {}').format(e))

if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)