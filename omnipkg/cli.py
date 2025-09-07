"""omnipkg CLI - Enhanced with runtime interpreter switching and language support"""
import sys
import argparse
from pathlib import Path
import os
import subprocess  # <-- FIX: Added this import
import tempfile    # <-- FIX: Added this import
import json        # <-- FIX: Added this import

# [REFACTORED] Import utilities from the common module
from .i18n import _, SUPPORTED_LANGUAGES
from .core import omnipkg as OmnipkgCore
from .core import ConfigManager
from .common_utils import print_header, run_script_in_omnipkg_env, UVFailureDetector
from .commands.run import execute_run_command # <-- NEW IMPORT

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

def handle_python_requirement(required_version_str: str, pkg_instance: OmnipkgCore, parser_prog: str) -> bool:
    """
    Checks if the current Python version matches the requirement.
    If not, it attempts to automatically adopt and/or swap.
    """
    actual_version_tuple = get_actual_python_version()
    required_version_tuple = tuple(map(int, required_version_str.split('.')))

    if actual_version_tuple == required_version_tuple:
        return True

    print_header(_('Python Version Requirement'))
    print(_('  ⚠️  This Demo Requires Python {}').format(required_version_str))
    print(_('  - Current Python version: {}.{}').format(actual_version_tuple[0], actual_version_tuple[1]))
    print(_('  - omnipkg will now attempt to automatically configure the correct interpreter.'))
    print('-' * 60)

    managed_interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
    if required_version_str not in managed_interpreters:
        discovered_interpreters = pkg_instance.config_manager.list_available_pythons()
        if required_version_str in discovered_interpreters:
            print(_('🐍 Python {} found on your system. Adopting it...').format(required_version_str))
            if pkg_instance.adopt_interpreter(required_version_str) != 0:
                print(_('❌ Failed to adopt Python {}. Please try manually.').format(required_version_str))
                print(_("   Run: {} python adopt {}").format(parser_prog, required_version_str))
                return False
            print(_('✅ Successfully adopted Python {}.').format(required_version_str))
        else:
            print(_('❌ Required Python version {} not found on your system.').format(required_version_str))
            print(_('   Please install Python {} and ensure it is in your PATH.').format(required_version_str))
            return False

    print(_('🔄 Swapping active interpreter to Python {}...').format(required_version_str))
    if pkg_instance.switch_active_python(required_version_str) != 0:
        print(_('❌ Failed to swap to Python {}. Please try manually.').format(required_version_str))
        print(_("   Run: {} swap python {}").format(parser_prog, required_version_str))
        return False

    print(_('✅ Environment successfully configured for Python {}.').format(required_version_str))
    print(_('🚀 Proceeding to run the demo...'))
    print('=' * 60)
    return True

def get_version():
    """Get version from package metadata."""
    try:
        from importlib.metadata import version
        return version('omnipkg')
    except Exception:
        # Fallback for development environments
        try:
            import tomllib
            toml_path = Path(__file__).parent.parent / 'pyproject.toml'
            if toml_path.exists():
                with open(toml_path, 'rb') as f:
                    data = tomllib.load(f)
                    return data.get('project', {}).get('version', 'unknown')
        except (ImportError, Exception):
            pass # Ignore errors if tomllib/tomli is not available or file is malformed
    return 'unknown'
VERSION = get_version()

def stress_test_command():
    """Handle stress test command - BLOCK if not Python 3.11."""
    actual_version = get_actual_python_version()
    if actual_version != (3, 11):
        print('=' * 60)
        print(_('  ⚠️  Stress Test Requires Python 3.11'))
        print('=' * 60)
        print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
        print()
        print(_('The omnipkg stress test only works in Python 3.11 environments.'))
        print(_('To run the stress test:'))
        print(_('1. Switch to Python 3.11: omnipkg swap python 3.11'))
        print(_('2. If not available, adopt it first: omnipkg python adopt 3.11'))
        print(_("3. Run 'omnipkg stress-test' from there"))
        print('=' * 60)
        return False

    print('=' * 60)
    print(_('  🚀 omnipkg Nuclear Stress Test - Runtime Version Swapping'))
    print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
    print('=' * 60)
    print(_('🎪 This demo showcases IMPOSSIBLE package combinations:'))
    print(_('   • Runtime swapping between numpy/scipy versions mid-execution'))
    print(_('   • Different numpy+scipy combos (1.24.3+1.12.0 → 1.26.4+1.16.1)'))
    print(_("   • Previously 'incompatible' versions working together seamlessly"))
    print(_('   • Live PYTHONPATH manipulation without process restart'))
    print(_('   • Space-efficient deduplication (shows deduplication - normally'))
    print(_('     we average ~60% savings, but less for C extensions/binaries)'))
    print()
    print(_('🤯 What makes this impossible with traditional tools:'))
    print(_("   • numpy 1.24.3 + scipy 1.12.0 → 'incompatible dependencies'"))
    print(_('   • Switching versions requires environment restart'))
    print(_('   • Dependency conflicts prevent coexistence'))
    print(_("   • Package managers can't handle multiple versions"))
    print()
    print(_('✨ omnipkg does this LIVE, in the same Python process!'))
    print(_('📊 Expected downloads: ~500MB | Duration: 30 seconds - 3 minutes'))
    try:
        response = input(_('🚀 Ready to witness the impossible? (y/n): ')).lower().strip()
    except EOFError:
        response = 'n'
    if response == 'y':
        return True
    else:
        print(_("🎪 Cancelled. Run 'omnipkg stress-test' anytime!"))
        return False

def run_actual_stress_test():
    """Run the actual stress test - only called if Python 3.11."""
    print(_('🔥 Starting stress test...'))
    try:
        from . import stress_test
        stress_test.run()
    except ImportError:
        print(_('❌ Stress test module not found. Implementation needed.'))
    except Exception as e:
        print(_('❌ An error occurred during stress test execution: {}').format(e))
        import traceback
        traceback.print_exc()

def run_demo_with_live_streaming(test_file, demo_name):
    """Run a demo with true, line-by-line live streaming output."""
    print(_('🚀 Running {} test from {}...').format(demo_name.capitalize(), test_file))
    print(_('📡 Live streaming output (this may take several minutes for heavy packages)...'))
    print(_("💡 Don't worry if there are pauses - packages are downloading/installing!"))
    print(_('🛑 Press Ctrl+C to safely cancel if needed'))
    print('-' * 60)
    process = None
    try:
        cm = ConfigManager()
        current_lang = cm.config.get('language', 'en')
        project_root = FILE_PATH.parent.parent
        env = os.environ.copy()
        env['OMNIPKG_LANG'] = current_lang
        env['LANG'] = f'{current_lang}.UTF-8'
        env['LANGUAGE'] = current_lang
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONPATH'] = str(project_root) + os.pathsep + env.get('PYTHONPATH', '')
        
        # Use the currently configured python executable for the subprocess
        python_exe = cm.config.get('python_executable', sys.executable)
        
        process = subprocess.Popen([python_exe, str(test_file)], text=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        
        for line in process.stdout:
            print(line, end='')
            
        returncode = process.wait()
        print('-' * 60)
        
        if returncode == 0:
            print(_('🎉 Demo completed successfully!'))
        else:
            print(_('❌ Demo failed with return code {}').format(returncode))
        return returncode

    except KeyboardInterrupt:
        print(_('\n⚠️  Demo cancelled by user (Ctrl+C)'))
        if process:
            process.terminate()
        return 130
    except Exception as e:
        print(_('❌ Demo failed with error: {}').format(e))
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
    run_parser = subparsers.add_parser('run', help=_('Run a script with auto-healing for version conflicts'))
    run_parser.add_argument('script_and_args', nargs=argparse.REMAINDER, help=_('The script to run, followed by its arguments'))
    prune_parser = subparsers.add_parser('prune', help=_('Clean up old, bubbled package versions'))
    prune_parser.add_argument('package', help=_('Package whose bubbles to prune'))
    prune_parser.add_argument('--keep-latest', type=int, metavar='N', help=_('Keep N most recent bubbled versions'))
    prune_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation'))
    return parser

def print_header(title):
    """Print a formatted header."""
    print('\n' + '=' * 60)
    print(_('  🚀 {}').format(title))
    print('=' * 60)

def main():
    """Main application entry point."""
    try:
        cm = ConfigManager()
        pkg_instance = OmnipkgCore(config_manager=cm)
        temp_parser = argparse.ArgumentParser(add_help=False)
        temp_parser.add_argument('--lang', default=None)
        temp_args, remaining_args = temp_parser.parse_known_args()
        if temp_args.lang:
            user_lang = temp_args.lang
        else:
            user_lang = cm.config.get('language')
        if user_lang:
            _.set_language(user_lang)
        prog_name = Path(sys.argv[0]).name
        if prog_name == '8pkg' or (len(sys.argv) > 0 and '8pkg' in sys.argv[0]):
            parser = create_8pkg_parser()
        else:
            parser = create_parser()
        args = parser.parse_args()
        if args.command is None:
            parser.print_help()
            print(_('\n👋 Welcome back to omnipkg! Run a command or see --help for details.'))
            return 0
        # [FIXED] Corrected logic for the 'config' command
        if args.command == 'config':
            if args.config_command == 'view':
                print_header("omnipkg Configuration")
                for key, value in sorted(cm.config.items()):
                    print(f"  - {key}: {value}")
                return 0
            elif args.config_command == 'set':
                if args.key == 'language':
                    if args.value not in SUPPORTED_LANGUAGES:
                        print(_("❌ Error: Language '{}' not supported. Supported: {}").format(args.value, ', '.join(SUPPORTED_LANGUAGES.keys())))
                        return 1
                    cm.set('language', args.value)
                    _.set_language(args.value)
                    lang_name = SUPPORTED_LANGUAGES.get(args.value, args.value)
                    print(_('✅ Language permanently set to: {lang}').format(lang=lang_name))
                elif args.key == 'install_strategy':
                    valid_strategies = ['stable-main', 'latest-active']
                    if args.value not in valid_strategies:
                        print(_('❌ Error: Invalid install strategy. Must be one of: {}').format(', '.join(valid_strategies)))
                        return 1
                    cm.set('install_strategy', args.value)
                    print(_('✅ Install strategy permanently set to: {}').format(args.value))
                else:
                    # This handles any key that isn't 'language' or 'install_strategy'
                    parser.print_help()
                    return 1
                return 0 # Success for the 'set' command
            elif args.config_command == 'reset':
                if args.key == 'interpreters':
                    print(_("Resetting managed interpreters registry..."))
                    return pkg_instance.rescan_interpreters()
                return 0 # Success for the 'reset' command
            
            # Fallback for any other config subcommand
            parser.print_help()
            return 1
        elif args.command == 'list':
            if args.filter and args.filter.lower() == 'python':
                interpreters = pkg_instance.interpreter_manager.list_available_interpreters()
                discovered = pkg_instance.config_manager.list_available_pythons()
                print_header('Managed Python Interpreters')
                if not interpreters:
                    print('   No interpreters are currently managed by omnipkg for this environment.')
                else:
                    for ver, path in sorted(interpreters.items()):
                        print(_('   • Python {}: {}').format(ver, path))
                print_header('Discovered System Interpreters')
                print("   (Use 'omnipkg python adopt <version>' to make these available for swapping)")
                for ver, path in sorted(discovered.items()):
                    if ver not in interpreters:
                        print(_('   • Python {}: {}').format(ver, path))
                return 0
            else:
                return pkg_instance.list_packages(args.filter)
        elif args.command == 'python':
            if args.python_command == 'adopt':
                return pkg_instance.adopt_interpreter(args.version)
            elif args.python_command == 'rescan':
                return pkg_instance.rescan_interpreters()
            # [NOTE] You may also need a 'switch' handler here if it's not already present
            elif args.python_command == 'remove': # <--- CORRECTED LINE
                return pkg_instance.remove_interpreter(args.version, force=args.yes)
            elif args.python_command == 'switch':
                return pkg_instance.switch_active_python(args.version)
            else:
                parser.print_help()
                return 1
        elif args.command == 'swap':
            if not args.target:
                print(_('❌ Error: You must specify what to swap.'))
                print(_('Examples:'))
                print(_('  {} swap python           # Interactive Python version picker').format(parser.prog))
                print(_('  {} swap python 3.11      # Switch to Python 3.11').format(parser.prog))
                return 1
            if args.target.lower() == 'python':
                if args.version:
                    return pkg_instance.switch_active_python(args.version)
                else:
                    interpreters = pkg_instance.config_manager.list_available_pythons()
                    if not interpreters:
                        print(_('❌ No Python interpreters found.'))
                        return 1
                    print(_('🐍 Available Python versions:'))
                    versions = sorted(interpreters.keys())
                    for i, ver in enumerate(versions, 1):
                        print(_('  {}. Python {}').format(i, ver))
                    try:
                        choice = input(_('Select version (1-{}): ').format(len(versions))).strip()
                        if choice.isdigit() and 1 <= int(choice) <= len(versions):
                            selected_version = versions[int(choice) - 1]
                            return pkg_instance.switch_active_python(selected_version)
                        else:
                            print(_('❌ Invalid selection.'))
                            return 1
                    except (EOFError, KeyboardInterrupt):
                        print(_('\n❌ Operation cancelled.'))
                        return 1
            else:
                print(_("❌ Error: Unknown swap target '{}'. Currently supported: python").format(args.target))
                return 1
        elif args.command == 'status':
            return pkg_instance.show_multiversion_status()
        elif args.command == 'demo':
            actual_version = get_actual_python_version()
            print(_('Current Python version: {}.{}').format(actual_version[0], actual_version[1]))
            print(_('🎪 Omnipkg supports version switching for:'))
            print(_('   • Python modules (e.g., rich): See tests/test_rich_switching.py'))
            print(_('   • Binary packages (e.g., uv): See tests/test_uv_switching.py'))
            print(_('   • C-extension packages (e.g., numpy, scipy): See stress_test.py'))
            print(_('   • Complex dependency packages (e.g., TensorFlow): See tests/test_tensorflow_switching.py'))
            print(_('   • Note: The Flask demo is under construction and not currently available.'))
            print(_('\nSelect a demo to run:'))
            print(_('1. Rich test (Python module switching)'))
            print(_('2. UV test (binary switching)'))
            print(_('3. NumPy + SciPy stress test (C-extension switching)'))
            print(_('4. TensorFlow test (complex dependency switching)'))
            print(_('5. Flask test (under construction)'))
            try:
                response = input(_('Enter your choice (1-4): ')).strip()
            except EOFError:
                response = ''
            
            test_file = None
            demo_name = ''
            
            if response == '1':
                test_file = TESTS_DIR / 'test_rich_switching.py'
                demo_name = 'rich'
            elif response == '2':
                test_file = TESTS_DIR / 'test_uv_switching.py'
                demo_name = 'uv'
            # [MODIFIED] Logic for demo option 3
            elif response == '3':
                if not handle_python_requirement('3.11', pkg_instance, parser.prog):
                    return 1  # Exit with error if setup fails
                test_file = DEMO_DIR / 'stress_test.py'
                demo_name = 'numpy_scipy'
            # [MODIFIED] Logic for demo option 4
            elif response == '4':
                if not handle_python_requirement('3.11', pkg_instance, parser.prog):
                    return 1  # Exit with error if setup fails
                test_file = TESTS_DIR / 'test_tensorflow_switching.py'
                demo_name = 'tensorflow'
            elif response == '5':
                test_file = TESTS_DIR / 'test_rich_switching.py'
                demo_name = 'rich'
                print(_('⚠️ The Flask demo is under construction and not currently available.'))
                print(_('Switching to the Rich test (option 1) for now!'))
            else:
                print(_('❌ Invalid choice. Please select 1, 2, 3, 4, or 5.'))
                return 1
            if not test_file.exists():
                print(_('❌ Error: Test file {} not found.').format(test_file))
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
                    print(_("❌ Error: Requirements file not found at '{}'").format(req_path))
                    return 1
                print(_('📄 Reading packages from {}...').format(req_path.name))
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
                print(_('🐍 Managed Python Versions (available for swapping):'))
                for ver, path in sorted(managed_interpreters.items()):
                    marker = ' ⭐ (currently active)' if active_version_str and ver == active_version_str else ''
                    print(_('   • Python {}: {}{}').format(ver, path, marker))
                if active_version_str:
                    print(_('\n🎯 Active Context: Python {}').format(active_version_str))
                    print(_('📍 Configured Path: {}').format(configured_active_exe))
                else:
                    print('\n⚠️ Could not determine active Python version from config.')
                print(_('\n💡 To switch context, use: {} swap python <version>').format(parser.prog))
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
            return execute_run_command(args.script_and_args, cm)
        else:
            parser.print_help()
            print(_("\n💡 Did you mean 'omnipkg config set language <code>'?"))
            return 1
    except KeyboardInterrupt:
        print(_('\n❌ Operation cancelled by user.'))
        return 1
    except Exception as e:
        print(_('\n❌ An unexpected error occurred: {}').format(e))
        import traceback
        traceback.print_exc()
        return 1
if __name__ == '__main__':
    sys.exit(main())