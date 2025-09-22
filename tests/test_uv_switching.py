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

# Setup project path for imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

# Apply language settings from environment if available
lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

CURRENT_PYTHON_VERSION = f'{sys.version_info.major}.{sys.version_info.minor}'
safe_print(_('🐍 Detected current Python version: {}').format(CURRENT_PYTHON_VERSION))

# Bootstrap to ensure the script runs in the correct context
from omnipkg.common_utils import ensure_python_or_relaunch, sync_context_to_runtime
if os.environ.get('OMNIPKG_RELAUNCHED') != '1':
    ensure_python_or_relaunch(CURRENT_PYTHON_VERSION)
sync_context_to_runtime()

# Import core omnipkg modules after path and context are set
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    safe_print(_('❌ Failed to import omnipkg modules. Is the project structure correct? Error: {}').format(e))
    sys.exit(1)

# --- Test Configuration ---
MAIN_UV_VERSION_FALLBACK = '0.8.18'
BUBBLE_VERSIONS_TO_TEST = ['0.4.30', '0.5.11']


def print_header(title):
    """Prints a formatted header to the console."""
    safe_print('\n' + '=' * 80)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 80)


def print_subheader(title):
    """Prints a formatted subheader to the console."""
    safe_print(_('\n--- {} ---').format(title))


def get_current_install_strategy(config_manager):
    """Get the current install strategy"""
    try:
        return config_manager.config.get('install_strategy', 'stable-main')
    except Exception:
        return 'stable-main'


def set_install_strategy(config_manager, strategy):
    """Set the install strategy"""
    try:
        subprocess.run(['omnipkg', 'config', 'set', 'install_strategy', strategy], capture_output=True, text=True, check=True)
        safe_print(_('   ⚙️  Install strategy set to: {}').format(strategy))
        return True
    except Exception as e:
        safe_print(_('   ⚠️  Failed to set install strategy: {}').format(e))
        return False


def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        safe_print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True


def setup_environment():
    """
    (SMARTER) Prepares the testing environment adaptively. It detects the existing
    uv version in the main environment and only installs a fallback if needed.
    """
    print_header(_('STEP 1: Environment Setup & Cleanup'))
    config_manager = ConfigManager(suppress_init_messages=True)
    omnipkg_core = OmnipkgCore(config_manager)

    original_strategy = get_current_install_strategy(config_manager)
    safe_print(_('   ℹ️  Current install strategy: {}').format(original_strategy))
    safe_print(_('   ⚙️  Setting install strategy to stable-main for testing...'))
    set_install_strategy(config_manager, 'stable-main')

    safe_print(_('   🧹 Cleaning up old demo bubbles...'))
    for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
        if bubble.is_dir():
            safe_print(_('   🧹 Removing old bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)

    # --- START: ADAPTIVE MAIN VERSION LOGIC ---
    main_uv_version = None
    try:
        # Use omnipkg's own logic to find the active version.
        active_install = next(
            (inst for inst in omnipkg_core._find_package_installations('uv') if inst.get('install_type') == 'active'),
            None
        )
        if active_install:
            main_uv_version = active_install.get('Version')
    except Exception:
        pass  # If KB is out of sync, we'll handle it.

    if main_uv_version:
        safe_print(f"   ✅ Found existing uv v{main_uv_version}. It will be used as the main version for the demo.")
    else:
        safe_print(f"   ℹ️  uv not found. Installing a baseline version ({MAIN_UV_VERSION_FALLBACK}) for the demo.")
        # Use omnipkg's own install method for consistency and KB accuracy.
        omnipkg_core.smart_install([f'uv=={MAIN_UV_VERSION_FALLBACK}'])
        main_uv_version = MAIN_UV_VERSION_FALLBACK
    # --- END: ADAPTIVE MAIN VERSION LOGIC ---

    # A single rescan ensures the KB is perfectly in sync with the detected/installed state.
    force_omnipkg_rescan(omnipkg_core, 'uv')
    safe_print(_('✅ Environment prepared'))
    return (config_manager, original_strategy, main_uv_version)


def create_test_bubbles(config_manager):
    """Create test bubbles for older UV versions"""
    print_header(_('STEP 2: Creating Test Bubbles for Older Versions'))
    omnipkg_core = OmnipkgCore(config_manager)
    for version in BUBBLE_VERSIONS_TO_TEST:
        safe_print(_('   🫧 Creating bubble for uv=={}').format(version))
        try:
            omnipkg_core.smart_install([f'uv=={version}'])
            safe_print(_('   ✅ Bubble created: uv-{}').format(version))
        except Exception as e:
            safe_print(_('   ❌ Failed to create bubble for uv=={}: {}').format(version, e))
    return BUBBLE_VERSIONS_TO_TEST


def force_omnipkg_rescan(omnipkg_core, package_name):
    """Tells omnipkg to forcibly rescan a specific package's metadata."""
    safe_print(f'   🧠 Forcing omnipkg KB rebuild for {package_name}...')
    try:
        omnipkg_core.rebuild_package_kb([package_name])
        safe_print(f'   ✅ KB rebuild for {package_name} complete.')
        return True
    except Exception as e:
        safe_print(f'   ❌ KB rebuild for {package_name} failed: {e}')
        return False


def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    safe_print(_('   🔍 Inspecting bubble structure: {}').format(bubble_path.name))
    if not bubble_path.exists():
        safe_print(_("   ❌ Bubble doesn't exist: {}").format(bubble_path))
        return False
    dist_info = list(bubble_path.glob('uv-*.dist-info'))
    if dist_info:
        safe_print(_('   ✅ Found dist-info: {}').format(dist_info[0].name))
    else:
        safe_print(_('   ⚠️  No dist-info found'))
    scripts_dir = bubble_path / 'bin'
    if scripts_dir.exists():
        items = list(scripts_dir.iterdir())
        safe_print(_('   ✅ Found bin directory with {} items').format(len(items)))
        uv_bin = scripts_dir / 'uv'
        if uv_bin.exists():
            safe_print(_('   ✅ Found uv binary: {}').format(uv_bin))
            if os.access(uv_bin, os.X_OK):
                safe_print(_('   ✅ Binary is executable'))
            else:
                safe_print(_('   ⚠️  Binary is not executable'))
        else:
            safe_print(_('   ⚠️  No uv binary in bin/'))
    else:
        safe_print(_('   ⚠️  No bin directory found'))
    contents = list(bubble_path.iterdir())
    safe_print(_('   📁 Bubble contents ({} items):').format(len(contents)))
    for item in sorted(contents)[:5]:
        suffix = '/' if item.is_dir() else ''
        safe_print(_('      - {}{}').format(item.name, suffix))
    return True


def test_swapped_binary_execution(expected_version, config_manager):
    """Tests version swapping using omnipkgLoader."""
    safe_print(_('   🔧 Testing swapped binary execution via omnipkgLoader...'))
    try:
        with omnipkgLoader(f'uv=={expected_version}', config=config_manager.config):
            safe_print(_('   🎯 Executing: uv --version (within context)'))
            result = subprocess.run(['uv', '--version'], capture_output=True, text=True, timeout=10, check=True)
            actual_version = result.stdout.strip().split()[-1]
            safe_print(_('   ✅ Swapped binary reported: {}').format(actual_version))
            if actual_version == expected_version:
                safe_print(_('   🎯 Swapped binary test: PASSED'))
                return True
            else:
                safe_print(_('   ❌ Version mismatch: expected {}, got {}').format(expected_version, actual_version))
                return False
    except Exception as e:
        safe_print(_('   ❌ Swapped binary execution failed: {}').format(e))
        traceback.print_exc()
        return False


def run_comprehensive_test():
    """Main function to orchestrate the entire test suite."""
    print_header(_('🚨 OMNIPKG UV BINARY STRESS TEST 🚨'))
    original_strategy = None
    main_uv_version_to_test = None  # This will hold our dynamic version

    try:
        # The setup function now returns the dynamic main version
        config_manager, original_strategy, main_uv_version_to_test = setup_environment()
        if config_manager is None:
            return False

        create_test_bubbles(config_manager)
        print_header(_('STEP 3: Comprehensive UV Version Testing'))

        test_results = {}
        all_tests_passed = True

        # --- TEST MAIN ENVIRONMENT DYNAMICALLY ---
        print_subheader(_('Testing Main Environment (uv=={})').format(main_uv_version_to_test))
        python_exe = config_manager.config.get('python_executable', sys.executable)
        uv_binary_path = Path(python_exe).parent / 'uv'
        try:
            result = subprocess.run([str(uv_binary_path), '--version'], capture_output=True, text=True, timeout=10, check=True)
            actual_version = result.stdout.strip().split()[-1]
            main_passed = actual_version == main_uv_version_to_test
            safe_print(_('   ✅ Main environment version: {}').format(actual_version))
            if main_passed:
                safe_print(_('   🎯 Main environment test: PASSED'))
            else:
                safe_print(_('   ❌ Main environment test: FAILED (expected {}, got {})').format(main_uv_version_to_test, actual_version))
            test_results[f'main-{main_uv_version_to_test}'] = main_passed
            all_tests_passed &= main_passed
        except Exception as e:
            safe_print(_('   ❌ Main environment test failed: {}').format(e))
            test_results[f'main-{main_uv_version_to_test}'] = False
            all_tests_passed = False
        # --- END DYNAMIC TEST ---

        multiversion_base = Path(config_manager.config['multiversion_base'])
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(_('Testing Bubble (uv=={})').format(version))
            bubble_path = multiversion_base / f'uv-{version}'
            if not inspect_bubble_structure(bubble_path):
                test_results[f'bubble-{version}'] = False
                all_tests_passed = False
                continue
            version_passed = test_swapped_binary_execution(version, config_manager)
            test_results[f'bubble-{version}'] = version_passed
            all_tests_passed &= version_passed

        print_header(_('FINAL TEST RESULTS'))
        safe_print(_('📊 Test Summary:'))
        for version_key, passed in test_results.items():
            status = _('✅ PASSED') if passed else _('❌ FAILED')
            safe_print(_('   {}: {}').format(version_key.ljust(25), status))

        if all_tests_passed:
            safe_print(_('\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉'))
        else:
            safe_print(_('\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥'))

        return all_tests_passed
    except Exception as e:
        safe_print(_('\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        # --- SMART CLEANUP ---
        print_header(_('STEP 4: Cleanup & Restoration'))
        try:
            config_manager = ConfigManager(suppress_init_messages=True)
            omnipkg_core = OmnipkgCore(config_manager)

            # Only clean up the bubbles we created for the test
            for version in BUBBLE_VERSIONS_TO_TEST:
                bubble_path = omnipkg_core.multiversion_base / f'uv-{version}'
                if bubble_path.is_dir():
                    safe_print(_('   🧹 Removing test bubble: {}').format(bubble_path.name))
                    shutil.rmtree(bubble_path, ignore_errors=True)

            safe_print('   ✅ Main environment has been preserved.')

            if original_strategy and original_strategy != 'stable-main':
                restore_install_strategy(config_manager, original_strategy)
                safe_print(_('   💡 Note: Install strategy has been restored to: {}').format(original_strategy))

            safe_print(_('✅ Cleanup complete'))
        except Exception as e:
            safe_print(_('⚠️  Cleanup failed: {}').format(e))


if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)