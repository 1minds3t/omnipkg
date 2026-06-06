"""omnipkg CLI - Enhanced with runtime interpreter switching and language support"""
from __future__ import annotations  # Python 3.6+ compatibility

import argparse
import copy
import os
import re
import time
import subprocess
import textwrap
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from omnipkg.i18n import _, SUPPORTED_LANGUAGES
from omnipkg.common_utils import safe_print, safe_input

# NOTE: worker_daemon, local_bridge, and commands.run are intentionally NOT
# imported here. Each was costing 29ms, 64ms, and 99ms respectively on EVERY
# invocation — including `8pkg install rich` which never touches any of them.
# They are now lazy-loaded inside the specific command handlers that need them.
from .common_utils import print_header
from .core import ConfigManager
from .core import omnipkg as OmnipkgCore

project_root = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent / "tests"

# ─────────────────────────────────────────────────────────────────────────────
# DEBUG HELPER — use everywhere instead of bare print(..., file=sys.stderr)
# Set OMNIPKG_DEBUG=1 in environment to enable.
# ─────────────────────────────────────────────────────────────────────────────
_DBG = os.environ.get("OMNIPKG_DEBUG", "0") == "1"

def _dbg(msg: str):
    """Lightweight debug printer; no-op unless OMNIPKG_DEBUG=1."""
    if _DBG:
        print(f"[DEBUG-CLI] {msg}", file=sys.stderr, flush=True)

_VERSION_CACHE: dict = {}  # exe_path -> (major, minor)

def get_actual_python_version(cm=None):
    """Get the actual Python version being used by omnipkg.

    Fast path: if configured_exe == sys.executable (the common case inside a
    daemon worker) we already know the version from sys.version_info — no
    subprocess needed.  Results are also cached so repeated calls within the
    same process (e.g. the install finally-block) cost nothing.
    """
    try:
        if cm is None:
            from omnipkg.core import ConfigManager
            cm = ConfigManager(suppress_init_messages=True)
        configured_exe = cm.config.get("python_executable") or sys.executable

        # Normalise to real path so symlink variants compare equal
        configured_exe = os.path.realpath(configured_exe)
        current_exe    = os.path.realpath(sys.executable)

        # Fast path: same interpreter that's running right now
        if configured_exe == current_exe:
            return sys.version_info[:2]

        # Cache: already paid the subprocess cost this process lifetime
        if configured_exe in _VERSION_CACHE:
            return _VERSION_CACHE[configured_exe]

        # Slow path: genuinely different interpreter — ask it once, then cache
        version_tuple = cm._verify_python_version(configured_exe)
        result = version_tuple[:2] if version_tuple else sys.version_info[:2]
        _VERSION_CACHE[configured_exe] = result
        return result
    except Exception:
        return sys.version_info[:2]

def _print_env_vars():
    """Print all recognized omnipkg env vars with descriptions and current values."""
    vars_doc = [
        # ── Non-interactive / CI control ──────────────────────────────────────
        ("OMNIPKG_NONINTERACTIVE",   "0|1",    "Force non-interactive mode (same as -y/--non-interactive)"),
        ("CI",                       "1",      "Standard CI env var — auto-detected, implies non-interactive"),
        # ── Verbosity ─────────────────────────────────────────────────────────
        ("OMNIPKG_VERBOSE",          "0|1",    "Verbose output across all subsystems (same as -V/--verbose)"),
        ("OMNIPKG_DEBUG",            "0|1",    "Debug output across all subsystems (implies verbose)"),
        # ── Language / Python context ─────────────────────────────────────────
        ("OMNIPKG_LANG",             "en|es…", "Override display language (ISO 639-1 code)"),
        ("OMNIPKG_PYTHON",           "3.11",   "Active Python version context (set by swap/install)"),
        ("OMNIPKG_ACTIVE_PYTHON",    "3.11",   "Same as OMNIPKG_PYTHON, set in parallel for compat"),
        ("OMNIPKG_PYTHON_EXECUTABLE","path",   "Full path to the active Python interpreter"),
        ("OMNIPKG_PYTHON_CHOICE",    "1",      "Auto-selects Python choice in non-interactive mode"),
        # ── Daemon / worker ───────────────────────────────────────────────────
        ("OMNIPKG_WORKER_TIMEOUT",   "864000", "Max seconds a daemon worker job may run"),
        ("OMNIPKG_DAEMON_TEMP_ID",   "hash",   "Override the venv hash used for socket/pid/log paths"),
        ("OMNIPKG_MULTIVERSION_BASE","path",   "Override bubble root directory"),
        # ── Misc ──────────────────────────────────────────────────────────────
        ("OMNIPKG_DEMO_ID",          "1",      "Auto-selects demo scenario in non-interactive mode"),
        ("UV_FFI_PROFILE",           "0|1",    "Enable Rust FFI timing profile output"),
        ("_OMNIPKG_RESEAT",          "1",      "Internal: signals daemon restart is a reseat cycle"),
    ]
    safe_print("\n  Recognized omnipkg environment variables:\n")
    for name, values, desc in vars_doc:
        current = os.environ.get(name, "")
        marker = f"  ← {current!r}" if current else ""
        safe_print(f"  {name:<32}  {values:<10}  {desc}{marker}")
    safe_print("")

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

    managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
    target_path = managed_interpreters[required_version_str]

    os.environ["OMNIPKG_PYTHON"] = required_version_str
    os.environ["OMNIPKG_ACTIVE_PYTHON"] = required_version_str
    os.environ["OMNIPKG_PYTHON_EXECUTABLE"] = str(target_path)
    safe_print(
        _("   - ✅ Environment successfully configured for Python {}.").format(required_version_str)
    )
    safe_print(_("🚀 Proceeding..."))
    safe_print("=" * 60)

    return True

def get_version():
    """Get version from package metadata."""
    import re as _re
    # Try pyproject.toml first (works in dev mode and fresh interpreters)
    try:
        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        if toml_path.exists():
            text = toml_path.read_text()
            m = _re.search(r'^\s*version\s*=\s*"([^"]+)"', text, _re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass
    # Fallback: installed metadata
    try:
        from importlib.metadata import version
        return version("omnipkg")
    except Exception:
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
    from omnipkg.i18n import normalize_language_code  # IMPORT AT THE TOP!

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
        val = safe_input(_("  Enter language code (e.g. en, es, de): "), default="").strip()
        if not val:
            safe_print(_("❌ No input. No changes made."))
            return 1
        normalized = normalize_language_code(val)
        if normalized is None:
            safe_print(_("❌ Unknown language '{}'. Run 'omnipkg config' to see all options.").format(val))
            safe_print(_("   Example: {} config set language es").format(parser_prog))
            return 1
        cm.set("language", normalized)
        _.set_language(normalized)
        os.environ["OMNIPKG_LANG"] = normalized
        safe_print(_("✅ Language set to: {} ({})").format(normalized, SUPPORTED_LANGUAGES[normalized]))

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
    import omnipkg.cli as _cli_mod
    _lang_key = _.current_lang
    if _lang_key in (_cli_mod._CACHED_8PKG_PARSER or {}):
        return _cli_mod._CACHED_8PKG_PARSER[_lang_key]
    if _cli_mod._CACHED_8PKG_PARSER is None:
        _cli_mod._CACHED_8PKG_PARSER = {}
    # Build fresh — do NOT mutate the shared cached base parser.
    parser = create_parser()
    parser = copy.copy(parser)          # shallow-copy the parser object
    parser.prog = "8pkg"
    parser.description = _(
        "🚀 The intelligent Python package manager that eliminates dependency hell (8pkg = ∞pkg)"
    )
    epilog_parts = parser.epilog.split("\n")
    parser.epilog = "\n".join(line.replace("omnipkg", "8pkg") for line in epilog_parts)
    _cli_mod._CACHED_8PKG_PARSER[_lang_key] = parser
    return parser

class _CleanFormatter(argparse.RawTextHelpFormatter):
    """RawTextHelpFormatter that properly hides SUPPRESS'd subcommands."""
    def _format_action(self, action):
        if action.help == argparse.SUPPRESS:
            return ''
        return super()._format_action(action)


_SUPPORTED_PYTHONS = [
    "3.7", "3.8", "3.9", "3.10", "3.11",
    "3.12", "3.13", "3.14", "3.15",
]


def _run_python_info_explorer(pkg_instance, is_interactive: bool) -> int:
    """Interactive Python interpreter explorer — `8pkg info python` and `8pkg python info`."""

    def _print_python_table():
        current_python = Path(sys.executable)
        managed = pkg_instance.interpreter_manager.list_available_interpreters()
        print_header(_("Python Interpreter Explorer"))
        safe_print(_("  {:>3}  {:>5}  {:12}  {}").format("#", "Ver", "Status", "Path"))
        safe_print("  " + "─" * 68)
        rows = []
        for ver in _SUPPORTED_PYTHONS:
            if ver in managed:
                path = managed[ver]
                is_active = (Path(path) == current_python)
                status = "⭐ active  " if is_active else "✅ managed "
                rows.append((ver, path, status, True))
            else:
                rows.append((ver, _("(not adopted)"), "➕ available", False))
        for i, (ver, path, status, _adopted) in enumerate(rows, 1):
            safe_print(_("  {:>3}.  {:>5}  {}  {}").format(i, ver, status, path))
        safe_print("")
        return rows

    if not is_interactive:
        _print_python_table()
        return 0

    while True:
        rows = _print_python_table()
        safe_print(_("  [#] select version    [r] rescan    [q] quit"))
        raw = safe_input(_("  → "), default="q").strip().lower()

        if raw in ("q", "quit", ""):
            break

        if raw == "r":
            safe_print(_("🔍 Rescanning interpreter registry..."))
            pkg_instance.rescan_interpreters()
            continue

        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            idx = int(raw) - 1
            ver, path, status, is_adopted = rows[idx]
            safe_print("")
            safe_print(_("🐍 Python {}  —  {}").format(ver, status.strip()))

            if is_adopted:
                safe_print(_("   Path: {}").format(path))
                active_ver = "{}.{}".format(sys.version_info.major, sys.version_info.minor)
                is_active = (ver == active_ver)
                options = []
                if not is_active:
                    options.append(("s", _("swap  — open a shell with this Python active")))
                options.append(("i", _("reinstall")))
                options.append(("x", _("remove")))
                options.append(("b", _("back")))
                for key, label in options:
                    safe_print(_("   [{}] {}").format(key, label))
                action = safe_input(_("  → "), default="b").strip().lower()

                if action == "s" and not is_active:
                    from omnipkg.dispatcher import resolve_python_path, spawn_swap_shell
                    python_path = resolve_python_path(ver)
                    return spawn_swap_shell(
                        version=ver, python_path=python_path, pkg_instance=pkg_instance
                    )
                elif action == "i":
                    safe_print(_("🔄 Reinstalling Python {}...").format(ver))
                    pkg_instance.adopt_interpreter(ver)
                elif action == "x":
                    confirm = safe_input(
                        _("⚠️  Remove Python {}? This cannot be undone. (y/N): ").format(ver),
                        default="n",
                    ).strip().lower()
                    if confirm in ("y", "yes"):
                        pkg_instance.remove_interpreter(ver, force=True)
                        safe_print(_("✅ Python {} removed.").format(ver))
                    else:
                        safe_print(_("Cancelled."))
            else:
                safe_print(_("   Not currently in your managed pool."))
                safe_print(_("   [a] adopt    [b] back"))
                action = safe_input(_("  → "), default="b").strip().lower()
                if action == "a":
                    safe_print(_("⬇️  Adopting Python {}...").format(ver))
                    result = pkg_instance.adopt_interpreter(ver)
                    if result == 0:
                        safe_print(_("✅ Python {} adopted.").format(ver))
                    else:
                        safe_print(_("❌ Failed to adopt Python {}.").format(ver))

            safe_print("")
            continue

        safe_print(_("❓ Unrecognized — enter a row number, 'r', or 'q'."))

    return 0

def create_parser():
    """Argparse skeleton for omnipkg — parsing structure only, no display help strings.

    Top-level help text (8pkg --help / omnipkg --help) is served from
    omnipkg._help.HELP_TEXT, which is baked at build time from help.toml via
    dev_tools/gen_help.py.  The C dispatcher handles --help before Python even
    starts; this path only runs when --help appears alongside a real subcommand
    (e.g. '8pkg install --help') in which case argparse uses each subparser's
    epilog= string.

    To add a new command:
      1. Add a [[command]] block to src/omnipkg/help.toml
      2. Add the subparser + arguments here (help= stays argparse.SUPPRESS)
      3. Add the handler function and wire it in main()
      4. Run dev_tools/gen_help.py to regenerate _help.h and _help.py
      5. Recompile dispatcher.c (the stale-check will trigger this automatically)
    """
    import omnipkg.cli as _cli_mod
    _lang_key = _.current_lang
    if _lang_key in _cli_mod._CACHED_PARSER:
        return _cli_mod._CACHED_PARSER[_lang_key]
    epilog_parts = [
        _("Common commands:"),
        _("  install <pkg>              install, uninstall, info, list, upgrade"),
        _("  python adopt|switch|info   manage Python interpreters"),
        _("  reset kb|config            reset knowledge base or configuration"),
        _("  daemon start|stop|status   manage background worker"),
        _("  run <script|cmd>           auto-healing script runner"),
        "",
        _("Version: {}").format(VERSION),
    ]
    translated_epilog = "\n".join(epilog_parts)
    parser = argparse.ArgumentParser(
        prog="omnipkg",
        description=_("🚀 The intelligent Python package manager that eliminates dependency hell"),
        formatter_class=_CleanFormatter,
        epilog=translated_epilog,
    )
    parser.add_argument(
        "-v", "--version", action="version", version=_("%(prog)s {}").format(VERSION)
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--py", "--python",
        dest="python",
        metavar="VER",
        help=argparse.SUPPRESS,
    )
    parser
    parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="<command>", required=False
    )

    # ── install ───────────────────────────────────────────────────────────────
    install_parser = subparsers.add_parser(
        "install",
        help=argparse.SUPPRESS,
        formatter_class=_CleanFormatter,
        epilog=_(
            "Examples:\n"
            "  8pkg install requests\n"
            "  8pkg install 'numpy>=1.20' scipy\n"
            "  8pkg install uv==0.7.13 uv==0.7.14        # coexisting versions!\n"
            "  8pkg install python==3.11                  # adopt a Python interpreter\n"
            "  8pkg install -r requirements.txt\n"
            "  8pkg install --force requests              # force reinstall\n"
            "  8pkg install --dry-run requests            # preview only\n"
            "  8pkg install --strategy latest-active requests  # override install strategy\n"
            "  8pkg install --target /tmp/pkgs requests   # install to custom directory\n"
            "  8pkg install --no-deps requests            # skip dependency resolution\n"
            "  8pkg install --pre requests                # include pre-release versions\n"
            "  8pkg install --find-links /path requests   # search local directory for wheels\n"
            "  8pkg install --no-binary :all: requests    # force source builds\n"
            "  8pkg install --only-binary :all: requests  # force binary wheels\n"
            "  8pkg -y install requests                   # skip all prompts\n"
            "  8pkg311 install requests                   # install under Python 3.11\n"
        ),
    )
    install_parser.add_argument(
        "--upgrade", "-U",
        action="store_true",
        dest="upgrade",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "-r", "--requirement", help=argparse.SUPPRESS, metavar="FILE"
    )
    install_parser.add_argument(
        "--force", "--force-reinstall",
        dest="force_reinstall",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    install_parser.add_argument(
        "-y", "--yes",
        dest="yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--strategy",
        dest="strategy",
        choices=["stable-main", "latest-active"],
        metavar="STRATEGY",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--target", "-t",
        dest="target",
        metavar="DIR",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--no-deps",
        action="store_true",
        dest="no_deps",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--pre",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--no-cache-dir",
        action="store_true",
        dest="no_cache_dir",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument("--index-url", "-i", metavar="URL", help=argparse.SUPPRESS)
    install_parser.add_argument("--extra-index-url", metavar="URL", help=argparse.SUPPRESS)
    install_parser.add_argument(
        "--find-links",
        dest="find_links",
        metavar="URL",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--trusted-host",
        dest="trusted_host",
        metavar="HOST",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--timeout",
        dest="timeout",
        type=int,
        metavar="SECS",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--retries",
        dest="retries",
        type=int,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--no-binary",
        dest="no_binary",
        metavar="PKG",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--only-binary",
        dest="only_binary",
        metavar="PKG",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--ignore-installed", "--ignore-requires-python",
        dest="ignore_installed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "packages", nargs="*",
        help=argparse.SUPPRESS,
    )
    install_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── install-with-deps ────────────────────────────────────────────────────
    # NOTE: handler not yet implemented — hidden from help until ready
    install_with_deps_parser = subparsers.add_parser(
        "install-with-deps",
        help=argparse.SUPPRESS,   # hide from top-level help until implemented
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "⚠️  This command is not yet fully implemented.\n"
            "    Use 'install' with explicit version pins for now.\n\n"
            "Example:\n"
            "  8pkg install-with-deps tensorflow==2.13.0 \\\n"
            "      --dependency numpy==1.24.3 \\\n"
            "      --dependency protobuf==3.20.3\n"
        ),
    )
    install_with_deps_parser.add_argument(
        "package", help=argparse.SUPPRESS
    )
    install_with_deps_parser.add_argument(
        "--dependency", "-d",
        action="append",
        help=argparse.SUPPRESS,
        default=[],
    )
    install_with_deps_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # ── uninstall ─────────────────────────────────────────────────────────────
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help=argparse.SUPPRESS,
    )
    uninstall_parser.add_argument("packages", nargs="+", help=argparse.SUPPRESS)
    uninstall_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    uninstall_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── info ──────────────────────────────────────────────────────────────────
    info_parser = subparsers.add_parser(
        "info",
        help=argparse.SUPPRESS,
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
        help=argparse.SUPPRESS,
    )
    info_parser.add_argument(
        "selection",
        nargs="?",
        type=int,
        help=argparse.SUPPRESS,
    )
    info_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    info_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # pip compat: `8pkg show <pkg>` → same as `8pkg info <pkg>`
    _show_parser = subparsers.add_parser("show", help=argparse.SUPPRESS)
    _show_parser.add_argument("package_spec", help=argparse.SUPPRESS)
    _show_parser.add_argument("--selection", "-s", type=int, default=None, help=argparse.SUPPRESS)
    _show_parser.add_argument("--force", "-y", action="store_true", help=argparse.SUPPRESS)

    # ── revert ────────────────────────────────────────────────────────────────
    revert_parser = subparsers.add_parser(
        "revert",
        help=argparse.SUPPRESS,
    )
    revert_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    revert_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── swap ──────────────────────────────────────────────────────────────────
    swap_parser = subparsers.add_parser(
        "swap",
        help=argparse.SUPPRESS,
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
        help=argparse.SUPPRESS,
    )
    swap_parser.add_argument("version", nargs="?", help=argparse.SUPPRESS)
    swap_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    swap_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── list ──────────────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser(
        "list",
        help=argparse.SUPPRESS,
    )
    list_parser.add_argument(
        "filter", nargs="?",
        help=argparse.SUPPRESS,
    )
    list_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── python ────────────────────────────────────────────────────────────────
    python_parser = subparsers.add_parser(
        "python",
        help=argparse.SUPPRESS,
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
    python_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )
    python_subparsers = python_parser.add_subparsers(
        dest="python_command", help=argparse.SUPPRESS, required=False
    )

    python_adopt_parser = python_subparsers.add_parser(
        "adopt",
        help=argparse.SUPPRESS,
    )
    python_adopt_parser.add_argument("version", help=argparse.SUPPRESS)
    python_adopt_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    python_adopt_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    python_switch_parser = python_subparsers.add_parser(
        "switch",
        help=argparse.SUPPRESS,
    )
    python_switch_parser.add_argument("version", help=argparse.SUPPRESS)

    python_reinstall_parser = python_subparsers.add_parser(
        "reinstall",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Shortcut for: uninstall python==X.Y && install python==X.Y\n\n"
            "Example:\n"
            "  omnipkg python reinstall 3.9\n"
            "  omnipkg python reinstall 3.9 -y   # no prompts\n"
        ),
    )
    python_reinstall_parser.add_argument("version", help=argparse.SUPPRESS)
    python_reinstall_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    python_subparsers.add_parser(
        "rescan",
        help=argparse.SUPPRESS,
    )

    remove_parser = python_subparsers.add_parser(
        "remove",
        help=argparse.SUPPRESS,
    )
    remove_parser.add_argument(
        "version",
        help=argparse.SUPPRESS,
    )
    remove_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    python_swap_parser = python_subparsers.add_parser(
        "swap",
        help=argparse.SUPPRESS,
    )
    python_swap_parser.add_argument("version", help=argparse.SUPPRESS)
    python_swap_parser.add_argument(
        "-y", "--yes",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    python_info_parser = python_subparsers.add_parser(
        "info",
        help=argparse.SUPPRESS,
    )
    python_info_parser.add_argument(
        "package_spec",
        help=argparse.SUPPRESS,
    )
    python_info_parser.add_argument(
        "selection", nargs="?", type=int,
        help=argparse.SUPPRESS,
    )
    python_info_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true",
        help=argparse.SUPPRESS,
    )
    # ── env ────────────────────────────────────────────────────────────────
    subparsers.add_parser(
        "env",
        help=argparse.SUPPRESS,
        description=_("Lists all OMNIPKG_* environment variables, their purpose, and current value."),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # ── status ────────────────────────────────────────────────────────────────
    subparsers.add_parser(
        "status",
        help=argparse.SUPPRESS,
        description=_(
            "Show a full health report of the active environment:\n"
            "  - Jailed tools (pip/uv/conda lockdown status)\n"
            "  - Active package count and site-packages path\n"
            "  - All bubble versions with sizes\n"
            "  - Knowledge base sync state\n\n"
            "This is the first command to run when something looks off."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # ── demo ──────────────────────────────────────────────────────────────────
    demo_parser = subparsers.add_parser(
        "demo",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Demos (1-10):\n"
            "  1  Rich test (Python module switching)\n"
            "  2  UV test (binary switching)\n"
            "  3  NumPy + SciPy (C-extension switching) — requires Python 3.11\n"
            "  4  TensorFlow (complex dependency switching) — requires Python 3.11\n"
            "  5  Multiverse Healing (cross-Python hot-swapping) — requires Python 3.11\n"
            "  6  Old Flask Test (legacy package healing) — requires Python 3.8\n"
            "  7  Script-healing Test (omnipkg run scripts)\n"
            "  8  Quantum Multiverse Warp (concurrent installs) — requires Python 3.11\n"
            "  9  Flask Port Finder (auto-healing with Flask)\n"
            " 10  CLI Healing Test (omnipkg run shell commands)\n\n"
            "For chaos/stress tests use: 8pkg stress-test\n"
        ),
    )
    demo_parser.add_argument(
        "demo_id",
        nargs="?",
        type=int,
        help=argparse.SUPPRESS,
    )
    demo_parser.add_argument(
        "--non-interactive", "-n",
        dest="non_interactive",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    demo_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # ── stress-test ───────────────────────────────────────────────────────────
    stress_parser = subparsers.add_parser(
        "stress-test",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "⚠️  Each test exercises extreme ABI switching scenarios.\n"
            "   Tests are designed to run individually — running all at once is unsafe.\n"
            "   Default (no args): launches interactive menu; picks test 1 in non-interactive mode.\n\n"
            "Example:\n"
            "  8pkg stress-test        # interactive menu\n"
            "  8pkg stress-test 3      # run test 3 only\n"
            "  8pkg -y stress-test 3   # run test 3, no prompt\n"
        ),
    )
    stress_parser.add_argument(
        "tests",
        nargs="*",
        help=argparse.SUPPRESS,
    )
    stress_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    stress_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # ── reset ─────────────────────────────────────────────────────────────────
    # 'reset' is now a subcommand group. Old bare 'reset' (= reset kb) still works.
    reset_parser = subparsers.add_parser(
        "reset",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Subcommands:\n"
            "  kb      Rebuild the omnipkg knowledge base (default)\n"
            "  config  Delete config file for a fresh setup\n\n"
            "Examples:\n"
            "  8pkg reset kb\n"
            "  8pkg reset config -y\n"
        ),
    )
    reset_subparsers = reset_parser.add_subparsers(dest="reset_command", required=False)
    reset_kb_parser = reset_subparsers.add_parser(
        "kb", help=argparse.SUPPRESS
    )
    reset_kb_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=argparse.SUPPRESS
    )
    reset_config_sub = reset_subparsers.add_parser(
        "config", help=argparse.SUPPRESS
    )
    reset_config_sub.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=argparse.SUPPRESS
    )

    # Hidden legacy aliases so old scripts/habits still work
    _rebuild_kb_parser = subparsers.add_parser(
        "rebuild-kb", help=argparse.SUPPRESS
    )
    _rebuild_kb_parser.add_argument("--force", "-f", action="store_true")
    _reset_config_parser = subparsers.add_parser(
        "reset-config", help=argparse.SUPPRESS
    )
    _reset_config_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true"
    )

    # ── config ────────────────────────────────────────────────────────────────
    config_parser = subparsers.add_parser(
        "config",
        help=argparse.SUPPRESS,
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
        "view", help=argparse.SUPPRESS
    )
    config_set_parser = config_subparsers.add_parser(
        "set",
        help=argparse.SUPPRESS,
    )
    config_set_parser.add_argument(
        "key",
        choices=["language", "install_strategy"],
        help=argparse.SUPPRESS,
    )
    config_set_parser.add_argument("value", help=argparse.SUPPRESS)
    config_reset_parser = config_subparsers.add_parser(
        "reset", help=argparse.SUPPRESS
    )
    config_reset_parser.add_argument(
        "key",
        choices=["interpreters"],
        help=argparse.SUPPRESS,
    )

    reset_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── doctor ────────────────────────────────────────────────────────────────
    doctor_parser = subparsers.add_parser(
        "doctor",
        help=argparse.SUPPRESS,
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
        help=argparse.SUPPRESS,
    )
    doctor_parser.add_argument(
        "--rebuild",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    doctor_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    doctor_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── heal ──────────────────────────────────────────────────────────────────
    heal_parser = subparsers.add_parser(
        "heal",
        help=argparse.SUPPRESS,
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
        help=argparse.SUPPRESS,
    )
    heal_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    heal_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── run ───────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help=argparse.SUPPRESS,
        description=_(
            "Execute a Python script, inline code, or CLI command (e.g., pytest) within\n"
            "omnipkg's auto-healing environment. Missing imports and ABI errors are resolved\n"
            "automatically. Passes all remaining arguments directly to the target."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(_("""\
            USAGE:
              8pkg run <script.py> [args...]    run a Python script
              8pkg run <cli-tool> [args...]     run any installed CLI tool
              8pkg run python -c "code"        run inline Python
              8pkg run python -                run Python from stdin
              8pkg run python -m module        run a module

            EXAMPLES:
              8pkg run my_script.py --arg 1
              8pkg run pytest tests/ -x
              8pkg run uvicorn main:app --reload
              8pkg run python -c "import pandas; print(pandas.__version__)"
              echo "import requests" | 8pkg run python -
              8pkg38 run pytest                # run under Python 3.8
              8pkg --py 3.8 run pytest         # same via flag

            NOTE: 'python' here means the system/configured python, not a literal
            8pkg subcommand. If it conflicts, use the full path or 8pkg38/8pkg311 aliases.
        """))
    )
    run_parser.add_argument(
        "script_and_args",
        nargs=argparse.REMAINDER,
        help=argparse.SUPPRESS,
    )
    run_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── daemon ────────────────────────────────────────────────────────────────
    daemon_parser = subparsers.add_parser(
        "daemon",
        help=argparse.SUPPRESS,
    )
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=False)
    daemon_subparsers.add_parser("start", help=argparse.SUPPRESS)
    daemon_subparsers.add_parser("stop", help=argparse.SUPPRESS)
    daemon_subparsers.add_parser("restart", help=argparse.SUPPRESS)
    daemon_subparsers.add_parser("status", help=argparse.SUPPRESS)

    idle_parser = daemon_subparsers.add_parser(
        "idle",
        help=argparse.SUPPRESS,
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
        help=argparse.SUPPRESS,
    )
    idle_parser.add_argument(
        "--count",
        type=int,
        help=argparse.SUPPRESS,
    )

    daemon_logs = daemon_subparsers.add_parser("logs", help=argparse.SUPPRESS)
    daemon_logs.add_argument(
        "-f", "--follow",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    daemon_logs.add_argument(
        "-n", "--lines",
        type=int, default=50,
        help=argparse.SUPPRESS,
    )

    daemon_monitor = daemon_subparsers.add_parser(
        "monitor", help=argparse.SUPPRESS
    )
    daemon_monitor.add_argument(
        "-w", "--watch",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # ── web ───────────────────────────────────────────────────────────────────
    web_parser = subparsers.add_parser(
        "web",
        help=argparse.SUPPRESS,
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
    web_subparsers = web_parser.add_subparsers(dest="web_command", required=False)
    web_subparsers.add_parser("start", help=argparse.SUPPRESS)
    web_subparsers.add_parser("stop", help=argparse.SUPPRESS)
    web_subparsers.add_parser("status", help=argparse.SUPPRESS)
    web_subparsers.add_parser("restart", help=argparse.SUPPRESS)
    web_subparsers.add_parser(
        "fix-permission",
        help=argparse.SUPPRESS,
    )

    web_logs = web_subparsers.add_parser("logs", help=argparse.SUPPRESS)
    web_logs.add_argument(
        "-f", "--follow",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    web_logs.add_argument(
        "-n", "--lines",
        type=int, default=50,
        help=argparse.SUPPRESS,
    )

    # ── prune ─────────────────────────────────────────────────────────────────
    prune_parser = subparsers.add_parser(
        "prune",
        help=argparse.SUPPRESS,
    )
    prune_parser.add_argument("package", help=argparse.SUPPRESS)
    prune_parser.add_argument(
        "--keep-latest",
        type=int,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    prune_parser.add_argument(
        "--yes", "-y", dest="force", action="store_true", help=argparse.SUPPRESS
    )
    prune_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # --- export ---
    export_parser = subparsers.add_parser(
        "export",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Writes to <venv_root>/.omnipkg/omnipkg.lock by default.\n"
            "Use --output to override, e.g. for sharing or CI artifacts.\n\n"
            "Examples:\n"
            "  8pkg export                        # write to default location\n"
            "  8pkg export -o /tmp/my.lock        # explicit path\n"
            "  8pkg export --python 3.11          # only capture python 3.11\n"
        ),
    )
    export_parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help=argparse.SUPPRESS,
    )
    export_parser.add_argument(
        "--python", "-p",
        metavar="VER",
        action="append",
        dest="pythons",
        help=argparse.SUPPRESS,
    )
    export_parser.add_argument(
        "--venv-root",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    export_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # --- sync ---
    sync_parser = subparsers.add_parser(
        "sync",
        help=argparse.SUPPRESS,
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_(
            "Reads from <venv_root>/.omnipkg/omnipkg.lock by default.\n"
            "Clears and reinstalls all packages for each python in the lock file.\n\n"
            "Examples:\n"
            "  8pkg sync                          # sync from default lock file\n"
            "  8pkg sync /path/to/other.lock      # explicit lock file\n"
            "  8pkg sync --python 3.11            # only sync python 3.11\n"
            "  8pkg sync --yes                    # skip confirmation (CI/Docker)\n"
        ),
    )
    sync_parser.add_argument(
        "lock_file",
        metavar="LOCK_FILE",
        nargs="?",                  # optional — defaults to canonical path
        default=None,
        help=argparse.SUPPRESS,
    )
    sync_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sync_parser.add_argument(
        "--python", "-p",
        metavar="VER",
        action="append",
        dest="pythons",
        help=argparse.SUPPRESS,
    )
    sync_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sync_parser.add_argument(
        "--venv-root",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    sync_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )

    # ── upgrade ───────────────────────────────────────────────────────────────
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help=argparse.SUPPRESS,
    )
    upgrade_parser.add_argument(
        "package_name",
        nargs="*",
        default=["omnipkg"],
        help=argparse.SUPPRESS,
    )
    upgrade_parser.add_argument(
        "--version",
        help=argparse.SUPPRESS,
    )
    upgrade_parser.add_argument(
        "--yes", "-y",
        dest="force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    upgrade_parser.add_argument(
        "--force-dev",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    upgrade_parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        dest="verbose",
        help=argparse.SUPPRESS,
    )
    upgrade_parser.set_defaults(func=upgrade)

    _cli_mod._CACHED_PARSER[_.current_lang] = parser
    return parser

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
# ── Daemon preload sentinels ──────────────────────────────────────────────
# worker_daemon.py populates these before blocking on stdin so that the
# first real command in a daemon worker skips OmnipkgCore.__init__ entirely.
_PRELOADED_CM     = None   # pre-built ConfigManager instance
_PRELOADED_CORE   = None   # pre-built OmnipkgCore instance
_CACHED_PARSER    = {}     # cached create_parser() result, keyed by lang
_CACHED_8PKG_PARSER = None # cached create_8pkg_parser() result
# ─────────────────────────────────────────────────────────────────────────
def main():
    """Main application entry point with pre-flight version check."""
    import time as _mt; _t_main_entry = _mt.perf_counter()
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

        # ── Normalize flags AND known command tokens to lowercase ─────────────
        # This lets users type "8PKG INSTALL Rich" and have it work correctly.
        # Package names / version specs after the command are intentionally preserved.
        _KNOWN_COMMANDS = {
            "install", "install-with-deps", "uninstall", "info", "revert", "swap",
            "list", "python", "status", "demo", "stress-test", "reset", "config",
            "doctor", "heal", "run", "daemon", "web", "prune", "export", "sync",
            "upgrade",
            # python sub-commands
            "adopt", "switch", "reinstall", "rescan", "remove", "info", "swap",
            # daemon sub-commands
            "start", "stop", "restart", "logs", "monitor", "idle",
            # web sub-commands
            "fix-permission",
            # config sub-commands
            "set", "view",
            # reset sub-commands
            "kb", "cfg",
        }
        normalized_argv = [sys.argv[0]]
        for arg in sys.argv[1:]:
            if arg.startswith("-"):
                normalized_argv.append(arg)          # preserve flag case — -V ≠ -v
            elif arg.lower() in _KNOWN_COMMANDS:
                normalized_argv.append(arg.lower())
            else:
                normalized_argv.append(arg)   # preserve package names / version specs
        sys.argv = normalized_argv

        # ── Pre-parse for global flags ─────────────────────────────────────
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument("--lang", default=None)
        global_parser.add_argument("--verbose", "-V", action="store_true")
        global_parser.add_argument("--py", "--python", dest="python", default=None)
        global_parser.add_argument("--yes", "-y", action="store_true", default=False)
        global_parser.add_argument("--non-interactive", "-n", dest="non_interactive",
                                   action="store_true", default=False)
        global_args, remaining_args = global_parser.parse_known_args()

        if remaining_args and not remaining_args[0].startswith("-"):
            remaining_args[0] = remaining_args[0].lower()

        command = (
            remaining_args[0] if remaining_args and not remaining_args[0].startswith("-") else None
        )

        # ── Early exits: no core init needed ──────────────────────────────────────
        # Only intercept --version when there is NO real subcommand — this prevents
        # `8pkg python --version` from being swallowed by the omnipkg version check.
        _bare_version_request = ("-v" in remaining_args or "--version" in remaining_args)
        _has_real_subcommand = bool(
            remaining_args and not remaining_args[0].startswith("-")
            and remaining_args[0] not in ("-v", "--version", "-h", "--help")
        )
        if _bare_version_request and not _has_real_subcommand:
            safe_print(_("omnipkg {}").format(get_version()))
            return 0

        # ── Everything below here only runs for real commands ─────────────────────
        os.environ["OMNIPKG_LANG"] = os.environ.get("OMNIPKG_LANG", "")
        # Use pre-built ConfigManager from daemon preload if available
        import omnipkg.cli as _cli_mod
        if _cli_mod._PRELOADED_CM is not None:
            cm = _cli_mod._PRELOADED_CM
            _cli_mod._PRELOADED_CM = None   # consume once
        else:
            cm = ConfigManager()
        user_lang = global_args.lang or cm.config.get("language") or os.environ.get("OMNIPKG_LANG")

        if user_lang:
            if _.current_lang != user_lang:
                _.set_language(user_lang)
            os.environ["OMNIPKG_LANG"] = user_lang

        if command is None:
            # No subcommand — print top-level help from the pre-rendered constant.
            # _help.py is baked at build time from help.toml via dev_tools/gen_help.py.
            # No argparse construction, no filesystem reads, no formatting.
            # The daemon already has omnipkg._help in sys.modules from preload,
            # so this is effectively a free lookup + write().
            from omnipkg._help import HELP_TEXT
            explicit_help = "-h" in remaining_args or "--help" in remaining_args
            if not explicit_help:
                # Bare invocation — POSIX convention: error to stderr, exit non-zero.
                # Mirrors the C dispatcher's argc==1 fast path (same message).
                sys.stderr.write("error: no command specified\n\n")
            sys.stdout.write(HELP_TEXT)
            return 0 if explicit_help else 2
        # When a subcommand IS present with -h/--help, fall through to parse_args()
        # which calls the subparser's print_help() and SystemExits automatically.

        # ── Choose minimal vs full init ────────────────────────────────────────────
        use_minimal = False
        if command in {"config", "python", "doctor"}:
            use_minimal = True
        elif command == "swap":
            if len(remaining_args) > 1 and remaining_args[1].lower() == "python":
                use_minimal = True
        elif command == "daemon":
            subcmd = remaining_args[1].lower() if len(remaining_args) > 1 else ""
            
            # These MUST run in-process, not through a daemon worker:
            #   restart/stop/kill — would deadlock (killing own parent)
            #   monitor           — needs a real TTY for input()
            if subcmd in ("restart", "stop", "kill", "monitor"):
                # If we're already inside a daemon worker, re-exec directly
                if os.environ.get("OMNIPKG_IS_DAEMON_WORKER") == "1":
                    python = sys.executable
                    os.execv(python, [python, "-m", "omnipkg.cli"] + sys.argv[1:])
                    # execv replaces this process — never returns on success
                use_minimal = True

        # Use pre-built OmnipkgCore from daemon preload if available (full mode only)
        import omnipkg.cli as _cli_mod
        if not use_minimal and _cli_mod._PRELOADED_CORE is not None:
            pkg_instance = _cli_mod._PRELOADED_CORE
            _cli_mod._PRELOADED_CORE = None   # consume once
        else:
            pkg_instance = OmnipkgCore(config_manager=cm, minimal_mode=use_minimal)

        # ── Build parser ───────────────────────────────────────────────────
        prog_name_lower = Path(sys.argv[0]).name.lower()
        if prog_name_lower == "8pkg" or "8pkg" in sys.argv[0].lower():
            parser = create_8pkg_parser()
        else:
            parser = create_parser()

        args = parser.parse_args(remaining_args)

        # ── Determine interactive mode ────────────────────────────────────
        # is_interactive_session() in common_utils is the single source of truth —
        # every prompt, picker, and wizard calls it directly.  The right override
        # is to set OMNIPKG_NONINTERACTIVE in the environment *before* the first
        # call, so all downstream code — including code that never sees is_interactive
        # as a local variable — automatically gets False.  No arg threading needed.
        _force_noninteractive = (
            global_args.yes
            or global_args.non_interactive
            or os.environ.get("OMNIPKG_NONINTERACTIVE", "0") == "1"
            or os.environ.get("CI", "") != ""
        )
        if _force_noninteractive:
            os.environ["OMNIPKG_NONINTERACTIVE"] = "1"   # propagates to all callsites
        from omnipkg.common_utils import is_interactive_session
        is_interactive = is_interactive_session()        # now reads the env var

        # ── Verbose: same pattern as NONINTERACTIVE ────────────────────────
        # --verbose/-V writes OMNIPKG_VERBOSE=1 into the environment so any
        # module can call is_verbose() without needing the args object.
        if global_args.verbose:
            os.environ["OMNIPKG_VERBOSE"] = "1"

        _dbg(f"is_interactive={is_interactive}  command={args.command}  forced_ni={_force_noninteractive}  verbose={global_args.verbose}")

        args.verbose = global_args.verbose
        args.lang = global_args.lang

        # Propagate global --yes to per-subcommand attributes, but only as a fallback
        # (don't clobber if the subparser already set it from its own -y flag)
        _global_yes = global_args.yes
        for _attr in ("yes", "force"):
            if not getattr(args, _attr, False):
                setattr(args, _attr, _global_yes)

        # Propagate verbose to pkg_instance so core can honour it
        if args.verbose and hasattr(pkg_instance, 'verbose'):
            pkg_instance.verbose = True

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
        sys.stderr.write(f'[CLI-TIMING] main-entry→pre-parser: {(_mt.perf_counter()-_t_main_entry)*1000000:.0f} us\n')

        # ── No command: show help ──────────────────────────────────────────
        # This path is only reachable if cli.main() is called directly (tests,
        # Python import) after the early-exit guard above was somehow bypassed.
        # Keep it consistent: same error + HELP_TEXT + exit-1 as the C fast path.
        if args.command is None:
            from omnipkg._help import HELP_TEXT
            sys.stderr.write("error: no command specified\n\n")
            sys.stdout.write(HELP_TEXT)
            return 2

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
                from omnipkg.i18n import normalize_language_code  # Add this line!
                if args.key == "language":
                    normalized = normalize_language_code(args.value.strip())
                    if normalized is None:
                        safe_print(
                            _("❌ Unknown language '{}'. Run '{} config' to see all options.").format(
                                args.value.strip(), parser.prog
                            )
                        )
                        _print_language_table()
                        return 1
                    cm.set("language", normalized)
                    _.set_language(normalized)
                    os.environ["OMNIPKG_LANG"] = normalized
                    lang_name = SUPPORTED_LANGUAGES.get(normalized, normalized)
                    safe_print(_("✅ Language set to: {} ({})").format(normalized, lang_name))

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

                    # Patch the live preloaded core so the daemon worker picks it up immediately
                    # without a restart — cm.set() wrote to disk but the warm core has a stale dict.
                    try:
                        import omnipkg.cli as _cli_mod
                        _live_core = getattr(_cli_mod, '_PRELOADED_CORE', None)
                        if _live_core is not None:
                            _live_core.config["install_strategy"] = args.value
                        _live_cm = getattr(_cli_mod, '_PRELOADED_CM', None)
                        if _live_cm is not None:
                            _live_cm["install_strategy"] = args.value
                    except Exception:
                        pass
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
        elif args.command == "env":
            _print_env_vars()
            return 0
        elif args.command == "doctor":
            return pkg_instance.doctor(dry_run=args.dry_run, force=args.force, rebuild=args.rebuild)
        elif args.command == "export":
            from omnipkg.integration.reproducible import export_lock
            written_path = export_lock(
                output_path=Path(args.output) if args.output else None,
                pythons=args.pythons,
                venv_root=Path(args.venv_root) if args.venv_root else None,
            )
            safe_print(_("📦 Lock file: {}").format(written_path))

        elif args.command == "sync":
            from omnipkg.integration.reproducible import sync_lock
            sync_lock(
                lock_path=Path(args.lock_file) if args.lock_file else None,
                yes=args.yes,
                pythons=args.pythons,
                dry_run=args.dry_run,
                venv_root=Path(args.venv_root) if args.venv_root else None,
            )
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
            if not args.python_command:
                parser.parse_args(["python", "--help"])
                return 0
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

            elif args.python_command == "swap":
                # Delegate to the same logic as `swap python`
                version = args.version
                _dbg(f"python swap → same path as swap python {version}")
                force = getattr(args, "force", False)
                from omnipkg.dispatcher import resolve_python_path
                python_path = resolve_python_path(version)
                if not python_path.exists():
                    safe_print(_("⚠️  Python {} not found in managed pool.").format(version))
                    should_adopt = force or not is_interactive
                    if is_interactive and not force:
                        ans = safe_input(_("Adopt Python {} now? (Y/n): ").format(version), default="y")
                        should_adopt = ans.lower() in ("", "y", "yes")
                    else:
                        safe_print(_("🤖 Auto-adopting Python {}...").format(version))
                    if not should_adopt:
                        safe_print(_("❌ Adopt first: {} python adopt {}").format(parser.prog, version))
                        return 1
                    result = pkg_instance.adopt_interpreter(version)
                    if result != 0:
                        safe_print(_("❌ Failed to adopt Python {}.").format(version))
                        return 1
                    python_path = resolve_python_path(version)
                if is_interactive:
                    from omnipkg.dispatcher import spawn_swap_shell
                    return spawn_swap_shell(version=version, python_path=python_path, pkg_instance=pkg_instance)
                else:
                    safe_print(_("🐍 Switching active Python context to {} (CI mode)...").format(version))
                    result = pkg_instance.switch_active_python(version)
                    if result == 0:
                        os.environ["OMNIPKG_PYTHON"] = version
                        os.environ["OMNIPKG_ACTIVE_PYTHON"] = version
                        safe_print(_("✅ Context switched to Python {}").format(version))
                    return result

            elif args.python_command == "info":
                # Delegate to the interactive Python explorer (same as `8pkg info python`)
                return _run_python_info_explorer(pkg_instance, is_interactive)

            else:
                parser.print_help()
        elif args.command == "show":
            # pip compat alias for `info`
            return pkg_instance.show_package_info(
                args.package_spec,
                selection=args.selection,
                force=args.force,
            )

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
                    if is_interactive and not getattr(args, 'force', False):
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
                safe_print(_("🎪 Omnipkg version-switching demos:"))
                safe_print(_("1. Rich test (Python module switching)"))
                safe_print(_("2. UV test (binary switching)"))
                safe_print(_("3. NumPy + SciPy (C-extension switching) — needs Python 3.11"))
                safe_print(_("4. TensorFlow (complex dep switching) — needs Python 3.11"))
                safe_print(_("5. 🚀 Multiverse Healing (cross-Python hot-swapping) — needs 3.11"))
                safe_print(_("6. Old Flask Test (legacy package healing) — needs Python 3.8"))
                safe_print(_("7. Script-healing Test (omnipkg run scripts)"))
                safe_print(_("8. 🌠 Quantum Multiverse Warp (concurrent installs) — needs 3.11"))
                safe_print(_("9. Flask Port Finder (auto-healing with Flask)"))
                safe_print(_("10. CLI Healing Test (omnipkg run shell commands)"))
                safe_print(_("\nFor chaos/stress tests: 8pkg stress-test"))

                from omnipkg.common_utils import safe_input

                non_interactive = not is_interactive_session()

                if args.demo_id is not None:
                    if not (1 <= args.demo_id <= 10):
                        safe_print(_("❌ Invalid demo ID {}. Choose 1-10.").format(args.demo_id))
                        return 1
                    response = str(args.demo_id)
                    safe_print(_('🎯 Running demo {}...').format(response))
                elif non_interactive:
                    response = os.environ.get("OMNIPKG_DEMO_ID", "1")
                    safe_print(_('🤖 Non-interactive: auto-selecting demo {}').format(response))
                else:
                    response = safe_input(
                        _("Enter your choice (1-10): "),
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
                }

                if response not in demo_map:
                    safe_print(_("❌ Invalid choice '{}'. Please select 1 through 10.").format(response))
                    return 1

                demo_name, test_file, required_version = demo_map[response]

                if required_version:
                    safe_print(
                        _("\nNOTE: The '{}' demo requires Python {}.").format(demo_name, required_version)
                    )
                    auto_adopt = non_interactive  # already incorporates all NI sources
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
            elif args.yes or not is_interactive:
                # Non-interactive or -y: run test 1 only (not 0 which runs all — unsafe)
                safe_print(_("🤖 Non-interactive: auto-selecting stress test 1"))
                cmd.append("1")

            if getattr(args, "verbose", False):
                cmd.append("--verbose")

            safe_print(_("🌀 Launching Chaos Theory Stress Test..."))
            if hasattr(args, 'tests') and args.tests:
                safe_print(_("   Running tests: {}").format(", ".join(args.tests)))

            return subprocess.call(cmd)

        elif args.command == "install":
            # Fast path: skip subprocess version check when we're already on the
            # configured interpreter (the normal case in a daemon worker).
            _cfg_exe = os.path.realpath(cm.config.get("python_executable") or sys.executable)
            _cur_exe = os.path.realpath(sys.executable)
            if _cfg_exe == _cur_exe:
                original_python_tuple = sys.version_info[:2]
            else:
                original_python_tuple = get_actual_python_version(cm)
            original_python_str = f"{original_python_tuple[0]}.{original_python_tuple[1]}"
            exit_code = 1
            if getattr(args, 'upgrade', False):
                for pkg in args.packages:
                    with temporary_install_strategy(pkg_instance, "latest-active"):
                        pkg_instance.smart_install(packages=[pkg], force_reinstall=True)
                return 0
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
                    # Build extra_flags list from pip-passthrough args
                    _extra_flags = []
                    if getattr(args, "no_deps",         False): _extra_flags.append("--no-deps")
                    if getattr(args, "pre",             False): _extra_flags.append("--pre")
                    if getattr(args, "no_cache_dir",    False): _extra_flags.append("--no-cache-dir")
                    if getattr(args, "ignore_installed",False): _extra_flags.append("--ignore-installed")
                    if getattr(args, "find_links",      None):  _extra_flags += ["-f", args.find_links]
                    if getattr(args, "trusted_host",    None):  _extra_flags += ["--trusted-host", args.trusted_host]
                    if getattr(args, "timeout",         None):  _extra_flags += ["--timeout", str(args.timeout)]
                    if getattr(args, "retries",         None):  _extra_flags += ["--retries", str(args.retries)]
                    if getattr(args, "no_binary",       None):  _extra_flags += ["--no-binary", args.no_binary]
                    if getattr(args, "only_binary",     None):  _extra_flags += ["--only-binary", args.only_binary]

                    exit_code = pkg_instance.smart_install(
                        regular_packages,
                        dry_run=getattr(args, "dry_run", False),
                        force_reinstall=args.force_reinstall,
                        override_strategy=getattr(args, "strategy", None),
                        target_directory=Path(args.target) if getattr(args, "target", None) else None,
                        index_url=args.index_url,
                        extra_index_url=args.extra_index_url,
                        extra_flags=_extra_flags or None,
                    )
                else:
                    exit_code = 0

                return exit_code

            finally:
                # Only check for Python context drift if we might have switched
                # interpreters during install. When configured_exe == sys.executable
                # (the normal daemon fast-path case) the version cannot have changed,
                # so skip the second subprocess call entirely.
                _configured = os.path.realpath(cm.config.get("python_executable") or sys.executable)
                _current    = os.path.realpath(sys.executable)
                if _configured != _current:
                    current_version_after_install_tuple = get_actual_python_version(cm)
                    current_version_after_install_str = (
                        f"{current_version_after_install_tuple[0]}.{current_version_after_install_tuple[1]}"
                    )
                    if original_python_str != current_version_after_install_str:
                        print_header(_('Restoring original Python {} context').format(original_python_str))
                        final_cm = ConfigManager(suppress_init_messages=True)
                        final_pkg_instance = OmnipkgCore(config_manager=final_cm)
                        final_pkg_instance.switch_active_python(original_python_str)

        elif args.command == "install-with-deps":
            safe_print(_("❌ 'install-with-deps' is not yet implemented."))
            safe_print(_("   Use 'install' with explicit version pins for now:"))
            safe_print(_("   8pkg install tensorflow==2.13.0 numpy==1.24.3 protobuf==3.20.3"))
            return 1

        elif args.command == "uninstall":
            regular_packages, python_versions = separate_python_from_packages(args.packages)

            if python_versions:
                safe_print(
                    _("🗑️  Uninstalling Python interpreter(s): {}").format(", ".join(python_versions))
                )
                for version in python_versions:
                    result = pkg_instance.remove_interpreter(
                        version, 
                        force=args.force or not is_interactive
                    )
                    if result != 0:
                        safe_print(_("⚠️  Warning: Failed to remove Python {}").format(version))

            if regular_packages:
                return pkg_instance.smart_uninstall(regular_packages, force=args.force)

            return 0

        elif args.command == "revert":
            return pkg_instance.revert_to_last_known_good(force=args.yes)

        elif args.command == "info":
            if args.package_spec.lower() == "python":
                return _run_python_info_explorer(pkg_instance, is_interactive)
            else:
                return pkg_instance.show_package_info(
                    args.package_spec,
                    selection=args.selection,
                    force=args.force
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
            reset_cmd = getattr(args, "reset_command", None)
            if reset_cmd == "config":
                return pkg_instance.reset_configuration(force=getattr(args, "force", False))
            elif reset_cmd == "kb" or reset_cmd is None:
                # bare 'reset' or 'reset kb' → knowledge base
                return pkg_instance.reset_knowledge_base(force=getattr(args, "force", False))
            else:
                parser.parse_args(["reset", "--help"])
                return 0

        elif args.command == "rebuild-kb":
            # Legacy alias — forward to reset kb
            pkg_instance.rebuild_knowledge_base(force=getattr(args, "force", False))
            return 0

        elif args.command == "reset-config":
            # Legacy alias — forward to reset config
            return pkg_instance.reset_configuration(force=getattr(args, "force", False))

        elif args.command == "daemon":
            from omnipkg.isolation.worker_daemon import (
                cli_start, cli_stop, cli_status, cli_logs, cli_idle_config
            )
            if not args.daemon_command:
                # Print daemon subcommand help by re-parsing with --help
                parser.parse_args(["daemon", "--help"])
                return 0
            if args.daemon_command == "start":
                cli_start()
            elif args.daemon_command == "stop":
                cli_stop()
            elif args.daemon_command == "restart":
                # Guard: if we're already in a re-seat cycle, just do one stop+start and exit.
                if os.environ.get("_OMNIPKG_RESEAT"):
                    safe_print("🔄 Re-seating via C dispatcher...")
                    cli_stop()
                    cli_start()
                    return 0
                safe_print("🔄 Restarting daemon...")
                cli_stop()
                cli_start()
                # Only re-seat if daemon didn't come up cleanly on first attempt
                try:
                    from omnipkg.isolation.worker_daemon import WorkerPoolDaemon as _WPD
                    if not _WPD.is_running():
                        safe_print("🔄 Re-seating via C dispatcher...")
                        cli_stop()
                        cli_start()
                except Exception:
                    pass
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

            if not args.web_command:
                parser.parse_args(["web", "--help"])
                return 0
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
            from .commands.run import execute_run_command
            return execute_run_command(
                args.script_and_args,
                cm,
                verbose=args.verbose,
                omnipkg_core=pkg_instance,
                python_version=getattr(args, "python", None),  # ← add this
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