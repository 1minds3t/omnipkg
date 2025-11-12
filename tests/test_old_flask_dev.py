# ==============================================================================
# FIXED FLASK TEST - MAIN ENV + BUBBLE (Like the UV test)
# ==============================================================================
from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path

# --- BOOTSTRAP OMNIPKG PATH ---
try:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from omnipkg.common_utils import safe_print
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.i18n import _
except ImportError:
    print("FATAL: Could not bootstrap omnipkg. Ensure you are running this from a valid dev environment.")
    sys.exit(1)

# --- Test Configuration ---
MAIN_VERSION = '0.6.3'   # Install this in MAIN environment
BUBBLE_VERSION = '0.4.1'  # Create bubble for this old version

def print_header(title):
    safe_print('\n' + '=' * 80)
    safe_print(f'  🚀 {title}')
    safe_print('=' * 80)

def run_command(command_list, check=True):
    safe_print(f'\n$ {" ".join(command_list)}')
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in iter(process.stdout.readline, ''):
        safe_print(line.strip())
    retcode = process.wait()
    if check and retcode != 0:
        raise RuntimeError(f'Demo command failed with exit code {retcode}')
    return retcode

def setup_main_environment(omnipkg_core: OmnipkgCore):
    """Install MAIN version (0.6.3) in the main environment"""
    print_header("STEP 1: Installing Main Version in Main Environment")
    safe_print(f"📦 Installing flask-login=={MAIN_VERSION} to main environment...")
    
    # Uninstall any existing version first
    run_command(['pip', 'uninstall', '-y', 'flask-login'], check=False)
    
    # Install the main version
    omnipkg_core.smart_install([f'flask-login=={MAIN_VERSION}'])
    
    # Verify it's installed
    try:
        from importlib.metadata import version
        actual_version = version('flask-login')
        if actual_version == MAIN_VERSION:
            safe_print(f"✅ Main environment has flask-login {actual_version}")
            return True
        else:
            safe_print(f"❌ Version mismatch: expected {MAIN_VERSION}, got {actual_version}")
            return False
    except Exception as e:
        safe_print(f"❌ Failed to verify main installation: {e}")
        return False

def create_bubble_for_old_version(omnipkg_core: OmnipkgCore):
    """Create bubble for OLD version (0.4.1) - THIS is where Time Machine runs"""
    print_header("STEP 2: Creating Bubble for Legacy Version")
    
    python_context_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    bubble_name = f'flask-login-{BUBBLE_VERSION}'
    bubble_path = omnipkg_core.multiversion_base / bubble_name
    
    if bubble_path.exists():
        safe_print(f'✅ Bubble for flask-login=={BUBBLE_VERSION} already exists. Skipping creation.')
        return True
    
    safe_print(f'🫧 Creating isolated bubble for flask-login=={BUBBLE_VERSION}...')
    safe_print(f'   (This will trigger the Time Machine for ancient dependencies)')
    
    try:
        success = omnipkg_core.bubble_manager.create_isolated_bubble(
            'flask-login', BUBBLE_VERSION, python_context_version=python_context_version
        )
        
        if success:
            safe_print(f'✅ Successfully created bubble for flask-login=={BUBBLE_VERSION}')
            omnipkg_core.rebuild_package_kb([f'flask-login=={BUBBLE_VERSION}'])
            return True
        else:
            safe_print(f'❌ Failed to create bubble for flask-login=={BUBBLE_VERSION}')
            return False
    except Exception as e:
        safe_print(f'❌ Error creating bubble: {e}')
        import traceback
        traceback.print_exc()
        return False

def test_version_switching():
    """Test that we can use main version normally and swap to bubble version"""
    print_header("STEP 3: Testing Version Switching")
    
    test_script_content = r'''
import sys, os
from importlib.metadata import version as get_version
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
# Bootstrap omnipkg loader
try:
    import importlib.metadata
    _omnipkg_dist = importlib.metadata.distribution('omnipkg')
    _omnipkg_site_packages = Path(_omnipkg_dist.locate_file("omnipkg")).parent.parent
    if str(_omnipkg_site_packages) not in sys.path:
        sys.path.insert(0, str(_omnipkg_site_packages))
    from omnipkg.loader import omnipkgLoader
except Exception as e:
    print(f"FATAL: Could not import omnipkg loader: {e}")
    sys.exit(1)

def test_versions(main_ver, bubble_ver):
    safe_print("🔍 Testing version switching...")
    
    # Test 1: Main environment version (should just work normally)
    safe_print(f"\n📦 Test 1: Using main environment version ({main_ver})...")
    try:
        import flask_login
        actual_version = get_version('flask-login')
        assert actual_version == main_ver, f"Expected {main_ver}, got {actual_version}"
        safe_print(f"✅ Main environment: flask-login {actual_version}")
        
        # Clean up module for next test
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
        if 'flask' in sys.modules:
            del sys.modules['flask']
    except Exception as e:
        safe_print(f"❌ Main environment test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Test 2: Bubble version (using omnipkgLoader)
    safe_print(f"\n📦 Test 2: Switching to bubble version ({bubble_ver})...")
    try:
        with omnipkgLoader(f"flask-login=={bubble_ver}"):
            import flask_login
            actual_version = get_version('flask-login')
            assert actual_version == bubble_ver, f"Expected {bubble_ver}, got {actual_version}"
            safe_print(f"✅ Bubble version: flask-login {actual_version}")
    except Exception as e:
        safe_print(f"❌ Bubble version test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "="*60)
    safe_print("🎯 SUCCESS: Version switching works perfectly!")
    print(f"   Main env: {main_ver}")
    print(f"   Bubble:   {bubble_ver}")
    print("="*60)

if __name__ == "__main__":
    test_versions(main_ver=sys.argv[1], bubble_ver=sys.argv[2])
'''
    
    test_script_path = Path('/tmp/flask_version_test.py')
    test_script_path.write_text(test_script_content)
    
    run_command([sys.executable, str(test_script_path), MAIN_VERSION, BUBBLE_VERSION], check=True)

def run_demo():
    """Main demo orchestration"""
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        omnipkg_core = OmnipkgCore(config_manager)
        
        print_header('Flask-Login Version Switching Demo')
        safe_print(f"Main version:   {MAIN_VERSION}")
        safe_print(f"Bubble version: {BUBBLE_VERSION}")
        
        # Step 1: Install main version
        if not setup_main_environment(omnipkg_core):
            safe_print("❌ Failed to setup main environment")
            return
        
        # Step 2: Create bubble for old version
        if not create_bubble_for_old_version(omnipkg_core):
            safe_print("❌ Failed to create bubble")
            return
        
        # Step 3: Test switching
        test_version_switching()
        
        print_header("🎉 DEMO COMPLETE! 🎉")
        safe_print(f"✅ Main environment: flask-login {MAIN_VERSION}")
        safe_print(f"✅ Bubble created: flask-login {BUBBLE_VERSION}")
        safe_print("✅ Version switching works perfectly!")
        safe_print("\n🚀 Time Machine successfully handled the legacy version!")
        
    except Exception as e:
        safe_print(f'\n❌ Demo failed: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    run_demo()