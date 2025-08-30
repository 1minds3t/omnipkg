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

def pip_uninstall_uv():
    """Use pip to directly uninstall uv from main environment"""
    print(_('   🧹 Using pip to uninstall uv from main environment...'))
    try:
        result = subprocess.run(['pip', 'uninstall', 'uv', '-y'], 
                              capture_output=True, text=True, check=False)
        if result.returncode == 0:
            print(_('   ✅ pip uninstall uv completed successfully'))
        else:
            print(_('   ℹ️  pip uninstall completed (uv may not have been installed)'))
        return True
    except Exception as e:
        print(_('   ⚠️  pip uninstall failed: {}').format(e))
        return False

def pip_install_uv(version):
    """Use pip to directly install specific uv version"""
    print(_('   📦 Using pip to install uv=={}...').format(version))
    try:
        result = subprocess.run(['pip', 'install', f'uv=={version}'], 
                              capture_output=True, text=True, check=True)
        print(_('   ✅ pip install uv=={} completed successfully').format(version))
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
    omnipkg_core = OmnipkgCore(config_manager.config)
    
    # Clean up any existing bubbles and cloaked packages
    print(_('   🧹 Cleaning up existing UV installations and bubbles...'))
    for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
        if bubble.is_dir():
            print(_('   🧹 Removing old bubble: {}').format(bubble.name))
            shutil.rmtree(bubble, ignore_errors=True)
    
    site_packages = Path(config_manager.config['site_packages_path'])
    for cloaked in site_packages.glob('uv.*_omnipkg_cloaked*'):
        print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
        shutil.rmtree(cloaked, ignore_errors=True)
    
    for cloaked in site_packages.glob('uv.*_test_harness_cloaked*'):
        print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
        shutil.rmtree(cloaked, ignore_errors=True)
    
    # Use pip to ensure clean main environment installation
    pip_uninstall_uv()
    if not pip_install_uv('0.6.13'):
        print(_('   ❌ Failed to install main environment UV version'))
        return None, original_strategy
    
    print(_('✅ Environment prepared'))
    return config_manager.config, original_strategy

def create_test_bubbles(config):
    print_header('STEP 2: Creating Test Bubbles')
    omnipkg_core = OmnipkgCore(config)
    test_versions = ['0.4.30', '0.5.11']
    
    for version in test_versions:
        print(_('   🫧 Creating bubble for uv=={}').format(version))
        try:
            omnipkg_core.smart_install([f'uv=={version}'])
            print(_('   ✅ Bubble created: uv-{}').format(version))
        except Exception as e:
            print(_('   ❌ Failed to create bubble for uv=={}: {}').format(version, e))
    
    return test_versions

def validate_bubble(bubble_path, expected_version):
    print(_('   🔎 Validating bubble: {}').format(bubble_path.name))
    
    uv_bin = bubble_path / 'bin' / 'uv'
    if not uv_bin.exists():
        print(_('   ❌ No uv binary found in {}/bin').format(bubble_path))
        return False
    
    if not os.access(uv_bin, os.X_OK):
        print(_('   ❌ Binary {} is not executable').format(uv_bin))
        return False
    
    dist_info = list(bubble_path.glob('uv-*.dist-info'))
    if not dist_info:
        print(_('   ❌ No dist-info found in {}').format(bubble_path))
        return False
    
    print(_('   ✅ Bubble validation passed'))
    return True

def inspect_bubble_structure(bubble_path):
    print(_('   🔍 Inspecting bubble structure: {}').format(bubble_path.name))
    
    if not bubble_path.exists():
        print(_("   ❌ Bubble doesn't exist: {}").format(bubble_path))
        return False
    
    uv_module = bubble_path / 'uv'
    if uv_module.exists():
        print(_('   ✅ Found uv module directory'))
    else:
        print(_('   ℹ️  No uv module directory (likely binary-only package)'))
    
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
    for item in sorted(contents)[:10]:
        print(_('      - {}{}').format(item.name, '/' if item.is_dir() else ''))
    
    return True

def test_direct_binary_execution(bubble_path, expected_version):
    print(_('   🔧 Testing direct binary execution...'))
    
    uv_binary = bubble_path / 'bin' / 'uv'
    if not uv_binary.exists():
        print(_('   ❌ No UV binary found in bubble'))
        return False
    
    try:
        print(_('   🎯 Executing: {} --version').format(uv_binary))
        result = subprocess.run([str(uv_binary), '--version'], 
                              capture_output=True, text=True, timeout=10, check=True)
        actual_version = result.stdout.strip().split()[-1]
        print(_('   ✅ Direct binary reported: {}').format(actual_version))
        
        if actual_version == expected_version:
            print(_('   🎯 Direct binary test: PASSED'))
            return True
        else:
            print(_('   ❌ Version mismatch: expected {}, got {}').format(expected_version, actual_version))
            return False
    except Exception as e:
        print(_('   ❌ Direct binary execution failed: {}').format(e))
        return False

def test_main_environment_uv():
    """Test the main environment UV installation"""
    print_subheader('Testing Main Environment (uv==0.6.13)')
    try:
        result = subprocess.run(['uv', '--version'], capture_output=True, text=True, timeout=10, check=True)
        actual_version = result.stdout.strip().split()[-1]
        main_passed = actual_version == '0.6.13'
        print(_('   ✅ Main environment version: {}').format(actual_version))
        if main_passed:
            print(_('   🎯 Main environment test: PASSED'))
        else:
            print(_('   ❌ Main environment test: FAILED (expected 0.6.13, got {})').format(actual_version))
        return main_passed
    except Exception as e:
        print(_('   ❌ Main environment test failed: {}').format(e))
        return False

def restore_install_strategy(config_manager, original_strategy):
    """Restore the original install strategy"""
    if original_strategy != 'stable-main':
        print(_('   🔄 Restoring original install strategy: {}').format(original_strategy))
        return set_install_strategy(config_manager, original_strategy)
    return True

def run_comprehensive_test():
    print_header('🚨 OMNIPKG UV BINARY STRESS TEST 🚨')
    original_strategy = None
    
    try:
        config, original_strategy = setup_environment()
        if config is None:
            return False
        
        test_versions = create_test_bubbles(config)
        multiversion_base = Path(config['multiversion_base'])
        
        print_header('STEP 3: Comprehensive UV Version Testing')
        all_tests_passed = True
        test_results = {}
        
        # Test main environment
        main_passed = test_main_environment_uv()
        test_results['main'] = main_passed
        all_tests_passed &= main_passed
        
        # Test each bubble version
        for version in test_versions:
            print_subheader(_('Testing Bubble (uv=={})').format(version))
            bubble_path = multiversion_base / f'uv-{version}'
            
            if not inspect_bubble_structure(bubble_path) or not validate_bubble(bubble_path, version):
                test_results[version] = False
                all_tests_passed = False
                continue
            
            version_passed = test_direct_binary_execution(bubble_path, version)
            test_results[version] = version_passed
            all_tests_passed &= version_passed
            
            if version_passed:
                print(_('   🎯 Overall result for uv=={}: PASSED').format(version))
            else:
                print(_('   ❌ Overall result for uv=={}: FAILED').format(version))
        
        print_header('FINAL TEST RESULTS')
        print(_('📊 Test Summary:'))
        for version_key, passed in test_results.items():
            status = '✅ PASSED' if passed else '❌ FAILED'
            print(_('   uv=={}: {}').format(version_key, status))
        
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
        print_header('STEP 4: Cleanup & Restoration')
        try:
            config_manager = ConfigManager()
            omnipkg_core = OmnipkgCore(config_manager.config)
            site_packages = Path(config_manager.config['site_packages_path'])
            
            # Clean up test bubbles
            for bubble in omnipkg_core.multiversion_base.glob('uv-*'):
                if bubble.is_dir():
                    print(_('   🧹 Removing test bubble: {}').format(bubble.name))
                    shutil.rmtree(bubble, ignore_errors=True)
            
            # Clean up cloaked packages
            for cloaked in site_packages.glob('uv.*_omnipkg_cloaked*'):
                print(_('   🧹 Removing residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
            
            for cloaked in site_packages.glob('uv.*_test_harness_cloaked*'):
                print(_('   🧹 Removing test harness residual cloaked: {}').format(cloaked.name))
                shutil.rmtree(cloaked, ignore_errors=True)
            
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