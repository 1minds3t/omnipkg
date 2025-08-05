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

def setup():
    """Ensures the environment is clean before the test."""
    print_header("STEP 1: Preparing a Clean Test Environment")
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager.config)
    
    packages_to_test = ["numpy", "scipy"]
    
    for pkg in packages_to_test:
        # Find all bubble directories for this package
        for bubble in omnipkg_core.multiversion_base.glob(f"{pkg}-*"):
            if bubble.is_dir():
                print(f"   - Removing old bubble: {bubble.name}")
                shutil.rmtree(bubble)

    # Use omnipkg to ensure the 'good' versions are installed
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
            importlib.reload(np)
            print(f"   ✅ Version: {np.__version__}")
            print(f"   🔢 Array sum: {np.array([1,2,3]).sum()}")
        else:
            print(f"   ❌ Activation failed for numpy=={numpy_ver}!")
    
    # ===== SCIPY C-EXTENSION CHAOS =====
    print("\n\n🔥 SCIPY C-EXTENSION TEST:")
    for scipy_ver in ["1.12.0", "1.16.1"]:
        print(f"\n🌋 Switching to scipy=={scipy_ver}")
        if loader.activate_snapshot(f"scipy=={scipy_ver}"):
            import scipy.sparse
            import scipy.linalg
            importlib.reload(scipy)
            print(f"   ✅ Version: {scipy.__version__}")
            eye = scipy.sparse.eye(3)
            print(f"   ♻️ Sparse matrix: {eye.nnz} non-zeros")
            det = scipy.linalg.det([[0, 2], [1, 1]])
            print(f"   📐 Linalg det: {det}")
        else:
            print(f"   ❌ Activation failed for scipy=={scipy_ver}!")

    # ===== THE IMPOSSIBLE TEST =====
    print("\n\n🤯 NUMPY + SCIPY VERSION MIXING:")
    combos = [("1.24.3", "1.12.0"), ("1.26.4", "1.16.1")]
    for np_ver, sp_ver in combos:
        print(f"\n🌀 COMBO: numpy=={np_ver} + scipy=={sp_ver}")
        loader.activate_snapshot(f"numpy=={np_ver}")
        loader.activate_snapshot(f"scipy=={sp_ver}")
        
        import numpy as np
        import scipy.sparse
        importlib.reload(np)
        importlib.reload(scipy)
        
        print(f"   🧪 numpy: {np.__version__}, scipy: {scipy.__version__}")
        result = np.array([1,2,3]) @ scipy.sparse.eye(3)
        print(f"   🔗 Compatibility check: {result}")
        
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
        
        # --- Create the bubbles for the test ---
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
            time.sleep(1) # Give filesystem a moment to settle

        # --- Run the actual test ---
        print_header("STEP 3: Executing the Nuclear Test")
        run_test()

    except Exception as e:
        print(f"\n❌ An error occurred during the stress test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # --- ALWAYS run the cleanup ---
        cleanup()

if __name__ == "__main__":
    run()
