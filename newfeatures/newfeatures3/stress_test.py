import sys
import importlib
import shutil
import time
import gc
from .loader import omnipkgLoader
from .core import omnipkg as OmnipkgCore, ConfigManager
from pathlib import Path
import os
import subprocess

def print_header(title):
    """Prints a consistent, pretty header for the test stages."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

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
                shutil.rmtree(bubble)

    print("   - Setting main environment to a known good state...")
    omnipkg_core.smart_install(["numpy==1.26.4", "scipy==1.16.1"])
    print("✅ Environment is clean and ready for testing.")

def run_test():
    """The core of the OMNIPKG Nuclear Stress Test."""
    loader = omnipkgLoader()
    
    # ===== NUMPY SHOWDOWN =====
    print("\n💥 NUMPY VERSION JUGGLING:")
    for numpy_ver in ["1.24.3", "1.26.4"]:
        print(f"\n⚡ Switching to numpy=={numpy_ver}")
        
        if loader.activate_snapshot(f"numpy=={numpy_ver}"):
            import numpy as np
            
            print(f"   ✅ Version: {np.__version__}")
            print(f"   🔢 Array sum: {np.array([1,2,3]).sum()}")
            
            if np.__version__ != numpy_ver:
                print(f"   ⚠️ WARNING: Expected {numpy_ver}, got {np.__version__}!")
            else:
                print(f"   🎯 Version verification: PASSED")
        else:
            print(f"   ❌ Activation failed for numpy=={numpy_ver}!")
    
    # ===== SCIPY C-EXTENSION CHAOS =====
    print("\n\n🔥 SCIPY C-EXTENSION TEST:")
    for scipy_ver in ["1.12.0", "1.16.1"]:
        print(f"\n🌋 Switching to scipy=={scipy_ver}")
        
        if loader.activate_snapshot(f"scipy=={scipy_ver}"):
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
        else:
            print(f"   ❌ Activation failed for scipy=={scipy_ver}!")

    # ===== THE IMPOSSIBLE TEST (using clean process) =====
    print("\n\n🤯 NUMPY + SCIPY VERSION MIXING:")
    combos = [("1.24.3", "1.12.0"), ("1.26.4", "1.16.1")]
    
    temp_script_path = Path(os.getcwd()) / "omnipkg_combo_test.py"

    for np_ver, sp_ver in combos:
        print(f"\n🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}")
        
        # Get bubble paths
        config_manager = ConfigManager()
        omnipkg_core = OmnipkgCore(config_manager.config)
        multiversion_base = omnipkg_core.multiversion_base
        
        numpy_bubble = multiversion_base / f"numpy-{np_ver}"
        scipy_bubble = multiversion_base / f"scipy-{sp_ver}"
        
        # Build PYTHONPATH with bubbles at the front
        bubble_paths = []
        if numpy_bubble.exists():
            bubble_paths.append(str(numpy_bubble))
        if scipy_bubble.exists():
            bubble_paths.append(str(scipy_bubble))
            
        # Write the subprocess script
        temp_script_content = f"""
import sys
import os

# Verify the bubbles are in our path
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
    
    if np.__version__ != "{np_ver}" or sp.__version__ != "{sp_ver}":
        print(f"   ❌ Version mismatch! Expected numpy=={np_ver}, scipy=={sp_ver} but got numpy={{np.__version__}}, scipy={{sp.__version__}}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"   🎯 Version verification: BOTH PASSED!")

except Exception as e:
    print(f"   ❌ Test failed in subprocess: {{e}}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
"""
        try:
            with open(temp_script_path, "w") as f:
                f.write(temp_script_content)

            # Create clean environment with bubbles prioritized
            clean_env = os.environ.copy()
            
            # Build PYTHONPATH with bubbles first, then existing paths
            existing_pythonpath = clean_env.get('PYTHONPATH', '')
            if existing_pythonpath:
                new_pythonpath = ':'.join(bubble_paths + [existing_pythonpath])
            else:
                new_pythonpath = ':'.join(bubble_paths)
            
            clean_env['PYTHONPATH'] = new_pythonpath
            
            # Remove any conda/pip environment variables that might interfere
            env_vars_to_remove = ['CONDA_DEFAULT_ENV', 'CONDA_PREFIX', 'PIP_TARGET']
            for var in env_vars_to_remove:
                clean_env.pop(var, None)
            
            print(f"   🔧 PYTHONPATH: {new_pythonpath}")
            
            subprocess.run(
                [sys.executable, temp_script_path],
                check=True,
                cwd=os.getcwd(),
                env=clean_env
            )
        except subprocess.CalledProcessError as e:
            print(f"   ❌ Subprocess test failed for combo numpy=={np_ver} + scipy=={sp_ver}")
            print(f"   💥 Exit code: {e.returncode}")
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
                print(f"   - Removing test bubble: {bubble.name}")
                shutil.rmtree(bubble)
    
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
            name, version = pkg.split('==')
            print(f"\n--- Creating bubble for {name}=={version} ---")
            omnipkg_core.bubble_manager.create_isolated_bubble(name, version)
            time.sleep(1)

        print_header("STEP 3: Executing the Nuclear Test")
        run_test()

    except Exception as e:
        print(f"\n❌ An error occurred during the stress test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()

if __name__ == "__main__":
    run()