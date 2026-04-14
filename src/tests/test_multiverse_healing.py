"""
Multiverse healing test — uses direct versioned binaries (8pkg39, 8pkg311, etc.)
and reads interpreter paths from the omnipkg registry.
No swap needed, works in CI across shells.
"""
import sys
import os
import subprocess
import json
import time
import traceback
from pathlib import Path

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _


# --- REGISTRY HELPERS ---

def get_registry(venv_root: Path) -> dict:
    """Read the omnipkg interpreter registry."""
    registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    if not registry_path.exists():
        raise RuntimeError(f"Registry not found: {registry_path}")
    return json.loads(registry_path.read_text(encoding="utf-8"))


def get_interpreter(venv_root: Path, version: str) -> Path:
    """Get the python executable path for a given version from the registry."""
    registry = get_registry(venv_root)
    interpreters = registry.get("interpreters", {})
    if version not in interpreters:
        raise RuntimeError(
            f"Python {version} not found in registry. "
            f"Available: {list(interpreters.keys())}"
        )
    exe = Path(interpreters[version])
    if not exe.exists():
        raise RuntimeError(f"Interpreter path does not exist: {exe}")
    return exe


def get_versioned_bin(venv_root: Path, version: str, cmd: str) -> list:
    """
    Return a command prefix for a versioned omnipkg invocation.

    Strategy (in order):
      1. 8pkg.exe --python X.Y   — preferred; exe is directly executable,
                                   no shell needed, works in subprocess.
      2. cmd /c 8pkgXY.bat       — fallback for Windows when only .bat exists;
                                   .bat files require cmd.exe to interpret them
                                   and CANNOT be launched via CreateProcess directly.
      3. 8pkgXY (no extension)   — Unix shim / symlink.

    Returns a list so callers can do: run(get_versioned_bin(...) + ["install", ...])
    """
    ver_tag = version.replace(".", "")  # "3.9" -> "39"
    is_windows = sys.platform == "win32"

    for bin_dir in [venv_root / "Scripts", venv_root / "bin"]:
        # --- Prefer the plain exe with --python flag (cross-platform, no shell needed) ---
        exe = bin_dir / f"{cmd}.exe"
        if exe.exists():
            return [str(exe), "--python", version]

        plain = bin_dir / cmd
        if plain.exists() and not is_windows:
            return [str(plain), "--python", version]

        # --- .bat fallback: must be wrapped in cmd /c on Windows ---
        for bat_suffix in [f"{cmd}{ver_tag}.bat", f"{cmd}{ver_tag}.cmd"]:
            bat = bin_dir / bat_suffix
            if bat.exists():
                if is_windows:
                    return ["cmd", "/c", str(bat)]
                else:
                    # Should not happen, but handle gracefully
                    return [str(bat)]

        # --- Unix shim with no extension ---
        shim = bin_dir / f"{cmd}{ver_tag}"
        if shim.exists():
            return [str(shim)]

    raise RuntimeError(
        f"Could not find '{cmd}' binary for Python {version} in {venv_root}.\n"
        f"  Looked for: {cmd}.exe, {cmd}, {cmd}{ver_tag}.bat, {cmd}{ver_tag}.cmd, {cmd}{ver_tag}\n"
        f"  Under: {venv_root / 'Scripts'} and {venv_root / 'bin'}"
    )


def detect_venv_root() -> Path:
    """Detect the omnipkg venv root from env or config."""
    override = os.environ.get("OMNIPKG_VENV_ROOT")
    if override:
        return Path(override)
    # Fall back to reading config next to this python
    try:
        from omnipkg.core import ConfigManager
        cm = ConfigManager(suppress_init_messages=True)
        return cm.venv_path
    except Exception:
        pass
    raise RuntimeError("Could not determine OMNIPKG venv root. Set OMNIPKG_VENV_ROOT.")


# --- SUBPROCESS HELPER ---

def run(cmd, description, check=True, env=None):
    """Run a command, stream output, return full output string."""
    safe_print(f"\n>> {description}")
    safe_print(f"   cmd: {' '.join(str(c) for c in cmd)}")
    _env = os.environ.copy()
    if env:
        _env.update(env)
    # Prevent recursive heal loops in subprocesses
    _env["OMNIPKG_DISABLE_AUTO_ALIGN"] = "1"
    _env["OMNIPKG_SUBPROCESS_MODE"] = "1"

    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=_env,
        )
    except (OSError, FileNotFoundError) as exc:
        # Happens when the executable itself can't be launched (e.g. .bat without cmd /c)
        safe_print(f"   [LAUNCH ERROR] Could not start process: {exc}")
        safe_print(f"   Attempted executable: {cmd[0]!r}")
        safe_print(f"   Full command: {cmd}")
        if check:
            raise RuntimeError(
                f"Failed to launch '{cmd[0]}': {exc}\n"
                f"  Full command: {cmd}\n"
                f"  Hint: on Windows, .bat files cannot be launched directly via "
                f"subprocess — they must be wrapped with ['cmd', '/c', ...]"
            ) from exc
        return ""

    lines = []
    for line in iter(proc.stdout.readline, ""):
        stripped = line.rstrip()
        if stripped:
            print(f" | {stripped}")
        lines.append(line)
    proc.stdout.close()
    rc = proc.wait()
    output = "".join(lines)

    safe_print(f"   exit code: {rc}")

    if check and rc != 0:
        safe_print(f"\n   [FAILURE DETAILS] Command exited with code {rc}")
        safe_print(f"   Executable : {cmd[0]!r}")
        safe_print(f"   Full cmd   : {' '.join(str(c) for c in cmd)}")
        if output.strip():
            safe_print("   --- captured output ---")
            for line in output.splitlines():
                safe_print(f"   {line}")
            safe_print("   --- end output ---")
        else:
            safe_print("   (no output captured — process may have failed to start)")
        raise subprocess.CalledProcessError(rc, cmd, output=output)

    return output


# --- PAYLOAD FUNCTIONS (called via subprocess self-invocation) ---

def run_legacy_payload():
    import scipy.signal
    import numpy
    import scipy
    print(
        f"--- Python {sys.version.split()[0]} | SciPy {scipy.__version__} | NumPy {numpy.__version__} ---",
        file=sys.stderr,
    )
    data = numpy.array([1, 2, 3, 4, 5])
    result = {"result": int(scipy.signal.convolve(data, data).sum())}
    print(json.dumps(result))


def run_modern_payload(legacy_json: str):
    import tensorflow as tf
    print(
        f"--- Python {sys.version.split()[0]} | TensorFlow {tf.__version__} ---",
        file=sys.stderr,
    )
    legacy = json.loads(legacy_json)
    prediction = "SUCCESS" if legacy["result"] > 200 else "FAILURE"
    print(json.dumps({"prediction": prediction}))


# --- MAIN TEST ---

def multiverse_analysis():
    venv_root = detect_venv_root()
    safe_print(f"[INFO] venv root: {venv_root}")

    registry = get_registry(venv_root)
    safe_print(f"[INFO] available interpreters: {list(registry['interpreters'].keys())}")

    # === STEP 1: Python 3.9 — legacy scipy/numpy ===
    safe_print("\n[STEP 1] Python 3.9 — installing legacy packages...")

    pkg39_cmd = get_versioned_bin(venv_root, "3.9", "8pkg")
    py39      = get_interpreter(venv_root, "3.9")
    safe_print(f"[INFO] pkg39 cmd prefix: {pkg39_cmd}")

    run(pkg39_cmd + ["install", "numpy<2", "scipy"],
        "Installing numpy<2 + scipy into Python 3.9")

    safe_print("\n[STEP 1] Running legacy payload in Python 3.9...")
    result = subprocess.run(
        [str(py39), __file__, "--run-legacy"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        safe_print(f"[ERROR] Legacy payload failed (rc={result.returncode})")
        safe_print(f"stderr: {result.stderr}")
        raise RuntimeError("Legacy payload failed")

    json_lines = [l.strip() for l in result.stdout.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        raise RuntimeError(f"No JSON from legacy payload. stdout: {result.stdout!r}")

    legacy_data = json.loads(json_lines[-1])
    safe_print(f"[OK] Legacy result: {legacy_data}")

    # === STEP 2: Python 3.11 — modern tensorflow ===
    safe_print("\n[STEP 2] Python 3.11 — installing tensorflow...")

    pkg311_cmd = get_versioned_bin(venv_root, "3.11", "8pkg")
    py311      = get_interpreter(venv_root, "3.11")
    safe_print(f"[INFO] pkg311 cmd prefix: {pkg311_cmd}")

    run(pkg311_cmd + ["install", "tensorflow"],
        "Installing tensorflow into Python 3.11")

    safe_print("\n[STEP 2] Running modern payload in Python 3.11...")
    result = subprocess.run(
        [str(py311), __file__, "--run-modern", json.dumps(legacy_data)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        safe_print(f"[ERROR] Modern payload failed (rc={result.returncode})")
        safe_print(f"stderr: {result.stderr}")
        raise RuntimeError("Modern payload failed")

    json_lines = [l.strip() for l in result.stdout.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        raise RuntimeError(f"No JSON from modern payload. stdout: {result.stdout!r}")

    final = json.loads(json_lines[-1])
    safe_print(f"[OK] Modern prediction: {final['prediction']}")
    return final["prediction"] == "SUCCESS"


if __name__ == "__main__":
    if "--run-legacy" in sys.argv:
        run_legacy_payload()
        sys.exit(0)
    elif "--run-modern" in sys.argv:
        idx = sys.argv.index("--run-modern") + 1
        run_modern_payload(sys.argv[idx])
        sys.exit(0)

    safe_print("=" * 70)
    safe_print(" OMNIPKG MULTIVERSE ANALYSIS TEST")
    safe_print("=" * 70)
    t0 = time.perf_counter()
    success = False
    try:
        success = multiverse_analysis()
    except Exception as e:
        safe_print(f"\n[ERROR] {e}")
        traceback.print_exc()

    safe_print("\n" + "=" * 70)
    if success:
        safe_print("[SUCCESS] Context switching, installs, and healing all working!")
    else:
        safe_print("[FAILED] Check output above.")
    safe_print(f"[PERF] Total: {time.perf_counter() - t0:.2f}s")
