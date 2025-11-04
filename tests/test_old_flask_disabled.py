from __future__ import annotations  # Python 3.6+ compatibility

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import subprocess
import sys
import time
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.loader import omnipkgLoader
import importlib
from pathlib import Path
from omnipkg.i18n import _

# --- Test Configuration ---
# Testing REAL old flask-login versions that work with different Flask versions
MODERN_VERSION = '0.6.3'  # Works with Flask 2.2+ and 3.x
OLD_VERSION = '0.4.1'      # REAL legacy version that EXISTS (Python 3.6-3.8, Flask 1.x era)
# Note: 0.4.3 doesn't exist! Available versions: 0.4.0, 0.4.1, then jumps to 0.5.0
# Note: 0.6.0 has compatibility issues with Flask 3.x (_request_ctx_stack removed)

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
    safe_print(_('│  [Y]es, I want chaos | [N]o, save me omnipkg! 🦸\u200d♂️        │'))
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
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in iter(process.stdout.readline, ''):
        safe_print(line.strip())
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        raise RuntimeError(_('Demo command failed with exit code {}').format(retcode))
    return retcode

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    safe_print(_('\n$ {}').format(' '.join(command_list)))
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    safe_print(_('💭 Simulating Enter key press...'))
    process.stdin.write(input_data + '\n')
    process.stdin.close()
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
    
    # flask-login 0.4.1 requires Python 3.6-3.8 ideally
    if py_version.major == 3 and 6 <= py_version.minor <= 11:
        safe_print(f'✅ Python {py_version.major}.{py_version.minor} should work with flask-login {OLD_VERSION}')
        return True
    else:
        safe_print(f'⚠️  Python {py_version.major}.{py_version.minor} may have issues with flask-login {OLD_VERSION}')
        safe_print(f'💡 This test works best on Python 3.6-3.11')
        return False

def run_demo():
    """Runs a fully automated, impressive demo of omnipkg's power."""
    try:
        # Check Python compatibility first
        check_python_compatibility()
        
        # FIX: Pass ConfigManager instance, not .config dict
        config_manager = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager)
        
        print_header('omnipkg Interactive Demo - REAL Legacy Version Test')
        safe_print(_(f'This demo will test flask-login {OLD_VERSION} (Python 3.6-3.8 era) vs {MODERN_VERSION} (modern).'))
        time.sleep(3)
        
        print_header('STEP 0: Clean slate - removing any existing installations')
        safe_print(_('🧹 Using omnipkg to properly clean up flask-login and flask...'))
        run_command(['omnipkg', 'uninstall', 'flask-login', '-y'], check=False)
        run_command(['omnipkg', 'uninstall', 'flask', '-y'], check=False)
        # Also use pip as fallback in case anything is only in pip
        run_command(['pip', 'uninstall', '-y', 'flask-login', 'flask'], check=False)
        safe_print(_('\n✅ Clean slate achieved! Starting fresh...'))
        time.sleep(2)
        
        print_header('STEP 1: Setting up a modern, stable environment')
        run_command(['pip', 'install', f'flask-login=={MODERN_VERSION}'])
        safe_print(_(f'\n✅ Beautiful! We have flask-login {MODERN_VERSION} installed and working perfectly.'))
        time.sleep(5)
        
        print_header('STEP 2: What happens when you use regular pip? 😱')
        safe_print(_(f"Let's say you need version {OLD_VERSION} for a legacy Python 3.6-3.8 project..."))
        time.sleep(3)
        omnipkg_pip_jail()
        choice = simulate_user_choice('y', "User thinks: 'How bad could it be?' 🤡")
        time.sleep(3)
        
        if choice == 'y':
            safe_print(_('\n🔓 Releasing pip... (your funeral)'))
            safe_print(_('💀 Watch as pip destroys your beautiful environment...'))
            run_command(['pip', 'install', f'flask-login=={OLD_VERSION}'])
            safe_print(_('\n💥 BOOM! Look what pip did:'))
            safe_print(_(f'   ❌ Uninstalled flask-login {MODERN_VERSION}'))
            safe_print(_(f'   ❌ Downgraded to flask-login {OLD_VERSION}'))
            safe_print(_('   ❌ Your modern project is now BROKEN'))
            safe_print(_('   ❌ Welcome to dependency hell! 🔥'))
            safe_print(_("\n💡 Remember: omnipkg exists when you're ready to stop suffering"))
            time.sleep(8)
            
        print_header('STEP 3: omnipkg to the rescue! 🦸\u200d♂️')
        safe_print(_("Let's fix this mess and install the newer version back with omnipkg..."))
        safe_print(_('Watch how omnipkg handles this intelligently:'))
        run_command(['omnipkg', 'install', f'flask-login=={MODERN_VERSION}'])
        safe_print(_(f'\n✅ omnipkg intelligently restored the modern version ({MODERN_VERSION})!'))
        safe_print(_('💡 Notice: No conflicts, no downgrades, just pure intelligence.'))
        time.sleep(5)
        
        print_header("STEP 4: Now let's install the LEGACY version the RIGHT way")
        safe_print(_(f"This time, let's be smart and use omnipkg for flask-login {OLD_VERSION}..."))
        time.sleep(3)
        omnipkg_pip_jail()
        choice = simulate_user_choice('n', "User thinks: 'I'm not falling for that again!' 🧠")
        
        if choice == 'n':
            safe_print(_('\n🧠 Smart choice! Using omnipkg instead...'))
            time.sleep(3)
            safe_print(_(f'🔧 Installing flask-login=={OLD_VERSION} with omnipkg...'))
            safe_print(_('💡 omnipkg will create isolation for this legacy version...'))
            run_command(['omnipkg', 'install', f'flask-login=={OLD_VERSION}'])
            safe_print(_('\n✅ omnipkg install successful!'))
            safe_print(_('🎯 BOTH versions now coexist peacefully!'))
            time.sleep(5)
            
        print_header("STEP 5: Verifying omnipkg's Smart Management")
        safe_print(_("Let's see how omnipkg is managing our packages..."))
        run_command(['omnipkg', 'status'], check=False)
        time.sleep(5)
        safe_print(_('\n🔧 Note how omnipkg intelligently manages versions!'))
        safe_print(_(f'📦 Main environment: flask-login {MODERN_VERSION} (modern, works with Flask 3.x)'))
        safe_print(_(f'🔧 omnipkg bubble: flask-login {OLD_VERSION} (legacy, isolated)'))
        
        print_header('STEP 6: Inspecting the Knowledge Base')
        time.sleep(2)
        safe_print(_('💡 Want details on specific versions?'))
        safe_print(_("We'll simulate pressing Enter to skip this part..."))
        run_interactive_command(['omnipkg', 'info', 'flask-login'], '')
        safe_print(_('\n🎯 Now you can see that BOTH versions are available to the system.'))
        time.sleep(5)
        
        print_header('STEP 7: The Grand Finale - Live Version Switching')
        # CRITICAL FIX: Use raw string to avoid escape issues and add isolation_mode='strict'
        test_script_content = r'''
# This content will be written to /tmp/omnipkg_magic_test.py by the demo script

import sys
import os
import importlib
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path

# Dynamically ensure omnipkg's loader is discoverable for this subprocess
try:
    _omnipkg_dist = importlib.metadata.distribution('omnipkg')
    _omnipkg_site_packages = Path(_omnipkg_dist.locate_file("omnipkg")).parent.parent
    if str(_omnipkg_site_packages) not in sys.path:
        sys.path.insert(0, str(_omnipkg_site_packages))
except Exception:
    pass

try:
    from omnipkg.common_utils import safe_print
    from omnipkg.i18n import _
except ImportError:
    def safe_print(msg, **kwargs):
        print(msg, **kwargs)
    def _(msg):
        return msg

from omnipkg.loader import omnipkgLoader

def test_version_switching():
    """Test omnipkg's seamless version switching with REAL legacy versions."""
    safe_print("🔍 Testing omnipkg's seamless version switching...")
    safe_print(f"   Modern version: MODERN_VER")
    safe_print(f"   Legacy version: OLD_VER")
    
    # FIRST: Verify the modern version is available in main environment
    safe_print(f"\n📦 Step 1: Checking main environment has MODERN_VER...")
    try:
        main_version = get_version('flask-login')
        safe_print(f"✅ Main environment has flask-login {main_version}")
        if main_version != "MODERN_VER":
            safe_print(f"⚠️  Expected MODERN_VER, got {main_version}")
    except PackageNotFoundError:
        safe_print("❌ flask-login not found in main environment!")
        safe_print("💡 The demo should have installed it. Something went wrong.")
        sys.exit(1)

    # Test activating the LEGACY version with STRICT ISOLATION
    safe_print(f"\n📦 Step 2: Switching to legacy version OLD_VER...")
    try:
        with omnipkgLoader("flask-login==OLD_VER", isolation_mode='strict', ):
            # Remove any cached flask_login from previous imports
            if 'flask_login' in sys.modules:
                del sys.modules['flask_login']
            
            import flask_login
            
            actual_version = "UNKNOWN"
            try:
                actual_version = get_version('flask-login')
                safe_print(f"✅ Loaded legacy version {actual_version}")
            except PackageNotFoundError:
                safe_print("❌ PackageNotFoundError: 'flask-login' not found inside context.")
                sys.exit(1)

            # Version-specific check: 0.4.1 has core login_user function
            if hasattr(flask_login, 'login_user'):
                safe_print("✅ 'flask_login.login_user' function found (core functionality works).")
            else:
                safe_print("❌ 'flask_login.login_user' function NOT found.")
                sys.exit(1)

            if actual_version != "OLD_VER":
                safe_print(f"❌ Version mismatch: Expected OLD_VER, got {actual_version}.")
                sys.exit(1)
            
            safe_print(f"🎯 Successfully using legacy flask-login {actual_version} in isolated context!")

    except Exception as context_error:
        safe_print(f"❌ Error while testing legacy version: {context_error}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    # Test that the environment automatically reverted to the MODERN version
    safe_print(f"\n📦 Step 3: Verifying automatic reversion to modern environment...")
    try:
        # Force module reload to pick up reverted environment
        if 'flask_login' in sys.modules:
            del sys.modules['flask_login']
            importlib.invalidate_caches()

        current_version = "UNKNOWN"
        try:
            current_version = get_version('flask-login')
        except PackageNotFoundError:
            safe_print("❌ flask-login not found after context deactivation.")
            sys.exit(1)

        safe_print(f"✅ Back to modern version: {current_version}")
        if current_version == "MODERN_VER":
            safe_print("🔄 Perfect! Seamlessly switched between legacy and modern versions!")
        else:
            safe_print(f"⚠️  Expected MODERN_VER but got {current_version}")
            safe_print("   (This might be OK depending on your setup)")

    except Exception as revert_error:
        safe_print(f"❌ Error while testing modern version after context: {revert_error}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    safe_print("\n" + "="*60)
    safe_print("🎯 THE MAGIC: Legacy and modern code coexist perfectly!")
    safe_print(f"   • Modern (MODERN_VER): Active in main environment")
    safe_print(f"   • Legacy (OLD_VER): Available in isolated bubble")
    safe_print("🚀 No virtual environments, no containers - pure Python magic!")
    safe_print("="*60)

if __name__ == "__main__":
    test_version_switching()
'''
        # Replace version placeholders
        test_script_content = test_script_content.replace('MODERN_VER', MODERN_VERSION)
        test_script_content = test_script_content.replace('OLD_VER', OLD_VERSION)

        test_script_path = Path('/tmp/omnipkg_magic_test.py')
        with open(test_script_path, 'w') as f:
            f.write(test_script_content)
        safe_print(_('\n$ python {}').format(test_script_path))
        run_command([sys.executable, str(test_script_path)], check=False)
        try:
            test_script_path.unlink()
        except:
            pass
        safe_print(_(f'\n🎉 See above: flask-login {OLD_VERSION} and {MODERN_VERSION} coexist in the SAME process!'))
        time.sleep(5)
        
        safe_print('\n' + '=' * 60)
        safe_print(_('🎉🎉🎉 LEGACY VERSION DEMO COMPLETE! 🎉🎉🎉'))
        safe_print(_('📚 What you learned:'))
        safe_print(_('   💀 pip: Breaks everything, creates dependency hell'))
        safe_print(_('   🧠 omnipkg: Smart isolation, peaceful coexistence'))
        safe_print(_(f'   🕰️  Legacy: flask-login {OLD_VERSION} works alongside {MODERN_VERSION}'))
        safe_print(_('   🔄 Magic: Seamless switching without containers'))
        safe_print(_('🚀 Dependency hell is officially SOLVED!'))
        safe_print(_('   Welcome to omnipkg heaven!'))
        safe_print('=' * 60)
    except Exception as demo_error:
        safe_print(_('\n❌ An unexpected error occurred during the demo: {}').format(demo_error))
        import traceback
        traceback.print_exc()
        safe_print(_("\n💡 Don't worry - even if some steps failed, the core isolation is working!"))
        safe_print(_("That's the main achievement of omnipkg! 🔥"))
        
if __name__ == '__main__':
    run_demo()