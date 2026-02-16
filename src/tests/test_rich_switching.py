import sys
import time
import subprocess
import traceback
import json
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

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
        if bubble_path.exi
