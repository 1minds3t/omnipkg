import sys
import importlib
import shutil
import time
from .loader import omnipkgLoader
from .core import omnipkg as OmnipkgCore, ConfigManager

def print_header(title):
    """Prints a consistent, pretty header for the test stages."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

def setup(omnipkg_core):
    """Ensures the environment is clean before the test."""
    print_header("STEP 1: Preparing a Clean Test Environment")
    
    packages_to_test = ["numpy", "scipy"]
    
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f"{pkg}-*"):
            if bubble.is_dir():
                print(f"   - Removing old bubble: {bubble.name}")
                shutil.rmtree(bubble)

    print("   - Setting main environment to a known good state...")
    omnipkg_core.smart_install(["numpy==1.26.4", "scipy==1.16.1"])
    print("✅ Environment is clean and ready for testing.")

def run_test(omnipkg_core):
    """The core of the OMNIPKG Nuclear Stress Test."""
    loader = omnipkgLoader()
    
    # First, create the older version bubbles needed for the test
    print_header("STEP 2: Creating Test Bubbles with `omnipkg`")
    packages_to_bubble = ["numpy==1.24.3", "scipy==1.12.0"]
    for pkg in packages_to_bubble:
        print(f"\n--- Creating bubble for {pkg} ---")
        omnipkg_core.smart_install([pkg])
        time.sleep(1)

    print_header("STEP 3: Executing the Nuclear Test")
    
    # ===== NUMPY SHOWDOWN =====
    print("\n💥 NUMPY VERSION JUGGLING:")
    for numpy_ver in ["1.24.3", "1.26.4"]:
        print(f"\n⚡ Switching to numpy=={numpy_ver}")
        if loader.activate_snapshot(f"numpy=={numpy_ver}"):
            import numpy as np
            importlib.reload(np)
            print(f"   ✅ Version: {np.__version__}")
            assert np.__version__ == numpy_ver, "Version Mismatch!"
        else:
            raise RuntimeError(f"Activation failed for numpy=={numpy_ver}!")
    
    # ===== SCIPY C-EXTENSION CHAOS =====
    print("\n\n🔥 SCIPY C-EXTENSION TEST:")
    for scipy_ver in ["1.12.0", "1.16.1"]:
        print(f"\n🌋 Switching to scipy=={scipy_ver}")
        if loader.activate_snapshot(f"scipy=={scipy_ver}"):
            import scipy.sparse
            importlib.reload(scipy)
            print(f"   ✅ Version: {scipy.__version__}")
            assert scipy.__version__ == scipy_ver, "Version Mismatch!"
        else:
            raise RuntimeError(f"Activation failed for scipy=={scipy_ver}!")

    # ===== THE IMPOSSIBLE TEST =====
    print("\n\n🤯 NUMPY + SCIPY VERSION MIXING:")
    combos = [("1.24.3", "1.12.0"), ("1.26.4", "1.16.1")]
    for np_ver, sp_ver in combos:
        print(f"\n🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}")
        
        # CRITICAL FIX: Import and verify IMMEDIATELY after each activation
        loader.activate_snapshot(f"numpy=={np_ver}")
        import numpy as np
        importlib.reload(np)
        actual_np_ver = np.__version__
        print(f"   🔢 Activated numpy: {actual_np_ver}")
        
        loader.activate_snapshot(f"scipy=={sp_ver}")
        import scipy.sparse
        importlib.reload(scipy)
        actual_sp_ver = scipy.__version__
        print(f"   🔢 Activated scipy: {actual_sp_ver}")
        
        # Re-import numpy to make sure it's still the right version
        # (in case scipy bubble overwrote it)
        importlib.reload(np)
        final_np_ver = np.__version__
        
        print(f"   🧪 Final versions - numpy: {final_np_ver}, scipy: {actual_sp_ver}")
        
        # More informative assertions
        if final_np_ver != np_ver:
            print(f"   ⚠️  Warning: numpy version changed from {actual_np_ver} to {final_np_ver} after scipy activation")
            print("   This indicates the scipy bubble contains a different numpy version")
        
        assert final_np_ver == np_ver, f"Numpy version mismatch! Expected {np_ver}, got {final_np_ver}"
        assert actual_sp_ver == sp_ver, f"Scipy version mismatch! Expected {sp_ver}, got {actual_sp_ver}"
        
        # Test compatibility
        result = np.array([1,2,3]) @ scipy.sparse.eye(3)
        print(f"   🔗 Compatibility check: {result}")
        
    print("\n\n🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇")

def cleanup(omnipkg_core):
    """Cleans up all bubbles created during the test."""
    print_header("STEP 4: Cleaning Up Test Environment")
    packages_to_test = ["numpy", "scipy"]
    
    for pkg in packages_to_test:
        for bubble in omnipkg_core.multiversion_base.glob(f"{pkg}-*"):
            if bubble.is_dir():
                print(f"   - Removing test bubble: {bubble.name}")
                shutil.rmtree(bubble)
    
    print("\n✅ Cleanup complete.")

def run():
    """Main entry point for the stress test, called by the CLI."""
    omnipkg_core = None
    try:
        config_manager = ConfigManager()
        omnipkg_core = OmnipkgCore(config_manager.config)
        
        setup(omnipkg_core)
        run_test(omnipkg_core)

    except Exception as e:
        print(f"\n❌ An error occurred during the stress test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if omnipkg_core:
            cleanup(omnipkg_core)

if __name__ == "__main__":
    run()
