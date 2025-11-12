import sys
import os
from pathlib import Path
import subprocess
import shutil
import traceback
from importlib.metadata import version as get_version, PackageNotFoundError

# Setup project path for imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

# Apply language settings
lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env:
    _.set_language(lang_from_env)

# Import core modules after path is set
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    safe_print(f'❌ Failed to import omnipkg modules. Is the project structure correct? Error: {e}')
    sys.exit(1)

# --- Test Configuration ---
# Fallback version ONLY if uv is not installed at all.
MAIN_UV_VERSION_FALLBACK = '0.9.5' # A recent, known-good version
BUBBLE_VERSIONS_TO_TEST = ['0.4.30', '0.5.11']


def print_header(title):
    safe_print('\n' + '=' * 80)
    safe_print(f'  🚀 {title}')
    safe_print('=' * 80)


def print_subheader(title):
    safe_print(f'\n--- {title} ---')


def run_command(command, check=True):
    """Helper to run a command and capture output."""
    return subprocess.run(command, capture_output=True, text=True, check=check)


def setup_environment(omnipkg_core: OmnipkgCore):
    """
    (FLEXIBLE) Prepares the environment by detecting the existing `uv` version
    or installing a safe fallback if it's missing.
    """
    print_header('STEP 1: Environment Setup & Cleanup')
    
    # REMOVED: Cleanup of old demo bubbles
    safe_print('   ⚠️  Skipping cleanup - files will be preserved for inspection')

    # --- THIS IS YOUR CORRECT, FLEXIBLE DETECTION LOGIC ---
    main_uv_version = None
    try:
        main_uv_version = get_version('uv')
        safe_print(f"   ✅ Found existing uv v{main_uv_version}. It will be used as the main version for the demo.")
    except PackageNotFoundError:
        safe_print(f"   ℹ️  'uv' not found in main environment. Installing a baseline version ({MAIN_UV_VERSION_FALLBACK}) for the demo.")
        omnipkg_core.smart_install([f'uv=={MAIN_UV_VERSION_FALLBACK}'])
        main_uv_version = MAIN_UV_VERSION_FALLBACK
    # --- END OF DETECTION LOGIC ---

    force_omnipkg_rescan(omnipkg_core, 'uv')
    safe_print('✅ Environment prepared')
    return main_uv_version


def create_test_bubbles(omnipkg_core: OmnipkgCore):
    """
    (CORRECTED) Create test bubbles for older UV versions using the
    direct bubble creation method to bypass satisfaction checks.
    """
    print_header('STEP 2: Creating Test Bubbles for Older Versions')
    
    # Get the Python version string for the current context
    python_context_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    for version in BUBBLE_VERSIONS_TO_TEST:
        bubble_name = f'uv-{version}'
        bubble_path = omnipkg_core.multiversion_base / bubble_name
        
        # Only create if it doesn't already exist from a previous failed run
        if bubble_path.exists():
            safe_print(f'   ✅ Bubble for uv=={version} already exists. Skipping creation.')
            continue

        safe_print(f'   🫧 Force-creating bubble for uv=={version}...')
        try:
            # --- THIS IS THE CRITICAL FIX ---
            # Use the direct, forceful bubble creation method
            success = omnipkg_core.bubble_manager.create_isolated_bubble(
                'uv', version, python_context_version=python_context_version
            )
            
            if success:
                safe_print(f'   ✅ Bubble created: {bubble_name}')
                # Also ensure the KB knows about this new bubble
                omnipkg_core.rebuild_package_kb([f'uv=={version}'])
            else:
                 safe_print(f'   ❌ Failed to create bubble for uv=={version}')

        except Exception as e:
            safe_print(f'   ❌ Failed to create bubble for uv=={version}: {e}')
            traceback.print_exc()


def force_omnipkg_rescan(omnipkg_core, package_name):
    """Tells omnipkg to forcibly rescan a specific package's metadata."""
    safe_print(f'   🧠 Forcing omnipkg KB rebuild for {package_name}...')
    try:
        omnipkg_core.rebuild_package_kb([package_name])
    except Exception as e:
        safe_print(f'   ❌ KB rebuild for {package_name} failed: {e}')


def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    safe_print(f'   🔍 Inspecting bubble structure: {bubble_path.name}')
    if not bubble_path.exists():
        safe_print(f"   ❌ Bubble doesn't exist: {bubble_path}")
        return False
    
    dist_info = list(bubble_path.glob('uv-*.dist-info'))
    if dist_info:
        safe_print(f'   ✅ Found dist-info: {dist_info[0].name}')
    else:
        safe_print('   ⚠️  No dist-info found')

    scripts_dir = bubble_path / 'bin'
    if scripts_dir.exists():
        items = list(scripts_dir.iterdir())
        safe_print(f'   ✅ Found bin directory with {len(items)} items')
        uv_bin = scripts_dir / 'uv'
        if uv_bin.exists():
            safe_print(f'   ✅ Found uv binary: {uv_bin}')
            if os.access(uv_bin, os.X_OK):
                safe_print('   ✅ Binary is executable')
            else:
                safe_print('   ⚠️  Binary is not executable')
        else:
            safe_print('   ⚠️  No uv binary in bin/')
    else:
        safe_print('   ⚠️  No bin directory found')
        
    contents = list(bubble_path.iterdir())
    safe_print(f'   📁 Bubble contents ({len(contents)} items):')
    for item in sorted(contents)[:5]:
        suffix = '/' if item.is_dir() else ''
        safe_print(f'      - {item.name}{suffix}')
        
    return True


def test_swapped_binary_execution(expected_version, config, omnipkg_core):
    """Tests version swapping using omnipkgLoader with enhanced debugging."""
    safe_print('   🔧 Testing swapped binary execution via omnipkgLoader...')
    
    bubble_path = omnipkg_core.multiversion_base / f'uv-{expected_version}'
    bubble_binary = bubble_path / 'bin' / 'uv'
    
    try:
        with omnipkgLoader(f'uv=={expected_version}', config=config, quiet=True, force_activation=True):

            # Debug: Show PATH and which binary is found
            path_entries = os.environ.get('PATH', '').split(os.pathsep)
            safe_print(f'   🔍 First 3 PATH entries: {path_entries[:3]}')
            safe_print(f'   🔍 Which uv: {shutil.which("uv")}')
            safe_print(f'   🔍 Bubble binary: {bubble_binary}')
            
            # Test using system PATH
            result = run_command(['uv', '--version'])
            actual_version = result.stdout.strip().split()[-1]
            safe_print(f'   📍 Version via PATH: {actual_version}')
            
            # Test using direct bubble path
            result_direct = run_command([str(bubble_binary), '--version'])
            direct_version = result_direct.stdout.strip().split()[-1]
            safe_print(f'   📍 Version via direct path: {direct_version}')
            
            if actual_version == expected_version:
                safe_print(f'   ✅ Swapped binary reported: {actual_version}')
                safe_print('   🎯 Swapped binary test: PASSED')
                return True
            else:
                safe_print(f'   ❌ Version mismatch: expected {expected_version}, got {actual_version}')
                if direct_version == expected_version:
                    safe_print(f'   ⚠️  BUT direct binary path shows correct version {direct_version}')
                    safe_print(f'   ⚠️  This suggests PATH manipulation issue')
                return False
    except Exception as e:
        safe_print(f'   ❌ Exception during test: {e}')
        traceback.print_exc()
        return False


def run_comprehensive_test():
    """Main function to orchestrate the test with robust strategy and version handling."""
    print_header('🚨 OMNIPKG UV BINARY STRESS TEST (NO CLEANUP) 🚨')
    
    config_manager = None
    original_strategy = None
    main_uv_version_to_test = None
    
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        omnipkg_core = OmnipkgCore(config_manager)

        original_strategy = config_manager.config.get('install_strategy', 'stable-main')
        if original_strategy != 'stable-main':
            safe_print(f"   ℹ️  Current install strategy: {original_strategy}")
            safe_print("   ⚙️  Temporarily setting install strategy to 'stable-main' for this test...")
            config_manager.set('install_strategy', 'stable-main')
            omnipkg_core = OmnipkgCore(config_manager)

        main_uv_version_to_test = setup_environment(omnipkg_core)
        
        create_test_bubbles(omnipkg_core)
        print_header('STEP 3: Comprehensive UV Version Testing')

        test_results = {}
        all_tests_passed = True

        # Test Main Environment
        print_subheader(f'Testing Main Environment (uv=={main_uv_version_to_test})')
        try:
            python_exe = config_manager.config.get('python_executable', sys.executable)
            uv_binary_path = Path(python_exe).parent / 'uv'
            
            safe_print(f'   🔬 Testing binary at: {uv_binary_path}')

            result = run_command([str(uv_binary_path), '--version'])
            actual_version = result.stdout.strip().split()[-1]

            main_passed = actual_version == main_uv_version_to_test
            safe_print(f'   ✅ Main environment version: {actual_version}')
            if not main_passed:
                safe_print(f'   ❌ FAILED: Expected {main_uv_version_to_test} but found {actual_version}')

            test_results[f'main-{main_uv_version_to_test}'] = main_passed
            all_tests_passed &= main_passed
        except Exception as e:
            safe_print(f'   ❌ Main environment test failed: {e}')
            test_results[f'main-{main_uv_version_to_test}'] = False
            all_tests_passed = False

        # Test Bubbles
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(f'Testing Bubble (uv=={version})')
            bubble_path = omnipkg_core.multiversion_base / f'uv-{version}'
            if not inspect_bubble_structure(bubble_path):
                test_results[f'bubble-{version}'] = False
                all_tests_passed = False
                continue
            version_passed = test_swapped_binary_execution(version, config_manager.config, omnipkg_core)
            test_results[f'bubble-{version}'] = version_passed
            all_tests_passed &= version_passed

        # Report Results
        print_header('FINAL TEST RESULTS')
        safe_print('📊 Test Summary:')
        for version_key, passed in sorted(test_results.items()):
            status = '✅ PASSED' if passed else '❌ FAILED'
            safe_print(f'   {version_key.ljust(25)}: {status}')

        if not all_tests_passed:
            safe_print('\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥')
        else:
            safe_print('\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉')
        
        # REMOVED: All cleanup code
        safe_print('\n📁 Bubble files preserved for inspection')
        safe_print(f'📍 Location: {omnipkg_core.multiversion_base}')
        
        return all_tests_passed

    except Exception as e:
        safe_print(f'\n❌ Critical error during testing: {e}')
        traceback.print_exc()
        return False
    finally:
        # Only restore config, NO file cleanup
        if config_manager and original_strategy and original_strategy != 'stable-main':
            safe_print(f"\n🔄 Restoring original install strategy: {original_strategy}")
            config_manager.set('install_strategy', original_strategy)


if __name__ == '__main__':
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)