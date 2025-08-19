#!/usr/bin/env python3
"""
omnipkg CLI - Enhanced with runtime interpreter switching showcase
"""
import sys
import argparse
import subprocess
from pathlib import Path
import textwrap
import os
from .core import omnipkg as OmnipkgCore, ConfigManager

# Path to tests and demo directories

TESTS_DIR = Path(__file__).parent.parent / "tests"
DEMO_DIR = Path(__file__).parent

# --- Get version from package metadata ---

def get_version():
    """Get version from package metadata"""
    try:
        from importlib.metadata import version
        return version('omnipkg')
    except Exception:
        try:
            import tomllib
            toml_path = Path(__file__).parent.parent / "pyproject.toml"
            if toml_path.exists():
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                return data.get("project", {}).get("version", "unknown")
        except ImportError:
            try:
                import tomli
                toml_path = Path(__file__).parent.parent / "pyproject.toml"
                if toml_path.exists():
                    with open(toml_path, "rb") as f:
                        data = tomli.load(f)
                    return data.get("project", {}).get("version", "unknown")
            except ImportError:
                pass
        except Exception:
            pass
    return "unknown"

VERSION = get_version()

def check_python_311_requirement(test_name="stress test"):
    """Check if Python 3.11 is required and show blocking message if not"""
    if sys.version_info[:2] != (3, 11):
        print("=" * 60)
        print(f"  ⚠️  {test_name.title()} Requires Python 3.11")
        print("=" * 60)
        print(f"Current Python version: {sys.version_info.major}.{sys.version_info.minor}")
        print()
        print(f"The omnipkg {test_name} only works in Python 3.11 environments.")
        print(f"To run the {test_name}:")
        print("1. Create a Python 3.11 virtual environment")
        print("2. Install omnipkg in that environment")  
        print(f"3. Run the {test_name} from there")
        print()
        print("🔮 Coming Soon: Hot Python interpreter swapping mid-script!")
        print("   This will allow seamless switching between Python versions")
        print("   during package operations - stay tuned!")
        print("=" * 60)
        return False
    return True

def stress_test_command():
    """Handle stress test command - BLOCK if not Python 3.11"""
    if not check_python_311_requirement("stress test"):
        return False

    print("=" * 60)
    print("  🚀 omnipkg Nuclear Stress Test - Runtime Version Swapping")
    print("=" * 60)
    print("🎪 This demo showcases IMPOSSIBLE package combinations:")
    print("   • Runtime swapping between numpy/scipy versions mid-execution")
    print("   • Different numpy+scipy combos (1.24.3+1.12.0 → 1.26.4+1.16.1)")
    print("   • Previously 'incompatible' versions working together seamlessly") 
    print("   • Live PYTHONPATH manipulation without process restart")
    print("   • Space-efficient deduplication (shows deduplication - normally")
    print("     we average ~60% savings, but less for C extensions/binaries)")
    print()
    print("🤯 What makes this impossible with traditional tools:")
    print("   • numpy 1.24.3 + scipy 1.12.0 → 'incompatible dependencies'")
    print("   • Switching versions requires environment restart")
    print("   • Dependency conflicts prevent coexistence")
    print("   • Package managers can't handle multiple versions")
    print()
    print("✨ omnipkg does this LIVE, in the same Python process!")
    print("📊 Expected downloads: ~500MB | Duration: 30 seconds - 3 minutes")
    try:
        response = input("🚀 Ready to witness the impossible? (y/n): ").lower().strip()
    except EOFError:
        response = 'n'
    if response == 'y':
        return True
    else:
        print("🎪 Cancelled. Run 'omnipkg stress-test' anytime!")
        return False

def run_actual_stress_test():
    """Run the actual stress test - only called if Python 3.11"""
    print("🔥 Starting stress test…")
    try:
        from . import stress_test
        stress_test.run()
    except ImportError:
        print("❌ Stress test module not found. Implementation needed.")
        print("💡 This would run the actual stress test with:")
        print("   • Large package installations (TensorFlow, PyTorch, etc.)")
        print("   • Version conflict demonstrations")
        print("   • Real-time bubbling and deduplication")
    except Exception as e:
        print(f"❌ An error occurred during stress test execution: {e}")
        import traceback
        traceback.print_exc()

def run_demo_with_live_streaming(test_file, demo_name):
    """Run a demo with true live streaming output"""
    print(f"🚀 Running {demo_name.capitalize()} test from {test_file}…")
    print("📡 Live streaming output (this may take several minutes for heavy packages)…")
    print("💡 Don’t worry if there are pauses - packages are downloading/installing!")
    print("🛑 Press Ctrl+C to safely cancel if needed")
    print("-" * 60)

    try:
        # Method 1: Direct execution without pipes (best for real-time)
        # This allows the subprocess to write directly to our terminal
        process = subprocess.Popen(
            [sys.executable, str(test_file)],
            # Don't redirect stdout/stderr - let it go directly to terminal
            # This ensures immediate output without buffering
            bufsize=0,  # Unbuffered
            text=True
        )
        
        # Wait for completion and get return code
        returncode = process.wait()
        
        print("-" * 60)
        if returncode == 0:
            if demo_name == "tensorflow":
                print("😎 TensorFlow escaped the matrix! 🚀")
            print("🎉 Demo completed successfully!")
            print("💡 Run 'omnipkg demo' to try another test.")
        else:
            print(f"❌ Demo failed with return code {returncode}")
            print("💡 Check the output above for error details.")
        
        return returncode
        
    except KeyboardInterrupt:
        print("\n⚠️  Demo cancelled by user (Ctrl+C)")
        print("🛡️  Your environment should be safe - omnipkg handles interruptions gracefully")
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
            except:
                pass
        return 130  # Standard exit code for Ctrl+C
        
    except Exception as e:
        print(f"❌ Demo failed with error: {e}")
        print("📋 Full traceback:")
        import traceback
        traceback.print_exc()
        return 1

def run_demo_with_fallback_streaming(test_file, demo_name):
    """Fallback method with manual streaming if direct doesn't work"""
    print(f"🚀 Running {demo_name.capitalize()} test from {test_file}…")
    print("📡 Streaming output in real-time…")
    print("💡 Heavy package installations may have natural pauses - this is normal!")
    print("🛑 Press Ctrl+C to safely cancel")
    print("-" * 60)

    try:
        # Method 2: Capture with immediate flushing
        process = subprocess.Popen(
            [sys.executable, str(test_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # Unbuffered
            text=True,
            # Force line buffering in subprocess
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}
        )
        
        # Read output character by character for true real-time streaming
        while True:
            # Read one character at a time to avoid line buffering delays
            char = process.stdout.read(1)
            if char == '' and process.poll() is not None:
                break
            if char:
                print(char, end='', flush=True)
        
        returncode = process.wait()
        
        print("\n" + "-" * 60)
        if returncode == 0:
            if demo_name == "tensorflow":
                print("😎 TensorFlow escaped the matrix! 🚀")
            print("🎉 Demo completed successfully!")
            print("💡 Run 'omnipkg demo' to try another test.")
        else:
            print(f"❌ Demo failed with return code {returncode}")
            
        return returncode
        
    except KeyboardInterrupt:
        print("\n⚠️  Demo cancelled by user (Ctrl+C)")
        print("🛡️  Cleaning up safely...")
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
        print(f"\n❌ Demo failed with error: {e}")
        return 1

def create_parser():
    """Creates and configures the argument parser."""
    parser = argparse.ArgumentParser(
        prog='omnipkg',
        description='🚀 The intelligent Python package manager that eliminates dependency hell',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(f'''\

    🌟 Key Features:
      • Runtime Python interpreter switching (no shell restart needed!)
      • Automatic version bubbling to prevent conflicts
      • Downgrade protection with smart conflict resolution
      • Multi-version package coexistence
      • Intelligent dependency management with Redis-backed knowledge base

    📖 Essential Commands:
      omnipkg install <package>   Install with automatic conflict resolution
      omnipkg install-with-deps <package>  Install with specific dependency versions
      omnipkg list [filter]       View all packages and their bubble status  
      omnipkg status              Check multi-version environment health
      omnipkg info <package>      Interactive package dashboard with version explorer
      omnipkg demo                Run interactive demos for version switching
      omnipkg stress-test         See the magic! Heavy-duty package installation demo

    🎯 Advanced Features:
      omnipkg revert             Roll back to last known good state
      omnipkg uninstall <pkg>    Smart removal with dependency checking
      omnipkg rebuild-kb         Refresh the intelligence knowledge base

    💡 Installation Examples:
      omnipkg install requests numpy>=1.20        # Multiple packages
      omnipkg install uv==0.7.13 uv==0.7.14      # Multiple versions (auto-bubbled!)
      omnipkg install-with-deps tensorflow==2.13.0 numpy==1.24.3 typing-extensions==4.5.0
      omnipkg install -r requirements.txt        # From requirements file
      omnipkg install 'django>=3.0,<4.0'         # Complex version specs

    🔍 Understanding Your Environment:
      omnipkg list                # Shows ✅ active and 🫧 bubbled versions
      omnipkg info <package>      # Deep dive into any package's status
      omnipkg status              # Overall environment health

    🛠️ Redis Knowledge Base (Advanced):
      omnipkg stores rich metadata in Redis. Explore with:
      redis-cli HGETALL omnipkg:pkg:<package>                    # Package info
      redis-cli SMEMBERS "omnipkg:pkg:<package>:installed_versions"  # All versions
      redis-cli HGETALL omnipkg:pkg:<package>:<version>          # Version details

    🔧 Python Version Management:
      omnipkg automatically manages Python interpreters! When you run commands
      that need a different Python version, omnipkg will:
      • Download and install the required Python version seamlessly
      • Switch interpreters mid-execution without shell restart
      • Maintain package isolation across Python versions
      • Keep your environment clean and organized

    💡 Pro Tips:
      • Run 'omnipkg demo' to try version-switching demos
      • Run 'omnipkg stress-test' to see automated interpreter switching
      • Use 'omnipkg info <package>' for interactive version selection
      • The system learns from conflicts and prevents future issues
      • All changes are logged and reversible with 'omnipkg revert'

    Version: {VERSION}
    ''')
)

    parser.add_argument(
        '-v', '--version', action='version',
        version=f'%(prog)s {VERSION}'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands:', required=False)

    install_parser = subparsers.add_parser('install', 
        help='Install packages with intelligent conflict resolution',
        description='Install packages with automatic version management and conflict resolution')
    install_parser.add_argument('packages', nargs='*', help='Packages to install (e.g., "requests==2.25.1", "numpy>=1.20")')
    install_parser.add_argument(
        '-r', '--requirement', 
        help='Install from requirements file with smart dependency resolution',
        metavar='FILE'
    )

    install_with_deps_parser = subparsers.add_parser('install-with-deps',
        help='Install a package with specific dependency versions',
        description='Install a package with specific dependency versions in a bubble to avoid conflicts')
    install_with_deps_parser.add_argument('package', help='Package to install (e.g., "tensorflow==2.13.0")')
    install_with_deps_parser.add_argument('--dependency', action='append', help='Dependency with version (e.g., "numpy==1.24.3")', default=[])

    uninstall_parser = subparsers.add_parser('uninstall', 
        help='Intelligently remove packages and their dependencies',
        description='Smart package removal with safety features')
    uninstall_parser.add_argument('packages', nargs='+', help='Packages to uninstall (removes all versions)')
    uninstall_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompts')

    info_parser = subparsers.add_parser('info', 
        help='Interactive package explorer with version management',
        description='Explore package details, dependencies, and manage versions interactively')
    info_parser.add_argument('package', help='Package name to explore')
    info_parser.add_argument('--version', default='active', help='Specific version to inspect (default: active)')

    revert_parser = subparsers.add_parser('revert', 
        help="Time-travel back to your last known good environment",
        description='Revert all changes to the last stable environment state')
    revert_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation and revert immediately')

    list_parser = subparsers.add_parser('list', 
        help='View all installed packages and their bubble status',
        description='List all packages with detailed status indicators')
    list_parser.add_argument('filter', nargs='?', help='Filter packages by name pattern')

    status_parser = subparsers.add_parser('status', 
        help='Multi-version environment health dashboard',
        description='Overview of your Python interpreters, packages, and bubble isolation')

    demo_parser = subparsers.add_parser('demo', 
        help='Interactive demo for version switching',
        description='''Run interactive demos showcasing omnipkg's version-switching capabilities:
    1. Rich test (Python module switching)
    2. UV test (binary switching)
    3. NumPy + SciPy stress test (C-extension switching) - Requires Python 3.11
    4. TensorFlow test (complex dependency switching)
    Note: The Flask demo is under construction and not currently available.''')

    stress_parser = subparsers.add_parser('stress-test', 
        help='Ultimate demonstration with heavy scientific packages',
        description='Showcase omnipkg\'s features with large package installations and version switching')

    reset_parser = subparsers.add_parser('reset', 
        help='Clean slate: rebuild the omnipkg knowledge base',
        description='Delete and rebuild the Redis knowledge base from scratch')
    reset_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    rebuild_parser = subparsers.add_parser('rebuild-kb', 
        help='Refresh the intelligence knowledge base',
        description='Force rebuild of package metadata and dependency intelligence')
    rebuild_parser.add_argument('--force', '-f', action='store_true', help='Ignore cache and force complete rebuild')

    reset_config_parser = subparsers.add_parser('reset-config', 
        help='Delete the config file to trigger a fresh setup',
        description='Deletes the omnipkg config.json file')
    reset_config_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation and delete immediately')

    return parser

def print_header(title):
    """Print a formatted header."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

def main():
    """
    Main function to parse arguments and execute omnipkg commands.
    """
    parser = argparse.ArgumentParser(
        description='omnipkg - The "Freedom" Edition v2. An intelligent Python package manager.',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '-v', '--version',
        action='version',
        version='omnipkg 2.0.0' # Replace with dynamic version if you have one
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands', required=True)

    # --- Installation Commands ---
    parser_install = subparsers.add_parser('install', help='Install packages intelligently, protecting the environment.')
    parser_install.add_argument('packages', nargs='+', help='One or more packages to install (e.g., "flask==2.0.0").')
    parser_install.add_argument('--dry-run', action='store_true', help='Simulate the installation without making any changes.')

    # --- Uninstallation Command ---
    parser_uninstall = subparsers.add_parser('uninstall', help='Uninstall packages from the main environment or from bubbles.')
    parser_uninstall.add_argument('packages', nargs='+', help='One or more packages to uninstall.')
    parser_uninstall.add_argument('--yes', '-y', dest='force', action='store_true', help='Confirm uninstallation without prompting.')

    # --- Inspection Commands ---
    parser_info = subparsers.add_parser('info', help='Display detailed, multi-version information about a package.')
    parser_info.add_argument('package_name', help='The name of the package to inspect.')
    parser_info.add_argument('version', nargs='?', default='active', help='The specific version to inspect (optional, defaults to active).')

    parser_list = subparsers.add_parser('list', help='List all known packages and their installed (active/bubbled) versions.')
    parser_list.add_argument('pattern', nargs='?', default=None, help='A pattern to filter package names (optional).')

    parser_status = subparsers.add_parser('status', help='Show the status of the multiversion environment and bubbles.')

    # --- Environment Management Commands ---
    parser_revert = subparsers.add_parser('revert', help='Revert the environment to the last known good snapshot.')
    parser_revert.add_argument('--yes', '-y', dest='force', action='store_true', help='Confirm revert operation without prompting.')

    # --- Maintenance Commands ---
    parser_reset = subparsers.add_parser('reset', help='Clear ALL omnipkg data from Redis and rebuild the knowledge base.')
    parser_reset.add_argument('--yes', '-y', dest='force', action='store_true', help='Confirm reset without prompting.')

    parser_rebuild = subparsers.add_parser('rebuild-kb', help='Force a rebuild of the knowledge base from the file system.')
    parser_rebuild.add_argument('--force', '-f', action='store_true', help='Force a full rebuild, ignoring any existing caches.')

    parser_reset_config = subparsers.add_parser('reset-config', help='Delete the configuration file to trigger first-time setup on next run.')
    parser_reset_config.add_argument('--yes', '-y', dest='force', action='store_true', help='Confirm config deletion without prompting.')

    # Parse the arguments from the command line
    args = parser.parse_args()

    # --- Initialization and Command Execution ---
    try:
        config_manager = ConfigManager()
        #
        # THIS IS THE CORRECTED LINE:
        #
        pkg_instance = OmnipkgCore(config_manager.config)
        #
        #
        #
    except Exception as e:
        print(f"❌ Fatal Error: Could not initialize omnipkg. {e}", file=sys.stderr)
        sys.exit(1)

    # --- Dispatch commands to the core engine ---
    # Each command calls the relevant method and exits with its return code.
    # This ensures that successful commands return 0 and failed commands return 1.
    if args.command == 'install':
        sys.exit(pkg_instance.smart_install(args.packages, dry_run=args.dry_run))

    elif args.command == 'uninstall':
        sys.exit(pkg_instance.smart_uninstall(args.packages, force=args.force))

    elif args.command == 'info':
        sys.exit(pkg_instance.show_package_info(args.package_name, args.version))

    elif args.command == 'list':
        sys.exit(pkg_instance.list_packages(pattern=args.pattern))

    elif args.command == 'status':
        sys.exit(pkg_instance.show_multiversion_status())

    elif args.command == 'revert':
        sys.exit(pkg_instance.revert_to_last_known_good(force=args.force))

    elif args.command == 'reset':
        sys.exit(pkg_instance.reset_knowledge_base(force=args.force))

    elif args.command == 'rebuild-kb':
        sys.exit(pkg_instance.rebuild_knowledge_base(force=args.force))

    elif args.command == 'reset-config':
        sys.exit(pkg_instance.reset_configuration(force=args.force))

    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
