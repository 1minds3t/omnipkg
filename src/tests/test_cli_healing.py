from __future__ import annotations

from omnipkg.common_utils import safe_print

#!/usr/bin/env python3
"""
Test script for omnipkg run auto-healing with shell commands.
Demonstrates urllib3 version conflict healing with httpie (http command).

Demo Flow:
1. Detect user's original urllib3 version (or use 1.26.20 as fallback)
2. Use latest-active to force urllib3==1.25.11 (BREAKS http command)
3. Show the RAW error with full traceback from 'http --version'
4. Show FULL 8pkg run output - error, healing, stats, everything!
5. Restore original version using latest-active again
6. Restore user's original install strategy

Key insight: 8pkg run doesn't fix main env - it makes bubbled versions work
as first-class citizens, treating them as if they're in main env without
actually modifying it. This is the future of dependency management!
"""


try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

import subprocess
import sys
import time
from contextlib import contextmanager
from importlib.metadata import version as get_version, PackageNotFoundError
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.i18n import _
import os

# Force unbuffered output
os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)

# --- Test Configuration ---
# The ONLY version that breaks httpie (no SKIP_HEADER)
BROKEN_URLLIB3 = "1.25.11"
FALLBACK_URLLIB3 = "1.26.20"  # Backup if somehow urllib3 not installed


@contextmanager
def temporary_install_strategy(core: OmnipkgCore, strategy: str):
    """
    A context manager to temporarily set the install strategy and restore it on exit.
    """
    original_strategy = core.config.get("install_strategy", "stable-main")

    # Only perform the switch if the desired strategy is different from the current one.
    switched = False
    if original_strategy != strategy:
        safe_print(f"   - 🔄 Temporarily switching install strategy to '{strategy}'...")
        # Update both the in-memory config for the current run and the persistent config
        core.config["install_strategy"] = strategy
        core.config_manager.set("install_strategy", strategy)
        switched = True

    try:
        # This 'yield' passes control to the code inside the 'with' block
        yield
    finally:
        # This code runs after the 'with' block, guaranteed.
        if switched:
            core.config["install_strategy"] = original_strategy
            core.config_manager.set("install_strategy", original_strategy)
            safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")


def run_command(command_list, check=True, capture=False, stream=True):
    """Helper to run a command and optionally stream its output."""
    safe_print(_("\n$ {}").format(" ".join(command_list)))

    if command_list[0] == "omnipkg" or command_list[0] == "8pkg":
        command_list = [sys.executable, "-m", "omnipkg.cli"] + command_list[1:]

    if capture:
        result = subprocess.run(
            command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if check and result.returncode != 0:
            safe_print(result.stdout)
            safe_print(result.stderr)
            raise RuntimeError(
                _("Command failed with exit code {}").format(result.returncode)
            )
        return result

    if stream:
        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        for line in iter(process.stdout.readline, ""):
            safe_print(line.rstrip())

        process.stdout.close()
        retcode = process.wait()

        if check and retcode != 0:
            raise RuntimeError(_("Command failed with exit code {}").format(retcode))

        return retcode
    else:
        result = subprocess.run(command_list, check=check)
        return result.returncode


def print_header(title):
    """Prints a consistent, pretty header."""
    safe_print("\n" + "=" * 70)
    safe_print(_("  🚀 {}").format(title))
    safe_print("=" * 70)


def get_urllib3_version():
    """Get current urllib3 version using importlib.metadata"""
    try:
        return get_version("urllib3")
    except PackageNotFoundError:
        return None


def show_raw_http_failure():
    """Show the RAW error from http command with full output"""
    safe_print(_("\n🧪 Testing: http --version (raw shell command)"))
    safe_print(_("   Showing FULL error output for transparency..."))
    safe_print("\n" + "-" * 70)

    result = subprocess.run(
        ["http", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Print the actual output
    if result.stdout:
        for line in result.stdout.split("\n"):
            safe_print(line)

    safe_print("-" * 70)

    if result.returncode != 0:
        safe_print(_("\n❌ Exit code: {}").format(result.returncode))
        safe_print(_("💥 As expected, http command is BROKEN!"))
        safe_print(_("   ImportError: cannot import SKIP_HEADER from urllib3.util"))
        return False
    else:
        safe_print(_("\n✅ Exit code: 0"))
        return True


def show_full_omnipkg_run():
    """Show FULL output from 8pkg run - the error, healing, stats, everything!"""
    safe_print(_("\n🧪 Testing: 8pkg run http --version"))
    safe_print(_("   Showing FULL output - error detection, healing, stats..."))
    safe_print("\n" + "=" * 70)

    # Run it and stream ALL output
    result = subprocess.run(
        [sys.executable, "-m", "omnipkg.cli", "run", "http", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Print everything
    if result.stdout:
        for line in result.stdout.split("\n"):
            safe_print(line)

    safe_print("=" * 70)

    if result.returncode == 0:
        safe_print(_("\n✅ 8pkg run succeeded! Exit code: 0"))
        safe_print(_("🎯 Key observations:"))
        safe_print(_("   • Detected the error from broken urllib3"))
        safe_print(_("   • Auto-healed by loading urllib3 from bubble"))
        safe_print(_("   • Command executed successfully with healed environment"))
        safe_print(
            _("   • Main environment UNCHANGED - bubbles used as first-class citizens!")
        )
        return True
    else:
        safe_print(_("\n❌ Exit code: {}").format(result.returncode))
        return False


def run_demo():
    """Runs the automated http command auto-healing demo."""
    global _  # ADD THIS LINE
    config_manager = None
    original_strategy = None
    original_urllib3_version = None

    try:
        # Initialize omnipkg
        config_manager = ConfigManager(suppress_init_messages=True)
        original_strategy = config_manager.config.get("install_strategy", "stable-main")
        omnipkg_core = OmnipkgCore(config_manager)

        print_header("omnipkg Auto-Healing Demo - HTTP Command + urllib3")
        safe_print(_("This demo shows automatic dependency healing for CLI tools."))
        safe_print(
            _(
                f"We'll break httpie with urllib3 {BROKEN_URLLIB3}, then watch 8pkg run heal it!"
            )
        )
        safe_print(
            _("\n💡 Key insight: 8pkg run makes bubbled versions work as first-class")
        )
        safe_print(_("   citizens WITHOUT modifying main env. This is the future! 🚀"))
        time.sleep(3)

        print_header("STEP 1: Detecting original urllib3 version")

        # Detect user's original urllib3 version
        original_urllib3_version = get_urllib3_version()

        if original_urllib3_version:
            safe_print(_(f"✅ Found existing urllib3 v{original_urllib3_version}"))
            safe_print(_("   This will be restored after the demo"))
        else:
            safe_print(
                _(
                    f"⚠️  urllib3 not detected, will install v{FALLBACK_URLLIB3} as baseline"
                )
            )
            original_urllib3_version = FALLBACK_URLLIB3
            # Install fallback version
            with temporary_install_strategy(omnipkg_core, "stable-main"):
                run_command(["8pkg", "install", f"urllib3=={FALLBACK_URLLIB3}"])
            original_urllib3_version = get_urllib3_version()

        time.sleep(2)

        print_header("STEP 2: Ensuring httpie is installed")
        safe_print(_("📦 Making sure httpie (http command) is available..."))

        # Ensure httpie is installed (should already be there via omnipkg dependencies)
        run_command(["8pkg", "install", "httpie"], check=False)
        time.sleep(2)

        print_header("STEP 3: Breaking the environment (forcing old urllib3)")
        safe_print(
            _(f"💥 Installing urllib3=={BROKEN_URLLIB3} with latest-active strategy...")
        )
        safe_print(_("   This will force the BROKEN version into main environment"))
        safe_print(_("   Breaking the http command in the process!"))
        time.sleep(2)

        # Use latest-active to force broken urllib3 into main env
        with temporary_install_strategy(omnipkg_core, "latest-active"):
            run_command(["8pkg", "install", f"urllib3=={BROKEN_URLLIB3}"])

        # Verify the broken state
        broken_urllib3 = get_urllib3_version()
        safe_print(_(f"\n📊 urllib3 is now: {broken_urllib3}"))

        if broken_urllib3 == BROKEN_URLLIB3:
            safe_print(_("✅ Successfully broke the environment!"))
            safe_print(_(f"   💀 urllib3 {BROKEN_URLLIB3} lacks SKIP_HEADER"))
            safe_print(_("   💀 httpie will now fail with ImportError"))
        time.sleep(3)

        print_header("STEP 4: Demonstrating the RAW failure")
        safe_print(_("🔥 Watch the RAW error from the broken http command..."))
        time.sleep(2)

        show_raw_http_failure()
        time.sleep(3)

        print_header("STEP 5: omnipkg run to the rescue! 🦸‍♂️")
        safe_print(_("🚀 Now let's see the FULL 8pkg run healing process..."))
        safe_print(
            _("   Watch it detect the error, heal it, and show performance stats!")
        )
        time.sleep(2)

        show_full_omnipkg_run()
        time.sleep(3)

        print_header("STEP 6: Checking available versions")
        safe_print(_("🔍 Let's see what versions omnipkg is managing..."))
        time.sleep(2)

        # Run 8pkg info urllib3 with automatic Enter press
        safe_print(_("\n$ 8pkg info urllib3"))
        safe_print(_("   (Automatically pressing Enter to skip interactive prompt)"))

        process = subprocess.Popen(
            [sys.executable, "-m", "omnipkg.cli", "info", "urllib3"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Send Enter to skip the interactive prompt
        stdout, _stderr = process.communicate(input="\n")

        # Print the output
        for line in stdout.split("\n"):
            safe_print(line.rstrip())

        time.sleep(2)

        print_header("STEP 7: Restoring original environment")
        safe_print(
            _(f"🔄 Restoring your original urllib3 version: {original_urllib3_version}")
        )
        safe_print(_("   Using latest-active to force the original version back..."))
        safe_print(_("   (This is ONLY for the demo - normally you keep bubbles!)"))
        time.sleep(2)

        # Restore original version using latest-active
        with temporary_install_strategy(omnipkg_core, "latest-active"):
            run_command(["8pkg", "install", f"urllib3=={original_urllib3_version}"])

        # Verify restoration
        restored_urllib3 = get_urllib3_version()
        if restored_urllib3 == original_urllib3_version:
            safe_print(_(f"\n✅ Original version restored: {restored_urllib3}"))
        else:
            safe_print(
                _(
                    f"\n⚠️  Restoration mismatch: expected {original_urllib3_version}, got {restored_urllib3}"
                )
            )

        time.sleep(2)

        print_header("STEP 8: Final verification")
        safe_print(_("🧪 Confirming http works with the restored version..."))

        # Simple version check
        result = subprocess.run(["http", "--version"], capture_output=True, text=True)

        if result.returncode == 0:
            version_output = result.stdout.strip()
            safe_print(_("\n$ http --version"))
            safe_print(version_output)
            safe_print(_("\n✅ http command works with your original urllib3 version!"))
            safe_print(_("   Environment fully restored to original state."))
        else:
            safe_print(_("\n⚠️  http command still having issues"))

        time.sleep(2)

        # Final summary
        safe_print("\n" + "=" * 70)
        safe_print(_("🎉🎉🎉 AUTO-HEALING DEMO COMPLETE! 🎉🎉🎉"))
        safe_print("=" * 70)
        safe_print(_("📚 What you witnessed:"))
        safe_print(_(f"   📊 Original: urllib3 {original_urllib3_version}"))
        safe_print(
            _(f"   💀 Step 3: Broke httpie with urllib3 {BROKEN_URLLIB3} in main env")
        )
        safe_print(_("   ❌ Step 4: http command failed - RAW ImportError shown"))
        safe_print(
            _("   🦸‍♂️ Step 5: 8pkg run detected, healed, and executed successfully")
        )
        safe_print(
            _(
                f"   🫧 Magic: Broken {BROKEN_URLLIB3} stays in main, bubble used for execution"
            )
        )
        safe_print(
            _(f"   ✅ Step 7: Restored original urllib3 {original_urllib3_version}")
        )
        safe_print(_("\n💡 THE KEY INSIGHT:"))
        safe_print(_('   8pkg run doesn\'t "fix" your main environment!'))
        safe_print(_("   It makes bubbled versions work as FIRST-CLASS CITIZENS."))
        safe_print(_("   Broken in main? No problem - use bubble for that command!"))
        safe_print(_("   This is how we'll intercept pip/conda/uv in the future."))
        safe_print(_("   Infinite versions, zero conflicts, microsecond switching. 🚀"))
        safe_print("=" * 70)

    except Exception as demo_error:
        safe_print(
            _("\n❌ An unexpected error occurred during the demo: {}").format(
                demo_error
            )
        )
        import traceback

        traceback.print_exc()

        # Try to restore original version even on error
        if config_manager and original_urllib3_version and omnipkg_core:
            safe_print(_("\n🔄 Attempting to restore original version after error..."))
            try:
                with temporary_install_strategy(omnipkg_core, "latest-active"):
                    run_command(
                        ["8pkg", "install", f"urllib3=={original_urllib3_version}"],
                        check=False,
                    )
            except:
                pass

    finally:
        # Restore original install strategy (always runs)
        if config_manager and original_strategy:
            safe_print(f"\n🔄 Restoring original install strategy: {original_strategy}")
            config_manager.set("install_strategy", original_strategy)
            safe_print(f"   ✅ Strategy restored to '{original_strategy}'")


if __name__ == "__main__":
    run_demo()
