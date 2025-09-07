# omnipkg/commands/run.py

import sys
import os
import subprocess
import tempfile
import json
import re
import textwrap
import time
from pathlib import Path

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.i18n import _
from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
from omnipkg.common_utils import sync_context_to_runtime

# Global variable to store initial run timing for comparison
_initial_run_time_ns = None

def analyze_runtime_failure_and_heal(stderr: str, cmd_args: list, config_manager: ConfigManager):
    """
    Analyzes stderr for a broader range of version conflict errors and triggers healing.
    This function now correctly returns a tuple (exit_code, stats) in all cases.
    """
    conflict_patterns = [
        (r"AssertionError: Incorrect ([\w\-]+) version! Expected ([\d\.]+)", 1, 2, "Runtime version assertion"),
        (r"requires ([\w\-]+)==([\d\.]+), but you have", 1, 2, "Import-time dependency conflict"),
        (r"VersionConflict:.*?Requirement\.parse\('([\w\-]+)==([\d\.]+)'\)", 1, 2, "Setuptools VersionConflict")
    ]
    
    for regex, pkg_group, ver_group, description in conflict_patterns:
        match = re.search(regex, stderr)
        if match:
            pkg_name = match.group(pkg_group).lower()
            expected_version = match.group(ver_group)
            failed_spec = f"{pkg_name}=={expected_version}"
            print(f"\n🔍 {description} failed. Auto-healing with omnipkg bubbles...")
            print(_("   - Conflict identified for: {}").format(failed_spec))
            original_script_path = Path(cmd_args[0]).resolve()
            original_script_args = cmd_args[1:]
            return heal_with_bubble(failed_spec, original_script_path, original_script_args, config_manager)
            
    print(_("❌ Script failed with an unhandled runtime error that could not be auto-healed."))
    print(stderr, file=sys.stderr)
    # --- FIX: Always return a tuple ---
    return 1, None

def heal_with_bubble(required_spec, original_script_path, original_script_args, config_manager):
    """
    Ensures the required bubble exists, auto-installs if missing, then re-runs the script inside it.
    This function now correctly returns a tuple (exit_code, stats) in all cases.
    """
    try:
        pkg_name, pkg_version = required_spec.split('==')
    except ValueError:
        print(_("❌ Healing requires a specific version format (e.g., 'package==1.2.3')."))
        # --- FIX: Always return a tuple ---
        return 1, None

    bubble_dir_name = f'{pkg_name.lower().replace("-", "_")}-{pkg_version}'
    bubble_path = Path(config_manager.config['multiversion_base']) / bubble_dir_name

    if not bubble_path.is_dir():
        print(_("💡 Missing bubble detected: {}").format(required_spec))
        print(_("🚀 Auto-installing bubble... (This may take a moment)"))
        omnipkg_instance = OmnipkgCore(config_manager)
        return_code = omnipkg_instance.smart_install([required_spec])
        if return_code != 0:
            print(_("\n❌ Auto-install failed for {}.").format(required_spec))
            # --- FIX: Always return a tuple ---
            return 1, None
        print(_("\n✅ Bubble installed successfully: {}").format(required_spec))

    print(_("✅ Using bubble: {}").format(bubble_path.name))
    return run_with_healing_wrapper(required_spec, original_script_path, original_script_args, config_manager)
    
def execute_run_command(cmd_args: list, config_manager: ConfigManager):
    """
    Handles the 'omnipkg run' command by ALWAYS using uv to run the script directly,
    timing the initial attempt, and catching runtime failures for auto-healing.
    """
    global _initial_run_time_ns
    
    if not cmd_args:
        print(_('❌ Error: No script specified to run.'))
        return 1

    print(_(" syncing omnipkg context...")); sync_context_to_runtime(); print(_("✅ Context synchronized."))
    
    python_exe = config_manager.config.get('python_executable', sys.executable)
    
    print(_("🚀 Attempting to run script with uv, forcing use of current environment..."))
    initial_cmd = ['uv', 'run', '--no-project', '--python', python_exe, '--'] + cmd_args
    
    start_time_ns = time.perf_counter_ns()
    process = subprocess.Popen(initial_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=Path.cwd())
    
    # FIXED: Stream output live instead of collecting it all first
    output_lines = []
    for line in iter(process.stdout.readline, ''):
        print(line, end='')  # Print each line immediately as it comes
        output_lines.append(line)  # Still collect for error analysis
    
    return_code = process.wait()
    end_time_ns = time.perf_counter_ns()
    
    full_output = "".join(output_lines)
    
    _initial_run_time_ns = end_time_ns - start_time_ns

    if return_code == 0:
        print("\n" + "="*60)
        print("✅ Script executed successfully in the main environment.")
        print("⏱️  Total runtime: {:.3f} ms ({:,} ns)".format(_initial_run_time_ns / 1_000_000, _initial_run_time_ns))
        print("="*60)
        return 0

    print("⏱️  UV run failed in: {:.3f} ms ({:,} ns)".format(_initial_run_time_ns / 1_000_000, _initial_run_time_ns))
    
    exit_code, heal_stats = analyze_runtime_failure_and_heal(full_output, cmd_args, config_manager)
    
    if heal_stats:
        _print_performance_comparison(_initial_run_time_ns, heal_stats)

    return exit_code

def run_with_healing_wrapper(required_spec, original_script_path, original_script_args, config_manager):
    """
    Generates and executes the temporary wrapper script that uses omnipkgLoader.
    This version captures performance stats and returns them for comparison.
    """
    global _initial_run_time_ns
    
    # This wrapper script will now print a special JSON line at the very end
    # containing the performance stats for the parent process to read.
    wrapper_content = textwrap.dedent(f"""\
        import sys, os, runpy, json
        from pathlib import Path
        sys.path.insert(0, r"{project_root}")
        from omnipkg.loader import omnipkgLoader
        from omnipkg.i18n import _
        
        lang_from_env = os.environ.get('OMNIPKG_LANG')
        if lang_from_env: _.set_language(lang_from_env)
        
        config = json.loads(r'''{json.dumps(config_manager.config)}''')
        package_spec = "{required_spec}"
        
        loader_instance = None
        try:
            print(f"\\n🌀 omnipkg auto-heal: Wrapping script with loader for '{{package_spec}}'...")
            print('-' * 60)
            with omnipkgLoader(package_spec, config=config) as loader:
                loader_instance = loader
                sys.argv = [{str(original_script_path)!r}] + {original_script_args!r}
                runpy.run_path({str(original_script_path)!r}, run_name="__main__")
            print('-' * 60)
            print(_("✅ Script completed successfully inside omnipkg bubble."))
        except Exception:
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            if loader_instance:
                stats = loader_instance.get_performance_stats()
                if stats:
                    # Print a machine-readable line for the parent process to capture
                    print(f"OMNIPKG_STATS_JSON:{{json.dumps(stats)}}", flush=True)
    """)
    
    temp_script_path = None
    heal_stats = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(wrapper_content)
            temp_script_path = f.name
        
        heal_command = [config_manager.config.get('python_executable', sys.executable), temp_script_path]
        
        print(_("\n🚀 Re-running with omnipkg auto-heal..."))
        
        process = subprocess.Popen(heal_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
        
        output_lines = []
        for line in iter(process.stdout.readline, ''):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                print(line, end='') # Print normal output to user in real-time
            output_lines.append(line)
        
        return_code = process.wait()

        # After the process finishes, parse all captured output for the stats line
        full_output = "".join(output_lines)
        for line in full_output.splitlines():
            if line.startswith("OMNIPKG_STATS_JSON:"):
                try:
                    stats_json = line.split(":", 1)[1]
                    heal_stats = json.loads(stats_json)
                    break
                except (IndexError, json.JSONDecodeError):
                    continue

        # --- FIX: Return the captured stats along with the exit code ---
        return return_code, heal_stats

    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def _print_performance_comparison(initial_ns, heal_stats):
    """Prints the final performance summary."""
    if not initial_ns or not heal_stats:
        return
        
    uv_time_ms = initial_ns / 1_000_000
    omnipkg_time_ms = heal_stats['total_swap_time_ns'] / 1_000_000
    
    if omnipkg_time_ms <= 0:
        return

    speed_ratio = uv_time_ms / omnipkg_time_ms
    speed_percentage = ((uv_time_ms - omnipkg_time_ms) / omnipkg_time_ms) * 100

    print("\n" + "="*70)
    print("🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG")
    print("="*70)
    print(f"UV Failed Run:     {uv_time_ms:>8.3f} ms  ({initial_ns:>12,}) ns)")
    print(f"omnipkg Healing:   {omnipkg_time_ms:>8.3f} ms  ({heal_stats['total_swap_time_ns']:>12,}) ns)")
    print("-" * 70)
    
    if speed_ratio >= 1000:
        print(f"🎯 omnipkg is {speed_ratio:>6.0f}x FASTER than UV!")
    elif speed_ratio >= 100:
        print(f"🎯 omnipkg is {speed_ratio:>6.1f}x FASTER than UV!")
    else:
        print(f"🎯 omnipkg is {speed_ratio:>6.2f}x FASTER than UV!")
    
    if speed_percentage >= 10000:
        print(f"💥 That's {speed_percentage:>8.0f}% improvement!")
    elif speed_percentage >= 1000:
        print(f"💥 That's {speed_percentage:>8.1f}% improvement!")
    else:
        print(f"💥 That's {speed_percentage:>8.2f}% improvement!")
    
    print("="*70)
    print("🌟 Same environment, zero downtime, microsecond swapping!")
    print("="*70 + "\n")