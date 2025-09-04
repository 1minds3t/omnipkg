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

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.i18n import _

lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import run_command, print_header
except ImportError as e:
    print(_('❌ Failed to import omnipkg modules. Is the project structure correct? Error: {}').format(e))
    sys.exit(1)

LATEST_RICH_VERSION = '13.7.1'
BUBBLE_VERSIONS_TO_TEST = ['13.5.3', '13.4.2']

def print_header(title):
    print('\n' + '=' * 80)
    print(_('  🚀 {}').format(title))
    print('=' * 80)

def print_subheader(title):
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

def pip_uninstall_rich():
    """Use pip to directly uninstall rich from main environment"""
    print(_('   🧹 Using pip to uninstall rich from main environment...'))
    try:
        result = subprocess.run(['pip', 'uninstall', 'rich', '-y'],
                              capture_output=True, text=True, check=False)
        if result.returncode == 0:
            print(_('   ✅ pip uninstall rich completed successfully'))
        else:
            print(_('   ℹ️  pip uninstall completed (rich may not have been installed)'))
        return True
    except Exception as e:
        print(_('   ⚠️  pip uninstall failed: {}').format(e))
        return False

def pip_install_rich(version):
    """Use pip to directly install specific rich version"""
    print(_('   📦 Using pip to install rich=={}...').format(version))
    try:
        result = subprocess.run(['pip', 'install', f'rich=={version}'],
                              capture_output=True, text=True, check=True)
        print(_('   ✅ pip install rich=={} completed successfully').format(version))
        return True
    except Exception as e:
        print(_('   ❌ pip install failed: {}').format(e))
        return False

def setup_environment():
    print_header('STEP 1: Environment Setup & Cleanup')
    
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

    # Clean up any existing bubbles and cloaked packages
    print(_('   🧹 Cleaning up existing Rich installations and bubbles...'))
    for bubble in omnipkg_core.multiversion_base.glob('rich-*'):
        if bubble.is_dir():
            print(_('   🧹 Removing old bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)

    site_packages = Path(config_manager.config['site_packages_path'])
    for cloaked in site_packages.glob('rich.*_omnipkg_cloaked*'):
        print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
        shutil.rmtree(cloaked, ignore_errors=True)

    for cloaked in site_packages.glob('rich.*_test_harness_cloaked*'):
        print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
        shutil.rmtree(cloaked, ignore_errors=True)

    # Use pip to ensure clean main environment installation
    pip_uninstall_rich()
    if not pip_install_rich(LATEST_RICH_VERSION):
        print(_('   ❌ Failed to install main environment Rich version'))
        return None, original_strategy

    print(_('✅ Environment prepared'))
    # FIX: Return config_manager instead of config_manager.config
    return config_manager, original_strategy

def create_test_bubbles(config_manager):
    print_header('STEP 2: Creating Test Bubbles for Older Versions')
    # FIX: Pass config_manager directly to OmnipkgCore
    omnipkg_core = OmnipkgCore(config_manager)
    
    for version in BUBBLE_VERSIONS_TO_TEST:
        print(_('   🫧 Creating bubble for rich=={}').format(version))
        try:
            omnipkg_core.smart_install([f'rich=={version}'])
            print(_('   ✅ Bubble created: rich-{}').format(version))
        except Exception as e:
            print(_('   ❌ Failed to create bubble for rich=={}: {}').format(version, e))

    return BUBBLE_VERSIONS_TO_TEST

def test_python_import(expected_version: str, config_manager, is_bubble: bool):
    print(_('   🔧 Testing import of version {}...').format(expected_version))
    # FIX: Extract config dict from config_manager
    config = config_manager.config
    config_json_str = json.dumps(config)
    
    test_script_content = f'''\
import sys
import importlib
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import json

# Ensure omnipkg's root is in sys.path for importing its modules (e.g., omnipkg.loader)
sys.path.insert(0, r"{Path(__file__).resolve().parents[1].parent}")

from omnipkg.loader import omnipkgLoader

# omnipkg.core.ConfigManager is also imported in subprocess for omnipkgLoader if config is passed
def test_import_and_version():
    target_package_spec = "rich=={expected_version}"
    
    # Load config in the subprocess
    subprocess_config = json.loads('{config_json_str}')

    # If it's the main environment test, we don't use the loader context for explicit switching,
    # just import directly from the system state.
    if not {is_bubble}: # Use the boolean value directly
        try:
            actual_version = version('rich')
            expected_version = "{expected_version}"
            assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
            print(f"✅ Imported and verified version {{actual_version}}")
        except PackageNotFoundError:
            print(f"❌ Test failed: Package 'rich' not found in main environment.", file=sys.stderr)
            sys.exit(1)
        except AssertionError as e:
            print(f"❌ Test failed: {{e}}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ An unexpected error occurred in main env test: {{e}}", file=sys.stderr)
            sys.exit(1)
        return

    # For bubble tests, use the omnipkgLoader context manager
    try:
        with omnipkgLoader(target_package_spec, config=subprocess_config):
            # Inside this block, the specific 'rich' version should be active
            import rich
            actual_version = version('rich')
            expected_version = "{expected_version}"
            assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
            print(f"✅ Imported and verified version {{actual_version}}")
    except PackageNotFoundError:
        print(f"❌ Test failed: Package 'rich' not found in bubble context '{{target_package_spec}}'.", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"❌ Test failed: {{e}}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ An unexpected error occurred activating/testing bubble '{{target_package_spec}}': {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    test_import_and_version()
'''
    
    site_packages = Path(config['site_packages_path'])
    main_rich_dir = site_packages / 'rich'
    main_rich_dist = next(site_packages.glob('rich-*.dist-info'), None)
    cloaked_paths_by_test_harness = []
    temp_script_path = None

    try:
        if is_bubble:
            if main_rich_dir.exists():
                cloak_path = main_rich_dir.with_name(_('rich.{}test_harness_cloaked').format(int(time.time() * 1000)))
                shutil.move(main_rich_dir, cloak_path)
                cloaked_paths_by_test_harness.append((main_rich_dir, cloak_path))
            
            if main_rich_dist and main_rich_dist.exists():
                cloak_path = main_rich_dist.with_name(_('{}.{}test_harness_cloaked').format(main_rich_dist.name, int(time.time() * 1000)))
                shutil.move(main_rich_dist, cloak_path)
                cloaked_paths_by_test_harness.append((main_rich_dist, cloak_path))

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_script_content)
            temp_script_path = f.name

        python_exe = config.get('python_executable', sys.executable)
        
        result = subprocess.run([python_exe, temp_script_path], capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            print(_('      └── {}').format(result.stdout.strip()))
            return True
        else:
            print(_('   ❌ Subprocess FAILED for version {}:').format(expected_version))
            print(_('      STDERR: {}').format(result.stderr.strip()))
            return False
            
    except subprocess.CalledProcessError as e:
        print(_('   ❌ Subprocess FAILED for version {}:').format(expected_version))
        print(_('      STDERR: {}').format(e.stderr.strip()))
        return False
        
    finally:
        # Restore cloaked paths in reverse order
        for original, cloaked in reversed(cloaked_paths_by_test_harness):
            if cloaked.exists():
                if original.exists():
                    shutil.rmtree(original, ignore_errors=True)
                try:
                    shutil.move(cloaked, original)
                except Exception as e:
                    print(_('   ⚠️  Test harness failed to restore {} from {}: {}').format(original.name, cloaked.name, e))
        
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True

def run_comprehensive_test():
    print_header('🚨 OMNIPKG RICH LIBRARY STRESS TEST 🚨')
    original_strategy = None
    
    try:
        # FIX: setup_environment now returns config_manager, not config dict
        config_manager, original_strategy = setup_environment()
        if config_manager is None:
            return False
        
        # FIX: Pass config_manager instead of config dict
        test_versions_to_bubble = create_test_bubbles(config_manager)
        
        print_header('STEP 3: Comprehensive Version Testing')
        test_results = {}
        all_tests_passed = True
        
        print_subheader(_('Testing Main Environment (rich=={})').format(LATEST_RICH_VERSION))
        # FIX: Pass config_manager instead of config dict
        main_passed = test_python_import(LATEST_RICH_VERSION, config_manager, is_bubble=False)
        test_results[_('main-{}').format(LATEST_RICH_VERSION)] = main_passed
        all_tests_passed &= main_passed
        
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(_('Testing Bubble (rich=={})').format(version))
            # FIX: Pass config_manager instead of config dict
            bubble_passed = test_python_import(version, config_manager, is_bubble=True)
            test_results[_('bubble-{}').format(version)] = bubble_passed
            all_tests_passed &= bubble_passed
        
        print_header('FINAL TEST RESULTS')
        print(_('📊 Test Summary:'))
        for test_name, passed in test_results.items():
            status = '✅ PASSED' if passed else '❌ FAILED'
            print(_('   {}: {}').format(test_name.ljust(25), status))
        
        if all_tests_passed:
            print(_('\n🎉🎉🎉 ALL RICH LIBRARY TESTS PASSED! 🎉🎉🎉'))
            print(_('🔥 OMNIPKG RICH HANDLING IS FULLY FUNCTIONAL! 🔥'))
        else:
            print(_('\n💥 SOME TESTS FAILED - RICH HANDLING NEEDS WORK 💥'))
            print(_('🔧 Check the detailed output above for diagnostics'))
        
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
            site_packages = Path(config_manager.config['site_packages_path'])
            
            # Clean up test bubbles
            for bubble in omnipkg_core.multiversion_base.glob('rich-*'):
                if bubble.is_dir():
                    print(_('   🧹 Removing test bubble: {}').format(bubble.name))
                    shutil.rmtree(bubble, ignore_errors=True)
            
            # Clean up cloaked packages
            for cloaked in site_packages.glob('rich.*_omnipkg_cloaked*'):
                print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
            
            for cloaked in site_packages.glob('rich.*_test_harness_cloaked*'):
                print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
            
            # Restore main environment to latest version using pip
            print(_('   📦 Restoring main environment: rich=={}').format(LATEST_RICH_VERSION))
            pip_uninstall_rich()
            pip_install_rich(LATEST_RICH_VERSION)
            
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