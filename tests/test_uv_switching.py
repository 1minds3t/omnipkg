import sys
import subprocess
import os
import shutil
import time
from pathlib import Path

# Adjust ROOT_DIR for importing omnipkg modules
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR.parent))

try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    print(f"❌ Failed to import omnipkg modules. Is it installed correctly? Error: {e}")
    sys.exit(1)

def print_header(title):
    print("\n" + "="*80)
    print(f"  🚀 {title}")
    print("="*80)

def print_subheader(title):
    print(f"\n--- {title} ---")

def setup_environment():
    print_header("STEP 1: Environment Setup & Cleanup")
    config_manager = ConfigManager()
    omnipkg_core = OmnipkgCore(config_manager.config)
    
    # Uninstall all UV versions first
    print("   🧹 Uninstalling all UV versions from main environment and bubbles...")
    omnipkg_core.smart_uninstall(["uv"], force=True)
    
    # Clean up any residual UV bubbles and cloaked files
    for bubble in omnipkg_core.multiversion_base.glob("uv-*"):
        if bubble.is_dir():
            print(f"   🧹 Removing old bubble: {bubble.name}")
            shutil.rmtree(bubble, ignore_errors=True)
    for cloaked in Path(config_manager.config["site_packages_path"]).glob("uv.*_omnipkg_cloaked*"):
        print(f"   🧹 Removing residual cloaked: {cloaked.name}")
        shutil.rmtree(cloaked, ignore_errors=True)
    for cloaked in Path(config_manager.config["site_packages_path"]).glob("uv.*_test_harness_cloaked*"):
        print(f"   🧹 Removing test harness residual cloaked: {cloaked.name}")
        shutil.rmtree(cloaked, ignore_errors=True)
    
    print("   📦 Installing main environment: uv==0.6.13")
    omnipkg_core.smart_install(["uv==0.6.13"])
    print("✅ Environment prepared")
    return config_manager.config

def create_test_bubbles(config):
    print_header("STEP 2: Creating Test Bubbles")
    omnipkg_core = OmnipkgCore(config)
    
    test_versions = ["0.4.30", "0.5.11"]
    
    for version in test_versions:
        print(f"   🫧 Creating bubble for uv=={version}")
        try:
            omnipkg_core.smart_install([f"uv=={version}"])
            print(f"   ✅ Bubble created: uv-{version}")
        except Exception as e:
            print(f"   ❌ Failed to create bubble for uv=={version}: {e}")
    
    return test_versions

def validate_bubble(bubble_path, expected_version):
    print(f"   🔎 Validating bubble: {bubble_path.name}")
    uv_bin = bubble_path / "bin" / "uv"
    if not uv_bin.exists():
        print(f"   ❌ No uv binary found in {bubble_path}/bin")
        return False
    if not os.access(uv_bin, os.X_OK):
        print(f"   ❌ Binary {uv_bin} is not executable")
        return False
    dist_info = list(bubble_path.glob("uv-*.dist-info"))
    if not dist_info:
        print(f"   ❌ No dist-info found in {bubble_path}")
        return False
    print(f"   ✅ Bubble validation passed")
    return True

def inspect_bubble_structure(bubble_path):
    print(f"   🔍 Inspecting bubble structure: {bubble_path.name}")
    
    if not bubble_path.exists():
        print(f"   ❌ Bubble doesn't exist: {bubble_path}")
        return False
    
    uv_module = bubble_path / "uv"
    if uv_module.exists():
        print(f"   ✅ Found uv module directory")
    else:
        print(f"   ℹ️  No uv module directory (likely binary-only package)")
    
    dist_info = list(bubble_path.glob("uv-*.dist-info"))
    if dist_info:
        print(f"   ✅ Found dist-info: {dist_info[0].name}")
    else:
        print(f"   ⚠️  No dist-info found")
    
    scripts_dir = bubble_path / "bin"
    if scripts_dir.exists():
        items = list(scripts_dir.iterdir())
        print(f"   ✅ Found bin directory with {len(items)} items")
        uv_bin = scripts_dir / "uv"
        if uv_bin.exists():
            print(f"   ✅ Found uv binary: {uv_bin}")
            if os.access(uv_bin, os.X_OK):
                print(f"   ✅ Binary is executable")
            else:
                print(f"   ⚠️  Binary is not executable")
        else:
            print(f"   ⚠️  No uv binary in bin/")
    else:
        print(f"   ⚠️  No bin directory found")
    
    contents = list(bubble_path.iterdir())
    print(f"   📁 Bubble contents ({len(contents)} items):")
    for item in sorted(contents)[:10]:
        print(f"      - {item.name}{'/' if item.is_dir() else ''}")
    
    return True

def test_direct_binary_execution(bubble_path, expected_version):
    print(f"   🔧 Testing direct binary execution...")
    
    uv_binary = bubble_path / "bin" / "uv"
    if not uv_binary.exists():
        print(f"   ❌ No UV binary found in bubble")
        return False
    
    try:
        print(f"   🎯 Executing: {uv_binary} --version")
        result = subprocess.run(
            [str(uv_binary), "--version"],
            capture_output=True, text=True, timeout=10, check=True
        )
        
        actual_version = result.stdout.strip().split()[-1]
        print(f"   ✅ Direct binary reported: {actual_version}")
        
        if actual_version == expected_version:
            print(f"   🎯 Direct binary test: PASSED")
            return True
        else:
            print(f"   ❌ Version mismatch: expected {expected_version}, got {actual_version}")
            return False
    except Exception as e:
        print(f"   ❌ Direct binary execution failed: {e}")
        return False

def run_comprehensive_test():
    print_header("🚨 OMNIPKG UV BINARY STRESS TEST 🚨")
    
    try:
        config = setup_environment()
        test_versions = create_test_bubbles(config)
        multiversion_base = Path(config["multiversion_base"])

        print_header("STEP 3: Comprehensive UV Version Testing")
        
        all_tests_passed = True
        test_results = {}
        
        print_subheader("Testing Main Environment (uv==0.6.13)")
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True, text=True, timeout=10, check=True
            )
            actual_version = result.stdout.strip().split()[-1]
            main_passed = actual_version == "0.6.13"
            print(f"   ✅ Main environment version: {actual_version}")
            if main_passed:
                print(f"   🎯 Main environment test: PASSED")
            else:
                print(f"   ❌ Main environment test: FAILED")
            test_results["main"] = main_passed
            all_tests_passed &= main_passed
        except Exception as e:
            print(f"   ❌ Main environment test failed: {e}")
            test_results["main"] = False
            all_tests_passed = False
        
        for version in test_versions:
            print_subheader(f"Testing Bubble (uv=={version})")
            bubble_path = multiversion_base / f"uv-{version}"
            
            if not inspect_bubble_structure(bubble_path) or not validate_bubble(bubble_path, version):
                test_results[version] = False
                all_tests_passed = False
                continue
            
            version_passed = test_direct_binary_execution(bubble_path, version)
            test_results[version] = version_passed
            all_tests_passed &= version_passed
            
            if version_passed:
                print(f"   🎯 Overall result for uv=={version}: PASSED")
            else:
                print(f"   ❌ Overall result for uv=={version}: FAILED")
        
        print_header("FINAL TEST RESULTS")
        print(f"📊 Test Summary:")
        for version_key, passed in test_results.items():
            status = "✅ PASSED" if passed else "❌ FAILED"
            print(f"   uv=={version_key}: {status}")
        
        if all_tests_passed:
            print("\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉")
            print("🔥 OMNIPKG UV BINARY HANDLING IS FULLY FUNCTIONAL! 🔥")
        else:
            print("\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥")
            print("🔧 Check the detailed output above for diagnostics")
        
        return all_tests_passed
    
    except Exception as e:
        print(f"\n❌ Critical error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print_header("STEP 4: Cleanup")
        try:
            config_manager = ConfigManager()
            omnipkg_core = OmnipkgCore(config_manager.config)
            site_packages = Path(config_manager.config['site_packages_path'])
            for bubble in omnipkg_core.multiversion_base.glob("uv-*"):
                if bubble.is_dir():
                    print(f"   🧹 Removing test bubble: {bubble.name}")
                    shutil.rmtree(bubble, ignore_errors=True)
            for cloaked in site_packages.glob("uv.*_omnipkg_cloaked*"):
                print(f"   🧹 Removing residual cloaked: {cloaked.name}")
                shutil.rmtree(cloaked, ignore_errors=True)
            for cloaked in site_packages.glob("uv.*_test_harness_cloaked*"):
                print(f"   🧹 Removing test harness residual cloaked: {cloaked.name}")
                shutil.rmtree(cloaked, ignore_errors=True)
            print("✅ Cleanup complete")
        except Exception as e:
            print(f"⚠️  Cleanup failed: {e}")

if __name__ == "__main__":
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)