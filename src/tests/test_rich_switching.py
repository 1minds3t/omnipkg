from omnipkg.common_utils import safe_print
from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy, WorkerPoolDaemon
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
import sys
import time
import shutil
import subprocess
import traceback
from pathlib import Path

# Setup project path to allow omnipkg imports
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


# Configuration
DEFAULT_RICH_VERSION = "13.7.1"
BUBBLE_VERSIONS_TO_TEST = ["13.5.3", "13.4.2"]


def print_header(title):
    safe_print("\n" + "=" * 80)
    safe_print(f"  🚀 {title}")
    safe_print("=" * 80)


def force_clean_rich():
    """Force remove any existing Rich installation to avoid corruption."""
    safe_print("   🧹 Force removing any existing Rich installation...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "rich", "-y"],
            capture_output=True,
            text=True,
            check=False,
        )
        safe_print("   ✅ Rich uninstalled successfully (if it was present).")
    except Exception as e:
        safe_print(f"   ⚠️  Uninstall attempt completed with note: {e}")


def ensure_daemon_running():
    """Ensures the worker daemon is up and running."""
    safe_print("   ⚙️  Checking Worker Daemon status...")
    client = DaemonClient()
    status = client.status()

    if not status.get("success"):
        safe_print("   🚀 Daemon not running. Starting it now...")
        WorkerPoolDaemon().start(daemonize=True)
        # Give it a moment to warm up
        for unused in range(20):
            if client.status().get("success"):
                break
            time.sleep(0.1)
        safe_print("   ✅ Daemon started and ready.")
    else:
        safe_print("   ✅ Daemon is already running.")

    return client


def setup_environment(omnipkg_core: OmnipkgCore):
    """
    Adapts to the current environment and cleans up old bubbles.
    """
    print_header("STEP 1: Environment Setup & Cleanup")

    # Force clean Rich first to avoid v14.2.0 corruption issues
    force_clean_rich()

    omnipkg_core.config_manager.set("install_strategy", "stable-main")
    safe_print("   ⚙️  Install strategy set to: stable-main")

    safe_print("   🧹 Cleaning up old demo bubbles...")
    for bubble in omnipkg_core.multiversion_base.glob("rich-*"):
        shutil.rmtree(bubble, ignore_errors=True)

    # Find active version (should be None after force clean)
    all_installations = omnipkg_core._find_package_installations("rich")
    active_install_info = next(
        (inst for inst in all_installations if inst.get("install_type") == "active"),
        None,
    )

    main_rich_version = (
        active_install_info.get("Version") if active_install_info else None
    )

    if main_rich_version:
        safe_print(
            f"   ⚠️  Found existing Rich v{main_rich_version} (unexpected after cleanup)."
        )
    else:
        safe_print(
            f"   ℹ️  Rich not found. Installing baseline v{DEFAULT_RICH_VERSION}..."
        )
        omnipkg_core.smart_install([f"rich=={DEFAULT_RICH_VERSION}"])
        main_rich_version = DEFAULT_RICH_VERSION

    safe_print("✅ Environment prepared")
    return main_rich_version


def create_test_bubbles(omnipkg_core: OmnipkgCore):
    print_header("STEP 2: Creating Test Bubbles")

    # Pre-calculate active version to avoid redundant bubbles
    main_version = omnipkg_core._find_package_installations("rich")
    active_version = next(
        (
            inst.get("Version")
            for inst in main_version
            if inst.get("install_type") == "active"
        ),
        None,
    )

    for version in BUBBLE_VERSIONS_TO_TEST:
        if version == active_version:
            safe_print(
                f"   ℹ️  Skipping bubble for v{version} (matches active version)."
            )
            continue

        safe_print(f"   🫧 Creating bubble for rich=={version}")
        omnipkg_core.smart_install([f"rich=={version}"])


def test_version_via_daemon(
    expected_version: str, client: DaemonClient, is_bubble: bool
):
    """
    Verifies version using the high-performance Daemon.
    """
    from importlib.metadata import version

    if is_bubble:
        # 🚀 DAEMON PATH: Run inside an isolated, persistent worker
        safe_print(f"   ⚡ Verifying v{expected_version} via Daemon Worker...")

        proxy = DaemonProxy(client, f"rich=={expected_version}")

        # Code to execute inside the worker - prints version and path
        code = """
import rich
from importlib.metadata import version
print(f"VERSION={version('rich')}")
print(f"PATH={rich.__file__}")
"""

        start = time.perf_counter()
        result = proxy.execute(code)
        duration = (time.perf_counter() - start) * 1000

        if result.get("success"):
            # Parse stdout to extract version and path
            stdout = result.get("stdout", "")
            actual_version = None
            actual_path = None

            for line in stdout.strip().split("\n"):
                if line.startswith("VERSION="):
                    actual_version = line.split("=", 1)[1]
                elif line.startswith("PATH="):
                    actual_path = line.split("=", 1)[1]

            safe_print(f"      - Version: {actual_version}")
            safe_print(f"      - Path: {actual_path}")
            safe_print(f"      - Latency: {duration:.2f}ms")

            if actual_version != expected_version:
                raise AssertionError(
                    f"Bubble mismatch! Expected {expected_version}, got {actual_version}"
                )
        else:
            raise RuntimeError(f"Daemon execution failed: {result.get('error')}")

    else:
        # MAIN ENV PATH: Check local process
        safe_print(f"   🏠 Verifying v{expected_version} in Main Process...")

        actual_version = version("rich")

        if actual_version != expected_version:
            raise AssertionError(
                f"Main env mismatch! Expected {expected_version}, got {actual_version}"
            )

    safe_print(f"   ✅ Verified version {actual_version}")


def run_comprehensive_test():
    main_version_to_preserve = None
    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        omnipkg_core = OmnipkgCore(config_manager)

        # 1. Setup
        main_version_to_preserve = setup_environment(omnipkg_core)
        create_test_bubbles(omnipkg_core)

        # 2. Ensure Daemon is ready
        daemon_client = ensure_daemon_running()

        print_header("STEP 3: High-Speed Version Verification")
        test_results = {}

        # Test Main Environment
        safe_print(
            f"\n--- Testing Main Environment (rich=={main_version_to_preserve}) ---"
        )
        try:
            test_version_via_daemon(
                main_version_to_preserve, daemon_client, is_bubble=False
            )
            test_results[f"main-{main_version_to_preserve}"] = True
        except Exception as e:
            safe_print(f"   ❌ FAILED: {e}")
            test_results[f"main-{main_version_to_preserve}"] = False

        # Test Bubbled Versions via Daemon
        for version in BUBBLE_VERSIONS_TO_TEST:
            if version == main_version_to_preserve:
                continue

            safe_print(f"\n--- Testing Bubble (rich=={version}) ---")
            try:
                test_version_via_daemon(version, daemon_client, is_bubble=True)
                test_results[f"bubble-{version}"] = True
            except Exception as e:
                safe_print(f"   ❌ FAILED: {e}")
                test_results[f"bubble-{version}"] = False

        print_header("FINAL TEST RESULTS")
        all_passed = all(test_results.values())
        for name, passed in test_results.items():
            status = "✅ PASSED" if passed else "❌ FAILED"
            safe_print(f"   {name:<25} : {status}")

        return all_passed

    except Exception as e:
        safe_print(f"\n❌ Critical error: {e}")
        traceback.print_exc()
        return False
    finally:
        print_header("STEP 4: Cleanup")
        if "omnipkg_core" in locals():
            safe_print("   🧹 Removing test bubbles...")
            omnipkg_core.smart_uninstall(
                [f"rich=={v}" for v in BUBBLE_VERSIONS_TO_TEST], force=True
            )
            safe_print(
                f"   ✅ Main environment (v{main_version_to_preserve}) preserved."
            )


if __name__ == "__main__":
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)
