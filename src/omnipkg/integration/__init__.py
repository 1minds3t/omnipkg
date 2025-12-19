"""
Integration utilities for omnipkg environment activation and CLI execution.

This module provides tools for:
- Environment activation with transparent command wrapping
- CLI executor for auto-healing command execution
- CI/CD integration helpers
"""

from .cli_executor import CLIExecutor, handle_run_command
from .environment import OmnipkgEnvironment, cmd_activate, cmd_deactivate

__all__ = [
    "OmnipkgEnvironment",
    "CLIExecutor",
    "cmd_activate",
    "cmd_deactivate",
    "handle_run_command",
]
