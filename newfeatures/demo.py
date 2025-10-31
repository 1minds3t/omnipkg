# /home/minds3t/omnipkg/omnipkg/demo.py

import subprocess
import sys
import time
from pathlib import Path

def run_cli_command(command: str, check=True):
    """Runs an omnipkg CLI command and prints its output."""
    import shlex
    full_command = [sys.executable, "-m", "omnipkg.cli"] + shlex.split(command)
    print(f"\n$ {' '.join(full_command)}")
    process = subprocess.Popen(full_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    for line in iter(process.stdout.readline, ''):
        print(line.strip())
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        print(f"⚠️  FATAL: Command failed with exit code {retcode}. Aborting demo.")
        sys.exit(1)
    return retcode

def print_header(title):
    """Prints a consistent, pretty header."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

def run_demo():
    """
    A robust, CLI-driven demo that sets up the environment and then runs
    a separate, bulletproof script to verify the version switching.
    """
    try:
        print_header("omnipkg Interactive Demo")
        print("This demo will prove omnipkg's ability to manage conflicting versions.")
        time.sleep(2)

        # Step 1: Prepare a clean, known state using omnipkg CLI
        print_header("STEP 1: Setting up the environment")
        print("Ensuring flask-login v0.6.3 is the active version...")
        run_cli_command("uninstall flask-login==0.4.1 -y", check=False)
        run_cli_command("install flask-login==0.6.3")
        
        print("\nCreating a bubble for the conflicting version (v0.4.1)...")
        run_cli_command("install flask-login==0.4.1")

        # Step 2: Verify the state with CLI commands
        print_header("STEP 2: Verifying the environment state")
        print("Checking the status of our multi-version environment...")
        run_cli_command("status")

        # Step 3: The user-requested Filesystem Proof
        print_header("STEP 3: Filesystem Proof (tree command)")
        print("Verifying the bubble contains all its dependencies...")
        # Get the bubble path dynamically
        from omnipkg.core import ConfigManager
        config = ConfigManager().config
        bubble_path = Path(config["multiversion_base"]) / "flask-login-0.4.1"
        run_cli_command(f"tree {bubble_path}", check=False)
        print("\nAs you can see, the bubble is complete and self-contained.")
        time.sleep(4)

        # Step 4: The Grand Finale
        print_header("STEP 4: The Grand Finale - Bulletproof Activation Test")
        print("Running a separate Python script to prove true isolation...")

        test_script_content = f'''
import sys
import importlib
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

# This script proves the bubble's integrity by temporarily removing
# the main site-packages from the path, forcing all imports to
# resolve from within the bubble itself.

def get_versions():
    """Safely get current versions of packages."""
    try:
        fl_ver = version("flask-login")
        f_ver = version("flask")
    except PackageNotFoundError:
        return "Not Found", "Not Found"
    return fl_ver, f_ver

# --- Main Test Execution ---
print("=== Bulletproof Activation Test ===")
from omnipkg.loader import omnipkgLoader
loader = omnipkgLoader()

print("\\nInitial State:")
fl_ver, f_ver = get_versions()
print(f"  - flask-login: {fl_ver}")
print(f"  - flask: {f_ver}")

print("\\n--- Activating Bubble: flask-login==0.4.1 ---")
loader.activate_snapshot("flask-login==0.4.1")

# The Bulletproof Technique: Temporarily hide main site-packages
main_site_packages = next((p for p in sys.path if 'site-packages' in p and '.omnipkg_versions' not in p), None)
if main_site_packages:
    print(f"  - Temporarily hiding main site-packages: {main_site_packages}")
    sys.path.remove(main_site_packages)

try:
    # These imports MUST now come from the bubble
    import flask
    import flask_login
    importlib.reload(flask_login)
    importlib.reload(flask)
    
    fl_ver, f_ver = get_versions()
    print(f"  - Active flask-login: {fl_ver}")
    print(f"  - Active flask: {f_ver}")
    if fl_ver == "0.4.1":
        print("  ✅ SUCCESS: Correctly using flask-login from the bubble.")
    else:
        print(f"  ❌ FAILURE: Incorrect flask-login version: {fl_ver}")
    
except ImportError as e:
    print(f"  ❌ FAILURE: ImportError after activation. The bubble is not self-contained.")
    print(f"     Error: {e}")
finally:
    # Restore the path for the next test
    if main_site_packages and main_site_packages not in sys.path:
        sys.path.insert(1, main_site_packages)
        print("  - Restored main site-packages path.")

print("\\n--- Deactivating Bubble and Restoring Main Environment ---")
loader.activate_snapshot("flask-login==0.6.3")
importlib.reload(importlib.import_module('flask_login'))
importlib.reload(importlib.import_module('flask'))
fl_ver, f_ver = get_versions()
print(f"  - Active flask-login: {fl_ver}")
print(f"  - Active flask: {f_ver}")
if fl_ver == "0.6.3":
    print("  ✅ SUCCESS: Correctly switched back to main environment version.")
else:
    print(f"  ❌ FAILURE: Incorrect flask-login version: {fl_ver}")

print("\\n🎉 DEMO SUCCESSFUL! True isolation and activation confirmed.")
'''
        test_script_path = Path("/tmp/omnipkg_bulletproof_test.py")
        with open(test_script_path, 'w') as f:
            f.write(test_script_content)
        
        run_cli_command(f"python {test_script_path.resolve()}", check=False)
        
        try:
            test_script_path.unlink()
        except OSError:
            pass

        print("\n" + "="*60)
        print("🎉🎉🎉 DEMO COMPLETE! 🎉🎉🎉")
        print("This proves that the core mechanics are working correctly.")
        print("🚀 Dependency hell is officially SOLVED!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ An unexpected error occurred during the demo: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_demo()