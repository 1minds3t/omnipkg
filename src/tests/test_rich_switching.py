import sys
import time
import subprocess
import traceback
import json
from pathlib import Path
try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:
    from importlib_metadata import version, PackageNotFoundError

# Setup project path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from omnipkg.common_utils import safe_print
from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.i18n import _

# Configuration
DEFAULT_RICH_VERSION = "13.7.1"
BUBBLE_VERSIONS_TO_TEST = ["13.5.3", "13.4.2"]

def print_header(title):
    safe_print("\n" + "=" * 80)
    safe_print(_('  🚀 {}').format(title))
    safe_print("=" * 80)

def ensure_daemon_running():
    """Ensures the worker daemon is up and running."""
    safe_print("   ⚙️  Checking Worker Daemon status...")
    client = DaemonClient()
    status = client.status()

    if not status.get("success"):
        safe_print("   🚀 Daemon not running. Starting it now...")
        # Start daemon in background
        subprocess.Popen(
            [sys.executable, "-m", "omnipkg.isolation.worker_daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x00000008 if sys.platform == 'win32' else 0, # DETACHED
        )
        
        # Wait for readiness
        safe_print("   ⏳ Waiting for daemon...", end="", flush=True)
        for i in range(50):
            time.sleep(0.2)
            status = client.status()
            if status.get("success"):
                safe_print(" ✅ Ready.")
                return client
            if i % 5 == 0: safe_print(".", end="", flush=True)
        
        raise RuntimeError("Daemon failed to start")
    else:
        safe_print("   ✅ Daemon is already running.")

    return client

def fast_setup(omnipkg_core: OmnipkgCore):
    """
    Checks for existing installations. Installs ONLY if missing.
    """
    print_header("STEP 1: Fast Environment Check")

    # 1. Check Main Environment
    try:
        current_main = version("rich")
        if current_main == DEFAULT_RICH_VERSION:
            safe_print(f"   ✅ Main Env: rich=={DEFAULT_RICH_VERSION} is already installed.")
        else:
            safe_print(f"   ⚠️  Main Env: Found v{current_main}, switching to v{DEFAULT_RICH_VERSION}...")
            omnipkg_core.smart_install([f"rich=={DEFAULT_RICH_VERSION}"])
    except PackageNotFoundError:
        safe_print(f"   ❌ Main Env: rich missing. Installing v{DEFAULT_RICH_VERSION}...")
        omnipkg_core.smart_install([f"rich=={DEFAULT_RICH_VERSION}"])

    # 2. Check Bubbles
    for v in BUBBLE_VERSIONS_TO_TEST:
        bubble_path = omnipkg_core.multiversion_base / f"rich-{v}"
        if bubble_path.exists() and (bubble_path / "rich").exists():
            safe_print(f"   ✅ Bubble:   rich=={v} exists at {bubble_path.name}")
        else:
            safe_print(f"   🛠️  Bubble:   rich=={v} missing. Installing...")
            omnipkg_core.smart_install([f"rich=={v}"])

def test_version_via_daemon(target_version: str, client: DaemonClient, is_bubble: bool):
    """
    Verifies version using the Daemon.
    """
    spec = f"rich=={target_version}"
    
    if is_bubble:
        safe_print(_('   ⚡ Verifying v{} via Daemon Worker...').format(target_version))
        proxy = DaemonProxy(client, spec)
    else:
        # For main env testing via daemon, we just use 'rich' without version constraints
        # or we rely on the daemon's default environment if no spec provided (but here we be explicit)
        safe_print(_('   🏠 Verifying v{} via Daemon (Main Env check)...').format(target_version))
        proxy = DaemonProxy(client, spec)

    code = "from importlib.metadata import version; import rich; print(f'VERSION={version(\"rich\")}|PATH={rich.__file__}')"


    start = time.perf_counter()
    result = proxy.execute(code)
    duration = (time.perf_counter() - start) * 1000

    if result.get("success"):
        stdout = result.get("stdout", "").strip()
        # Parse output "VERSION=x.y.z|PATH=..."
        try:
            parts = stdout.split("|")
            actual_version = parts[0].split("=")[1]
            actual_path = parts[1].split("=")[1]
            
            safe_print(f"      - Version: {actual_version}")
            safe_print(f"      - Path:    {actual_path}")
            safe_print(f"      - Latency: {duration:.2f}ms")

            if actual_version != target_version:
                safe_print(f"      ❌ MISMATCH! Expected {target_version}")
                return False
            return True
        except IndexError:
            safe_print(f"      ❌ Parse Error. Stdout: {stdout}")
            return False
    else:
        safe_print(f"      ❌ Execution Failed: {result.get('error')}")
        return False

def run_fast_test():
    try:
        cm = ConfigManager(suppress_init_messages=True)
        core = OmnipkgCore(cm)

        # 1. Fast Setup (Skipping installs if present)
        fast_setup(core)

        # 2. Daemon Check
        client = ensure_daemon_running()

        # 3. Run Tests
        print_header("STEP 2: Daemon Verification")
        results = {}

        # Test Main
        print(f"\n--- Testing Main Version ({DEFAULT_RICH_VERSION}) ---")
        results["Main"] = test_version_via_daemon(DEFAULT_RICH_VERSION, client, is_bubble=False)

        # Test Bubbles
        for v in BUBBLE_VERSIONS_TO_TEST:
            print(f"\n--- Testing Bubble Version ({v}) ---")
            results[f"Bubble-{v}"] = test_version_via_daemon(v, client, is_bubble=True)

        print_header("FINAL RESULTS")
        for k, v in results.items():
            print(f"{k:<15}: {'✅ PASSED' if v else '❌ FAILED'}")

    except KeyboardInterrupt:
        print("\n🛑 Interrupted.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run_fast_test()
