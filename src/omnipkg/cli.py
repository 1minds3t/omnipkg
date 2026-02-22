from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print
try:
    from .common_utils import safe_print
except ImportError:
    pass
from omnipkg.i18n import _, SUPPORTED_LANGUAGES
"""omnipkg CLI - Enhanced with runtime interpreter switching and language support"""


import argparse
import os
import re
import time
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from omnipkg.isolation.worker_daemon import cli_logs  # <--- NEW IMPORT
from omnipkg.isolation.worker_daemon import (
    cli_start,
    cli_status,
    cli_stop,
    cli_idle_config
)
try:
    from omnipkg.apis.local_bridge import WebBridgeManager
except ImportError:
    run_bridge_logic = None

from .commands.run import execute_run_command
from .common_utils import print_header
from .core import ConfigManager
from .core import omnipkg as OmnipkgCore

project_root = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).parent.parent / "tests"
DEMO_DIR = Path(__file__).parent
try:
    FILE_PATH = Path(__file__).resolve()
except NameError:
    FILE_PATH = Path.cwd()

# ─────────────────────────────────────────────────────────────────────────────
# DEBUG HELPER — use everywhere instead of bare print(..., file=sys.stderr)
# Set OMNIPKG_DEBUG=1 in environment to enable.
# ─────────────────────────────────────────────────────────────────────────────
_DBG = os.environ.get("OMNIPKG_DEBUG", "0") == "1"

def _dbg(msg: str):
    """Lightweight debug printer; no-op unless OMNIPKG_DEBUG=1."""
    if _DBG:
        print(f"[DEBUG-CLI] {msg}", file=sys.stderr, flush=True)


def get_actual_python_version():
    """Get the actual Python version being used by omnipkg, not just sys.version_info."""
    from omnipkg.core import ConfigManager
    try:
        cm = ConfigManager(suppress_init_messages=True)
        configured_exe = cm.config.get("python_executable")
        if configured_exe:
            version_tuple = cm._verify_python_version(configured_exe)
            if version_tuple:
                return version_tuple[:2]
        return sys.version_info[:2]
    except Exception:
        return sys.version_info[:2]


def debug_python_context(label=""):
    """Print comprehensive Python context information for debugging."""
    print(_('\n{}').format('=' * 70))
    safe_print(_('🔍 DEBUG CONTEXT CHECK: {}').format(label))
    print(_('{}').format('=' * 70))
    safe_print(_('📍 sys.executable:        {}').format(sys.executable))
    safe_print(_('📍 sys.version:           {}').format(sys.version))
    safe_print(
        _('📍 sys.version_info:      {}.{}.{}').format(sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    )
    safe_print(_('📍 os.getpid():           {}').format(os.getpid()))
    safe_print(f"📍 __file__ (if exists):  {__file__ if '__file__' in globals() else 'N/A'}")
    safe_print(_('📍 Path.cwd():            {}').format(Path.cwd()))

    relevant_env_vars = [
        "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
        "OMNIPKG_MAIN_ORCHESTRATOR_PID", "OMNIPKG_RELAUNCHED",
        "OMNIPKG_LANG", "PYTHONHOME", "PYTHONEXECUTABLE",
    ]
    safe_print("\n📦 Relevant Environment Variables:")
    for var in relevant_env_vars:
        value = os.environ.get(var, "NOT SET")
        print(_('   {}: {}').format(var, value))

    safe_print("\n📂 sys.path (first 5 entries):")
    for i, path in enumerate(sys.path[:5]):
        print(_('   [{}] {}').format(i, path))
    print(_('{}\n').format('=' * 70))


@contextmanager
def temporary_install_strategy(core: OmnipkgCore, strategy: str):
    """Context manager to temporarily set the install strategy and restore it on exit."""
    original_strategy = core.config.get("install_strategy", "stable-main")
    switched = False
    if original_strategy != strategy:
        safe_print(_("   - 🔄 Temporarily switching install strategy to '{}'...").format(strategy))
        core.config["install_strategy"] = strategy
        core.config_manager.set("install_strategy", strategy)
        switched = True
    try:
        yield
    finally:
        if switched:
            core.config["install_strategy"] = original_strategy
            core.config_manager.set("install_strategy", original_strategy)
            safe_print(_("   - ✅ Strategy restored to '{}'").format(original_strategy))


def separate_python_from_packages(packages):
    """
    Separates python interpreter requests from regular packages.
    Fixes the bug where packages like 'python-dateutil' were mistaken for interpreter requests.
    """
    regular_packages = []
    python_versions = []
    python_interpreter_pattern = re.compile(r"^python(?:[<>=!~].*)?$", re.IGNORECASE)
    for pkg in packages:
        pkg = pkg.strip()
        if not pkg:
            continue
        if python_interpreter_pattern.match(pkg):
            version_part = pkg[6:].strip()
            for op in ["==", ">=", "<=", ">", "<", "~="]:
                if version_part.startswith(op):
                    version_part = version_part[len(op):].strip()
                    break
            if version_part:
                python_versions.append(version_part)
        else:
            regular_packages.append(pkg)
    return regular_packages, python_versions


def upgrade(args, core):
    """Handler for the upgrade command."""
    package_name = args.package_name[0] if args.package_name else "omnipkg"
    if package_name.lower() == "omnipkg":
        return core.smart_upgrade(
            version=args.version, force=args.force, skip_dev_check=args.force_dev
        )
    safe_print(_("🔄 Upgrading '{}' to latest version...").format(package_name))
    with temporary_install_strategy(core, "latest-active"):
        return core.smart_install(packages=[package_name], force_reinstall=True)


def run_demo_with_enforced_context(
    source_script_path: Path,
    demo_name: str,
    pkg_instance: OmnipkgCore,
    parser_prog: str,
    required_version: str = None,
) -> int:
    """Run a demo test with enforced Python context."""
    actual_version = get_actual_python_version()
    target_version_str = (
        required_version if required_version else f"{actual_version[0]}.{actual_version[1]}"
    )
    if not source_script_path.exists():
        safe_print(_('❌ Error: Source test file {} not found.').format(source_script_path))
        return 1
    python_exe = pkg_instance.config_manager.get_interpreter_for_version(target_version_str)
    if not python_exe or not python_exe.exists():
        safe_print(_('❌ Python {} is not managed by omnipkg.').format(target_version_str))
        safe_print(_('   Please adopt it first: {} python adopt {}').format(parser_prog, target_version_str))
        return 1
    safe_print(f"🚀 Running {demo_name} demo with Python {target_version_str} via sterile environment...")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as temp_script:
        temp_script_path = Path(temp_script.name)
        temp_script.write(source_script_path.read_text(encoding="utf-8"))
    safe_print(_('   - Sterile script created at: {}').format(temp_script_path))
    try:
        return run_demo_with_live_streaming(
            test_file_name=str(temp_script_path),
            demo_name=demo_name,
            python_exe=str(python_exe),
        )
    finally:
        temp_script_path.unlink(missing_ok=True)


def handle_python_requirement(
    required_version_str: str, pkg_instance: OmnipkgCore, parser_prog: str,
    auto_adopt: bool = False,
) -> bool:
    """
    Checks if the current Python context matches the requirement.
    If not, it automatically finds, adopts (or downloads), and swaps to it.

    Args:
        auto_adopt: If True (non-interactive / --force), skip the confirmation prompt.
    """
    from omnipkg.common_utils import is_interactive_session, safe_input

    actual_version_tuple = get_actual_python_version()
    required_version_tuple = tuple(map(int, required_version_str.split(".")))

    if actual_version_tuple == required_version_tuple:
        return True

    print_header(_("Python Version Requirement"))
    safe_print(_("  - Diagnosis: This operation requires Python {}").format(required_version_str))
    safe_print(
        _("  - Current Context: Python {}.{}").format(
            actual_version_tuple[0], actual_version_tuple[1]
        )
    )

    managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
    needs_adopt = required_version_str not in managed_interpreters

    # Interactive: ask before adopting an unknown Python
    if needs_adopt and not auto_adopt and is_interactive_session():
        safe_print(_("\n  - ⚠️  Python {} is not yet adopted.").format(required_version_str))
        answer = safe_input(
            _("  Adopt Python {} now and continue? (Y/n): ").format(required_version_str),
            default="y",
        )
        if answer.lower() not in ("", "y", "yes"):
            safe_print(_("  ❌ Cancelled. Adopt first: {} python adopt {}").format(
                parser_prog, required_version_str))
            return False

    safe_print(
        _("  - Action: omnipkg will now attempt to automatically configure the correct interpreter.")
    )

    if needs_adopt:
        safe_print(
            _("\n   - Step 1: Adopting Python {}... (This may trigger a download)").format(
                required_version_str
            )
        )
        if pkg_instance.adopt_interpreter(required_version_str) != 0:
            safe_print(
                _("   - ❌ Failed to adopt Python {}. Cannot proceed.").format(required_version_str)
            )
            return False
        safe_print(_("   - ✅ Successfully adopted Python {}.").format(required_version_str))

    safe_print(
        _("\n   - Step 2: Swapping active context to Python {}...").format(required_version_str)
    )
    if pkg_instance.switch_active_python(required_version_str) != 0:
        safe_print(
            _("   - ❌ Failed to swap to Python {}. Please try manually.").format(required_version_str)
        )
        safe_print(_("      Run: {} swap python {}").format(parser_prog, required_version_str))
        return False

    safe_print(
        _("   - ✅ Environment successfully configured for Python {}.").format(required_version_str)
    )
    safe_print(_("🚀 Proceeding..."))
    safe_print("=" * 60)
    return True


def get_version():
    """Get version from package metadata."""
    try:
        from importlib.metadata import version
        return version("omnipkg")
    except Exception:
        try:
            import tomllib
            toml_path = Path(__file__).parent.parent / "pyproject.toml"
            if toml_path.exists():
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                    return data.get("project", {}).get("version", "unknown")
        except (ImportError, Exception):
            pass
    return "unknown"


VERSION = get_version()


def stress_test_command(force=False):
    """Handle stress test command - BLOCK if not Python 3.11."""
    actual_version = get_actual_python_version()
    if actual_version != (3, 11):
        safe_print("=" * 60)
        safe_print(_("  ⚠️  Stress Test Requires Python 3.11"))
        safe_print("=" * 60)
        safe_print(_("Current Python version: {}.{}").format(actual_version[0], actual_version[1]))
        safe_print()
        safe_print(_("The omnipkg stress test only works in Python 3.11 environments."))
        safe_print(_("To run the stress test:"))
        safe_print(_("1. Switch to Python 3.11: omnipkg swap python 3.11"))
        safe_print(_("2. If not available, adopt it first: omnipkg python adopt 3.11"))
        safe_print(_("3. Run 'omnipkg stress-test' from there"))
        safe_print("=" * 60)
        return False
    safe_print("=" * 60)
    safe_print(_("  🚀 omnipkg Nuclear Stress Test - Runtime Version Swapping"))
    safe_print(_("Current Python version: {}.{}").format(actual_version[0], actual_version[1]))
    safe_print("=" * 60)
    safe_print(_("🎪 This demo showcases IMPOSSIBLE package combinations:"))
    safe_print(_("   • Runtime swapping between numpy/scipy versions mid-execution"))
    safe_print(_("   • Different numpy+scipy combos (1.24.3+1.12.0 → 1.26.4+1.16.1)"))
    safe_print(_("   • Previously 'incompatible' versions working together seamlessly"))
    safe_print(_("   • Live PYTHONPATH manipulation without process restart"))
    safe_print(_("   • Space-efficient deduplication (shows deduplication - normally"))
    safe_print(_("     we average ~60% savings, but less for C extensions/binaries)"))
    safe_print()
    safe_print(_("🤯 What makes this impossible with traditional tools:"))
    safe_print(_("   • numpy 1.24.3 + scipy 1.12.0 → 'incompatible dependencies'"))
    safe_print(_("   • Switching versions requires environment restart"))
    safe_print(_("   • Dependency conflicts prevent coexistence"))
    safe_print(_("   • Package managers can't handle multiple versions"))
    safe_print()
    safe_print(_("✨ omnipkg does this LIVE, in the same Python process!"))
    safe_print(_("📊 Expected downloads: ~500MB | Duration: 30 seconds - 3 minutes"))
    from omnipkg.common_utils import safe_input, is_interactive_session

    if force or not is_interactive_session():
        safe_print(_("⚡ Non-interactive mode: Starting immediately..."))
        return True

    response = safe_input(
        _("🚀 Ready to witness the impossible? (y/n): "),
        default="n"
    ).lower()
    return response == "y"


def run_actual_stress_test():
    """Run the actual stress test by locating and executing the test file."""
    safe_print(_("🔥 Starting stress test..."))
    try:
        test_file_path = TESTS_DIR / "test_version_combos.py"
        run_demo_with_live_streaming(test_file_name=str(test_file_path), demo_name="Stress Test")
    except Exception as e:
        safe_print(_("❌ An error occurred during stress test execution: {}").format(e))
        import traceback
        traceback.print_exc()


def run_demo_with_live_streaming(
    test_file_name: str,
    demo_name: str,
    python_exe: str = None,
    isolate_env: bool = False,
):
    """
    Run a demo with live streaming.
    - If given an ABSOLUTE path (like a temp file), it uses it directly.
    - If given a RELATIVE name (like a test file), it dynamically locates it.
    - It ALWAYS dynamically determines the correct project root for PYTHONPATH.
    """
    process = None
    try:
        cm = ConfigManager(suppress_init_messages=True)
        if python_exe:
            effective_python_exe = python_exe
        else:
            effective_python_exe = cm.config.get("python_executable")
            if not effective_python_exe:
                safe_print(
                    "⚠️  Warning: Could not find configured Python. Falling back to the host interpreter."
                )
                effective_python_exe = sys.executable

        cmd = [
            effective_python_exe,
            "-c",
            "import omnipkg; from pathlib import Path; print(Path(omnipkg.__file__).resolve().parent.parent)",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")
        project_root_in_context = Path(result.stdout.strip())

        input_path = Path(test_file_name)
        if input_path.is_absolute():
            test_file_path = input_path
        else:
            test_file_path = project_root_in_context / "tests" / input_path.name

        safe_print(
            _("🚀 Running {} demo from source: {}...").format(
                demo_name.capitalize(), test_file_path
            )
        )

        if not test_file_path.exists():
            safe_print(_("❌ CRITICAL ERROR: Test file not found at: {}").format(test_file_path))
            safe_print(
                _(" (This can happen if omnipkg is not installed in the target Python environment.)")
            )
            return 1

        safe_print(_("📡 Live streaming output..."))
        safe_print("-" * 60)
        safe_print(_('(Executing with: {})').format(effective_python_exe))

        env = os.environ.copy()
        if isolate_env:
            env["PYTHONPATH"] = str(project_root_in_context)
            safe_print(" - Running in ISOLATED environment mode.")
        else:
            current_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(project_root_in_context) + os.pathsep + current_pythonpath
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            [effective_python_exe, "-u", str(test_file_path)],
            text=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
        )

        while True:
            output = process.stdout.read(1)
            if output == "" and process.poll() is not None:
                break
            if output:
                safe_print(output, end="", flush=True)

        returncode = process.wait()
        safe_print("-" * 60)
        if returncode == 0:
            safe_print(_("🎉 Demo completed successfully!"))
        else:
            safe_print(_("❌ Demo failed with return code {}").format(returncode))
        return returncode

    except (Exception, subprocess.CalledProcessError) as e:
        safe_print(_("❌ Demo failed with a critical error: {}").format(e))
        if isinstance(e, subprocess.CalledProcessError):
            safe_print("--- Stderr ---")
            safe_print(e.stderr)
        import traceback
        traceback.print_exc()
        return 1


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE CONFIG WIZARD
# ─────────────────────────────────────────────────────────────────────────────

def _print_language_table():
    """Print SUPPORTED_LANGUAGES as a compact multi-column table."""
    items = sorted(SUPPORTED_LANGUAGES.items())  # [(code, name), ...]
    col_width = 22  # "  ab  Full Name       "
    cols = 4
    safe_print(_("\n  Available languages:"))
    safe_print("  " + "─" * (col_width * cols))
    for i in range(0, len(items), cols):
        row = items[i:i + cols]
        line = ""
        for code, name in row:
            entry = f"  {code:<4} {name:<14}"
            line += entry
        safe_print(line)
    safe_print("  " + "─" * (col_width * cols))


def _print_strategy_table():
    """Print install strategy options with descriptions."""
    safe_print(_("\n  Available install strategies:"))
    safe_print("  " + "─" * 60)
    safe_print(_("  1  stable-main     Prefer stable, well-tested releases (default)"))
    safe_print(_("  2  latest-active   Use the very latest version even if pre-release"))
    safe_print("  " + "─" * 60)


def run_config_wizard(cm: ConfigManager, parser_prog: str) -> int:
    """
    Interactive config editor. Shows current config, lets user pick what to change,
    then guides them through each option with tables + numbered picks.
    Falls back gracefully to non-interactive (just prints config).
    """
    from omnipkg.common_utils import is_interactive_session, safe_input

    # Always print current config
    print_header(_("omnipkg Configuration"))
    current = cm.config
    safe_print(_("  python_executable  : {}").format(current.get("python_executable", "auto")))
    safe_print(_("  install_strategy   : {}").format(current.get("install_strategy", "stable-main")))
    lang_code = current.get("language", "en")
    lang_name = SUPPORTED_LANGUAGES.get(lang_code, lang_code)
    safe_print(_("  language           : {} ({})").format(lang_code, lang_name))
    safe_print(_("  redis_enabled      : {}").format(current.get("redis_enabled", True)))
    safe_print(_("  venv_root          : {}").format(current.get("venv_root", "auto")))
    safe_print()
    safe_print(_("  💡 Quick edit:"))
    safe_print(_("     {} config set install_strategy <value>").format(parser_prog))
    safe_print(_("     {} config set language <code>").format(parser_prog))

    if not is_interactive_session():
        return 0

    safe_print(_("\n  ─── What would you like to change? ───"))
    safe_print(_("  1  install_strategy"))
    safe_print(_("  2  language"))
    safe_print(_("  q  quit (no changes)"))

    choice = safe_input(_("\n  Enter choice [1/2/q]: "), default="q").strip().lower()

    if choice == "1":
        _print_strategy_table()
        val = safe_input(_("  Enter number or name [1/2/stable-main/latest-active]: "), default="").strip()
        mapping = {"1": "stable-main", "2": "latest-active",
                   "stable-main": "stable-main", "latest-active": "latest-active"}
        strategy = mapping.get(val)
        if not strategy:
            safe_print(_("❌ Invalid choice. No changes made."))
            return 1
        cm.set("install_strategy", strategy)
        safe_print(_("✅ install_strategy set to: {}").format(strategy))

    elif choice == "2":
        _print_language_table()
        val = safe_input(_("  Enter language code (e.g. en, es, de): "), default="").strip().lower()
        if not val:
            safe_print(_("❌ No input. No changes made."))
            return 1
        if val not in SUPPORTED_LANGUAGES:
            safe_print(_("❌ Unknown language code '{}'. Available codes shown above.").format(val))
            safe_print(_("   Example: {} config set language es").format(parser_prog))
            return 1
        cm.set("language", val)
        _.set_language(val)
        os.environ["OMNIPKG_LANG"] = val
        safe_print(_("✅ Language set to: {} ({})").format(val, SUPPORTED_LANGUAGES[val]))

    elif choice in ("q", ""):
        safe_print(_("  No changes made."))
    else:
        safe_print(_("❌ Invalid choice '{}'. No changes made.").format(choice))
        return 1

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# PARSER CREATION
# ─────────────────────────────────────────────────────────────────────────────

def create_8pkg_parser():
    """Creates parser for the 8pkg alias (same as omnipkg but with different prog name)."""
    parser = create_parser()
    parser.prog = "8pkg"
    parser.description = _(
        "🚀 The intelligent Python package manager that eliminates dependency hell (8pkg = ∞pkg)"
    )
    epilog_parts = parser.epilog.split("\n")
    updated_epilog = "\n".join([line.replace("omnipkg", "8pkg") for line in epilog_parts])
    parser.epilog = updated_epilog
    return parser


def create_parser():
    """Creates and configures the argument parser."""
    epilog_parts = [
        _("🔥 Key Features:"),
        _("  • Runtime version switching without environment restart"),
        _("  • Automatic conflict resolution with intelligent bubbling"),
        _("  • Multi-version package coexistence"),
        "",
        _("💡 Quick Start:"),
        _("  omnipkg install <package>        # Smart install with conflict resolution"),
        _("  omnipkg install -r requirements.txt"),
        _("  omnipkg list                     # View installed packages and status"),
        _("  omnipkg info <package>           # Interactive package explorer"),
        _("  omnipkg demo                     # Try version-switching demos"),
        _("  omnipkg stress-test              # See the magic in action"),
        "",
        _("🐍 Python Version Management:"),
        _("  omnipkg python adopt 3.11        # Add Python 3.11 to managed pool"),
        _("  omnipkg python reinstall 3.9 -y  # Clean reinstall of Python 3.9"),
        _("  omnipkg python switch 3.11       # Switch active Python (same as swap python)"),
        _("  omnipkg swap python 3.11         # Switch active Python (spawns new shell)"),
        _("  omnipkg list python              # List managed + discoverable interpreters"),
        _("  8pkg311 install requests         # Run under Python 3.11 specifically"),
        _("  8pkg39 install pandas            # Run under Python 3.9 specifically"),
        "",
        _("⚙️  Config & Strategy:"),
        _("  omnipkg config                   # View config + interactive editor"),
        _("  omnipkg config set install_strategy latest-active"),
        _("  omnipkg config set language es"),
        "",
        _("🌐 Web Bridge:"),
        _("  omnipkg web start                # Start the local dashboard bridge"),
        _("  omnipkg web restart              # Restart the bridge"),
        _("  omnipkg web fix-permission       # Fix Chrome local network permission"),
        "",
        _("🛠️ Examples:"),
        _("  omnipkg install requests numpy>=1.20"),
        _("  omnipkg install uv==0.7.13 uv==0.7.14  # Multiple versions!"),
        _("  omnipkg info tensorflow==2.13.0"),
        "",
        _("Version: {}").format(VERSION),
    ]
    translated_epilog = "\n".join(epilog_parts)
    parser = argparse.ArgumentParser(
        prog="omnipkg",
        description=_("🚀 The intelligent Python package manager that eliminates dependency hell"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=translated_epilog,
    )
    parser.add_argument(
        "-v", "--version", action="version", version=_("%(prog)s {}").format(VERSION)
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        help=_("Override the display language for this command (e.g., es, de, ja)"),
    )
    parser.add_argument(
        "--python",
        metavar="VERSION",
        help=_(
            "Specify which Python version to use for this command (e.g. 3.10, 3.11).\n"
            "Also usable as a versioned alias: 8pkg311 install foo"
        ),
    )
    parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        help=_("Enable verbose output for detailed debugging"),
    )
    subparsers = parser.add_subparsers(
        dest="command", help=_("Available commands:"), required=False
    )

    # ── install ───────────────────────────────────────────────────────────────
    install_parser = subparsers.add_parser(
        "install",
        help=_("Install packages with intelligent conflict resolution"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Examples:\n"
            "  omnipkg install requests\n"
            "  omnipkg install numpy>=1.20 scipy\n"
            "  omnipkg install uv==0.7.13 uv==0.7.14   # coexisting versions!\n"
            "  omnipkg install python==3.11             # adopt a Python interpreter\n"
            "  omnipkg install -r requirements.txt\n"
            "  omnipkg install --force requests         # force reinstall\n"
        ),
    )
    install_parser.add_argument(
        "packages",
        nargs="*",
        help=_('Packages to install (e.g., "requests==2.25.1", "numpy>=1.20")'),
    )
    install_parser.add_argument(
        "-r", "--requirement", help=_("Install from requirements file"), metavar="FILE"
    )
    install_parser.add_argument(
        "--force", "--force-reinstall",
        dest="force_reinstall",
        action="store_true",
        help=_("Force reinstall even if already satisfied"),
    )
    install_parser.add_argument(
        "-y", "--yes",
        dest="yes",
        action="store_true",
        help=_("Skip all confirmation prompts (non-interactive / CI mode)"),
    )
    install_parser.add_argument("--index-url", help=_("Base URL of the Python Package Index"))
    install_parser.add_argument("--extra-index-url", help=_("Extra URLs of package indexes to use"))

    # ── install-with-deps ────────────────────────────────────────────────────
    install_with_deps_parser = subparsers.add_parser(
        "install-with-deps",
        help=_("Install a package with pinned dependency versions"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Use this when you need exact reproducibility with a specific dep graph.\n\n"
            "Example:\n"
            "  omnipkg install-with-deps tensorflow==2.13.0 \\\n"
            "      --dependency numpy==1.24.3 \\\n"
            "      --dependency protobuf==3.20.3\n"
        ),
    )
    install_with_deps_parser.add_argument(
        "package", help=_('Package to install (e.g., "tensorflow==2.13.0")')
    )
    install_with_deps_parser.add_argument(
        "--dependency", "-d",
        action="append",
        help=_('Pinned dependency (e.g., "numpy==1.24.3"). Repeat for multiple.'),
        default=[],
    )

    # ── uninstall ─────────────────────────────────────────────────────────────
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help=_("Intelligently remove packages and their dependencies"),
    )
    uninstall_parser.add_argument("packages", nargs="+", help=_("Packages to uninstall"))
    uninstall_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=_("Skip confirmation prompts"),
    )

    # ── info ──────────────────────────────────────────────────────────────────
    info_parser = subparsers.add_parser(
        "info",
        help=_("Interactive package explorer with version management"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Examples:\n"
            "  omnipkg info requests\n"
            "  omnipkg info requests 1        # Automatically select the first installation\n"
            "  omnipkg info requests 1 -y     # Select first and auto-expand raw data\n"
            "  omnipkg info python            # Show managed Python interpreters\n"
        ),
    )
    info_parser.add_argument(
        "package_spec",
        help=_('Package to inspect (e.g., "requests" or "requests==2.28.1" or "python")'),
    )
    # 👇 Add these two arguments 👇
    info_parser.add_argument(
        "selection",
        nargs="?",
        type=int,
        help=_("Optional: Directly select an installation index to skip the prompt (e.g., 1)"),
    )
    info_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=_("Skip confirmation prompts and auto-expand raw data"),
    )

    # ── revert ────────────────────────────────────────────────────────────────
    revert_parser = subparsers.add_parser(
        "revert",
        help=_("Revert to last known good environment snapshot"),
    )
    revert_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help=_("Skip confirmation prompt"),
    )

    # ── swap ──────────────────────────────────────────────────────────────────
    swap_parser = subparsers.add_parser(
        "swap",
        help=_("Swap Python version or active package environment"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Swapping Python versions:\n"
            "  omnipkg swap python 3.11    # Spawns a new shell running Python 3.11\n"
            "                              # In CI (non-interactive): updates config only\n"
            "  omnipkg swap python         # Interactive picker if no version given\n\n"
            "Swapping package versions:\n"
            "  omnipkg swap numpy==1.26.4  # Switch numpy to a specific version\n\n"
            "Alias: 'python switch <ver>' does the same thing as 'swap python <ver>'\n"
        ),
    )
    swap_parser.add_argument(
        "target", nargs="?",
        help=_('What to swap: "python" or a package spec like "numpy==1.26.4"'),
    )
    swap_parser.add_argument("version", nargs="?", help=_("Specific version to swap to"))

    # ── list ──────────────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser(
        "list",
        help=_("View all installed packages and their status"),
    )
    list_parser.add_argument(
        "filter", nargs="?",
        help=_('Filter packages by name pattern, or "python" to list interpreters'),
    )

    # ── python ────────────────────────────────────────────────────────────────
    python_parser = subparsers.add_parser(
        "python",
        help=_("Manage Python interpreters"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Subcommands:\n"
            "  adopt 3.11          Copy or download Python 3.11 into the managed pool\n"
            "  adopt 3.9 --force   Re-adopt even if already managed (overwrites)\n"
            "  switch 3.11         Switch active Python (same as 'swap python 3.11')\n"
            "  reinstall 3.9       Remove + re-adopt Python 3.9 (clean reinstall)\n"
            "  remove 3.8          Remove a managed interpreter\n"
            "  rescan              Re-scan and repair the interpreter registry\n"
        ),
    )
    python_subparsers = python_parser.add_subparsers(
        dest="python_command", help=_("Available subcommands:"), required=True
    )

    python_adopt_parser = python_subparsers.add_parser(
        "adopt",
        help=_("Copy or download a Python version into the managed pool"),
    )
    python_adopt_parser.add_argument("version", help=_('The version to adopt (e.g., "3.9")'))
    python_adopt_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help=_("Force re-adoption even if already managed (overwrites existing)"),
    )
    python_adopt_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=_("Skip confirmation prompts"),
    )

    python_switch_parser = python_subparsers.add_parser(
        "switch",
        help=_("Switch the active Python interpreter (same as 'swap python <version>')"),
    )
    python_switch_parser.add_argument("version", help=_('The version to switch to (e.g., "3.10")'))

    python_reinstall_parser = python_subparsers.add_parser(
        "reinstall",
        help=_("Remove and re-adopt a Python version (clean reinstall)"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Shortcut for: uninstall python==X.Y && install python==X.Y\n\n"
            "Example:\n"
            "  omnipkg python reinstall 3.9\n"
            "  omnipkg python reinstall 3.9 -y   # no prompts\n"
        ),
    )
    python_reinstall_parser.add_argument("version", help=_('The version to reinstall (e.g., "3.9")'))
    python_reinstall_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=_("Skip confirmation prompts"),
    )

    python_subparsers.add_parser(
        "rescan",
        help=_("Force a re-scan and repair of the interpreter registry"),
    )

    remove_parser = python_subparsers.add_parser(
        "remove",
        help=_("Forcefully remove a managed Python interpreter"),
    )
    remove_parser.add_argument(
        "version",
        help=_('The version to remove (e.g., "3.9")'),
    )
    remove_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=_("Do not ask for confirmation"),
    )

    # ── status ────────────────────────────────────────────────────────────────
    subparsers.add_parser("status", help=_("Environment health dashboard"))

    # ── demo ──────────────────────────────────────────────────────────────────
    demo_parser = subparsers.add_parser(
        "demo",
        help=_("Interactive demo for version switching"),
    )
    demo_parser.add_argument(
        "demo_id",
        nargs="?",
        type=int,
        help=_("Run a specific demo by number (1-11) to skip interactive menu"),
    )
    demo_parser.add_argument(
        "--non-interactive", "-y",
        action="store_true",
        help=_("Run in non-interactive mode (auto-selects defaults, no prompts)"),
    )

    # ── stress-test ───────────────────────────────────────────────────────────
    stress_parser = subparsers.add_parser(
        "stress-test",
        help=_("Run chaos theory stress tests"),
    )
    stress_parser.add_argument(
        "tests",
        nargs="*",
        help=_("Specific test numbers to run (e.g., '11 17 18'). Leave empty for interactive menu."),
    )
    stress_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help=_("Skip confirmation and run all tests (equivalent to test 0)"),
    )

    # ── reset ─────────────────────────────────────────────────────────────────
    reset_parser = subparsers.add_parser("reset", help=_("Rebuild the omnipkg knowledge base"))
    reset_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=_("Skip confirmation")
    )

    # ── rebuild-kb ────────────────────────────────────────────────────────────
    rebuild_parser = subparsers.add_parser(
        "rebuild-kb", help=_("Refresh the intelligence knowledge base")
    )
    rebuild_parser.add_argument(
        "--force", "-f", action="store_true", help=_("Force complete rebuild")
    )

    # ── reset-config ──────────────────────────────────────────────────────────
    reset_config_parser = subparsers.add_parser(
        "reset-config", help=_("Delete config file for fresh setup")
    )
    reset_config_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=_("Skip confirmation")
    )

    # ── config ────────────────────────────────────────────────────────────────
    config_parser = subparsers.add_parser(
        "config",
        help=_("View or edit omnipkg configuration"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "With no subcommand, prints current config and opens interactive editor.\n\n"
            "Examples:\n"
            "  omnipkg config                             # view + interactive edit\n"
            "  omnipkg config set install_strategy latest-active\n"
            "  omnipkg config set language es\n"
            "  omnipkg config view                        # print only, no editor\n\n"
            "install_strategy options:\n"
            "  stable-main    Prefer stable, well-tested releases (default)\n"
            "  latest-active  Use the very latest version even if pre-release\n\n"
            "language options (use 2-letter code):\n"
            "  en  English     es  Spanish     de  German      fr  French\n"
            "  ja  Japanese    zh  Chinese     pt  Portuguese  ko  Korean\n"
            "  ru  Russian     ar  Arabic     (run 'omnipkg config' for full list)\n"
        ),
    )
    # Make subcommand optional — bare 'config' triggers wizard
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=False)
    config_subparsers.add_parser(
        "view", help=_("Display the current configuration (no interactive editor)")
    )
    config_set_parser = config_subparsers.add_parser(
        "set",
        help=_("Set a configuration value"),
    )
    config_set_parser.add_argument(
        "key",
        choices=["language", "install_strategy"],
        help=_("Configuration key to set"),
    )
    config_set_parser.add_argument("value", help=_("Value to set for the key"))
    config_reset_parser = config_subparsers.add_parser(
        "reset", help=_("Reset a specific configuration key to its default")
    )
    config_reset_parser.add_argument(
        "key",
        choices=["interpreters"],
        help=_("Configuration key to reset (e.g., interpreters)"),
    )

    # ── doctor ────────────────────────────────────────────────────────────────
    doctor_parser = subparsers.add_parser(
        "doctor",
        help=_("Diagnose and repair a corrupted environment"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "🩺  Finds and removes orphaned package metadata ('ghosts') left behind\n"
            "   by failed or interrupted installations from other package managers.\n\n"
            "Use --dry-run first to see what would be changed, then run without it.\n"
        ),
    )
    doctor_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=_("Diagnose only — show the healing plan without making any changes"),
    )
    doctor_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=_("Automatically confirm and proceed with healing without prompting"),
    )

    # ── heal ──────────────────────────────────────────────────────────────────
    heal_parser = subparsers.add_parser(
        "heal",
        help=_("Audit for dependency conflicts and attempt to repair them"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "❤️‍🩹  Automatically resolves version conflicts and installs missing packages\n"
            "   required by your currently installed packages.\n\n"
            "Tip: run with --dry-run first to preview changes.\n"
        ),
    )
    heal_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=_("Show what would be installed/reinstalled without making changes"),
    )
    heal_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=_("Automatically proceed with healing without prompting"),
    )

    # ── run ───────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help=_("Run a script with auto-healing for version conflicts"),
    )
    run_parser.add_argument(
        "script_and_args",
        nargs=argparse.REMAINDER,
        help=_("The script to run, followed by its arguments"),
    )

    # ── daemon ────────────────────────────────────────────────────────────────
    daemon_parser = subparsers.add_parser(
        "daemon",
        help=_("Manage the persistent worker daemon"),
    )
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_subparsers.add_parser("start", help=_("Start the background daemon"))
    daemon_subparsers.add_parser("stop", help=_("Stop the daemon"))
    daemon_subparsers.add_parser("restart", help=_("Restart the daemon (stop then start)"))
    daemon_subparsers.add_parser("status", help=_("Check daemon status and memory usage"))

    idle_parser = daemon_subparsers.add_parser(
        "idle",
        help=_("Configure idle worker pools"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Controls how many warm Python workers omnipkg keeps ready in background.\n\n"
            "Examples:\n"
            "  omnipkg daemon idle --python 3.11 --count 2\n"
            "  omnipkg daemon idle --python all    # show all configs\n"
        ),
    )
    # Accept both --python-version and --python (no argparse conflict at subcommand level)
    idle_parser.add_argument(
        "--python-version", "--python",
        type=str,
        dest="idle_python",
        metavar="VERSION",
        help=_('Python version (e.g., 3.11, 3.12) or "all" to show all configs'),
    )
    idle_parser.add_argument(
        "--count",
        type=int,
        help=_("Number of idle workers to keep ready (0 to disable)"),
    )

    daemon_logs = daemon_subparsers.add_parser("logs", help=_("View or follow daemon logs"))
    daemon_logs.add_argument(
        "-f", "--follow",
        action="store_true",
        help=_("Output appended data as the file grows"),
    )
    daemon_logs.add_argument(
        "-n", "--lines",
        type=int, default=50,
        help=_("Output the last N lines (default: 50)"),
    )

    daemon_monitor = daemon_subparsers.add_parser(
        "monitor", help=_("Live resource usage dashboard (TUI)")
    )
    daemon_monitor.add_argument(
        "-w", "--watch",
        action="store_true",
        help=_("Auto-refresh mode (dashboard style)"),
    )

    # ── web ───────────────────────────────────────────────────────────────────
    web_parser = subparsers.add_parser(
        "web",
        help=_("Manage the local web bridge"),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "The web bridge connects the omnipkg dashboard UI to your local machine.\n\n"
            "Commands:\n"
            "  start           Start the bridge (opens browser, shows permission prompt)\n"
            "  stop            Stop the bridge\n"
            "  restart         Stop then start\n"
            "  status          Show PID, port, memory, uptime\n"
            "  logs            Tail the bridge log file\n"
            "  fix-permission  Chrome blocked 'local network access'? Run this.\n"
        ),
    )
    web_subparsers = web_parser.add_subparsers(dest="web_command", required=True)
    web_subparsers.add_parser("start", help=_("Start the web bridge in background"))
    web_subparsers.add_parser("stop", help=_("Stop the web bridge"))
    web_subparsers.add_parser("status", help=_("Check web bridge status"))
    web_subparsers.add_parser("restart", help=_("Restart the web bridge"))
    web_subparsers.add_parser(
        "fix-permission",
        help=_(
            "Guide to resolve Chrome 'Allow access to local network resources?' block.\n"
            "Run this if you accidentally clicked 'Block' on the Chrome permission prompt."
        ),
    )

    web_logs = web_subparsers.add_parser("logs", help=_("View web bridge logs"))
    web_logs.add_argument(
        "-f", "--follow",
        action="store_true",
        help=_("Follow log output in real-time"),
    )
    web_logs.add_argument(
        "-n", "--lines",
        type=int, default=50,
        help=_("Number of lines to show (default: 50)"),
    )

    # ── prune ─────────────────────────────────────────────────────────────────
    prune_parser = subparsers.add_parser(
        "prune",
        help=_("Clean up old, bubbled package versions"),
    )
    prune_parser.add_argument("package", help=_("Package whose bubbles to prune"))
    prune_parser.add_argument(
        "--keep-latest",
        type=int,
        metavar="N",
        help=_("Keep N most recent bubbled versions"),
    )
    prune_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=_("Skip confirmation")
    )

    # ── upgrade ───────────────────────────────────────────────────────────────
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help=_("Upgrade omnipkg or other packages to the latest version"),
    )
    upgrade_parser.add_argument(
        "package_name",
        nargs="*",
        default=["omnipkg"],
        help=_("Package to upgrade (defaults to omnipkg itself)"),
    )
    upgrade_parser.add_argument(
        "--version",
        help=_("(For omnipkg self-upgrade only) Specify a target version"),
    )
    upgrade_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=_("Skip confirmation prompt"),
    )
    upgrade_parser.add_argument(
        "--force-dev",
        action="store_true",
        help=_("Force upgrade even in a developer environment (use with caution)"),
    )
    upgrade_parser.set_defaults(func=upgrade)

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Main application entry point with pre-flight version check."""
    # ── Windows console fix (must be FIRST) ───────────────────────────────────
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
                sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
            if hasattr(sys.stdin, 'reconfigure'):
                sys.stdin.reconfigure(encoding='utf-8')
            os.environ['PYTHONIOENCODING'] = 'utf-8'
            os.environ['PYTHONUNBUFFERED'] = '1'
        except Exception:
            pass

    try:
        # ── Detect version-specific command (8pkg310, 8pkg311, etc.) ──────────
        prog_name = Path(sys.argv[0]).name.lower()
        version_match = re.match(r"8pkg(\d)(\d+)", prog_name)
        if version_match:
            major = version_match.group(1)
            minor = version_match.group(2)
            forced_version = f"{major}.{minor}"
            if "--python" not in sys.argv:
                sys.argv.insert(1, "--python")
                sys.argv.insert(2, forced_version)
                _dbg(f"Detected {prog_name} → injected --python {forced_version}")

        # ── Normalize flags to lowercase (but not package names) ───────────
        normalized_argv = [sys.argv[0]]
        for arg in sys.argv[1:]:
            normalized_argv.append(arg.lower() if arg.startswith("-") else arg)
        sys.argv = normalized_argv

        # ── Pre-parse for global flags ─────────────────────────────────────
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument("--lang", default=None)
        global_parser.add_argument("--verbose", "-V", action="store_true")
        global_args, remaining_args = global_parser.parse_known_args()

        if remaining_args and not remaining_args[0].startswith("-"):
            remaining_args[0] = remaining_args[0].lower()

        command = (
            remaining_args[0] if remaining_args and not remaining_args[0].startswith("-") else None
        )

        if ("-v" in remaining_args or "--version" in remaining_args) and command != "run":
            safe_print(_("omnipkg {}").format(get_version()))
            return 0

        os.environ["OMNIPKG_LANG"] = os.environ.get("OMNIPKG_LANG", "")

        cm = ConfigManager()
        user_lang = global_args.lang or cm.config.get("language") or os.environ.get("OMNIPKG_LANG")

        if user_lang:
            import importlib
            from omnipkg import i18n
            importlib.reload(i18n)
            _.set_language(user_lang)
            os.environ["OMNIPKG_LANG"] = user_lang

        # ── Choose minimal vs full init ────────────────────────────────────
        use_minimal = False
        if command in {"config", "python"}:
            use_minimal = True
        elif command == "swap":
            if len(remaining_args) > 1 and remaining_args[1].lower() == "python":
                use_minimal = True

        pkg_instance = OmnipkgCore(config_manager=cm, minimal_mode=use_minimal)

        # ── Build parser ───────────────────────────────────────────────────
        prog_name_lower = Path(sys.argv[0]).name.lower()
        if prog_name_lower == "8pkg" or "8pkg" in sys.argv[0].lower():
            parser = create_8pkg_parser()
        else:
            parser = create_parser()

        args = parser.parse_args(remaining_args)

        # ── Determine interactive mode using the shared helper ─────────────
        from omnipkg.common_utils import is_interactive_session
        is_interactive = is_interactive_session()
        _dbg(f"is_interactive={is_interactive}  command={args.command}")

        args.verbose = global_args.verbose
        args.lang = global_args.lang

        # ── Handle --python pre-flight: adopt + relaunch if needed ────────
        # This covers both the explicit --python flag and 8pkg39-style aliases.
        python_flag = getattr(args, "python", None)
        if python_flag:
            _dbg(f"--python {python_flag} requested")
            managed = pkg_instance.interpreter_manager.list_available_interpreters()
            if python_flag not in managed:
                safe_print(_("⚠️  Python {} is not yet in the managed pool.").format(python_flag))
                should_adopt = True
                if is_interactive:
                    from omnipkg.common_utils import safe_input
                    ans = safe_input(
                        _("  Adopt Python {} now and rerun your command? (Y/n): ").format(python_flag),
                        default="y",
                    )
                    should_adopt = ans.lower() in ("", "y", "yes")
                else:
                    safe_print(_("🤖 Non-interactive: auto-adopting Python {}...").format(python_flag))

                if not should_adopt:
                    safe_print(
                        _("❌ Cancelled. Adopt first: {} python adopt {}").format(parser.prog, python_flag)
                    )
                    return 1

                result = pkg_instance.adopt_interpreter(python_flag)
                if result != 0:
                    safe_print(_("❌ Failed to adopt Python {}.").format(python_flag))
                    return 1
                safe_print(_("✅ Python {} adopted. Rerunning command...").format(python_flag))
                from omnipkg.common_utils import ensure_python_or_relaunch
                ensure_python_or_relaunch(python_flag)
                # ensure_python_or_relaunch does os.execve, so we only reach here on failure
                return 1

        # ── No command: show help ──────────────────────────────────────────
        if args.command is None:
            parser.print_help()
            safe_print(_("\n👋 Welcome back to omnipkg! Run a command or see --help for details."))
            return 0

        # ══════════════════════════════════════════════════════════════════
        # COMMAND DISPATCH
        # ══════════════════════════════════════════════════════════════════

        if args.command == "config":
            # No subcommand → interactive wizard (or plain view in CI)
            if not args.config_command:
                return run_config_wizard(cm, parser.prog)

            elif args.config_command == "view":
                print_header("omnipkg Configuration")
                for key, value in sorted(cm.config.items()):
                    safe_print(_("  {:<25} {}").format(key, value))
                safe_print(_("\n💡 Edit with: {} config set <key> <value>").format(parser.prog))
                return 0

            elif args.config_command == "set":
                if args.key == "language":
                    val = args.value.strip().lower()
                    if val not in SUPPORTED_LANGUAGES:
                        safe_print(
                            _("❌ Unknown language '{}'. Run '{} config' to see all options.").format(
                                val, parser.prog
                            )
                        )
                        _print_language_table()
                        return 1
                    cm.set("language", val)
                    _.set_language(val)
                    os.environ["OMNIPKG_LANG"] = val
                    lang_name = SUPPORTED_LANGUAGES.get(val, val)
                    safe_print(_("✅ Language set to: {} ({})").format(val, lang_name))

                elif args.key == "install_strategy":
                    valid_strategies = ["stable-main", "latest-active"]
                    if args.value not in valid_strategies:
                        safe_print(
                            _("❌ Invalid strategy '{}'. Valid options: {}").format(
                                args.value, ", ".join(valid_strategies)
                            )
                        )
                        _print_strategy_table()
                        return 1
                    cm.set("install_strategy", args.value)
                    safe_print(_("✅ install_strategy set to: {}").format(args.value))
                else:
                    parser.print_help()
                    return 1
                return 0

            elif args.config_command == "reset":
                if args.key == "interpreters":
                    safe_print(_("Resetting managed interpreters registry..."))
                    return pkg_instance.rescan_interpreters()
                return 0

            parser.print_help()
            return 1

        elif args.command == "doctor":
            return pkg_instance.doctor(dry_run=args.dry_run, force=args.force)

        elif args.command == "heal":
            with temporary_install_strategy(pkg_instance, "latest-active"):
                return pkg_instance.heal(dry_run=args.dry_run, force=args.force)

        elif args.command == "list":
            if args.filter and args.filter.lower() == "python":
                interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
                discovered = pkg_instance.config_manager.list_available_pythons()
                print_header("Managed Python Interpreters")
                if not interpreters:
                    safe_print(
                        _("   No interpreters are currently managed by omnipkg for this environment.")
                    )
                else:
                    for ver, path in sorted(interpreters.items()):
                        safe_print(_("   • Python {}: {}").format(ver, path))
                print_header("Discovered System Interpreters")
                safe_print(
                    _("   (Use '{}  python adopt <version>' to make these available for swapping)").format(
                        parser.prog
                    )
                )
                for ver, path in sorted(discovered.items()):
                    if ver not in interpreters:
                        safe_print(_("   • Python {}: {}").format(ver, path))
                return 0
            else:
                return pkg_instance.list_packages(args.filter)

        elif args.command == "python":
            if args.python_command == "adopt":
                managed = pkg_instance.interpreter_manager.list_available_interpreters()
                already_managed = args.version in managed
                if already_managed and not args.force:
                    if is_interactive and not getattr(args, "yes", False):
                        from omnipkg.common_utils import safe_input
                        ans = safe_input(
                            _("Python {} is already managed. Re-adopt (overwrite)? (y/N): ").format(
                                args.version
                            ),
                            default="n",
                        )
                        if ans.lower() not in ("y", "yes"):
                            safe_print(_("  Skipped. Use --force to overwrite without prompting."))
                            return 0
                    else:
                        safe_print(
                            _("ℹ️  Python {} already managed. Use --force to overwrite.").format(
                                args.version
                            )
                        )
                        return 0
                return pkg_instance.adopt_interpreter(args.version)

            elif args.python_command == "switch":
                # Delegate to the same logic as `swap python` for full consistency
                version = args.version
                _dbg(f"python switch → same path as swap python {version}")
                if is_interactive:
                    from omnipkg.dispatcher import resolve_python_path, spawn_swap_shell
                    python_path = resolve_python_path(version)
                    if not python_path.exists():
                        safe_print(_("❌ Python {} not found: {}").format(version, python_path))
                        safe_print(_("   Adopt it first: {} python adopt {}").format(parser.prog, version))
                        return 1
                    return spawn_swap_shell(
                        version=version,
                        python_path=python_path,
                        pkg_instance=pkg_instance,
                    )
                else:
                    safe_print(_("🐍 Switching active Python context to {} (CI mode)...").format(version))
                    result = pkg_instance.switch_active_python(version)
                    if result == 0:
                        os.environ["OMNIPKG_PYTHON"] = version
                        os.environ["OMNIPKG_ACTIVE_PYTHON"] = version
                        safe_print(_("✅ Context switched to Python {}").format(version))
                    return result

            elif args.python_command == "reinstall":
                # Combined remove + adopt in one step
                safe_print(_("♻️  Reinstalling Python {}...").format(args.version))
                managed = pkg_instance.interpreter_manager.list_available_interpreters()

                if args.version in managed:
                    if not args.yes and is_interactive:
                        from omnipkg.common_utils import safe_input
                        ans = safe_input(
                            _("Remove and re-adopt Python {}? This cannot be undone. (y/N): ").format(
                                args.version
                            ),
                            default="n",
                        )
                        if ans.lower() not in ("y", "yes"):
                            safe_print(_("❌ Cancelled."))
                            return 1
                    elif not args.yes and not is_interactive:
                        safe_print(
                            _("🤖 Non-interactive: proceeding with reinstall of Python {}...").format(
                                args.version
                            )
                        )

                    safe_print(_("  Step 1/2: Removing Python {}...").format(args.version))
                    result = pkg_instance.remove_interpreter(args.version, force=True)
                    if result != 0:
                        safe_print(_("❌ Failed to remove Python {}.").format(args.version))
                        return result
                    safe_print(_("  ✅ Removed."))
                else:
                    safe_print(
                        _("ℹ️  Python {} not currently managed — performing fresh adopt.").format(
                            args.version
                        )
                    )

                safe_print(_("  Step 2/2: Adopting Python {}...").format(args.version))
                result = pkg_instance.adopt_interpreter(args.version)
                if result == 0:
                    safe_print(_("✅ Python {} reinstalled successfully.").format(args.version))
                return result

            elif args.python_command == "rescan":
                return pkg_instance.rescan_interpreters()

            elif args.python_command == "remove":
                return pkg_instance.remove_interpreter(args.version, force=args.yes)

            else:
                parser.print_help()

        elif args.command == "swap":
            if not args.target:
                safe_print(_("❌ Error: You must specify what to swap."))
                safe_print(_("Examples:"))
                safe_print(_("  {} swap python 3.11").format(parser.prog))
                safe_print(_("  {} swap numpy==1.26.4").format(parser.prog))
                return 1

            if args.target.lower().startswith("python"):
                if "==" in args.target:
                    version = args.target.split("==")[1]
                elif args.version:
                    version = args.version
                else:
                    # Interactive picker
                    interpreters = pkg_instance.config_manager.list_available_pythons()
                    if not interpreters:
                        safe_print(_("❌ No Python interpreters found."))
                        return 1
                    safe_print(_("🐍 Available Python versions:"))
                    versions = sorted(interpreters.keys())
                    for i, ver in enumerate(versions, 1):
                        safe_print(_("  {}. Python {}").format(i, ver))
                    from omnipkg.common_utils import safe_input
                    choice = safe_input(
                        _("Select version (1-{}): ").format(len(versions)),
                        default="1",
                        auto_value=os.environ.get("OMNIPKG_PYTHON_CHOICE", "1"),
                    )
                    if choice.isdigit() and 1 <= int(choice) <= len(versions):
                        version = versions[int(choice) - 1]
                    else:
                        safe_print(_("❌ Invalid selection."))
                        return 1

                from omnipkg.dispatcher import resolve_python_path
                python_path = resolve_python_path(version)

                if not python_path.exists():
                    # Auto-adopt then retry
                    safe_print(_("⚠️  Python {} not found in managed pool.").format(version))
                    should_adopt = True
                    if is_interactive:
                        from omnipkg.common_utils import safe_input
                        ans = safe_input(
                            _("Adopt Python {} now? (Y/n): ").format(version),
                            default="y",
                        )
                        should_adopt = ans.lower() in ("", "y", "yes")
                    else:
                        safe_print(_("🤖 Auto-adopting Python {}...").format(version))

                    if not should_adopt:
                        safe_print(_("❌ Install it first: {} python adopt {}").format(parser.prog, version))
                        return 1

                    result = pkg_instance.adopt_interpreter(version)
                    if result != 0:
                        safe_print(_("❌ Failed to adopt Python {}.").format(version))
                        return 1

                    python_path = resolve_python_path(version)
                    if not python_path.exists():
                        safe_print(_("❌ Still can't find Python {} after adoption.").format(version))
                        return 1

                _dbg(f"swap python: is_interactive={is_interactive} version={version} path={python_path}")

                if is_interactive:
                    from omnipkg.dispatcher import spawn_swap_shell
                    return spawn_swap_shell(
                        version=version,
                        python_path=python_path,
                        pkg_instance=pkg_instance,
                    )
                else:
                    safe_print(_("🐍 Switching active Python context to {} (CI mode)...").format(version))
                    result = pkg_instance.switch_active_python(version)
                    if result == 0:
                        os.environ["OMNIPKG_PYTHON"] = version
                        os.environ["OMNIPKG_ACTIVE_PYTHON"] = version
                        safe_print(_("✅ Context switched to Python {}").format(version))
                        safe_print(_("💡 Env vars set for current process"))
                    return result

            else:
                package_spec = args.target
                if args.version:
                    package_spec = f"{package_spec}=={args.version}"
                safe_print(_("🔄 Swapping main environment package to '{}'...").format(package_spec))
                with temporary_install_strategy(pkg_instance, "latest-active"):
                    return pkg_instance.smart_install(packages=[package_spec])

        elif args.command == "upgrade":
            return upgrade(args, pkg_instance)

        elif args.command == "status":
            return pkg_instance.show_multiversion_status()

        elif args.command == "demo":
            original_python_tuple = get_actual_python_version()
            original_python_str = f"{original_python_tuple[0]}.{original_python_tuple[1]}"

            try:
                safe_print(
                    _("Current Python version: {}.{}").format(
                        original_python_tuple[0], original_python_tuple[1]
                    )
                )
                safe_print(_("🎪 Omnipkg supports version switching for:"))
                safe_print(_("   • Python modules (e.g., rich)"))
                safe_print(_("   • Binary packages (e.g., uv)"))
                safe_print(_("   • C-extension packages (e.g., numpy, scipy)"))
                safe_print(_("   • Complex dependency packages (e.g., TensorFlow)"))
                safe_print(_("\nSelect a demo to run:"))
                safe_print(_("1. Rich test (Python module switching)"))
                safe_print(_("2. UV test (binary switching)"))
                safe_print(_("3. NumPy + SciPy stress test (C-extension switching)"))
                safe_print(_("4. TensorFlow test (complex dependency switching)"))
                safe_print(_("5. 🚀 Multiverse Healing Test (Cross-Python Hot-Swapping Mid-Script)"))
                safe_print(_("6. Old Flask Test (legacy package healing) - Fully functional!"))
                safe_print(_("7. Script-healing Test (omnipkg run scripts)"))
                safe_print(_("8. 🌠 Quantum Multiverse Warp (Concurrent Python Installations)"))
                safe_print(_("9. Flask Port Finder Test (auto-healing with Flask)"))
                safe_print(_("10. CLI Healing Test (omnipkg run shell commands)"))
                safe_print(_("11. 🌀 Chaos Theory Stress Test (Loader torture test)"))

                from omnipkg.common_utils import safe_input

                # non-interactive / --non-interactive flag both bypass the prompt
                non_interactive = getattr(args, "non_interactive", False) or not is_interactive_session()

                if hasattr(args, 'demo_id') and args.demo_id:
                    response = str(args.demo_id)
                    safe_print(_('🎯 Running demo {}...').format(response))
                elif non_interactive:
                    response = os.environ.get("OMNIPKG_DEMO_ID", "1")
                    safe_print(_('🤖 Non-interactive: auto-selecting demo {}').format(response))
                else:
                    response = safe_input(
                        _("Enter your choice (1-11): "),
                        default="1",
                        auto_value=os.environ.get("OMNIPKG_DEMO_ID", "1"),
                    )

                demo_map = {
                    "1": ("Rich Test", TESTS_DIR / "test_rich_switching.py", None),
                    "2": ("UV Test", TESTS_DIR / "test_uv_switching.py", None),
                    "3": ("NumPy/SciPy Test", TESTS_DIR / "test_version_combos.py", "3.11"),
                    "4": ("TensorFlow Test", TESTS_DIR / "test_tensorflow_switching.py", "3.11"),
                    "5": ("Multiverse Healing", TESTS_DIR / "test_multiverse_healing.py", "3.11"),
                    "6": ("Old Flask Test", TESTS_DIR / "test_old_flask.py", "3.8"),
                    "7": ("Auto-healing Test", TESTS_DIR / "test_old_rich.py", None),
                    "8": ("Quantum Multiverse Warp", TESTS_DIR / "test_concurrent_install.py", "3.11"),
                    "9": ("Flask Port Finder", TESTS_DIR / "test_flask_port_finder.py", None),
                    "10": ("CLI Healing Test", TESTS_DIR / "test_cli_healing.py", None),
                    "11": ("Chaos Theory Stress Test", TESTS_DIR / "test_loader_stress_test.py", None),
                }

                if response not in demo_map:
                    safe_print(_("❌ Invalid choice. Please select 1 through 11."))
                    return 1

                demo_name, test_file, required_version = demo_map[response]

                if required_version:
                    safe_print(
                        _("\nNOTE: The '{}' demo requires Python {}.").format(demo_name, required_version)
                    )
                    auto_adopt = non_interactive or getattr(args, "non_interactive", False)
                    if not handle_python_requirement(
                        required_version, pkg_instance, parser.prog, auto_adopt=auto_adopt
                    ):
                        return 1

                if not test_file or not test_file.exists():
                    safe_print(_("❌ Error: Test file {} not found.").format(test_file))
                    return 1

                configured_python_exe = pkg_instance.config_manager.config.get(
                    "python_executable", sys.executable
                )

                safe_print(
                    _('🚀 This demo uses "omnipkg run" to showcase its auto-healing capabilities.')
                )

                if response == "11":
                    safe_print(
                        _("\n⚠️  The Chaos Theory test is interactive - you'll be prompted to select scenarios.")
                    )
                    safe_print(_('💡 Tip: Choose "0" to run all tests for the full experience!\n'))

                cmd = [configured_python_exe, "-m", "omnipkg.cli"]
                if args.verbose:
                    cmd.append("--verbose")
                cmd.extend(["run", str(test_file)])

                process = subprocess.Popen(
                    cmd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in process.stdout:
                    safe_print(line, end="")
                returncode = process.wait()

                safe_print("-" * 60)
                if returncode == 0:
                    safe_print(_("🎉 Demo completed successfully!"))
                    # In non‑interactive mode, exit immediately to avoid hanging
                    if non_interactive:
                        sys.exit(0)
                else:
                    safe_print(_("❌ Demo failed with return code {}").format(returncode))
                return returncode

            finally:
                current_version_after_demo_tuple = get_actual_python_version()
                current_version_after_demo_str = (
                    f"{current_version_after_demo_tuple[0]}.{current_version_after_demo_tuple[1]}"
                )
                if original_python_str != current_version_after_demo_str:
                    print_header(_('Restoring original Python {} context').format(original_python_str))
                    pkg_instance.switch_active_python(original_python_str)

        elif args.command == "stress-test":
            test_file = TESTS_DIR / "test_loader_stress_test.py"
            if not test_file.exists():
                safe_print(_("❌ Error: Chaos test file not found."))
                return 1

            cmd = [sys.executable, str(test_file)]
            if hasattr(args, 'tests') and args.tests:
                cmd.extend(args.tests)
            elif args.yes:
                cmd.append("0")

            safe_print(_("🌀 Launching Chaos Theory Stress Test..."))
            if args.tests:
                safe_print(_("   Running tests: {}").format(", ".join(args.tests)))

            return subprocess.call(cmd)

        elif args.command == "install":
            original_python_tuple = get_actual_python_version()
            original_python_str = f"{original_python_tuple[0]}.{original_python_tuple[1]}"
            exit_code = 1

            try:
                packages_to_process = []
                if args.requirement:
                    req_path = Path(args.requirement)
                    if not req_path.is_file():
                        safe_print(
                            _("❌ Error: Requirements file not found at '{}'").format(req_path)
                        )
                        return 1
                    safe_print(_("📄 Reading packages from {}...").format(req_path.name))
                    with open(req_path, "r") as f:
                        packages_to_process = [
                            line.split("#")[0].strip() for line in f if line.split("#")[0].strip()
                        ]
                elif args.packages:
                    packages_to_process = args.packages
                else:
                    parser.parse_args(["install", "--help"])
                    return 1

                regular_packages, python_versions = separate_python_from_packages(packages_to_process)

                if python_versions:
                    safe_print(
                        _("🐍 Installing Python interpreter(s): {}").format(", ".join(python_versions))
                    )
                    for version in python_versions:
                        result = pkg_instance.adopt_interpreter(version)
                        if result != 0:
                            safe_print(_("⚠️  Warning: Failed to install Python {}").format(version))

                if regular_packages:
                    exit_code = pkg_instance.smart_install(
                        regular_packages,
                        force_reinstall=args.force_reinstall,
                        index_url=args.index_url,
                        extra_index_url=args.extra_index_url,
                    )
                else:
                    exit_code = 0

                return exit_code

            finally:
                current_version_after_install_tuple = get_actual_python_version()
                current_version_after_install_str = (
                    f"{current_version_after_install_tuple[0]}.{current_version_after_install_tuple[1]}"
                )
                if original_python_str != current_version_after_install_str:
                    print_header(_('Restoring original Python {} context').format(original_python_str))
                    final_cm = ConfigManager(suppress_init_messages=True)
                    final_pkg_instance = OmnipkgCore(config_manager=final_cm)
                    final_pkg_instance.switch_active_python(original_python_str)

        elif args.command == "install-with-deps":
            packages_to_process = [args.package] + args.dependency
            return pkg_instance.smart_install(packages_to_process)

        elif args.command == "uninstall":
            regular_packages, python_versions = separate_python_from_packages(args.packages)

            if python_versions:
                safe_print(
                    _("🗑️  Uninstalling Python interpreter(s): {}").format(", ".join(python_versions))
                )
                for version in python_versions:
                    result = pkg_instance.remove_interpreter(version, force=args.force)
                    if result != 0:
                        safe_print(_("⚠️  Warning: Failed to remove Python {}").format(version))

            if regular_packages:
                return pkg_instance.smart_uninstall(regular_packages, force=args.force)

            return 0

        elif args.command == "revert":
            return pkg_instance.revert_to_last_known_good(force=args.yes)

        elif args.command == "info":
            if args.package_spec.lower() == "python":
                current_python = Path(sys.executable).resolve()
                active_version_tuple = (sys.version_info.major, sys.version_info.minor)
                active_version_str = f"{active_version_tuple[0]}.{active_version_tuple[1]}"

                print_header(_("Python Interpreter Information"))
                managed_interpreters = (
                    pkg_instance.interpreter_manager.list_available_interpreters()
                )
                safe_print(_("🐍 Managed Python Versions (available for swapping):"))
                for ver, path in sorted(managed_interpreters.items()):
                    path_obj = Path(path).resolve()
                    is_current = (path_obj == current_python)
                    marker = " ⭐ (currently active)" if is_current else ""
                    safe_print(_("   • Python {}: {}{}").format(ver, path, marker))

                safe_print(_("\n🎯 Active Context: Python {}").format(active_version_str))
                safe_print(_("📍 Current Executable: {}").format(current_python))

                swapped_version = os.environ.get("OMNIPKG_PYTHON")
                if swapped_version and swapped_version != active_version_str:
                    safe_print(
                        _("💡 Note: OMNIPKG_PYTHON env var is set to {}, but you're running {}").format(
                            swapped_version, active_version_str
                        )
                    )
                    safe_print(
                        _("   (Expected when using version-specific commands like 8pkg{})").format(
                            active_version_str.replace(".", "")
                        )
                    )

                safe_print(
                    _("\n💡 To switch context, use: {} swap python <version>").format(parser.prog)
                )
                safe_print(
                    _("💡 To clean reinstall:    {} python reinstall <version>").format(parser.prog)
                )
                return 0
            else:
                return pkg_instance.show_package_info(
                    args.package_spec,
                    selection=args.selection,  # Pass the positional index
                    force=args.force           # Pass the -y flag
                )

        elif args.command == "list":
            return pkg_instance.list_packages(args.filter)

        elif args.command == "status":
            return pkg_instance.show_multiversion_status()

        elif args.command == "prune":
            return pkg_instance.prune_bubbled_versions(
                args.package, keep_latest=args.keep_latest, force=args.force
            )

        elif args.command == "reset":
            return pkg_instance.reset_knowledge_base(force=args.force)

        elif args.command == "rebuild-kb":
            pkg_instance.rebuild_knowledge_base(force=args.force)
            return 0

        elif args.command == "reset-config":
            return pkg_instance.reset_configuration(force=args.force)

        elif args.command == "daemon":
            if args.daemon_command == "start":
                cli_start()
            elif args.daemon_command == "stop":
                cli_stop()
            elif args.daemon_command == "restart":
                safe_print("🔄 Restarting daemon...")
                cli_stop()
                cli_start()
            elif args.daemon_command == "status":
                cli_status()
            elif args.daemon_command == "logs":
                cli_logs(follow=args.follow, tail_lines=args.lines)
            elif args.daemon_command == "monitor":
                try:
                    from omnipkg.isolation.resource_monitor import start_monitor
                    start_monitor(watch_mode=args.watch)
                except ImportError:
                    safe_print(_("❌ Error: resource_monitor module not found."))
                    return 1
            elif args.daemon_command == "idle":
                _dbg(f"daemon idle: python={args.idle_python}  count={args.count}")
                cli_idle_config(python_version=args.idle_python, count=args.count)

        elif args.command == "web":
            from omnipkg.apis.local_bridge import WebBridgeManager
            manager = WebBridgeManager()

            if args.web_command == "start":
                return manager.start()
            elif args.web_command == "stop":
                return manager.stop()
            elif args.web_command == "status":
                return manager.status()
            elif args.web_command == "restart":
                manager.stop()
                time.sleep(1)
                return manager.start()
            elif args.web_command == "logs":
                return manager.show_logs(follow=args.follow, lines=args.lines)
            elif args.web_command == "fix-permission":
                return manager.fix_permission()

        elif args.command == "run":
            return execute_run_command(
                args.script_and_args,
                cm,
                verbose=args.verbose,
                omnipkg_core=pkg_instance,
            )

        elif args.command == "upgrade":
            return upgrade(args, pkg_instance)

        else:
            parser.print_help()
            safe_print(_("\n💡 Did you mean 'omnipkg config set language <code>'?"))
            return 1

    except KeyboardInterrupt:
        safe_print(_("\n❌ Operation cancelled by user."))
        return 1
    except Exception as e:
        safe_print(_("\n❌ An unexpected error occurred: {}").format(e))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())