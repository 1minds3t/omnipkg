from __future__ import annotations

import sys
from typing import List, Optional

import typer

app = typer.Typer()

from omnipkg.common_utils import safe_print

"""
Integration guide for adding CLI execution support to omnipkg

Add these commands to your main CLI handler (cli.py)
"""

# In your cli.py, add these imports:
from omnipkg.cli_executor import handle_run_command
from omnipkg.omnipkg_activate import cmd_activate, cmd_deactivate


# Then enhance your run command:
@app.command()
def run(
    command: str = typer.Argument(..., help="Command or script to run"),
    args: List[str] = typer.Argument(None, help="Arguments to pass to the command"),
):
    """
    Run a command or script with automatic conflict resolution

    Examples:
        8pkg run script.py --arg1 --arg2
        8pkg run lollama start-mining
        8pkg run black --check .
        8pkg run pytest tests/
    """
    import sys

    # Combine command and args for execution
    all_args = [command] + (args or [])
    exit_code = handle_run_command(all_args)
    sys.exit(exit_code)


# Add activation commands:
@app.command()
def activate(shell: Optional[str] = typer.Option(None, help="Shell type (bash, zsh)")):
    """
    Activate omnipkg environment for transparent conflict resolution

    Once activated, ALL CLI commands automatically resolve conflicts.
    You never need to prefix commands with '8pkg run' again!

    Example:
        8pkg activate
        source ~/.omnipkg/active_env/activate.bash

        # Now just use commands normally:
        lollama start-mining  # Auto-heals conflicts!
        black .               # Auto-heals conflicts!
    """
    import sys

    sys.exit(cmd_activate([shell] if shell else []))


@app.command()
def deactivate():
    """
    Deactivate and clean up omnipkg environment
    """


try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

    sys.exit(cmd_deactivate([]))


# Also add a helper status command:
@app.command()
def status():
    """Show omnipkg environment status"""
    import os
    from pathlib import Path

    is_active = os.environ.get("OMNIPKG_ACTIVE") == "1"
    env_dir = Path.home() / ".omnipkg" / "active_env"

    if is_active:
        safe_print("✅ Omnipkg environment is ACTIVE")
        print(f"   Wrappers directory: {env_dir / 'bin'}")

        # Count wrappers
        bin_dir = env_dir / "bin"
        if bin_dir.exists():
            wrapper_count = len(list(bin_dir.iterdir()))
            print(f"   Active wrappers: {wrapper_count}")
    else:
        safe_print("⏹️  Omnipkg environment is NOT active")

        if env_dir.exists():
            print(f"   (Environment exists at {env_dir})")
            print("   Run: 8pkg activate")
        else:
            print("   Run: 8pkg activate  # to create environment")


"""
USAGE EXAMPLES:

# Before - broken:
$ lollama start-mining
SystemError: pydantic-core version mismatch...

# Option 1: Use 8pkg run (still requires prefix):
$ 8pkg run lollama start-mining
✅ Works! Auto-heals conflicts

# Option 2: Activate environment (best UX):
$ 8pkg activate
$ source ~/.omnipkg/active_env/activate.bash
$ lollama start-mining  # Just works now!
$ black --check .        # Just works!
$ pytest                 # Just works!
$ deactivate             # When done

# Check status anytime:
$ 8pkg status
✅ Omnipkg environment is ACTIVE
   Active wrappers: 47
"""

# IMPLEMENTATION CHECKLIST:
"""
[ ] 1. Add CLIExecutor class to omnipkg/cli_executor.py
[ ] 2. Add OmnipkgEnvironment class to omnipkg/omnipkg_activate.py  
[ ] 3. Update cli.py with new commands (run, activate, deactivate, status)
[ ] 4. Test basic execution:
       - 8pkg run script.py
       - 8pkg run lollama start-mining
[ ] 5. Test environment activation:
       - 8pkg activate
       - source activate script
       - Run commands without prefix
[ ] 6. Test conflict detection and healing
[ ] 7. Document in README
"""
