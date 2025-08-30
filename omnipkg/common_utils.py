import sys
import subprocess
import json
from pathlib import Path
import time
from omnipkg.i18n import _

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

# FILE: /home/minds3t/omnipkg/omnipkg/common_utils.py

# ... (keep your existing imports and run_command function)

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