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

# --- Test Configuration ---
MAIN_UV_VERSION = '0.6.13'
BUBBLE_VERSIONS_TO_TEST = ['0.4.30', '0.5.11']

# --- Omnipkg Core Imports ---
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import print_header
    from omnipkg.i18n import _
    # Create a single, global config manager instance to be used throughout the script
    config_manager = ConfigManager()
except ImportError as e:
    print(f'❌ Failed to import omnipkg modules. Is the project structure correct? Error: {e}')
    sys.exit(1)

# --- Helper Functions ---

def print_header(title):
    """Prints a formatted header to the console."""
    print('\n' + '=' * 80)
    print(f'  🚀 {title}')
    print('=' * 80)

def print_subheader(title):
    """Prints a formatted subheader to the console."""
    print(f'\n--- {title} ---')

def set_install_strategy(strategy):
    """Sets the omnipkg install strategy via the CLI."""
    try:
        subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy], capture_output=True, text=True, check=True)
        print(f'   ⚙️  Install strategy set to: {strategy}')
        return True
    except Exception as e:
        print(f'   ⚠️  Failed to set install strategy: {e}')
        return False

def pip_uninstall_uv():
    """Uses pip to uninstall uv from the main environment."""
    print('   🧹 Using pip to uninstall uv from main environment...')
    try:
        result = subprocess.run(['pip', 'uninstall', 'uv', '-y'], capture_output=True, text=True, check=False)
        print('   ✅ pip uninstall uv completed successfully' if result.returncode == 0 else '   ℹ️  pip uninstall completed (uv may not have been installed)')
        return True
    except Exception as e:
        print(f'   ⚠️  pip uninstall failed: {e}')
        return False

def pip_install_uv(version):
    """Uses pip to install a specific version of uv."""
    print(f'   📦 Using pip to install uv=={version}...')
    try:
        subprocess.run(['pip', 'install', f'uv=={version}'], capture_output=True, text=True, check=True)
        print(f'   ✅ pip install uv=={version} completed successfully')
        return True
    except Exception as e:
        print(f'   ❌ pip install failed: {e}')
        return False

# --- Test Workflow Steps ---

def setup_environment():
    """Prepares the testing environment by cleaning up and setting up a baseline."""
    print_header('STEP 1: Environment Setup & Cleanup')
    omnipkg_core = OmnipkgCore(config_manager)
    print('   🧹 Cleaning up existing UV installations...')
    pip_uninstall_uv()
    for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
        shutil.rmtree(bubble, ignore_errors=True)
    
    print(f'   📦 Establishing stable main environment: uv=={MAIN_UV_VERSION}')
    if not pip_install_uv(MAIN_UV_VERSION):
        return None, None
    
    original_strategy = config_manager.config.get('install_strategy', 'multiversion')
    set_install_strategy('stable-main')
    
    print('   🫧 Creating all required test bubbles...')
    for version in BUBBLE_VERSIONS_TO_TEST:
        print(f'      -> Installing bubble for uv=={version}')
        omnipkg_core.smart_install([f'uv=={version}'])
    
    print('✅ Environment prepared')
    return config_manager, original_strategy

def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    print(f'   🔍 Inspecting bubble structure: {bubble_path.name}')
    # ... (rest of the function is unchanged)
    if not bubble_path.exists():
        print(f"   ❌ Bubble doesn't exist: {bubble_path}")
        return False
    
    dist_info = list(bubble_path.glob('uv-*.dist-info'))
    print(f'   ✅ Found dist-info: {dist_info[0].name}' if dist_info else '   ⚠️  No dist-info found')
        
    scripts_dir = bubble_path / 'bin'
    if scripts_dir.exists():
        items = list(scripts_dir.iterdir())
        print(f'   ✅ Found bin directory with {len(items)} items')
        uv_bin = scripts_dir / 'uv'
        if uv_bin.exists():
            print(f'   ✅ Found uv binary: {uv_bin}')
            print('   ✅ Binary is executable' if os.access(uv_bin, os.X_OK) else '   ⚠️  Binary is not executable')
        else:
            print('   ⚠️  No uv binary in bin/')
    else:
        print('   ⚠️  No bin directory found')
        
    contents = list(bubble_path.iterdir())
    print(f'   📁 Bubble contents ({len(contents)} items):')
    for item in sorted(contents)[:5]:
        print(f"      - {item.name}{'/' if item.is_dir() else ''}")
    return True

# ***** FIX IS HERE: Function signature reverted to original *****
def test_swapped_binary_execution(expected_version):
    """
    Tests version swapping using omnipkgLoader.
    """
    print('   🔧 Testing swapped binary execution via omnipkgLoader...')
    try:
        # It correctly uses the global config_manager instance now
        with omnipkgLoader(f'uv=={expected_version}', config=config_manager.config):
            print('   🎯 Executing: uv --version (within context)')
            
            result = subprocess.run(['uv', '--version'], capture_output=True, text=True, timeout=10, check=True)
            actual_version = result.stdout.strip().split()[-1]
            
            print(f'   ✅ Swapped binary reported: {actual_version}')
            
            if actual_version == expected_version:
                print('   🎯 Swapped binary test: PASSED')
                return True
            else:
                print(f'   ❌ Version mismatch: expected {expected_version}, got {actual_version}')
                return False
    except Exception as e:
        print(f'   ❌ Swapped binary execution failed: {e}')
        traceback.print_exc()
        return False

def test_main_environment_uv():
    """Tests the main environment's uv installation as a baseline."""
    print_subheader(f'Testing Main Environment (uv=={MAIN_UV_VERSION})')
    python_exe = config_manager.config.get('python_executable', sys.executable)
    uv_binary_path = Path(python_exe).parent / 'uv'
    try:
        result = subprocess.run([str(uv_binary_path), '--version'], capture_output=True, text=True, timeout=10, check=True)
        actual_version = result.stdout.strip().split()[-1]
        main_passed = actual_version == MAIN_UV_VERSION
        print(f'   ✅ Main environment version: {actual_version}')
        print('   🎯 Main environment test: PASSED' if main_passed else f'   ❌ Main environment test: FAILED (expected {MAIN_UV_VERSION}, got {actual_version})')
        return main_passed
    except Exception as e:
        print(f'   ❌ Main environment test failed: {e}')
        return False

def run_comprehensive_test():
    """Main function to orchestrate the entire test suite."""
    print_header('🚨 OMNIPKG UV BINARY STRESS TEST 🚨')
    original_strategy = 'multiversion'
    try:
        local_config_manager, original_strategy = setup_environment()
        if not local_config_manager:
            return False
            
        multiversion_base = Path(local_config_manager.config['multiversion_base'])
        print_header('STEP 3: Comprehensive UV Version Testing')
        
        test_results = {}
        
        main_passed = test_main_environment_uv()
        test_results[f'main-{MAIN_UV_VERSION}'] = main_passed
        
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(f'Testing Bubble (uv=={version})')
            bubble_path = multiversion_base / f'uv-{version}'
            
            if not inspect_bubble_structure(bubble_path):
                test_results[f'bubble-{version}'] = False
                continue

            # ***** FIX IS HERE: The call now matches the corrected function signature *****
            version_passed = test_swapped_binary_execution(version)
            test_results[f'bubble-{version}'] = version_passed

        print_header('FINAL TEST RESULTS')
        print('📊 Test Summary:')
        all_tests_passed = all(test_results.values())

        for version_key, passed in test_results.items():
            status = '✅ PASSED' if passed else '❌ FAILED'
            print(f'   {version_key:<25}: {status}')

        if all_tests_passed:
            print('\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉')
            print('🔥 OMNIPKG UV BINARY HANDLING IS FULLY FUNCTIONAL! 🔥')
        else:
            print('\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥')
        
        return all_tests_passed
        
    except Exception as e:
        print(f'\n❌ Critical error during testing: {e}')
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup & Restoration')
        try:
            omnipkg_core = OmnipkgCore(config_manager)
            for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
                if bubble.is_dir():
                    print(f'   🧹 Removing test bubble: {bubble.name}')
                    shutil.rmtree(bubble, ignore_errors=True)

            if original_strategy and original_strategy != 'stable-main':
                print(f'   🔄 Restoring original install strategy: {original_strategy}')
                set_install_strategy(original_strategy)
            else:
                print('   ℹ️  Install strategy remains at: stable-main')
            print('✅ Cleanup complete')
        except Exception as e:
            print(f'⚠️  Cleanup failed: {e}')

if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)