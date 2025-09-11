import sys
import subprocess
import json
import re
import os
import tempfile
import traceback
from pathlib import Path
import time
from omnipkg.i18n import _
from omnipkg.core import ConfigManager
from typing import Optional

def run_command(command_list, check=True):
    """
    Helper to run a command and stream its output.
    Raises RuntimeError on non-zero exit code, with captured output.
    """
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        print(stripped_line)
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
    
    FAILURE_PATTERNS = [
        r"No solution found when resolving dependencies",
        r"ResolutionImpossible",
        r"Could not find a version that satisfies",
    ]
    
    # [FIXED] A much more robust pattern that finds the first explicit
    # package==version pin in the error message, which is the most likely culprit.
    CONFLICT_PATTERN = r"([a-zA-Z0-9_-]+==[0-9.]+[a-zA-Z0-9_.-]*)"

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
        # This regex now looks for any 'package==version' string
        matches = re.findall(self.CONFLICT_PATTERN, stderr_output)
        
        # Often, the user's direct requirement is mentioned first.
        if matches:
            # Let's find one that isn't part of a sub-dependency clause if possible
            for line in stderr_output.splitlines():
                if "your project requires" in line:
                    sub_matches = re.findall(self.CONFLICT_PATTERN, line)
                    if sub_matches:
                        return sub_matches[0].strip().strip("'\"")
            # Fallback to the first match found anywhere
            return matches[0].strip().strip("'\"")
            
        return None

def sync_context_to_runtime():
    """
    Ensures omnipkg's active context matches the currently running Python interpreter
    by using the omnipkg API directly. This is the robust method for post-relaunch
    synchronization, avoiding the state conflicts of CLI subprocesses.
    """
    print(_('🔄 Forcing omnipkg context to match script Python version: {}...').format(
        f'{sys.version_info.major}.{sys.version_info.minor}'
    ))
    try:
        from omnipkg.core import ConfigManager
        
        # Suppress messages here to avoid the "new environment" prompt if it occurs
        config_manager = ConfigManager(suppress_init_messages=True)
        
        current_executable = str(Path(sys.executable).resolve())

        # Optimization: If the config is already correct, do nothing.
        if config_manager.config.get('python_executable') == current_executable:
            print(_('✅ Context is already synchronized.'))
            return

        # Use the ConfigManager's internal method to get the correct paths for the CURRENT interpreter.
        new_paths = config_manager._get_paths_for_interpreter(current_executable)
        if not new_paths:
            raise RuntimeError(f"Could not determine paths for the current interpreter: {current_executable}")

        # Directly update and save the configuration. This is what 'swap' does internally.
        print(_('   - Aligning configuration to the new runtime...'))
        config_manager.set('python_executable', new_paths['python_executable'])
        config_manager.set('site_packages_path', new_paths['site_packages_path'])
        config_manager.set('multiversion_base', new_paths['multiversion_base'])

        # Also update the default python symlinks to reflect the change.
        config_manager._update_default_python_links(config_manager.venv_path, Path(current_executable))

        print(_('✅ omnipkg context synchronized successfully via API.'))
        return

    except Exception as e:
        print(_('❌ A critical error occurred during context synchronization: {}').format(e))
        import traceback
        traceback.print_exc()
        # Exit because a failed sync is a fatal error for the script's logic.
        sys.exit(1)

def run_script_in_omnipkg_env(command_list, streaming_title):
    """
    A centralized utility to run a command in a fully configured omnipkg environment.
    It handles finding the correct python executable, setting environment variables,
    and providing true, line-by-line live streaming of the output.
    """
    print(f"🚀 {streaming_title}")
    print(_('📡 Live streaming output (this may take several minutes for heavy packages)...'))
    print(_("💡 Don't worry if there are pauses - packages are downloading/installing!"))
    print(_('🛑 Press Ctrl+C to safely cancel if needed'))
    print('-' * 60)
    
    process = None
    try:
        cm = ConfigManager()
        project_root = Path(__file__).parent.parent.resolve()

        # Set up the environment for the subprocess
        env = os.environ.copy()
        current_lang = cm.config.get('language', 'en')
        env['OMNIPKG_LANG'] = current_lang
        env['LANG'] = f'{current_lang}.UTF-8'
        env['LANGUAGE'] = current_lang
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONPATH'] = str(project_root) + os.pathsep + env.get('PYTHONPATH', '')
        
        # Start the subprocess
        process = subprocess.Popen(
            command_list,
            text=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding='utf-8',
            errors='replace'
        )
        
        # Stream the output line by line
        for line in process.stdout:
            print(line, end='')
            
        returncode = process.wait()
        print('-' * 60)
        
        if returncode == 0:
            print(_('🎉 Command completed successfully!'))
        else:
            print(_('❌ Command failed with return code {}').format(returncode))
        return returncode

    except KeyboardInterrupt:
        print(_('\n⚠️  Command cancelled by user (Ctrl+C)'))
        if process:
            process.terminate()
        return 130
    except FileNotFoundError:
        print(_('❌ Error: Command not found. Ensure "{}" is installed and in your PATH.').format(command_list[0]))
        return 1
    except Exception as e:
        print(_('❌ Command failed with an unexpected error: {}').format(e))
        traceback.print_exc()
        return 1

def print_header(title):
    """Prints a consistent, pretty header."""
    print('\n' + '=' * 60)
    print(_('  🚀 {}').format(title))
    print('=' * 60)

def ensure_python_or_relaunch(required_version: str):
    """
    Ensures the script is running on a specific Python version.
    If not, it finds the target interpreter and relaunches the script using os.execve,
    preserving arguments and environment context.
    """
    major, minor = map(int, required_version.split('.'))
    if sys.version_info[:2] == (major, minor):
        return # Correct version, do nothing

    print('\n' + '=' * 80)
    print(_('  🚀 AUTOMATIC DIMENSION JUMP REQUIRED'))
    print('=' * 80)
    print(_('   - Current Dimension: Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
    print(_('   - Target Dimension:  Python {}').format(required_version))
    print(_('   - Re-calibrating multiverse coordinates and relaunching...'))

    try:
        # --- THIS IS THE FIX ---
        # We import the lowercase 'omnipkg' class and give it a PascalCase alias
        # to match the intent of the original code.
        from .core import omnipkg as OmnipkgCore
        # --- END OF FIX ---
        
        cm = ConfigManager(suppress_init_messages=True)
        pkg_instance = OmnipkgCore(config_manager=cm)

        target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)

        if not target_exe_path or not target_exe_path.exists():
            print(_('   -> Target dimension not yet managed. Attempting to adopt...'))
            if pkg_instance.adopt_interpreter(required_version) != 0:
                 raise RuntimeError(f"Failed to adopt required Python version {required_version}")
            target_exe_path = pkg_instance.interpreter_manager.config_manager.get_interpreter_for_version(required_version)
            if not target_exe_path or not target_exe_path.exists():
                raise RuntimeError(f"Could not find Python {required_version} even after adoption.")

        print(_('   ✅ Target interpreter found at: {}').format(target_exe_path))

        new_env = os.environ.copy()
        
        # This replaces the current process. It does not return.
        os.execve(str(target_exe_path), [str(target_exe_path)] + sys.argv, new_env)

    except Exception as e:
        print('\n' + '-' * 80)
        print('   ❌ FATAL ERROR during dimension jump.')
        print(f'   -> Error: {e}')
        import traceback
        traceback.print_exc()
        print('-' * 80)
        sys.exit(1)

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    if command_list[0] == 'omnipkg':
        command_list = [sys.executable, '-m', 'omnipkg.cli'] + command_list[1:]
    process = subprocess.Popen(command_list, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
    print(_('💭 Simulating Enter key press...'))
    process.stdin.write(input_data + '\n')
    process.stdin.close()
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        error_message = _("Subprocess command '{}' failed with exit code {}.").format(' '.join(command_list), retcode)
        if output_lines:
            error_message += '\nSubprocess Output:\n' + '\n'.join(output_lines)
        raise RuntimeError(error_message)
    return retcode

def print_header(title):
    """Prints a consistent, pretty header."""
    print('\n' + '=' * 60)
    print(_('  🚀 {}').format(title))
    print('=' * 60)

def simulate_user_choice(choice, message):
    """Simulate user input with a delay, for interactive demos."""
    print(_('\nChoice (y/n): '), end='', flush=True)
    time.sleep(1)
    print(choice)
    time.sleep(0.5)
    print(_('💭 {}').format(message))
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
        # 1. Save a copy of the user's original configuration
        self.original_config = self.config_manager.config.copy()
        
        # 2. Create the new temporary configuration
        temp_config = self.original_config.copy()
        temp_config.update(self.temporary_overrides)
        
        # 3. Apply and save the temporary config so subprocesses can see it
        self.config_manager.config = temp_config
        self.config_manager.save_config()
        print(_("🛡️ ConfigGuard: Activated temporary test configuration."))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Guarantees restoration of the original config."""
        # This code will run ALWAYS, even if the code inside the 'with' block crashes.
        self.config_manager.config = self.original_config
        self.config_manager.save_config()
        print(_("🛡️ ConfigGuard: Restored original user configuration."))
