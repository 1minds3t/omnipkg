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
    Ensures omnipkg's active context matches the currently running Python interpreter.

    This is a critical pre-flight check to prevent state mismatches between the
    runtime environment and the omnipkg configuration. It uses the CLI for robust
    switching logic.

    Returns:
        bool: True if the context is synchronized, False otherwise.
    """
    current_python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
    
    try:
        config_manager = ConfigManager()
        active_config_version = config_manager.config.get('active_python_version')

        # If the config already matches the runtime, we don't need to do anything.
        if active_config_version == current_python_version:
            # This is a minor optimization to avoid a subprocess call if not needed.
            return True

        # If a change is needed, use the robust CLI which contains all the necessary logic.
        print(_('🔄 Forcing omnipkg context to match script Python version: {}...').format(current_python_version))
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        
        # The 'swap' command is the source of truth for this operation.
        result = subprocess.run(
            omnipkg_cmd_base + ['swap', 'python', current_python_version],
            capture_output=True, text=True, check=True
        )
        
        print(_('✅ omnipkg context synchronized to Python {}').format(current_python_version))
        return True
    
    except subprocess.CalledProcessError as e:
        # This block catches errors if the 'swap' command fails.
        print(_('⚠️  Could not synchronize omnipkg context via CLI: {}').format(e))
        print(_('   CLI output: {}').format(e.stdout))
        print(_('   CLI error: {}').format(e.stderr))
        return False
        
    except Exception as e:
        # This is a general catch-all for other unexpected errors.
        print(_('⚠️  Unexpected error synchronizing omnipkg context: {}').format(e))
        import traceback
        traceback.print_exc()
        return False

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
    A generic utility to ensure the script is running on a specific Python version.

    If the current interpreter does not match, it uses omnipkg to switch
    to the required version and then re-launches the original script in a new
    process. The original process is then terminated.

    Args:
        required_version (str): The required version string (e.g., "3.11").
    """
    major, minor = map(int, required_version.split('.'))
    if sys.version_info[:2] == (major, minor):
        return # Correct version, do nothing

    # If we get here, we need to switch and relaunch.
    print('\n' + '=' * 80)
    print(_('  🚀 AUTOMATIC ENVIRONMENT CORRECTION'))
    print('=' * 80)
    print(_('   This script requires Python {}').format(required_version))
    print(_('   Currently running on: Python {}.{}').format(sys.version_info.major, sys.version_info.minor))
    print(_('   Attempting to automatically switch using omnipkg...'))
    
    try:
        omnipkg_cmd_base = [sys.executable, '-m', 'omnipkg.cli']
        
        # Step 1: Adopt the required version
        subprocess.run(omnipkg_cmd_base + ['python', 'adopt', required_version], check=True, capture_output=True)
        
        # Step 2: Swap to the required version
        subprocess.run(omnipkg_cmd_base + ['swap', 'python', required_version], check=True, capture_output=True)
        
        # Step 3: Find the new executable path
        info_result = subprocess.run(omnipkg_cmd_base + ['info', 'python'], check=True, capture_output=True, text=True)
        new_python_exe = None
        for line in info_result.stdout.splitlines():
            if '⭐ (currently active)' in line:
                match = re.search(r':\s*(/\S+)', line)
                if match:
                    new_python_exe = match.group(1).strip()
                    break
        
        if not new_python_exe:
            raise RuntimeError(_("Could not find the new Python {} executable after swapping.").format(required_version))

        print(_('   ✅ Found Python {} at: {}').format(required_version, new_python_exe))
        print(_('\n   STEP 4: Relaunching script with the correct interpreter...'))
        
        # Relaunch the original script
        original_script = os.path.abspath(sys.argv[0])
        script_args = sys.argv[1:]
        
        # Use os.execv to replace the current process. This is cleaner than a wrapper.
        new_env = os.environ.copy()
        new_env['OMNIPKG_RELAUNCHED'] = '1'
        os.execve(new_python_exe, [new_python_exe, original_script] + script_args, new_env)

    except Exception as e:
        print('\n' + '-' * 80)
        print('   ❌ An error occurred while trying to switch Python versions.')
        # ... (your existing error handling) ...
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
