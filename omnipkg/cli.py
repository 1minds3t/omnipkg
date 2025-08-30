"""
omnipkg CLI - Enhanced with runtime interpreter switching and language support
"""
import sys
import argparse
import subprocess
from pathlib import Path
import textwrap
import os
from .i18n import _, SUPPORTED_LANGUAGES
from .core import omnipkg as OmnipkgCore
from .core import ConfigManager
TESTS_DIR = Path(__file__).parent.parent / 'tests'
DEMO_DIR = Path(__file__).parent

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
        except ImportError:
            try:
                import tomli
                toml_path = Path(__file__).parent.parent / 'pyproject.toml'
                if toml_path.exists():
                    with open(toml_path, 'rb') as f:
                        data = tomli.load(f)
                        return data.get('project', {}).get('version', 'unknown')
            except ImportError:
                pass
        except Exception:
            pass
    return 'unknown'

VERSION = get_version()

def stress_test_command():
    """Handle stress test command - BLOCK if not Python 3.11."""
    if sys.version_info[:2] != (3, 11):
        print('=' * 60)
        print(_('  ⚠️  Stress Test Requires Python 3.11'))
        print('=' * 60)
        print(_('Current Python version: {}.{}').format(sys.version_info.major, sys.version_info.minor))
        print()
        print(_('The omnipkg stress test only works in Python 3.11 environments.'))
        print(_('To run the stress test:'))
        print(_('1. Create a Python 3.11 virtual environment'))
        print(_('2. Install omnipkg in that environment'))
        print(_("3. Run 'omnipkg stress-test' from there"))
        print()
        print(_('🔮 Coming Soon: Hot Python interpreter swapping mid-script!'))
        print(_('   This will allow seamless switching between Python versions'))
        print(_('   during package operations - stay tuned!'))
        print('=' * 60)
        return False
    print('=' * 60)
    print(_('  🚀 omnipkg Nuclear Stress Test - Runtime Version Swapping'))
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
        print(_('💡 This would run the actual stress test with:'))
        print(_('   • Large package installations (TensorFlow, PyTorch, etc.)'))
        print(_('   • Version conflict demonstrations'))
        print(_('   • Real-time bubbling and deduplication'))
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
        current_lang = _.get_language_code()
        project_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env['OMNIPKG_LANG'] = current_lang
        env['LANG'] = f'{current_lang}.UTF-8'
        env['LANGUAGE'] = current_lang
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONPATH'] = str(project_root) + os.pathsep + env.get('PYTHONPATH', '')
        print(_('🌍 Language environment passed to subprocess: {}').format(current_lang))
        process = subprocess.Popen([sys.executable, str(test_file)], text=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in process.stdout:
            print(line, end='')
        returncode = process.wait()
        print('-' * 60)
        if returncode == 0:
            if demo_name == 'tensorflow':
                print(_('😎 TensorFlow escaped the matrix! 🚀'))
            print(_('🎉 Demo completed successfully!'))
            print(_("💡 Run 'omnipkg demo' to try another test."))
        else:
            print(_('❌ Demo failed with return code {}').format(returncode))
            print(_('💡 Check the output above for error details.'))
        return returncode
    except KeyboardInterrupt:
        print(_('\n⚠️  Demo cancelled by user (Ctrl+C)'))
        print(_('🛡️  Your environment should be safe - omnipkg handles interruptions gracefully'))
        if process:
            try:
                process.terminate()
            except:
                pass
        return 130
    except Exception as e:
        print(_('❌ Demo failed with error: {}').format(e))
        print(_('📋 Full traceback:'))
        import traceback
        traceback.print_exc()
        return 1

def run_demo_with_fallback_streaming(test_file, demo_name):
    """Fallback method with manual streaming if direct doesn't work."""
    print(_('🚀 Running {} test from {}...').format(demo_name.capitalize(), test_file))
    print(_('📡 Streaming output in real-time...'))
    print(_('💡 Heavy package installations may have natural pauses - this is normal!'))
    print(_('🛑 Press Ctrl+C to safely cancel'))
    print('-' * 60)
    try:
        cm = ConfigManager()
        current_lang = cm.config.get('language', 'en')
        env = os.environ.copy()
        env['OMNIPKG_LANG'] = current_lang
        env['LANG'] = f'{current_lang}.UTF-8'
        env['LANGUAGE'] = current_lang
        env['PYTHONUNBUFFERED'] = '1'
        process = subprocess.Popen([sys.executable, str(test_file)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, text=True, env=env)
        while True:
            char = process.stdout.read(1)
            if char == '' and process.poll() is not None:
                break
            if char:
                print(char, end='', flush=True)
        returncode = process.wait()
        print('\n' + '-' * 60)
        if returncode == 0:
            if demo_name == 'tensorflow':
                print(_('😎 TensorFlow escaped the matrix! 🚀'))
            print(_('🎉 Demo completed successfully!'))
            print(_("💡 Run 'omnipkg demo' to try another test."))
        else:
            print(_('❌ Demo failed with return code {}').format(returncode))
        return returncode
    except KeyboardInterrupt:
        print(_('\n⚠️  Demo cancelled by user (Ctrl+C)'))
        print(_('🛡️  Cleaning up safely...'))
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
            except:
                pass
        return 130
    except Exception as e:
        print(_('\n❌ Demo failed with error: {}').format(e))
        return 1

def create_parser():
    """Creates and configures the argument parser."""

    # --- THE DEFINITIVE FIX ---
    # We will build the epilog manually from smaller, individually translated strings.
    # This is the most robust method and guarantees the extractor will find them all.
    
    epilog_parts = [
        _("🌟 Key Features:"),
        _("  • Runtime Python interpreter switching (no shell restart needed!)"),
        _("  • Automatic version bubbling to prevent conflicts"),
        _("  • Downgrade protection with smart conflict resolution"),
        _("  • Multi-version package coexistence"),
        _("  • Intelligent dependency management with Redis-backed knowledge base"),
        "", # Adds a blank line
        _("📖 Essential Commands:"),
        _("  omnipkg install <package>   Install with automatic conflict resolution"),
        _("  omnipkg install-with-deps <package>  Install with specific dependency versions"),
        _("  omnipkg list [filter]       View all packages and their bubble status  "),
        _("  omnipkg status              Check multi-version environment health"),
        _("  omnipkg info <package>      Interactive package dashboard with version explorer"),
        _("  omnipkg demo                Run interactive demos for version switching"),
        _("  omnipkg stress-test         See the magic! Heavy-duty package installation demo"),
        _("  omnipkg config set language <code>  Set display language (e.g., en, es)"),
        "",
        _("🎯 Advanced Features:"),
        _("  omnipkg revert             Roll back to last known good state"),
        _("  omnipkg uninstall <pkg>    Smart removal with dependency checking"),
        _("  omnipkg rebuild-kb         Refresh the intelligence knowledge base"),
        "",
        _("💡 Installation Examples:"),
        _("  omnipkg install requests numpy>=1.20        # Multiple packages"),
        _("  omnipkg install uv==0.7.13 uv==0.7.14      # Multiple versions (auto-bubbled!)"),
        _("  omnipkg install-with-deps tensorflow==2.13.0 numpy==1.24.3 typing-extensions==4.5.0"),
        _("  omnipkg install -r requirements.txt        # From requirements file"),
        _("  omnipkg install 'django>=3.0,<4.0'         # Complex version specs"),
        "",
        _("🔍 Understanding Your Environment:"),
        _("  omnipkg list                # Shows ✅ active and 🫧 bubbled versions"),
        _("  omnipkg info <package>      # Deep dive into any package's status"),
        _("  omnipkg status              # Overall environment health"),
        "",
        _("🛠️ Redis Knowledge Base (Advanced):"),
        _("  omnipkg stores rich metadata in Redis. Explore with:"),
        _("  redis-cli HGETALL omnipkg:pkg:<package>                    # Package info"),
        _('  redis-cli SMEMBERS "omnipkg:pkg:<package>:installed_versions"  # All versions'),
        _("  redis-cli HGETALL omnipkg:pkg:<package>:<version>          # Version details"),
        "",
        _("🔧 Python Version Management:"),
        _("  omnipkg automatically manages Python interpreters! When you run commands"),
        _("  that need a different Python version, omnipkg will:"),
        _("  • Download and install the required Python version seamlessly"),
        _("  • Switch interpreters mid-execution without shell restart"),
        _("  • Maintain package isolation across Python versions"),
        _("  • Keep your environment clean and organized"),
        "",
        _("💡 Pro Tips:"),
        _("  • Run 'omnipkg demo' to try version-switching demos"),
        _("  • Run 'omnipkg stress-test' to see automated interpreter switching"),
        _("  • Use 'omnipkg info <package>' for interactive version selection"),
        _("  • Use 'omnipkg config set language <code>' to change language"),
        _("  • The system learns from conflicts and prevents future issues"),
        _("  • All changes are logged and reversible with 'omnipkg revert'"),
        "",
        _("Version: {}").format(VERSION)
    ]
    
    translated_epilog = "\n".join(epilog_parts)
    # --- END OF FIX ---

    parser = argparse.ArgumentParser(
        prog='omnipkg',
        description=_('🚀 The intelligent Python package manager that eliminates dependency hell'),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=translated_epilog
    )
    
    parser.add_argument('-v', '--version', action='version', version=_('%(prog)s {}').format(VERSION))
    parser.add_argument('--lang', metavar='CODE', help=_('Override the display language for this command (e.g., es, de, ja)'))
    subparsers = parser.add_subparsers(dest='command', help=_('Available commands:'), required=False)
    install_parser = subparsers.add_parser('install', help=_('Install packages with intelligent conflict resolution'), description=_('Install packages with automatic version management and conflict resolution'))
    install_parser.add_argument('packages', nargs='*', help=_('Packages to install (e.g., "requests==2.25.1", "numpy>=1.20")'))
    install_parser.add_argument('-r', '--requirement', help=_('Install from requirements file with smart dependency resolution'), metavar='FILE')
    install_with_deps_parser = subparsers.add_parser('install-with-deps', help=_('Install a package with specific dependency versions'), description=_('Install a package with specific dependency versions in a bubble to avoid conflicts'))
    install_with_deps_parser.add_argument('package', help=_('Package to install (e.g., "tensorflow==2.13.0")'))
    install_with_deps_parser.add_argument('--dependency', action='append', help=_('Dependency with version (e.g., "numpy==1.24.3")'), default=[])
    uninstall_parser = subparsers.add_parser('uninstall', help=_('Intelligently remove packages and their dependencies'), description=_('Smart package removal with safety features'))
    uninstall_parser.add_argument('packages', nargs='+', help=_('Packages to uninstall (removes all versions)'))
    uninstall_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation prompts'))
    info_parser = subparsers.add_parser('info', help=_('Interactive package explorer with version management'), description=_('Explore package details, dependencies, and manage versions interactively'))
    info_parser.add_argument('package', help=_('Package name to explore'))
    info_parser.add_argument('--version', default='active', help=_('Specific version to inspect (default: active)'))
    revert_parser = subparsers.add_parser('revert', help=_('Time-travel back to your last known good environment'), description=_('Revert all changes to the last stable environment state'))
    revert_parser.add_argument('--yes', '-y', action='store_true', help=_('Skip confirmation and revert immediately'))
    list_parser = subparsers.add_parser('list', help=_('View all installed packages and their bubble status'), description=_('List all packages with detailed status indicators'))
    list_parser.add_argument('filter', nargs='?', help=_('Filter packages by name pattern'))
    status_parser = subparsers.add_parser('status', help=_('Multi-version environment health dashboard'), description=_('Overview of your Python interpreters, packages, and bubble isolation'))
    demo_parser = subparsers.add_parser('demo', help=_('Interactive demo for version switching'), description=_("Run interactive demos showcasing omnipkg's version-switching capabilities:\n        1. Rich test (Python module switching)\n        2. UV test (binary switching)\n        3. NumPy + SciPy stress test (C-extension switching)\n        4. TensorFlow test (complex dependency switching)\n        Note: The Flask demo is under construction and not currently available."))
    stress_parser = subparsers.add_parser('stress-test', help=_('Ultimate demonstration with heavy scientific packages'), description=_("Showcase omnipkg's features with large package installations and version switching"))
    reset_parser = subparsers.add_parser('reset', help=_('Clean slate: rebuild the omnipkg knowledge base'), description=_('Delete and rebuild the Redis knowledge base from scratch'))
    reset_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation'))
    rebuild_parser = subparsers.add_parser('rebuild-kb', help=_('Refresh the intelligence knowledge base'), description=_('Force rebuild of package metadata and dependency intelligence'))
    rebuild_parser.add_argument('--force', '-f', action='store_true', help=_('Ignore cache and force complete rebuild'))
    reset_config_parser = subparsers.add_parser('reset-config', help=_('Delete the config file to trigger a fresh setup'), description=_('Deletes the omnipkg config.json file'))
    reset_config_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation and delete immediately'))
    config_parser = subparsers.add_parser('config', help=_('View or edit the omnipkg configuration'), description=_('Allows setting persistent configuration values'))
    config_subparsers = config_parser.add_subparsers(dest='config_command', required=True)
    config_set_parser = config_subparsers.add_parser('set', help=_('Set a configuration value'))
    
    config_set_parser.add_argument('key', choices=['language', 'install_strategy'], help=_('The configuration key to set'))
    config_set_parser.add_argument('value', help=_('The value to set for the key (e.g., en, es, de, ja, zh_CN, stable-main, latest-active, fast-compat)'))


    prune_parser = subparsers.add_parser('prune', help=_('Clean up old, bubbled package versions'), description=_('Removes bubbled package versions to save space, keeping the active version safe.'))
    prune_parser.add_argument('package', help=_('The package whose bubbles you want to prune.'))
    prune_parser.add_argument('--keep-latest', type=int, metavar='N', help=_('Keep the N most recent bubbled versions and prune the rest.'))
    prune_parser.add_argument('--yes', '-y', dest='force', action='store_true', help=_('Skip confirmation prompts'))
    return parser

def print_header(title):
    """Print a formatted header."""
    print('\n' + '=' * 60)
    print(_('  🚀 {}').format(title))
    print('=' * 60)

def main():
    """Main application entry point."""
    try:
        # STEP 1: Set the language FIRST, before anything else.
        # This ensures the `_` object is configured correctly.
        cm = ConfigManager()
        
        # Check for a temporary --lang override from the command line first.
        # We do a quick, preliminary parse just for this one argument.
        temp_parser = argparse.ArgumentParser(add_help=False)
        temp_parser.add_argument('--lang', default=None)
        temp_args, remaining_args = temp_parser.parse_known_args()

        if temp_args.lang:
            user_lang = temp_args.lang
        else:
            user_lang = cm.config.get('language')
        
        if user_lang:
            _.set_language(user_lang)

        # STEP 2: NOW that the language is set, create the parser.
        # It will now use the correct translations for all its help text.
        parser = create_parser()
        args = parser.parse_args()

        # If no command was given, show the help text and exit gracefully.
        if args.command is None:
            parser.print_help()
            # A more helpful welcome message.
            print(_("\n👋 Welcome back to omnipkg! Run a command or see --help for details."))
            return 0

        # Create omnipkg instance
                # Create omnipkg instance
        pkg_instance = OmnipkgCore(config_data=cm.config)

        if args.command == 'config':
            if args.config_command == 'set':
                if args.key == 'language':
                    if args.value not in SUPPORTED_LANGUAGES:
                        print(_("❌ Error: Language '{}' not supported. Supported: {}").format(args.value, ', '.join(SUPPORTED_LANGUAGES.keys())))
                        return 1
                    cm.set('language', args.value)
                    _.set_language(args.value)
                    lang_name = SUPPORTED_LANGUAGES.get(args.value, args.value)
                    print(_('✅ Language permanently set to: {lang}').format(lang=lang_name))
                elif args.key == 'install_strategy':
                    valid_strategies = ['stable-main', 'latest-active', 'fast-compat']
                    if args.value not in valid_strategies:
                        print(_("❌ Error: Invalid install strategy. Must be one of: {}").format(', '.join(valid_strategies)))
                        return 1
                    cm.set('install_strategy', args.value)
                    print(_('✅ Install strategy permanently set to: {}').format(args.value))

                else:
                    # Should not be reached due to argparse choices, but good for safety.
                    parser.print_help()
                    return 1
            return 0

        
        
        elif args.command == 'demo':
            print_header(_('Interactive Omnipkg Demo'))
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
                response = input(_('Enter your choice (1-5): ')).strip()
            except EOFError:
                response = ''
            if response == '1':
                test_file = TESTS_DIR / 'test_rich_switching.py'
                demo_name = 'rich'
            elif response == '2':
                test_file = TESTS_DIR / 'test_uv_switching.py'
                demo_name = 'uv'
            elif response == '3':
                if sys.version_info[:2] != (3, 11):
                    print('=' * 60)
                    print(_('  ⚠️  NumPy/SciPy Stress Test Requires Python 3.11'))
                    print('=' * 60)
                    print(_('Current Python version: {}.{}').format(sys.version_info.major, sys.version_info.minor))
                    print('=' * 60)
                    return 1
                test_file = DEMO_DIR / 'stress_test.py'
                demo_name = 'numpy_scipy'
            elif response == '4':
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
            return pkg_instance.show_package_info(args.package, args.version)

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