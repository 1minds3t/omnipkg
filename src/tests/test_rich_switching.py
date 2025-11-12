import sys
from pathlib import Path
import subprocess
import shutil
import traceback

# Setup project path to allow omnipkg imports
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from omnipkg.common_utils import safe_print
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.loader import omnipkgLoader

# This is now just a fallback if Rich isn't installed at all.
DEFAULT_RICH_VERSION = '13.7.1'
BUBBLE_VERSIONS_TO_TEST = ['13.5.3', '13.4.2']

def print_header(title):
    safe_print('\n' + '=' * 80)
    safe_print(f'  🚀 {title}')
    safe_print('=' * 80)

def setup_environment(omnipkg_core: OmnipkgCore):
    """
    Adapts to the current environment. Installs a baseline Rich only if
    one is not already present. Cleans up old bubbles from previous runs.
    Returns the version of Rich that will be used as the main version.
    """
    print_header('STEP 1: Environment Setup & Cleanup')
    omnipkg_core.config_manager.set('install_strategy', 'stable-main')
    safe_print("   ⚙️  Install strategy set to: stable-main")
    
    # Clean up artifacts (bubbles) from any previous demo runs.
    safe_print('   🧹 Cleaning up old demo bubbles...')
    for bubble in omnipkg_core.multiversion_base.glob('rich-*'):
        shutil.rmtree(bubble, ignore_errors=True)

    # --- START: CORRECTED ADAPTIVE LOGIC ---
    # Use the reliable internal method to find all installations of 'rich'.
    all_installations = omnipkg_core._find_package_installations('rich')
    
    # Find the one that is marked as 'active'.
    active_install_info = next(
        (inst for inst in all_installations if inst.get('install_type') == 'active'), 
        None
    )
    
    main_rich_version = None
    if active_install_info:
        main_rich_version = active_install_info.get('Version')

    if main_rich_version:
        safe_print(f"   ✅ Found existing Rich v{main_rich_version}. It will be used as the main version for the demo.")
    else:
        safe_print(f"   ℹ️  Rich not found. Installing a baseline version ({DEFAULT_RICH_VERSION}) for the demo.")
        omnipkg_core.smart_install([f'rich=={DEFAULT_RICH_VERSION}'])
        main_rich_version = DEFAULT_RICH_VERSION
    # --- END: CORRECTED ADAPTIVE LOGIC ---

    safe_print('✅ Environment prepared')
    return main_rich_version

def create_test_bubbles(omnipkg_core: OmnipkgCore):
    print_header('STEP 2: Creating Test Bubbles for Older Versions')
    for version in BUBBLE_VERSIONS_TO_TEST:
        # Prevent creating a bubble that matches the main version
        # This avoids redundant installations and potential conflicts
        main_version = omnipkg_core._find_package_installations('rich')
        active_version = next((inst.get('Version') for inst in main_version if inst.get('install_type') == 'active'), None)
        if version == active_version:
            safe_print(f"   ℹ️  Skipping bubble for v{version} as it matches the active main version.")
            continue
            
        safe_print(f'   🫧 Creating bubble for rich=={version}')
        omnipkg_core.smart_install([f'rich=={version}'])

def test_version_in_context(expected_version: str, config, is_bubble: bool):
    """The actual test logic to verify the correct version is active."""
    from importlib.metadata import version
    
    if is_bubble:
        with omnipkgLoader(f"rich=={expected_version}", config=config, quiet=True):
            import rich
            actual_version = version('rich')
            assert actual_version == expected_version, f"Bubble test failed! Expected {expected_version}, got {actual_version}"
    else:
        import rich
        actual_version = version('rich')
        assert actual_version == expected_version, f"Main env test failed! Expected {expected_version}, got {actual_version}"
    
    safe_print(f"✅ Imported and verified version {actual_version}")

def run_comprehensive_test():
    # Keep track of the main version to preserve it during cleanup
    main_version_to_preserve = None
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        omnipkg_core = OmnipkgCore(config_manager)
        
        main_version_to_preserve = setup_environment(omnipkg_core)
        create_test_bubbles(omnipkg_core)
        
        print_header('STEP 3: Comprehensive Version Testing')
        test_results = {}

        # Test Main Environment (using the detected or installed version)
        safe_print(f'\n--- Testing Main Environment (rich=={main_version_to_preserve}) ---')
        try:
            test_version_in_context(main_version_to_preserve, omnipkg_core.config, is_bubble=False)
            test_results[f'main-{main_version_to_preserve}'] = True
        except Exception as e:
            safe_print(f"   ❌ FAILED: {e}")
            traceback.print_exc()
            test_results[f'main-{main_version_to_preserve}'] = False

        # Test Bubbled Versions
        for version in BUBBLE_VERSIONS_TO_TEST:
            if version == main_version_to_preserve:
                continue # Don't test a bubble that matches the main version

            safe_print(f'\n--- Testing Bubble (rich=={version}) ---')
            try:
                test_version_in_context(version, omnipkg_core.config, is_bubble=True)
                test_results[f'bubble-{version}'] = True
            except Exception as e:
                safe_print(f"   ❌ FAILED: {e}")
                traceback.print_exc()
                test_results[f'bubble-{version}'] = False

        print_header('FINAL TEST RESULTS')
        all_passed = all(test_results.values())
        for name, passed in test_results.items():
            safe_print(f"   {name.ljust(25)}: {'✅ PASSED' if passed else '❌ FAILED'}")
        
        return all_passed

    except Exception as e:
        safe_print(f'\n❌ Critical error during testing: {e}')
        traceback.print_exc()
        return False
    finally:
        print_header('STEP 4: Cleanup & Restoration')
        if 'omnipkg_core' in locals():
            safe_print('   🧹 Cleaning up demo bubbles...')
            omnipkg_core.smart_uninstall([f'rich=={v}' for v in BUBBLE_VERSIONS_TO_TEST], force=True)
            
            if main_version_to_preserve:
                 safe_print(f'   ✅ Main environment with Rich v{main_version_to_preserve} has been preserved.')
            else:
                 safe_print('   ✅ Main environment has been left untouched.')

        safe_print('✅ Cleanup complete')

if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)