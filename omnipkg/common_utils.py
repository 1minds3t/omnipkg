# omnipkg/common_utils.py
import sys
import subprocess
import json
from pathlib import Path
import time # For simulating user input delay

def run_command(command_list, check=True):
    """
    Helper to run a command and stream its output.
    Raises RuntimeError on non-zero exit code, with captured output.
    """
    # Ensure we call the omnipkg CLI entrypoint correctly for omnipkg commands
    if command_list[0] == "omnipkg":
        command_list = [sys.executable, "-m", "omnipkg.cli"] + command_list[1:]

    process = subprocess.Popen(
        command_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Merge stderr into stdout
        text=True,
        bufsize=1, # Line-buffered output
        universal_newlines=True
    )
    
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        print(stripped_line) # Print to console in real-time
        output_lines.append(stripped_line) # Store for potential error message

    process.stdout.close()
    retcode = process.wait()

    if retcode != 0:
        error_message = f"Subprocess command '{' '.join(command_list)}' failed with exit code {retcode}."
        if output_lines:
            error_message += "\nSubprocess Output:\n" + "\n".join(output_lines)
        raise RuntimeError(error_message) # Always raise a standard RuntimeError for failures

    return retcode

def run_interactive_command(command_list, input_data, check=True):
    """Helper to run a command that requires stdin input."""
    # Ensure we call the omnipkg CLI entrypoint correctly for omnipkg commands
    if command_list[0] == "omnipkg":
        command_list = [sys.executable, "-m", "omnipkg.cli"] + command_list[1:]
        
    process = subprocess.Popen(
        command_list, 
        stdin=subprocess.PIPE, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True, 
        bufsize=1, 
        universal_newlines=True
    )
    
    # Write the simulated input and close stdin
    print("💭 Simulating Enter key press...")
    process.stdin.write(input_data + "\n")
    process.stdin.close()

    output_lines = []
    for line in iter(process.stdout.readline, ''):
        stripped_line = line.strip()
        print(stripped_line)
        output_lines.append(stripped_line)
    process.stdout.close()
    retcode = process.wait()
    if check and retcode != 0:
        error_message = f"Subprocess command '{' '.join(command_list)}' failed with exit code {retcode}."
        if output_lines:
            error_message += "\nSubprocess Output:\n" + "\n".join(output_lines)
        raise RuntimeError(error_message)
    return retcode

def print_header(title):
    """Prints a consistent, pretty header."""
    print("\n" + "="*60)
    print(f"  🚀 {title}")
    print("="*60)

def simulate_user_choice(choice, message):
    """Simulate user input with a delay, for interactive demos."""
    print(f"\nChoice (y/n): ", end="", flush=True)
    time.sleep(1)
    print(choice)
    time.sleep(0.5)
    print(f"💭 {message}")
    return choice.lower()