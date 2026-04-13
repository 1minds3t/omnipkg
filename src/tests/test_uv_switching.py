from omnipkg.common_utils import safe_print
from omnipkg.i18n import _
import sys
import os
import platform
from pathlib import Path
import subprocess
import shutil
import traceback
from importlib.metadata import version as get_version, PackageNotFoundError

# Setup project path for imports
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


# Apply language settings
lang_from_env = os.environ.get("OMNIPKG_LANG")
if lang_from_env:
    _.set_language(lang_from_env)

# Import core modules after path is set
try:
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    from omnipkg.loader import omnipkgLoader
except ImportError as e:
    safe_print(
        f"❌ Failed to import omnipkg modules. Is the project structure correct? Error: {e}"
    )
    sys.exit(1)

# --- Test Configuration ---
MAIN_UV_VERSION_FALLBACK = "0.9.5"
BUBBLE_VERSIONS_TO_TEST = ["0.4.30", "0.5.11"]

# Platform-aware binary name
UV_BIN_NAME = "uv.exe" if sys.platform == "win32" else "uv"


def print_header(title):
    safe_print("\n" + "=" * 80)
    safe_print(_('  🚀 {}').format(title))
    safe_print("=" * 80)


def parse_uv_version(version_output):
    import re
    match = re.search(r'\b(\d+\.\d+\.\d+)\b', version_output)
    if match:
        return match.group(1)
    return version_output.strip()


def print_subheader(title):
    safe_print(f"\n--- {title} ---")


def run_command(command, check=True):
    """Helper to run a command and capture output with Windows compatibility."""
    creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        creationflags=creationflags
    )


def find_uv_binary(bubble_path: Path) -> Path:
    """
    Find the uv binary in a bubble's bin dir, handling Windows .exe extension.
    Returns the Path if found, raises FileNotFoundError otherwise.
    """
    bin_dir = bubble_path / "bin"
    # Try exact platform name first, then case-insensitive glob as fallback
    for candidate in [UV_BIN_NAME, "uv.EXE", "uv"]:
        p = bin_dir / candidate
        if p.exists():
            return p
    # Last resort: glob for anything named uv* in bin/
    matches = list(bin_dir.glob("uv*"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No uv binary found in {bin_dir}")


def setup_environment(omnipkg_core: OmnipkgCore):
    print_header("STEP 1: Environment Setup & Cleanup")
    safe_print("   ⚠️  Skipping cleanup - files will be preserved for inspection")

    main_uv_version = None
    try:
        main_uv_version = get_version("uv")
        safe_print(
            f"   ✅ Found existing uv v{main_uv_version}. It will be used as the main version for the demo."
        )
    except PackageNotFoundError:
        safe_print(
            f"   ℹ️  'uv' not found in main environment. Installing a baseline version ({MAIN_UV_VERSION_FALLBACK}) for the demo."
        )
        omnipkg_core.smart_install([f"uv=={MAIN_UV_VERSION_FALLBACK}"])
        main_uv_version = MAIN_UV_VERSION_FALLBACK

    force_omnipkg_rescan(omnipkg_core, "uv")
    safe_print("✅ Environment prepared")
    return main_uv_version


def create_test_bubbles(omnipkg_core: OmnipkgCore):
    print_header("STEP 2: Creating Test Bubbles for Older Versions")

    python_context_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    for version in BUBBLE_VERSIONS_TO_TEST:
        bubble_name = f"uv-{version}"
        bubble_path = omnipkg_core.multiversion_base / bubble_name

        if bubble_path.exists():
            safe_print(
                f"   ✅ Bubble for uv=={version} already exists. Skipping creation."
            )
            continue

        safe_print(f"   🫧 Force-creating bubble for uv=={version}...")
        try:
            success = omnipkg_core.bubble_manager.create_isolated_bubble(
                "uv", version, python_context_version=python_context_version
            )

            if success:
                safe_print(_('   ✅ Bubble created: {}').format(bubble_name))
                omnipkg_core.rebuild_package_kb([f"uv=={version}"])
            else:
                safe_print(f"   ❌ Failed to create bubble for uv=={version}")

        except Exception as e:
            safe_print(f"   ❌ Failed to create bubble for uv=={version}: {e}")
            traceback.print_exc()


def force_omnipkg_rescan(omnipkg_core, package_name):
    safe_print(f"   🧠 Forcing omnipkg KB rebuild for {package_name}...")
    try:
        omnipkg_core.rebuild_package_kb([package_name])
    except Exception as e:
        safe_print(f"   ❌ KB rebuild for {package_name} failed: {e}")


def inspect_bubble_structure(bubble_path):
    """Prints a summary of the bubble's directory structure for verification."""
    safe_print(_('   🔍 Inspecting bubble structure: {}').format(bubble_path.name))
    if not bubble_path.exists():
        safe_print(_("   ❌ Bubble doesn't exist: {}").format(bubble_path))
        return False

    dist_info = list(bubble_path.glob("uv-*.dist-info"))
    if dist_info:
        safe_print(_('   ✅ Found dist-info: {}').format(dist_info[0].name))
    else:
        safe_print("   ⚠️  No dist-info found")

    bin_dir = bubble_path / "bin"
    if bin_dir.exists():
        items = list(bin_dir.iterdir())
        safe_print(f"   ✅ Found bin directory with {len(items)} items")
        # Platform-aware binary check
        try:
            uv_bin = find_uv_binary(bubble_path)
            safe_print(_('   ✅ Found uv binary: {}').format(uv_bin.name))
            if os.access(uv_bin, os.X_OK):
                safe_print("   ✅ Binary is executable")
            else:
                safe_print("   ⚠️  Binary is not executable")
        except FileNotFoundError:
            safe_print(f"   ⚠️  No uv binary found in bin/ (looked for {UV_BIN_NAME})")
            safe_print(f"   📋 bin/ contents: {[x.name for x in items]}")
    else:
        safe_print("   ⚠️  No bin directory found")

    contents = list(bubble_path.iterdir())
    safe_print(_('   📁 Bubble contents ({} items):').format(len(contents)))
    for item in sorted(contents)[:5]:
        suffix = "/" if item.is_dir() else ""
        safe_print(f"      - {item.name}{suffix}")

    return True


def test_swapped_binary_execution(expected_version, config, omnipkg_core):
    """Tests version swapping using omnipkgLoader with enhanced debugging."""
    safe_print("   🔧 Testing swapped binary execution via omnipkgLoader...")

    bubble_path = omnipkg_core.multiversion_base / f"uv-{expected_version}"

    # Platform-aware binary path
    try:
        bubble_binary = find_uv_binary(bubble_path)
    except FileNotFoundError as e:
        safe_print(f"   ❌ Cannot find bubble binary: {e}")
        return False

    try:
        with omnipkgLoader(
            f"uv=={expected_version}", config=config, quiet=True, force_activation=True
        ):
            path_entries = os.environ.get("PATH", "").split(os.pathsep)
            safe_print(_('   🔍 First 3 PATH entries: {}').format(path_entries[:3]))
            safe_print(_('   🔍 Which uv: {}').format(shutil.which('uv')))
            safe_print(_('   🔍 Bubble binary: {}').format(bubble_binary))

            # Test using system PATH
            result = run_command(["uv", "--version"])
            actual_version = parse_uv_version(result.stdout)
            safe_print(_('   📍 Version via PATH: {}').format(actual_version))

            # Test using direct bubble path
            result_direct = run_command([str(bubble_binary), "--version"])
            direct_version = parse_uv_version(result_direct.stdout)
            safe_print(_('   📍 Version via direct path: {}').format(direct_version))

            if actual_version == expected_version:
                safe_print(_('   ✅ Swapped binary reported: {}').format(actual_version))
                safe_print("   🎯 Swapped binary test: PASSED")
                return True
            else:
                safe_print(
                    _('   ❌ Version mismatch: expected {}, got {}').format(expected_version, actual_version)
                )
                if direct_version == expected_version:
                    safe_print(
                        _('   ⚠️  BUT direct binary path shows correct version {}').format(direct_version)
                    )
                    safe_print("   ⚠️  This suggests PATH manipulation issue")
                return False
    except Exception as e:
        safe_print(_('   ❌ Exception during test: {}').format(e))
        traceback.print_exc()
        return False


def run_comprehensive_test():
    print_header("🚨 OMNIPKG UV BINARY STRESS TEST (NO CLEANUP) 🚨")

    config_manager = None
    original_strategy = None
    main_uv_version_to_test = None

    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        omnipkg_core = OmnipkgCore(config_manager)

        original_strategy = config_manager.config.get("install_strategy", "stable-main")
        if original_strategy != "stable-main":
            safe_print(_('   ℹ️  Current install strategy: {}').format(original_strategy))
            safe_print(
                "   ⚙️  Temporarily setting install strategy to 'stable-main' for this test..."
            )
            config_manager.set("install_strategy", "stable-main")
            omnipkg_core = OmnipkgCore(config_manager)

        main_uv_version_to_test = setup_environment(omnipkg_core)

        create_test_bubbles(omnipkg_core)
        print_header("STEP 3: Comprehensive UV Version Testing")

        test_results = {}
        all_tests_passed = True

        # Test Main Environment
        print_subheader(_('Testing Main Environment (uv=={})').format(main_uv_version_to_test))
        try:
            uv_binary_path = shutil.which("uv")

            if not uv_binary_path:
                python_exe = config_manager.config.get("python_executable", sys.executable)
                scripts_dir = Path(python_exe).parent / ("Scripts" if sys.platform == "win32" else "bin")
                uv_binary_path = scripts_dir / UV_BIN_NAME
                if not uv_binary_path.exists():
                    raise FileNotFoundError(f"UV binary not found in {scripts_dir}")

            safe_print(_('   🔬 Testing binary at: {}').format(uv_binary_path))

            result = run_command([str(uv_binary_path), "--version"])
            actual_version = parse_uv_version(result.stdout)

            main_passed = actual_version == main_uv_version_to_test
            safe_print(_('   ✅ Main environment version: {}').format(actual_version))
            if not main_passed:
                safe_print(
                    _('   ❌ FAILED: Expected {} but found {}').format(main_uv_version_to_test, actual_version)
                )

            test_results[f"main-{main_uv_version_to_test}"] = main_passed
            all_tests_passed &= main_passed
        except Exception as e:
            safe_print(_('   ❌ Main environment test failed: {}').format(e))
            test_results[f"main-{main_uv_version_to_test}"] = False
            all_tests_passed = False

        # Test Bubbles
        for version in BUBBLE_VERSIONS_TO_TEST:
            print_subheader(_('Testing Bubble (uv=={})').format(version))
            bubble_path = omnipkg_core.multiversion_base / f"uv-{version}"
            if not inspect_bubble_structure(bubble_path):
                test_results[f"bubble-{version}"] = False
                all_tests_passed = False
                continue
            version_passed = test_swapped_binary_execution(
                version, config_manager.config, omnipkg_core
            )
            test_results[f"bubble-{version}"] = version_passed
            all_tests_passed &= version_passed

        # Report Results
        print_header("FINAL TEST RESULTS")
        safe_print("📊 Test Summary:")
        for version_key, passed in sorted(test_results.items()):
            status = "✅ PASSED" if passed else "❌ FAILED"
            safe_print(f"   {version_key.ljust(25)}: {status}")

        if not all_tests_passed:
            safe_print("\n💥 SOME TESTS FAILED - UV BINARY HANDLING NEEDS WORK 💥")
        else:
            safe_print("\n🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉")

        safe_print("\n📁 Bubble files preserved for inspection")
        safe_print(_('📍 Location: {}').format(omnipkg_core.multiversion_base))

        return all_tests_passed

    except Exception as e:
        safe_print(_('\n❌ Critical error during testing: {}').format(e))
        traceback.print_exc()
        return False
    finally:
        if config_manager and original_strategy and original_strategy != "stable-main":
            safe_print(_('\n🔄 Restoring original install strategy: {}').format(original_strategy))
            config_manager.set("install_strategy", original_strategy)


if __name__ == "__main__":
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)
