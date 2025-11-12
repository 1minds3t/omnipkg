from __future__ import annotations  # Python 3.6+ compatibility
import sys
import subprocess
import json
import re
import os
import tempfile
import traceback
from pathlib import Path
import time
from typing import Optional

# Keep a reference to the original, built-in print function
_builtin_print = print

def safe_print(*args, **kwargs):
    """
    A robust print function that handles UnicodeEncodeError gracefully.
    It attempts to print normally, but falls back to a safe, error-replacing
    UTF-8 encoding if the default console encoding fails.
    This version is self-contained and does not depend on i18n to avoid circular imports.
    """
    try:
        # Try to use the normal, built-in print
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            # If it fails, create a safe, sanitized version of the arguments
            safe_args = []
            for arg in args:
                if isinstance(arg, str):
                    # Encode to bytes, replacing errors, then decode back to a clean string
                    encoding = sys.stdout.encoding or 'utf-8'
                    safe_args.append(arg.encode(encoding, 'replace').decode(encoding))
                else:
                    safe_args.append(arg)
            
            # Use the built-in print with the sanitized arguments
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            # Final, ultra-safe fallback. Hardcoded English string.
            _builtin_print("[omnipkg: A message could not be displayed due to an encoding error.]")

def run_command(command_list, check=True):
    """
    Helper to run a command and stream its output.
    Raises RuntimeError on non-zero exit code, with captured output.
    """
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        safe_print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if retcode != 0:
        error_message = _("Subprocess command '{}' failed with exit code {}.").format(' '.join(command_list), retcode)
        if output_lines:
            error_message += '\nSubprocess Output:\n' + '\n'.join(output_lines)
        raise RuntimeError(error_message)
    return retcode

class UVFailureDetector:
    """Detects UV dependency resolution failures."""
    
    FAILURE_PATTERNS = ['No solution found when resolving dependencies', 'ResolutionImpossible', 'Could not find a version that satisfies']
    CONFLICT_PATTERN = '([a-zA-Z0-9_-]+==[0-9.]+[a-zA-Z0-9_.-]*)'

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
                if 'your project requires' in line:
                    sub_matches = re.findall(self.CONFLICT_PATTERN, line)
                    if sub_matches:
                        return sub_matches[0].strip().strip('\'"')
            return matches[0].strip().strip('\'"')
        return None

def debug_python_context(label=""):
    """Print comprehensive Python context information for debugging."""
    print(f"\n{'='*70}")
    safe_print(f"🔍 DEBUG CONTEXT CHECK: {label}")
    print(f"{'='*70}")
    safe_print(f"📍 sys.executable:        {sys.executable}")
    safe_print(f"📍 sys.version:           {sys.version}")
    safe_print(f"📍 sys.version_info:      {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    safe_print(f"📍 os.getpid():           {os.getpid()}")
    safe_print(f"📍 __file__ (if exists):  {__file__ if '__file__' in globals() else 'N/A'}")
    safe_print(f"📍 Path.cwd():            {Path.cwd()}")
    
    # Environment variables that might affect context
    relevant_env_vars = [
        'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
        'OMNIPKG_MAIN_ORCHESTRATOR_PID', 'OMNIPKG_RELAUNCHED',
        'OMNIPKG_LANG', 'PYTHONHOME', 'PYTHONEXECUTABLE'
    ]
    safe_print(f"\n📦 Relevant Environment Variables:")
    for var in relevant_env_vars:
        value = os.environ.get(var, 'NOT SET')
        print(f"   {var}: {value}")
    
    # Check sys.path for omnipkg locations
    safe_print(f"\n📂 sys.path (first 5 entries):")
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
        
        if config_manager.config.get('python_executable') == current_executable:
            return # Context is already synchronized.
        
        # This print is helpful for the user to know a sync is happening.
        safe_print(_('🔄 Forcing omnipkg context to match script Python version: {}...').format(f'{sys.version_info.major}.{sys.version_info.minor}'))

        new_paths = config_manager._get_paths_for_interpreter(current_executable)
        
        if not new_paths:
            raise RuntimeError(f'Could not determine paths for the current interpreter: {current_executable}')
        
        config_manager.set('python_executable', new_paths['python_executable'])
        config_manager.set('site_packages_path', new_paths['site_packages_path'])
        config_manager.set('multiversion_base', new_paths['multiversion_base'])
        config_manager._update_default_python_links(config_manager.venv_path, Path(current_executable))
        
        safe_print(_('✅ omnipkg context synchronized successfully.'))
        
    except Exception as e:
        safe_print(_('❌ A critical error occurred during context synchronization: {}').format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)

def ensure_script_is_running_on_version(required_version: str):
    """
    A declarative guard placed at the start of a script. It ensures the script is
    running on a specific Python version. If not, it uses the omnipkg API to
    find the target interpreter and relaunches the script using os.execve.
    """
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    major, minor = map(int, required_version.split('.'))
    if sys.version_info[:2] == (major, minor):
        return
    if os.environ.get('OMNIPKG_RELAUNCHED') == '1':
        safe_print(_('❌ FATAL ERROR: Relaunch attempted, but still not on Python {}. Aborting.').format(required_version))
        sys.exit(1)
    safe_print('\n' + '=' * 80)
    safe_print(_('  🚀 AUTOMATIC CONTEXT RELAUNCH REQUIRED'))
    safe_print('=' * 80)
    safe_print(_('   - Script requires:   Python {}').format(required_version))
    safe_print(_('   - Currently running: Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
    safe_print(_('   - Relaunching into the correct context...'))
    try:
        from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
        cm = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager=cm)
        target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)
        if not target_exe_path or not target_exe_path.exists():
            safe_print(_('   -> Target interpreter not yet managed. Attempting to adopt...'))
            if pkg_instance.adopt_interpreter(required_version) != 0:
                raise RuntimeError(_('Failed to adopt required Python version {}').format(required_version))
            target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)
            if not target_exe_path or not target_exe_path.exists():
                raise RuntimeError(_('Could not find Python {} even after adoption.').format(required_version))
        safe_print(_('   ✅ Target interpreter found at: {}').format(target_exe_path))
        new_env = os.environ.copy()
        new_env['OMNIPKG_RELAUNCHED'] = '1'
        os.execve(str(target_exe_path), [str(target_exe_path)] + sys.argv, new_env)
    except Exception as e:
        safe_print('\n' + '-' * 80)
        safe_print(_('   ❌ FATAL ERROR during context relaunch.'))
        safe_print(_('   -> Error: {}').format(e))
        import traceback
        traceback.print_exc()
        safe_print('-' * 80)
        sys.exit(1)

def run_script_in_omnipkg_env(command_list, streaming_title):
    """
    A centralized utility to run a command in a fully configured omnipkg environment.
    It handles finding the correct python executable, setting environment variables,
    and providing true, line-by-line live streaming of the output.
    """
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    safe_print(_('🚀 {}').format(streaming_title))
    safe_print(_('📡 Live streaming output (this may take several minutes for heavy packages)...'))
    safe_print(_("💡 Don't worry if there are pauses - packages are downloading/installing!"))
    safe_print(_('🛑 Press Ctrl+C to safely cancel if needed'))
    safe_print('-' * 60)
    process = None
    try:
        cm = ConfigManager()
        project_root = Path(__file__).parent.parent.resolve()
        env = os.environ.copy()
        current_lang = cm.config.get('language', 'en')
        env['OMNIPKG_LANG'] = current_lang
        env['LANG'] = f'{current_lang}.UTF-8'
        env['LANGUAGE'] = current_lang
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONPATH'] = str(project_root) + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen(command_list, text=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        for line in process.stdout:
            safe_print(line, end='')
        returncode = process.wait()
        safe_print('-' * 60)
        if returncode == 0:
            safe_print(_('🎉 Command completed successfully!'))
        else:
            safe_print(_('❌ Command failed with return code {}').format(returncode))
        return returncode
    except KeyboardInterrupt:
        safe_print(_('\n⚠️  Command cancelled by user (Ctrl+C)'))
        if process:
            process.terminate()
        return 130
    except FileNotFoundError:
        safe_print(_('❌ Error: Command not found. Ensure "{}" is installed and in your PATH.').format(command_list[0]))
        return 1
    except Exception as e:
        safe_print(_('❌ Command failed with an unexpected error: {}').format(e))
        traceback.print_exc()
        return 1

def print_header(title):
    """Prints a consistent, pretty header."""
    # Lazy import to avoid circular import
    from omnipkg.i18n import _
    
    safe_print('\n' + '=' * 60)
    safe_print(_('  🚀 {}').format(title))
    safe_print('=' * 60)

def ensure_python_or_relaunch(required_version: str):
    """
    Ensures the script is running on a specific Python version.
    If not, it finds the target interpreter and relaunches the script using os.execve,
    preserving arguments and environment context.
    """
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    major, minor = map(int, required_version.split('.'))
    if sys.version_info[:2] == (major, minor):
        return
    safe_print('\n' + '=' * 80)
    safe_print(_('  🚀 AUTOMATIC DIMENSION JUMP REQUIRED'))
    safe_print('=' * 80)
    safe_print(_('   - Current Dimension: Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
    safe_print(_('   - Target Dimension:  Python {}').format(required_version))
    safe_print(_('   - Re-calibrating multiverse coordinates and relaunching...'))
    try:
        from .core import OmnipkgCore
        cm = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager=cm)
        target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)
        if not target_exe_path or not target_exe_path.exists():
            safe_print(_('   -> Target dimension not yet managed. Attempting to adopt...'))
            if pkg_instance.adopt_interpreter(required_version) != 0:
                raise RuntimeError(_('Failed to adopt required Python version {}').format(required_version))
            target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)
            if not target_exe_path or not target_exe_path.exists():
                raise RuntimeError(_('Could not find Python {} even after adoption.').format(required_version))
        safe_print(_('   ✅ Target interpreter found at: {}').format(target_exe_path))
        new_env = os.environ.copy()
        os.execve(str(target_exe_path), [str(target_exe_path)] + sys.argv, new_env)
    except Exception as e:
        safe_print('\n' + '-' * 80)
        safe_print(_('   ❌ FATAL ERROR during dimension jump.'))
        safe_print(_('   -> Error: {}').format(e))
        import traceback
        traceback.print_exc()
        safe_print('-' * 80)
        sys.exit(1)

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    safe_print(_('💭 Simulating Enter key press...'))
    process.stdin.write(input_data + '\n')
    process.stdin.close()
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        safe_print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        error_message = _("Subprocess command '{}' failed with exit code {}.").format(' '.join(command_list), retcode)
        if output_lines:
            error_message += '\nSubprocess Output:\n' + '\n'.join(output_lines)
        raise RuntimeError(error_message)
    return retcode

def simulate_user_choice(choice, message):
    """Simulate user input with a delay, for interactive demos."""
    # Lazy import to avoid circular import
    from omnipkg.core import ConfigManager
    from omnipkg.i18n import _
    
    safe_print(_('\nChoice (y/n): '), end='', flush=True)
    time.sleep(1)
    safe_print(choice)
    time.sleep(0.5)
    safe_print(_('💭 {}').format(message))
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
        # Lazy import to avoid circular import
        from omnipkg.core import ConfigManager
        from omnipkg.i18n import _
        
        self.original_config = self.config_manager.config.copy()
        temp_config = self.original_config.copy()
        temp_config.update(self.temporary_overrides)
        self.config_manager.config = temp_config
        self.config_manager.save_config()
        safe_print(_('🛡️ ConfigGuard: Activated temporary test configuration.'))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Guarantees restoration of the original config."""
        # Lazy import to avoid circular import
        from omnipkg.i18n import _
        
        self.config_manager.config = self.original_config
        self.config_manager.save_config()
        safe_print(_('🛡️ ConfigGuard: Restored original user configuration.'))