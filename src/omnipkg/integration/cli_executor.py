from omnipkg.common_utils import safe_print

"""
Enhanced 8pkg run - Support both Python scripts AND CLI executables
This makes omnipkg actually usable for real development work
"""
import importlib
try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class CLIExecutor:
    """Handles execution of both scripts and CLI commands with auto-healing"""

    def __init__(self, omnipkg_manager):
        self.manager = omnipkg_manager
        self.site_packages = (
            Path(sys.prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )

    def is_installed_cli(self, command: str) -> Optional[Path]:
        """Check if command is an installed Python CLI tool"""
        # Check in bin directory
        bin_dir = Path(sys.prefix) / "bin"
        cli_path = bin_dir / command

        if cli_path.exists():
            return cli_path

        # Also check Scripts on Windows
        if os.name == "nt":
            scripts_dir = Path(sys.prefix) / "Scripts"
            cli_path = scripts_dir / f"{command}.exe"
            if cli_path.exists():
                return cli_path

        return None

    def get_cli_package_info(self, cli_name: str) -> Optional[Dict]:
        """Find which package provides this CLI command"""
        try:
            # Check all installed packages for entry points
            for dist in importlib.metadata.distributions():
                if dist.entry_points:
                    for ep in dist.entry_points:
                        if ep.group == "console_scripts" and ep.name == cli_name:
                            return {
                                "package": dist.name,
                                "version": dist.version,
                                "entry_point": ep,
                                "module": ep.value.split(":")[0],
                            }
        except Exception as e:
            safe_print(f"⚠️  Error scanning entry points: {e}", file=sys.stderr)
        return None

    def detect_cli_conflicts(self, cli_name: str, package_info: Dict) -> List[Tuple[str, str, str]]:
        """Detect version conflicts for a CLI tool and its dependencies"""
        conflicts = []

        # Get the package's dependencies
        try:
            dist = importlib.metadata.distribution(package_info["package"])
            if dist.requires:
                for req_str in dist.requires:
                    # Parse requirement (e.g., "pydantic-core==2.41.4")
                    if "==" in req_str:
                        pkg_name = req_str.split("==")[0].strip()
                        needed_version = req_str.split("==")[1].split(";")[0].strip()

                        # Check what's actually installed
                        try:
                            installed = importlib.metadata.version(pkg_name)
                            if installed != needed_version:
                                conflicts.append((pkg_name, needed_version, installed))
                        except:
                            pass
        except Exception as e:
            safe_print(f"⚠️  Error checking dependencies: {e}", file=sys.stderr)

        return conflicts

    def execute_with_healing(self, command: str, args: List[str]) -> int:
        """Execute a CLI command with automatic conflict resolution"""

        # Check if it's a script file
        if os.path.exists(command) and command.endswith(".py"):
            safe_print(f"🐍 Detected Python script: {command}", file=sys.stderr)
            return self._execute_script(command, args)

        # Check if it's an installed CLI tool
        cli_path = self.is_installed_cli(command)
        if not cli_path:
            # Try to find it in PATH
            cli_path = shutil.which(command)
            if not cli_path:
                safe_print(f"❌ Error: Command not found: {command}", file=sys.stderr)
                return 1

        safe_print(f"🔧 Detected CLI command: {command}", file=sys.stderr)

        # Get package info
        package_info = self.get_cli_package_info(command)
        if not package_info:
            safe_print(
                f"⚠️  Could not determine package for {command}, running without healing",
                file=sys.stderr,
            )
            return subprocess.call([command] + args)

        safe_print(
            f"📦 CLI provided by: {package_info['package']}=={package_info['version']}",
            file=sys.stderr,
        )

        # Detect conflicts
        conflicts = self.detect_cli_conflicts(command, package_info)

        if conflicts:
            safe_print(f"🔍 Detected {len(conflicts)} dependency conflicts:", file=sys.stderr)
            for pkg, needed, found in conflicts:
                print(f"   - {pkg}: need {needed}, found {found}", file=sys.stderr)

            # Auto-heal the conflicts
            return self._execute_with_bubbles(command, args, conflicts)
        else:
            safe_print("✅ No conflicts detected, running normally", file=sys.stderr)
            return subprocess.call([command] + args)

    def _execute_with_bubbles(
        self, command: str, args: List[str], conflicts: List[Tuple[str, str, str]]
    ) -> int:
        """Execute command with bubbled dependencies"""

        safe_print("🌀 Activating bubbles for conflict resolution...", file=sys.stderr)

        # Create a wrapper script that activates bubbles then runs the CLI
        wrapper_code = """
import sys
import os
from omnipkgLoader import omnipkgLoader

# Initialize loader with dummy script name
loader = omnipkgLoader(None, auto_detect=False)

# Activate required bubbles
conflicts = {conflicts}
for pkg_name, needed_version, _ in conflicts:
    try:
        safe_print(f"🔄 Activating {{pkg_name}}=={{needed_version}}", file=sys.stderr)
        loader.activate(pkg_name, needed_version)
    except Exception as e:
        safe_print(f"⚠️  Could not activate {{pkg_name}}=={{needed_version}}: {{e}}", file=sys.stderr)

# Now run the actual CLI command
import subprocess
result = subprocess.call({[command] + args})

# Cleanup
loader.deactivate_all()
sys.exit(result)
\ntry:\n    from .common_utils import safe_print\nexcept ImportError:\n    from omnipkg.common_utils import safe_print"""

        # Write wrapper to temp file
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(wrapper_code)
            wrapper_path = f.name

        try:
            # Execute wrapper
            result = subprocess.call([sys.executable, wrapper_path])
            return result
        finally:
            # Cleanup
            try:
                os.unlink(wrapper_path)
            except:
                pass

    def _execute_script(self, script: str, args: List[str]) -> int:
        """Execute a Python script (existing functionality)"""
        # Delegate to existing script runner
        safe_print(f"🐍 Executing Python script: {script}", file=sys.stderr)
        # ... existing script execution logic ...
        return subprocess.call([sys.executable, script] + args)


def handle_run_command(args: List[str]):
    """
    Enhanced run command handler
    Usage: 8pkg run <command> [args...]

    Supports:
    - Python scripts: 8pkg run my_script.py --arg1 --arg2
    - CLI commands: 8pkg run lollama start-mining
    - Any executable: 8pkg run black --check .
    """
    if not args:
        print("Usage: 8pkg run <command> [args...]", file=sys.stderr)
        print("\nExamples:", file=sys.stderr)
        print("  8pkg run my_script.py --arg1", file=sys.stderr)
        print("  8pkg run lollama start-mining", file=sys.stderr)
        print("  8pkg run black --check .", file=sys.stderr)
        return 1

    command = args[0]
    cmd_args = args[1:]

    # Initialize omnipkg (you'd get this from your actual manager)
    executor = CLIExecutor(omnipkg_manager=None)

    return executor.execute_with_healing(command, cmd_args)


# Test the enhanced functionality
if __name__ == "__main__":
    # Example: 8pkg run lollama start-mining
    sys.exit(handle_run_command(sys.argv[1:]))
