import sys
import subprocess
import os
import shutil
import tempfile
import time
import json # Added to serialize config for subprocess
from pathlib import Path

# Adjust ROOT_DIR for importing omnipkg modules in subprocesses
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR.parent) not in sys.path: # Add omnipkg's root directory to sys.path
    sys.path.insert(0, str(ROOT_DIR.parent))

try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore # Corrected import alias
    from omnipkg.loader import omnipkgLoader # Ensure loader is imported
except ImportError as e:
    print(f"❌ Failed to import omnipkg modules. Is it installed correctly? Error: {e}")
    print(f"💡 Try installing with: pip install omnipkg")
    sys.exit(1)

LATEST_RICH_VERSION = "13.7.1"
BUBBLE_VERSIONS_TO_TEST = ["13.5.3", "13.4.2"]

def print_header(title):
    print("\n" + "="*80)
    print(f"  🚀 {title}")
    print("="*80)

def print_subheader(title):
    print(f"\n--- {title} ---")

def setup_environment():
    print_header("STEP 1: Establishing a Clean Baseline Environment")
    config = ConfigManager().config
    omnipkg_core = OmnipkgCore(config)
    
    # Clean up old bubbles and cloaked artifacts
    site_packages = Path(config["site_packages_path"])
    for bubble in omnipkg_core.multiversion_base.glob("rich-*"):
        print(f"   🧹 Removing old bubble: {bubble.name}")
        shutil.rmtree(bubble, ignore_errors=True)
    # Be more specific for cloaked items, using the pattern OmnipkgLoader creates
    for cloaked in site_packages.glob("rich.*_omnipkg_cloaked*"):
        print(f"   🧹 Removing residual cloaked: {cloaked.name}")
        shutil.rmtree(cloaked, ignore_errors=True)
    
    # Install baseline version
    print(f"   📦 Ensuring main environment has baseline: rich=={LATEST_RICH_VERSION}")
    omnipkg_core.smart_install([f"rich=={LATEST_RICH_VERSION}"])
    
    print("✅ Environment prepared")
    return config

def create_test_bubbles(config):
    print_header("STEP 2: Creating Test Bubbles for Older Versions")
    omnipkg_core = OmnipkgCore(config)
    for version in BUBBLE_VERSIONS_TO_TEST:
        print(f"   🫧 Creating bubble for rich=={version}")
        omnipkg_core.smart_install([f"rich=={version}"]) # Use smart_install, it creates bubbles if needed
    return BUBBLE_VERSIONS_TO_TEST

def test_python_import(expected_version: str, config: dict, is_bubble: bool):
    print(f"   🔧 Testing import of version {expected_version}...")
    
    # Serialize config for passing to the subprocess
    config_json_str = json.dumps(config)
    
    # The core change: Use with statement for omnipkgLoader
    test_script_content = f"""
import sys
import importlib
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import json

# Ensure omnipkg's root is in sys.path for importing its modules (e.g., omnipkg.loader)
sys.path.insert(0, r"{Path(__file__).resolve().parents[1].parent}")

from omnipkg.loader import omnipkgLoader
# omnipkg.core.ConfigManager is also imported in subprocess for omnipkgLoader if config is passed

def test_import_and_version():
    target_package_spec = "rich=={expected_version}"
    
    # Load config in the subprocess
    subprocess_config = json.loads('{config_json_str}')

    # If it's the main environment test, we don't use the loader context for explicit switching,
    # just import directly from the system state.
    if not {is_bubble}: # Use the boolean value directly
        try:
            actual_version = version('rich')
            expected_version = "{expected_version}"
            assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
            print(f"✅ Imported and verified version {{actual_version}}")
        except PackageNotFoundError:
            print(f"❌ Test failed: Package 'rich' not found in main environment.", file=sys.stderr)
            sys.exit(1)
        except AssertionError as e:
            print(f"❌ Test failed: {{e}}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ An unexpected error occurred in main env test: {{e}}", file=sys.stderr)
            sys.exit(1)
        return

    # For bubble tests, use the omnipkgLoader context manager
    try:
        with omnipkgLoader(target_package_spec, config=subprocess_config):
            # Inside this block, the specific 'rich' version should be active
            import rich
            actual_version = version('rich')
            expected_version = "{expected_version}"
            assert actual_version == expected_version, f"Version mismatch! Expected {{expected_version}}, got {{actual_version}}"
            print(f"✅ Imported and verified version {{actual_version}}")
    except PackageNotFoundError:
        print(f"❌ Test failed: Package 'rich' not found in bubble context '{{target_package_spec}}'.", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"❌ Test failed: {{e}}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ An unexpected error occurred activating/testing bubble '{{target_package_spec}}': {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    test_import_and_version()
"""
    
    site_packages = Path(config["site_packages_path"])
    # Cloaking logic is handled by omnipkgLoader's __enter__ for bubble tests.
    # We temporarily move the main installation out of the way *before* the subprocess starts
    # so the subprocess only sees the controlled environment or the 'cloaked' absence.
    # The omnipkgLoader itself does the actual cloaking/uncloaking inside the subprocess's context.
    # This outer cloaking is a safety measure if the subprocess relies on a clean slate before loader.
    main_rich_dir = site_packages / "rich"
    main_rich_dist = next(site_packages.glob("rich-*.dist-info"), None)
    cloaked_paths_by_test_harness = [] # Track paths cloaked by this test harness (outside subprocess)

    try:
        if is_bubble: # Only cloak if we're testing a bubble to allow main env to be seen for its test
            if main_rich_dir.exists():
                cloak_path = main_rich_dir.with_name(f"rich.{int(time.time()*1000)}_test_harness_cloaked")
                shutil.move(main_rich_dir, cloak_path)
                cloaked_paths_by_test_harness.append((main_rich_dir, cloak_path))
                # print(f"   🛡️  Test harness cloaked main rich module to {cloak_path.name}") # Verbose
            if main_rich_dist and main_rich_dist.exists():
                cloak_path = main_rich_dist.with_name(f"{main_rich_dist.name}.{int(time.time()*1000)}_test_harness_cloaked")
                shutil.move(main_rich_dist, cloak_path)
                cloaked_paths_by_test_harness.append((main_rich_dist, cloak_path))
                # print(f"   🛡️  Test harness cloaked main rich dist-info to {cloak_path.name}") # Verbose

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(test_script_content)
            temp_script_path = f.name
        
        result = subprocess.run(
            [sys.executable, temp_script_path],
            capture_output=True, text=True, timeout=60 # Increased timeout for slow environments
        )
        if result.returncode == 0:
            print(f"      └── {result.stdout.strip()}")
            return True
        else:
            print(f"   ❌ Subprocess FAILED for version {expected_version}:")
            print(f"      STDERR: {result.stderr.strip()}")
            return False
        
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Subprocess FAILED for version {expected_version}:")
        print(f"      STDERR: {e.stderr.strip()}")
        return False
    finally:
        # Restore cloaked items by this test harness in case of any error
        for original, cloaked in reversed(cloaked_paths_by_test_harness):
            if cloaked.exists():
                if original.exists(): # Ensure original path is clean before moving back
                    shutil.rmtree(original, ignore_errors=True)
                try:
                    shutil.move(cloaked, original)
                    # print(f"   🛡️  Test harness restored {original.name}") # Verbose
                except Exception as e:
                    print(f"   ⚠️  Test harness failed to restore {original.name} from {cloaked.name}: {e}")
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def run_comprehensive_test():
    try:
        config = setup_environment()
        test_versions_to_bubble = create_test_bubbles(config)
        
        print_header("STEP 3: Comprehensive Version Testing")
        test_results = {}
        
        print_subheader(f"Testing Main Environment ({LATEST_RICH_VERSION})")
        # For the main environment, is_bubble=False, so loader.py's subprocess will directly import.
        test_results[f"main-{LATEST_RICH_VERSION}"] = test_python_import(LATEST_RICH_VERSION, config, is_bubble=False)

        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(f"Testing Bubble (rich=={version})")
            # For bubbles, is_bubble=True, so loader.py's subprocess will use omnipkgLoader context.
            test_results[f"bubble-{version}"] = test_python_import(version, config, is_bubble=True)
            
        print_header("FINAL TEST RESULTS")
        all_passed = all(test_results.values())
        for test_name, passed in test_results.items():
            print(f"   - {test_name.ljust(25)}: {'✅ PASSED' if passed else '❌ FAILED'}")
        if all_passed:
            print("\n🎉🎉🎉 ALL TESTS PASSED! 🎉🎉🎉")
        else:
            print("\n💥 SOME TESTS FAILED.")
        return all_passed
    except Exception as e:
        print(f"\n❌ Critical error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print_header("STEP 4: Cleanup")
        config = ConfigManager().config
        omnipkg_core = OmnipkgCore(config)
        site_packages = Path(config["site_packages_path"])
        for bubble in omnipkg_core.multiversion_base.glob("rich-*"):
            print(f"   🧹 Removing test bubble: {bubble.name}")
            shutil.rmtree(bubble, ignore_errors=True)
        # Specific cleanup for cloaked items that might have been left by the test harness itself
        for cloaked in site_packages.glob("rich.*_omnipkg_cloaked*"): 
            print(f"   🧹 Removing residual cloaked: {cloaked.name}")
            shutil.rmtree(cloaked, ignore_errors=True)
        for cloaked in site_packages.glob("rich.*_test_harness_cloaked*"): # Cleanup for test harness's own cloaks
            print(f"   🧹 Removing test harness residual cloaked: {cloaked.name}")
            shutil.rmtree(cloaked, ignore_errors=True)

        print(f"   📦 Restoring main environment: rich=={LATEST_RICH_VERSION}")
        omnipkg_core.smart_install([f"rich=={LATEST_RICH_VERSION}"])
        print("✅ Cleanup complete")

if __name__ == "__main__":
    sys.exit(0 if run_comprehensive_test() else 1)