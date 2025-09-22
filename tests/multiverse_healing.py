try:
    from omnipkg.common_utils import safe_print
except ImportError:
    import sys
    _builtin_print = print
    def safe_print(*args, **kwargs):
        try: _builtin_print(*args, **kwargs)
        except: pass

import sys
import os
import subprocess
import json
import re
from pathlib import Path
import time
import traceback

# --- BOOTSTRAP: Ensure we are running as the Python 3.11 orchestrator ---
if 'OMNIPKG_ORCHESTRATOR_ACTIVE' not in os.environ:
    os.environ['OMNIPKG_ORCHESTRATOR_ACTIVE'] = '1'
    if sys.version_info[:2] != (3, 11):
        safe_print("--- OMNIPKG MULTIVERSE DEMO: BOOTSTRAP ---")
        safe_print(f"Orchestrator requires Python 3.11, but is running on {sys.version_info.major}.{sys.version_info.minor}.")
        safe_print("Attempting to relaunch with the correct interpreter...")
        try:
            project_root_bootstrap = Path(__file__).resolve().parent.parent
            if str(project_root_bootstrap) not in sys.path:
                sys.path.insert(0, str(project_root_bootstrap))
            from omnipkg.core import ConfigManager
            cm = ConfigManager(suppress_init_messages=True)
            target_exe = cm.get_interpreter_for_version('3.11')
            if not target_exe or not target_exe.exists():
                raise RuntimeError("Managed Python 3.11 not found. Please `omnipkg python adopt 3.11` first.")
            os.execve(str(target_exe), [str(target_exe)] + sys.argv, os.environ)
        except Exception as e:
            safe_print(f"FATAL BOOTSTRAP ERROR: Could not relaunch into Python 3.11. Error: {e}")
            sys.exit(1)
# --- END BOOTSTRAP ---

from omnipkg.i18n import _
from omnipkg.common_utils import sync_context_to_runtime

def print_header(title):
    safe_print('\n' + '=' * 80)
    safe_print(f'🚀 {title}')
    safe_print('=' * 80)

def debug_critical_sys_path(context_name=""):
    """Prints a concise, high-value summary of the import environment."""
    safe_print("\n" + "-"*25 + f" CRITICAL ENV CHECK ({context_name}) " + "-"*25)
    safe_print(f"Python Version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    safe_print(f"Python Executable: {sys.executable}")
    safe_print(f"PYTHONPATH env var: {os.environ.get('PYTHONPATH', '<NOT SET>')}")
    
    # Show TOP 5 sys.path entries
    safe_print("TOP 5 sys.path entries:")
    for i, path in enumerate(sys.path[:5], 1):
        safe_print(f"{i}. {path}")
    safe_print("")

def create_sterile_environment():
    """Creates a perfectly clean environment for a subprocess."""
    sterile_env = {}
    essential_vars = ['PATH', 'HOME', 'USER', 'SHELL', 'TERM', 'LANG', 'CONDA_DEFAULT_ENV', 'CONDA_PREFIX']
    for var in essential_vars:
        if var in os.environ:
            sterile_env[var] = os.environ.get(var)
    sterile_env['OMNIPKG_DISABLE_AUTO_ALIGN'] = '1'
    sterile_env['OMNIPKG_SUBPROCESS_MODE'] = '1'
    # Explicitly clear PYTHONPATH to ensure sterile environment
    if 'PYTHONPATH' in sterile_env:
        del sterile_env['PYTHONPATH']
    return sterile_env

def run_step(command, description, check=True):
    """
    FIXED: Runs a command as a step, but intelligently uses the currently configured
    omnipkg python interpreter to ensure the correct execution context.
    """
    step_start = time.perf_counter()
    safe_print(f'\n▶️  STEP: {description}')
    
    # --- START OF THE FIX ---
    # 1. Determine which Python interpreter SHOULD be running this command.
    from omnipkg.core import ConfigManager
    # We create a temporary, silent ConfigManager to peek at the state on disk.
    cm = ConfigManager(suppress_init_messages=True)
    configured_exe_path = cm.config.get('python_executable', sys.executable)
    
    # 2. If the command is 'omnipkg', we build the command to be executed
    #    by the correct interpreter. This is the key to context switching.
    if command[0] == 'omnipkg':
        # Re-write the command to be: /path/to/correct/python -m omnipkg.cli ...
        final_command = [configured_exe_path, '-m', 'omnipkg.cli'] + command[1:]
        safe_print(f"└── CONTEXT-AWARE EXEC: Running via {Path(configured_exe_path).name}")
    else:
        final_command = command
    # --- END OF THE FIX ---
    
    safe_print(f"└── COMMAND: {' '.join(final_command)}")
    
    env = os.environ.copy()
    env['OMNIPKG_DISABLE_AUTO_ALIGN'] = '1'
    env['OMNIPKG_SUBPROCESS_MODE'] = '1'
    
    try:
        process = subprocess.Popen(final_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', bufsize=1, universal_newlines=True, env=env)
        
        output_lines = []
        for line in iter(process.stdout.readline, ''):
            stripped_line = line.strip()
            if stripped_line:
                safe_print(f"| {stripped_line}")
            output_lines.append(line)
        return_code = process.wait()
        full_output = ''.join(output_lines)
        
        step_end = time.perf_counter()
        step_time = step_end - step_start
        
        if return_code == 0:
            safe_print(f"└── ✅ SUCCESS (⏱️ {step_time:.2f}s)")
            return full_output
        else:
            safe_print(f"└── ❌ FAILED (Exit Code: {return_code}, ⏱️ {step_time:.2f}s)")
            if check: raise RuntimeError(f"Step '{description}' failed.")
            return full_output
            
    except Exception as e:
        step_end = time.perf_counter()
        step_time = step_end - step_start
        safe_print(f"└── ❌ FAILED TO EXECUTE: {e} (⏱️ {step_time:.2f}s)")
        raise

def get_interpreter_path(version: str) -> str:
    """Asks omnipkg for the location of a specific Python interpreter."""
    output = run_step(['omnipkg', 'info', 'python'], f'Querying for Python {version} interpreter')
    for line in output.splitlines():
        if f'Python {version}' in line and ('currently active' in line or '⭐' in line):
            match = re.search(r':\s*(/\S+)', line)
            if match:
                path = match.group(1).strip()
                safe_print(f"└── Found at: {path}")
                return path
    raise RuntimeError(f'Could not find managed Python {version}.')

def multiverse_analysis():
    """The main orchestrator for the cross-dimensional test."""
    original_context = '3.11'
    try:
        print_header("OMNIPKG MULTIVERSE ANALYSIS TEST")
        safe_print(f'Orchestrator is running in the primary dimension: Python {original_context}')
        
        # Debug initial environment
        debug_critical_sys_path("ORCHESTRATOR START")
        
        # --- MISSION 1: THE LEGACY DIMENSION ---
        run_step(['omnipkg', 'swap', 'python', '3.9'], 'Entering Legacy Dimension (Python 3.9)')
        python_3_9_exe = get_interpreter_path('3.9')
        run_step(['omnipkg', 'install', 'numpy<2', 'scipy'], 'Installing legacy tools')
        
        safe_print("\n▶️  STEP: Executing payload in sterile Python 3.9 environment...")
        payload_start = time.perf_counter()
        sterile_env = create_sterile_environment()
        safe_print("└── 🧼 Sterile environment created (PYTHONPATH cleared)")
        
        # Show what the 3.9 interpreter will see BEFORE execution
        safe_print("└── 🔍 Checking Python 3.9 sys.path before payload execution...")
        debug_result = subprocess.run(
            [python_3_9_exe, '-I', '-c', 'import sys; print("\\n--- PYTHON 3.9 SYS.PATH DEBUG ---"); print(f"Python: {sys.version_info}"); print(f"Executable: {sys.executable}"); import os; print(f"PYTHONPATH: {os.environ.get(\\"PYTHONPATH\\", \\"<NOT SET>\\")}"); print("TOP 5 sys.path:"); [print(f"  {i+1}. {p}") for i, p in enumerate(sys.path[:5])]'],
            capture_output=True, text=True, env=sterile_env
        )
        if debug_result.returncode == 0:
            for line in debug_result.stdout.strip().split('\n'):
                safe_print(f"| {line}")
        
        result_3_9 = subprocess.run(
            [python_3_9_exe, '-I', __file__, '--run-legacy'],
            capture_output=True, text=True, check=False, env=sterile_env
        )
        
        payload_end = time.perf_counter()
        payload_time = payload_end - payload_start
        
        if result_3_9.returncode != 0:
            safe_print(f"└── ❌ FAILED (⏱️ {payload_time:.2f}s)")
            safe_print("--- LEGACY PAYLOAD STDOUT ---")
            safe_print(result_3_9.stdout)
            safe_print("--- LEGACY PAYLOAD STDERR ---")
            safe_print(result_3_9.stderr)
            raise RuntimeError('Legacy payload execution failed in sterile environment.')
        
        safe_print(f"└── ✅ SUCCESS (⏱️ {payload_time:.2f}s)")
        
        # Parse JSON from the LAST line that looks like JSON
        legacy_data = None
        for line in reversed(result_3_9.stdout.splitlines()):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    legacy_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        
        if legacy_data is None:
            safe_print("--- FULL LEGACY PAYLOAD OUTPUT ---")
            safe_print(result_3_9.stdout)
            raise RuntimeError('Could not find valid JSON output from legacy payload.')
        
        safe_print(f"└── Artifact retrieved from legacy dimension: {legacy_data}")
        
        # --- MISSION 2: THE MODERN DIMENSION ---
        run_step(['omnipkg', 'swap', 'python', '3.11'], 'Returning to Modern Dimension (Python 3.11)')
        run_step(['omnipkg', 'install', 'tensorflow'], 'Installing modern tools')
        
        modern_output = run_step(
            ['omnipkg', 'run', __file__, '--run-modern', json.dumps(legacy_data)],
            'Executing modern payload with auto-healing run command'
        )
        
        json_output = None
        for line in reversed(modern_output.splitlines()):
            stripped_line = line.strip()
            if stripped_line.startswith('{') and stripped_line.endswith('}'):
                try:
                    json_output = json.loads(stripped_line)
                    break
                except json.JSONDecodeError: continue
        if json_output is None:
            safe_print("--- FULL MODERN PAYLOAD OUTPUT ---")
            safe_print(modern_output)
            raise RuntimeError("Could not find valid JSON output from the modern payload.")
        
        safe_print(f"└── Artifact processed by modern dimension: {json_output}")
        return json_output.get('prediction') == 'SUCCESS'
        
    finally:
        safe_print("\n🌀 SAFETY PROTOCOL: Returning to original dimension...")
        run_step(['omnipkg', 'swap', 'python', original_context], 'Restoring original context', check=False)

# --- PAYLOADS: These run only when the script is called as a subprocess ---

def run_legacy_payload():
    """Legacy payload - runs in Python 3.9 with numpy/scipy"""
    debug_critical_sys_path("LEGACY PAYLOAD")
    
    try:
        # --- FIX: Import JSON first to guarantee its availability for error handling ---
        import json
        import numpy
        import scipy.signal
        # --- END FIX ---
        data = numpy.array([1, 2, 3, 4, 5])
        analysis_result = {'result': int(scipy.signal.convolve(data, data).sum())}
        
        # Print the JSON on the LAST line for easy parsing
        print(json.dumps(analysis_result))
        
    except Exception as e:
        # If there's an error, still output valid JSON
        error_result = {'result': 0, 'error': str(e)}
        print(json.dumps(error_result))

def run_modern_payload(legacy_data_json: str):
    """Modern payload - runs in Python 3.11 with tensorflow"""
    debug_critical_sys_path("MODERN PAYLOAD")
    
    try:
        # --- FIX: Import JSON first to guarantee its availability for error handling ---
        import json
        import tensorflow as tf
        # --- END FIX ---
        input_data = json.loads(legacy_data_json)
        legacy_value = input_data['result']
        prediction = 'SUCCESS' if legacy_value > 200 else 'FAILURE'
        
        # Print the JSON on the LAST line for easy parsing
        print(json.dumps({'prediction': prediction}))
        
    except Exception as e:
        # If there's an error, still output valid JSON
        error_result = {'prediction': 'ERROR', 'error': str(e)}
        print(json.dumps(error_result))

# --- SCRIPT ENTRY POINT ---

if __name__ == '__main__':
    if '--run-legacy' in sys.argv:
        run_legacy_payload()
    elif '--run-modern' in sys.argv:
        run_modern_payload(sys.argv[sys.argv.index('--run-modern') + 1])
    else:
        # This is the main orchestrator run
        start_time = time.perf_counter()
        success = False
        try:
            # Sync context before starting the main orchestration
            sync_context_to_runtime()
            success = multiverse_analysis()
        except Exception as e:
            safe_print(f'\n🔥🔥🔥 A CRITICAL ERROR OCCURRED DURING THE ANALYSIS 🔥🔥🔥')
            traceback.print_exc()
        
        end_time = time.perf_counter()
        
        print_header("TEST SUMMARY")
        if success:
            safe_print('🎉🎉🎉 MULTIVERSE ANALYSIS COMPLETE! All systems nominal. 🎉🎉🎉')
        else:
            safe_print('🔥🔥🔥 MULTIVERSE ANALYSIS FAILED! Check the output above for issues. 🔥🔥🔥')
        
        safe_print(f'\n⚡ Total Test Runtime: {end_time - start_time:.2f} seconds')
        
        sys.exit(0 if success else 1)