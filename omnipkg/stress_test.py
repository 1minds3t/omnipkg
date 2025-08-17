import sys
import importlib
import shutil
import time
import gc
from pathlib import Path
import os
import subprocess
import json # Added for serializing config

# Adjust ROOT_DIR for importing omnipkg modules in subprocesses
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR.parent) not in sys.path: # Add omnipkg's root directory to sys.path
    sys.path.insert(0, str(ROOT_DIR.parent))

try:
    from omnipkg.core import omnipkg as OmnipkgCore, ConfigManager # Corrected import alias
    from omnipkg.loader import omnipkgLoader # Ensure loader is imported
    from omnipkg.common_utils import run_command, print_header # Import common utilities
except ImportError as e:
    print(f"❌ Failed to import omnipkg modules. Is it installed correctly? Error: {e}")
    sys.exit(1)


def setup():
    """Ensures the environment is clean before the test."""
    print_header("STEP 1: Preparing a Clean Test Environment")
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager.config)
    
    packages_to_test = ["numpy", "scipy"]
    
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f"{pkg}-*"):
            if bubble.is_dir():
                print(f"   - Removing old bubble: {bubble.name}")
                shutil.rmtree(bubble, ignore_errors=True)

    # Also clean up any cloaked main packages by the loader in previous runs
    site_packages = Path(omnipkg_core.config["site_packages_path"])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f"{canonical_pkg_name}.*_omnipkg_cloaked*", f"{canonical_pkg_name}-*.dist-info.*_omnipkg_cloaked*"]:
            for cloaked in site_packages.glob(cloaked_pattern):
                print(f"   🧹 Removing residual cloaked: {cloaked.name}")
                shutil.rmtree(cloaked, ignore_errors=True)


    print("   - Setting main environment to a known good state... (This might trigger an omnipkg install)")
    # Explicitly uninstall before installing baseline to ensure a clean slate for the test
    print("   🗑️ Ensuring clean `numpy` and `scipy` installations for baseline test...")
    try:
        run_command([sys.executable, "-m", "pip", "uninstall", "-y", "numpy", "scipy"], check=False)
    except Exception as e:
        print(f"   ⚠️ Warning: Failed to run pip uninstall during setup cleanup: {e}")

    omnipkg_core.smart_install(["numpy==1.26.4", "scipy==1.16.1"])
    print("✅ Environment is clean and ready for testing.")

def run_test():
    """The core of the OMNIPKG Nuclear Stress Test."""
    
    # Get config for passing to omnipkgLoader
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config

    # ===== NUMPY SHOWDOWN =====
    print("\n💥 NUMPY VERSION JUGGLING:")
    for numpy_ver in ["1.24.3", "1.26.4"]:
        print(f"\n⚡ Switching to numpy=={numpy_ver}")
        
        try:
            with omnipkgLoader(f"numpy=={numpy_ver}", config=omnipkg_config):
                import numpy as np
                
                print(f"   ✅ Version: {np.__version__}")
                print(f"   🔢 Array sum: {np.array([1,2,3]).sum()}")
                
                if np.__version__ != numpy_ver:
                    print(f"   ⚠️ WARNING: Expected {numpy_ver}, got {np.__version__}!")
                else:
                    print(f"   🎯 Version verification: PASSED")
        except Exception as e:
            print(f"   ❌ Activation/Test failed for numpy=={numpy_ver}: {e}!")
            import traceback
            traceback.print_exc(file=sys.stderr)

    # ===== SCIPY C-EXTENSION CHAOS =====
    print("\n\n🔥 SCIPY C-EXTENSION TEST:")
    for scipy_ver in ["1.12.0", "1.16.1"]:
        print(f"\n🌋 Switching to scipy=={scipy_ver}")
        
        try:
            with omnipkgLoader(f"scipy=={scipy_ver}", config=omnipkg_config):
                import scipy as sp
                import scipy.sparse
                import scipy.linalg
                
                print(f"   ✅ Version: {sp.__version__}")
                print(f"   ♻️ Sparse matrix: {sp.sparse.eye(3).nnz} non-zeros")
                print(f"   📐 Linalg det: {sp.linalg.det([[0, 2], [1, 1]])}")
                
                if sp.__version__ != scipy_ver:
                    print(f"   ⚠️ WARNING: Expected {scipy_ver}, got {sp.__version__}!")
                else:
                    print(f"   🎯 Version verification: PASSED")
        except Exception as e:
            print(f"   ❌ Activation/Test failed for scipy=={scipy_ver}: {e}!")
            import traceback
            traceback.print_exc(file=sys.stderr)

    # ===== THE IMPOSSIBLE TEST (using clean process) =====
    print("\n\n🤯 NUMPY + SCIPY VERSION MIXING:")
    combos = [("1.24.3", "1.12.0"), ("1.26.4", "1.16.1")]
    
    temp_script_path = Path(os.getcwd()) / "omnipkg_combo_test.py"

    for np_ver, sp_ver in combos:
        print(f"\n🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}")
        
        # Serialize config to pass to the subprocess
        config_json_str = json.dumps(omnipkg_config)

        # Write the subprocess script
        temp_script_content = f"""
import sys
import os
import json # To load config
import importlib
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path

# Ensure omnipkg's root is in sys.path for importing its modules
sys.path.insert(0, r"{ROOT_DIR.parent}")

# Load config in the subprocess
subprocess_config = json.loads('{config_json_str}')

def run_combo_test():
    # Retrieve bubble paths from the loaded config in the subprocess
    numpy_bubble_path = Path(subprocess_config['multiversion_base']) / f"numpy-{{'{np_ver}'}}"
    scipy_bubble_path = Path(subprocess_config['multiversion_base']) / f"scipy-{{'{sp_ver}'}}"

    # Manually construct PYTHONPATH for this specific test as it was originally designed
    # by prepending bubble paths to sys.path in this subprocess.
    bubble_paths_to_add = []
    if numpy_bubble_path.is_dir():
        bubble_paths_to_add.append(str(numpy_bubble_path))
    if scipy_bubble_path.is_dir():
        bubble_paths_to_add.append(str(scipy_bubble_path))
        
    # Prepend bubble paths to sys.path for this subprocess
    sys.path = bubble_paths_to_add + sys.path 
    
    print("🔍 Python path (first 5 entries):")
    for idx, path in enumerate(sys.path[:5]):
        print(f"   {{idx}}: {{path}}")

    try:
        import numpy as np
        import scipy as sp
        import scipy.sparse
        
        print(f"   🧪 numpy: {{np.__version__}}, scipy: {{sp.__version__}}")
        print(f"   📍 numpy location: {{np.__file__}}")
        print(f"   📍 scipy location: {{sp.__file__}}")
        
        result = np.array([1,2,3]) @ sp.sparse.eye(3).toarray()
        print(f"   🔗 Compatibility check: {{result}}")
        
        # Version validation
        np_ok = False
        sp_ok = False
        try:
            if get_version('numpy') == "{np_ver}":
                np_ok = True
            else:
                print(f"   ❌ Numpy version mismatch! Expected {np_ver}, got {{get_version('numpy')}}", file=sys.stderr)
        except PackageNotFoundError:
            print(f"   ❌ Numpy not found in subprocess!", file=sys.stderr)

        try:
            if get_version('scipy') == "{sp_ver}":
                sp_ok = True
            else:
                print(f"   ❌ Scipy version mismatch! Expected {sp_ver}, got {{get_version('scipy')}}", file=sys.stderr)
        except PackageNotFoundError:
            print(f"   ❌ Scipy not found in subprocess!", file=sys.stderr)

        if np_ok and sp_ok:
            print(f"   🎯 Version verification: BOTH PASSED!")
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"   ❌ Test failed in subprocess: {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    run_combo_test()
"""
        try:
            with open(temp_script_path, "w") as f:
                f.write(temp_script_content)

            run_command(
                [sys.executable, str(temp_script_path)],
                check=True,
            )
        except RuntimeError as e: # Catch RuntimeError raised by run_command
            print(f"   ❌ Subprocess test failed for combo numpy=={np_ver} + scipy=={sp_ver}")
            print(f"   💥 Error: {e}")
            sys.exit(1) # Ensure the main stress test also fails
        except Exception as e:
            print(f"   ❌ An unexpected error occurred during combo test subprocess setup: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.exit(1)
        finally:
            if temp_script_path.exists():
                os.remove(temp_script_path)
            
    print("\n\n 🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇")

def cleanup():
    """Cleans up all bubbles created during the test."""
    print_header("STEP 3: Cleaning Up Test Environment")
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager.config)
    
    packages_to_test = ["numpy", "scipy"]
    
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f"{pkg}-*"):
            if bubble.is_dir():
                print(f"   🧹 Removing test bubble: {bubble.name}")
                shutil.rmtree(bubble, ignore_errors=True)
    
    site_packages = Path(omnipkg_core.config["site_packages_path"])
    for pkg_name in packages_to_test:
        canonical_pkg_name = pkg_name.lower().replace('_', '-')
        for cloaked_pattern in [f"{canonical_pkg_name}.*_omnipkg_cloaked*", f"{canonical_pkg_name}-*.dist-info.*_omnipkg_cloaked*"]:
            for cloaked in site_packages.glob(cloaked_pattern):
                print(f"   🧹 Removing residual cloaked: {cloaked.name}")
                shutil.rmtree(cloaked, ignore_errors=True)

    print("\n✅ Cleanup complete. Your environment is back to normal.")

def run():
    """Main entry point for the stress test, called by the CLI."""
    try:
        setup()
        
        print_header("STEP 2: Creating Test Bubbles with `omnipkg`")
        config_manager = ConfigManager()
        omnipkg_core = OmnipkgCore(config_manager.config)
        packages_to_bubble = [
            "numpy==1.24.3",
            "scipy==1.12.0"
        ]
        for pkg in packages_to_bubble:
            print(f"\n--- Creating bubble for {pkg} ---")
            omnipkg_core.smart_install([pkg])
            time.sleep(1)

        print_header("STEP 3: Executing the Nuclear Test")
        run_test()

    except Exception as e:
        print(f"\n❌ An error occurred during the stress test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1) # Ensure the script exits with a non-zero code on failure
    finally:
        cleanup()

if __name__ == "__main__":
    run()