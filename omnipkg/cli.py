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
import requests as http_requests
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
    try:
        cm = ConfigManager()
        configured_exe = cm.config.get('python_executable')
        if configured_exe:
            version_tuple = cm._verify_python_version(configured_exe)
            if version_tuple:
                return version_tuple[:2]
        return sys.version_info[:2]
    except Exception:
        return sys.version_info[:2]
    
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

    # For non-omnipkg packages: temporarily switch strategy, install, then restore
    original_strategy = core.config.get('install_strategy', 'stable-main')
    
    try:
        if original_strategy != 'latest-active':
            safe_print(f"   - 🔄 Temporarily switching to 'latest-active' strategy...")
            core.config_manager.set('install_strategy', 'latest-active')
            # Also update the in-memory config so smart_install sees it
            core.config['install_strategy'] = 'latest-active'
        
        safe_print(f"🔄 Upgrading '{package_name}' to latest version...")
        
        return core.smart_install(
            packages=[package_name],
            force_reinstall=True
        )
    
    finally:
        # ALWAYS restore the original strategy
        if original_strategy != 'latest-active':
            core.config_manager.set('install_strategy', original_strategy)
            core.config['install_strategy'] = original_strategy
            safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")

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
    Checks if the current Python version matches the requirement.
    If not, it automatically finds, adopts (or downloads), and swaps to it.
    """
    # Get the true version of the currently configured Python context
    actual_version_tuple = get_actual_python_version()
    required_version_tuple = tuple(map(int, required_version_str.split('.')))

    # If we are already in the correct context, we're done.
    if actual_version_tuple == required_version_tuple:
        return True

    # --- Start the full healing process ---
    print_header(_('Python Version Requirement'))
    safe_print(_('  - Diagnosis: This operation requires Python {}').format(required_version_str))
    safe_print(_('  - Current Context: Python {}.{}').format(actual_version_tuple[0], actual_version_tuple[1]))
    safe_print(_('  - Action: omnipkg will now attempt to automatically configure the correct interpreter.'))
    
    # Check if the required version is already managed by omnipkg
    managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
    
    if required_version_str not in managed_interpreters:
        #
        # >>>>>>>> THIS IS THE CRITICAL NEW LOGIC <<<<<<<<
        #
        # The required version is NOT managed. We must now run the full adopt
        # process, which will either find it locally or download it.
        safe_print(_('\n   - Step 1: Adopting Python {}... (This may trigger a download)').format(required_version_str))
        
        # Call the core adopt_interpreter method. This is the robust function that
        # contains the full logic: check local system -> fallback to download.
        if pkg_instance.adopt_interpreter(required_version_str) != 0:
            safe_print(_('   - ❌ Failed to adopt Python {}. Cannot proceed with healing.').format(required_version_str))
            return False
        
        safe_print(_('   - ✅ Successfully adopted Python {}.').format(required_version_str))
        #
        # >>>>>>>> END OF CRITICAL NEW LOGIC <<<<<<<<
        #

    # By this point, we are GUARANTEED that the interpreter is managed. Now we can swap.
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
        effective_python_exe = python_exe or cm.config.get('python_executable', sys.executable)
        
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
    upgrade_parser.add_argument('package', nargs='?', default='omnipkg', help=_('Package to upgrade (defaults to omnipkg itself)'))
    upgrade_parser.add_argument('--version', help=_('Specify a version to upgrade/downgrade to'))
    upgrade_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation prompt'))
    upgrade_parser.add_argument('--force-dev', action='store_true', help=_('Force upgrade even in a developer environment (use with caution)'))
    return parser

def print_header(title):
    """Print a formatted header."""
    safe_print('\n' + '=' * 60)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 60)

def main():
    """Main application entry point with pre-flight version check."""
    try:
        # --- START: ROBUST PRE-PARSING LOGIC ---
        
        # This pre-parser finds global flags like --verbose and --lang,
        # even if they come after a command like 'run'.
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument('--lang', default=None)
        global_parser.add_argument('--verbose', '-V', action='store_true')
        
        # Parse ONLY the known global flags and keep the rest.
        global_args, remaining_args = global_parser.parse_known_args()

        # Handle version check separately as it exits immediately.
        if '-v' in remaining_args or '--version' in remaining_args:
            prog_name = Path(sys.argv[0]).name
            if prog_name == '8pkg' or (len(sys.argv) > 0 and '8pkg' in sys.argv[0]):
                safe_print(_('8pkg {}').format(get_version()))
            else:
                safe_print(_('omnipkg {}').format(get_version()))
            return 0
        
        # --- END: ROBUST PRE-PARSING LOGIC ---
        
        cm = ConfigManager()
        
        # Set language based on pre-scanned flag or config
        user_lang = global_args.lang or cm.config.get('language')
        if user_lang:
            _.set_language(user_lang)

        pkg_instance = OmnipkgCore(config_manager=cm)
        prog_name = Path(sys.argv[0]).name
        if prog_name == '8pkg' or (len(sys.argv) > 0 and '8pkg' in sys.argv[0]):
            parser = create_8pkg_parser()
        else:
            parser = create_parser()

        # Now, parse ONLY the remaining arguments with the full parser.
        args = parser.parse_args(remaining_args)

        # Manually add the pre-scanned global flags to the final args namespace.
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
            actual_version = get_actual_python_version()
            safe_print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
            safe_print(_('🎪 Omnipkg supports version switching for:'))
            safe_print(_('   • Python modules (e.g., rich): See tests/test_rich_switching.py'))
            safe_print(_('   • Binary packages (e.g., uv): See tests/test_uv_switching.py'))
            safe_print(_('   • C-extension packages (e.g., numpy, scipy): See stress_test.py'))
            safe_print(_('   • Complex dependency packages (e.g., TensorFlow): See tests/test_tensorflow_switching.py'))
            safe_print(_('   • Note: The Flask demo is under construction and not currently available.'))
            safe_print(_('\nSelect a demo to run:'))
            safe_print(_('1. Rich test (Python module switching)'))
            safe_print(_('2. UV test (binary switching)'))
            safe_print(_('3. NumPy + SciPy stress test (C-extension switching)'))
            safe_print(_('4. TensorFlow test (complex dependency switching)'))
            safe_print(_('5. 🚀 Multiverse Healing Test (Cross-Python Hot-Swapping Mid-Script)'))
            safe_print(_('6. Flask test (under construction)'))
            safe_print(_('7. Auto-healing Test (omnipkg run)')) # <--- ADD THIS
            safe_print(_('8. 🌠 Quantum Multiverse Warp (Concurrent Python Installations)'))
            safe_print(_('9. Flask Port Finder Test (auto-healing with Flask)')) # <-- ADD THIS LINE
            def run_and_stream(cmd_list):
                process = subprocess.Popen(cmd_list, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
                for line in process.stdout:
                    safe_print(line, end='')
                return process.wait()
            try:
                response = input(_('Enter your choice (1-8): ')).strip()
            except EOFError:
                response = ''
            test_file = None
            demo_name = ''
            if response == '1':
                # Corrected logic for the Rich demo
                demo_name = 'rich'
                test_file = TESTS_DIR / 'test_rich_switching.py'
                if not test_file.exists():
                    safe_print(_('❌ Error: Test file {} not found.').format(test_file))
                    return 1
                return run_demo_with_live_streaming(str(test_file), demo_name)
            elif response == '2':
                # INSERT THESE LINES HERE (after line 586):
                actual_version = get_actual_python_version()
                actual_version_str = f"{actual_version[0]}.{actual_version[1]}"
                
                demo_name = 'uv'
                demo_name = 'uv'
                source_script_path = TESTS_DIR / 'test_uv_switching.py'
                if not source_script_path.exists():
                    safe_print(f'❌ Error: Source test file {source_script_path} not found.')
                    return 1

                # Get the Python executable for the current version
                python_exe = pkg_instance.config_manager.get_interpreter_for_version(actual_version_str)
                if not python_exe or not python_exe.exists():
                    safe_print(f"❌ Python {actual_version_str} is not managed by omnipkg.")
                    safe_print(f"   Please adopt it first: {parser.prog} python adopt {actual_version_str}")
                    return 1

                safe_print(f'🚀 Running {demo_name} demo with Python {actual_version_str} via sterile environment...')
                
                # Create sterile copy
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
                    temp_script_path = Path(temp_script.name)
                    temp_script.write(source_script_path.read_text(encoding='utf-8'))
                
                safe_print(f"   - Sterile script created at: {temp_script_path}")
                
                returncode = 1
                try:
                    # Use run_demo_with_live_streaming with the detected Python version
                    return run_demo_with_live_streaming(
                        test_file_name=str(temp_script_path),
                        demo_name=demo_name,
                        python_exe=str(python_exe)
                    )
                finally:
                    temp_script_path.unlink(missing_ok=True)

            elif response == '3':
                if not handle_python_requirement('3.11', pkg_instance, parser.prog):
                    return 1
                test_file = TESTS_DIR / 'test_version_combos.py'
                demo_name = 'numpy_scipy'
            elif response == '4':
                if not handle_python_requirement('3.11', pkg_instance, parser.prog):
                    return 1
                test_file = TESTS_DIR / 'test_tensorflow_switching.py'
                demo_name = 'tensorflow'
            elif response == '5':
                safe_print(_('\n' + '!'*60))
                safe_print(_('  🚀 INITIATING MULTIVERSE HEALING & ANALYSIS DEMO!'))
                safe_print(_('  This is a test of omnipkg\'s cross-context capabilities.'))
                safe_print(_('  Creating a sterile temporary copy to ensure a clean run...'))
                safe_print('!'*60)
                
                return run_demo_with_enforced_context(
                    source_script_path=TESTS_DIR / 'test_multiverse_healing.py',
                    demo_name='multiverse_healing',
                    pkg_instance=pkg_instance,
                    parser_prog=parser.prog,
                    required_version='3.11'  # This test requires 3.11 specifically
                )

            elif response == '6':
                test_file = TESTS_DIR / 'test_rich_switching.py'
                demo_name = 'rich'
                safe_print(_('⚠️ The Flask demo is under construction and not currently available.'))
                safe_print(_('Switching to the Rich test (option 1) for now!'))
            elif response == '7': # <--- ADD THIS ENTIRE BLOCK
                demo_name = 'auto-heal'
                test_file_path = TESTS_DIR / 'test_old_rich.py'
                safe_print(_('🚀 Running {} demo from source: {}...').format(demo_name, test_file_path))
                safe_print(_('📡 Live streaming output...'))
                safe_print('-' * 60)
                
                # We must call omnipkg as a subprocess to properly test the 'run' command
                cmd = [parser.prog, 'run', str(test_file_path)]
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
            elif response == '8':
                demo_name = 'rich_multiverse'
                source_script_path = TESTS_DIR / 'test_concurrent_install.py'
                if not source_script_path.exists():
                    safe_print(f'❌ Error: Source test file {source_script_path} not found.')
                    return 1

                safe_print(_('🚀 Running {} demo from a sterile environment...').format(demo_name))
                safe_print(_('   (This ensures no PYTHONPATH contamination from the orchestrator)'))
                
                # Create a sterile copy of the script in /tmp
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as temp_script:
                    temp_script_path = Path(temp_script.name)
                    temp_script.write(source_script_path.read_text(encoding='utf-8'))
                
                safe_print(f"   - Sterile script created at: {temp_script_path}")
                
                returncode = 1 # Default to failure
                try:
                    safe_print('📡 Live streaming output...')
                    safe_print('-' * 60)
                    
                    # Execute the STERILE script using 'omnipkg run'
                    cmd = [parser.prog, 'run', str(temp_script_path)]
                    process = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
                    for line in process.stdout:
                        safe_print(line, end='')
                    returncode = process.wait()

                    safe_print('-' * 60)
                    if returncode == 0:
                        safe_print(_('🎉 Demo completed successfully!'))
                    else:
                        safe_print(_('❌ Demo failed with return code {}').format(returncode))
                
                finally:
                    # ALWAYS clean up the temporary file
                    temp_script_path.unlink(missing_ok=True)
                
                return returncode
            elif response == '9':
                demo_name = 'flask_port_finder'
                test_file = TESTS_DIR / 'test_flask_port_finder.py'
                if not test_file.exists():
                    safe_print(_('❌ Error: Test file {} not found.').format(test_file))
                    return 1
                # This demo is best run via 'omnipkg run' to showcase auto-healing
                safe_print(_('🚀 This demo uses "omnipkg run" to showcase auto-healing of missing modules.'))
                cmd = [parser.prog, 'run', str(test_file)]
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
            else:
                safe_print(_('❌ Invalid choice. Please select 1, 2, 3, 4, 5, 6, 7, 8 or 9.'))
                return 1
            if not test_file.exists():
                safe_print(_('❌ Error: Test file {} not found.').format(test_file))
                return 1
            return run_demo_with_live_streaming(test_file, demo_name)
        elif args.command == 'stress-test':
            if stress_test_command():
                run_actual_stress_test()
            return 0
        elif args.command == 'install':
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
            return pkg_instance.smart_install(packages_to_process)
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
        elif args.command == 'reset-config':
            return pkg_instance.reset_configuration(force=args.force)
        elif args.command == 'run':
            return execute_run_command(args.script_and_args, cm, verbose=args.verbose)
        elif args.command == 'upgrade':
            if args.package.lower() == 'omnipkg':
                return pkg_instance.smart_upgrade(version=args.version, force=args.force, skip_dev_check=args.force_dev)

            else:
                # Upgrading other packages is just a reinstall of the latest
                safe_print(_("Redirecting to smart_install to get the latest version of '{}'...").format(args.package))
                return pkg_instance.smart_install([args.package], force_reinstall=True)
        else:
            parser.print_help()
            safe_print(_("\n💡 Did you mean 'omnipkg config set language <code>'?"))
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