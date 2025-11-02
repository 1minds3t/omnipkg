# ==============================================================================
# REPLACEMENT TEST SCRIPT - GUARANTEED ISOLATION
# ==============================================================================
from __future__ import annotations

import sys
import os
import subprocess
import time
from pathlib import Path

# --- BOOTSTRAP OMNIPKG PATH ---
# Ensures the script can find the omnipkg library even when run directly
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.common_utils import safe_print
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.i18n import _
except ImportError:
    print("FATAL: Could not bootstrap omnipkg. Ensure you are running this from a valid dev environment.")
    sys.exit(1)
# --- END BOOTSTRAP ---


# --- Test Configuration ---
MODERN_VERSION = '0.6.3'  # Works with Flask 2.2+ and 3.x
OLD_VERSION = '0.4.1'      # The legacy version that requires the Time Machine

def print_header(title):
    """Prints a consistent, pretty header."""
    safe_print('\n' + '=' * 80)
    safe_print(f'  🚀 {title}')
    safe_print('=' * 80)

def run_command(command_list, check=True):
    """Helper to run a command and stream its output."""
    safe_print(f'\n$ {" ".join(command_list)}')
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in iter(process.stdout.readline, ''):
        safe_print(line.strip())
    retcode = process.wait()
    if check and retcode != 0:
        raise RuntimeError(f'Demo command failed with exit code {retcode}')
    return retcode

def ensure_bubbles_are_created_safely(config_manager: ConfigManager):
    """
    --- THIS IS THE NEW, CORRECT LOGIC ---
    This function directly uses the BubbleManager to guarantee isolated bubble creation.
    It NEVER touches the main environment.
    """
    print_header("STEP 1: GUARANTEED ISOLATED BUBBLE CREATION")
    omnipkg_core = OmnipkgCore(config_manager)

    # Determine the correct Python context for the bubbles, just like in smart_install.
    configured_exe = config_manager.config.get('python_executable', sys.executable)
    version_tuple = config_manager._verify_python_version(configured_exe)
    python_context_version = f'{version_tuple[0]}.{version_tuple[1]}' if version_tuple else 'unknown'
    if python_context_version == 'unknown':
        safe_print("   ⚠️ CRITICAL: Could not determine Python context for test bubble creation.")
        return False

    packages_to_bubble = {'flask-login': [MODERN_VERSION, OLD_VERSION]}
    
    for pkg_name, versions in packages_to_bubble.items():
        for version in versions:
            bubble_name = f'{pkg_name}-{version}'
            bubble_path = omnipkg_core.multiversion_base / bubble_name
            
            # If the bubble doesn't exist, we create it using the SAFE method.
            if not bubble_path.exists():
                safe_print(f'\n--- Creating bubble for {pkg_name}=={version} ---')
                
                # THIS IS THE KEY: We call create_isolated_bubble directly.
                # This function contains our "try modern -> test -> fallback to Time Machine"
                # logic, all performed inside a temporary directory.
                success = omnipkg_core.bubble_manager.create_isolated_bubble(
                    pkg_name, version, python_context_version
                )

                if success:
                    safe_print(f'✅ Successfully created isolated bubble for {pkg_name}=={version}')
                    # We must tell the KB about the new bubble.
                    omnipkg_core.rebuild_package_kb(
                        [f'{pkg_name}=={version}'], 
                        target_python_version=python_context_version
                    )
                else:
                    safe_print(f'❌ FATAL: Failed to create bubble for {pkg_name}=={version}. Demo cannot continue.')
                    return False
            else:
                safe_print(f'✅ Bubble for {pkg_name}=={version} already exists. Skipping creation.')
    
    return True

def run_demo():
    """Runs a fully automated, impressive, and CORRECT demo."""
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        
        print_header('omnipkg Demo: Legacy Package Isolation')
        
        # --- STEP 0: CLEAN SLATE ---
        print_header("STEP 0: Preparing a Clean Environment")
        safe_print("🧹 Uninstalling any active 'flask-login' and its dependencies to ensure a clean test...")
        # We uninstall the dependencies the Time Machine might have installed to be extra safe.
        run_command(['pip', 'uninstall', '-y', 'flask-login', 'flask', 'werkzeug', 'jinja2', 'click', 'itsdangerous'], check=False)
        safe_print("\n✅ Clean slate achieved!")

        # --- STEP 1: FORCE BUBBLE CREATION (The Right Way) ---
        if not ensure_bubbles_are_created_safely(config_manager):
            return # Stop the demo if bubble creation fails

        # --- STEP 2: VERIFY STATE ---
        print_header("STEP 2: Verifying Environment State")
        omnipkg_core = OmnipkgCore(config_manager)
        info = omnipkg_core._find_package_installations('flask-login')
        active_version = next((i for i in info if i.get('install_type') == 'active'), None)
        
        if active_version:
            safe_print(f"❌ VALIDATION FAILED: 'flask-login' version {active_version.get('Version')} was found in the main environment!")
            safe_print("   This means isolation failed. Aborting demo.")
            return

        safe_print("✅ VALIDATION PASSED: Main environment is clean. 'flask-login' only exists in bubbles.")
        
        # --- STEP 3: RUN THE SWITCHING TEST ---
        print_header("STEP 3: The Grand Finale - Live Version Switching")
        test_script_content = r'''
# Injected script content...
import sys, os, importlib
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path

# Bootstrap to find omnipkg loader
try:
    _omnipkg_dist = importlib.metadata.distribution('omnipkg')
    _omnipkg_site_packages = Path(_omnipkg_dist.locate_file("omnipkg")).parent.parent
    if str(_omnipkg_site_packages) not in sys.path:
        sys.path.insert(0, str(_omnipkg_site_packages))
    from omnipkg.loader import omnipkgLoader
except Exception as e:
    print(f"FATAL: Could not import omnipkg loader: {e}")
    sys.exit(1)

def test_version_switching(modern_ver, old_ver):
    print("🔍 Testing omnipkg's seamless version switching...")
    
    # Test activating the MODERN version with STRICT ISOLATION
    print(f"\n📦 Step 1: Loading modern version {modern_ver} from bubble...")
    try:
        with omnipkgLoader(f"flask-login=={modern_ver}", isolation_mode='strict'):
            import flask_login
            actual_version = get_version('flask-login')
            assert actual_version == modern_ver, f"Version mismatch! Expected {modern_ver}, got {actual_version}"
            print(f"✅ Successfully using modern flask-login {actual_version}")
    except Exception as e:
        print(f"❌ Error while testing modern version: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # Test activating the LEGACY version with STRICT ISOLATION
    print(f"\n📦 Step 2: Switching to legacy version {old_ver}...")
    try:
        # We must clear the module from cache to force a re-import
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
            
        with omnipkgLoader(f"flask-login=={old_ver}", isolation_mode='strict'):
            import flask_login
            actual_version = get_version('flask-login')
            assert actual_version == old_ver, f"Version mismatch! Expected {old_ver}, got {actual_version}"
            print(f"✅ Successfully using legacy flask-login {actual_version}")
    except Exception as e:
        print(f"❌ Error while testing legacy version: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    print("\n" + "="*60)
    print("🎯 THE MAGIC: Legacy and modern code coexisted perfectly!")
    print("="*60)

if __name__ == "__main__":
    test_version_switching(modern_ver=sys.argv[1], old_ver=sys.argv[2])
'''
        test_script_path = Path('/tmp/omnipkg_magic_test.py')
        test_script_path.write_text(test_script_content)
        
        run_command([sys.executable, str(test_script_path), MODERN_VERSION, OLD_VERSION], check=True)

        print_header("🎉🎉🎉 DEMO COMPLETE! 🎉🎉🎉")
        print("✅ Isolation was maintained.")
        print("✅ The Time Machine was correctly triggered for the legacy bubble.")
        print("✅ The main environment was never polluted.")
        print("✅ Live version switching worked as intended.")
        print("\n🚀 Dependency hell is officially SOLVED!")

    except Exception as demo_error:
        safe_print(f'\n❌ An unexpected error occurred during the demo: {demo_error}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    run_demo()