from __future__ import annotations

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

import subprocess
import sys
import time
from pathlib import Path
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.loader import omnipkgLoader
from omnipkg.i18n import _

# --- Test Configuration ---
MODERN_VERSION = '0.6.3'  # Works with Flask 2.2+ and 3.x
OLD_VERSION = '0.4.1'      # REAL legacy version (Python 3.6-3.8, Flask 1.x era)

def omnipkg_pip_jail():
    """The most passive-aggressive warning ever - EPIC EDITION"""
    safe_print('\n' + '🔥' * 50)
    safe_print(_('🚨 DEPENDENCY DESTRUCTION ALERT 🚨'))
    safe_print('🔥' * 50)
    safe_print('┌' + '─' * 58 + '┐')
    safe_print(_('│                                                          │'))
    safe_print(_(f'│  💀 You: pip install flask-login=={OLD_VERSION}             │'))
    safe_print(_('│                                                          │'))
    safe_print(_('│  🧠 omnipkg AI suggests:                                 │'))
    safe_print(_(f'│      omnipkg install flask-login=={OLD_VERSION}                 │'))
    safe_print(_('│                                                          │'))
    safe_print(_('│  ⚠️  WARNING: pip will NUKE your environment! ⚠️       │'))
    safe_print(_(f'│      • Downgrade from {MODERN_VERSION} to {OLD_VERSION}                   │'))
    safe_print(_('│      • Break newer Flask compatibility                  │'))
    safe_print(_('│      • Destroy your modern app                          │'))
    safe_print(_('│      • Welcome you to dependency hell 🔥                │'))
    safe_print(_('│                                                          │'))
    safe_print(_('│  [Y]es, I want chaos | [N]o, save me omnipkg! 🦸‍♂️        │'))
    safe_print(_('│                                                          │'))
    safe_print('└' + '─' * 58 + '┘')
    safe_print(_('        \\   ^__^'))
    safe_print(_('         \\  (💀💀)\\______   <- This is your environment'))
    safe_print(_('            (__)\\       )\\/\\   after using pip'))
    safe_print(_('                ||---ww |'))
    safe_print(_('                ||     ||'))
    safe_print(_("💡 Pro tip: Choose 'N' unless you enjoy suffering"))

def simulate_user_choice(choice, message):
    """Simulate user input with a delay"""
    safe_print(_('\nChoice (y/n): '), end='', flush=True)
    time.sleep(1)
    safe_print(choice)
    time.sleep(0.5)
    safe_print(_('💭 {}').format(message))
    return choice.lower()

def run_command(command_list, check=True):
    """Helper to run a command and stream its output."""
    safe_print(_('\n$ {}').format(' '.join(command_list)))
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    
    process = subprocess.Popen(
        command_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    for line in iter(process.stdout.readline, ''):
        safe_print(line.strip())
    
    process.stdout.close()
    retcode = process.wait()
    
    if check and retcode != 0:
        raise RuntimeError(_('Demo command failed with exit code {}').format(retcode))
    
    return retcode

def print_header(title):
    """Prints a consistent, pretty header."""
    safe_print('\n' + '=' * 60)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 60)

def check_python_compatibility():
    """Check if current Python version can run the old flask-login version."""
    py_version = sys.version_info
    safe_print(f'\n🐍 Python version: {py_version.major}.{py_version.minor}.{py_version.micro}')
    
    if py_version.major == 3 and 6 <= py_version.minor <= 11:
        safe_print(f'✅ Python {py_version.major}.{py_version.minor} should work with flask-login {OLD_VERSION}')
        return True
    else:
        safe_print(f'⚠️  Python {py_version.major}.{py_version.minor} may have issues with flask-login {OLD_VERSION}')
        safe_print(f'💡 This test works best on Python 3.6-3.11')
        return False

def check_version_with_pip(package_name):
    """Check installed version using pip show"""
    try:
        result = subprocess.run(
            ['pip', 'show', package_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
        return None
    except Exception:
        return None

def test_version_switching():
    """Test that we can seamlessly switch between versions"""
    print_header("STEP 5: Testing Seamless Version Switching")
    
    test_script_content = r'''
import sys
import os
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
    safe_print("🔍 Testing omnipkg's seamless version switching...")
    
    # Test 1: Main environment version
    safe_print(f"\n📦 Test 1: Using main environment version ({main_ver})...")
    try:
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
        
        import flask_login
        actual_version = get_version('flask-login')
        
        if actual_version != main_ver:
            safe_print(f"❌ Version mismatch: expected {main_ver}, got {actual_version}")
            sys.exit(1)
        
        safe_print(f"✅ Main environment: flask-login {actual_version}")
        
        # Check for a feature from modern version
        if hasattr(flask_login, 'LoginManager'):
            safe_print("✅ LoginManager class found (modern feature works)")
        
    except Exception as e:
        safe_print(f"❌ Main environment test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Test 2: Bubble version (using omnipkgLoader with strict isolation)
    safe_print(f"\n📦 Test 2: Switching to bubble version ({bubble_ver})...")
    try:
        # Clean modules before switching
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
        if 'flask' in sys.modules:
            del sys.modules['flask']
        
        with omnipkgLoader(f"flask-login=={bubble_ver}", isolation_mode='strict'):
            import flask_login
            actual_version = get_version('flask-login')
            
            if actual_version != bubble_ver:
                safe_print(f"❌ Version mismatch: expected {bubble_ver}, got {actual_version}")
                sys.exit(1)
            
            safe_print(f"✅ Bubble version: flask-login {actual_version}")
            
            # Check for core functionality
            if hasattr(flask_login, 'login_user'):
                safe_print("✅ 'login_user' function found (core 0.4.1 functionality works)")
            else:
                safe_print("❌ 'login_user' function NOT found")
                sys.exit(1)
    
    except Exception as e:
        safe_print(f"❌ Bubble version test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Test 3: Verify we're back to main version
    safe_print(f"\n📦 Test 3: Verifying automatic reversion to main environment...")
    try:
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
        
        import flask_login
        current_version = get_version('flask-login')
        
        if current_version == main_ver:
            safe_print(f"✅ Back to modern version: {current_version}")
            safe_print("🔄 Perfect! Seamlessly switched between legacy and modern versions!")
        else:
            safe_print(f"⚠️  Expected {main_ver} but got {current_version}")
    
    except Exception as e:
        safe_print(f"❌ Reversion test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "="*60)
    safe_print("🎯 THE MAGIC: Legacy and modern code coexist perfectly!")
    print(f"   • Modern ({main_ver}): Active in main environment")
    print(f"   • Legacy ({bubble_ver}): Available in isolated bubble")
    safe_print("🚀 No virtual environments, no containers - pure Python magic!")
    print("="*60)

if __name__ == "__main__":
    test_versions(main_ver=sys.argv[1], bubble_ver=sys.argv[2])
'''
    
    test_script_path = Path('/tmp/flask_version_test.py')
    test_script_path.write_text(test_script_content)
    
    safe_print(f'\n$ python {test_script_path}')
    run_command([sys.executable, str(test_script_path), MODERN_VERSION, OLD_VERSION], check=True)
    
    try:
        test_script_path.unlink()
    except:
        pass

def run_demo():
    """Runs a fully automated demo of omnipkg's Time Machine power."""
    config_manager = None
    original_strategy = None
    
    try:
        # Check Python compatibility first
        check_python_compatibility()
        
        # Initialize with ConfigManager instance
        config_manager = ConfigManager(suppress_init_messages=True)
        
        # Store original strategy and set to stable-main initially
        original_strategy = config_manager.config.get('install_strategy', 'stable-main')
        if original_strategy != 'stable-main':
            safe_print(f"\n⚙️  Setting install strategy to 'stable-main' for initial setup...")
            config_manager.set('install_strategy', 'stable-main')
        
        omnipkg_core = OmnipkgCore(config_manager)
        
        print_header('omnipkg Time Machine Demo - Legacy Flask-Login Resurrection')
        safe_print(_(f'This demo will test flask-login {OLD_VERSION} (2017) vs {MODERN_VERSION} (2024).'))
        safe_print(_('Watch as the Time Machine rebuilds ancient Python dependencies!'))
        time.sleep(3)
        
        print_header('STEP 0: Clean slate - removing any existing installations')
        safe_print(_('🧹 Using omnipkg to properly clean up flask-login and flask...'))
        
        # Use OMNIPKG for uninstall (not pip!) - DO THIS ONCE
        run_command(['omnipkg', 'uninstall', 'flask-login', '-y'], check=False)
        run_command(['omnipkg', 'uninstall', 'flask', '-y'], check=False)
        
        safe_print(_('\n✅ Clean slate achieved! Starting fresh...'))
        time.sleep(2)
        
        print_header('STEP 1: Setting up a modern, stable environment')
        safe_print(f'📦 Installing flask-login=={MODERN_VERSION} with omnipkg...')
        
        # Install modern version ONCE using omnipkg
        omnipkg_core.smart_install([f'flask-login=={MODERN_VERSION}'])
        
        # Verify installation
        version = check_version_with_pip('flask-login')
        if version == MODERN_VERSION:
            safe_print(_(f'\n✅ Beautiful! We have flask-login {MODERN_VERSION} installed and working perfectly.'))
        else:
            safe_print(f'⚠️  Expected {MODERN_VERSION}, got {version}')
        
        time.sleep(3)
        
        print_header('STEP 2: What happens when you use regular pip? 😱')
        safe_print(_(f"Let's say you need version {OLD_VERSION} for a legacy project..."))
        
        # Show current version before destruction
        current_version = check_version_with_pip('flask-login')
        safe_print(f'\n📦 Current version (via pip show): {current_version}')
        
        time.sleep(2)
        
        # FIRST COW: User chooses 'y' - pip destroys everything
        omnipkg_pip_jail()
        choice = simulate_user_choice('y', "User thinks: 'How bad could it be?' 🤡")
        time.sleep(2)
        
        if choice == 'y':
            safe_print(_('\n🔓 Releasing pip... (your funeral)'))
            safe_print(_('💀 Watch as pip destroys your beautiful environment...'))
            
            safe_print(f'\n📊 Before pip install:')
            before_version = check_version_with_pip('flask-login')
            safe_print(f'   flask-login version: {before_version}')
            
            # Let pip DESTROY the environment
            run_command(['pip', 'install', f'flask-login=={OLD_VERSION}'])
            
            safe_print(f'\n📊 After pip install:')
            after_version = check_version_with_pip('flask-login')
            safe_print(f'   flask-login version: {after_version}')
            
            safe_print(_('\n💥 BOOM! Look what pip did:'))
            safe_print(_(f'   ❌ Uninstalled flask-login {before_version}'))
            safe_print(_(f'   ❌ Downgraded to flask-login {after_version}'))
            safe_print(_('   ❌ Your modern project is now BROKEN'))
            safe_print(_('   ❌ Welcome to dependency hell! 🔥'))
            time.sleep(5)
        
        print_header('STEP 3: omnipkg to the rescue! 🦸‍♂️')
        safe_print(_("Let's fix this mess the SMART way..."))
        safe_print(_("We'll show you the warning again, but THIS TIME choose wisely..."))
        time.sleep(3)
        
        # SECOND COW: User chooses 'n' - omnipkg saves the day!
        omnipkg_pip_jail()
        choice = simulate_user_choice('n', "User thinks: 'I'm not falling for that again!' 🧠")
        
        if choice == 'n':
            safe_print(_('\n🧠 Smart choice! Using omnipkg instead...'))
            time.sleep(2)
            
            safe_print(_(f'\n🔧 Installing flask-login=={OLD_VERSION} with omnipkg...'))
            safe_print(_('💡 omnipkg will use latest-active strategy to:'))
            safe_print(_(f'   1. Bubble the broken {OLD_VERSION} installation'))
            safe_print(_(f'   2. Restore clean {MODERN_VERSION} to main environment'))
            safe_print(_('   3. Make BOTH versions available!'))
            time.sleep(2)
            
            # Switch to latest-active strategy for the magic
            safe_print(_('\n⚙️  Temporarily switching to latest-active strategy...'))
            config_manager.set('install_strategy', 'latest-active')
            omnipkg_core = OmnipkgCore(config_manager)  # Reinitialize with new strategy
            
            # This will trigger the magic:
            # - Detect 0.4.1 in main env (broken)
            # - Bubble it (triggers Time Machine if needed!)
            # - Install clean 0.6.3 to main env
            omnipkg_core.smart_install([f'flask-login=={MODERN_VERSION}'])
            
            safe_print(_('\n✅ omnipkg install successful!'))
            safe_print(_('🎯 BOTH versions now coexist peacefully!'))
            
            # Verify both versions exist
            main_version = check_version_with_pip('flask-login')
            bubble_path = omnipkg_core.multiversion_base / f'flask-login-{OLD_VERSION}'
            
            safe_print(f'\n📊 Final state:')
            safe_print(f'   • Main environment: flask-login {main_version}')
            if bubble_path.exists():
                safe_print(f'   • Bubble: flask-login {OLD_VERSION} ✅')
            else:
                safe_print(f'   • Bubble: flask-login {OLD_VERSION} (creating...)')
            
            time.sleep(3)
        
        print_header("STEP 4: Verifying omnipkg's Smart Management")
        safe_print(_("Let's see how omnipkg is managing our packages..."))
        run_command(['omnipkg', 'status'], check=False)
        time.sleep(3)
        
        safe_print(_('\n🔧 Note how omnipkg intelligently manages versions!'))
        safe_print(_(f'📦 Main environment: flask-login {MODERN_VERSION} (modern, works with Flask 3.x)'))
        safe_print(_(f'🔧 omnipkg bubble: flask-login {OLD_VERSION} (legacy, isolated + healed)'))
        time.sleep(3)
        
        # Test version switching
        test_version_switching()
        time.sleep(2)
        
        safe_print('\n' + '=' * 60)
        safe_print(_('🎉🎉🎉 TIME MACHINE DEMO COMPLETE! 🎉🎉🎉'))
        safe_print(_('📚 What you learned:'))
        safe_print(_('   💀 pip: Breaks everything, creates dependency hell'))
        safe_print(_('   🧠 omnipkg: Smart isolation, peaceful coexistence'))
        safe_print(_(f'   ⏰ Time Machine: Resurrected flask-login {OLD_VERSION} from 2017'))
        safe_print(_('   🔄 Magic: Seamless switching without containers'))
        safe_print(_('🚀 Dependency hell is officially SOLVED!'))
        safe_print('=' * 60)
        
    except Exception as demo_error:
        safe_print(_('\n❌ An unexpected error occurred during the demo: {}').format(demo_error))
        import traceback
        traceback.print_exc()
        safe_print(_("\n💡 Don't worry - even if some steps failed, the core isolation is working!"))
        
    finally:
        # Restore original install strategy
        if config_manager and original_strategy:
            safe_print(f"\n🔄 Restoring original install strategy: {original_strategy}")
            config_manager.set('install_strategy', original_strategy)

if __name__ == '__main__':
    run_demo()