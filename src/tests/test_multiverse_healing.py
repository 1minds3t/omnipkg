"""
Multiverse healing test — uses direct versioned binaries (8pkg39, 8pkg311, etc.)
No pre-flight registry check needed: the shim auto-adopts the interpreter and
installs packages on first use. We only read the registry AFTER the shim has
run (adoption guaranteed complete) to get the exe path for payload subprocesses.
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


# --- HELPERS ---

def detect_venv_root() -> Path:
    """Detect the omnipkg venv root from env or config."""
    override = os.environ.get("OMNIPKG_VENV_ROOT")
    if override:
        return Path(override)
    try:
        from omnipkg.core import ConfigManager
        cm = ConfigManager(suppress_init_messages=True)
        return cm.venv_path
    except Exception:
        pass
    raise RuntimeError("Could not determine OMNIPKG venv root. Set OMNIPKG_VENV_ROOT.")


def get_versioned_bin(venv_root: Path, version: str, cmd: str) -> list:
    """
    Return a command prefix for a versioned omnipkg shim.
    The shim handles auto-adopt + install — no registry pre-check needed.

    Priority:
      1. cmd.exe --python X.Y   (cross-platform, no shell)
      2. cmd /c cmdXY.bat       (Windows .bat shim)
      3. cmdXY                  (Unix symlink/shim)
      4. cmd --python X.Y       (plain Unix binary with flag)
    """
    ver_tag = version.replace(".", "")
    is_windows = sys.platform == "win32"

    for bin_dir in [venv_root / "Scripts", venv_root / "bin"]:
        # Plain .exe with --python flag — works everywhere without shell
        exe = bin_dir / f"{cmd}.exe"
        if exe.exists():
            return [str(exe), "--python", version]

        # Windows .bat shim — must go through cmd /c
        for bat_suffix in [f"{cmd}{ver_tag}.bat", f"{cmd}{ver_tag}.cmd"]:
            bat = bin_dir / bat_suffix
            if bat.exists():
                return (["cmd", "/c", str(bat)] if is_windows else [str(bat)])

        # Unix versioned shim (symlink like 8pkg39)
        shim = bin_dir / f"{cmd}{ver_tag}"
        if shim.exists():
            return [str(shim)]

        # Plain Unix binary with --python flag
        plain = bin_dir / cmd
        if plain.exists() and not is_windows:
            return [str(plain), "--python", version]

    # Last resort: rely on PATH (CI often has 8pkg39 on PATH directly)
    shim_name = f"{cmd}{ver_tag}"
    safe_print(f"   [WARN] No shim found under {venv_root} — falling back to PATH: {shim_name}")
    return [shim_name]


def get_interpreter_after_adopt(venv_root: Path, version: str) -> Path:
    """
    Read the interpreter exe from the registry.
    Only call this AFTER the versioned shim has already run (adoption complete).
    """
    registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    if not registry_path.exists():
        raise RuntimeError(f"Registry not found at {registry_path}")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    interpreters = registry.get("interpreters", {})
    if version not in interpreters:
        raise RuntimeError(
            f"Python {version} still not in registry after adoption. "
            f"Available: {list(interpreters.keys())}"
        )
    exe = Path(interpreters[version])
    if not exe.exists():
        raise RuntimeError(f"Interpreter path does not exist: {exe}")
    return exe


# --- SUBPROCESS HELPER ---

def run(cmd, description, check=True, env=None):
    """Run a command, stream output, return full output string."""
    safe_print(f"\n>> {description}")
    safe_print(f"   cmd: {' '.join(str(c) for c in cmd)}")
    _env = os.environ.copy()
    if env:
        _env.update(env)
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
        safe_print(f"   [LAUNCH ERROR] Could not start process: {exc}")
        safe_print(f"   Attempted executable: {cmd[0]!r}")
        if check:
            raise RuntimeError(
                f"Failed to launch '{cmd[0]}': {exc}\n"
                f"  Hint: on Windows, .bat files must be wrapped with ['cmd', '/c', ...]"
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
        safe_print(f"\n   [FAILURE DETAILS] rc={rc}")
        safe_print(f"   Full cmd: {' '.join(str(c) for c in cmd)}")
        if output.strip():
            for line in output.splitlines():
                safe_print(f"   {line}")
        else:
            safe_print("   (no output captured)")
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

    # === STEP 1: Python 3.9 — legacy scipy/numpy ===
    # The shim handles adopt + install automatically — no registry pre-check.
    safe_print("\n[STEP 1] Python 3.9 — installing legacy packages via shim (auto-adopts)...")
    pkg39_cmd = get_versioned_bin(venv_root, "3.9", "8pkg")
    safe_print(f"[INFO] pkg39 cmd prefix: {pkg39_cmd}")

    run(pkg39_cmd + ["install", "numpy<2", "scipy"],
        "Installing numpy<2 + scipy into Python 3.9")

    # Now adoption is complete — safe to read registry for the exe path
    py39 = get_interpreter_after_adopt(venv_root, "3.9")
    safe_print(f"[INFO] py39 exe: {py39}")

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
    safe_print("\n[STEP 2] Python 3.11 — installing tensorflow via shim (auto-adopts)...")
    pkg311_cmd = get_versioned_bin(venv_root, "3.11", "8pkg")
    safe_print(f"[INFO] pkg311 cmd prefix: {pkg311_cmd}")

    run(pkg311_cmd + ["install", "tensorflow"],
        "Installing tensorflow into Python 3.11")

    # Adoption complete — safe to read registry
    py311 = get_interpreter_after_adopt(venv_root, "3.11")
    safe_print(f"[INFO] py311 exe: {py311}")

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