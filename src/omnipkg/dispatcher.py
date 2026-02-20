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
    #
    # SPECIAL CASE: The 'swap' command itself must ALWAYS run via the host/native
    # Python, never via a currently-swapped interpreter.  If we are inside a swap
    # shell and the user runs '8pkg swap python X', routing it through the swapped
    # interpreter (e.g. 3.7) will fail if that interpreter is missing deps needed
    # by the swap command (e.g. importlib.metadata).  The swap machinery lives in
    # the host env and should always be invoked from there.
    argv_commands = [a for a in sys.argv[1:] if not a.startswith("-")]
    is_swap_command = len(argv_commands) >= 1 and argv_commands[0] == "swap"
    if is_swap_command and os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1":
        # Force host Python by temporarily clearing the swap env vars for
        # determine_target_python(), then restore them so the spawned shell
        # inherits the correct environment.
        _saved = {k: os.environ.pop(k, None)
                  for k in ("OMNIPKG_PYTHON", "OMNIPKG_ACTIVE_PYTHON", "_OMNIPKG_SWAP_ACTIVE")}
        target_python = determine_target_python()
        for k, v in _saved.items():
            if v is not None:
                os.environ[k] = v
        if debug_mode:
            print("[DEBUG-DISPATCH] swap command inside swap shell — forcing host Python", file=sys.stderr)
    else:
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
       (SKIPPED when inside a swap shell — self-awareness resolves to the
        host interpreter's config and returns the wrong Python version)
    2. CLI flag --python (explicit user intent)
    3. OMNIPKG_PYTHON env var (only if shims are verified active via PATH check)
    4. Fallback to sys.executable
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    swap_active = os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1"

    # ─────────────────────────────────────────────────────────────
    # Priority 0: Self-awareness — config next to THIS script/exe
    #
    # IMPORTANT: Skip entirely when inside a swap shell.
    # When swapped, sys.argv[0] resolves to the host env's 8pkg binary
    # which has a .omnipkg_config.json pointing at the host Python (e.g.
    # 3.11). Self-awareness would "succeed" but return the wrong version.
    # Instead, fall through to Priority 2 (OMNIPKG_PYTHON + shims check)
    # which correctly picks up the swapped version.
    # ─────────────────────────────────────────────────────────────
    if not swap_active:
        script_path = Path(sys.argv[0]).resolve()
        script_dir = script_path.parent
        config_path = script_dir / ".omnipkg_config.json"

    if not swap_active and config_path.exists():
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
    # Priority 2: OMNIPKG_PYTHON — only inside an active swap shell.
    #
    # OMNIPKG_PYTHON leaks into parent shells after swap exits because
    # the parent shell's environment can't be unset by the child's EXIT
    # trap. The exe version check is useless as a leak detector — 3.10
    # will always report 3.10. The ONLY reliable signal is
    # _OMNIPKG_SWAP_ACTIVE, which the rcfile sets on spawn and the EXIT
    # trap unsets on close. If it's absent, we are NOT in a swap shell.
    # ─────────────────────────────────────────────────────────────
    if "OMNIPKG_PYTHON" in os.environ:
        claimed_version = os.environ["OMNIPKG_PYTHON"]
        if os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1":
            if debug_mode:
                print(f"[DEBUG-DISPATCH] ✅ _OMNIPKG_SWAP_ACTIVE set, trusting OMNIPKG_PYTHON={claimed_version}", file=sys.stderr)
            return resolve_python_path(claimed_version)
        else:
            if debug_mode:
                print(f"[DEBUG-DISPATCH] ⚠️  OMNIPKG_PYTHON={claimed_version} present but _OMNIPKG_SWAP_ACTIVE not set — leaked, ignoring", file=sys.stderr)

    # ─────────────────────────────────────────────────────────────
    # Fallback: whatever Python is running this script
    # ─────────────────────────────────────────────────────────────
    if debug_mode:
        print(_('[DEBUG-DISPATCH] Fallback to sys.executable: {}').format(sys.executable), file=sys.stderr)
    return Path(sys.executable)


def _shims_are_active_in_path(debug_mode: bool = False) -> bool:
    """
    DEPRECATED STUB — no longer used by determine_target_python().
    Kept so any external callers don't break.
    """
    return os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1"


def _verify_python_version(python_path: Path, claimed_version: str, debug_mode: bool = False) -> bool:
    """
    Ask the actual Python executable what version it is and check it matches
    the claimed major.minor. This is the ground truth — no env var guessing.
    """
    try:
        result = subprocess.run(
            [str(python_path), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=5,
        )
        actual = result.stdout.strip()
        claimed_mm = ".".join(str(claimed_version).split(".")[:2])
        match = actual == claimed_mm
        if debug_mode:
            status = "✅" if match else "❌"
            print(f"[DEBUG-DISPATCH] {status} Version check: exe reports {actual}, claimed {claimed_mm}", file=sys.stderr)
        return match
    except Exception as e:
        if debug_mode:
            print(f"[DEBUG-DISPATCH] Version check failed ({e}), rejecting", file=sys.stderr)
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
    # 1) Validate swap context
    #
    # Two hard requirements:
    #   A) _OMNIPKG_SWAP_ACTIVE=1  — rcfile sets it, EXIT trap unsets it.
    #      If it's gone, the swap shell exited and these env vars are stale.
    #   B) The resolved Python exe must actually report the claimed version.
    #      Ask it directly — no PATH tricks, no guessing.
    # ─────────────────────────────────────────────
    if target_version and venv_root:
        if os.environ.get("_OMNIPKG_SWAP_ACTIVE") != "1":
            if debug:
                print("[DEBUG-SHIM] _OMNIPKG_SWAP_ACTIVE not set — swap shell exited, ignoring leaked env vars",
                      file=sys.stderr)
            target_version = None

    if target_version and venv_root:
        candidate = resolve_python_path(target_version)
        if not candidate.exists() or not _verify_python_version(candidate, target_version, debug):
            if debug:
                print(f"[DEBUG-SHIM] Exe version mismatch — ignoring OMNIPKG_PYTHON={target_version}",
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
            real_exe = Path(path_dir) / prog_name
            if real_exe.exists() and os.access(real_exe, os.X_OK):
                if debug:
                    print(_('[DEBUG-SHIM] Found: {}').format(real_exe), file=sys.stderr)
                os.execv(str(real_exe), [str(real_exe)] + sys.argv[1:])

        # Nothing found → behave like a real shell command-not-found
        if debug:
            print(_('[DEBUG-SHIM] No {} found in PATH').format(prog_name), file=sys.stderr)

        print(_("Command '{}' not found, did you mean:").format(prog_name), file=sys.stderr)
        print("  command 'python3' from deb python3", file=sys.stderr)
        print("  command 'python' from deb python-is-python3", file=sys.stderr)
        sys.exit(127)

    # ─────────────────────────────────────────────
    # 3) Valid swap: execute the swapped Python
    # (candidate already resolved and verified above)
    # ─────────────────────────────────────────────
    target_python = candidate

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
            with open(registry_path, "r") as _dbg_f:
                print(f"[DEBUG-DISPATCH] Registry contents: {_dbg_f.read()}", file=sys.stderr)
        
        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
            
            interpreters = data.get("interpreters", {})
            
            # Try exact match first, then major.minor
            for key in [version, major_minor]:
                if key in interpreters:
                    path = Path(interpreters[key])
                    if debug_mode:
                        print(f"[DEBUG-DISPATCH] Registry entry for {key}: {path} (exists={path.exists()})", file=sys.stderr)
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


def spawn_swap_shell(version: str, python_path: Path, pkg_instance) -> int:
    """
    Spawn an interactive sub-shell with the swapped Python context.
    Handles all shim setup, PATH manipulation, .bat writing (Windows),
    rcfile generation (Unix), and debug output.

    This is the SINGLE source of truth for interactive swap logic.
    cli.py must not duplicate any of this.
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"

    from omnipkg.common_utils import safe_print
    from omnipkg.i18n import _

    # ── 1. Ensure shims dir exists ────────────────────────────────────────────
    shims_dir = pkg_instance.config_manager._ensure_shims_installed()
    original_venv = pkg_instance.config_manager.venv_path

    # ── 2. Build environment ──────────────────────────────────────────────────
    # IMPORTANT: OMNIPKG_PYTHON, OMNIPKG_VENV_ROOT, and _OMNIPKG_SWAP_ACTIVE are
    # intentionally NOT set in new_env. new_env is the inherited process environment
    # for the bash subprocess — anything in it persists across nested shells and
    # survives the EXIT trap (the trap unsets shell variables, not the inherited env).
    # These vars are set ONLY inside the rcfile so they exist exclusively within
    # the swap subshell and are fully cleaned up by the EXIT trap.
    new_env = os.environ.copy()
    # Strip any leaked OMNIPKG vars from the parent env before passing to bash
    for _var in ("OMNIPKG_PYTHON", "OMNIPKG_ACTIVE_PYTHON", "OMNIPKG_VENV_ROOT", "_OMNIPKG_SWAP_ACTIVE"):
        new_env.pop(_var, None)
    new_env["CONDA_CHANGEPS1"] = "false"
    new_env["CONDA_AUTO_ACTIVATE_BASE"] = "false"

    # Clean PATH: remove stale omnipkg shim/interpreter entries
    current_path = new_env.get("PATH", "")
    path_parts = current_path.split(os.pathsep)
    cleaned_parts = []
    for p in path_parts:
        if ".omnipkg/shims" in p or ".omnipkg\\shims" in p:
            continue
        if (".omnipkg/interpreters" in p or ".omnipkg\\interpreters" in p) and (
            "/bin" in p or "\\bin" in p or "\\Scripts" in p
        ):
            continue
        cleaned_parts.append(p)

    seen: set = set()
    deduped = []
    for p in cleaned_parts:
        if p and p not in seen:
            deduped.append(p)
            seen.add(p)

    # Shims are intentionally NOT inserted into new_env["PATH"].
    # new_env is passed to os.execle() which becomes the baseline inherited
    # environment for the bash process. If shims were in it, they would survive
    # across nested swap shells and the EXIT trap could not fully clean them:
    # the trap clears the shell variable but the process-inherited PATH is gone.
    # On nested swaps (3.11 shell → 3.7 shell → exit), the outer shell would
    # retain shims in its inherited env, causing _shims_are_active_in_path() to
    # return True indefinitely even after the inner shell exits.
    #
    # Shims are injected ONLY inside the rcfile via "export PATH=shims_dir:/home/claude/.npm-global/bin:/home/claude/.local/bin:/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    # which runs after bash initialises. The EXIT trap strips them in that same
    # shell. The parent shell's PATH is completely untouched.
    new_env["PATH"] = os.pathsep.join(deduped)

    # ── 3. Resolve pip executable for this interpreter ────────────────────────
    # Prefer <interp_dir>/pip over -m pip so the path is concrete and visible
    interp_dir = python_path.parent
    if sys.platform == "win32":
        pip_exe = interp_dir / "Scripts" / "pip.exe"
        if not pip_exe.exists():
            pip_exe = interp_dir / "pip.exe"
    else:
        pip_exe = interp_dir / "pip"
        if not pip_exe.exists():
            pip_exe = interp_dir / "pip3"

    # ── 4. Debug output ───────────────────────────────────────────────────────
    if debug_mode:
        safe_print("", file=sys.stderr)
        safe_print("=" * 70, file=sys.stderr)
        safe_print("[DEBUG-SWAP] omnipkg swap — pre-shell diagnostic", file=sys.stderr)
        safe_print("=" * 70, file=sys.stderr)

        # How to enable/disable debug — Windows-friendly
        if sys.platform == "win32":
            safe_print("[DEBUG-SWAP] To enable debug next time:", file=sys.stderr)
            safe_print("   set OMNIPKG_DEBUG=1   (Command Prompt)", file=sys.stderr)
            safe_print("   $env:OMNIPKG_DEBUG=1  (PowerShell)", file=sys.stderr)
            safe_print("   To disable: set OMNIPKG_DEBUG=  (CMD) / Remove-Item Env:OMNIPKG_DEBUG (PS)", file=sys.stderr)
        else:
            safe_print("[DEBUG-SWAP] To enable debug: export OMNIPKG_DEBUG=1", file=sys.stderr)
            safe_print("[DEBUG-SWAP] To disable:      unset OMNIPKG_DEBUG", file=sys.stderr)

        safe_print("", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] Python interpreter path : {python_path}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] Python interpreter exists: {python_path.exists()}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] pip executable path      : {pip_exe}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] pip exe exists           : {pip_exe.exists()}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] sys.executable (THIS proc): {sys.executable}", file=sys.stderr)

        # Find omnipkg entry point next to this interpreter
        if sys.platform == "win32":
            omnipkg_exe = python_path.parent / "Scripts" / "omnipkg.exe"
            pkg8_exe = python_path.parent / "Scripts" / "8pkg.exe"
        else:
            omnipkg_exe = python_path.parent / "omnipkg"
            pkg8_exe = python_path.parent / "8pkg"

        safe_print(f"[DEBUG-SWAP] omnipkg exe (target env) : {omnipkg_exe} (exists={omnipkg_exe.exists()})", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] 8pkg exe (target env)    : {pkg8_exe} (exists={pkg8_exe.exists()})", file=sys.stderr)
        safe_print("", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] OMNIPKG_PYTHON           : {version}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] OMNIPKG_VENV_ROOT        : {original_venv}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] Shims directory          : {shims_dir}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] PATH[0] (rcfile injects shims above this): {deduped[0]}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] CONDA_PREFIX             : {new_env.get('CONDA_PREFIX', 'NOT SET')}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] CONDA_DEFAULT_ENV        : {new_env.get('CONDA_DEFAULT_ENV', 'NOT SET')}", file=sys.stderr)

        # Config contents
        venv_root = find_absolute_venv_root()
        config_path = venv_root / ".omnipkg_config.json"
        interp_config_path = python_path.parent / ".omnipkg_config.json"
        for label, cp in [("venv config", config_path), ("interp config", interp_config_path)]:
            safe_print("", file=sys.stderr)
            safe_print(f"[DEBUG-SWAP] --- {label}: {cp} ---", file=sys.stderr)
            if cp.exists():
                try:
                    import json as _json
                    with open(cp, "r", encoding="utf-8") as _f:
                        _cfg = _json.load(_f)
                    for k, v in _cfg.items():
                        safe_print(f"[DEBUG-SWAP]   {k}: {v}", file=sys.stderr)
                except Exception as _e:
                    safe_print(f"[DEBUG-SWAP]   (error reading: {_e})", file=sys.stderr)
            else:
                safe_print("[DEBUG-SWAP]   (file not found)", file=sys.stderr)

        safe_print("=" * 70, file=sys.stderr)
        safe_print("", file=sys.stderr)

    # ── 5. Platform: Windows ──────────────────────────────────────────────────
    if sys.platform == "win32":
        shell = os.environ.get("COMSPEC", "cmd.exe")

        # Write .bat shims — python.bat and python3.bat just invoke the interpreter.
        # pip.bat explicitly calls -m pip so it uses the right pip regardless of
        # whether a standalone pip.exe is present in the interpreter's Scripts dir.
        scripts_dir = Path(shims_dir)
        scripts_dir.mkdir(parents=True, exist_ok=True)

        # python / python3 shims
        for cmd_name in ["python", "python3"]:
            (scripts_dir / f"{cmd_name}.bat").write_text(
                f'@echo off\n"{python_path}" %*\n',
                encoding="utf-8",
            )

        # pip shim — always use -m pip so it's guaranteed to belong to python_path
        (scripts_dir / "pip.bat").write_text(
            f'@echo off\n"{python_path}" -m pip %*\n',
            encoding="utf-8",
        )

        # Versioned 8pkg shim
        major_minor_flat = version.replace(".", "")
        _pkg8_exe = python_path.parent / "Scripts" / "8pkg.exe"
        if _pkg8_exe.exists():
            (scripts_dir / f"8pkg{major_minor_flat}.bat").write_text(
                f'@echo off\n"{_pkg8_exe}" --python {version} %*\n',
                encoding="utf-8",
            )

        if debug_mode:
            safe_print(f"[DEBUG-SWAP] Wrote python.bat  -> {scripts_dir / 'python.bat'}", file=sys.stderr)
            safe_print(f"[DEBUG-SWAP] Wrote python3.bat -> {scripts_dir / 'python3.bat'}", file=sys.stderr)
            safe_print(f"[DEBUG-SWAP] Wrote pip.bat     -> {scripts_dir / 'pip.bat'}", file=sys.stderr)
            safe_print(f"[DEBUG-SWAP] Spawning shell: {shell}", file=sys.stderr)

        safe_print(_("🐚 Spawning new shell... (Type 'exit' to return)"))
        safe_print(f"   🐍 Python {version} context active (via shims)")
        safe_print(_("   💡 Note: Type 'exit' to clean up and return"))

        conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
        if conda_env:
            safe_print(_("   📦 Conda env '{}' preserved").format(conda_env))

        if not debug_mode:
            safe_print(_("   🔍 To debug path/pip issues, run before swapping:"))
            safe_print(_("      set OMNIPKG_DEBUG=1        (Command Prompt)"))
            safe_print(_("      $env:OMNIPKG_DEBUG = '1'   (PowerShell)"))

        try:
            proc = subprocess.Popen([shell, "/K"], env=new_env)
            proc.wait()
        except Exception as e:
            safe_print(_("❌ Failed to spawn shell: {}").format(e))
            return 1
        finally:
            for var in ["OMNIPKG_PYTHON", "OMNIPKG_ACTIVE_PYTHON", "OMNIPKG_VENV_ROOT"]:
                os.environ.pop(var, None)

        return 0

    # ── 6. Platform: Unix ─────────────────────────────────────────────────────
    shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = Path(shell).name  # e.g. "bash", "zsh", "fish"

    # Build a temp rcfile so the swap context is active AND user aliases work.
    # Strategy:
    #   1. Source the user's real rc file first → all their aliases/functions load.
    #   2. Then inject our env vars + PATH on top.
    #   3. Trap EXIT to clean up OMNIPKG vars and strip shims from PATH.
    #
    # This is why the old os.execle(..., "-i") broke aliases: interactive mode
    # skips BASH_ENV and without --rcfile the startup sequence can load
    # /etc/bash.bashrc but NOT ~/.bashrc in some distros.

    if "bash" in shell_name:
        user_rc = Path.home() / ".bashrc"
        system_rc = Path("/etc/bash.bashrc")
    elif "zsh" in shell_name:
        user_rc = Path.home() / ".zshrc"
        system_rc = Path("/etc/zshrc")
    else:
        # fish, sh, etc. — fall back to simple interactive execle
        safe_print(_("🐚 Spawning shell... (Type 'exit' to return)"))
        safe_print(f"   🐍 Python {version} context active (via shims)")
        try:
            # For non-bash/zsh, just set env and exec interactive
            os.execle(shell, shell_name, "-i", new_env)
        except Exception as e:
            safe_print(_("❌ Failed to spawn shell: {}").format(e))
        return 1  # Only reached on execle failure

    # Write temp rcfile
    import tempfile as _tempfile
    with _tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, encoding="utf-8") as _tf:
        rcfile_path = _tf.name
        # Source user rc first (ignore errors if it doesn't exist)
        _tf.write(f'# omnipkg swap rcfile — Python {version}\n')
        if system_rc.exists():
            _tf.write(f'[ -f "{system_rc}" ] && source "{system_rc}"\n')
        if user_rc.exists():
            _tf.write(f'[ -f "{user_rc}" ] && source "{user_rc}"\n')
        # Inject swap context ON TOP of user's env (overrides their python/pip)
        _tf.write(f'\n# --- omnipkg swap context ---\n')
        _tf.write(f'export OMNIPKG_PYTHON="{version}"\n')
        _tf.write(f'export OMNIPKG_ACTIVE_PYTHON="{version}"\n')
        _tf.write(f'export OMNIPKG_VENV_ROOT="{original_venv}"\n')
        _tf.write(f'export _OMNIPKG_SWAP_ACTIVE=1\n')
        # Prepend shims to PATH (after user rc may have modified PATH)
        _tf.write(f'export PATH="{shims_dir}:$PATH"\n')
        # Override any user-defined exit() shell function (e.g. ones that call
        # conda deactivate instead of actually exiting) so that typing 'exit'
        # inside a swap shell always terminates the bash process and fires the
        # EXIT trap.  We undefine the function and replace it with a thin wrapper
        # that calls the real builtin, which guarantees the EXIT trap runs.
        _tf.write(f'\n# Ensure exit actually exits this shell (not a user conda wrapper)\n')
        _tf.write(f'unset -f exit 2>/dev/null\n')
        _tf.write(f'exit() {{ builtin exit "$@"; }}\n')
        # Set prompt to show conda env + python version
        conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
        env_prefix = f"({conda_env}) " if conda_env else ""
        _tf.write(f'\n# Show conda env + python version in prompt\n')
        _tf.write(f'export PS1="{env_prefix}(py{version}) \\u@\\h:\\w\\$ "\n')
        # Cleanup on exit — trap fires when the bash process actually terminates
        _tf.write(f'\ntrap \'\n')
        _tf.write(f'    unset OMNIPKG_PYTHON OMNIPKG_ACTIVE_PYTHON OMNIPKG_VENV_ROOT _OMNIPKG_SWAP_ACTIVE\n')
        _tf.write(f'    export PATH=$(echo "$PATH" | tr ":" "\\n" | grep -v ".omnipkg/shims" | tr "\\n" ":" | sed "s/:$//")\n')
        _tf.write(f'    rm -f "{rcfile_path}" 2>/dev/null\n')
        _tf.write(f"\' EXIT\n")

    if debug_mode:
        safe_print(f"[DEBUG-SWAP] rcfile written: {rcfile_path}", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] user rc       : {user_rc} (exists={user_rc.exists()})", file=sys.stderr)
        safe_print(f"[DEBUG-SWAP] Spawning      : {shell} --rcfile {rcfile_path}", file=sys.stderr)

    safe_print(_(f"🐚 Entering Python {version} swap context..."))
    safe_print(f"   🐍 Python {version} active — type 'exit' to return")

    try:
        # --rcfile loads ONLY our file (which sources user rc first).
        # This is the correct way to get both user aliases AND our injected env.
        os.execle(shell, shell_name, "--rcfile", rcfile_path, new_env)
    except Exception as e:
        safe_print(_("❌ Failed to spawn shell: {}").format(e))
        try:
            os.unlink(rcfile_path)
        except Exception:
            pass
        return 1

    return 0  # Only reached if execle fails


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