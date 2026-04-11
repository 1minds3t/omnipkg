"""
omnipkg.integration.reproducible
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reproducible environment snapshots: `8pkg export` and `8pkg sync`.

Default lock file location: <venv_root>/.omnipkg/omnipkg.lock
Both export and sync use this location automatically — no path needed.

TOML schema
-----------
[meta]
generated        = "ISO-8601 timestamp"
omnipkg_version  = "2.4.1"
native_python    = "3.11"
venv_root        = "/home/minds3t/miniforge3/envs/evocoder_env"
platform         = "linux_x86_64"
machine          = "x86_64"
env_id           = "4db29431"        # from Redis key prefix
env_kind         = "conda"           # see ENV_KINDS below
env_name         = "evocoder_env"
env_manager      = "conda"
python_origin    = "conda"
python_executable = "/path/to/python"
is_base_env      = false
is_global        = false

[python."3.11"]
source = "native"                    # use the venv's own python
executable = "/path/to/python3.11"

[python."3.11".active]
numpy = "1.26.4"
rich  = "14.3.2"

[python."3.11".bubbles]
rich  = ["14.3.1", "13.5.3"]

[python."3.10"]
source = "cpython-3.10.18"          # managed — auto-adopt on sync
executable = "/path/to/python3.10"

[python."3.10".active]
numpy = "1.26.4"

[python."3.10".bubbles]
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# tomllib: stdlib in 3.11+, fall back to tomli
try:
    import tomllib                    # type: ignore[import]
except ImportError:
    try:
        import tomli as tomllib       # type: ignore[import]
    except ImportError:
        tomllib = None

try:
    from filelock import FileLock as _FileLock, Timeout as _FileLockTimeout  # type: ignore[import]
    _FILELOCK_AVAILABLE = True
except ImportError:
    _FILELOCK_AVAILABLE = False
    _FileLockTimeout = TimeoutError  # type: ignore[assignment,misc]

try:
    import tomli_w                    # type: ignore[import]
except ImportError:
    tomli_w = None


# ── cross-process registry lock (used by _update_global_lock_registry) ────────
#
# Tier 1: filelock.FileLock  — best cross-platform, handles edge cases
# Tier 2: fcntl.flock        — POSIX stdlib (Linux/macOS, Python 3.3+)
# Tier 3: msvcrt.locking     — Windows stdlib
# Tier 4: warn + unprotected — absolute last resort (CI single-process is fine)
#
# NOTE: omnipkg_atomic (CAS/store/load) is an intra-process primitive that
# operates on raw memory addresses — it cannot protect against two *separate*
# processes racing on the same file, so it is intentionally not used here.

import contextlib as _contextlib

@_contextlib.contextmanager
def _registry_lock(lock_file_path: Path, timeout: float = 10.0):
    """
    Cross-process mutex over a sentinel file.
    Falls through tiers until something works; warns if fully unprotected.
    """
    # Tier 1: filelock
    if _FILELOCK_AVAILABLE:
        try:
            with _FileLock(str(lock_file_path), timeout=timeout):
                yield
            return
        except _FileLockTimeout:
            raise TimeoutError(
                f"Could not acquire omnipkg registry lock within {timeout:.0f}s.\n"
                f"  Lock file: {lock_file_path}\n"
                f"  Another '8pkg export' may be running. Delete the lock file if stuck."
            )

    # Tier 2: fcntl (POSIX — Linux/macOS, Python 3.3+)
    try:
        import fcntl as _fcntl
        import time as _time
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_file_path, "a+")
        try:
            deadline = _time.monotonic() + timeout
            while True:
                try:
                    _fcntl.flock(fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if _time.monotonic() > deadline:
                        fh.close()
                        raise TimeoutError(
                            f"Could not acquire omnipkg registry lock within {timeout:.0f}s.\n"
                            f"  Lock file: {lock_file_path}\n"
                            f"  Another '8pkg export' may be running."
                        )
                    _time.sleep(0.05)
            try:
                yield
            finally:
                _fcntl.flock(fh, _fcntl.LOCK_UN)
        finally:
            fh.close()
        return
    except ImportError:
        pass  # not POSIX — fall through to Windows

    # Tier 3: msvcrt (Windows, Python 3.3+)
    try:
        import msvcrt as _msvcrt
        import time as _time
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_file_path, "a+b")
        try:
            deadline = _time.monotonic() + timeout
            while True:
                try:
                    fh.seek(0)
                    _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if _time.monotonic() > deadline:
                        fh.close()
                        raise TimeoutError(
                            f"Could not acquire omnipkg registry lock within {timeout:.0f}s.\n"
                            f"  Lock file: {lock_file_path}\n"
                            f"  Another '8pkg export' may be running."
                        )
                    _time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    fh.seek(0)
                    _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            fh.close()
        return
    except ImportError:
        pass  # not Windows either — absolute last resort below

    # Tier 4: fully unprotected — warn once
    import warnings as _warnings
    _warnings.warn(
        "omnipkg: no cross-process lock available for registry updates "
        "(filelock not installed, fcntl/msvcrt unavailable). "
        "Parallel '8pkg export' calls may race. Install filelock to fix.",
        RuntimeWarning,
        stacklevel=4,
    )
    yield


# ── environment kind taxonomy ─────────────────────────────────────────────────

ENV_KINDS = {
    "conda",        # conda managed env (non-base)
    "conda-base",   # conda base env — dangerous to touch
    "venv",         # stdlib venv
    "virtualenv",   # virtualenv (non-stdlib)
    "uv",           # uv-managed venv
    "pyenv",        # pyenv virtualenv
    "system",       # system python — extremely dangerous
    "docker",       # inside a container
    "omnipkg",      # future: omnipkg-native env
    "unknown",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _venv_root() -> Path:
    """
    Resolve the venv root using the same priority chain as dispatcher.c.
    Raises RuntimeError if nothing can be found.
    """
    # 1. Explicit override
    env = os.environ.get("OMNIPKG_VENV_ROOT")
    if env and Path(env).exists():
        return Path(env)

    # 2. Conda env
    conda = os.environ.get("CONDA_PREFIX")
    if conda and Path(conda).exists():
        return Path(conda)

    # 3. Standard venv
    venv = os.environ.get("VIRTUAL_ENV")
    if venv and Path(venv).exists():
        return Path(venv)

    # 4. Walk up from sys.prefix — covers edge cases where env vars aren't set
    prefix = Path(sys.prefix)
    if (prefix / ".omnipkg").exists():
        return prefix

    raise RuntimeError(
        "Cannot determine venv root.\n"
        "Activate a conda/venv environment first, or set OMNIPKG_VENV_ROOT."
    )


def _default_lock_path(venv_root: Path) -> Path:
    """Canonical lock file: <venv_root>/.omnipkg/omnipkg.lock"""
    return venv_root / ".omnipkg" / "omnipkg.lock"


def _load_registry(venv_root: Path) -> dict:
    reg_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
    if not reg_path.exists():
        raise FileNotFoundError(
            f"Registry not found: {reg_path}\n"
            f"Is this an omnipkg-managed environment?"
        )
    return json.loads(reg_path.read_text())


def _load_interp_config(interp_bin_dir: Path) -> dict:
    cfg = interp_bin_dir / ".omnipkg_config.json"
    if not cfg.exists():
        return {}
    try:
        return json.loads(cfg.read_text())
    except Exception:
        return {}


def _site_packages_for(python_path: Path) -> Path | None:
    """Ask the interpreter where its site-packages is."""
    try:
        result = subprocess.run(
            [str(python_path), "-c",
             "import site; print(site.getsitepackages()[0])"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _detect_env_identity(venv_root: Path, native_cfg: dict) -> dict:
    """
    Classify the current environment and collect identity metadata.
    Returns a dict of fields to merge into [meta].
    """
    identity: dict[str, Any] = {
        "env_id": "unknown",
        "env_kind": "unknown",
        "env_name": "unknown",
        "env_manager": "unknown",
        "python_origin": "unknown",
        "python_executable": native_cfg.get("python_executable", sys.executable),
        "is_base_env": False,
        "is_global": False,
    }

    # ── env_kind detection ────────────────────────────────────────────────────
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    conda_default = os.environ.get("CONDA_DEFAULT_ENV", "")
    virtual_env = os.environ.get("VIRTUAL_ENV", "")
    in_docker = Path("/.dockerenv").exists() or os.environ.get("container") == "docker"

    if in_docker:
        identity["env_kind"] = "docker"
        identity["env_manager"] = "docker"
    elif conda_prefix:
        # Check if this is base conda env
        conda_root = os.environ.get("CONDA_ROOT", "") or os.environ.get("CONDA_EXE", "")
        if conda_root:
            conda_root_dir = Path(conda_root).parent.parent  # CONDA_EXE is in bin/
            is_base = Path(conda_prefix) == conda_root_dir
        else:
            # Heuristic: base env name is typically "base"
            is_base = conda_default in ("base", "")
        identity["env_kind"] = "conda-base" if is_base else "conda"
        identity["env_manager"] = "conda"
        identity["is_base_env"] = is_base
        identity["env_name"] = conda_default or Path(conda_prefix).name
        identity["python_origin"] = "conda"
    elif virtual_env:
        # Distinguish venv vs virtualenv by presence of pyvenv.cfg
        pyvenv = Path(virtual_env) / "pyvenv.cfg"
        if pyvenv.exists():
            cfg_text = pyvenv.read_text()
            if "uv" in cfg_text.lower():
                identity["env_kind"] = "uv"
                identity["env_manager"] = "uv"
            else:
                identity["env_kind"] = "venv"
                identity["env_manager"] = "python"
        else:
            identity["env_kind"] = "virtualenv"
            identity["env_manager"] = "virtualenv"
        identity["env_name"] = Path(virtual_env).name
        identity["python_origin"] = "venv"
    elif str(venv_root) in ("/usr", "/usr/local", str(Path.home())):
        identity["env_kind"] = "system"
        identity["env_manager"] = "system"
        identity["is_global"] = True
        identity["python_origin"] = "system"
    else:
        identity["env_kind"] = "unknown"

    # ── env_id from Redis key prefix ──────────────────────────────────────────
    try:
        redis_cfg_path = venv_root / "bin" / ".omnipkg_config.json"
        if redis_cfg_path.exists():
            rcfg = json.loads(redis_cfg_path.read_text())
            if rcfg.get("redis_enabled"):
                import redis as _redis
                r = _redis.Redis(
                    host=rcfg.get("redis_host", "localhost"),
                    port=int(rcfg.get("redis_port", 6379)),
                    decode_responses=True,
                    socket_connect_timeout=1,
                )
                sample = r.keys("omnipkg:env_*:py*:inst:*")
                if sample:
                    identity["env_id"] = sample[0].split(":")[1]  # "env_4db29431"
    except Exception:
        pass

    # Fallback env_id: hash of venv_root path
    if identity["env_id"] == "unknown":
        import hashlib
        identity["env_id"] = "env_" + hashlib.md5(
            str(venv_root).encode()
        ).hexdigest()[:8]

    return identity


def _scan_active_from_fs(site_packages: Path) -> dict[str, str]:
    """Scan *.dist-info dirs for active (non-bubbled) packages."""
    active: dict[str, str] = {}
    for di in site_packages.glob("*.dist-info"):
        if ".omnipkg_versions" in str(di):
            continue
        meta_file = di / "METADATA"
        if not meta_file.exists():
            meta_file = di / "PKG-INFO"
        if not meta_file.exists():
            continue
        name = version = None
        try:
            for line in meta_file.read_text(errors="replace").splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
        except Exception:
            continue
        if name and version:
            active[re.sub(r"[-_.]+", "-", name).lower()] = version
    return active


def _scan_bubbles_from_fs(multiversion_base: Path) -> dict[str, list[str]]:
    """
    Scan .omnipkg_versions/ for bubbled package versions.
    Each top-level subdirectory is named <pkg>-<version> and represents ONE bubble.
    Deps installed inside that directory are vendored — NOT separate bubbles.
    """
    bubbles: dict[str, list[str]] = {}
    if not multiversion_base.exists():
        return bubbles
    for entry in multiversion_base.iterdir():
        if not entry.is_dir():
            continue
        # Skip hidden dirs like .cache, .locks
        if entry.name.startswith("."):
            continue
        # The directory name IS the bubble identity — never scan inside
        # Format: <package_name>-<version>  e.g. rich-14.3.3, uv-0.10.0
        parts = entry.name.rsplit("-", 1)
        if len(parts) == 2:
            pkg = re.sub(r"[-_.]+", "-", parts[0]).lower()
            ver = parts[1]
            # Sanity check: version must start with a digit
            if ver and ver[0].isdigit():
                bubbles.setdefault(pkg, []).append(ver)
    return bubbles


def _get_env_id_for_venv(venv_root: Path) -> str:
    """Compute env_id the same way core.py does — canonical resolved path hash."""
    import hashlib
    import os
    p = venv_root.resolve()
    s = str(p).replace("\\", "/").rstrip("/")
    if os.name == "nt":
        s = s.lower()
    return hashlib.md5(s.encode()).hexdigest()[:8]


def _query_cache(cache_client, env_id: str, py_ver: str) -> tuple[dict, dict]:
    """Query a cache client (Redis or SQLite) for active+bubble data."""
    active: dict[str, str] = {}
    bubbles: dict[str, list[str]] = {}
    keys = cache_client.keys(f"omnipkg:{env_id}:py{py_ver}:inst:*")
    for key in keys:
        data = cache_client.hgetall(key)
        if not data:
            continue
        pkg_name = re.sub(r"[-_.]+", "-", data.get("Name", "")).lower()
        ver = data.get("Version", "") or data.get("version", "")
        if not pkg_name or not ver:
            continue
        install_type = data.get("install_type", "bubble").lower()
        if install_type == "active":
            active[pkg_name] = ver
        else:
            bubbles.setdefault(pkg_name, []).append(ver)
    return active, bubbles


def _try_redis_for_python(
    venv_root: Path, py_ver: str
) -> tuple[dict[str, str] | None, dict[str, list[str]] | None]:
    """
    Try Redis → SQLite KB for active + bubble data for one python version.
    Returns (active, bubbles) or (None, None) to signal filesystem fallback.
    """
    # Find per-interpreter config
    interp_cfg_path = venv_root / ".omnipkg" / "interpreters" / f"cpython-{py_ver}"
    # Try native config first, then walk interpreters for a matching version
    native_cfg_path = venv_root / "bin" / ".omnipkg_config.json"
    cfg = {}
    if native_cfg_path.exists():
        try:
            cfg = json.loads(native_cfg_path.read_text())
        except Exception:
            pass

    # Also try per-interpreter config which may have redis_enabled=false
    try:
        registry_path = venv_root / ".omnipkg" / "interpreters" / "registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text())
            py_exe = registry.get("interpreters", {}).get(py_ver, "")
            if py_exe:
                interp_bin = Path(py_exe).parent
                interp_cfg = interp_bin / ".omnipkg_config.json"
                if interp_cfg.exists():
                    cfg = json.loads(interp_cfg.read_text())
    except Exception:
        pass

    env_id = _get_env_id_for_venv(venv_root)

    # --- Tier 1: Redis ---
    if cfg.get("redis_enabled", False):
        try:
            import redis as _redis  # type: ignore[import]
            r = _redis.Redis(
                host=cfg.get("redis_host", "localhost"),
                port=int(cfg.get("redis_port", 6379)),
                decode_responses=True,
                socket_connect_timeout=1,
            )
            r.ping()
            active, bubbles = _query_cache(r, env_id, py_ver)
            if active or bubbles:
                print(f"  [{py_ver}] source: Redis KB", file=sys.stderr)
                return active, bubbles
        except Exception:
            pass

    # --- Tier 2: SQLite ---
    try:
        from ..cache import SQLiteCacheClient
        config_dir = Path.home() / ".config" / "omnipkg"
        sqlite_path = config_dir / f"cache_{env_id}-py{py_ver}.sqlite"
        if sqlite_path.exists():
            client = SQLiteCacheClient(db_path=sqlite_path)
            active, bubbles = _query_cache(client, env_id, py_ver)
            if active or bubbles:
                print(f"  [{py_ver}] source: SQLite KB", file=sys.stderr)
                return active, bubbles
    except Exception:
        pass

    # --- Tier 3: signal filesystem fallback ---
    return None, None


def _omnipkg_version() -> str:
    try:
        return importlib.metadata.version("omnipkg")
    except Exception:
        return "unknown"


def _detect_host_profile() -> dict:
    """
    Detect host system properties for realization context.
    All fields are informational only — most are NOT included in content_sha.
    Exception: libc_family IS included in content_sha (hard binary compat wall).

    Returns dict with:
      linux_distro_id       e.g. "ubuntu", "alpine", "debian", "fedora"
      linux_distro_version  e.g. "24.04", "3.19"
      linux_id_like         e.g. "debian", "rhel fedora"
      libc_family           "glibc" | "musl" | "unknown"
      libc_version          e.g. "2.39" | "unknown"
      python_impl           "cpython" | "pypy" | "graalpy" | "unknown"
    """
    profile: dict[str, Any] = {
        "linux_distro_id": "unknown",
        "linux_distro_version": "unknown",
        "linux_id_like": "unknown",
        "libc_family": "unknown",
        "libc_version": "unknown",
        "python_impl": "unknown",
    }

    # ── python implementation ─────────────────────────────────────────────────
    impl = platform.python_implementation().lower()
    if impl in ("cpython", "pypy", "graalpy"):
        profile["python_impl"] = impl
    else:
        profile["python_impl"] = impl or "unknown"

    # ── linux only ────────────────────────────────────────────────────────────
    if platform.system().lower() != "linux":
        return profile

    # ── distro detection (/etc/os-release is the standard) ───────────────────
    os_release: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        os_release[k.strip()] = v.strip().strip('"')
            break
        except OSError:
            continue

    profile["linux_distro_id"] = os_release.get("ID", "unknown").lower()
    profile["linux_distro_version"] = os_release.get("VERSION_ID", "unknown")
    profile["linux_id_like"] = os_release.get("ID_LIKE", "unknown").lower()

    # ── libc detection ────────────────────────────────────────────────────────
    # Strategy 1: ldd --version output (glibc prints "ldd (GNU libc) X.Y")
    # Strategy 2: check for musl via ldd symlink or /lib/libc.musl-*
    # Strategy 3: ctypes.CDLL as last resort
    libc_family = "unknown"
    libc_version = "unknown"

    try:
        r = subprocess.run(
            ["ldd", "--version"],
            capture_output=True, text=True, timeout=5
        )
        output = (r.stdout + r.stderr).lower()
        if "gnu libc" in output or "glibc" in output or "free software foundation" in output:
            libc_family = "glibc"
            # Parse version from first line e.g. "ldd (GNU libc) 2.39"
            for line in output.splitlines():
                m = re.search(r"(\d+\.\d+)", line)
                if m:
                    libc_version = m.group(1)
                    break
        elif "musl" in output:
            libc_family = "musl"
            for line in output.splitlines():
                m = re.search(r"(\d+\.\d+\.\d+|\d+\.\d+)", line)
                if m:
                    libc_version = m.group(1)
                    break
    except Exception:
        pass

    # Strategy 2: musl ldd is often a symlink, or musl libs exist on fs
    if libc_family == "unknown":
        try:
            ldd_path = subprocess.run(
                ["which", "ldd"], capture_output=True, text=True, timeout=3
            ).stdout.strip()
            if ldd_path:
                real = os.path.realpath(ldd_path)
                if "musl" in real.lower():
                    libc_family = "musl"
        except Exception:
            pass

    if libc_family == "unknown":
        # Check for musl lib files directly
        import glob
        if glob.glob("/lib/libc.musl-*") or glob.glob("/usr/lib/libc.musl-*"):
            libc_family = "musl"

    # Strategy 3: ctypes for glibc version
    if libc_family == "unknown":
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc_family = "glibc"
            # gnu_get_libc_version() returns a string like "2.39"
            gnu_get_libc_version = libc.gnu_get_libc_version
            gnu_get_libc_version.restype = ctypes.c_char_p
            libc_version = gnu_get_libc_version().decode()
        except Exception:
            pass

    profile["libc_family"] = libc_family
    profile["libc_version"] = libc_version

    return profile


def _get_python_full_version(py_path: Path) -> str:
    """
    Ask the interpreter for its full patch version string e.g. "3.11.15".
    Falls back to major.minor only if the subprocess fails.
    """
    try:
        r = subprocess.run(
            [str(py_path), "-c",
             "import sys; print(f'{sys.version_info.major}."
             "{sys.version_info.minor}.{sys.version_info.micro}')"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ── lock discovery ────────────────────────────────────────────────────────────

def _quick_lock_summary(lock_path: Path) -> dict:
    """Extract meta fields for the picker without a full parse."""
    if tomllib is None:
        return {}
    try:
        with open(lock_path, "rb") as f:
            data = tomllib.load(f)
        meta = data.get("meta", {})
        pythons = data.get("python", {})
        n_packages = sum(len(pdata.get("active", {})) for pdata in pythons.values())
        return {
            "env_name": meta.get("env_name", "?"),
            "native_python": meta.get("native_python", "?"),
            "n_pythons": len(pythons),
            "n_packages": n_packages,
        }
    except Exception:
        return {}


def _discover_lock_files() -> list[dict]:
    """
    Scan system-wide for omnipkg.lock files across:
      - All conda envs (via conda env list --json)
      - Common virtualenv locations (~/.venvs, ~/.virtualenvs, ~/venvs, ~/envs)
      - ~/.omnipkg/ for named user-level lock files

    Returns list of dicts sorted by mtime descending (most recent first).
    Each dict: {path, mtime, mtime_str, env_name, native_python, n_pythons, n_packages}
    """
    candidates: list[Path] = []

    # 1. Conda envs
    try:
        result = subprocess.run(
            ["conda", "env", "list", "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for env_path in data.get("envs", []):
                lock = Path(env_path) / ".omnipkg" / "omnipkg.lock"
                if lock.exists():
                    candidates.append(lock)
    except Exception:
        pass

    # 2. Common venv locations
    for base in [
        Path.home() / ".venvs",
        Path.home() / ".virtualenvs",
        Path.home() / "venvs",
        Path.home() / "envs",
    ]:
        if base.is_dir():
            for env_dir in base.iterdir():
                lock = env_dir / ".omnipkg" / "omnipkg.lock"
                if lock.exists():
                    candidates.append(lock)

    # 3. Machine-global canonical lock store
    if _GLOBAL_STATES_DIR.is_dir():
        for lock in _GLOBAL_STATES_DIR.glob("*.toml"):
            candidates.append(lock)

    global_lock_dir = Path.home() / ".omnipkg"
    if global_lock_dir.is_dir():
        for lock in global_lock_dir.glob("*.lock"):
            candidates.append(lock)

    try:
        global_registry = _load_global_registry()
        registry_states = global_registry.get("states", {})
    except Exception:
        registry_states = {}
    _canonical_to_envs: dict[str, list[str]] = {}
    for sha_key, state in registry_states.items():
        canon = state.get("canonical_lock_path", "")
        refs = state.get("env_refs", [])
        if canon and refs:
            names = [r.get("env_name", "") for r in refs if r.get("env_name")]
            if names:
                _canonical_to_envs[canon] = names

    # Deduplicate and build result list
    seen: set[Path] = set()
    results: list[dict] = []
    for lock in candidates:
        resolved = lock.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            stat = resolved.stat()
        except OSError:
            continue
        summary = _quick_lock_summary(resolved)
        env_name = summary.get("env_name", "")
        if env_name == "<env_name>" or not env_name:
            known_envs = _canonical_to_envs.get(str(resolved), [])
            env_name = "used by: " + ", ".join(known_envs) if known_envs else resolved.parent.parent.name
        results.append({
            "path": resolved,
            "mtime": stat.st_mtime,
            "mtime_str": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "env_name": env_name,
            "native_python": summary.get("native_python", "?"),
            "n_pythons": summary.get("n_pythons", 0),
            "n_packages": summary.get("n_packages", 0),
        })

    # Group by env_name, sort by mtime descending within each group,
    # then sort groups by their most recent mtime descending
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        grouped[r["env_name"]].append(r)
    for entries in grouped.values():
        entries.sort(key=lambda x: x["mtime"], reverse=True)
    sorted_groups = sorted(
        grouped.values(),
        key=lambda entries: entries[0]["mtime"],
        reverse=True,
    )
    final: list[dict] = []
    for entries in sorted_groups:
        final.extend(entries)
    return final


def _print_lock_list(found: list[dict]) -> None:
    """Print discovered lock files grouped by env with index numbers."""
    current_env = None
    for i, entry in enumerate(found, 1):
        env = entry["env_name"]
        if env != current_env:
            if current_env is not None:
                print()
            print(f"  ── {env} ──")
            current_env = env
        marker = "  ◀ most recent" if i == 1 else ""
        print(
            f"    {i}) {entry['path']}\n"
            f"       python: {entry['native_python']}  "
            f"{entry['n_pythons']} interpreter(s)  "
            f"{entry['n_packages']} active packages  "
            f"modified: {entry['mtime_str']}{marker}"
        )


def _manage_lock_files(found: list[dict], safe_input_fn) -> None:
    """
    Interactive lock file manager — lets user mark and delete old lock files.
    """
    if not found:
        print("  No lock files found.")
        return

    while True:
        print("\n🗂️  Manage lock files\n")
        _print_lock_list(found)
        print()
        print("  Enter number(s) to delete (comma-separated), or [q] to go back: ", end="")
        try:
            raw = safe_input_fn("", default="q", auto_value="q").strip().lower()
        except KeyboardInterrupt:
            print()
            return

        if raw in ("q", ""):
            return

        # Parse selection
        to_delete: list[dict] = []
        for part in raw.split(","):
            part = part.strip()
            try:
                idx = int(part) - 1
                if 0 <= idx < len(found):
                    to_delete.append(found[idx])
                else:
                    print(f"  ⚠️  {part} is out of range, skipping.")
            except ValueError:
                print(f"  ⚠️  '{part}' is not a valid number, skipping.")

        if not to_delete:
            print("  Nothing selected.")
            continue

        print("\n  About to delete:")
        for entry in to_delete:
            print(f"    • {entry['path']}  ({entry['env_name']}, {entry['mtime_str']})")
        print()
        try:
            confirm = safe_input_fn(
                "  Confirm delete? (y/N): ", default="n", auto_value="n"
            ).strip().lower()
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            continue

        if confirm != "y":
            print("  Cancelled.")
            continue

        for entry in to_delete:
            try:
                entry["path"].unlink()
                print(f"  🗑️  Deleted: {entry['path']}")
                found.remove(entry)
            except Exception as e:
                print(f"  ⚠️  Could not delete {entry['path']}: {e}")

        if not found:
            print("\n  No lock files remaining.")
            return


def _interactive_lock_picker(
    found: list[dict],
    safe_input_fn,
    allow_manage: bool = True,
) -> Path | None:
    """
    Show the lock file picker. Returns chosen Path or None if user aborted.
    If allow_manage=True, user can enter 'm' to manage/delete lock files.
    """
    while True:
        print("\n📋 Found lock files:\n")
        _print_lock_list(found)
        print()
        hint = "  Enter number [1], [m] to manage/delete, or [q] to quit: " \
            if allow_manage else "  Enter number [1] or [q] to quit: "
        try:
            raw = safe_input_fn(hint, default="1", auto_value="1").strip().lower()
        except KeyboardInterrupt:
            print("\nAborted.")
            return None

        if raw in ("q", "quit"):
            return None

        if raw == "m" and allow_manage:
            _manage_lock_files(found, safe_input_fn)
            if not found:
                print("  No lock files remaining.")
                return None
            continue

        target = raw or "1"
        try:
            idx = int(target) - 1
            if 0 <= idx < len(found):
                return found[idx]["path"]
        except ValueError:
            pass

        print("  ⚠️  Invalid choice, try again.")


def _pick_lock_file(
    lock_path: Path | None,
    venv_root: Path,
    force_pick: bool = False,
) -> Path:
    """
    Resolve which lock file to use for sync.

    Priority:
      1. Explicit path passed by user/script → use it directly, no discovery.
      2. force_pick=True → always show interactive picker.
      3. Default location exists (<venv_root>/.omnipkg/omnipkg.lock) → use it,
         but inform user if other lock files exist.
      4. Discover all lock files system-wide:
           - Non-interactive (CI): auto-select most recent.
           - Interactive: show picker.
    """
    try:
        from omnipkg.common_utils import is_interactive_session, safe_input
        print(f"[REPRO-DEBUG] using common_utils safe_input", file=sys.stderr)
    except ImportError:
        print(f"[REPRO-DEBUG] FALLBACK safe_input", file=sys.stderr)
        def is_interactive_session():
            return sys.stdin.isatty() and not os.environ.get("CI")
        def safe_input(prompt, default="", auto_value=None):
            if is_interactive_session():
                try:
                    return input(prompt).strip()
                except EOFError:
                    return default
                except KeyboardInterrupt:
                    raise
            result = auto_value if auto_value is not None else default
            print(f"🤖 Auto-selecting: {result}")
            return result

    print(
        f"[REPRO-DEBUG] DAEMON_WORKER={os.environ.get('OMNIPKG_DAEMON_WORKER')} "
        f"ISATTY={os.environ.get('_OMNIPKG_ISATTY')} "
        f"interactive={is_interactive_session()} "
        f"stdin.isatty={sys.stdin.isatty()}",
        file=sys.stderr,
    )

    # Case 1: explicit path
    if lock_path is not None:
        p = Path(lock_path)
        if not p.exists():
            raise FileNotFoundError(
                f"Lock file not found: {p}\n"
                f"Run '8pkg export' first to create it."
            )
        return p

    found = _discover_lock_files()

    # Case 2: force picker
    if force_pick:
        if not found:
            raise FileNotFoundError(
                "No omnipkg.lock found anywhere on this system.\n"
                "Run '8pkg export' first to create one."
            )
        chosen = _interactive_lock_picker(found, safe_input)
        if chosen is None:
            print("Aborted.")
            sys.exit(0)
        return chosen

    # Case 3: default location exists
    default = _default_lock_path(venv_root)
    if default.exists():
        if found and len(found) > 1:
            print(
                f"\n💡 Using default lock file: {default}\n"
                f"   (Found {len(found)} lock files on this system — "
                f"run '8pkg sync --pick' to choose a different one.)"
            )
        return default

    # Case 4: no default — discover + pick
    if not found:
        raise FileNotFoundError(
            "No omnipkg.lock found anywhere on this system.\n"
            "Run '8pkg export' first to create one."
        )

    if not is_interactive_session():
        chosen = found[0]
        print(f"🤖 Auto-selecting most recent lock file: {chosen['path']}")
        return chosen["path"]

    chosen = _interactive_lock_picker(found, safe_input)
    if chosen is None:
        print("Aborted.")
        sys.exit(0)
    return chosen




# ── python version mismatch check ─────────────────────────────────────────────

def _check_python_version_mismatch(
    lock_native: str,
    current_native: str,
    yes: bool,
) -> bool:
    """
    Compare the lock file's native python version against the current env's.
    Returns True if sync should proceed, False if it should abort.

    Behaviour:
      - Match: silent pass-through.
      - Mismatch + interactive: warn and ask for confirmation.
      - Mismatch + CI (non-interactive):
          * OMNIPKG_SYNC_PYTHON_MISMATCH=allow → proceed with warning.
          * Otherwise → hard fail (exit 1). Reproducibility is the point.
    """
    if lock_native == current_native:
        return True

    try:
        from omnipkg.common_utils import is_interactive_session, safe_input
    except ImportError:
        def is_interactive_session():
            return sys.stdin.isatty() and not os.environ.get("CI")
        def safe_input(prompt, default="", auto_value=None):
            if is_interactive_session():
                try:
                    return input(prompt).strip()
                except EOFError:
                    return default
                except KeyboardInterrupt:
                    raise
            result = auto_value if auto_value is not None else default
            print(f"🤖 Auto-selecting: {result}")
            return result

    print(
        f"\n⚠️  Python version mismatch\n"
        f"   Lock file native python : {lock_native}\n"
        f"   Current env native python: {current_native}\n"
        f"\n"
        f"   Sync will still work — {lock_native} will be adopted as a managed\n"
        f"   interpreter and its packages installed there. However, for a\n"
        f"   byte-for-byte identical environment you should create a new conda/venv\n"
        f"   env with Python {lock_native} and sync into that instead.\n"
        f"\n"
        f"   Example:\n"
        f"     conda create -n myenv python={lock_native} -y\n"
        f"     conda activate myenv\n"
        f"     8pkg sync  # will find the lock file automatically\n",
        file=sys.stderr,
    )

    if yes:
        print("   --yes passed, proceeding despite mismatch.", file=sys.stderr)
        return True

    if not is_interactive_session():
        # CI path
        override = os.environ.get("OMNIPKG_SYNC_PYTHON_MISMATCH", "").lower()
        if override == "allow":
            print(
                "   OMNIPKG_SYNC_PYTHON_MISMATCH=allow — proceeding.",
                file=sys.stderr,
            )
            return True
        print(
            "   ❌ Aborting: native python mismatch in non-interactive mode.\n"
            "   Set OMNIPKG_SYNC_PYTHON_MISMATCH=allow to override, or use --yes.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Interactive confirmation
    try:
        ans = safe_input(
            "   Proceed anyway? (y/N): ",
            default="n",
            auto_value="n",
        ).strip().lower()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(0)
    if ans == "y":
        return True

    print("Aborted.")
    return False


# ── machine-global lock registry ─────────────────────────────────────────────
# Layout:
#   ~/.config/omnipkg/locks/
#       registry.json          — private index: sha → env_refs (never shared)
#       states/
#           sha256_abc123....toml  — canonical portable lock (shareable)

_GLOBAL_LOCKS_DIR = Path.home() / ".config" / "omnipkg" / "locks"
_GLOBAL_STATES_DIR = _GLOBAL_LOCKS_DIR / "states"
_GLOBAL_REGISTRY_PATH = _GLOBAL_LOCKS_DIR / "registry.json"


def _global_state_path(sha: str) -> Path:
    """Canonical path for a given content SHA."""
    safe_sha = f"sha256_{sha}"
    return _GLOBAL_STATES_DIR / f"{safe_sha}.toml"


def _load_global_registry() -> dict:
    if not _GLOBAL_REGISTRY_PATH.exists():
        return {"version": 1, "states": {}}
    try:
        return json.loads(_GLOBAL_REGISTRY_PATH.read_text())
    except Exception:
        return {"version": 1, "states": {}}


def _save_global_registry(reg: dict) -> None:
    _GLOBAL_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write
    import tempfile
    tmp = _GLOBAL_REGISTRY_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(reg, indent=2))
        tmp.replace(_GLOBAL_REGISTRY_PATH)
    except Exception as e:
        print(f"⚠️  Could not save global lock registry: {e}", file=sys.stderr)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _update_global_lock_registry(
    lock: dict,
    sha: str,
    venv_root: Path,
    env_identity: dict,
    native_ver: str,
    silent: bool = False,
) -> Path | None:
    """
    Update the machine-global lock registry with this export.

    - If this SHA is new: write canonical lock to states/, register it.
    - If SHA already known: just add/update this env's ref.
    - Either way: inform the user if this build is shared across envs.

    The read-modify-write is protected by a filelock so parallel `8pkg export`
    calls (e.g. from a Makefile) cannot clobber each other.

    Args:
        silent: If True, suppress all user-facing print output (used when
                the caller already knows the env is unchanged and handles
                the messaging itself).

    Returns the canonical global state path (may not exist yet if brand new).
    """
    _GLOBAL_STATES_DIR.mkdir(parents=True, exist_ok=True)
    state_path = _global_state_path(sha)
    sha_key = f"sha256:{sha}"
    now = datetime.now(timezone.utc).isoformat()

    env_ref = {
        "env_root": str(venv_root),
        "env_kind": env_identity.get("env_kind", "unknown"),
        "env_name": env_identity.get("env_name", "unknown"),
        "native_python": native_ver,
        "last_exported": now,
    }

    _GLOBAL_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_file_path = _GLOBAL_LOCKS_DIR / "registry.lock"

    def _do_update() -> None:
        reg = _load_global_registry()

        if sha_key not in reg["states"]:
            # Brand new build state
            reg["states"][sha_key] = {
                "canonical_lock_path": str(state_path),
                "first_seen": now,
                "created_at": now,
                "last_seen": now,
                "env_refs": [env_ref],
            }
            if not silent:
                print(
                    f"📝 Exporting environment state (sha256:{sha[:16]}...)\n"
                    f"🔒 New build state registered\n"
                    f"   Canonical: {state_path}\n"
                    f"   Local:     {_default_lock_path(venv_root)}"
                )
        else:
            entry = reg["states"][sha_key]
            entry["last_seen"] = now

            # Update or add this env's ref
            existing_roots = [r["env_root"] for r in entry["env_refs"]]
            if str(venv_root) in existing_roots:
                for ref in entry["env_refs"]:
                    if ref["env_root"] == str(venv_root):
                        ref["last_exported"] = now
            else:
                entry["env_refs"].append(env_ref)

            if not silent:
                n_envs = len(entry["env_refs"])
                if n_envs > 1:
                    env_names = [r.get("env_name", "?") for r in entry["env_refs"]]
                    print(
                        f"♻️  Build state already known (sha256:{sha[:16]}...)\n"
                        f"   This build state is used by {n_envs} envs on this machine:\n"
                        + "\n".join(f"   • {n}" for n in env_names)
                    )
                else:
                    print(
                        f"♻️  Build state already known (sha256:{sha[:16]}...) — reusing canonical lock."
                    )

        _save_global_registry(reg)

    with _registry_lock(lock_file_path, timeout=10.0):
        _do_update()

    # Write canonical state file if missing
    if not state_path.exists():
        portable = _scrub_lock(lock, venv_root)
        portable["meta"]["content_sha"] = sha
        state_path.write_text(_dict_to_toml(portable))

    return state_path


def get_global_registry_summary() -> list[dict]:
    """
    Return a list of known build states for display/CLI use.
    Each entry: {sha, n_envs, env_names, native_python, created_at, last_seen}
    """
    reg = _load_global_registry()
    results = []
    for sha_key, entry in reg.get("states", {}).items():
        refs = entry.get("env_refs", [])
        results.append({
            "sha": sha_key,
            "sha_short": sha_key.split(":")[-1][:16],
            "n_envs": len(refs),
            "env_names": [r.get("env_name", "?") for r in refs],
            "native_python": refs[0].get("native_python", "?") if refs else "?",
            "created_at": entry.get("created_at", ""),
            "last_seen": entry.get("last_seen", ""),
            "canonical_lock_path": entry.get("canonical_lock_path", ""),
        })
    results.sort(key=lambda x: x["last_seen"], reverse=True)
    return results


# ── privacy scrubbing + content hashing ──────────────────────────────────────

def _scrub_lock(lock: dict, venv_root: Path) -> dict:
    """
    Return a minimal dict containing ONLY the fields that contribute to the
    content SHA. Everything else is dropped entirely — not replaced with
    placeholders, just absent.

    Used for:
      - SHA computation
      - Canonical state file written to global registry (safe to share/upload)

    SHA-relevant fields kept (content identity):
      meta:
        native_python, platform, machine, libc_family, content_sha_format
      per-python:
        executable (normalized to "<pythonX.Y>")
        python_full_version
        active packages + versions
        bubble versions

    Everything else (paths, env name, distro details, omnipkg version,
    timestamps, python_origin, is_base_env, is_global, source, python_impl)
    is dropped — it lives only in the local lock file.
    """
    import copy

    # ── meta: keep ONLY the fields that define content identity ──────────────
    _META_SHA_KEYS = {"native_python", "platform", "machine", "libc_family"}
    scrubbed_meta = {k: v for k, v in lock.get("meta", {}).items() if k in _META_SHA_KEYS}

    # ── python entries: keep only reproducibility fields ─────────────────────
    scrubbed_python: dict[str, Any] = {}
    for ver, pdata in lock.get("python", {}).items():
        scrubbed_python[ver] = {
            "executable": f"<python{ver}>",
            "python_full_version": pdata.get("python_full_version", "unknown"),
            "active": copy.deepcopy(pdata.get("active", {})),
            "bubbles": copy.deepcopy(pdata.get("bubbles", {})),
        }

    return {"meta": scrubbed_meta, "python": scrubbed_python}


def _content_sha(lock: dict, venv_root: Path) -> str:
    """
    Compute a stable SHA256 of the lock's *portable* content.
    Two machines with identical packages + env_kind produce the same hash.
    """
    scrubbed = _scrub_lock(lock, venv_root)
    canonical = json.dumps(scrubbed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _read_existing_sha(lock_path: Path, venv_root: Path) -> str | None:
    """
    Read an existing lock file and return its content SHA.
    Returns None if the file doesn't exist or can't be parsed.
    """
    if not lock_path.exists():
        return None
    if tomllib is None:
        return None
    try:
        with open(lock_path, "rb") as f:
            existing = tomllib.load(f)
        return _content_sha(existing, venv_root)
    except Exception:
        return None


# ── export ────────────────────────────────────────────────────────────────────

def _compute_current_sha(venv_root: Path) -> str | None:
    """
    Compute the content SHA of the current env state without writing anything.
    Reuses the same scan + scrub + hash path as export_lock so the result is
    directly comparable to content_sha stored in a lock file.
    """
    try:
        registry = _load_registry(venv_root)
        interpreters = registry.get("interpreters", {})
        native_ver = registry.get("primary_version", "")
        native_cfg = _load_interp_config((venv_root / "bin"))
        env_identity = _detect_env_identity(venv_root, native_cfg)
        host_profile = _detect_host_profile()
        plat = platform.system().lower()
        mach = platform.machine()
        lock: dict[str, Any] = {
            "meta": {
                "generated": "",
                "omnipkg_version": _omnipkg_version(),
                "native_python": native_ver,
                "venv_root": str(venv_root),
                "platform": f"{plat}_{mach}",
                "machine": mach,
                "env_id": env_identity["env_id"],
                "env_kind": env_identity["env_kind"],
                "env_name": env_identity["env_name"],
                "env_manager": env_identity["env_manager"],
                "python_origin": env_identity["python_origin"],
                "python_executable": env_identity["python_executable"],
                "is_base_env": env_identity["is_base_env"],
                "is_global": env_identity["is_global"],
                "libc_family": host_profile["libc_family"],
                "libc_version": host_profile["libc_version"],
                "linux_distro_id": host_profile["linux_distro_id"],
                "linux_distro_version": host_profile["linux_distro_version"],
                "linux_id_like": host_profile["linux_id_like"],
                "python_impl": host_profile["python_impl"],
            },
            "python": {},
        }
        for ver, py_path_str in sorted(interpreters.items()):
            py_path = Path(py_path_str)
            if ver == native_ver:
                source = "native"
            else:
                try:
                    source = next(p for p in py_path.parts if p.startswith("cpython-"))
                except StopIteration:
                    source = f"cpython-{ver}"
            python_full_version = _get_python_full_version(py_path)
            try:
                r = subprocess.run(
                    [str(py_path), "-c",
                     "import platform; print(platform.python_implementation().lower())"],
                    capture_output=True, text=True, timeout=10,
                )
                python_impl = r.stdout.strip() if r.returncode == 0 else "unknown"
            except Exception:
                python_impl = "unknown"
            sp_path = _site_packages_for(py_path)
            if not sp_path or not sp_path.exists():
                continue
            active = _scan_active_from_fs(sp_path)
            mv_base = sp_path / ".omnipkg_versions"
            bubbles = _scan_bubbles_from_fs(mv_base) if mv_base.exists() else {}
            lock["python"][ver] = {
                "source": source,
                "executable": py_path_str,
                "python_full_version": python_full_version,
                "python_impl": python_impl,
                "active": active,
                "bubbles": bubbles,
            }
        return _content_sha(lock, venv_root)
    except Exception:
        return None

def export_lock(
    output_path: Path | None = None,
    pythons: list[str] | None = None,
    venv_root: Path | None = None,
) -> Path:
    """
    Snapshot the current omnipkg environment to a TOML lock file.

    Default location: <venv_root>/.omnipkg/omnipkg.lock
    Override with output_path for CI/Docker artifact use.

    Returns:
        Path to the written lock file.
    """
    # ── resolve roots first — nothing else runs if these fail ────────────────
    venv_root = venv_root or _venv_root()
    registry = _load_registry(venv_root)

    interpreters: dict[str, str] = registry.get("interpreters", {})
    native_ver: str = registry.get("primary_version", "")

    if pythons:
        missing = [p for p in pythons if p not in interpreters]
        if missing:
            print(f"⚠️  Warning: pythons not in registry: {missing}", file=sys.stderr)
        interpreters = {k: v for k, v in interpreters.items() if k in pythons}

    # ── env identity ──────────────────────────────────────────────────────────
    native_cfg_path = venv_root / "bin" / ".omnipkg_config.json"
    native_cfg = _load_interp_config(native_cfg_path.parent)
    env_identity = _detect_env_identity(venv_root, native_cfg)

    plat = platform.system().lower()
    mach = platform.machine()

    # ── host profile (distro, libc, python impl) ──────────────────────────────
    host_profile = _detect_host_profile()

    # ── build lock structure ──────────────────────────────────────────────────
    lock: dict[str, Any] = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "omnipkg_version": _omnipkg_version(),
            "native_python": native_ver,
            "venv_root": str(venv_root),
            "platform": f"{plat}_{mach}",
            "machine": mach,
            # env identity fields (informational — not in SHA)
            "env_id": env_identity["env_id"],
            "env_kind": env_identity["env_kind"],
            "env_name": env_identity["env_name"],
            "env_manager": env_identity["env_manager"],
            "python_origin": env_identity["python_origin"],
            "python_executable": env_identity["python_executable"],
            "is_base_env": env_identity["is_base_env"],
            "is_global": env_identity["is_global"],
            # host profile: libc_family goes into SHA, rest informational only
            "libc_family": host_profile["libc_family"],
            "libc_version": host_profile["libc_version"],
            "linux_distro_id": host_profile["linux_distro_id"],
            "linux_distro_version": host_profile["linux_distro_version"],
            "linux_id_like": host_profile["linux_id_like"],
            "python_impl": host_profile["python_impl"],
        },
        "python": {},
    }

    for ver, py_path_str in sorted(interpreters.items()):
        py_path = Path(py_path_str)
        interp_bin = py_path.parent

        # Source tag: "native" or "cpython-X.Y.Z" — realization signal only
        if ver == native_ver:
            source = "native"
        else:
            try:
                source = next(
                    p for p in py_path.parts if p.startswith("cpython-")
                )
            except StopIteration:
                source = f"cpython-{ver}"

        # Full patch version — goes into SHA (C extensions care about patch)
        python_full_version = _get_python_full_version(py_path)

        # Python implementation — informational, realization context only
        try:
            r = subprocess.run(
                [str(py_path), "-c",
                 "import platform; print(platform.python_implementation().lower())"],
                capture_output=True, text=True, timeout=10,
            )
            python_impl = r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            python_impl = "unknown"

        # Per-interpreter config → site_packages + multiversion_base
        cfg = _load_interp_config(interp_bin)
        sp_path = cfg.get("site_packages_path")
        mv_base = cfg.get("multiversion_base")

        if not sp_path:
            sp = _site_packages_for(py_path)
            sp_path = str(sp) if sp else None
        if sp_path and not mv_base:
            mv_base = str(Path(sp_path) / ".omnipkg_versions")

        # Data: Redis first, filesystem fallback
        active: dict[str, str] = {}
        bubbles: dict[str, list[str]] = {}

        redis_active, redis_bubbles = _try_redis_for_python(venv_root, ver)
        if redis_active is not None:
            active = redis_active
            bubbles = redis_bubbles or {}
            print(f"  [{ver}] source: Redis KB", file=sys.stderr)
        elif sp_path and Path(sp_path).exists():
            active = _scan_active_from_fs(Path(sp_path))
            if mv_base:
                bubbles = _scan_bubbles_from_fs(Path(mv_base))
            print(f"  [{ver}] source: filesystem scan", file=sys.stderr)
        else:
            print(
                f"  [{ver}] ⚠️  skipped — cannot locate site-packages",
                file=sys.stderr,
            )

        lock["python"][ver] = {
            "source": source,
            "executable": py_path_str,          # real path — kept in local lock
            "python_full_version": python_full_version,  # in SHA
            "python_impl": python_impl,          # informational only
            "active": active,
            "bubbles": bubbles,
        }

    # ── compute portable SHA (scrubbed — no machine paths) ───────────────────
    new_sha = _content_sha(lock, venv_root)

    # ── resolve output path ───────────────────────────────────────────────────
    if output_path is None:
        output_path = _default_lock_path(venv_root)
    output_path = Path(output_path)

    # ── short-circuit: nothing changed ───────────────────────────────────────
    # Check this BEFORE touching the global registry to avoid confusing output.
    existing_sha = _read_existing_sha(output_path, venv_root)

    if existing_sha == new_sha:
        # Still update last_seen in registry (silent — no print from registry update)
        _update_global_lock_registry(
            lock=lock,
            sha=new_sha,
            venv_root=venv_root,
            env_identity=env_identity,
            native_ver=native_ver,
            silent=True,
        )
        print(
            f"✅ Environment unchanged (sha256:{new_sha[:16]}...)\n"
            f"   Lock file is up to date — nothing to write.\n"
            f"   {output_path}"
        )
        return output_path

    # ── global machine registry: write/update canonical store ────────────────
    local_lock_path = _update_global_lock_registry(
        lock=lock,
        sha=new_sha,
        venv_root=venv_root,
        env_identity=env_identity,
        native_ver=native_ver,
        silent=False,
    )

    # ── write local lock — restore ALL private fields scrub removed ───────────
    # _scrub_lock replaces everything for SHA computation. The local copy is
    # private and should have full fidelity — restore every scrubbed field.
    portable_lock = _scrub_lock(lock, venv_root)
    portable_lock["meta"]["generated"] = lock["meta"]["generated"]
    portable_lock["meta"]["omnipkg_version"] = lock["meta"]["omnipkg_version"]
    portable_lock["meta"]["venv_root"] = str(venv_root)
    portable_lock["meta"]["env_id"] = lock["meta"]["env_id"]
    portable_lock["meta"]["env_name"] = lock["meta"]["env_name"]
    portable_lock["meta"]["env_kind"] = lock["meta"]["env_kind"]
    portable_lock["meta"]["env_manager"] = lock["meta"]["env_manager"]
    portable_lock["meta"]["python_origin"] = lock["meta"]["python_origin"]
    portable_lock["meta"]["python_executable"] = lock["meta"]["python_executable"]
    portable_lock["meta"]["is_base_env"] = lock["meta"]["is_base_env"]
    portable_lock["meta"]["is_global"] = lock["meta"]["is_global"]
    # restore realization context fields
    portable_lock["meta"]["libc_version"] = lock["meta"]["libc_version"]
    portable_lock["meta"]["linux_distro_id"] = lock["meta"]["linux_distro_id"]
    portable_lock["meta"]["linux_distro_version"] = lock["meta"]["linux_distro_version"]
    portable_lock["meta"]["linux_id_like"] = lock["meta"]["linux_id_like"]
    portable_lock["meta"]["python_impl"] = lock["meta"]["python_impl"]
    portable_lock["meta"]["content_sha_format"] = 1
    portable_lock["meta"]["content_sha"] = new_sha
    # restore per-python realization fields
    for ver, pdata in lock["python"].items():
        if ver in portable_lock["python"]:
            portable_lock["python"][ver]["executable"] = pdata["executable"]
            portable_lock["python"][ver]["source"] = pdata["source"]
            portable_lock["python"][ver]["python_impl"] = pdata["python_impl"]

    if existing_sha is not None:
        print(
            f"🔄 Environment changed — updating lock file\n"
            f"   sha256:{existing_sha[:16]}... → sha256:{new_sha[:16]}...\n"
            f"   Local: {output_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_dict_to_toml(portable_lock))

    # Also update the canonical global copy if we just created the sha entry
    if local_lock_path and not local_lock_path.exists():
        local_lock_path.parent.mkdir(parents=True, exist_ok=True)
        local_lock_path.write_text(_dict_to_toml(portable_lock))

    return output_path


# ── TOML writer ───────────────────────────────────────────────────────────────

def _dict_to_toml(data: dict) -> str:
    """Uses tomli_w if available, otherwise a minimal hand-rolled writer."""
    if tomli_w:
        return tomli_w.dumps(data)

    lines: list[str] = []

    # [meta]
    lines.append("[meta]")
    for k, v in data["meta"].items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        else:
            lines.append(f"{k} = {json.dumps(v)}")
    lines.append("")

    # [python."X.Y"] sections
    for ver, pdata in data.get("python", {}).items():
        lines.append(f'[python."{ver}"]')
        lines.append(f'source = {json.dumps(pdata["source"])}')
        lines.append(f'executable = {json.dumps(pdata["executable"])}')
        lines.append(f'python_full_version = {json.dumps(pdata.get("python_full_version", "unknown"))}')
        lines.append(f'python_impl = {json.dumps(pdata.get("python_impl", "unknown"))}')
        lines.append("")

        lines.append(f'[python."{ver}".active]')
        for pkg, v in sorted(pdata.get("active", {}).items()):
            lines.append(f"{pkg} = {json.dumps(v)}")
        lines.append("")

        lines.append(f'[python."{ver}".bubbles]')
        for pkg, versions in sorted(pdata.get("bubbles", {}).items()):
            lines.append(
                f"{pkg} = [{', '.join(json.dumps(v) for v in versions)}]"
            )
        lines.append("")

    return "\n".join(lines)



def _get_core_deps_for_interp(py_path: Path, py_ver: str) -> list:
    """
    Ask the target interpreter to run _get_core_dependencies for its Python
    version. Returns a list of PEP 508 dep strings like "aiohttp>=3.13.3".
    Slow but only called once per sync when bootstrap reconciliation is needed.
    """
    r = subprocess.run(
        [str(py_path), "-c",
         f"from omnipkg.core import _get_core_dependencies; "
         f"import json; print(json.dumps(sorted(_get_core_dependencies(\"{py_ver}\"))))"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode == 0:
        try:
            return json.loads(r.stdout.strip())
        except Exception:
            pass
    return []


def _bootstrap_version_safe(pkg: str, locked_ver: str, core_constraints: list) -> bool:
    """
    Returns True if `locked_ver` satisfies omnipkg's own constraint for `pkg`.
    Falls back to True (allow) if packaging is unavailable or pkg not found in
    constraints — pip will reject it if it's truly broken.
    """
    try:
        from packaging.version import Version
        from packaging.requirements import Requirement

        pkg_norm = pkg.lower().replace("-", "_")
        for dep_str in core_constraints:
            try:
                req = Requirement(dep_str)
            except Exception:
                continue
            if req.name.lower().replace("-", "_") != pkg_norm:
                continue
            return req.specifier.contains(Version(locked_ver), prereleases=True)
        # pkg not in our constraints at all — safe to pin
        return True
    except ImportError:
        # packaging not available — allow and let pip decide
        return True
    except Exception:
        return True


# ── sync ──────────────────────────────────────────────────────────────────────

def sync_lock(
    lock_path: Path | None = None,
    yes: bool = False,
    pythons: list[str] | None = None,
    dry_run: bool = False,
    venv_root: Path | None = None,
    force: bool = False,
    pick: bool = False,
) -> None:
    """
    Rebuild an omnipkg environment from a lock file.

    DESTRUCTIVE — clears and reinstalls site-packages + bubbles for each python.
    Default lock file: <venv_root>/.omnipkg/omnipkg.lock

    If no lock file is found at the default location, discovers all lock files
    system-wide and presents an interactive picker (CI auto-selects most recent).

    Python version mismatch (lock native != current env native):
      - Interactive: warns and asks for confirmation.
      - CI: hard fails unless OMNIPKG_SYNC_PYTHON_MISMATCH=allow is set.

    Args:
        lock_path:  Override default lock file location.
        yes:        Skip confirmation prompts (for CI / Docker).
        pythons:    Only sync these version keys (None → all).
        dry_run:    Print plan without making changes.
        venv_root:  Override venv root detection.
        force:      Skip SHA match check — rebuild even if env is up to date.
        pick:       Always show interactive lock file picker.
    """
    if tomllib is None:
        raise RuntimeError(
            "tomllib/tomli not available. Install: pip install tomli"
        )

    # ── interactive helpers ───────────────────────────────────────────────────
    try:
        from omnipkg.common_utils import is_interactive_session, safe_input
    except ImportError:
        def is_interactive_session():
            return sys.stdin.isatty() and not os.environ.get("CI")
        def safe_input(prompt, default="", auto_value=None):
            if is_interactive_session():
                try:
                    return input(prompt).strip()
                except EOFError:
                    return default
                except KeyboardInterrupt:
                    raise
            result = auto_value if auto_value is not None else default
            print(f"🤖 Auto-selecting: {result}")
            return result

    # ── resolve paths ─────────────────────────────────────────────────────────
    venv_root = venv_root or _venv_root()

    # Smart lock file resolution: explicit → default → discovery → picker
    lock_path = _pick_lock_file(lock_path, venv_root, force_pick=pick)

    with open(lock_path, "rb") as f:
        lock = tomllib.load(f)

    meta = lock.get("meta", {})
    lock_pythons: dict[str, dict] = lock.get("python", {})

    if pythons:
        missing = [p for p in pythons if p not in lock_pythons]
        if missing:
            raise ValueError(f"Pythons not in lock file: {missing}")
        lock_pythons = {k: v for k, v in lock_pythons.items() if k in pythons}

    # ── python version mismatch check ─────────────────────────────────────────
    lock_native = meta.get("native_python", "")
    try:
        registry = _load_registry(venv_root)
        current_native = registry.get("primary_version", "")
    except FileNotFoundError:
        current_native = f"{sys.version_info.major}.{sys.version_info.minor}"

    if not _check_python_version_mismatch(lock_native, current_native, yes):
        return

    # ── re-load registry after mismatch check (user may have aborted) ─────────
    registry = _load_registry(venv_root)
    interp_map: dict[str, str] = registry.get("interpreters", {})

    # ── safety check — warn on dangerous env kinds ────────────────────────────
    env_kind = meta.get("env_kind", "unknown")
    if env_kind in ("system", "conda-base"):
        print(
            f"\n🚨 WARNING: Lock file targets a '{env_kind}' environment.\n"
            f"   Syncing into a {env_kind} environment can break your OS or base conda.\n"
            f"   env_root: {meta.get('venv_root', 'unknown')}",
            file=sys.stderr,
        )
        if not yes:
            try:
                from omnipkg.common_utils import safe_input
                ans = safe_input(
                    "   Are you absolutely sure? Type 'yes' to proceed: ",
                    default="no",
                    auto_value="no",
                ).strip()
            except ImportError:
                ans = input("   Are you absolutely sure? Type 'yes' to proceed: ").strip()
            if ans != "yes":
                print("Aborted.")
                return

    _print_sync_plan(meta, lock_pythons, interp_map, lock_path, dry_run)

    if dry_run:
        print("\n[dry-run] Nothing was changed.")
        return

    # SHA check — if current env matches the lock, offer options
    lock_sha = meta.get("content_sha", "")
    print(f"[SHA-CHECK] lock_sha={lock_sha!r}", file=sys.stderr)
    if lock_sha and not force:
        try:
            current_sha = _compute_current_sha(venv_root)
            print(f"[SHA-CHECK] current_sha={current_sha!r}", file=sys.stderr)
            print(f"[SHA-CHECK] match={current_sha == lock_sha}", file=sys.stderr)
            if current_sha and current_sha == lock_sha:
                print("\n✅ Environment already matches lock file (sha256:{sha}...).".format(
                    sha=lock_sha[:16]
                ))
                if not is_interactive_session():
                    print("   Nothing to do.")
                    return
                # Interactive: offer options
                print(
                    "\n   What would you like to do?\n"
                    "   [1] Exit — nothing to do  (default)\n"
                    "   [2] Rebuild anyway\n"
                    "   [3] Sync from a different lock file\n"
                    "   [4] Manage lock files (delete old ones)\n"
                )
                try:
                    choice = safe_input(
                        "   Choice [1]: ", default="1", auto_value="1"
                    ).strip() or "1"
                except KeyboardInterrupt:
                    print("\nAborted.")
                    return

                if choice == "1" or choice == "":
                    print("   Nothing to do.")
                    return
                elif choice == "2":
                    # Fall through to rebuild
                    pass
                elif choice == "3":
                    found = _discover_lock_files()
                    if not found:
                        print("   No other lock files found.")
                        return
                    new_path = _interactive_lock_picker(found, safe_input)
                    if new_path is None:
                        print("Aborted.")
                        return
                    # Reload lock from new path and re-enter sync
                    sync_lock(
                        lock_path=new_path,
                        yes=yes,
                        pythons=pythons,
                        dry_run=dry_run,
                        venv_root=venv_root,
                        force=force,
                        pick=pick,
                    )
                    return
                elif choice == "4":
                    found = _discover_lock_files()
                    _manage_lock_files(found, safe_input)
                    return
                else:
                    print("   Invalid choice — exiting.")
                    return
        except Exception as e:
            print(f"[SHA-CHECK] exception: {e}", file=sys.stderr)

    if not yes:
        try:
            from omnipkg.common_utils import safe_input
            ans = safe_input(
                "\n⚠️  This will DESTROY and rebuild the listed python environments.\n"
                "Proceed? (y/N): ",
                default="n",
                auto_value="n",
            ).strip().lower()
        except ImportError:
            ans = input(
                "\n⚠️  This will DESTROY and rebuild the listed python environments.\n"
                "Proceed? (y/N): "
            ).strip().lower()
        if ans != "y":
            print("Aborted.")
            return

    for ver, pdata in lock_pythons.items():
        print(f"\n{'='*60}\n  Syncing Python {ver}\n{'='*60}")

        source = pdata.get("source", "native")
        active: dict[str, str] = pdata.get("active", {})
        bubbles: dict[str, list[str]] = pdata.get("bubbles", {})

        if ver not in interp_map:
            if source == "native":
                if ver == current_native:
                    # Genuinely native here but not yet registered — this is a
                    # heal-later situation; nothing to adopt, nothing to install into.
                    print(
                        f"  ⚠️  Python {ver} is native to this env but not in registry — "
                        f"run '8pkg doctor' to repair, skipping for now."
                    )
                    continue
                # source=native in the LOCK but this env has a different native.
                # Adopt it as a managed interpreter and install packages there.
                print(
                    f"  📥 Python {ver} was native in the source env but is not native "
                    f"here (native={current_native}) — adopting as managed interpreter..."
                )
            else:
                print(f"  📥 Auto-adopting {source}...")
            _run_cmd(["8pkg", "python", "adopt", ver])
            registry = _load_registry(venv_root)
            interp_map = registry.get("interpreters", {})

        py_path = Path(interp_map[ver])
        interp_bin = py_path.parent
        cfg = _load_interp_config(interp_bin)

        # Always ask the interpreter directly — cfg may point to wrong interpreter
        sp_path = str(_site_packages_for(py_path) or "")
        mv_base = cfg.get("multiversion_base") or (
            str(Path(sp_path) / ".omnipkg_versions") if sp_path else None
        )

        if not sp_path or not Path(sp_path).exists():
            print(f"  ⚠️  Cannot locate site-packages for {ver} — skipping.")
            continue

        # Compatibility pre-check
        compat_issues = _check_compat(py_path, active)
        # Packages with "no compatible version" are hard-skipped from install —
        # they will never succeed on this interpreter (local pkgs, wrong-channel
        # CUDA libs, etc.). "may not be available" packages get a warning only.
        hard_skip: set[str] = {
            pkg for pkg, msg in compat_issues.items()
            if "no compatible version" in msg
        }
        soft_warn: dict[str, str] = {
            pkg: msg for pkg, msg in compat_issues.items()
            if pkg not in hard_skip
        }
        if hard_skip:
            print(f"\n  ⏭️  Auto-skipping {len(hard_skip)} package(s) with no compatible version for Python {ver}:")
            for pkg in sorted(hard_skip):
                print(f"    ✗ {pkg}  ({compat_issues[pkg]})")
        if soft_warn:
            print(f"\n  ⚠️  Compatibility warnings for Python {ver}:")
            for pkg, msg in soft_warn.items():
                print(f"    {pkg}: {msg}")
            if not yes:
                try:
                    from omnipkg.common_utils import safe_input
                    ans = safe_input(
                        "  Continue anyway? (y/N): ",
                        default="n",
                        auto_value="n",
                    ).strip().lower()
                except ImportError:
                    ans = input("  Continue anyway? (y/N): ").strip().lower()
                if ans != "y":
                    print(f"  Skipping Python {ver}.")
                    continue

        # === PER-PYTHON SHA SKIP (fast path — no subprocess) ===
        _plat = meta.get("platform", platform.system())
        _py_sha = _per_python_sha(ver, active, bubbles, _plat)
        if _per_python_sha_check(lock_path, ver, _py_sha):
            print(f"  ✅ Python {ver} already synced (SHA match) — skipping.")
            continue

        # Skip if already matches lock (slower live pip freeze check)
        if _interp_matches_lock(py_path, active, bubbles):
            print(f"  ✅ Python {ver} already matches lock — skipping.")
            _per_python_sha_write(lock_path, ver, _py_sha)  # cache for next run
            continue

        print(f"\n  🧹 Clearing site-packages: {sp_path}")
        _wipe_site_packages(Path(sp_path), keep_omnipkg=True)
        if mv_base and Path(mv_base).exists():
            print(f"  🧹 Clearing bubbles: {mv_base}")
            _wipe_dir_contents(Path(mv_base))

        # pip itself is never reconciled — ensurepip's version is fine and
        # pip upgrading itself mid-process is unstable. Everything else in the
        # bootstrap set that has a locked version gets pinned after the wipe.
        _SYNC_SKIP = {"omnipkg", "pip", "setuptools", "wheel", "pkg-resources"}
        _BOOTSTRAP_PKGS = {
            "aiohttp", "authlib", "flask", "rich", "typer", "uv", "uv-ffi",
            "safety", "pip-audit", "requests", "packaging", "filelock", "tomli",
            "tomli-w", "click", "uvicorn", "starlette", "cryptography",
        }
        if active:
            pkgs = [
                f"{pkg}=={v}" for pkg, v in active.items()
                if pkg not in _SYNC_SKIP and pkg not in hard_skip
            ]
            print(f"\n  📦 Installing {len(pkgs)} active packages...")
            if pkgs:
                _install_with_skip_fallback(py_path, pkgs)

            # Reconcile bootstrap deps: if the lock has an explicit version for
            # any omnipkg runtime package, validate it doesn't conflict with
            # omnipkg's own constraints for this interpreter before pinning.
            bootstrap_candidates = {
                pkg: v for pkg, v in active.items()
                if pkg in _BOOTSTRAP_PKGS and pkg not in _SYNC_SKIP
            }
            if bootstrap_candidates:
                core_constraints = _get_core_deps_for_interp(py_path, ver)
                bootstrap_overrides = []
                for pkg, v in bootstrap_candidates.items():
                    if _bootstrap_version_safe(pkg, v, core_constraints):
                        bootstrap_overrides.append(f"{pkg}=={v}")
                    else:
                        print(f"  ⚠️  Skipping bootstrap pin {pkg}=={v} — "
                              f"conflicts with omnipkg's own constraint for Python {ver}, "
                              f"leaving bootstrap-installed version.")
                if bootstrap_overrides:
                    print(f"\n  🔧 Pinning {len(bootstrap_overrides)} bootstrap dep(s) to locked versions...")
                    _install_with_skip_fallback(py_path, bootstrap_overrides)

        if bubbles:
            total = sum(len(vs) for vs in bubbles.values())
            print(f"\n  🫧  Installing {total} bubble versions...")
            short = ver.replace(".", "")
            for pkg, versions in bubbles.items():
                for bver in versions:
                    print(f"    {pkg}=={bver}")
                    _run_cmd([f"8pkg{short}", "install", f"{pkg}=={bver}"], check=False)

        print(f"\n  ✅ Python {ver} synced.")
        _per_python_sha_write(lock_path, ver, _py_sha)

    # Stop daemon only after all shim calls are done — keeping it alive during
    # the loop prevents execv fallback on adopt/bubble/doctor calls.
    print("  $ 8pkg daemon stop  (background)")
    subprocess.Popen(["8pkg", "daemon", "stop"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    _run_cmd(["8pkg", "daemon", "start"], check=False)

    # Run doctor across every interpreter that was in the sync scope —
    # not just the native one. Each versioned shim targets the right site-packages.
    print("\n\n🏥 Running doctor across all synced interpreters...")
    for ver in lock_pythons:
        short = ver.replace(".", "")
        print(f"\n  🩺 Python {ver}:")
        _run_cmd([f"8pkg{short}", "doctor"], check=False)
    print("\n✅ sync complete.")


# ── sync helpers ──────────────────────────────────────────────────────────────

def _per_python_sha(ver: str, active: dict, bubbles: dict, plat: str) -> str:
    """Compute a deterministic SHA for a single Python version's sync state."""
    active_str = ",".join(f"{k}=={v}" for k, v in sorted(active.items()))
    bubble_str = ";".join(
        f"{k}:{','.join(sorted(vs))}" for k, vs in sorted(bubbles.items())
    )
    raw = f"{ver}|{plat}|{active_str}|{bubble_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _per_python_sha_path(lock_path: Path, ver: str) -> Path:
    """Sidecar file path: <lock_dir>/per_python/<lock_stem>/<ver>.sha"""
    return lock_path.parent / "per_python" / lock_path.stem / f"{ver}.sha"

def _per_python_sha_check(lock_path: Path, ver: str, sha: str) -> bool:
    """Returns True if the stored SHA matches — safe to skip this Python."""
    try:
        p = _per_python_sha_path(lock_path, ver)
        return p.exists() and p.read_text().strip() == sha
    except Exception:
        return False

def _per_python_sha_write(lock_path: Path, ver: str, sha: str) -> None:
    """Write SHA sidecar after a successful sync."""
    try:
        p = _per_python_sha_path(lock_path, ver)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha)
    except Exception:
        pass  # non-fatal — just means next run won't skip

def _interp_matches_lock(py_path: Path, active: dict, bubbles: dict | None = None) -> bool:
    """
    FS-based check — no pip subprocess.
    Scans dist-info for active packages and .omnipkg_versions/ for bubbles.
    pip freeze is intentionally NOT used: it is blind to omnipkg bubble structure.
    """
    try:
        sp = _site_packages_for(py_path)
        if not sp or not sp.exists():
            return False

        _SKIP = {"omnipkg", "pip", "setuptools", "wheel", "pkg-resources", "pkg_resources"}

        # ── active packages: scan dist-info on fs ────────────────────────────
        installed = _scan_active_from_fs(sp)
        for pkg, ver in active.items():
            norm = re.sub(r"[-_.]+", "-", pkg).lower()
            if norm in _SKIP:
                continue
            if installed.get(norm) != ver:
                return False

        # ── bubbles: scan .omnipkg_versions/ on fs ───────────────────────────
        if bubbles:
            mv_base = sp / ".omnipkg_versions"
            installed_bubbles = _scan_bubbles_from_fs(mv_base)
            for pkg, versions in bubbles.items():
                norm = re.sub(r"[-_.]+", "-", pkg).lower()
                installed_vers = set(installed_bubbles.get(norm, []))
                for bver in versions:
                    if bver not in installed_vers:
                        return False

        return True
    except Exception:
        return False

def _print_sync_plan(
    meta: dict, lock_pythons: dict, interp_map: dict,
    lock_path: Path, dry_run: bool,
) -> None:
    tag = "[dry-run] " if dry_run else ""
    print(f"\n{tag}📋 Sync plan")
    print(f"  Lock file:      {lock_path}")
    raw_ts = meta.get("generated", "")
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(raw_ts)
        ts = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts = raw_ts or "unknown"
    print(f"  Generated:      {ts}")
    print(f"  Source venv:    {meta.get('venv_root', 'unknown')}")
    print(f"  Env kind:       {meta.get('env_kind', 'unknown')} ({meta.get('env_name', '')})")
    print(f"  Platform:       {meta.get('platform', 'unknown')}")
    print(f"  Native python:  {meta.get('native_python', 'unknown')}")
    print()
    for ver, pdata in lock_pythons.items():
        n_active = len(pdata.get("active", {}))
        n_bubbles = sum(len(v) for v in pdata.get("bubbles", {}).values())
        status = "✅ in registry" if ver in interp_map else "📥 will adopt"
        print(f"  Python {ver:6s}  {status}  {n_active} active, {n_bubbles} bubbles")


def _install_with_skip_fallback(py_path: Path, pkgs: list[str]) -> None:
    """Batch pip install with automatic retry-without-all-failed-packages on error.

    Runs pip with stderr captured separately so we can actually parse it.
    Any rc != 0 is treated as a skippable failure — identifies the bad package(s),
    drops them, and retries the rest. Loops until clean or nothing left to remove.
    Skipped packages are always reported — never silently dropped.
    """
    import re

    def _normalize(name: str) -> str:
        return name.split("==")[0].lower().replace("_", "-").replace(".", "-")

    def _extract_bad_packages(error_text: str) -> set[str]:
        """Pull every failing package name out of pip's stderr."""
        bad: set[str] = set()
        # "Failed to build msgpack aiohttp cffi"  (space-separated on one line)
        for m in re.finditer(
            r"Failed (?:building|to build)\s+(?:installable wheels for some[^\n]*?\n\s*)?(.+)",
            error_text, re.IGNORECASE
        ):
            for tok in re.split(r"[\s,]+", m.group(1).strip()):
                tok = tok.strip(".,;")
                if tok:
                    bad.add(tok.lower().replace("_", "-").replace(".", "-"))
        # "ERROR: Failed building wheel for aiohttp"
        for m in re.finditer(
            r"ERROR: Failed building wheel for ([\w\-\.]+)",
            error_text, re.IGNORECASE
        ):
            bad.add(m.group(1).lower().replace("_", "-").replace(".", "-"))
        # "No matching distribution found for foo==1.2"
        for m in re.finditer(
            r"(?:No matching distribution found for|satisfies the requirement)\s+([\w\-\.]+(?:==[\w\.\-]+)?)",
            error_text, re.IGNORECASE
        ):
            bad.add(_normalize(m.group(1)))
        return bad

    remaining = list(pkgs)
    skipped: list[str] = []

    while remaining:
        import glob as _glob, os as _os
        _sp = Path(py_path).parent.parent / "lib"
        _egg_paths = _glob.glob(str(_sp / "python*/site-packages/pip-*.egg"))
        _env = _os.environ.copy()
        if _egg_paths:
            _existing = _env.get("PYTHONPATH", "")
            _env["PYTHONPATH"] = _os.pathsep.join(_egg_paths + ([_existing] if _existing else []))
        cmd = [str(py_path), "-m", "pip", "install", "--no-deps", "--no-cache-dir"] + remaining
        print(f"  $ {' '.join(cmd[:4])} ... ({len(remaining)} packages)")

        # Stream stdout live for progress; capture stderr SEPARATELY for parsing.
        # _run_cmd_streaming merges stderr into stdout and loses it — can't use it here.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=_env,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
        stderr_text = proc.stderr.read()
        proc.wait()

        if proc.returncode == 0:
            break  # all good

        # rc != 0 — print stderr so user sees what happened
        for line in stderr_text.splitlines():
            if line.rstrip():
                print(f"    {line.rstrip()}")

        bad_names = _extract_bad_packages(stderr_text)

        if not bad_names:
            raise RuntimeError(
                f"pip exited {proc.returncode} but could not identify failing package.\n"
                f"stderr:\n{stderr_text}\ncmd: {cmd}"
            )

        before = len(remaining)
        new_remaining = []
        for p in remaining:
            if _normalize(p) in bad_names:
                skipped.append(p)
                print(f"  ⚠️  Skipping (failed to build/install): {p}")
            else:
                new_remaining.append(p)

        if len(new_remaining) == before:
            raise RuntimeError(
                f"pip failed on {bad_names} but none matched the install list.\n"
                f"stderr:\n{stderr_text}"
            )

        remaining = new_remaining
        print(f"  🔄 Retrying with {len(remaining)} remaining packages...")

    if skipped:
        print(f"\n  📋 Skipped {len(skipped)} package(s) during install (build/compat failure):")
        for s in skipped:
            print(f"    ✗ {s}")


def _check_compat(py_path: Path, active: dict[str, str]) -> dict[str, str]:
    """Quick pre-check: warn about packages that may not exist for target python."""
    issues: dict[str, str] = {}
    try:
        r = subprocess.run(
            [str(py_path), "-c",
             "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            return issues
        py_ver = r.stdout.strip()
    except Exception:
        return issues

    _COMPAT_SKIP = {"omnipkg", "pip", "setuptools", "wheel"}
    for pkg, ver in active.items():
        if pkg in _COMPAT_SKIP:
            continue  # not on PyPI / managed separately
        try:
            # pip index versions requires pip>=21.2 — use download --dry-run
            # which works back to pip 9 and uses the actual target interpreter
            r = subprocess.run(
                [str(py_path), "-m", "pip", "download", "--dry-run",
                 "--no-deps", "--quiet", f"{pkg}=={ver}"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode != 0:
                out = r.stdout + r.stderr
                if "No matching distribution" in out or "Could not find" in out:
                    issues[pkg] = f"no compatible version for Python {py_ver}"
                # else: network/pip error — dont penalize the package
        except subprocess.TimeoutExpired:
            pass
    return issues


def _wipe_site_packages(sp: Path, keep_omnipkg: bool = True) -> None:
    import shutil
    # Sanity guard — refuse to wipe obviously dangerous roots
    resolved = sp.resolve()
    _dangerous = [
        Path("/").resolve(),
        Path.home().resolve(),
        Path("/usr").resolve(),
        Path("/usr/local").resolve(),
        Path("/usr/lib").resolve(),
    ]
    if resolved in _dangerous:
        raise RuntimeError(
            f"Cowardly refusing to wipe {resolved} — "
            f"this looks like a system or home directory, not a site-packages."
        )
    _keep_names = {"pip", "setuptools", "wheel", "pkg_resources", "_distutils_hack"}
    for item in sp.iterdir():
        if item.name == ".omnipkg_versions":
            continue
        if keep_omnipkg and "omnipkg" in item.name.lower():
            continue
        stem = item.name.split("-")[0].lower().replace("_", "-")
        if stem in _keep_names:
            continue
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        except Exception as e:
            print(f"  ⚠️  Could not remove {item}: {e}", file=sys.stderr)


def _wipe_dir_contents(d: Path) -> None:
    import shutil
    # Lighter sanity guard — bubbles live under site-packages/.omnipkg_versions,
    # but still worth blocking obviously dangerous roots.
    resolved = d.resolve()
    _dangerous = [Path("/").resolve(), Path.home().resolve(), Path("/usr").resolve()]
    if resolved in _dangerous:
        raise RuntimeError(
            f"Cowardly refusing to wipe {resolved} — "
            f"this looks like a system or home directory."
        )
    for item in d.iterdir():
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        except Exception as e:
            print(f"  ⚠️  Could not remove {item}: {e}", file=sys.stderr)


def _run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd}")
    return result

def _run_cmd_streaming(cmd: list[str], check: bool = True) -> int:
    """Stream pip output live so the user can see progress instead of a silent hang."""
    print(f"  $ {' '.join(cmd[:4])} ... ({len(cmd) - 4} packages)")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(f"    {line}")
    proc.wait()
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed (exit {proc.returncode}): {cmd}")
    return proc.returncode