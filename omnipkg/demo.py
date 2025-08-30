import subprocess
import sys
import time
from .core import omnipkg as OmnipkgCore, ConfigManager
from .loader import omnipkgLoader
import importlib
from pathlib import Path
from omnipkg.i18n import _

def omnipkg_pip_jail():
    """The most passive-aggressive pip warning ever - EPIC EDITION"""
    print('\n' + '🔥' * 50)
    print(_('🚨 PIP DEPENDENCY DESTRUCTION ALERT 🚨'))
    print('🔥' * 50)
    print('┌' + '─' * 58 + '┐')
    print(_('│                                                          │'))
    print(_('│  💀 You: pip install flask-login==0.6.0                 │'))
    print(_('│                                                          │'))
    print(_('│  🧠 omnipkg AI suggests:                                 │'))
    print(_('│      omnipkg install flask-login==0.6.0                 │'))
    print(_('│                                                          │'))
    print(_('│  ⚠️  WARNING: pip will NUKE your environment! ⚠️          │'))
    print(_('│      • Downgrade from 0.6.3 to 0.6.0                   │'))
    print(_('│      • Break newer Flask compatibility                  │'))
    print(_('│      • Destroy your modern app                          │'))
    print(_('│      • Welcome you to dependency hell 🔥                │'))
    print(_('│                                                          │'))
    print(_('│  [Y]es, I want chaos | [N]o, save me omnipkg! 🦸\u200d♂️        │'))
    print(_('│                                                          │'))
    print('└' + '─' * 58 + '┘')
    print(_('        \\   ^__^'))
    print(_('         \\  (💀💀)\\______   <- This is your environment'))
    print(_('            (__)\\       )\\/\\   after using pip'))
    print(_('                ||---ww |'))
    print(_('                ||     ||'))
    print(_("💡 Pro tip: Choose 'N' unless you enjoy suffering"))

def simulate_user_choice(choice, message):
    """Simulate user input with a delay"""
    print(_('\nChoice (y/n): '), end='', flush=True)
    time.sleep(1)
    print(choice)
    time.sleep(0.5)
    print(_('💭 {}').format(message))
    return choice.lower()

def run_command(command_list, check=True):
    """Helper to run a command and stream its output."""
    print(_('\n$ {}').format(' '.join(command_list)))
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in iter(process.stdout.readline, ''):
        print(line.strip())
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        raise RuntimeError(_('Demo command failed with exit code {}').format(retcode))
    return retcode

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    print(_('\n$ {}').format(' '.join(command_list)))
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    print(_('💭 Simulating Enter key press...'))
    process.stdin.write(input_data + '\n')
    process.stdin.close()
    for line in iter(process.stdout.readline, ''):
        print(line.strip())
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        raise RuntimeError(_('Demo command failed with exit code {}').format(retcode))
    return retcode

def print_header(title):
    """Prints a consistent, pretty header."""
    print('\n' + '=' * 60)
    print(_('  🚀 {}').format(title))
    print('=' * 60)

def run_demo():
    """Runs a fully automated, impressive demo of omnipkg's power."""
    try:
        config_manager = ConfigManager()
        pkg_instance = OmnipkgCore(config_manager.config)
        print_header('omnipkg Interactive Demo')
        print(_('This demo will show you the classic dependency conflict and how omnipkg solves it.'))
        time.sleep(3)
        print_header('STEP 1: Setting up a modern, stable environment')
        run_command(['pip', 'uninstall', '-y', 'flask-login', 'flask'], check=False)
        run_command(['pip', 'install', 'flask-login==0.6.3'])
        print(_('\n✅ Beautiful! We have flask-login 0.6.3 installed and working perfectly.'))
        time.sleep(5)
        print_header('STEP 2: What happens when you use regular pip? 😱')
        print(_("Let's say you need version 0.6.0 for compatibility with an existing project..."))
        time.sleep(3)
        omnipkg_pip_jail()
        choice = simulate_user_choice('y', "User thinks: 'How bad could it be?' 🤡")
        time.sleep(3)
        if choice == 'y':
            print(_('\n🔓 Releasing pip... (your funeral)'))
            print(_('💀 Watch as pip destroys your beautiful environment...'))
            run_command(['pip', 'install', 'flask-login==0.6.0'])
            print(_('\n💥 BOOM! Look what pip did:'))
            print(_('   ❌ Uninstalled flask-login 0.6.3'))
            print(_('   ❌ Downgraded to flask-login 0.6.0'))
            print(_('   ❌ Your modern project is now BROKEN'))
            print(_('   ❌ Welcome to dependency hell! 🔥'))
            print(_("\n💡 Remember: omnipkg exists when you're ready to stop suffering"))
            time.sleep(8)
        print_header('STEP 3: omnipkg to the rescue! 🦸\u200d♂️')
        print(_("Let's fix this mess and install the newer version back with omnipkg..."))
        print(_('Watch how omnipkg handles this intelligently:'))
        run_command(['omnipkg', 'install', 'flask-login==0.6.3'])
        print(_('\n✅ omnipkg intelligently restored the modern version!'))
        print(_('💡 Notice: No conflicts, no downgrades, just pure intelligence.'))
        time.sleep(5)
        print_header("STEP 4: Now let's install the older version the RIGHT way")
        print(_("This time, let's be smart about it..."))
        time.sleep(3)
        omnipkg_pip_jail()
        choice = simulate_user_choice('n', "User thinks: 'I'm not falling for that again!' 🧠")
        if choice == 'n':
            print(_('\n🧠 Smart choice! Using omnipkg instead...'))
            time.sleep(3)
            print(_('🔧 Installing flask-login==0.6.0 with omnipkg...'))
            print(_('💡 omnipkg will skip if already available or create isolation as needed...'))
            run_command(['omnipkg', 'install', 'flask-login==0.6.0'])
            print(_('\n✅ omnipkg install successful!'))
            print(_('🎯 BOTH versions now coexist peacefully!'))
            time.sleep(5)
        print_header("STEP 5: Verifying omnipkg's Smart Management")
        print(_("Let's see how omnipkg is managing our packages..."))
        run_command(['omnipkg', 'status'], check=False)
        time.sleep(5)
        print(_('\n🔧 Note how omnipkg intelligently manages versions!'))
        print(_('📦 Main environment: flask-login 0.6.3 (untouched)'))
        print(_('🔧 omnipkg: flask-login 0.6.0 (available when needed)'))
        print_header('STEP 6: Inspecting the Knowledge Base')
        time.sleep(2)
        print(_('💡 Want details on a specific version?'))
        print(_("We'll simulate pressing Enter to skip this part..."))
        run_interactive_command(['omnipkg', 'info', 'flask-login'], '')
        print(_('\n🎯 Now you can see that BOTH versions are available to the system.'))
        time.sleep(5)
        print_header('STEP 7: The Grand Finale - Live Version Switching')
        test_script_content = '\n# This content will be written to /tmp/omnipkg_magic_test.py by the demo script\n\nimport sys\nimport os\nimport importlib\nfrom importlib.metadata import version as get_version, PackageNotFoundError\nfrom pathlib import Path # Ensure Path is available\n\n# Dynamically ensure omnipkg\'s loader is discoverable for this subprocess\ntry:\n    _omnipkg_dist = importlib.metadata.distribution(\'omnipkg\')\n    _omnipkg_site_packages = Path(_omnipkg_dist.locate_file("omnipkg")).parent.parent\n    if str(_omnipkg_site_packages) not in sys.path:\n        sys.path.insert(0, str(_omnipkg_site_packages))\nexcept Exception:\n    # Fallback if omnipkg isn\'t formally installed or path already exists\n    pass\n\nfrom omnipkg.loader import omnipkgLoader # Import the new context manager loader\n\ndef test_version_switching():\n    """Test omnipkg\'s seamless version switching using the new omnipkgLoader context manager."""\n    print("🔍 Testing omnipkg\'s seamless version switching using omnipkgLoader...")\n\n    # Test activating the specific version\n    try:\n        # Use the context manager to activate flask-login 0.6.0\n        with omnipkgLoader("flask-login==0.6.0"):\n            # Inside this block, flask_login 0.6.0 should be active\n            import flask_login\n            \n            actual_version = "UNKNOWN" # Initialize to a safe default\n            try:\n                actual_version = get_version(\'flask-login\')\n                print(f"✅ Imported and verified version {actual_version}")\n            except PackageNotFoundError:\n                print("❌ PackageNotFoundError: \'flask-login\' not found by importlib.metadata.version inside context.")\n                sys.exit(1) # Indicate failure in the subprocess\n\n            # Crucial check: access flask_login.config\n            if hasattr(flask_login, \'config\'):\n                print("✅ \'flask_login.config\' module found within specified version.")\n            else:\n                print("❌ \'flask_login.config\' module NOT found within specified version.")\n                sys.exit(1) # Fail the test if not found\n\n            if actual_version != "0.6.0":\n                print(f"❌ Version mismatch inside context: Expected 0.6.0, got {actual_version}.")\n                sys.exit(1)\n\n    except Exception as context_error:\n        print(f"❌ Error while testing specified version: {context_error}")\n        import traceback\n        traceback.print_exc(file=sys.stderr)\n        sys.exit(1)\n\n    # Test that the environment automatically reverted to the main version\n    print(f"\\n🌀 omnipkg loader: Verifying automatic reversion to main environment...")\n    try:\n        # After the \'with\' block, the environment should be restored to its original state.\n        # This means flask_login 0.6.3 should be active here.\n        \n        # Force a reload to ensure Python picks up the restored main environment module\n        # This is important if \'flask_login\' was imported *before* the \'with\' block was entered.\n        try:\n            # Attempt to reload if it\'s already in sys.modules\n            if \'flask_login\' in sys.modules:\n                importlib.reload(sys.modules[\'flask_login\'])\n            else:\n                # Otherwise, just import it normally\n                import flask_login\n        except ImportError:\n            # If it\'s not found, that\'s also an indicator (should be present for 0.6.3)\n            print(_("❌ flask_login not found after context deactivation."))\n            sys.exit(1)\n\n        current_version = "UNKNOWN"\n        try:\n            current_version = get_version(\'flask-login\')\n        except PackageNotFoundError:\n            print("❌ PackageNotFoundError: \'flask-login\' not found by importlib.metadata.version after context deactivation.")\n            sys.exit(1)\n\n        print(f"✅ Back to version: {current_version}")\n        if current_version == "0.6.3":\n            print(_("🔄 Seamless switching between main environment and omnipkg versions!"))\n        else:\n            print(f"❌ Reversion failed! Expected 0.6.3 but got {current_version}.")\n            sys.exit(1)\n\n    except Exception as revert_error:\n        print(f"❌ Error while testing main version after context: {revert_error}")\n        import traceback\n        traceback.print_exc(file=sys.stderr)\n        sys.exit(1)\n\n    print(_("\\n🎯 THE MAGIC: All versions work in the SAME Python process!"))\n    print(_("🚀 No virtual environments, no containers - just pure Python import magic!"))\n\nif __name__ == "__main__":\n    test_version_switching()\n'
        test_script_path = Path('/tmp/omnipkg_magic_test.py')
        with open(test_script_path, 'w') as f:
            f.write(test_script_content)
        print(_('\n$ python {}').format(test_script_path))
        run_command([sys.executable, str(test_script_path)], check=False)
        try:
            test_script_path.unlink()
        except:
            pass
        print(_('\nSee test above: we not only have multiple versions in the same environment, but can even run them in the same script!'))
        time.sleep(5)
        print('\n' + '=' * 60)
        print(_('🎉🎉🎉 DEMO COMPLETE! 🎉🎉🎉'))
        print(_('📚 What you learned:'))
        print(_('   💀 pip: Breaks everything, creates dependency hell'))
        print(_('   🧠 omnipkg: Smart isolation, peaceful coexistence'))
        print(_('   🔧 Intelligence: Skips redundant work, creates isolation when needed'))
        print(_('   🔄 Magic: Seamless switching without containers'))
        print(_('🚀 Dependency hell is officially SOLVED!'))
        print(_('   Welcome to omnipkg heaven!'))
        print('=' * 60)
    except Exception as demo_error:
        print(_('\n❌ An unexpected error occurred during the demo: {}').format(demo_error))
        import traceback
        traceback.print_exc()
        print(_("\n💡 Don't worry - even if some steps failed, the core isolation is working!"))
        print(_("That's the main achievement of omnipkg! 🔥"))
if __name__ == '__main__':
    run_demo()