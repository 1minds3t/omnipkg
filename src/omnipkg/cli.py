from __future__ import annotations  # Python 3.6+ compatibility
"""omnipkg CLI - Enhanced with runtime interpreter switching and language support"""
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
import argparse
from pathlib import Path
import os
import subprocess
import tempfile
import json
import re
from packaging.utils import canonicalize_name
import requests as http_requests
from contextlib import contextmanager
from .i18n import _, SUPPORTED_LANGUAGES
from .core import omnipkg as OmnipkgCore
from .core import ConfigManager
from .common_utils import print_header, run_script_in_omnipkg_env, UVFailureDetector
from .commands.run import execute_run_command
from .common_utils import sync_context_to_runtime
project_root = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).parent.parent / 'tests'
DEMO_DIR = Path(__file__).parent
try:
    FILE_PATH = Path(__file__).resolve()
except NameError:
    FILE_PATH = Path.cwd()

def get_actual_python_version():
    """Get the actual Python version being used by omnipkg, not just sys.version_info."""
    # This function is now silent and clean for production use.
    from omnipkg.core import ConfigManager
    try:
        cm = ConfigManager(suppress_init_messages=True)
        configured_exe = cm.config.get('python_executable')
        if configured_exe:
            version_tuple = cm._verify_python_version(configured_exe)
            if version_tuple:
                return version_tuple[:2]
        return sys.version_info[:2]
    except Exception:
        return sys.version_info[:2]

def debug_python_context(label=""):
    """Print comprehensive Python context information for debugging."""
    print(f"\n{'='*70}")
    safe_print(f"🔍 DEBUG CONTEXT CHECK: {label}")
    print(f"{'='*70}")
    safe_print(f"📍 sys.executable:        {sys.executable}")
    safe_print(f"📍 sys.version:           {sys.version}")
    safe_print(f"📍 sys.version_info:      {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    safe_print(f"📍 os.getpid():           {os.getpid()}")
    safe_print(f"📍 __file__ (if exists):  {__file__ if '__file__' in globals() else 'N/A'}")
    safe_print(f"📍 Path.cwd():            {Path.cwd()}")
    
    # Environment variables that might affect context
    relevant_env_vars = [
        'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
        'OMNIPKG_MAIN_ORCHESTRATOR_PID', 'OMNIPKG_RELAUNCHED',
        'OMNIPKG_LANG', 'PYTHONHOME', 'PYTHONEXECUTABLE'
    ]
    safe_print(f"\n📦 Relevant Environment Variables:")
    for var in relevant_env_vars:
        value = os.environ.get(var, 'NOT SET')
        print(f"   {var}: {value}")
    
    # Check sys.path for omnipkg locations
    safe_print(f"\n📂 sys.path (first 5 entries):")
    for i, path in enumerate(sys.path[:5]):
        print(f"   [{i}] {path}")
    
    print(f"{'='*70}\n")

@contextmanager   
def temporary_install_strategy(core: OmnipkgCore, strategy: str):
    """
    A context manager to temporarily set the install strategy and restore it on exit.
    """
    original_strategy = core.config.get('install_strategy', 'stable-main')
    
    # Only perform the switch if the desired strategy is different from the current one.
    switched = False
    if original_strategy != strategy:
        safe_print(f"   - 🔄 Temporarily switching install strategy to '{strategy}'...")
        # Update both the in-memory config for the current run and the persistent config
        core.config['install_strategy'] = strategy
        core.config_manager.set('install_strategy', strategy)
        switched = True
    
    try:
        # This 'yield' passes control to the code inside the 'with' block
        yield
    finally:
        # This code runs after the 'with' block, guaranteed.
        if switched:
            core.config['install_strategy'] = original_strategy
            core.config_manager.set('install_strategy', original_strategy)
            safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")

def upgrade(args, core):
    """Handler for the upgrade command."""
    package_name = args.package_name[0] if args.package_name else 'omnipkg'

    # Handle self-upgrade as a special case
    if package_name.lower() == 'omnipkg':
        return core.smart_upgrade(
            version=args.version,
            force=args.force,
            skip_dev_check=args.force_dev
        )

    # For all other packages, use the context manager to handle the strategy.
    safe_print(f"🔄 Upgrading '{package_name}' to latest version...")
    with temporary_install_strategy(core, 'latest-active'):
        return core.smart_install(
            packages=[package_name],
            force_reinstall=True
        )

def run_demo_with_enforced_context(
    source_script_path: Path,
    demo_name: str,
    pkg_instance: OmnipkgCore,
    parser_prog: str,
    required_version: str = None
) -> int:
    """
    Run a demo test with enforced Python context.
    
    Args:
        source_script_path: Path to the test script to run
        demo_name: Name of the demo (for display purposes)
        pkg_instance: Initialized OmnipkgCore instance
        parser_prog: Parser program name (for error messages)
        required_version: Optional specific version (e.g., "3.11"). 
                         If None, uses currently detected version.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Detect the actual current Python version
    actual_version = get_actual_python_version()
    
    # Use required version if specified, otherwise use detected version
    target_version_str = required_version if required_version else f"{actual_version[0]}.{actual_version[1]}"
    
    # Validate the source script exists
    if not source_script_path.exists():
        safe_print(f'❌ Error: Source test file {source_script_path} not found.')
        return 1
    
    # Get the Python executable for the target version
    python_exe = pkg_instance.config_manager.get_interpreter_for_version(target_version_str)
    if not python_exe or not python_exe.exists():
        safe_print(f"❌ Python {target_version_str} is not managed by omnipkg.")
        safe_print(f"   Please adopt it first: {parser_prog} python adopt {target_version_str}")
        return 1
    
    safe_print(f'🚀 Running {demo_name} demo with Python {target_version_str} via sterile environment...')
    
    # Create a sterile copy of the script in /tmp to avoid PYTHONPATH contamination
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
        temp_script_path = Path(temp_script.name)
        temp_script.write(source_script_path.read_text(encoding='utf-8'))
    
    safe_print(f"   - Sterile script created at: {temp_script_path}")
    
    try:
        # Execute using the enforced Python context
        return run_demo_with_live_streaming(
            test_file_name=str(temp_script_path),
            demo_name=demo_name,
            python_exe=str(python_exe)
        )
    finally:
        # Always clean up the temporary file
        temp_script_path.unlink(missing_ok=True)

def handle_python_requirement(required_version_str: str, pkg_instance: OmnipkgCore, parser_prog: str) -> bool:
    """
    Checks if the current Python context matches the requirement.
    If not, it automatically finds, adopts (or downloads), and swaps to it.
    """
    actual_version_tuple = get_actual_python_version()
    required_version_tuple = tuple(map(int, required_version_str.split('.')))

    if actual_version_tuple == required_version_tuple:
        return True # We are already in the correct context.

    # --- Start the full healing process ---
    print_header(_('Python Version Requirement'))
    safe_print(_('  - Diagnosis: This operation requires Python {}').format(required_version_str))
    safe_print(_('  - Current Context: Python {}.{}').format(actual_version_tuple[0], actual_version_tuple[1]))
    safe_print(_('  - Action: omnipkg will now attempt to automatically configure the correct interpreter.'))
    
    managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
    
    if required_version_str not in managed_interpreters:
        safe_print(_('\n   - Step 1: Adopting Python {}... (This may trigger a download)').format(required_version_str))
        if pkg_instance.adopt_interpreter(required_version_str) != 0:
            safe_print(_('   - ❌ Failed to adopt Python {}. Cannot proceed with healing.').format(required_version_str))
            return False
        safe_print(_('   - ✅ Successfully adopted Python {}.').format(required_version_str))

    safe_print(_('\n   - Step 2: Swapping active context to Python {}...').format(required_version_str))
    if pkg_instance.switch_active_python(required_version_str) != 0:
        safe_print(_('   - ❌ Failed to swap to Python {}. Please try manually.').format(required_version_str))
        safe_print(_('      Run: {} swap python {}').format(parser_prog, required_version_str))
        return False
        
    safe_print(_('   - ✅ Environment successfully configured for Python {}.').format(required_version_str))
    safe_print(_('🚀 Proceeding...'))
    safe_print('=' * 60)
    return True

def get_version():
    """Get version from package metadata."""
    try:
        from importlib.metadata import version
        return version('omnipkg')
    except Exception:
        try:
            import tomllib
            toml_path = Path(__file__).parent.parent / 'pyproject.toml'
            if toml_path.exists():
                with open(toml_path, 'rb') as f:
                    data = tomllib.load(f)
                    return data.get('project', {}).get('version', 'unknown')
        except (ImportError, Exception):
            pass
    return 'unknown'
VERSION = get_version()

def stress_test_command():
    """Handle stress test command - BLOCK if not Python 3.11."""
    actual_version = get_actual_python_version()
    if actual_version != (3, 11):
        safe_print('=' * 60)
        safe_print(_('  ⚠️  Stress Test Requires Python 3.11'))
        safe_print('=' * 60)
        safe_print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
        safe_print()
        safe_print(_('The omnipkg stress test only works in Python 3.11 environments.'))
        safe_print(_('To run the stress test:'))
        safe_print(_('1. Switch to Python 3.11: omnipkg swap python 3.11'))
        safe_print(_('2. If not available, adopt it first: omnipkg python adopt 3.11'))
        safe_print(_("3. Run 'omnipkg stress-test' from there"))
        safe_print('=' * 60)
        return False
    safe_print('=' * 60)
    safe_print(_('  🚀 omnipkg Nuclear Stress Test - Runtime Version Swapping'))
    safe_print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
    safe_print('=' * 60)
    safe_print(_('🎪 This demo showcases IMPOSSIBLE package combinations:'))
    safe_print(_('   • Runtime swapping between numpy/scipy versions mid-execution'))
    safe_print(_('   • Different numpy+scipy combos (1.24.3+1.12.0 → 1.26.4+1.16.1)'))
    safe_print(_("   • Previously 'incompatible' versions working together seamlessly"))
    safe_print(_('   • Live PYTHONPATH manipulation without process restart'))
    safe_print(_('   • Space-efficient deduplication (shows deduplication - normally'))
    safe_print(_('     we average ~60% savings, but less for C extensions/binaries)'))
    safe_print()
    safe_print(_('🤯 What makes this impossible with traditional tools:'))
    safe_print(_("   • numpy 1.24.3 + scipy 1.12.0 → 'incompatible dependencies'"))
    safe_print(_('   • Switching versions requires environment restart'))
    safe_print(_('   • Dependency conflicts prevent coexistence'))
    safe_print(_("   • Package managers can't handle multiple versions"))
    safe_print()
    safe_print(_('✨ omnipkg does this LIVE, in the same Python process!'))
    safe_print(_('📊 Expected downloads: ~500MB | Duration: 30 seconds - 3 minutes'))
    try:
        response = input(_('🚀 Ready to witness the impossible? (y/n): ')).lower().strip()
    except EOFError:
        response = 'n'
    if response == 'y':
        return True
    else:
        safe_print(_("🎪 Cancelled. Run 'omnipkg stress-test' anytime!"))
        return False

def run_actual_stress_test():
    """Run the actual stress test by locating and executing the test file."""
    safe_print(_('🔥 Starting stress test...'))
    try:
        # Define the correct path to the refactored test file
        test_file_path = TESTS_DIR / 'test_version_combos.py'
        
        # Reuse the robust live streaming runner
        run_demo_with_live_streaming(
            test_file_name=str(test_file_path),
            demo_name="Stress Test"
        )
    except Exception as e:
        safe_print(_('❌ An error occurred during stress test execution: {}').format(e))
        import traceback
        traceback.print_exc()

    
    
def run_demo_with_live_streaming(test_file_name: str, demo_name: str, python_exe: str = None, isolate_env: bool = False):
    """
    (FINAL v3) Run a demo with live streaming.
    - If given an ABSOLUTE path (like a temp file), it uses it directly.
    - If given a RELATIVE name (like a test file), it dynamically locates it.
    - It ALWAYS dynamically determines the correct project root for PYTHONPATH to ensure imports work.
    """
    process = None
    try:
        cm = ConfigManager(suppress_init_messages=True)
        if python_exe:
            effective_python_exe = python_exe
        else:
            effective_python_exe = cm.config.get('python_executable')
            if not effective_python_exe:
                safe_print("⚠️  Warning: Could not find configured Python. Falling back to the host interpreter.")
                effective_python_exe = sys.executable
        
        # --- START: ROBUST PATHING LOGIC ---
        # Step 1: ALWAYS find the project root for the target Python context.
        # This is essential for setting PYTHONPATH so the subprocess can 'import omnipkg'.
        cmd = [
            effective_python_exe, '-c',
            "import omnipkg; from pathlib import Path; print(Path(omnipkg.__file__).resolve().parent.parent)"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        project_root_in_context = Path(result.stdout.strip())
        
        # Step 2: Determine the final path to the SCRIPT to be executed.
        input_path = Path(test_file_name)
        
        if input_path.is_absolute():
            # If we're given an absolute path (like for a temp file), use it directly.
            test_file_path = input_path
        else:
            # Otherwise, assume all standard demo tests are in the 'tests' directory.
            # This is simpler and more reliable than checking filenames.
            test_file_path = project_root_in_context / 'tests' / input_path.name
        # --- END OF NEW LOGIC ---

        safe_print(_('🚀 Running {} demo from source: {}...').format(demo_name.capitalize(), test_file_path))
        
        if not test_file_path.exists():
            safe_print(_('❌ CRITICAL ERROR: Test file not found at: {}').format(test_file_path))
            safe_print(_(' (This can happen if omnipkg is not installed in the target Python environment.)'))
            return 1
        
        safe_print(_('📡 Live streaming output...'))
        safe_print('-' * 60)
        safe_print(f"(Executing with: {effective_python_exe})")
        
        env = os.environ.copy()
        # Step 3: Set PYTHONPATH using the dynamically found project root. This is now always correct.
        if isolate_env:
            env['PYTHONPATH'] = str(project_root_in_context)
            safe_print(" - Running in ISOLATED environment mode.")
        else:
            current_pythonpath = env.get('PYTHONPATH', '')
            env['PYTHONPATH'] = str(project_root_in_context) + os.pathsep + current_pythonpath
        
        # FORCE UNBUFFERED OUTPUT for true live streaming
        env['PYTHONUNBUFFERED'] = '1'
        
        process = subprocess.Popen(
            [effective_python_exe, '-u', str(test_file_path)],  # -u forces unbuffered
            text=True, 
            env=env, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            encoding='utf-8', 
            errors='replace',
            bufsize=0  # Unbuffered
        )
        
        # Force real-time streaming with immediate flush
        while True:
            output = process.stdout.read(1)  # Read one character at a time
            if output == '' and process.poll() is not None:
                break
            if output:
                safe_print(output, end='', flush=True)  # Force flush immediately
        
        returncode = process.wait()
        safe_print('-' * 60)
        
        if returncode == 0:
            safe_print(_('🎉 Demo completed successfully!'))
        else:
            safe_print(_('❌ Demo failed with return code {}').format(returncode))
        
        return returncode
        
    except (Exception, subprocess.CalledProcessError) as e:
        safe_print(_('❌ Demo failed with a critical error: {}').format(e))
        if isinstance(e, subprocess.CalledProcessError):
            safe_print("--- Stderr ---")
            safe_print(e.stderr)
        import traceback
        traceback.print_exc()
        return 1

def create_8pkg_parser():
    """Creates parser for the 8pkg alias (same as omnipkg but with different prog name)."""
    parser = create_parser()
    parser.prog = '8pkg'
    parser.description = _('🚀 The intelligent Python package manager that eliminates dependency hell (8pkg = ∞pkg)')
    epilog_parts = parser.epilog.split('\n')
    updated_epilog = '\n'.join([line.replace('omnipkg', '8pkg') for line in epilog_parts])
    parser.epilog = updated_epilog
    return parser

def create_parser():
    """Creates and configures the argument parser."""
    epilog_parts = [_('🔥 Key Features:'), _('  • Runtime version switching without environment restart'), _('  • Automatic conflict resolution with intelligent bubbling'), _('  • Multi-version package coexistence'), '', _('💡 Quick Start:'), _('  omnipkg install <package>      # Smart install with conflict resolution'), _('  omnipkg list                   # View installed packages and status'), _('  omnipkg info <package>         # Interactive package explorer'), _('  omnipkg demo                   # Try version-switching demos'), _('  omnipkg stress-test            # See the magic in action'), '', _('🛠️ Examples:'), _('  omnipkg install requests numpy>=1.20'), _('  omnipkg install uv==0.7.13 uv==0.7.14  # Multiple versions!'), _('  omnipkg info tensorflow==2.13.0'), _('  omnipkg config set language es'), '', _('Version: {}').format(VERSION)]
    translated_epilog = '\n'.join(epilog_parts)
    parser = argparse.ArgumentParser(prog='omnipkg', description=_('🚀 The intelligent Python package manager that eliminates dependency hell'), formatter_class=argparse.RawTextHelpFormatter, epilog=translated_epilog)
    parser.add_argument('-v', '--version', action='version', version=_('%(prog)s {}').format(VERSION))
    parser.add_argument('--lang', metavar='CODE', help=_('Override the display language for this command (e.g., es, de, ja)'))
    parser.add_argument('--verbose', '-V', action='store_true', help=_('Enable verbose output for detailed debugging'))
    subparsers = parser.add_subparsers(dest='command', help=_('Available commands:'), required=False)    
    install_parser = subparsers.add_parser('install', help=_('Install packages with intelligent conflict resolution'))
    install_parser.add_argument('packages', nargs='*', help=_('Packages to install (e.g., "requests==2.25.1", "numpy>=1.20")'))
    install_parser.add_argument('-r', '--requirement', help=_('Install from requirements file'), metavar='FILE')
    install_with_deps_parser = subparsers.add_parser('install-with-deps', help=_('Install a package with specific dependency versions'))
    install_with_deps_parser.add_argument('package', help=_('Package to install (e.g., "tensorflow==2.13.0")'))
    install_with_deps_parser.add_argument('--dependency', action='append', help=_('Dependency with version (e.g., "numpy==1.24.3")'), default=[])
    uninstall_parser = subparsers.add_parser('uninstall', help=_('Intelligently remove packages and their dependencies'))
    uninstall_parser.add_argument('packages', nargs='+', help=_('Packages to uninstall'))
    uninstall_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation prompts'))
    info_parser = subparsers.add_parser('info', help=_('Interactive package explorer with version management'))
    info_parser.add_argument('package_spec', help=_('Package to inspect (e.g., "requests" or "requests==2.28.1")'))
    revert_parser = subparsers.add_parser('revert', help=_('Revert to last known good environment'))
    revert_parser.add_argument('--yes', '-y', action='store_true', help=_('Skip confirmation'))
    swap_parser = subparsers.add_parser('swap', help=_('Swap Python versions or package environments'))
    swap_parser.add_argument('target', nargs='?', help=_('What to swap (e.g., "python", "python 3.11")'))
    swap_parser.add_argument('version', nargs='?', help=_('Specific version to swap to'))
    list_parser = subparsers.add_parser('list', help=_('View all installed packages and their status'))
    list_parser.add_argument('filter', nargs='?', help=_('Filter packages by name pattern'))
    python_parser = subparsers.add_parser('python', help=_('Manage Python interpreters for the environment'))
    python_subparsers = python_parser.add_subparsers(dest='python_command', help=_('Available subcommands:'), required=True)
    python_adopt_parser = python_subparsers.add_parser('adopt', help=_('Copy or download a Python version into the environment'))
    python_adopt_parser.add_argument('version', help=_('The version to adopt (e.g., "3.9")'))
    python_switch_parser = python_subparsers.add_parser('switch', help=_('Switch the active Python interpreter for this environment'))
    python_switch_parser.add_argument('version', help=_('The version to switch to (e.g., "3.10")'))
    python_rescan_parser = python_subparsers.add_parser('rescan', help=_('Force a re-scan and repair of the interpreter registry'))
    remove_parser = python_subparsers.add_parser('remove', help='Forcefully remove a managed Python interpreter.')
    remove_parser.add_argument('version', help='The version of the managed Python interpreter to remove (e.g., "3.9").')
    remove_parser.add_argument('-y', '--yes', action='store_true', help='Do not ask for confirmation.')
    status_parser = subparsers.add_parser('status', help=_('Environment health dashboard'))
    demo_parser = subparsers.add_parser('demo', help=_('Interactive demo for version switching'))
    stress_parser = subparsers.add_parser('stress-test', help=_('Ultimate demonstration with heavy packages'))
    reset_parser = subparsers.add_parser('reset', help=_('Rebuild the omnipkg knowledge base'))
    reset_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation'))
    rebuild_parser = subparsers.add_parser('rebuild-kb', help=_('Refresh the intelligence knowledge base'))
    rebuild_parser.add_argument('--force', '-f', action='store_true', help=_('Force complete rebuild'))
    reset_config_parser = subparsers.add_parser('reset-config', help=_('Delete config file for fresh setup'))
    reset_config_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation'))
    config_parser = subparsers.add_parser('config', help=_('View or edit omnipkg configuration'))
    config_subparsers = config_parser.add_subparsers(dest='config_command', required=True)
    config_view_parser = config_subparsers.add_parser('view', help=_('Display the current configuration for this environment'))
    config_set_parser = config_subparsers.add_parser('set', help=_('Set a configuration value'))
    config_set_parser.add_argument('key', choices=['language', 'install_strategy'], help=_('Configuration key to set'))
    config_set_parser.add_argument('value', help=_('Value to set for the key'))
    config_reset_parser = config_subparsers.add_parser('reset', help=_('Reset a specific configuration key to its default'))
    config_reset_parser.add_argument('key', choices=['interpreters'], help=_('Configuration key to reset (e.g., interpreters)'))
    doctor_parser = subparsers.add_parser(
        'doctor', 
        help=_('Diagnose and repair a corrupted environment with conflicting package versions.'),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=_("🩺  Finds and removes orphaned package metadata ('ghosts') left behind\n"
                 "   by failed or interrupted installations from other package managers.")
    )
    doctor_parser.add_argument(
        '--dry-run', 
        action='store_true', 
        help=_('Diagnose the environment and show the healing plan without making any changes.')
    )
    doctor_parser.add_argument(
        '--yes', '-y', 
        dest='force', 
        action='store_true', 
        help=_('Automatically confirm and proceed with healing without prompting.')
    )
    heal_parser = subparsers.add_parser(
    'heal',
    help=('Audits the environment for dependency conflicts and attempts to repair them.'),
    formatter_class=argparse.RawTextHelpFormatter,
    epilog=("❤️‍🩹  Automatically resolves version conflicts and installs missing packages\n"
            "   required by your currently installed packages.")
    )
    heal_parser.add_argument(
        '--dry-run',
        action='store_true',
        help=('Show the list of packages that would be installed/reinstalled without making changes.')
    )
    heal_parser.add_argument(
        '--yes', '-y',
        dest='force',
        action='store_true',
        help=('Automatically proceed with healing without prompting.')
    )
    run_parser = subparsers.add_parser('run', help=_('Run a script with auto-healing for version conflicts'))
    run_parser.add_argument('script_and_args', nargs=argparse.REMAINDER, help=_('The script to run, followed by its arguments'))
    prune_parser = subparsers.add_parser('prune', help=_('Clean up old, bubbled package versions'))
    prune_parser.add_argument('package', help=_('Package whose bubbles to prune'))
    prune_parser.add_argument('--keep-latest', type=int, metavar='N', help=_('Keep N most recent bubbled versions'))
    prune_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation'))
    upgrade_parser = subparsers.add_parser('upgrade', help=_('Upgrade omnipkg or other packages to the latest version'))
    upgrade_parser.add_argument('package_name', nargs='*', default=['omnipkg'], help='Package to upgrade (defaults to omnipkg itself)')
    upgrade_parser.add_argument('--version', help='(For omnipkg self-upgrade only) Specify a target version')
    upgrade_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation prompt'))
    upgrade_parser.add_argument('--force-dev', action='store_true', help=_('Force upgrade even in a developer environment (use with caution)'))
    upgrade_parser.set_defaults(func=upgrade)  # CRITICAL: This connects the handler!
    return parser

def print_header(title):
    """Print a formatted header."""
    safe_print('\n' + '=' * 60)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 60)

def create_healing_wrapper_script(command_args: list, healing_plan: list, config: dict, entry_point: dict) -> Path:
    """
    Creates a script that executes a command using a pre-resolved entry point from the KB,
    eliminating the need for discovery inside the hostile, cloaked environment.
    """
    import textwrap

    command_name = command_args[0]
    command_argv = command_args[1:]

    script_content = f"""
import sys, os, json, traceback, runpy, shutil
from pathlib import Path

# Basic setup to get omnipkg loader
project_root_path = r"{str(project_root)}"
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)

try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.common_utils import safe_print
except ImportError as e:
    print(f"FATAL WRAPPER ERROR: Could not import omnipkg. {{e}}")
    sys.exit(127)

# --- INJECTED DATA FROM THE KB ---
config = json.loads(r'''{json.dumps(config)}''')
healing_plan = {healing_plan!r}
command_name = "{command_name}"
command_argv = {command_argv!r}
entry_point_data = {json.dumps(entry_point)}
# --- NO MORE DISCOVERY ---

activated_loaders = []
TRUE_ORIGINAL_SYS_PATH = list(sys.path)

try:
    # Activate all the necessary package versions
    loaders_to_activate = [
        omnipkgLoader(spec, config=config, isolation_mode='overlay', _original_sys_path=TRUE_ORIGINAL_SYS_PATH)
        for spec in healing_plan
    ]
    for loader in loaders_to_activate:
        loader.__enter__()
        activated_loaders.append(loader)

    safe_print("--- Environment Ready. Executing Command via Pre-Resolved Entry Point ---")
    
    # Directly use the data passed from the KB. No discovery. No bullshit.
    module_str = entry_point_data['module']
    safe_print(f"   - Resolved '{{command_name}}' to '{{module_str}}' from KB.")
    
    sys.argv = [command_name] + command_argv
    runpy.run_module(module_str, run_name='__main__')

except Exception as e:
    safe_print(f"❌ A fatal error occurred: {{e}}")
    traceback.print_exc()
    sys.exit(1)

finally:
    # Cleanly deactivate all loaders
    for loader in reversed(activated_loaders):
        try:
            loader.__exit__(None, None, None)
        except Exception: pass
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', prefix='8pkg_heal_', delete=False, encoding='utf-8') as f:
        f.write(textwrap.dedent(script_content))
        return Path(f.name)

def analyze_error_for_specs(error_text: str) -> list[str]:
    """Extract specific package version requirements from error messages."""
    specs = set()
    # Match "requires X.Y.Z" pattern (without trailing period)
    match = re.search(r"pydantic version, which requires ([\d]+(?:\.\d+)*)", error_text)
    if match:
        version = match.group(1)
        spec = f"pydantic-core=={version}"
        specs.add(spec)
        safe_print(f"   - Found specific fix from error: {spec}")
    return list(specs)

def get_all_transitive_dependencies(package_name: str, package_version: str = None, 
                                     max_depth: int = 10) -> set:
    """
    Gets ALL dependencies (direct + transitive) for a package from the live environment.
    """
    import importlib.metadata
    
    def _recurse(pkg_name: str, visited: set, depth: int) -> set:
        if depth > max_depth or canonicalize_name(pkg_name) in visited:
            return set()
        
        visited.add(canonicalize_name(pkg_name))
        all_deps = set()
        
        try:
            pkg_meta = importlib.metadata.metadata(pkg_name)
            reqs = pkg_meta.get_all('Requires-Dist') or []
            
            for req in reqs:
                if 'extra ==' in req: continue
                
                match = re.match(r'^([a-zA-Z0-9\-_.]+)', req)
                if not match: continue
                    
                dep_name = match.group(1)
                
                try:
                    dep_version = importlib.metadata.version(dep_name)
                    dep_spec = f"{canonicalize_name(dep_name)}=={dep_version}"
                    all_deps.add(dep_spec)
                    all_deps.update(_recurse(dep_name, visited, depth + 1))
                except importlib.metadata.PackageNotFoundError:
                    continue
                    
        except Exception as e:
            safe_print(f"   ⚠️  [Transitive Dep] Could not analyze {pkg_name}: {e}")
        
        return all_deps
    
    all_deps = _recurse(package_name, set(), depth=0)
    
    if package_version:
        all_deps.add(f"{canonicalize_name(package_name)}=={package_version}")
    
    return all_deps

def build_comprehensive_healing_plan(owner_package_name: str, owner_version: str, 
                                      reactive_overrides: list) -> list:
    """
    Builds a complete healing plan with ALL transitive dependencies from the live env.
    """
    safe_print(f"💡 Proactive: Command owned by '{owner_package_name}=={owner_version}'. Loading its full dependency tree.")
    
    all_deps = get_all_transitive_dependencies(owner_package_name, owner_version)
    safe_print(f"   🌳 Discovered {len(all_deps)} total packages (direct + transitive).")
    
    final_plan_map = {}
    for spec in all_deps:
        name, version = spec.split('==', 1)
        final_plan_map[canonicalize_name(name)] = spec
    
    if reactive_overrides:
        safe_print("💡 Reactive: Applying specific fixes from error analysis...")
        for spec in reactive_overrides:
            name, version = spec.split('==', 1)
            c_name = canonicalize_name(name)
            if c_name in final_plan_map and final_plan_map[c_name] != spec:
                safe_print(f"   - Overriding {final_plan_map[c_name]} with reactive fix: {spec}")
            else:
                safe_print(f"   - Applying reactive fix: {spec}")
            final_plan_map[c_name] = spec
    
    return sorted(list(final_plan_map.values()))
    
def main():
    """Main application entry point with pre-flight version check."""
    try:
        # --- START: ROBUST PRE-PARSING LOGIC ---
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument('--lang', default=None)
        global_parser.add_argument('--verbose', '-V', action='store_true')
        
        global_args, remaining_args = global_parser.parse_known_args()

        # Handle version check separately
        if '-v' in remaining_args or '--version' in remaining_args:
            prog_name = Path(sys.argv[0]).name
            if prog_name == '8pkg' or (len(sys.argv) > 0 and '8pkg' in sys.argv[0]):
                safe_print(_('8pkg {}').format(get_version()))
            else:
                safe_print(_('omnipkg {}').format(get_version()))
            return 0
        
        # --- NEW: EXTRACT COMMAND BEFORE FULL PARSING ---
        # We need to know the command to decide minimal vs full init
        command = remaining_args[0] if remaining_args and not remaining_args[0].startswith('-') else None
        
        # --- END: ROBUST PRE-PARSING LOGIC ---
        
        cm = ConfigManager()
        
        # Set language based on pre-scanned flag or config
        user_lang = global_args.lang or cm.config.get('language')
        if user_lang:
            _.set_language(user_lang)

        # --- NEW: DECIDE MINIMAL VS FULL INITIALIZATION ---
        # Commands that only need config + interpreter manager (no cache/database)
        minimal_commands = {'swap', 'config', 'python'}
        use_minimal = command in minimal_commands
        
        pkg_instance = OmnipkgCore(config_manager=cm, minimal_mode=use_minimal)
        # --- END NEW LOGIC ---
        
        prog_name = Path(sys.argv[0]).name
        if prog_name == '8pkg' or (len(sys.argv) > 0 and '8pkg' in sys.argv[0]):
            parser = create_8pkg_parser()
        else:
            parser = create_parser()

        # Now parse fully
        args = parser.parse_args(remaining_args)

        # Manually add the pre-scanned global flags
        args.verbose = global_args.verbose
        args.lang = global_args.lang
        if args.command is None:
            parser.print_help()
            safe_print(_('\n👋 Welcome back to omnipkg! Run a command or see --help for details.'))
            return 0
        if args.command == 'config':
            if args.config_command == 'view':
                print_header('omnipkg Configuration')
                for key, value in sorted(cm.config.items()):
                    safe_print(_('  - {}: {}').format(key, value))
                return 0
            elif args.config_command == 'set':
                if args.key == 'language':
                    if args.value not in SUPPORTED_LANGUAGES:
                        safe_print(_("❌ Error: Language '{}' not supported. Supported: {}").format(args.value, ', '.join(SUPPORTED_LANGUAGES.keys())))
                        return 1
                    cm.set('language', args.value)
                    _.set_language(args.value)
                    lang_name = SUPPORTED_LANGUAGES.get(args.value, args.value)
                    safe_print(_('✅ Language permanently set to: {lang}').format(lang=lang_name))
                elif args.key == 'install_strategy':
                    valid_strategies = ['stable-main', 'latest-active']
                    if args.value not in valid_strategies:
                        safe_print(_('❌ Error: Invalid install strategy. Must be one of: {}').format(', '.join(valid_strategies)))
                        return 1
                    cm.set('install_strategy', args.value)
                    safe_print(_('✅ Install strategy permanently set to: {}').format(args.value))
                else:
                    parser.print_help()
                    return 1
                return 0
            elif args.config_command == 'reset':
                if args.key == 'interpreters':
                    safe_print(_('Resetting managed interpreters registry...'))
                    return pkg_instance.rescan_interpreters()
                return 0
            parser.print_help()
            return 1
        elif args.command == 'doctor':
            return pkg_instance.doctor(dry_run=args.dry_run, force=args.force)
        elif args.command == 'heal':
            # Use the context manager to wrap the call to the core heal logic.
            with temporary_install_strategy(pkg_instance, 'latest-active'):
                return pkg_instance.heal(dry_run=args.dry_run, force=args.force)
        elif args.command == 'list':
            if args.filter and args.filter.lower() == 'python':
                interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
                discovered = pkg_instance.config_manager.list_available_pythons()
                print_header('Managed Python Interpreters')
                if not interpreters:
                    safe_print('   No interpreters are currently managed by omnipkg for this environment.')
                else:
                    for ver, path in sorted(interpreters.items()):
                        safe_print(_('   • Python {}: {}').format(ver, path))
                print_header('Discovered System Interpreters')
                safe_print("   (Use 'omnipkg python adopt <version>' to make these available for swapping)")
                for ver, path in sorted(discovered.items()):
                    if ver not in interpreters:
                        safe_print(_('   • Python {}: {}').format(ver, path))
                return 0
            else:
                return pkg_instance.list_packages(args.filter)
        elif args.command == 'python':
            if args.python_command == 'adopt':
                return pkg_instance.adopt_interpreter(args.version)
            elif args.python_command == 'rescan':
                return pkg_instance.rescan_interpreters()
            elif args.python_command == 'remove':
                return pkg_instance.remove_interpreter(args.version, force=args.yes)
            elif args.python_command == 'switch':
                return pkg_instance.switch_active_python(args.version)
            else:
                parser.print_help()
                return 1
        elif args.command == 'upgrade':
            return upgrade(args, pkg_instance)
        elif args.command == 'swap':
            if not args.target:
                safe_print(_('❌ Error: You must specify what to swap.'))
                safe_print(_('Examples:'))
                safe_print(_('  {} swap python           # Interactive Python version picker').format(parser.prog))
                safe_print(_('  {} swap python 3.11      # Switch to Python 3.11').format(parser.prog))
                return 1
            if args.target.lower() == 'python':
                if args.version:
                    return pkg_instance.switch_active_python(args.version)
                else:
                    interpreters = pkg_instance.config_manager.list_available_pythons()
                    if not interpreters:
                        safe_print(_('❌ No Python interpreters found.'))
                        return 1
                    safe_print(_('🐍 Available Python versions:'))
                    versions = sorted(interpreters.keys())
                    for i, ver in enumerate(versions, 1):
                        safe_print(_('  {}. Python {}').format(i, ver))
                    try:
                        choice = input(_('Select version (1-{}): ').format(len(versions))).strip()
                        if choice.isdigit() and 1 <= int(choice) <= len(versions):
                            selected_version = versions[int(choice) - 1]
                            return pkg_instance.switch_active_python(selected_version)
                        else:
                            safe_print(_('❌ Invalid selection.'))
                            return 1
                    except (EOFError, KeyboardInterrupt):
                        safe_print(_('\n❌ Operation cancelled.'))
                        return 1
            else:
                safe_print(_("❌ Error: Unknown swap target '{}'. Currently supported: python").format(args.target))
                return 1
        elif args.command == 'status':
            return pkg_instance.show_multiversion_status()
        elif args.command == 'demo':
            # --- [ STEP 1: Store the original state ] ---
            original_python_tuple = get_actual_python_version()
            original_python_str = f"{original_python_tuple[0]}.{original_python_tuple[1]}"
            
            try:
                # --- [ STEP 2: The entire existing demo logic runs here ] ---
                safe_print(_('Current Python version: {}.{}').format(original_python_tuple[0], original_python_tuple[1]))
                safe_print(_('🎪 Omnipkg supports version switching for:'))
                safe_print(_('   • Python modules (e.g., rich)'))
                safe_print(_('   • Binary packages (e.g., uv)'))
                safe_print(_('   • C-extension packages (e.g., numpy, scipy)'))
                safe_print(_('   • Complex dependency packages (e.g., TensorFlow)'))
                safe_print(_('\nSelect a demo to run:'))
                safe_print(_('1. Rich test (Python module switching)'))
                safe_print(_('2. UV test (binary switching)'))
                safe_print(_('3. NumPy + SciPy stress test (C-extension switching)'))
                safe_print(_('4. TensorFlow test (complex dependency switching)'))
                safe_print(_('5. 🚀 Multiverse Healing Test (Cross-Python Hot-Swapping Mid-Script)'))
                safe_print(_('6. Old Flask Test (legacy package healing) - Fully functional!'))
                safe_print(_('7. Auto-healing Test (omnipkg run)'))
                safe_print(_('8. 🌠 Quantum Multiverse Warp (Concurrent Python Installations)'))
                safe_print(_('9. Flask Port Finder Test (auto-healing with Flask)'))
                
                try:
                    response = input(_('Enter your choice (1-9): ')).strip()
                except EOFError:
                    response = ''
                
                demo_map = {
                    '1': ('Rich Test', TESTS_DIR / 'test_rich_switching.py', None),
                    '2': ('UV Test', TESTS_DIR / 'test_uv_switching.py', None),
                    '3': ('NumPy/SciPy Test', TESTS_DIR / 'test_version_combos.py', '3.11'),
                    '4': ('TensorFlow Test', TESTS_DIR / 'test_tensorflow_switching.py', '3.11'),
                    '5': ('Multiverse Healing', TESTS_DIR / 'test_multiverse_healing.py', '3.11'),
                    '6': ('Old Flask Test', TESTS_DIR / 'test_old_flask.py', '3.8'),
                    '7': ('Auto-healing Test', TESTS_DIR / 'test_old_rich.py', None),
                    '8': ('Quantum Multiverse Warp', TESTS_DIR / 'test_concurrent_install.py', '3.11'),
                    '9': ('Flask Port Finder', TESTS_DIR / 'test_flask_port_finder.py', None),
                }

                if response not in demo_map:
                    safe_print(_('❌ Invalid choice. Please select 1 through 9.'))
                    return 1

                demo_name, test_file, required_version = demo_map[response]

                if required_version:
                    safe_print(f"\nNOTE: The '{demo_name}' demo requires Python {required_version}.")
                    if not handle_python_requirement(required_version, pkg_instance, parser.prog):
                        return 1
                
                if not test_file or not test_file.exists():
                    safe_print(_('❌ Error: Test file {} not found.').format(test_file))
                    return 1
                
                # After any potential swap, get the correct python exe for the command
                configured_python_exe = pkg_instance.config_manager.config.get('python_executable', sys.executable)

                safe_print(_('🚀 This demo uses "omnipkg run" to showcase its auto-healing capabilities.'))
                
                cmd = [configured_python_exe, '-m', 'omnipkg.cli', 'run', str(test_file)]
                
                process = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
                for line in process.stdout:
                    safe_print(line, end='')
                returncode = process.wait()

                safe_print('-' * 60)
                if returncode == 0:
                    safe_print(_('🎉 Demo completed successfully!'))
                else:
                    safe_print(_('❌ Demo failed with return code {}').format(returncode))
                return returncode

            finally:
                # --- [ STEP 3: ALWAYS restore the original state ] ---
                # Check what the context is *now*, after the demo has run.
                current_version_after_demo_tuple = get_actual_python_version()
                current_version_after_demo_str = f"{current_version_after_demo_tuple[0]}.{current_version_after_demo_tuple[1]}"

                # Only restore if the context was actually changed.
                if original_python_str != current_version_after_demo_str:
                    print_header(f"Restoring original Python {original_python_str} context")
                    pkg_instance.switch_active_python(original_python_str)
                    
        elif args.command == 'stress-test':
            if stress_test_command():
                run_actual_stress_test()
            return 0
        elif args.command == 'install':
            # --- [ START: "Return to Origin" Logic ] ---
            # Store the original Python context before the operation begins.
            original_python_tuple = get_actual_python_version()
            original_python_str = f"{original_python_tuple[0]}.{original_python_tuple[1]}"
            exit_code = 1 # Default to failure

            try:
                # This is the original logic to determine which packages to install
                packages_to_process = []
                if args.requirement:
                    req_path = Path(args.requirement)
                    if not req_path.is_file():
                        safe_print(_("❌ Error: Requirements file not found at '{}'").format(req_path))
                        return 1
                    safe_print(_('📄 Reading packages from {}...').format(req_path.name))
                    with open(req_path, 'r') as f:
                        packages_to_process = [line.split('#')[0].strip() for line in f if line.split('#')[0].strip()]
                elif args.packages:
                    packages_to_process = args.packages
                else:
                    parser.parse_args(['install', '--help'])
                    return 1
                
                # Execute the core install logic and store its exit code
                exit_code = pkg_instance.smart_install(packages_to_process)
                return exit_code

            finally:
                # This block ALWAYS runs, ensuring we return to the user's original context.
                current_version_after_install_tuple = get_actual_python_version()
                current_version_after_install_str = f"{current_version_after_install_tuple[0]}.{current_version_after_install_tuple[1]}"

                if original_python_str != current_version_after_install_str:
                    # The context was changed by Quantum Healing, so we switch it back.
                    print_header(f"Restoring original Python {original_python_str} context")
                    
                    # We must create a new OmnipkgCore instance because the config
                    # was modified during the swap. This ensures we are acting on the
                    # most current state before switching back.
                    final_cm = ConfigManager(suppress_init_messages=True)
                    final_pkg_instance = OmnipkgCore(config_manager=final_cm)
                    final_pkg_instance.switch_active_python(original_python_str)
            # --- [ END: "Return to Origin" Logic ] ---
        elif args.command == 'install-with-deps':
            packages_to_process = [args.package] + args.dependency
            return pkg_instance.smart_install(packages_to_process)
        elif args.command == 'uninstall':
            return pkg_instance.smart_uninstall(args.packages, force=args.force)
        elif args.command == 'revert':
            return pkg_instance.revert_to_last_known_good(force=args.yes)
        elif args.command == 'info':
            if args.package_spec.lower() == 'python':
                configured_active_exe = pkg_instance.config.get('python_executable')
                active_version_tuple = pkg_instance.config_manager._verify_python_version(configured_active_exe)
                active_version_str = f'{active_version_tuple[0]}.{active_version_tuple[1]}' if active_version_tuple else None
                print_header(_('Python Interpreter Information'))
                managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
                safe_print(_('🐍 Managed Python Versions (available for swapping):'))
                for ver, path in sorted(managed_interpreters.items()):
                    marker = ' ⭐ (currently active)' if active_version_str and ver == active_version_str else ''
                    safe_print(_('   • Python {}: {}{}').format(ver, path, marker))
                if active_version_str:
                    safe_print(_('\n🎯 Active Context: Python {}').format(active_version_str))
                    safe_print(_('📍 Configured Path: {}').format(configured_active_exe))
                else:
                    safe_print('\n⚠️ Could not determine active Python version from config.')
                safe_print(_('\n💡 To switch context, use: {} swap python <version>').format(parser.prog))
                return 0
            else:
                return pkg_instance.show_package_info(args.package_spec)
        elif args.command == 'list':
            return pkg_instance.list_packages(args.filter)
        elif args.command == 'status':
            return pkg_instance.show_multiversion_status()
        elif args.command == 'prune':
            return pkg_instance.prune_bubbled_versions(args.package, keep_latest=args.keep_latest, force=args.force)
        elif args.command == 'reset':
            return pkg_instance.reset_knowledge_base(force=args.force)
        elif args.command == 'rebuild-kb':
            pkg_instance.rebuild_knowledge_base(force=args.force)
            return 0
        # START REPLACING FROM HERE
        # In cli.py, inside the main() function

        elif args.command == 'run':
            if not args.script_and_args:
                parser.print_help()
                return 1

            first_arg = args.script_and_args[0]
            is_script = first_arg.endswith('.py') and Path(first_arg).is_file()

            if is_script:
                return execute_run_command(args.script_and_args, cm, verbose=args.verbose)

            command_name = Path(first_arg).name
            temp_script = None
            try:
                # This direct run is for the happy path and for capturing the initial failure
                process = subprocess.run(args.script_and_args, capture_output=True, text=True, encoding='utf-8', errors='replace')
                full_output = process.stdout + process.stderr
                print(full_output, end='')
                if process.returncode == 0:
                    return 0

                # --- START OF THE REAL FIX ---
                # The command failed, so now we use the KB to build the healing script.
                safe_print(_("\n⚠️  Command failed. Looking up owner package in Knowledge Base..."))
                
                # 1. GET THE ENTRY POINT DATA FROM THE KB (THE SMART PART)
                owner_package_info = pkg_instance.find_package_by_command(command_name)
                if not owner_package_info:
                    safe_print(f"❌ Could not find an owner for '{command_name}' in the KB. Cannot heal.")
                    return process.returncode

                # --- THIS IS THE FUCKING FIX ---
                # Use .get() with the CORRECT CAPITALIZED keys from the KB.
                owner_name = owner_package_info.get('Name')
                owner_version = owner_package_info.get('Version')

                # Add a sanity check in case the KB data is corrupted.
                if not owner_name or not owner_version:
                    safe_print(f"❌ KB data for command '{command_name}' is corrupted (missing Name/Version). Please rebuild the KB with '8pkg rebuild-kb'.")
                    return 1
                # --- END OF THE FUCKING FIX ---

                safe_print(f"   - Command owned by '{owner_name}'. Building comprehensive healing plan...")

                # --- START OF THE FUCKING FIX ---
                # This is the logic that was missing. It extracts the entry point data
                # from the dictionary we already fetched from the KB.

                entry_point_data = None
                entry_points_json_str = owner_package_info.get('entry_points')

                if not entry_points_json_str:
                    safe_print(f"❌ KB data for '{owner_name}' is corrupted (missing 'entry_points' field). Please rebuild KB.")
                    return 1
                try:
                    entry_points_list = json.loads(entry_points_json_str)
                    for ep in entry_points_list:
                        if ep.get('name') == command_name:
                            entry_point_data = ep
                            break
                except (json.JSONDecodeError, TypeError):
                    safe_print(f"❌ KB data for '{owner_name}' is corrupted (invalid JSON in 'entry_points'). Please rebuild KB.")
                    return 1

                if not entry_point_data:
                    safe_print(f"❌ KB data for '{owner_name}' is missing the entry point definition for '{command_name}'. Please rebuild KB.")
                    return 1
                # --- END OF THE FUCKING FIX ---

                reactive_plan_set = set()
                pydantic_conflict_match = re.search(
                    r"SystemError: The installed pydantic-core version.*?is incompatible with the current pydantic version, which requires ([\d\.]+)",
                    full_output
                )
                if pydantic_conflict_match:
                    required_core_version = pydantic_conflict_match.group(1).rstrip('.')
                    
                    # YOUR CORRECT MAPPING LOGIC
                    pydantic_core_to_pydantic_map = {
                        '2.41.5': '2.12.4',
                        '2.41.4': '2.12.3',
                        '2.41.2': '2.12.2',
                        '2.41.1': '2.12.1',
                        '2.41.0': '2.12.0',
                        '2.40.1': '2.11.1',
                        '2.40.0': '2.11.0',
                    }
                    compatible_pydantic_version = pydantic_core_to_pydantic_map.get(required_core_version)

                    if compatible_pydantic_version:
                        safe_print(f"\n💡 Reactive: Pydantic/Core conflict requires a matched pair.")
                        safe_print(f"   - For pydantic-core=={required_core_version}, the correct partner is pydantic=={compatible_pydantic_version}.")
                        # Add BOTH packages to the plan to treat them as an atomic unit.
                        reactive_plan_set.add(f"pydantic-core=={required_core_version}")
                        reactive_plan_set.add(f"pydantic=={compatible_pydantic_version}")
                    else:
                        safe_print(f"   - ⚠️  Unknown pydantic-core mapping for {required_core_version}. Adding only core to plan.")
                        reactive_plan_set.add(f"pydantic-core=={required_core_version}")
                else:
                    # Fallback for non-pydantic errors
                    reactive_plan_set.update(analyze_error_for_specs(full_output))
                # --- END OF THE FUCKING FIX ---

                # Build the complete plan using the CORRECT reactive overrides.
                final_plan = build_comprehensive_healing_plan(
                    owner_package_name=owner_package_info['Name'],
                    owner_version=owner_package_info['Version'],
                    reactive_overrides=list(reactive_plan_set)
                )
                if not final_plan:
                    safe_print("❌ Failed to build a valid healing plan. Aborting.")
                    return process.returncode

                # --- THE FINAL BOSS FIX ---
                # As you correctly diagnosed, if 'pydantic' is in the healing plan,
                # we MUST NOT also try to activate a separate 'pydantic-core' bubble.
                # The pydantic bubble is a self-contained unit and includes the correct core.
                # This surgical removal prevents the loader conflict.
                has_pydantic_in_plan = any('pydantic==' in spec for spec in final_plan)
                if has_pydantic_in_plan:
                    plan_before_count = len(final_plan)
                    # Create a new plan that EXCLUDES any pydantic-core entry.
                    final_plan = [spec for spec in final_plan if not spec.startswith('pydantic-core==')]
                    plan_after_count = len(final_plan)
                    
                    if plan_after_count < plan_before_count:
                        safe_print("   - 💡 Adjusting plan: Removing separate 'pydantic-core' bubble to rely on the one nested inside the 'pydantic' bubble.")
                # --- END OF THE FINAL BOSS FIX ---

                safe_print(f"   - Verifying and building sandbox with {len(final_plan)} packages...")

                # --- THIS IS THE FUCKING FIX ---
                # Wrap the entire bubble creation process in a context that forces the 'stable-main'
                # strategy. This PREVENTS corruption of the main environment by ensuring that
                # smart_install creates bubbles instead of overwriting active packages.
                with temporary_install_strategy(pkg_instance, 'stable-main'):
                    for spec in final_plan:
                        if '==' in spec:
                            pkg_name, pkg_version = spec.split('==', 1)
                            bubble_dir_name = f'{canonicalize_name(pkg_name)}-{pkg_version}'
                            bubble_path = Path(cm.config['multiversion_base']) / bubble_dir_name
                            
                            if not bubble_path.is_dir():
                                safe_print(f"   💡 Missing bubble detected: {spec}")
                                safe_print(f"   🚀 Auto-installing bubble with 'stable-main' policy...")
                                
                                if pkg_instance.smart_install([spec]) != 0:
                                    safe_print(f"\n❌ Auto-install failed for {spec}. Aborting heal.")
                                    return 1
                                
                                safe_print(f"\n   ✅ Bubble installed successfully: {spec}")
                # --- END OF THE FUCKING FIX ---

                safe_print("-" * 60)
                safe_print("🚀 All required bubbles are ready. Re-running command inside the healed sandbox...")

                temp_script = create_healing_wrapper_script(
                    command_args=args.script_and_args,
                    healing_plan=final_plan,
                    config=pkg_instance.config,
                    entry_point=entry_point_data
                )
                
                # 3. EXECUTE THE WRAPPER, WHICH NO LONGER NEEDS TO DISCOVER ANYTHING
                safe_print("-" * 60)
                safe_print("🚀 Re-running command inside the healed sandbox (with pre-resolved entry point)...")
                python_exe = pkg_instance.config.get('python_executable', sys.executable)
                result = subprocess.run([python_exe, str(temp_script)])
                
                if result.returncode == 0:
                    safe_print("\n✅ Command completed successfully in healed environment.")
                else:
                    safe_print(f"\n❌ Command failed even inside the healed environment (exit code: {result.returncode}).")
                return result.returncode
                # --- END OF THE REAL FIX ---
            finally:
                if temp_script and temp_script.exists():
                    temp_script.unlink(missing_ok=True)
        
        else:
            parser.print_help()
            return 1
            
    except KeyboardInterrupt:
        safe_print(_('\n❌ Operation cancelled by user.'))
        return 1
    except Exception as e:
        safe_print(_('\n❌ An unexpected error occurred: {}').format(e))
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())