from __future__ import annotations  # Python 3.6+ compatibility

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# Keep a reference to the original, built-in print function
_builtin_print = print

"""
Add these functions to /home/minds3t/omnipkg/src/omnipkg/common_utils.py
They provide standardized, bulletproof solutions for subprocess config passing and printing.
"""


# Keep a reference to the original print
_builtin_print = print


def safe_print(*args, **kwargs):
    """
    Ultra-robust print: Handles Windows encoding issues and prevents shell crashes.
    Detects non-UTF8 sessions (like cp1252) and strips emojis to prevent mojibake.
    """
    if "flush" not in kwargs:
        kwargs["flush"] = True
    try:
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            safe_args = []
            # Get shell encoding (often cp1252 on legacy Windows)
            encoding = sys.stdout.encoding or "utf-8"
            for arg in args:
                if isinstance(arg, str):
                    # If shell is not UTF-8, strip problematic symbols
                    if sys.platform == "win32" and encoding.lower() not in [
                        "utf-8",
                        "utf8",
                    ]:
                        import unicodedata

                        arg = "".join(
                            (c if ord(c) < 128 or unicodedata.category(c)[0] != "S" else "?")
                            for c in arg
                        )
                    safe_args.append(arg.encode(encoding, "replace").decode(encoding))
                else:
                    safe_args.append(arg)
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            _builtin_print("[omnipkg: Encoding Error - Shell might not support UTF-8]", flush=True)

def safe_unlink(path: Path) -> None:
    """Python 3.7 compatible unlink that ignores missing files."""
    if path.exists():
        path.unlink()

def pass_config_to_subprocess(config_dict: Dict[str, Any]) -> str:
    """
    Bulletproof way to pass configuration to a subprocess.

    Creates a temporary JSON file and returns the path.
    This completely avoids Windows path escaping nightmares.

    Usage in subprocess script:
        config_path = sys.argv[1]
        with open(config_path, 'r') as f:
            config = json.load(f)

    Returns:
        Path to temporary config file (caller should clean up after subprocess completes)
    """
    fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="omnipkg_config_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config_dict, f, indent=2)
        return temp_path
    except Exception:
        # Clean up on error
        try:
            os.close(fd)
        except:
            pass
        try:
            os.unlink(temp_path)
        except:
            pass
        raise


def safe_subprocess_call(
    cmd: List[str],
    config: Optional[Dict[str, Any]] = None,
    stream_output: bool = True,
    timeout: Optional[int] = None,
) -> tuple[int, str, str]:
    """
    Standardized subprocess call that handles:
    - Config passing via temp file (no JSON escaping issues)
    - Unicode output on Windows
    - Real-time output streaming
    - Proper cleanup

    Args:
        cmd: Command list (e.g., [sys.executable, 'script.py'])
        config: Optional config dict to pass via temp file
        stream_output: If True, print output in real-time
        timeout: Optional timeout in seconds

    Returns:
        (returncode, stdout, stderr)
    """
    config_file = None

    try:
        # Pass config via temp file if provided
        if config:
            config_file = pass_config_to_subprocess(config)
            cmd = cmd + ["--config-file", config_file]

        # Set up subprocess with UTF-8 encoding
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",  # Replace bad chars with ?
            bufsize=1,
            universal_newlines=True,
        )

        stdout_lines = []
        stderr_lines = []

        # Stream output if requested
        if stream_output:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    safe_print(f"   {line}")
                stdout_lines.append(line)
        else:
            stdout_lines = process.stdout.readlines()

        # Wait for completion
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = -1
            safe_print(f"⚠️ Process timed out after {timeout} seconds")

        # Read stderr
        stderr_lines = process.stderr.readlines()

        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)

        return returncode, stdout, stderr

    finally:
        # Always clean up temp config file
        if config_file:
            try:
                os.unlink(config_file)
            except:
                pass


def create_subprocess_script_with_config(
    script_content: str, config_dict: Dict[str, Any], script_name: str = "temp_script"
) -> str:
    """
    Creates a temporary Python script that loads config from a file.

    This is the PROPER way to pass config to subprocess scripts - no string escaping issues!

    Args:
        script_content: Python code (should start with imports)
        config_dict: Configuration dictionary
        script_name: Name for the temp script file

    Returns:
        Path to the created script file

    Example script_content should start with:
        ```python
        import sys
        import json

        # Load config from file passed as first argument
        with open(sys.argv[1], 'r') as f:
            config = json.load(f)

        # Your code here using config
        ```
    """
    fd, script_path = tempfile.mkstemp(suffix=".py", prefix=f"omnipkg_{script_name}_")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script_content)
        return script_path
    except Exception:
        try:
            os.close(fd)
        except:
            pass
        try:
            os.unlink(script_path)
        except:
            pass
        raise


# Convenience wrapper for the most common use case
def run_python_script_with_config(
    script_content: str,
    config_dict: Dict[str, Any],
    python_exe: Optional[str] = None,
    stream_output: bool = True,
    cleanup: bool = True,
) -> tuple[int, str, str]:
    """
    All-in-one: Create script, pass config, run it, clean up.

    This is what you want for combo tests and similar scenarios.

    Args:
        script_content: Python code (will auto-add config loading if needed)
        config_dict: Configuration to pass
        python_exe: Python interpreter to use (default: sys.executable)
        stream_output: Print output in real-time
        cleanup: Delete temp files after execution

    Returns:
        (returncode, stdout, stderr)
    """
    python_exe = python_exe or sys.executable
    config_file = None
    script_file = None

    try:
        # Create config file
        config_file = pass_config_to_subprocess(config_dict)

        # Inject config loading at the start of script if not present
        if "sys.argv[1]" not in script_content and "config =" not in script_content:
            config_loading = """
import sys
import json

# Auto-injected config loading
with open(sys.argv[1], 'r') as f:
    config = json.load(f)

"""
            script_content = config_loading + script_content

        # Create script file
        script_file = create_subprocess_script_with_config(
            script_content, config_dict, "run_with_config"
        )

        # Run it
        cmd = [python_exe, script_file, config_file]
        returncode, stdout, stderr = safe_subprocess_call(
            cmd, config=None, stream_output=stream_output  # Already passed via argv
        )

        return returncode, stdout, stderr

    finally:
        # Clean up temp files
        if cleanup:
            for path in [config_file, script_file]:
                if path:
                    try:
                        os.unlink(path)
                    except:
                        pass


def run_command(command_list, check=True):
    """
    Helper to run a command and stream its output.
    Raises RuntimeError on non-zero exit code, with captured output.
    """
    from omnipkg.i18n import _

    if command_list[0] == "omnipkg":
        command_list = [sys.executable, "-m", "omnipkg.cli"] + command_list[1:]
    process = subprocess.Popen(
        command_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    output_lines = []
    for line in iter(process.stdout.readline, ""):
        stripped_line = line.strip()
        safe_print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if retcode != 0:
        error_message = _("Subprocess command '{}' failed with exit code {}.").format(
            " ".join(command_list), retcode
        )
        if output_lines:
            error_message += "\nSubprocess Output:\n" + "\n".join(output_lines)
        raise RuntimeError(error_message)
    return retcode


class ProcessCorruptedException(Exception):
    """
    Raised when omnipkg detects that the current process memory is corrupted
    by a C++ state collision and cannot be recovered without a full restart.
    """

    pass


class UVFailureDetector:
    """Detects UV dependency resolution failures."""

    FAILURE_PATTERNS = [
        "No solution found when resolving dependencies",
        "ResolutionImpossible",
        "Could not find a version that satisfies",
    ]
    CONFLICT_PATTERN = "([a-zA-Z0-9_-]+==[0-9.]+[a-zA-Z0-9_.-]*)"

    def detect_failure(self, stderr_output):
        """Check if UV output contains dependency resolution failure"""
        for pattern in self.FAILURE_PATTERNS:
            if re.search(pattern, stderr_output, re.IGNORECASE):
                return True
        return False

    def extract_required_dependency(self, stderr_output: str) -> Optional[str]:
        """
        Extracts the first specific conflicting package==version from the error message.
        """
        matches = re.findall(self.CONFLICT_PATTERN, stderr_output)
        if matches:
            for line in stderr_output.splitlines():
                if "your project requires" in line:
                    sub_matches = re.findall(self.CONFLICT_PATTERN, line)
                    if sub_matches:
                        return sub_matches[0].strip().strip("'\"")
            return matches[0].strip().strip("'\"")
        return None


def debug_python_context(label=""):
    """Print comprehensive Python context information for debugging."""
    print(f"\n{'='*70}")
    safe_print(f"🔍 DEBUG CONTEXT CHECK: {label}")
    print(f"{'='*70}")
    safe_print(f"📍 sys.executable:        {sys.executable}")
    safe_print(f"📍 sys.version:           {sys.version}")
    safe_print(
        f"📍 sys.version_info:      {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    safe_print(f"📍 os.getpid():           {os.getpid()}")
    safe_print(f"📍 __file__ (if exists):  {__file__ if '__file__' in globals() else 'N/A'}")
    safe_print(f"📍 Path.cwd():            {Path.cwd()}")

    # Environment variables that might affect context
    relevant_env_vars = [
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "OMNIPKG_MAIN_ORCHESTRATOR_PID",
        "OMNIPKG_RELAUNCHED",
        "OMNIPKG_LANG",
        "PYTHONHOME",
        "PYTHONEXECUTABLE",
    ]
    safe_print("\n📦 Relevant Environment Variables:")
    for var in relevant_env_vars:
        value = os.environ.get(var, "NOT SET")
        print(f"   {var}: {value}")

    # Check sys.path for omnipkg locations
    safe_print("\n📂 sys.path (first 5 entries):")
    for i, path in enumerate(sys.path[:5]):
        print(f"   [{i}] {path}")

    print(f"{'='*70}\n")


def sync_context_to_runtime():
    """
    Ensures omnipkg's active context matches the currently running Python interpreter.
    This version is now silent for production use.
    """
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _

    try:
        config_manager = ConfigManager(suppress_init_messages=True)
        current_executable = str(Path(sys.executable).resolve())

        if config_manager.config.get("python_executable") == current_executable:
            return  # Context is already synchronized.

        # This print is helpful for the user to know a sync is happening.
        safe_print(
            _("🔄 Forcing omnipkg context to match script Python version: {}...").format(
                f"{sys.version_info.major}.{sys.version_info.minor}"
            )
        )

        new_paths = config_manager._get_paths_for_interpreter(current_executable)

        if not new_paths:
            raise RuntimeError(
                f"Could not determine paths for the current interpreter: {current_executable}"
            )

        config_manager.set("python_executable", new_paths["python_executable"])
        config_manager.set("site_packages_path", new_paths["site_packages_path"])
        config_manager.set("multiversion_base", new_paths["multiversion_base"])
        config_manager._update_default_python_links(
            config_manager.venv_path, Path(current_executable)
        )

        safe_print(_("✅ omnipkg context synchronized successfully."))

    except Exception as e:
        safe_print(_("❌ A critical error occurred during context synchronization: {}").format(e))
        import traceback

        traceback.print_exc()
        sys.exit(1)


def ensure_script_is_running_on_version(required_version: str):
    """
    A declarative guard placed at the start of a script. It ensures the script is
    running on a specific Python version. If not, it uses the omnipkg API to
    find the target interpreter and relaunches the script using os.execve.
    """
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _

    major, minor = map(int, required_version.split("."))
    if sys.version_info[:2] == (major, minor):
        return
    if os.environ.get("OMNIPKG_RELAUNCHED") == "1":
        safe_print(
            _("❌ FATAL ERROR: Relaunch attempted, but still not on Python {}. Aborting.").format(
                required_version
            )
        )
        sys.exit(1)
    safe_print("\n" + "=" * 80)
    safe_print(_("  🚀 AUTOMATIC CONTEXT RELAUNCH REQUIRED"))
    safe_print("=" * 80)
    safe_print(_("   - Script requires:   Python {}").format(required_version))
    safe_print(
        _("   - Currently running: Python {}.{}").format(
            sys.version_info.major, sys.version_info.minor
        )
    )
    safe_print(_("   - Relaunching into the correct context..."))
    try:
        from omnipkg.core import ConfigManager
        from omnipkg.core import omnipkg as OmnipkgCore

        cm = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager=cm)
        target_exe_path = (
            pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(
                required_version
            )
        )
        if not target_exe_path or not target_exe_path.exists():
            safe_print(_("   -> Target interpreter not yet managed. Attempting to adopt..."))
            if pkg_instance.adopt_interpreter(required_version) != 0:
                raise RuntimeError(
                    _("Failed to adopt required Python version {}").format(required_version)
                )
            target_exe_path = (
                pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(
                    required_version
                )
            )
            if not target_exe_path or not target_exe_path.exists():
                raise RuntimeError(
                    _("Could not find Python {} even after adoption.").format(required_version)
                )
        safe_print(_("   ✅ Target interpreter found at: {}").format(target_exe_path))
        new_env = os.environ.copy()
        new_env["OMNIPKG_RELAUNCHED"] = "1"
        os.execve(str(target_exe_path), [str(target_exe_path)] + sys.argv, new_env)
    except Exception as e:
        safe_print("\n" + "-" * 80)
        safe_print(_("   ❌ FATAL ERROR during context relaunch."))
        safe_print(_("   -> Error: {}").format(e))
        import traceback

        traceback.print_exc()
        safe_print("-" * 80)
        sys.exit(1)


def run_script_in_omnipkg_env(command_list, streaming_title):
    """
    A centralized utility to run a command in a fully configured omnipkg environment.
    It handles finding the correct python executable, setting environment variables,
    and providing true, line-by-line live streaming of the output.
    """
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _

    safe_print(_("🚀 {}").format(streaming_title))
    safe_print(_("📡 Live streaming output (this may take several minutes for heavy packages)..."))
    safe_print(_("💡 Don't worry if there are pauses - packages are downloading/installing!"))
    safe_print(_("🛑 Press Ctrl+C to safely cancel if needed"))
    safe_print("-" * 60)
    process = None
    try:
        cm = ConfigManager()
        project_root = Path(__file__).parent.parent.resolve()
        env = os.environ.copy()
        current_lang = cm.config.get("language", "en")
        env["OMNIPKG_LANG"] = current_lang
        env["LANG"] = f"{current_lang}.UTF-8"
        env["LANGUAGE"] = current_lang
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            command_list,
            text=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            safe_print(line, end="")
        returncode = process.wait()
        safe_print("-" * 60)
        if returncode == 0:
            safe_print(_("🎉 Command completed successfully!"))
        else:
            safe_print(_("❌ Command failed with return code {}").format(returncode))
        return returncode
    except KeyboardInterrupt:
        safe_print(_("\n⚠️  Command cancelled by user (Ctrl+C)"))
        if process:
            process.terminate()
        return 130
    except FileNotFoundError:
        safe_print(
            _('❌ Error: Command not found. Ensure "{}" is installed and in your PATH.').format(
                command_list[0]
            )
        )
        return 1
    except Exception as e:
        safe_print(_("❌ Command failed with an unexpected error: {}").format(e))
        traceback.print_exc()
        return 1


def print_header(title):
    """Prints a consistent, pretty header."""
    # Lazy import to avoid circular import
    from omnipkg.i18n import _

    safe_print("\n" + "=" * 60)
    safe_print(_("  🚀 {}").format(title))
    safe_print("=" * 60)


def ensure_python_or_relaunch(required_version: str):
    """
    Ensures the script is running on a specific Python version.
    If not, it finds the target interpreter and relaunches the script using os.execve,
    preserving arguments and environment context.
    """
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _

    major, minor = map(int, required_version.split("."))
    if sys.version_info[:2] == (major, minor):
        return
    safe_print("\n" + "=" * 80)
    safe_print(_("  🚀 AUTOMATIC DIMENSION JUMP REQUIRED"))
    safe_print("=" * 80)
    safe_print(
        _("   - Current Dimension: Python {}.{}").format(
            sys.version_info.major, sys.version_info.minor
        )
    )
    safe_print(_("   - Target Dimension:  Python {}").format(required_version))
    safe_print(_("   - Re-calibrating multiverse coordinates and relaunching..."))
    try:
        from .core import OmnipkgCore

        cm = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager=cm)
        target_exe_path = (
            pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(
                required_version
            )
        )
        if not target_exe_path or not target_exe_path.exists():
            safe_print(_("   -> Target dimension not yet managed. Attempting to adopt..."))
            if pkg_instance.adopt_interpreter(required_version) != 0:
                raise RuntimeError(
                    _("Failed to adopt required Python version {}").format(required_version)
                )
            target_exe_path = (
                pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(
                    required_version
                )
            )
            if not target_exe_path or not target_exe_path.exists():
                raise RuntimeError(
                    _("Could not find Python {} even after adoption.").format(required_version)
                )
        safe_print(_("   ✅ Target interpreter found at: {}").format(target_exe_path))
        new_env = os.environ.copy()
        os.execve(str(target_exe_path), [str(target_exe_path)] + sys.argv, new_env)
    except Exception as e:
        safe_print("\n" + "-" * 80)
        safe_print(_("   ❌ FATAL ERROR during dimension jump."))
        safe_print(_("   -> Error: {}").format(e))
        import traceback

        traceback.print_exc()
        safe_print("-" * 80)
        sys.exit(1)

def is_interactive_session():
    """
    Reusable version of the detection logic you already have in _first_time_setup.
    Returns False for: CI, Docker, piped input, explicitly non-interactive envs.
    """
    import sys
    
    # Check all the conditions you're already using
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    no_tty = not sys.stdin.isatty()
    forced_noninteractive = os.environ.get("OMNIPKG_NONINTERACTIVE")
    in_ci = os.environ.get("CI")
    web_mode = os.environ.get("OMNIPKG_WEB_MODE")
    
    # If ANY of these are true, it's non-interactive
    if in_ci or forced_noninteractive or no_tty or is_docker or web_mode:
        return False
    
    return True

def safe_input(prompt: str, default: str = "", auto_value: str = None):
    """
    Safe input wrapper that returns defaults in non-interactive environments.
    Uses your existing detection logic.
    
    Args:
        prompt: The prompt to show users
        default: Default value for non-interactive (if auto_value not specified)
        auto_value: Specific value to use in non-interactive mode (overrides default)
    """
    if not is_interactive_session():
        result = auto_value if auto_value is not None else default
        safe_print(f"🤖 Auto-selecting: {result}")
        return result
    
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    from omnipkg.i18n import _

    if command_list[0] == "omnipkg":
        command_list = [sys.executable, "-m", "omnipkg.cli"] + command_list[1:]
    process = subprocess.Popen(
        command_list,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    safe_print(_("💭 Simulating Enter key press..."))
    process.stdin.write(input_data + "\n")
    process.stdin.close()
    output_lines = []
    for line in iter(process.stdout.readline, ""):
        stripped_line = line.strip()
        safe_print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        error_message = _("Subprocess command '{}' failed with exit code {}.").format(
            " ".join(command_list), retcode
        )
        if output_lines:
            error_message += "\nSubprocess Output:\n" + "\n".join(output_lines)
        raise RuntimeError(error_message)
    return retcode

def simulate_user_choice(choice, message):
    """Simulate user input with a delay, for interactive demos."""
    from omnipkg.i18n import _

    safe_print(_("\nChoice (y/n): "), end="", flush=True)
    time.sleep(1)
    safe_print(choice)
    time.sleep(0.5)
    safe_print(_("💭 {}").format(message))
    return choice.lower()

class ConfigGuard:
    """
    A context manager to safely and temporarily override omnipkg's configuration
    for the duration of a test or a specific operation.
    """

    def __init__(self, config_manager, temporary_overrides: dict):
        self.config_manager = config_manager
        self.temporary_overrides = temporary_overrides
        self.original_config = None

    def __enter__(self):
        """Saves the original config and applies the temporary one."""
        from omnipkg.i18n import _

        self.original_config = self.config_manager.config.copy()
        temp_config = self.original_config.copy()
        temp_config.update(self.temporary_overrides)
        self.config_manager.config = temp_config
        self.config_manager.save_config()
        safe_print(_("🛡️ ConfigGuard: Activated temporary test configuration."))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Guarantees restoration of the original config."""
        # Lazy import to avoid circular import
        from omnipkg.i18n import _

        self.config_manager.config = self.original_config
        self.config_manager.save_config()
        safe_print(_("🛡️ ConfigGuard: Restored original user configuration."))
