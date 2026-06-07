"""
build_hooks.py — thin wrapper around setuptools build backend.

Intercepts pip's build/install hooks to compile and install the C dispatcher
binary after entry point scripts are written.

Referenced in pyproject.toml:
  [build-system]
  requires = ["setuptools>=50.0,<70.0", "wheel"]
  build-backend = "build_hooks"
  backend-path = ["."]          ← tells pip to look in repo root for this module
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Compatibility shim for Python < 3.11 — locale.getencoding() added in 3.11
# Without this, packaging/_musllinux.py crashes in manylinux containers
import locale
if not hasattr(locale, 'getencoding'):
    locale.getencoding = lambda: 'UTF-8'

# Re-export everything from the real setuptools backend
from setuptools.build_meta import (
    build_wheel,
    build_sdist,
    build_editable,
    get_requires_for_build_wheel,
    get_requires_for_build_sdist,
    get_requires_for_build_editable,
    prepare_metadata_for_build_wheel,
    prepare_metadata_for_build_editable,
)

import sys
from pathlib import Path
import shutil

def _get_ver():
    try:
        import importlib.metadata as _im
        return _im.version("omnipkg")
    except Exception:
        pass
    try:
        repo_root = Path(__file__).parent
        for toml_path in [repo_root / "pyproject.toml", repo_root / "src" / "pyproject.toml"]:
            if toml_path.exists():
                content = toml_path.read_text(encoding="utf-8")[:2048]
                for line in content.split("\n"):
                    s = line.strip()
                    if s.startswith("version"):
                        return s.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return None



def _collect_host_info() -> dict:
    """Same as setup.py _collect_host_info — duplicated since build_hooks cannot import setup.py."""
    import shutil as _sh
    info: dict = {
        "libc_family": "unknown", "libc_version": "unknown",
        "linux_distro_id": "unknown", "linux_distro_version": "unknown",
        "linux_id_like": "unknown",
        "gcc_version": "unknown", "gcc_path": "unknown",
        "cl_version": "unknown", "cargo_version": "unknown",
        "maturin_version": "unknown",
        "env_kind": "unknown", "env_name": "unknown", "is_docker": False,
    }
    info["is_docker"] = (
        Path("/.dockerenv").exists()
        or os.environ.get("container") == "docker"
        or os.environ.get("DOCKER_CONTAINER") == "1"
    )
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    conda_default = os.environ.get("CONDA_DEFAULT_ENV", "")
    virtual_env   = os.environ.get("VIRTUAL_ENV", "")
    if info["is_docker"]:
        info["env_kind"] = "docker"
    elif conda_prefix:
        conda_root = os.environ.get("CONDA_ROOT") or os.environ.get("CONDA_EXE", "")
        is_base = (
            Path(conda_prefix) == Path(conda_root).parent.parent
            if conda_root else conda_default in ("base", "")
        )
        info["env_kind"] = "conda-base" if is_base else "conda"
        info["env_name"] = conda_default or Path(conda_prefix).name
    elif virtual_env:
        pyvenv = Path(virtual_env) / "pyvenv.cfg"
        if pyvenv.exists() and "uv" in pyvenv.read_text().lower():
            info["env_kind"] = "uv"
        else:
            info["env_kind"] = "venv"
        info["env_name"] = Path(virtual_env).name
    else:
        info["env_kind"] = "system"

    if sys.platform == "linux":
        for osr in ("/etc/os-release", "/usr/lib/os-release"):
            try:
                fields = {}
                for line in Path(osr).read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, unused, v = line.partition("=")
                        fields[k.strip()] = v.strip().strip('"')
                info["linux_distro_id"]      = fields.get("ID", "unknown").lower()
                info["linux_distro_version"] = fields.get("VERSION_ID", "unknown")
                info["linux_id_like"]        = fields.get("ID_LIKE", "unknown").lower()
                break
            except OSError:
                continue
        try:
            import subprocess as _sp, re as _re
            r = _sp.run(["ldd", "--version"], capture_output=True, text=True, timeout=5)
            out = (r.stdout + r.stderr).lower()
            if "gnu libc" in out or "glibc" in out:
                info["libc_family"] = "glibc"
                for line in out.splitlines():
                    m = _re.search(r"(\d+\.\d+)", line)
                    if m: info["libc_version"] = m.group(1); break
            elif "musl" in out:
                info["libc_family"] = "musl"
                for line in out.splitlines():
                    m = _re.search(r"(\d+\.\d+\.\d+|\d+\.\d+)", line)
                    if m: info["libc_version"] = m.group(1); break
        except Exception:
            pass
        if info["libc_family"] == "unknown":
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.gnu_get_libc_version.restype = ctypes.c_char_p
                info["libc_family"] = "glibc"
                info["libc_version"] = libc.gnu_get_libc_version().decode()
            except Exception:
                pass

    import subprocess as _sp, re as _re
    gcc = _sh.which("gcc")
    if gcc:
        try:
            r = _sp.run([gcc, "--version"], capture_output=True, text=True, timeout=5)
            first = r.stdout.splitlines()[0] if r.stdout else ""
            m = _re.search(r"(\d+\.\d+\.\d+)", first)
            info["gcc_version"] = m.group(1) if m else first.strip()[:40]
            info["gcc_path"] = gcc
        except Exception:
            info["gcc_path"] = gcc
    if sys.platform == "win32":
        cl = _sh.which("cl")
        if cl:
            try:
                r = _sp.run([cl], capture_output=True, text=True, timeout=5)
                first = (r.stdout + r.stderr).splitlines()[0] if (r.stdout + r.stderr) else ""
                m = _re.search(r"(\d+\.\d+\.\d+)", first)
                info["cl_version"] = m.group(1) if m else first.strip()[:60]
            except Exception:
                pass
    cargo = _sh.which("cargo")
    if cargo:
        try:
            r = _sp.run([cargo, "--version"], capture_output=True, text=True, timeout=5)
            m = _re.search(r"(\d+\.\d+\.\d+)", r.stdout)
            info["cargo_version"] = m.group(1) if m else "unknown"
        except Exception:
            pass
    maturin = _sh.which("maturin")
    if maturin:
        try:
            r = _sp.run([maturin, "--version"], capture_output=True, text=True, timeout=5)
            m = _re.search(r"(\d+\.\d+\.\d+)", r.stdout)
            info["maturin_version"] = m.group(1) if m else "unknown"
        except Exception:
            pass
    return info


def _write_install_stamp(ver=None):
    # Writes <venv>/.omnipkg/omnipkg_install_stamp.json so core.py init
    # can bail out of _self_heal_omnipkg_installation in a single file read.
    import json
    ver = ver or _get_ver()
    if not ver:
        return
    try:
        venv_root = Path(sys.prefix)
        stamp_dir = venv_root / ".omnipkg"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        stamp_path = stamp_dir / "omnipkg_install_stamp.json"
        stamp_path.write_text(json.dumps({"version": ver}), encoding="utf-8")
        print(f"  [omnipkg] install stamp  → {stamp_path}  (v{ver})")
    except Exception as e:
        print(f"  [omnipkg] stamp write skipped: {e}")


def _write_build_manifest_hooks(ver=None):
    # build_hooks.py variant: filesystem-probe fallback.
    # setup.py's OptionalBuildExt.run() writes the manifest with accurate
    # compile-time results first.  We only write here if that didn't happen
    # (e.g. pure-Python / noarch builds that skip OptionalBuildExt entirely).
    import json, datetime, glob as _gl
    ver = ver or _get_ver() or "unknown"
    try:
        # If setup.py already wrote a manifest for this exact version, trust it.
        manifest_path = Path(sys.prefix) / ".omnipkg" / "build_manifest.json"
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if existing.get("omnipkg_version") == ver:
                    return  # setup.py's manifest is authoritative — don't clobber
            except Exception:
                pass  # unreadable — fall through and rewrite
        _exe = ".exe" if sys.platform == "win32" else ""
        bin_dir = Path(sys.executable).parent

        # Dispatcher: check if 8pkg/omnipkg is a real native binary (not a Python script)
        disp_status = {"status": "skipped", "reason": "not checked"}
        for name in ("8pkg", "omnipkg"):
            target = bin_dir / (name + _exe)
            if target.exists():
                # Heuristic: Python wrapper scripts start with #!; C binaries don't
                try:
                    header = target.read_bytes()[:4]
                    is_elf  = header[:4] == b'\x7fELF'
                    is_pe   = header[:2] == b'MZ'
                    is_macho = header[:4] in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe',
                                              b'\xca\xfe\xba\xbe')
                    is_binary = is_elf or is_pe or is_macho
                    disp_status = {
                        "status": "ok" if is_binary else "skipped",
                        "path": str(target),
                        "reason": "C binary confirmed" if is_binary else "Python script (C build skipped)",
                    }
                except Exception as e:
                    disp_status = {"status": "unknown", "path": str(target), "reason": str(e)}
                break

        # uv-ffi: look for the .so in site-packages
        ffi_status = {"status": "skipped", "reason": "not checked"}
        try:
            import site as _site
            sp_dirs = _site.getsitepackages()
            for sp in sp_dirs:
                matches = (_gl.glob(os.path.join(sp, "uv_ffi*.so")) +
                           _gl.glob(os.path.join(sp, "uv_ffi*.pyd")))
                if matches:
                    ffi_status = {"status": "ok", "so_path": matches[0]}
                    break
            else:
                ffi_status = {"status": "skipped", "reason": "no uv_ffi .so found (PyPI dep or build skipped)"}
        except Exception as e:
            ffi_status = {"status": "unknown", "reason": str(e)}

        # atomic: look for the .so
        atomic_status = {"status": "skipped", "reason": "not checked"}
        try:
            import site as _site
            sp_dirs = _site.getsitepackages()
            for sp in sp_dirs:
                matches = _gl.glob(os.path.join(sp, "omnipkg", "isolation", "omnipkg_atomic*.so"))
                if not matches:
                    matches = _gl.glob(os.path.join(sp, "omnipkg", "isolation", "omnipkg_atomic*.pyd"))
                if matches:
                    atomic_status = {"status": "ok", "so_path": matches[0]}
                    break
            else:
                atomic_status = {"status": "skipped", "reason": "no atomic .so found"}
        except Exception as e:
            atomic_status = {"status": "unknown", "reason": str(e)}

        failed = [k for k, v in [("dispatcher", disp_status), ("uv_ffi", ffi_status), ("atomic", atomic_status)]
                  if v.get("status") == "failed"]
        ok     = [k for k, v in [("dispatcher", disp_status), ("uv_ffi", ffi_status), ("atomic", atomic_status)]
                  if v.get("status") == "ok"]
        summary = (f"⚠️  {len(failed)} failed: {', '.join(failed)} | ok: {', '.join(ok) or 'none'}"
                   if failed else f"✅ ({', '.join(ok) or 'none built'})")

        host = _collect_host_info()
        manifest = {
            "omnipkg_version": ver,
            "python":    sys.version.split()[0],
            "python_impl": __import__("platform").python_implementation().lower(),
            "platform":  sys.platform,
            "arch":      __import__("platform").machine(),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "host":      host,
            "dispatcher": disp_status,
            "uv_ffi":     ffi_status,
            "atomic":     atomic_status,
            "summary":    summary,
        }
        venv_root = Path(sys.prefix)
        stamp_dir = venv_root / ".omnipkg"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = stamp_dir / "build_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  [omnipkg] build manifest → {manifest_path}")
        print(f"  [omnipkg]   {summary}")
    except Exception as e:
        print(f"  [omnipkg] manifest write skipped: {e}")


def _run_post_install():
    if sys.platform in ('emscripten', 'wasm32'):
        return
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from omnipkg.dispatcher import _maybe_install_c_dispatcher
        _maybe_install_c_dispatcher()
    except Exception as e:
        print(f"  [dispatcher] post-install skipped: {e}")
    ver = _get_ver()
    _write_install_stamp(ver)
    _write_build_manifest_hooks(ver)

# Override build_editable — this is what `pip install -e .` calls
def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools.build_meta import build_editable as _build_editable
    result = _build_editable(wheel_directory, config_settings, metadata_directory)
    _run_post_install()
    _heal_entrypoints()  # add this
    return result

def _heal_entrypoints():
    bin_dir = Path(sys.executable).parent
    base = bin_dir / "omnipkg"
    if not base.exists():
        return

    for name in ["8pkg", "OMNIPKG", "8PKG"]:
        target = bin_dir / name
        # Skip if the target already exists *or* is the same file as base
        # (important on case-insensitive FS)
        if target.exists():
            if target.samefile(base):
                continue
            # If it exists but is different, assume it's valid and skip
            continue

        shutil.copy2(str(base), str(target))
        print(f" [build_hooks] healed missing entrypoint: {name}")

# Override build_wheel — this is what `pip install .` (non-editable) calls
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools.build_meta import build_wheel as _build_wheel
    result = _build_wheel(wheel_directory, config_settings, metadata_directory)
    _run_post_install()
    return result