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
# NOTE: No omnipkg imports at top level — importing omnipkg.i18n triggers the
# full package boot (gettext init, locale detection, file I/O) which costs
# ~60-80ms even when the dispatcher does nothing but find a Python path and
# os.execv().  All omnipkg imports are now lazy (inside the functions that
# actually need them — error paths only, never the happy path).

def _i18n():
    """Lazy accessor for the _ translation function."""
    from omnipkg.i18n import _ as _gettext
    return _gettext

def _safe_print(msg, **kwargs):
    """Lazy safe_print — only imported on error paths."""
    from omnipkg.common_utils import safe_print
    safe_print(msg, **kwargs)

def main():
    """
    Omnipkg Unified Dispatcher.
    """
    _maybe_install_c_dispatcher()
    _ensure_native_shims()  # idempotent: ~1µs if shim exists, heals native Python shim if missing
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

    if debug_mode:
        print(f'[DEBUG-DISPATCH] ════════════════════════════════════════════════', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] omnipkg dispatcher startup', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] sys.argv           : {sys.argv}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] sys.executable     : {sys.executable}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] sys.prefix         : {sys.prefix}', file=sys.stderr)
        _argv0_path = Path(sys.argv[0])
        try:
            _argv0_resolved = _argv0_path.resolve()
            _is_symlink = _argv0_path.is_symlink()
            _symlink_target = os.readlink(str(_argv0_path)) if _is_symlink else "n/a"
        except Exception as _e:
            _argv0_resolved, _is_symlink, _symlink_target = "err", False, str(_e)
        print(f'[DEBUG-DISPATCH] argv[0] resolved   : {_argv0_resolved}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] argv[0] is_symlink : {_is_symlink} -> {_symlink_target}', file=sys.stderr)
        _search = Path(sys.argv[0]).resolve().parent
        _pyvenv_found = "NOT FOUND (will fall back to sys.prefix)"
        while _search != _search.parent:
            if (_search / "pyvenv.cfg").exists():
                _pyvenv_found = str(_search / "pyvenv.cfg")
                break
            _search = _search.parent
        print(f'[DEBUG-DISPATCH] pyvenv.cfg search  : {_pyvenv_found}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] OMNIPKG_VENV_ROOT  : {os.environ.get("OMNIPKG_VENV_ROOT", "NOT SET")}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] OMNIPKG_PYTHON     : {os.environ.get("OMNIPKG_PYTHON", "NOT SET")}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] _OMNIPKG_SWAP_ACTIVE: {os.environ.get("_OMNIPKG_SWAP_ACTIVE", "NOT SET")}', file=sys.stderr)
        _bin_dir = Path(sys.argv[0]).resolve().parent
        try:
            _shims = sorted(f.name for f in _bin_dir.iterdir() if f.name.startswith(("8pkg", "omnipkg")))
            print(f'[DEBUG-DISPATCH] shims in argv[0] bin/  : {_shims}', file=sys.stderr)
        except Exception as _le:
            print(f'[DEBUG-DISPATCH] could not list argv[0] bin/: {_le}', file=sys.stderr)
        _reg = Path(sys.prefix) / ".omnipkg" / "interpreters" / "registry.json"
        print(f'[DEBUG-DISPATCH] registry @ sys.prefix  : {_reg} (exists={_reg.exists()})', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] ════════════════════════════════════════════════', file=sys.stderr)

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
                    print(f'[DEBUG-DISPATCH] Config read error: {e}', file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # 🎯 STEP 0: DETECT VERSION-SPECIFIC COMMAND (8pkg39, omnipkg39, etc.)
    # ═══════════════════════════════════════════════════════════
    import re
    prog_name = Path(sys.argv[0]).name.lower()
    
    # Match both 8pkgXY and omnipkgXY (e.g. 8pkg39, omnipkg39, omnipkg311)
    version_match = re.match(r"(?:8pkg|omnipkg)(\d)(\d+)", prog_name)
    
    if version_match:
        major = version_match.group(1)
        minor = version_match.group(2)
        forced_version = f"{major}.{minor}"
        
        # Inject --python flag if not already present
        if "--python" not in sys.argv:
            sys.argv.insert(1, "--python")
            sys.argv.insert(2, forced_version)
        
        if debug_mode:
            print(f'[DEBUG-DISPATCH] Detected version-specific command: {prog_name}', file=sys.stderr)
            print(f'[DEBUG-DISPATCH] Injected --python {forced_version}', file=sys.stderr)
            print(f'[DEBUG-DISPATCH] Modified argv: {sys.argv}', file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # STEP 1: Identify how we were called
    # ═══════════════════════════════════════════════════════════
    
    # If called as 'python', 'python3', or 'pip' -> ACT AS SHIM
    if prog_name.startswith("python") or prog_name == "pip":
        if debug_mode:
            print("[DEBUG-SHIM] Intercepted call to '{}'".format(prog_name), file=sys.stderr)
        handle_shim_execution(prog_name, debug_mode)
        return
    
    # ═══════════════════════════════════════════════════════════
    # STEP 2: Determine which Python interpreter to use
    # ═══════════════════════════════════════════════════════════
    #
    # SPECIAL CASE: 'swap python X' must ALWAYS run via the host/native Python.
    # Routing it through a swapped interpreter (e.g. 3.7) can fail if that
    # interpreter is missing deps (e.g. importlib.metadata).
    # Package swaps (e.g. 'swap numpy==1.26.4') MUST use the active context Python.
    argv_commands = [a for a in sys.argv[1:] if not a.startswith("-")]
    is_swap_command = len(argv_commands) >= 1 and argv_commands[0] == "swap"
    is_swap_python = is_swap_command and len(argv_commands) >= 2 and argv_commands[1].lower().startswith("python")

    if debug_mode:
        print(f'[DEBUG-DISPATCH] argv_commands: {argv_commands}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] is_swap_python: {is_swap_python}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] _OMNIPKG_SWAP_ACTIVE: {os.environ.get("_OMNIPKG_SWAP_ACTIVE")}', file=sys.stderr)

    if is_swap_python and os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1":
        _saved = {k: os.environ.pop(k, None)
                  for k in ("OMNIPKG_PYTHON", "OMNIPKG_ACTIVE_PYTHON", "_OMNIPKG_SWAP_ACTIVE")}
        target_python = determine_target_python()
        for k, v in _saved.items():
            if v is not None:
                os.environ[k] = v
        if debug_mode:
            print("[DEBUG-DISPATCH] swap python inside swap shell — forcing host Python", file=sys.stderr)
    else:
        target_python = determine_target_python()
        if is_swap_command and not is_swap_python and os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1":
            if debug_mode:
                print("[DEBUG-DISPATCH] swap package inside swap shell — using active context Python", file=sys.stderr)
    
    if debug_mode:
        print(f'[DEBUG-DISPATCH] Using Python: {target_python}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH] Current executable: {sys.executable}', file=sys.stderr)
    
    # NEW
    venv_root = find_absolute_venv_root()
    # NEW
    is_managed = (
        str(target_python).startswith(str(venv_root))
        or str(target_python.resolve()).startswith(str(venv_root.resolve()))
    )
    
    if debug_mode:
        print(f'[DEBUG-DISPATCH] is_managed check:', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]   target_python         : {target_python}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]   target_python.resolve(): {target_python.resolve()}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]   venv_root             : {venv_root}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]   venv_root.resolve()   : {venv_root.resolve()}', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]   is_managed            : {is_managed}', file=sys.stderr)
    
    if not target_python.exists() or not is_managed:
        # Lazy import — only needed on this path
        import subprocess
        version_str = os.environ.get("OMNIPKG_PYTHON") or extract_version(target_python)
        print(f'⚠️  Python {version_str} not adopted — adopting now...', file=sys.stderr)
        adopt_result = subprocess.call(
            [sys.executable, "-m", "omnipkg.cli", "python", "adopt", version_str]
        )
        if adopt_result != 0:
            print(f'❌ Failed to adopt Python {version_str}.', file=sys.stderr)
            sys.exit(1)
        # Re-resolve after adoption and fall through to re-exec
        target_python = resolve_python_path(version_str)
        if not target_python.exists():
            print(f'❌ Adoption succeeded but interpreter still not found.', file=sys.stderr)
            sys.exit(1)

    # Try daemon socket first (fast path)
    if not is_swap_command:
        try:
            import socket
            import tempfile
            
            sock_path = os.path.join(tempfile.gettempdir(), "omnipkg", "omnipkg_daemon.sock")
            if sys.platform == "win32":
                conn_file = os.path.join(tempfile.gettempdir(), "omnipkg", "daemon_connection.txt")
                if os.path.exists(conn_file):
                    with open(conn_file, "r") as f:
                        conn_str = f.read().strip()
                    if conn_str.startswith("tcp://"):
                        host, port = conn_str[6:].split(":")
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.connect((host, int(port)))
                    else:
                        raise ValueError()
                else:
                    raise ValueError()
            else:
                if os.path.exists(sock_path):
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(sock_path)
                else:
                    raise ValueError()
                    
            req = {
                "type": "run_cli",
                "argv":["omnipkg"] + sys.argv[1:],
                "cwd": os.getcwd(),
                "isatty": sys.stdout.isatty(),
                "python_exe": str(target_python)
            }
            
            req_bytes = json.dumps(req).encode("utf-8")
            sock.sendall(len(req_bytes).to_bytes(8, "big") + req_bytes)
            
            while True:
                len_bytes = sock.recv(8)
                if not len_bytes:
                    break
                msg_len = int.from_bytes(len_bytes, "big")
                data = bytearray()
                while len(data) < msg_len:
                    chunk = sock.recv(min(msg_len - len(data), 8192))
                    if not chunk:
                        break
                    data.extend(chunk)
                msg = json.loads(data.decode("utf-8"))
                
                if msg.get("stream") == "stdout":
                    sys.stdout.write(msg.get("data", ""))
                    sys.stdout.flush()
                elif msg.get("stream") == "stderr":
                    sys.stderr.write(msg.get("data", ""))
                    sys.stderr.flush()
                elif msg.get("status") == "COMPLETED":
                    sys.exit(msg.get("exit_code", 0))
                elif msg.get("status") == "ERROR":
                    sys.stderr.write(msg.get("error", "") + "\n")
                    sys.exit(msg.get("exit_code", 1))
        except Exception:
            pass

    exec_args = [str(target_python), "-m", "omnipkg.cli"] + sys.argv[1:]

    if debug_mode:
        print(f'[DEBUG-DISPATCH] Executing: {" ".join(exec_args)}', file=sys.stderr)

    if sys.platform == "win32":
        import subprocess
        sys.exit(subprocess.call(exec_args))
    else:
        os.execv(str(target_python), exec_args)

def _maybe_install_c_dispatcher():
    import sys, os, subprocess, shutil
    from pathlib import Path

    debug = os.environ.get("OMNIPKG_DEBUG") == "1"

    _here = Path(__file__).parent  # src/omnipkg/
    if debug:
        print(f"[C-INSTALL] __file__  = {__file__}", file=sys.stderr)
        print(f"[C-INSTALL] _here     = {_here}", file=sys.stderr)

    # Search every plausible location for dispatcher.c
    candidates = [
        _here / "dispatcher.c",                                              # packaged alongside dispatcher.py
        _here.parent.parent / "tools" / "dispatcher_bin" / "dispatcher.c",  # editable: src/omnipkg/ -> repo root
        _here.parent / "tools" / "dispatcher_bin" / "dispatcher.c",         # alternate layout
        Path(sys.argv[0]).resolve().parent.parent / "tools" / "dispatcher_bin" / "dispatcher.c",  # relative to bin/
    ]

    c_source = None
    for candidate in candidates:
        if debug:
            print(f"[C-INSTALL] checking candidate: {candidate} -> exists={candidate.exists()}", file=sys.stderr)
        if candidate.exists():
            c_source = candidate
            break

    if c_source is None:
        if debug:
            print(f"[C-INSTALL] dispatcher.c not found in any candidate — skipping", file=sys.stderr)
        return

    if debug:
        print(f"[C-INSTALL] found dispatcher.c at: {c_source}", file=sys.stderr)

    gcc = shutil.which("gcc")
    if not gcc:
        if debug:
            print(f"[C-INSTALL] gcc not found in PATH — skipping", file=sys.stderr)
        return

    if debug:
        print(f"[C-INSTALL] gcc = {gcc}", file=sys.stderr)

    bin_dir = Path(sys.executable).parent
    marker = bin_dir / ".omnipkg_dispatch_compiled"

    if debug:
        print(f"[C-INSTALL] bin_dir = {bin_dir}", file=sys.stderr)
        print(f"[C-INSTALL] marker  = {marker} -> exists={marker.exists()}", file=sys.stderr)
        if marker.exists():
            print(f"[C-INSTALL] marker mtime={marker.stat().st_mtime}  source mtime={c_source.stat().st_mtime}  up_to_date={marker.stat().st_mtime >= c_source.stat().st_mtime}", file=sys.stderr)

    if marker.exists() and marker.stat().st_mtime >= c_source.stat().st_mtime:
        if debug:
            print(f"[C-INSTALL] binary is up-to-date — skipping recompile", file=sys.stderr)
        return

    binary_tmp = bin_dir / ("_omnipkg_dispatch_tmp.exe" if sys.platform == "win32" else "_omnipkg_dispatch_tmp")
    if debug:
        print(f"[C-INSTALL] compiling: gcc -O2 -o {binary_tmp} {c_source}", file=sys.stderr)

    try:
        r = subprocess.run(
            ["gcc", "-O2", "-o", str(binary_tmp), str(c_source)] + ([] if sys.platform == "win32" else ["-ldl"]),
            capture_output=True, timeout=15
        )
        if debug:
            print(f"[C-INSTALL] gcc returncode={r.returncode}", file=sys.stderr)
            if r.stdout:
                print(f"[C-INSTALL] gcc stdout: {r.stdout.decode(errors='replace')}", file=sys.stderr)
            if r.stderr:
                print(f"[C-INSTALL] gcc stderr: {r.stderr.decode(errors='replace')}", file=sys.stderr)

        if r.returncode != 0:
            if debug:
                print(f"[C-INSTALL] compile FAILED — staying on Python dispatcher", file=sys.stderr)
            return

        replaced = []
        for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
            target = bin_dir / name
            if target.exists():
                shutil.copy2(str(binary_tmp), str(target))
                os.chmod(str(target), 0o755)
                replaced.append(name)

        # Create versioned shims 3.7-3.15 as copies of the C binary
        for minor in range(7, 16):
            for prefix in ("8pkg", "omnipkg"):
                versioned = bin_dir / f"{prefix}3{minor}"
                if not versioned.exists() or versioned.stat().st_mtime < binary_tmp.stat().st_mtime:
                    shutil.copy2(str(binary_tmp), str(versioned))
                    os.chmod(str(versioned), 0o755)
                    replaced.append(f"{prefix}3{minor}")

        binary_tmp.unlink()
        if replaced:
            marker.touch()

        if debug:
            print(f"[C-INSTALL] done. replaced={replaced}  marker touched={marker}", file=sys.stderr)
            print(f"[C-INSTALL] ⚡ NEXT invocation will use the C dispatcher — re-exec now to use it immediately", file=sys.stderr)

    except Exception as e:
        if debug:
            print(f"[C-INSTALL] EXCEPTION: {e}", file=sys.stderr)
        if binary_tmp.exists():
            try:
                binary_tmp.unlink()
            except Exception:
                pass

def determine_target_python() -> Path:
    """
    PRIORITY ORDER:
    1. CLI flag --python  (explicit user intent — ALWAYS wins, including 8pkg39)
    2. OMNIPKG_PYTHON_XY_PATH env var (set by adopt; CI-safe, no swap needed)
    3. Self-awareness: config file next to THIS script/exe
       (SKIPPED when swap is active OR when --python is present — both cases
        mean self-awareness would return the wrong Python)
    4. OMNIPKG_PYTHON env var (only inside an active swap shell)
    5. Fallback to sys.executable
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"
    swap_active = os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1"

    # ─────────────────────────────────────────────────────────────
    # Priority 1: CLI flag --python  (explicit user intent)
    #
    # MUST be checked first — before self-awareness.
    # When the user runs 8pkg39, the dispatcher injects --python 3.9
    # into argv.  But 8pkg39 is a symlink to 8pkg which lives next to
    # python3.11, so the .omnipkg_config.json in that bin/ points at
    # 3.11.  If self-awareness runs first it "wins" and ignores --python
    # entirely, executing everything under 3.11.  Wrong.
    # ─────────────────────────────────────────────────────────────
    if "--python" in sys.argv:
        try:
            idx = sys.argv.index("--python")
            if idx + 1 < len(sys.argv):
                version = sys.argv[idx + 1]
                resolved = resolve_python_path(version)
                if debug_mode:
                    print(f'[DEBUG-DISPATCH] ✅ CLI flag: --python {version} -> {resolved}', file=sys.stderr)
                return resolved
        except (ValueError, IndexError):
            pass

    # ─────────────────────────────────────────────────────────────
    # Priority 2: OMNIPKG_PYTHON_XY_PATH env var
    #
    # Written by 'omnipkg python adopt' for every adopted interpreter.
    # Survives fresh shells (CI steps, new terminals) without swap.
    # Format: OMNIPKG_PYTHON_39_PATH, OMNIPKG_PYTHON_311_PATH, etc.
    # Only reached here when --python was NOT in argv (handled above).
    # ─────────────────────────────────────────────────────────────
    # (No --python in argv at this point, so nothing to look up here.
    #  This priority is consumed inline inside the --python block above
    #  via resolve_python_path which checks the registry. Kept as a
    #  comment marker for clarity in the priority chain.)

    # ─────────────────────────────────────────────────────────────
    # Priority 3: Self-awareness — config next to THIS script/exe
    #
    # Skipped when:
    #   - inside a swap shell (_OMNIPKG_SWAP_ACTIVE=1): self-awareness
    #     would resolve to the host Python's config (wrong version)
    #   - --python was in argv: already handled above (won't reach here)
    # ─────────────────────────────────────────────────────────────
    if not swap_active:
        script_path = Path(sys.argv[0]).resolve()
        script_dir = script_path.parent

        # On Windows, 8pkg.exe lives in Scripts\ but the config is written
        # one level up at the env root (debug\.omnipkg_config.json).
        # Check both: Scripts\.omnipkg_config.json (Linux/managed interpreters)
        # and Scripts\..\omnipkg_config.json (Windows conda/venv root).
        config_candidates = [script_dir / ".omnipkg_config.json"]
        if sys.platform == "win32":
            config_candidates.append(script_dir.parent / ".omnipkg_config.json")

        for config_path in config_candidates:
            if config_path.exists():
                try:
                    with open(config_path, "r") as f:
                        config = json.load(f)
                    python_exe = config.get("python_executable")
                    if python_exe:
                        python_path = Path(python_exe)
                        if python_path.exists():
                            if debug_mode:
                                print(f'[DEBUG-DISPATCH] ✅ Self-aware ({config_path}): {python_path}', file=sys.stderr)
                            return python_path
                except Exception as e:
                    if debug_mode:
                        print(f'[DEBUG-DISPATCH] Config read error ({config_path}): {e}', file=sys.stderr)

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
        print(f'[DEBUG-DISPATCH] ⚠️  All resolution strategies exhausted — fallback to sys.executable', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]    Cause: no --python flag, no self-aware config found, no active swap.', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]    In CI this usually means adopt did not run or venv_root resolved wrong.', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]    sys.executable: {sys.executable}', file=sys.stderr)
    return Path(sys.executable)


def _shims_are_active_in_path(debug_mode: bool = False) -> bool:
    """
    DEPRECATED STUB — no longer used by determine_target_python().
    Kept so any external callers don't break.
    """
    return os.environ.get("_OMNIPKG_SWAP_ACTIVE") == "1"


def _verify_python_version(python_path: Path, claimed_version: str, debug_mode: bool = False) -> bool:
    """
    Ask the actual Python executable what version it is...
    """
    import subprocess  # <--- ADD THIS LINE
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
    import subprocess  # <--- ADD THIS LINE
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
        print(f"[DEBUG-SHIM] Intercepted call to '{prog_name}'", file=sys.stderr)
        print(f'[DEBUG-SHIM] OMNIPKG_PYTHON={target_version}', file=sys.stderr)
        print(f'[DEBUG-SHIM] CONDA_PREFIX={conda_prefix}', file=sys.stderr)
        print(f'[DEBUG-SHIM] OMNIPKG_VENV_ROOT={venv_root}', file=sys.stderr)

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
        # Fast path: trust the registry path — it encodes major.minor
        # (e.g. cpython-3.8.20/bin/python3.8). No subprocess needed.
        if not candidate.exists():
            if debug:
                print(f"[DEBUG-SHIM] Candidate not found: {candidate}", file=sys.stderr)
            target_version = None
        else:
            major_minor = ".".join(str(target_version).split(".")[:2])
            path_str = str(candidate)
            path_has_version = (
                f"python{major_minor}" in path_str
                or f"cpython-{major_minor}" in path_str
                or f"python{major_minor.replace('.', '')}" in path_str
            )
            if not path_has_version:
                # Path doesn't encode version — last resort subprocess check
                if not _verify_python_version(candidate, target_version, debug):
                    if debug:
                        print(f"[DEBUG-SHIM] Exe version mismatch — ignoring OMNIPKG_PYTHON={target_version}",
                              file=sys.stderr)
                    target_version = None
            elif debug:
                print(f"[DEBUG-SHIM] ✅ Version {major_minor} confirmed from path (no subprocess)", file=sys.stderr)

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
                    print(f'[DEBUG-SHIM] Found: {real_exe}', file=sys.stderr)
                os.execv(str(real_exe), [str(real_exe)] + sys.argv[1:])

        # Nothing found → behave like a real shell command-not-found
        if debug:
            print(f'[DEBUG-SHIM] No {prog_name} found in PATH', file=sys.stderr)

        print(f"Command '{prog_name}' not found, did you mean:", file=sys.stderr)
        print("  command 'python3' from deb python3", file=sys.stderr)
        print("  command 'python' from deb python-is-python3", file=sys.stderr)
        sys.exit(127)

    # ─────────────────────────────────────────────
    # 3) Valid swap: execute the swapped Python
    # (candidate already resolved and verified above)
    # ─────────────────────────────────────────────
    target_python = candidate

    if debug:
        print(f'[DEBUG-SHIM] Executing: {target_python} {" ".join(sys.argv[1:])}', file=sys.stderr)

    # Direct execution - no daemon needed for simple commands
    if prog_name.startswith("python"):
        # Execute the target Python directly
        os.execv(str(target_python), [str(target_python)] + sys.argv[1:])
    elif prog_name == "pip":
        os.execv(str(target_python), [str(target_python), "-m", "pip"] + sys.argv[1:])
    else:
        # For any other command (pytest, black, mypy, etc.), try running it
        # as a module via the target Python first, then fall back to finding
        # the binary in the same bin/ dir as the target interpreter
        target_bin_dir = target_python.parent
        target_cmd = target_bin_dir / prog_name
        if target_cmd.exists():
            os.execv(str(target_cmd), [str(target_cmd)] + sys.argv[1:])
        else:
            # Fall back to running as -m module (works for pytest, coverage, etc.)
            os.execv(str(target_python), [str(target_python), "-m", prog_name] + sys.argv[1:])

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
        print(f'[DEBUG-DISPATCH] Absolute Venv Root: {venv_root}', file=sys.stderr)
    
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
                            print(f'[DEBUG-DISPATCH] Registry hit ({key}): {path}', file=sys.stderr)
                        # AUTO-CREATE config for this interpreter if missing.
                        # Without this, managed interpreters (cpython-3.11.9 etc)
                        # have no .omnipkg_config.json and fall back to the global
                        # config, running as the wrong Python version.
                        _ensure_interpreter_config(path, key, venv_root, debug_mode)
                        return path
        
        except Exception as e:
            if debug_mode:
                print(f'[DEBUG-DISPATCH] Registry read error: {e}', file=sys.stderr)
    
    # ═══════════════════════════════════════════════════════════
    # STEP 3: FALLBACK - Check Native Venv Binaries
    # ═══════════════════════════════════════════════════════════
    # If a specific version was requested but not found in registry,
    # return a non-existent path so auto-adopt triggers in main().
    if version:
        if debug_mode:
            print(f'[DEBUG-DISPATCH] Version {version} not in registry — returning sentinel for auto-adopt', file=sys.stderr)
        return venv_root / ".omnipkg" / "interpreters" / f"cpython-{version}" / "bin" / f"python{major_minor}"

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
                print(f'[DEBUG-DISPATCH] Found native: {candidate}', file=sys.stderr)
            return candidate
    
    # ═══════════════════════════════════════════════════════════
    # STEP 4: LAST RESORT - Check System PATH
    # ═══════════════════════════════════════════════════════════
    import shutil
    path_exe = shutil.which(f"python{version}") or shutil.which(f"python{major_minor}")
    
    if path_exe:
        if debug_mode:
            print(f'[DEBUG-DISPATCH] Found in PATH: {path_exe}', file=sys.stderr)
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

    # ── 1b. Ensure python_path is managed (not a system fallback) ────────────
    venv_str = str(original_venv.resolve())
    is_managed = str(python_path.resolve()).startswith(venv_str)
    if not is_managed:
        safe_print(_("⚠️  Python {} is not adopted yet (found system python at {}) — adopting now...").format(version, python_path))
        adopt_result = pkg_instance.adopt_interpreter(version)
        if adopt_result != 0:
            safe_print(_("❌ Failed to adopt Python {}.").format(version))
            safe_print(_("   Try manually: 8pkg python adopt {}").format(version))
            return 1
        python_path = resolve_python_path(version)
        if not str(python_path.resolve()).startswith(venv_str):
            safe_print(_("❌ Adoption succeeded but interpreter still not managed: {}").format(python_path))
            return 1
        safe_print(_("✅ Python {} adopted — continuing with swap...").format(version))

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

        # 8pkg / omnipkg shims — route ALL bare 8pkg/omnipkg calls through the
        # swapped interpreter's own exe so the dispatcher sees the right Python.
        # Without these, `8pkg info python` resolves to the conda Scripts\8pkg.exe
        # (3.12) instead of the swapped interpreter's 8pkg (3.11).
        for _shim_name in ["8pkg", "omnipkg"]:
            _target_exe = python_path.parent / "Scripts" / f"{_shim_name}.exe"
            if _target_exe.exists():
                (scripts_dir / f"{_shim_name}.bat").write_text(
                    f'@echo off\n"{_target_exe}" --python {version} %*\n',
                    encoding="utf-8",
                )

        # CRITICAL: Set swap context vars in new_env so the dispatcher's
        # Priority 4 (OMNIPKG_PYTHON) fires correctly inside the child shell.
        # On Unix these are set inside the rcfile; on Windows they must be in
        # the inherited environment since cmd.exe has no rcfile mechanism.
        # _OMNIPKG_SWAP_ACTIVE is the gate that prevents stale OMNIPKG_PYTHON
        # from leaking — both must be set together.
        new_env["OMNIPKG_PYTHON"] = version
        new_env["OMNIPKG_VENV_ROOT"] = str(original_venv)
        new_env["_OMNIPKG_SWAP_ACTIVE"] = "1"

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
        import subprocess  # <--- ADD THIS LINE
        try:
            new_env["PATH"] = str(scripts_dir) + os.pathsep + new_env["PATH"]
            proc = subprocess.Popen([shell, "/K"], env=new_env)
            proc.wait()
        except Exception as e:
            safe_print(_("❌ Failed to spawn shell: {}").format(e))
            return 1
        finally:
            for var in ["OMNIPKG_PYTHON", "OMNIPKG_ACTIVE_PYTHON", "OMNIPKG_VENV_ROOT", "_OMNIPKG_SWAP_ACTIVE"]:
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
    # NEW
    else:
        # fish: has its own config mechanism, -i is enough since env is inherited
        # git bash (MINGW): IS bash under the hood — shell_name will be "bash",
        #   so it won't reach here. But MSYS2/cygwin bash also resolves to "bash".
        # sh / dash / ksh: no rcfile concept, -i + inherited env is the best we can do.
        # tcsh / csh: sourcing works differently, -i is safest fallback.
        if "fish" in shell_name:
            safe_print(_("🐚 Entering Python {} swap context (fish)...").format(version))
            safe_print(f"   🐍 Python {version} active — type 'exit' to return")
            safe_print(f"   ⚠️  Fish shell: aliases may not load. Run 'source ~/.config/fish/config.fish' if needed.")
        else:
            safe_print(_("🐚 Entering Python {} swap context...").format(version))
            safe_print(f"   🐍 Python {version} active — type 'exit' to return")
            safe_print(f"   ⚠️  Shell '{shell_name}' not fully supported — env vars active but aliases may not load.")
        try:
            os.execle(shell, shell_name, "-i", new_env)
        except Exception as e:
            safe_print(_("❌ Failed to spawn shell: {}").format(e))
        return 1

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
        _# NEW
        if "zsh" in shell_name:
            _tf.write(f'export PROMPT="{env_prefix}(py{version}) %n@%m:%~%# "\n')
        else:
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

    # NEW — zsh uses ZDOTDIR trick; bash keeps --rcfile
    try:
        if "zsh" in shell_name:
            import tempfile as _td, shutil as _shutil
            zdotdir = _td.mkdtemp(prefix="omnipkg_zdot_")
            zshrc = Path(zdotdir) / ".zshrc"
            _shutil.copy2(rcfile_path, str(zshrc))
            os.unlink(rcfile_path)
            content = zshrc.read_text()
            content = content.replace(
                f'rm -f "{rcfile_path}" 2>/dev/null',
                f'rm -rf "{zdotdir}" 2>/dev/null'
            )
            zshrc.write_text(content)
            new_env["ZDOTDIR"] = zdotdir
            os.execle(shell, shell_name, "-i", new_env)
        else:
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
                print(f'[DEBUG-DISPATCH] Using OMNIPKG_VENV_ROOT override: {override}', file=sys.stderr)
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
                    print(f'[DEBUG-DISPATCH] Found venv via .omnipkg split: {original_venv}', file=sys.stderr)
                return original_venv

            # If no pyvenv.cfg at that level, search upward from there
            search_dir = original_venv
            while search_dir != search_dir.parent:
                if (search_dir / "pyvenv.cfg").exists():
                    if debug_mode:
                        print(f'[DEBUG-DISPATCH] Found venv via upward search: {search_dir}', file=sys.stderr)
                    return search_dir
                search_dir = search_dir.parent

            # Last resort: if we can't find pyvenv.cfg, just use the directory
            # before .omnipkg as it's definitely the venv root
            if debug_mode:
                print(f'[DEBUG-DISPATCH] Using pre-.omnipkg path: {original_venv}', file=sys.stderr)
            return original_venv

    # --- Standard upward search for non-managed interpreters ---
    # Search upwards from the current executable for pyvenv.cfg
    search_dir = current_executable.parent
    while search_dir != search_dir.parent:  # Stop at the filesystem root
        if (search_dir / "pyvenv.cfg").exists():
            if debug_mode:
                print(f'[DEBUG-DISPATCH] Found venv via standard search: {search_dir}', file=sys.stderr)
            return search_dir
        search_dir = search_dir.parent

    # --- Conda environment detection ---
    # Conda envs never have pyvenv.cfg but have reliable markers:
    #   1. $CONDA_PREFIX env var (set by `conda activate`)
    #   2. conda-meta/ directory at the env root
    # Check these before falling back to sys.prefix so we get the right
    # root even when running inside a conda env that was never activated
    # via `conda activate` (e.g. direct invocation in CI).
    conda_prefix_env = os.environ.get("CONDA_PREFIX")
    if conda_prefix_env:
        conda_root = Path(conda_prefix_env)
        if (conda_root / "conda-meta").is_dir():
            if debug_mode:
                print(f'[DEBUG-DISPATCH] ✅ Conda env via $CONDA_PREFIX: {conda_root}', file=sys.stderr)
            return conda_root

    # sys.prefix points at the conda env root for direct invocations
    # (e.g. /path/to/envs/debug) — confirm it's actually conda before trusting it
    sys_prefix_path = Path(sys.prefix)
    if (sys_prefix_path / "conda-meta").is_dir():
        if debug_mode:
            print(f'[DEBUG-DISPATCH] ✅ Conda env via sys.prefix/conda-meta: {sys_prefix_path}', file=sys.stderr)
        return sys_prefix_path

    # Only use sys.prefix as a last resort if all else fails.
    # In CI (GitHub Actions hostedtoolcache), there is no pyvenv.cfg so this
    # is the normal path — registry and symlinks will be under sys.prefix/.omnipkg/
    if debug_mode:
        print(f'[DEBUG-DISPATCH] ⚠️  sys.prefix fallback — no pyvenv.cfg found walking up from argv[0]', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]    Expected in CI / hostedtoolcache environments.', file=sys.stderr)
        print(f'[DEBUG-DISPATCH]    venv_root => sys.prefix = {sys.prefix}', file=sys.stderr)
        _reg_chk = Path(sys.prefix) / ".omnipkg" / "interpreters" / "registry.json"
        print(f'[DEBUG-DISPATCH]    registry exists: {_reg_chk.exists()} @ {_reg_chk}', file=sys.stderr)
    return Path(sys.prefix)

def find_venv_root() -> Path:
    """Find the virtual environment root (legacy function for compatibility)."""
    return find_absolute_venv_root()

def extract_version(python_path: Path) -> str:
    """Extract version string from Python path for error messages."""
    import re
    match = re.search(r"python(\d+\.\d+)", str(python_path))
    return match.group(1) if match else "unknown"

def _get_known_versions() -> set:
    """
    Return the set of Python version strings currently registered in the
    omnipkg interpreter registry (e.g. {'3.9', '3.10', '3.11'}).

    Used by main() to distinguish "not adopted yet" from "completely invalid
    version that can never exist", so we can auto-adopt the former and
    immediately reject the latter.

    This is intentionally self-contained — no imports from core.py or cli.py
    because it runs before we have a target Python to delegate to.
    """
    try:
        venv_root = find_absolute_venv_root()
        registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
        if not registry_path.exists():
            return set()
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("interpreters", {}).keys())
    except Exception:
        return set()


def _is_plausible_python_version(version: str) -> bool:
    """
    Return True if `version` *could* be a real CPython release, i.e. the
    major is 3 and the minor is in a sensible range (0-99).

    This gates the auto-adopt flow so that typos like "3.200" or "4.0" are
    caught early with a clear error instead of a 30-second download attempt.
    We deliberately allow future minors (e.g. 3.15) — we can't know what
    Python releases will exist; the actual download will fail if unavailable.

    Version 2.x is excluded because omnipkg only manages CPython 3.x.
    """
    import re as _re
    m = _re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", version.strip())
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return major == 3 and 0 <= minor <= 99



def _ensure_native_shims() -> None:
    """
    Self-healing shim check for the native/primary Python.

    The adoption path short-circuits with "already available" for the native
    interpreter and skips install_versioned_entrypoints, leaving no 8pkg311
    (or 8pkg3X for whatever is native). This function runs at dispatcher
    startup and fixes that in ~1µs on the happy path (shim already exists).

    Logic:
      1. Derive the running Python version from sys.version_info (no subprocess).
      2. Check whether the versioned shim already exists next to argv[0].
      3. If missing, call install_versioned_entrypoints() for the native version.
         That function is fully idempotent and handles both Unix symlinks and
         Windows .bat shims.
    """
    debug_mode = os.environ.get("OMNIPKG_DEBUG") == "1"

    # Derive version from the running interpreter — no subprocess needed.
    vi = sys.version_info
    version = f"{vi.major}.{vi.minor}"
    flat = f"{vi.major}{vi.minor}"          # "3.11" -> "311"

    # Find the bin dir where our entry points live (next to argv[0] or sys.executable)
    argv0 = Path(sys.argv[0]).resolve()
    bin_dir = argv0.parent

    # Quick exit: shim already exists — this is the common case after first run.
    if sys.platform == "win32":
        shim_path = bin_dir / f"8pkg{flat}.bat"
    else:
        shim_path = bin_dir / f"8pkg{flat}"

    if shim_path.exists():
        # Still proactively create all versioned shims 3.7-3.15 if any are missing
        main_shim = bin_dir / "8pkg"
        omni_shim = bin_dir / "omnipkg"
        for minor in range(7, 16):
            for prefix, target in [("8pkg", main_shim), ("omnipkg", omni_shim)]:
                versioned = bin_dir / f"{prefix}3{minor}"
                if not versioned.exists() and target.exists():
                    try:
                        os.link(str(target), str(versioned))
                    except Exception:
                        try:
                            import shutil
                            shutil.copy2(str(target), str(versioned))
                        except Exception:
                            pass
        return  # already installed, nothing to do

    # Shim is missing — this is the native Python that adoption skipped.
    if debug_mode:
        print(f"[DEBUG-DISPATCH] _ensure_native_shims: 8pkg{flat} missing — creating shims for native Python {version}", file=sys.stderr)

    # We need venv_root to call install_versioned_entrypoints.
    venv_root = find_absolute_venv_root()

    # Confirm this interpreter is actually registered (or is the primary).
    # If it's not registered at all we don't want to create phantom shims.
    registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    if registry_path.exists():
        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
            interpreters = data.get("interpreters", {})
            primary = data.get("primary_version", "")
            # Accept if it's the primary OR explicitly registered
            if version not in interpreters and version != primary:
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] _ensure_native_shims: {version} not in registry, skipping", file=sys.stderr)
                return
        except Exception as e:
            if debug_mode:
                print(f"[DEBUG-DISPATCH] _ensure_native_shims: registry read error: {e}", file=sys.stderr)
            return
    else:
        # No registry yet (pre-first-adopt). Don't create phantom shims.
        if debug_mode:
            print(f"[DEBUG-DISPATCH] _ensure_native_shims: no registry yet, skipping", file=sys.stderr)
        return

    # Call the canonical shim installer — handles Unix symlinks and Windows .bat
    interpreter_path = Path(sys.executable)
    install_versioned_entrypoints(interpreter_path, version, venv_root, debug_mode)
    if debug_mode:
        print(f"[DEBUG-DISPATCH] _ensure_native_shims: ✅ shims installed for native Python {version}", file=sys.stderr)


def install_versioned_entrypoints(
    interpreter_path: Path,
    version: str,
    venv_root: Path,
    debug_mode: bool = False,
) -> None:
    """
    Called by 'omnipkg python adopt' after a new interpreter is registered.

    Does TWO things that make versioned commands work in CI (fresh shells,
    no swap active):

    1. SYMLINKS  — creates  omnipkgXY / 8pkgXY  next to the main entry points
       in the venv's bin/ directory.  GitHub Actions runners find them in PATH
       automatically because the venv bin/ is already on PATH.

       Example for 3.9:
         $VENV/bin/omnipkg39  →  $VENV/bin/omnipkg   (symlink)
         $VENV/bin/8pkg39     →  $VENV/bin/8pkg       (symlink)

    2. ENV-VAR SNIPPET  — writes a one-liner to
         $VENV/.omnipkg/profile.d/omnipkg_pythons.sh
       that exports OMNIPKG_PYTHON_39_PATH=/path/to/python3.9 etc. for every
       known interpreter. The venv activate script sources profile.d/*.sh, so
       the var is available in every shell that activates the env — including
       each CI step that runs `source venv/bin/activate`.

    Priority 0.5 in determine_target_python() reads OMNIPKG_PYTHON_XY_PATH,
    so the versioned commands resolve correctly without swap or a live subshell.
    """
    debug_mode = debug_mode or (os.environ.get("OMNIPKG_DEBUG") == "1")
    flat = version.replace(".", "")          # "3.9" -> "39", "3.11" -> "311"
    bin_dir = venv_root / ("Scripts" if sys.platform == "win32" else "bin")

    # ── 1. Create versioned shims (symlinks on Unix, .bat wrappers on Windows) ──
    for base_name in ("omnipkg", "8pkg"):
        if sys.platform == "win32":
            # Windows: the entry point is a .exe or .bat script.
            # CRITICAL: we must inject --python X.Y explicitly in the .bat so
            # that sys.argv[0] being "8pkg" (not "8pkg310") doesn't lose the
            # version. Delegating bare to 8pkg.exe strips the version suffix
            # and the dispatcher falls back to sys.executable (always 3.11).
            src_bat = None
            for ext in (".exe", ".bat", ".cmd", ""):
                candidate = bin_dir / f"{base_name}{ext}"
                if candidate.exists():
                    src_bat = candidate
                    break
            if src_bat is None:
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] Skipping Windows shim for {base_name} — not found in {bin_dir}", file=sys.stderr)
                continue
            link_bat = bin_dir / f"{base_name}{flat}.bat"
            # Derive major.minor from flat ("310" -> "3.10", "39" -> "3.9")
            _maj = flat[0]
            _min = flat[1:]
            _ver = f"{_maj}.{_min}"
            try:
                # Inject --python X.Y as first arg so dispatcher version detection
                # works even though sys.argv[0] will be "8pkg" not "8pkg310"
                bat_content = f'@echo off\r\n"{src_bat}" --python {_ver} %*\r\n'
                link_bat.write_text(bat_content, encoding="ascii")
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ✅ Windows .bat shim: {link_bat} -> {src_bat.name} --python {_ver}", file=sys.stderr)
            except Exception as e:
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ⚠️  Could not create Windows shim {link_bat}: {e}", file=sys.stderr)
        else:
            # Unix: relative symlink within same directory
            src = bin_dir / base_name
            if not src.exists():
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] Skipping symlink for {base_name} — not found at {src}", file=sys.stderr)
                continue
            link = bin_dir / f"{base_name}{flat}"
            try:
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(src.name)
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ✅ Symlink: {link} -> {src.name}", file=sys.stderr)
            except Exception as e:
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ⚠️  Could not create symlink {link}: {e}", file=sys.stderr)

    # ── 1b. ALSO symlink next to the actual running entry point ─────────────────
    # When omnipkg is installed with `pip install --user`, the 8pkg entry point
    # lands in ~/.local/bin — NOT in venv_root/bin. The block above only covers
    # venv_root/bin. This covers wherever `8pkg` actually lives so that `8pkg310`
    # is always findable in the same directory as the `8pkg` that was invoked.
    actual_entry = Path(sys.argv[0]).resolve()
    actual_bin_dir = actual_entry.parent
    if actual_bin_dir.resolve() != bin_dir.resolve():
        if debug_mode:
            print(f"[DEBUG-DISPATCH] Entry point dir differs from venv bin/", file=sys.stderr)
            print(f"[DEBUG-DISPATCH]   venv bin/   : {bin_dir}", file=sys.stderr)
            print(f"[DEBUG-DISPATCH]   actual bin/ : {actual_bin_dir}", file=sys.stderr)
        for base_name in ("omnipkg", "8pkg"):
            src = actual_bin_dir / base_name
            if not src.exists():
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] Skipping extra symlink: {src} not found", file=sys.stderr)
                continue
            link = actual_bin_dir / f"{base_name}{flat}"
            try:
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(src.name)
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ✅ Extra symlink (entry-point dir): {link} -> {src.name}", file=sys.stderr)
            except Exception as e:
                if debug_mode:
                    print(f"[DEBUG-DISPATCH] ⚠️  Could not create extra symlink {link}: {e}", file=sys.stderr)
    else:
        if debug_mode:
            print(f"[DEBUG-DISPATCH] Entry point dir == venv bin/ — no extra symlinks needed", file=sys.stderr)

    # ── 2. Write / update env-var snippet ─────────────────────────────────────
    profile_dir = venv_root / ".omnipkg" / "profile.d"
    profile_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = profile_dir / "omnipkg_pythons.sh"

    # Read existing known interpreters from the registry so we can emit ALL of
    # them in one idempotent file (not just the one being adopted right now).
    known: dict[str, str] = {}
    registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    if registry_path.exists():
        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
            for ver, path in data.get("interpreters", {}).items():
                if Path(path).exists():
                    known[ver] = path
        except Exception:
            pass

    # Also include the interpreter being adopted right now (may not be in
    # the registry yet if called before the registry write completes).
    known[version] = str(interpreter_path)

    lines = [
        "# Auto-generated by omnipkg — do not edit manually.",
        "# Sourced by the venv activate script so every shell (including CI steps)",
        "# can resolve versioned commands (omnipkg39, 8pkg311, etc.) without swap.",
        "",
    ]
    for ver, path in sorted(known.items()):
        ver_flat = ver.replace(".", "")
        lines.append(f'export OMNIPKG_PYTHON_{ver_flat}_PATH="{path}"')

    try:
        snippet_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if debug_mode:
            print(f"[DEBUG-DISPATCH] ✅ Profile snippet written: {snippet_path}", file=sys.stderr)
    except Exception as e:
        if debug_mode:
            print(f"[DEBUG-DISPATCH] ⚠️  Could not write profile snippet: {e}", file=sys.stderr)

    # ── 3. Patch venv activate to source profile.d ───────────────────────────
    # Only needed once — idempotent.
    _patch_activate_script(bin_dir, profile_dir, debug_mode)


def _patch_activate_script(bin_dir: Path, profile_dir: Path, debug_mode: bool) -> None:
    """
    Append a one-time source block to $VENV/bin/activate so that
    profile.d/omnipkg_pythons.sh is loaded in every shell that activates
    the venv (including each GitHub Actions step that runs
    `source venv/bin/activate` or `pip install -e .` in a matrix job).
    """
    activate = bin_dir / "activate"
    if not activate.exists():
        return

    marker = "# omnipkg-profile.d-source"
    try:
        content = activate.read_text(encoding="utf-8")
    except Exception:
        return

    if marker in content:
        return  # already patched

    snippet = (
        f"\n{marker}\n"
        f'if [ -d "{profile_dir}" ]; then\n'
        f'    for _f in "{profile_dir}"/*.sh; do\n'
        f'        [ -r "$_f" ] && . "$_f"\n'
        f'    done\n'
        f'fi\n'
        f"unset _f\n"
    )
    try:
        with open(activate, "a", encoding="utf-8") as f:
            f.write(snippet)
        if debug_mode:
            print(f"[DEBUG-DISPATCH] ✅ Patched activate script: {activate}", file=sys.stderr)
    except Exception as e:
        if debug_mode:
            print(f"[DEBUG-DISPATCH] ⚠️  Could not patch activate: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()