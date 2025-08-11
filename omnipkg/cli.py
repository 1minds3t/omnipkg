#!/usr/bin/env python3
"""
omnipkg CLI
"""
import sys
import argparse
from .core import omnipkg, ConfigManager
from pathlib import Path
import textwrap

def print_header(title):
    """Prints a consistent, pretty header for CLI sections."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

def create_parser():
    """Creates and configures the argument parser."""
    parser = argparse.ArgumentParser(
        prog='omnipkg',
        description='The intelligent Python package manager that solves dependency hell.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent('''\
            
        Common Commands:
          omnipkg install <package>   Install a package with downgrade protection.
          omnipkg list                See all installed packages and their health.
          omnipkg status              Check the health of your multi-version environment.
          omnipkg info <package>      Get a detailed dashboard for a specific package.
          omnipkg stress-test         Run the ultimate compatibility stress test.
        ''')
    )
    
    subparsers = parser.add_subparsers(dest='command', help='All available commands:', required=True)

    install_parser = subparsers.add_parser('install', help='Install packages (with downgrade protection)')
    install_parser.add_argument('packages', nargs='*', help='Packages to install (e.g., "requests==2.25.1")')
    install_parser.add_argument('-r', '--requirement', help='Install from the given requirements file.', metavar='FILE')
    
    uninstall_parser = subparsers.add_parser('uninstall', help='Uninstall packages from main env or bubbles')
    uninstall_parser.add_argument('packages', nargs='+', help='Packages to uninstall')
    uninstall_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompts')

    info_parser = subparsers.add_parser('info', help='Show detailed package information')
    info_parser.add_argument('package', help='Package name to inspect')
    info_parser.add_argument('--version', default='active', help='Specific version to inspect')

    revert_parser = subparsers.add_parser('revert', help="Revert environment to the last known good state")
    revert_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    list_parser = subparsers.add_parser('list', help='List installed packages')
    list_parser.add_argument('filter', nargs='?', help='Optional filter for package names')

    status_parser = subparsers.add_parser('status', help='Show multi-version system status')

    demo_parser = subparsers.add_parser('demo', help='Run the interactive, automated demo')

    stress_parser = subparsers.add_parser('stress-test', help='Run the ultimate stress test with heavy-duty packages.')

    reset_parser = subparsers.add_parser('reset', help='DELETE and rebuild the omnipkg knowledge base in Redis')
    reset_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    rebuild_parser = subparsers.add_parser('rebuild-kb', help='Force a full rebuild of the knowledge base')
    rebuild_parser.add_argument('--force', '-f', action='store_true', help='Ignore cache and force re-processing')

    return parser

def main():
    """The main entry point for the CLI."""
    if len(sys.argv) == 1:
        cm = ConfigManager()
        # This part for running 'omnipkg' with no arguments is fine.
        print("👋 Welcome to omnipkg! Run `omnipkg --help` for commands.")
        return 0

    parser = create_parser()
    args = parser.parse_args()
    
    cm = ConfigManager()
    pkg_instance = omnipkg(cm.config)

    try:
        if args.command == 'install':
            packages_to_process = []
            if args.requirement:
                req_path = Path(args.requirement)
                if not req_path.is_file():
                    print(f"❌ Error: Requirements file not found at '{req_path}'")
                    return 1
                with open(req_path, 'r') as f:
                    packages_to_process = [line.split('#')[0].strip() for line in f if line.split('#')[0].strip()]
            elif args.packages:
                packages_to_process = args.packages
            else:
                print("❌ Error: You must specify packages or use -r. Ex: `omnipkg install requests`")
                return 1
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
        
        # ### MODIFIED: Fix for the TypeError ###
        elif args.command == 'demo':
            print_header("Demo Under Construction!")
            print("The interactive demo is still being polished.")
            print("For now, we'll run the 'stress-test' to showcase omnipkg's power.")
            # Fall-through to the stress-test logic
            from . import stress_test
            if input("\nProceed with the stress test? (y/n): ").lower() != 'y':
                print("Stress test cancelled.")
                return 0
            stress_test.run()  # <-- CORRECTED: Pass no arguments
            return 0
            
        elif args.command == 'stress-test':
            from . import stress_test
            print_header("omnipkg Ultimate Stress Test")
            print("This test will install, bubble, and test multiple large scientific packages.")
            print("\n⚠️  This will download several hundred MB and may take several minutes.")
            if input("\nProceed with the stress test? (y/n): ").lower() != 'y':
                print("Stress test cancelled.")
                return 0
            stress_test.run() # <-- CORRECTED: Pass no arguments
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