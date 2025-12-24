from omnipkg.common_utils import safe_print

"""
8pkg activate - Create a transparent environment where ALL commands auto-heal

This creates wrapper executables for ALL installed CLI tools so users never
have to prefix commands with '8pkg run'
"""
import importlib
try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata
import os
import shutil
import stat
import sys
import textwrap
from pathlib import Path
from typing import Set


class OmnipkgEnvironment:
    """Manages an activated omnipkg environment"""

    def __init__(self):
        self.env_dir = Path.home() / ".omnipkg" / "active_env"
        self.bin_dir = self.env_dir / "bin"
        self.wrappers_dir = self.env_dir / "wrappers"
        self.site_packages = (
            Path(sys.prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )

    def setup(self):
        """Create the omnipkg environment structure"""
        self.env_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(exist_ok=True)
        self.wrappers_dir.mkdir(exist_ok=True)

        safe_print(f"✅ Created omnipkg environment at: {self.env_dir}")

    def discover_cli_tools(self) -> Set[str]:
        """Find all CLI tools installed in the current environment"""
        cli_tools = set()

        # Method 1: Scan entry points
        for dist in importlib.metadata.distributions():
            if dist.entry_points:
                for ep in dist.entry_points:
                    if ep.group == "console_scripts":
                        cli_tools.add(ep.name)

        # Method 2: Scan bin directory
        real_bin = Path(sys.prefix) / "bin"
        if real_bin.exists():
            for item in real_bin.iterdir():
                if item.is_file() and os.access(item, os.X_OK):
                    # Skip common system binaries
                    if item.name not in {
                        "python",
                        "python3",
                        "pip",
                        "pip3",
                        "activate",
                        "conda",
                    }:
                        cli_tools.add(item.name)

        return cli_tools

    def create_wrapper(self, cli_name: str):
        """Create a wrapper script for a CLI tool that auto-heals conflicts"""

        wrapper_script = textwrap.dedent(
            '''#!/usr/bin/env python3
"""
Omnipkg auto-healing wrapper for: {cli_name}
This wrapper automatically resolves version conflicts before executing the CLI
"""
import sys
import os

# Ensure omnipkg is importable
if True:
    try:
        from omnipkg.cli_executor import CLIExecutor
        
        # Initialize executor
        executor = CLIExecutor(omnipkg_manager=None)
        
        # Execute with auto-healing
        exit_code = executor.execute_with_healing("{cli_name}", sys.argv[1:])
        sys.exit(exit_code)
        
    except ImportError:
        # Fallback: just run the command normally if omnipkg not available
        import subprocess
        import shutil
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
        
        # Find real executable (skip our wrapper)
        real_bin = shutil.which("{cli_name}", path=os.environ.get("OMNIPKG_REAL_PATH"))
        if real_bin:
            sys.exit(subprocess.call([real_bin] + sys.argv[1:]))
        else:
            print(f"Error: Could not find {cli_name}", file=sys.stderr)
            sys.exit(1)
'''
        )

        wrapper_path = self.bin_dir / cli_name
        wrapper_path.write_text(wrapper_script)
        wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IEXEC)

        return wrapper_path

    def activate(self, shell: str = None):
        """Activate the omnipkg environment"""

        # Auto-detect shell if not provided
        if not shell:
            shell = os.environ.get("SHELL", "").split("/")[-1]
            if not shell:
                shell = "bash"

        # Discover all CLI tools
        safe_print("🔍 Discovering installed CLI tools...")
        cli_tools = self.discover_cli_tools()
        safe_print(f"📦 Found {len(cli_tools)} CLI tools")

        # Create wrappers for all tools
        safe_print("🔧 Creating auto-healing wrappers...")
        created = 0
        for cli_name in cli_tools:
            try:
                self.create_wrapper(cli_name)
                created += 1
            except Exception as e:
                safe_print(f"⚠️  Could not create wrapper for {cli_name}: {e}", file=sys.stderr)

        safe_print(f"✅ Created {created} wrappers")

        # Generate activation script
        activation_script = self._generate_activation_script(shell)

        # Write activation script
        activate_file = self.env_dir / f"activate.{shell}"
        activate_file.write_text(activation_script)

        print(f"\n{'='*60}")
        safe_print("🎉 Omnipkg environment ready!")
        print(f"{'='*60}")
        print("\nTo activate, run:")
        print(f"  source {activate_file}")
        print("\nOnce activated, ALL CLI commands will auto-heal conflicts:")
        print("  lollama start-mining  # Just works!")
        print("  black --check .        # Just works!")
        print("  pytest                 # Just works!")

        return activate_file

    def _generate_activation_script(self, shell: str) -> str:
        """Generate shell-specific activation script"""

        if shell in ["bash", "zsh"]:
            return textwrap.dedent(
                f"""
# Omnipkg Environment Activation Script
# Save original PATH
export OMNIPKG_REAL_PATH="$PATH"
export OMNIPKG_ORIGINAL_PATH="$PATH"

# Prepend omnipkg wrappers to PATH
export PATH="{self.bin_dir}:$PATH"

# Set environment marker
export OMNIPKG_ACTIVE=1

# Update prompt
if [ -n "${{BASH_VERSION}}" ]; then
    export PS1="(omnipkg) $PS1"
elif [ -n "${{ZSH_VERSION}}" ]; then
    export PS1="(omnipkg) $PS1"
fi

echo "✅ Omnipkg environment activated"
echo "   All CLI commands now auto-heal conflicts"
echo "   Type 'deactivate' to exit"

# Deactivation function
deactivate() {{
    export PATH="$OMNIPKG_ORIGINAL_PATH"
    unset OMNIPKG_ACTIVE
    unset OMNIPKG_REAL_PATH
    unset OMNIPKG_ORIGINAL_PATH
    
    # Restore prompt
    if [ -n "${{BASH_VERSION}}" ]; then
        export PS1="${{PS1#(omnipkg) }}"
    elif [ -n "${{ZSH_VERSION}}" ]; then
        export PS1="${{PS1#(omnipkg) }}"
    fi
    
    echo "⏹️  Omnipkg environment deactivated"
    unset -f deactivate
}}
"""
            )
        else:
            raise ValueError(f"Unsupported shell: {shell}")

    def deactivate(self):
        """Clean up the environment"""
        safe_print("🧹 Cleaning up omnipkg environment...")
        if self.env_dir.exists():
            shutil.rmtree(self.env_dir)
        safe_print("✅ Omnipkg environment removed")


def cmd_activate(args):
    """Handle 8pkg activate command"""
    env = OmnipkgEnvironment()
    env.setup()

    shell = args[0] if args else None
    env.activate(shell)

    return 0


def cmd_deactivate(args):
    """Handle 8pkg deactivate command"""
    env = OmnipkgEnvironment()
    env.deactivate()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: 8pkg activate [shell]")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "activate":
        sys.exit(cmd_activate(args))
    elif cmd == "deactivate":
        sys.exit(cmd_deactivate(args))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
