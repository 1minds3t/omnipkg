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
        # Windows: Use subprocess instead of execv to avoid handle inheritance issues
        sys.exit(subprocess.call(exec_args))
    else:
        os.execv(str(target_python), exec_args)


def determine_target_python() -> Path:
    """
    PRIORITY ORDER:
    1. Self-awareness: config file next to the 8pkg script itself
    2. CLI flag --python (explicit user intent)
    3. OMNIPKG_PYTHON env var (only if shims are verified active via PATH check)
    4. Fallback to sys.executable
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"

    # ─────────────────────────────────────────────────────────────
    # Priority 0: Self-awareness — config next to THIS script/exe
    # ─────────────────────────────────────────────────────────────
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

    # ─────────────────────────────────────────────────────────────
    # Priority 1: CLI flag --python  (explicit user intent)
    # ─────────────────────────────────────────────────────────────
    if "--python" in sys.argv:
        try:
            idx = sys.argv.index("--python")
            if idx + 1 < len(sys.argv):
                version = sys.argv[idx + 1]
                resolved = resolve_python_path(version)
                if debug_mode:
                    safe_print(_('[DEBUG-DISPATCH] ✅ CLI flag: --python {} -> {}').format(version, resolved), file=sys.stderr)
                return resolved
        except (ValueError, IndexError):
            pass

    # ─────────────────────────────────────────────────────────────
    # Priority 2: OMNIPKG_PYTHON — but ONLY when shims are live.
    #
    # The old approach called `subprocess.run(["python", "--version"])`
    # to "verify" shims.  On Windows this spawns a new process that
    # resolves "python" through the OS PATH *before* our shim dir has
    # taken effect in the child, so it always sees the base conda Python
    # and wrongly concludes the shims are "leaked".
    #
    # The correct check is purely in-process: are our shims at the
    # FRONT of the PATH that THIS process inherited?  If yes, the
    # shell the user is sitting in already has the shims active and
    # OMNIPKG_PYTHON is trustworthy.
    # ─────────────────────────────────────────────────────────────
    if "OMNIPKG_PYTHON" in os.environ:
        claimed_version = os.environ["OMNIPKG_PYTHON"]

        if debug_mode:
            print(_('[DEBUG-DISPATCH] OMNIPKG_PYTHON claims: {}').format(claimed_version), file=sys.stderr)

        if _shims_are_active_in_path(debug_mode):
            if debug_mode:
                safe_print(_('[DEBUG-DISPATCH] ✅ Shims confirmed in PATH, trusting OMNIPKG_PYTHON {}').format(claimed_version), file=sys.stderr)
            return resolve_python_path(claimed_version)
        else:
            if debug_mode:
                safe_print(_('[DEBUG-DISPATCH] ⚠️ Shims not at front of PATH - OMNIPKG_PYTHON is leaked, ignoring'), file=sys.stderr)

    # ─────────────────────────────────────────────────────────────
    # Fallback: whatever Python is running this script
    # ─────────────────────────────────────────────────────────────
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Fallback to sys.executable: {}').format(sys.executable), file=sys.stderr)
    return Path(sys.executable)


def _shims_are_active_in_path(debug_mode: bool = False) -> bool:
    """
    Return True when the omnipkg shims directory is genuinely prepended to the
    PATH that this process inherited — meaning we are inside a `swap` shell.

    Strategy (works identically on Windows and Unix):
      1. Look for OMNIPKG_VENV_ROOT to know where the shims directory lives.
      2. Check that the shims dir appears *before* any real Python executable
         in the current process PATH entries.

    We deliberately do NOT spawn a subprocess here.  Spawning `python --version`
    resolves the name through a fresh CreateProcess / execvp call that may not
    see our shim .bat files first (Windows) or may pick up the wrong PATH
    ordering — which is exactly the false-negative that broke the old check.
    """
    venv_root_str = os.environ.get("OMNIPKG_VENV_ROOT")
    if not venv_root_str:
        # Without OMNIPKG_VENV_ROOT we cannot know where shims live.
        # The env var is always set by `swap`, so its absence means
        # we are NOT in a swap context.
        if debug_mode:
            print("[DEBUG-DISPATCH] No OMNIPKG_VENV_ROOT → shims not active", file=sys.stderr)
        return False

    venv_root = Path(venv_root_str).resolve()
    # Shims dir is always <venv_root>/.omnipkg/shims  (set by swap code)
    shims_dir = venv_root / ".omnipkg" / "shims"
    shims_dir_str = str(shims_dir).replace("\\", "/").lower()

    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep)

    for entry in path_parts:
        normalized = entry.replace("\\", "/").lower().rstrip("/")
        if normalized == shims_dir_str.rstrip("/"):
            if debug_mode:
                print(_('[DEBUG-DISPATCH] Shims dir found in PATH at position {}').format(
                    path_parts.index(entry)), file=sys.stderr)
            return True

    if debug_mode:
        print("[DEBUG-DISPATCH] Shims dir not found in PATH entries", file=sys.stderr)
    return False


def test_active_python_version() -> str:
    """
    DEPRECATED — no longer used by determine_target_python().
    Kept only so external callers don't break.
    Returns the major.minor of whatever `python` resolves to in PATH.
    """
    try:
        result = subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        output = result.stdout or result.stderr
        if "Python" in output:
            version = output.strip().split()[1]
            return ".".join(version.split(".")[:2])
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
                        # AUTO-CREATE config for this interpreter if missing.
                        # Without this, managed interpreters (cpython-3.11.9 etc)
                        # have no .omnipkg_config.json and fall back to the global
                        # config, running as the wrong Python version.
                        _ensure_interpreter_config(path, key, venv_root, debug_mode)
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



def _ensure_interpreter_config(interpreter_path: Path, version: str, venv_root: Path, debug_mode: bool):
    """
    Creates .omnipkg_config.json next to the interpreter if it doesn't exist.
    Called from the dispatcher at the earliest moment we know a path is valid —
    right after a registry hit — so the config is always present before cli.py loads.
    
    This is intentionally lightweight: no imports from core.py, no subprocesses.
    We just write the essential keys so _load_or_create_config has something to read.
    """
    import re as _re
    config_path = interpreter_path.parent / ".omnipkg_config.json"
    if config_path.exists():
        return  # Already exists, nothing to do

    if debug_mode:
        print(f"[DEBUG-DISPATCH] Creating missing config for {version} at {config_path}", file=sys.stderr)

    try:
        # Derive site-packages path for this interpreter without running a subprocess.
        # Standard layout: .../cpython-3.11.9/Lib/site-packages (Windows)
        #                   .../cpython-3.11.9/lib/python3.11/site-packages (Unix)
        exe_dir = interpreter_path.parent  # e.g. .../cpython-3.11.9/ or .../cpython-3.11.9/bin/
        
        # Walk up to find the interpreter root (parent of bin/ or Scripts/)
        interp_root = exe_dir
        if exe_dir.name.lower() in ("bin", "scripts"):
            interp_root = exe_dir.parent

        # Try Windows layout first, then Unix
        major_minor = ".".join(version.split(".")[:2])
        candidates = [
            interp_root / "Lib" / "site-packages",
            interp_root / "lib" / f"python{major_minor}" / "site-packages",
            interp_root / "lib" / "site-packages",
        ]
        site_packages = next((str(p) for p in candidates if p.exists()), None)

        if not site_packages:
            # Fall back to the venv's own site-packages
            site_packages = str(venv_root / "Lib" / "site-packages")

        config_data = {
            "python_executable": str(interpreter_path.resolve()),
            "python_version": version,
            "python_version_short": major_minor,
            "site_packages_path": site_packages,
            "multiversion_base": str(Path(site_packages) / ".omnipkg_versions"),
            "install_strategy": "stable-main",
            "redis_enabled": True,
            "redis_host": "localhost",
            "redis_port": 6379,
            "enable_python_hotswap": True,
            "venv_root": str(venv_root.resolve()),
            "managed_by_omnipkg": True,
            "_auto_generated_by": "dispatcher",
        }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        if debug_mode:
            print(f"[DEBUG-DISPATCH] ✅ Config written to {config_path}", file=sys.stderr)

    except Exception as e:
        if debug_mode:
            print(f"[DEBUG-DISPATCH] ⚠️  Could not write config for {version}: {e}", file=sys.stderr)
        # Non-fatal — _load_or_create_config will handle it via fallback


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
