#!/usr/bin/env python3
"""
omnipkg CLI
"""
import sys
import argparse
from .core import omnipkg, ConfigManager
from pathlib import Path

def print_header(title):
    """Prints a consistent, pretty header for CLI sections."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

import textwrap

def create_parser():
    """Creates and configures the argument parser."""
    # This formatter_class is key to making our new help text look good.
    # The epilog provides examples of the most important commands.
    parser = argparse.ArgumentParser(
        prog='omnipkg',
        description='The intelligent Python package manager that solves dependency hell.',
        formatter_class=argparse.RawTextHelpFormatter, # Prevents argparse from messing up our formatting
        epilog=textwrap.dedent('''\
            
        Common Commands:
          omnipkg install <package>   Install a package with downgrade protection.
          omnipkg list                See all installed packages and their health.
          omnipkg status              Check the health of your multi-version environment.
          omnipkg info <package>      Get a detailed dashboard for a specific package.
          omnipkg demo                Run the interactive showcase to see the magic.
        ''')
    )
    
    subparsers = parser.add_subparsers(dest='command', help='All available commands:', required=True)

    install_parser = subparsers.add_parser('install', help='Install packages (with downgrade protection)')
    # Make 'packages' optional (nargs='*') to allow using -r instead
    install_parser.add_argument('packages', nargs='*', help='Packages to install (e.g., "requests==2.25.1")')
    # Add the new -r/--requirement flag
    install_parser.add_argument(
        '-r', '--requirement', 
        help='Install from the given requirements file.',
        metavar='FILE'
    )
    
    uninstall_parser = subparsers.add_parser('uninstall', help='Uninstall packages from main env or bubbles')
    uninstall_parser.add_argument('packages', nargs='+', help='Packages to uninstall (e.g., "requests" or "requests==2.25.1")')
    uninstall_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompts')

    info_parser = subparsers.add_parser('info', help='Show detailed package information with interactive version selection')
    info_parser.add_argument('package', help='Package name to inspect')
    info_parser.add_argument('--version', default='active', help='Specific version to inspect')

    revert_parser = subparsers.add_parser('revert', help="Revert environment to the last known good state")
    revert_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation and revert immediately')

    list_parser = subparsers.add_parser('list', help='List installed packages')
    list_parser.add_argument('filter', nargs='?', help='Optional filter pattern for package names')

    status_parser = subparsers.add_parser('status', help='Show multi-version system status')

    demo_parser = subparsers.add_parser('demo', help='Run the interactive, automated demo')

    stress_parser = subparsers.add_parser('stress-test', help='Run the ultimate stress test with heavy-duty packages.')

    reset_parser = subparsers.add_parser('reset', help='DELETE and rebuild the omnipkg knowledge base in Redis')
    reset_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    rebuild_parser = subparsers.add_parser('rebuild-kb', help='Force a full rebuild of the knowledge base without deleting')
    rebuild_parser.add_argument('--force', '-f', action='store_true', help='Ignore cache and force re-processing of all metadata')

    return parser

def main():
    """The main entry point for the CLI."""
    
    # Handle the case where 'omnipkg' is run with no arguments
    if len(sys.argv) == 1:
        cm = ConfigManager()
        if not cm.config_path.exists():
            cm._first_time_setup()
            print("\n" + "="*50)
            print("🚀 Welcome to omnipkg! Your setup is complete.")
            print("To see the magic in action, we highly recommend running the demo:")
            print("\n    omnipkg demo\n")
            print("="*50)
        else:
            print("👋 Welcome back to omnipkg!")
            print("   Run `omnipkg status` to see your environment.")
            print("   Run `omnipkg demo` for a showcase of features.")
            print("   Run `omnipkg --help` for all commands.")
        return 0

    parser = create_parser()
    args = parser.parse_args()

    # First, create the config manager to load/create the config
    cm = ConfigManager()
    
    # Now, create the main instance, PASSING IN the loaded config
    pkg_instance = omnipkg(cm.config)
    try:
        # In omnipkg/cli.py -> main()

        if args.command == 'install':
            packages_to_process = []

            if args.requirement:
                # User provided a requirements file
                req_path = Path(args.requirement)
                if not req_path.is_file():
                    print(f"❌ Error: Requirements file not found at '{req_path}'")
                    return 1
                
                print(f"📄 Reading packages from {req_path.name}...")
                # In omnipkg/cli.py -> main()

                with open(req_path, 'r') as f:
                    # Parse the file, handling inline comments and empty lines
                    packages_to_process = []
                    for line in f:
                        # Get the part before any comment and strip whitespace
                        clean_line = line.split('#')[0].strip()
                        # Only add it to our list if it's not an empty string
                        if clean_line:
                            packages_to_process.append(clean_line)
            
            elif args.packages:
                # User provided packages directly on the command line
                packages_to_process = args.packages
            
            else:
                # No packages or file provided
                print("❌ Error: You must either specify packages to install or use the -r flag.")
                print("   Example: `omnipkg install requests` or `omnipkg install -r requirements.txt`")
                return 1

            # The magic happens here: pass the list to your existing core logic
            return pkg_instance.smart_install(packages_to_process)
        elif args.command == 'uninstall':
            return pkg_instance.smart_uninstall(args.packages, force=args.yes)
            
        elif args.command == 'revert':
            return pkg_instance.revert_to_last_known_good(force=args.yes)
        elif args.command == 'info':
            return pkg_instance.show_package_info(args.package, args.version)
        elif args.command == 'list':
            return pkg_instance.list_packages(args.filter)
        elif args.command == 'status':
            return pkg_instance.show_multiversion_status()
        elif args.command == 'demo':
            from .demo import run_demo
            return run_demo()
        elif args.command == 'stress-test':
            from . import stress_test
            print_header("omnipkg Ultimate Stress Test")
            print("This test will install, bubble, and test multiple large scientific packages.")
            print("\n⚠️  This will download several hundred MB and may take several minutes.")

            if input("\nProceed with the stress test? (y/n): ").lower() != 'y':
                print("Stress test cancelled.")
                return 0
            
            stress_test.run()
            return 0
        elif args.command == 'reset':
            return pkg_instance.reset_knowledge_base(force=args.yes)
        elif args.command == 'rebuild-kb':
            return pkg_instance.rebuild_knowledge_base(force=args.force)
        else:
            parser.print_help()
            return 1
            
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user.")
        return 1
    except Exception as e:
        print(f"\n❌ An unexpected top-level error occurred: {e}")
        import traceback
        traceback.print_exc()
        return 1
        
if __name__ == "__main__":
    sys.exit(main())