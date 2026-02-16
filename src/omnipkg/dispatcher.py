"""
OMNIPKG DISPATCHER ARCHITECTURE
This module serves as the entry point for the 'omnipkg' and '8pkg' commands.

KEY PRINCIPLE: Each Python interpreter's 8pkg should be SELF-AWARE.
It knows which Python it belongs to by examining sys.executable.
No global config - each interpreter reads its own local .omnipkg_config.json
"""
import os
import sys
import json
from pathlib import Path
from omnipkg.i18n import _
from omnipkg.common_utils import safe_print  # ← Should be here
import platform
import subprocess

def main():
    """
    Omnipkg Unified Dispatcher.
    """
    # ============================================================================
    # WINDOWS CONSOLE FIX: Enable proper UTF-8 and ANSI handling FIRST
    # ============================================================================
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable ANSI escape sequences for stdout
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            
            # Force UTF-8 encoding
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
                sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
            if hasattr(sys.stdin, 'reconfigure'):
                sys.stdin.reconfigure(encoding='utf-8')
                
            os.environ['PYTHONIOENCODING'] = 'utf-8'
            os.environ['PYTHONUNBUFFERED'] = '1'
        except Exception:
            pass
    
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    
    # ═══════════════════════════════════════════════════════════
    # 🌍 STEP -1: PROPAGATE LANGUAGE BEFORE ANYTHING ELSE
    # ═══════════════════════════════════════════════════════════
    # Check if language is set in config and propagate to env var
    # This ensures subprocesses inherit the language setting
    if "OMNIPKG_LANG" not in os.environ:
        venv_root = find_absolute_venv_root()
        config_path = venv_root / ".omnipkg_config.json"
        
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                language = config.get("language")
                if language:
                    os.environ["OMNIPKG_LANG"] = language
                    if debug_mode:
                        print(f"[DEBUG-DISPATCH] Set OMNIPKG_LANG={language} from config", file=sys.stderr)
            except Exception as e:
                if debug_mode:
                    print(_('[DEBUG-DISPATCH] Config read error: {}').format(e), file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # 🎯 STEP 0: DETECT VERSION-SPECIFIC COMMAND (8pkg39, etc.)
    # ═══════════════════════════════════════════════════════════
    import re
    prog_name = Path(sys.argv[0]).name.lower()
    
    # Check if it's a version-specific command
    version_match = re.match(r"8pkg(\d)(\d+)", prog_name)
    
    if version_match:
        major = version_match.group(1)
        minor = version_match.group(2)
        forced_version = f"{major}.{minor}"
        
        # Inject --python flag if not already present
        if "--python" not in sys.argv:
            sys.argv.insert(1, "--python")
            sys.argv.insert(2, forced_version)
        
        if debug_mode:
            print(_('[DEBUG-DISPATCH] Detected version-specific command: {}').format(prog_name), file=sys.stderr)
            print(_('[DEBUG-DISPATCH] Injected --python {}').format(forced_version), file=sys.stderr)
            print(_('[DEBUG-DISPATCH] Modified argv: {}').format(sys.argv), file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # STEP 1: Identify how we were called
    # ═══════════════════════════════════════════════════════════
    
    # If called as 'python', 'python3', or 'pip' -> ACT AS SHIM
    if prog_name.startswith("python") or prog_name == "pip":
        if debug_mode:
            print(_("[DEBUG-SHIM] Intercepted call to '{}'").format(prog_name), file=sys.stderr)
        handle_shim_execution(prog_name, debug_mode)
        return
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    
    # 1. Identify how we were called
    prog_name = Path(sys.argv[0]).name.lower()
    
    # If called as 'python', 'python3', or 'pip' -> ACT AS SHIM
    if prog_name.startswith("python") or prog_name == "pip":
        if debug_mode:
            print(_("[DEBUG-SHIM] Intercepted call to '{}'").format(prog_name), file=sys.stderr)
        handle_shim_execution(prog_name, debug_mode)
        return
    
    # 2. Determine which Python interpreter to use
    target_python = determine_target_python()
    
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Using Python: {}').format(target_python), file=sys.stderr)
        print(_('[DEBUG-DISPATCH] Current executable: {}').format(sys.executable), file=sys.stderr)
    
    if not target_python.exists():
        safe_print(_('❌ Python interpreter not found: {}').format(target_python), file=sys.stderr)
        print(_('   Run: 8pkg python adopt {}').format(extract_version(target_python)), file=sys.stderr)
        sys.exit(1)
    
    exec_args = [str(target_python), "-m", "omnipkg.cli"] + sys.argv[1:]
    
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Executing: {}').format(' '.join(exec_args)), file=sys.stderr)
    
    if platform.system() == "Windows":
        # Windows: Use subprocess with proper flags to avoid handle inheritance issues and hangs
        creationflags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            exec_args,
            creationflags=creationflags,
            encoding="utf-8",
            errors="replace"
        )
        sys.exit(result.returncode)
    else:
        os.execv(str(target_python), exec_args)


def determine_target_python() -> Path:
    """
    FIXED PRIORITY ORDER:
    1. Self-awareness (config file)
    2. CLI flag --python (HIGHEST PRIORITY FOR USER INTENT)
    3. OMNIPKG_PYTHON env var (only if shims are actually active)
    4. Fallback to sys.executable
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    
    # Priority 0: Self-awareness
    script_path = Path(sys.argv[0]).resolve()
    script_dir = script_path.parent
    config_path = script_dir / ".omnipkg_config.json"
    
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            python_exe = config.get("python_executable")
            if python_exe:
                python_path = Path(python_exe)
                if python_path.exists():
                    if debug_mode:
                        safe_print(_('[DEBUG-DISPATCH] ✅ Self-aware: {}').format(python_path), file=sys.stderr)
                    return python_path
        except Exception as e:
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Config read error: {}').format(e), file=sys.stderr)
    
    # 🎯 Priority 1: CLI flag --python (EXPLICIT USER INTENT - HIGHEST!)
    if "--python" in sys.argv:
        try:
            idx = sys.argv.index("--python")
            if idx + 1 < len(sys.argv):
                version = sys.argv[idx + 1]
                # Don't remove from argv yet - let the CLI parser handle it
                resolved = resolve_python_path(version)
                if debug_mode:
                    safe_print(_('[DEBUG-DISPATCH] ✅ CLI flag: --python {} -> {}').format(version, resolved), file=sys.stderr)
                return resolved
        except (ValueError, IndexError):
            pass
    
    # Priority 2: OMNIPKG_PYTHON (only if shims are actually active)
    if "OMNIPKG_PYTHON" in os.environ:
        claimed_version = os.environ["OMNIPKG_PYTHON"]
        actual_version = test_active_python_version()
        
        if debug_mode:
            print(_('[DEBUG-DISPATCH] OMNIPKG_PYTHON claims: {}').format(claimed_version), file=sys.stderr)
            print(_('[DEBUG-DISPATCH] Actual python --version: {}').format(actual_version), file=sys.stderr)
        
        if actual_version and claimed_version in actual_version:
            if debug_mode:
                safe_print(_('[DEBUG-DISPATCH] ✅ Shims active, using swapped Python {}').format(claimed_version), file=sys.stderr)
            return resolve_python_path(claimed_version)
        else:
            if debug_mode:
                safe_print(_('[DEBUG-DISPATCH] ⚠️ Shims inactive - OMNIPKG_PYTHON is leaked, ignoring'), file=sys.stderr)
    
    # Fallback
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Fallback to sys.executable: {}').format(sys.executable), file=sys.stderr)
    return Path(sys.executable)

def test_active_python_version() -> str:
    """
    Test what `python --version` actually returns right now.
    Returns the version string or None if can't determine.
    """
    import subprocess
    
    try:
        # Run `python --version` and capture output
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            creationflags=creationflags,
        )
        
        # Parse version from "Python 3.10.18" -> "3.10"
        output = result.stdout or result.stderr
        if "Python" in output:
            version = output.strip().split()[1]  # "Python 3.10.18" -> "3.10.18"
            major_minor = ".".join(version.split(".")[:2])  # "3.10.18" -> "3.10"
            return major_minor
    except Exception:
        pass
    
    return None
    
def handle_shim_execution(prog_name: str, debug: bool):
    target_version = os.environ.get("OMNIPKG_PYTHON")
    venv_root = os.environ.get("OMNIPKG_VENV_ROOT")
    conda_prefix = os.environ.get("CONDA_PREFIX")

    if debug:
        print(_("[DEBUG-SHIM] Intercepted call to '{}'").format(prog_name), file=sys.stderr)
        print(_('[DEBUG-SHIM] OMNIPKG_PYTHON={}').format(target_version), file=sys.stderr)
        print(_('[DEBUG-SHIM] CONDA_PREFIX={}').format(conda_prefix), file=sys.stderr)
        print(_('[DEBUG-SHIM] OMNIPKG_VENV_ROOT={}').format(venv_root), file=sys.stderr)

    # ─────────────────────────────────────────────
    # 1) Validate swap context against conda
    # ─────────────────────────────────────────────
    if target_version and venv_root:
        if conda_prefix:
            # Still inside *some* conda env: only honor swap if it matches our venv_root
            venv_path = Path(venv_root).resolve()
            conda_path = Path(conda_prefix).resolve()
            if venv_path != conda_path:
                if debug:
                    print(_('[DEBUG-SHIM] Conda env mismatch - ignoring leaked OMNIPKG_PYTHON'),
                          file=sys.stderr)
                target_version = None
        else:
            # No conda env active at all – treat OMNIPKG_PYTHON as leaked
            if debug:
                print(_('[DEBUG-SHIM] No conda env - ignoring leaked OMNIPKG_PYTHON'),
                      file=sys.stderr)
            target_version = None

    # ─────────────────────────────────────────────
    # 2) If no valid swap, pass-through to real tool
    # ─────────────────────────────────────────────
    if not target_version:
        if debug:
            print(f"[DEBUG-SHIM] No active swap, searching for real {prog_name}", file=sys.stderr)

        path_var = os.environ.get("PATH", "")
        for path_dir in path_var.split(os.pathsep):
            # Avoid infinite recursion on our own shims
            if ".omnipkg/shims" in path_dir:
                continue
            candidate = Path(path_dir) / prog_name
            if candidate.exists() and os.access(candidate, os.X_OK):
                if debug:
                    print(_('[DEBUG-SHIM] Found: {}').format(candidate), file=sys.stderr)
                os.execv(str(candidate), [str(candidate)] + sys.argv[1:])

        # Nothing found → behave like a real shell command-not-found
        if debug:
            print(_('[DEBUG-SHIM] No {} found in PATH').format(prog_name), file=sys.stderr)

        print(_("Command '{}' not found, did you mean:").format(prog_name), file=sys.stderr)
        print("  command 'python3' from deb python3", file=sys.stderr)
        print("  command 'python' from deb python-is-python3", file=sys.stderr)
        sys.exit(127)

    # ─────────────────────────────────────────────
    # 3) Valid swap: run via omnipkg daemon
    # ─────────────────────────────────────────────
    from omnipkg.isolation.worker_daemon import DaemonClient
    # ─────────────────────────────────────────────
    # 3) Valid swap: execute the swapped Python
    # ─────────────────────────────────────────────
    from omnipkg.dispatcher import resolve_python_path

    target_python = resolve_python_path(target_version)

    if debug:
        print(_('[DEBUG-SHIM] Executing: {} {}').format(target_python, ' '.join(sys.argv[1:])), file=sys.stderr)

    # Direct execution - no daemon needed for simple commands
    if prog_name.startswith("python"):
        # Execute the target Python directly
        os.execv(str(target_python), [str(target_python)] + sys.argv[1:])
    elif prog_name == "pip":
        # Execute pip via the target Python
        os.execv(str(target_python), [str(target_python), "-m", "pip"] + sys.argv[1:])
    else:
        # Fallback: shouldn't happen, but handle it
        sys.exit(1)

def resolve_python_path(version: str) -> Path:
    """
    Resolve a Python version string to an actual interpreter path.
    
    NEW BEHAVIOR: First checks if the CURRENT executable's config knows about
    this version, then falls back to registry lookup.
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    
    # If it's already a path, use it
    if "/" in version or "\\" in version:
        return Path(version)
    
    # Extract major.minor from version string
    version_parts = version.split(".")
    major_minor = f"{version_parts[0]}.{version_parts[1]}" if len(version_parts) >= 2 else version
    
    # ═══════════════════════════════════════════════════════════
    # STEP 1: Find the venv root (where the registry lives)
    # ═══════════════════════════════════════════════════════════
    venv_root = find_absolute_venv_root()
    
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Absolute Venv Root: {}').format(venv_root), file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # STEP 2: Check the master registry
    # ═══════════════════════════════════════════════════════════
    registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    
    if registry_path.exists():
        if debug_mode:
            print(f"[DEBUG-DISPATCH] Reading registry: {registry_path}", file=sys.stderr)
        
        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
            
            interpreters = data.get("interpreters", {})
            
            # Try exact match first, then major.minor
            for key in [version, major_minor]:
                if key in interpreters:
                    path = Path(interpreters[key])
                    if path.exists():
                        if debug_mode:
                            print(_('[DEBUG-DISPATCH] Registry hit ({}): {}').format(key, path), file=sys.stderr)
                        return path
        
        except Exception as e:
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Registry read error: {}').format(e), file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # STEP 3: FALLBACK - Check Native Venv Binaries
    # ═══════════════════════════════════════════════════════════
    bin_dir = venv_root / ("Scripts" if os.name == "nt" else "bin")
    
    if os.name == "nt":
        candidates = [bin_dir / "python.exe"]
    else:
        candidates = [
            bin_dir / f"python{major_minor}",
            bin_dir / "python3",
            bin_dir / "python",
        ]
    
    for candidate in candidates:
        if candidate.exists():
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Found native: {}').format(candidate), file=sys.stderr)
            return candidate
    
    # ═══════════════════════════════════════════════════════════
    # STEP 4: LAST RESORT - Check System PATH
    # ═══════════════════════════════════════════════════════════
    import shutil
    path_exe = shutil.which(f"python{version}") or shutil.which(f"python{major_minor}")
    
    if path_exe:
        if debug_mode:
            print(_('[DEBUG-DISPATCH] Found in PATH: {}').format(path_exe), file=sys.stderr)
        return Path(path_exe)
    
    # Not found
    return Path(f"/path/to/python{major_minor}/NOT_FOUND")


def find_absolute_venv_root(ignore_env_override: bool = False) -> Path:
    """
    Find the ABSOLUTE TOP-LEVEL virtual environment root.
    Uses the SAME logic as ConfigManager._get_venv_root() to ensure consistency.
    
    Args:
        ignore_env_override: If True, ignores OMNIPKG_VENV_ROOT env var.
                           Used by shims to determine their true identity.
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    
    if not ignore_env_override:
        override = os.environ.get("OMNIPKG_VENV_ROOT")
        if override:
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Using OMNIPKG_VENV_ROOT override: {}').format(override), file=sys.stderr)
            return Path(override)
    
    # CRITICAL: When running as a shim/dispatcher, sys.executable is the python interpreter
    # running this script. But we want to find the venv relative to THIS SCRIPT.
    # If we are frozen or running as a script, sys.argv[0] is more reliable for location.
    current_executable = Path(sys.argv[0]).resolve()
    
    # Fallback to sys.executable if argv[0] seems weird (e.g. -c)
    if not current_executable.exists():
        current_executable = Path(sys.executable).resolve()

    # --- CRITICAL FIX: Detect if we're in a managed interpreter ---
    # If we're running from .omnipkg/interpreters/*, we need to find the REAL venv root
    # by going up past ALL .omnipkg directories (handles nested cases!)
    executable_str = str(current_executable)

    # AGGRESSIVE: Handle ANY level of nesting by finding the FIRST .omnipkg in the path
    if ".omnipkg" in executable_str:
        # Normalize path separators
        normalized_path = executable_str.replace("\\", "/")

        # Find the FIRST occurrence of .omnipkg (going from left/root)
        omnipkg_parts = normalized_path.split("/.omnipkg/")

        if len(omnipkg_parts) >= 2:
            # Everything BEFORE the first .omnipkg is the original venv
            original_venv = Path(omnipkg_parts[0])

            # Verify this is actually a venv by checking for pyvenv.cfg
            if (original_venv / "pyvenv.cfg").exists():
                if debug_mode:
                    print(_('[DEBUG-DISPATCH] Found venv via .omnipkg split: {}').format(original_venv), file=sys.stderr)
                return original_venv

            # If no pyvenv.cfg at that level, search upward from there
            search_dir = original_venv
            while search_dir != search_dir.parent:
                if (search_dir / "pyvenv.cfg").exists():
                    if debug_mode:
                        print(_('[DEBUG-DISPATCH] Found venv via upward search: {}').format(search_dir), file=sys.stderr)
                    return search_dir
                search_dir = search_dir.parent

            # Last resort: if we can't find pyvenv.cfg, just use the directory
            # before .omnipkg as it's definitely the venv root
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Using pre-.omnipkg path: {}').format(original_venv), file=sys.stderr)
            return original_venv

    # --- Standard upward search for non-managed interpreters ---
    # Search upwards from the current executable for pyvenv.cfg
    search_dir = current_executable.parent
    while search_dir != search_dir.parent:  # Stop at the filesystem root
        if (search_dir / "pyvenv.cfg").exists():
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Found venv via standard search: {}').format(search_dir), file=sys.stderr)
            return search_dir
        search_dir = search_dir.parent

    # Only use sys.prefix as a last resort if all else fails.
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Using sys.prefix fallback: {}').format(sys.prefix), file=sys.stderr)
    return Path(sys.prefix)

def find_venv_root() -> Path:
    """Find the virtual environment root (legacy function for compatibility)."""
    return find_absolute_venv_root()

def extract_version(python_path: Path) -> str:
    """Extract version string from Python path for error messages."""
    import re
    match = re.search(r"python(\d+\.\d+)", str(python_path))
    return match.group(1) if match else "unknown"

if __name__ == "__main__":
    main()
