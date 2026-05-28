#!/usr/bin/env python
"""
Minimal setup.py bridge for Python 3.7 compatibility.
"""
# Compatibility shim — locale.getencoding() added in Python 3.11
# Without this, C dispatcher compilation silently skips in manylinux containers
import locale
if not hasattr(locale, 'getencoding'):
    locale.getencoding = lambda: 'UTF-8'

from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.install import install
from setuptools.command.develop import develop
import os
import sys
import subprocess
import shutil
import platform
from pathlib import Path

# Automatically detect WASM environment and skip all C compilation
IS_WASM = sys.platform in ('emscripten', 'wasm32')
if IS_WASM:
    os.environ['OMNIPKG_SKIP_C_EXT'] = '1'
    
SKIP_C_EXTENSIONS = os.environ.get('OMNIPKG_SKIP_C_EXT', '0') == '1'

# ── Dispatcher binary install (shared by both install and develop commands) ──

def _install_dispatcher_binary(install_dir=None):
    """
    Compile the C dispatcher and overwrite the pip-generated 8pkg/omnipkg
    wrapper scripts with the fast binary.
    Supports gcc (Linux/macOS) and MSVC cl.exe (Windows).
    """
    repo_root = Path(__file__).parent
    c_source = repo_root / "src" / "omnipkg" / "dispatcher.c"
    if not c_source.exists():
        print("  [dispatcher] No C source found, skipping binary install")
        return

    # --- Compiler detection ---
    compiler = None
    compiler_args = []
    compile_env = os.environ.copy()

    if sys.platform == "win32":
        cl = shutil.which("cl")
        print(f"  [dispatcher] win32: cl in PATH = {cl}")
        if not cl:
            import glob as _cgl
            for _pat in [
                "C:/Program Files/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe",
                "C:/Program Files (x86)/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe",
                "C:/Program Files (x86)/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/HostX86/x64/cl.exe",
            ]:
                _matches = _cgl.glob(_pat)
                print(f"  [dispatcher] glob {_pat} -> {_matches}")
                if _matches:
                    cl = _matches[0]
                    break
        print(f"  [dispatcher] final compiler = {cl}")
        
        if cl:
            compiler = cl
            compiler_args = ["/O2", "/Fe:{out}", "{src}", "ws2_32.lib"]
            # Reconstruct MSVC environment (what vcvarsall.bat does)
            try:
                cl_path = Path(cl)
                msvc_root = cl_path.parent.parent.parent.parent
                msvc_include = str(msvc_root / "include")
                msvc_lib = str(msvc_root / "lib" / "x64")
                sdk_includes, sdk_libs = [], []
                sdk_base = Path("C:/Program Files (x86)/Windows Kits/10")
                if not sdk_base.exists():
                    sdk_base = Path("C:/Program Files/Windows Kits/10")
                if sdk_base.exists():
                    inc_base = sdk_base / "include"
                    lib_base = sdk_base / "lib"
                    sdk_versions = sorted(inc_base.iterdir(), reverse=True)
                    if sdk_versions:
                        sdk_ver = sdk_versions[0]
                        for sub in ("ucrt", "um", "shared"):
                            p = sdk_ver / sub
                            if p.exists():
                                sdk_includes.append(str(p))
                        lib_ver = lib_base / sdk_ver.name
                        for sub in ("ucrt/x64", "um/x64"):
                            p = lib_ver / sub.replace("/", os.sep)
                            if p.exists():
                                sdk_libs.append(str(p))
                compile_env["INCLUDE"] = os.pathsep.join([msvc_include] + sdk_includes)
                compile_env["LIB"] = os.pathsep.join([msvc_lib] + sdk_libs)
            except Exception as e:
                print(f"  [dispatcher] Warning: could not derive MSVC env: {e}")
        else:
            gcc = shutil.which("gcc") or shutil.which("x86_64-w64-mingw32-gcc")
            if gcc:
                compiler = gcc
                compiler_args = ["-O2", "-o", "{out}", "{src}", "-lws2_32"]
    else:
        gcc = shutil.which("gcc")
        if gcc:
            compiler = gcc
            compiler_args = ["-O2", "-o", "{out}", "{src}"]
        if sys.platform == "darwin":
            archflags = os.environ.get("ARCHFLAGS", "")
            if archflags:
                compiler_args = ["-O2"] + archflags.split() + ["-o", "{out}", "{src}"]
            elif platform.machine() == "arm64":
                compiler_args = ["-O2", "-arch", "x86_64", "-arch", "arm64", "-o", "{out}", "{src}"]

    if not compiler:
        print("  [dispatcher] no compiler found — using Python dispatcher")
        return

    if install_dir is None:
        install_dir = Path(sys.executable).parent

    _exe = ".exe" if sys.platform == "win32" else ""
    binary_out = Path(install_dir) / f"_omnipkg_dispatch_bin{_exe}"
    print(f"  [dispatcher] install_dir  : {Path(install_dir).resolve()}")
    print(f"  [dispatcher] binary_out   : {binary_out.resolve()}")
    print(f"  [dispatcher] c_source     : {c_source.resolve()}")
    binary_out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [compiler] + [
        a.format(out=str(binary_out), src=str(c_source))
        for a in compiler_args
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=compile_env)
        if result.returncode != 0:
            print(f"  [dispatcher] Compilation failed (retcode={result.returncode})")
            print(f"  [dispatcher] cmd: {' '.join(cmd)}")
            print(f"  [dispatcher] stderr:\n{result.stderr.strip()}")
            return

        import time
        replaced = []
        for attempt in range(10):
            replaced = []
            for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
                target = Path(install_dir) / (name + _exe)
                if target.exists():
                    shutil.copy2(str(binary_out), str(target))
                    if sys.platform != "win32":
                        os.chmod(str(target), 0o755)
                    replaced.append(name)
            if replaced:
                break
            time.sleep(0.5)

        if replaced:
            binary_out.unlink(missing_ok=True)
            print(f"  [dispatcher] ✅ Fast C dispatcher installed over: {replaced}")
            for name in replaced:
                target = Path(install_dir) / (name + _exe)
                print(f"  [dispatcher]    → {target}")
        else:
            print(f"  [dispatcher] Bundling compiled C dispatcher into the wheel.")
            print(f"  [dispatcher]    binary at : {binary_out.resolve()}")

    except Exception as e:
        print(f"  [dispatcher] Skipping C dispatcher: {e}")

def _build_uv_ffi(install_dir=None):
    """
    Build the uv_ffi PyO3 extension via maturin and install the .so
    into the active environment. Only runs in a dev checkout (submodule present);
    normal installs get uv-ffi from PyPI as a declared dependency.
    """
    repo_root = Path(__file__).parent
    crate = repo_root / "src/omnipkg/_vendor/uv/crates/uv-ffi"
    # Crate only exists when the git submodule is checked out (dev env).
    # Wheel/sdist installs won't have it — skip silently, PyPI dep covers it.
    if not crate.exists() or not (repo_root / ".git").exists():
        return

    if not shutil.which("cargo"):
        return

    if not shutil.which("maturin"):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "maturin", "-q"],
                           check=True)
        except Exception:
            return

    try:
        # maturin locates pip via VIRTUAL_ENV; conda envs don't set this,
        # so point it at the conda env root (parent of bin/ where pip lives)
        env_root = str(Path(sys.executable).parent.parent)
        env = {**os.environ,
               "VIRTUAL_ENV": env_root,
               "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        # maturin develop needs pip internally; use build+install instead
        # crate     = .../src/omnipkg/_vendor/uv/crates/uv-ffi
        # workspace = crate.parent.parent  = .../src/omnipkg/_vendor/uv
        # maturin writes wheels to <workspace>/target/wheels by default;
        # pass --out explicitly so we always know exactly where they land.
        workspace_root = crate.parent.parent
        wheel_dir = workspace_root / "target" / "wheels"
        wheel_dir.mkdir(parents=True, exist_ok=True)
        print(f"  [uv-ffi] workspace    : {workspace_root.resolve()}")
        print(f"  [uv-ffi] wheel_dir    : {wheel_dir.resolve()}")

        build_result = subprocess.run(
            ["maturin", "build", "--release", "--out", str(wheel_dir)],
            capture_output=True, text=True, env=env, cwd=str(crate)
        )
        if build_result.returncode != 0:
            print(f"  [uv-ffi] build failed — FFI unavailable, daemon path will be used")
            print(f"  {build_result.stderr[-500:].strip()}")
            return
        wheels = sorted(wheel_dir.glob("uv_ffi*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)

        if wheels:
            print(f"  [uv-ffi] installing   : {wheels[0].resolve()}")
        if not wheels:
            print(f"  [uv-ffi] build succeeded but no wheel found in {wheel_dir}")
            return

        # pip install -e . runs setup.py inside an isolated build venv whose
        # Python has no pip module. Find the real env pip executable via
        # CONDA_PREFIX or VIRTUAL_ENV — those always point at the real env root.
        def _get_pip_cmd():
            # The isolated build venv sets PYTHONPATH/PYTHONHOME which poison
            # subprocesses. Find the real env Python and run it with a clean env.
            for var in ("CONDA_PREFIX", "VIRTUAL_ENV"):
                env_root = os.environ.get(var)
                if not env_root:
                    continue
                for name in ("python3", "python"):
                    p = Path(env_root) / "bin" / name
                    if p.exists():
                        print(f"  [uv-ffi] using pip : {p} -m pip  (via {var})")
                        return [str(p), "-m", "pip"]
            print(f"  [uv-ffi] no pip found (CONDA_PREFIX={os.environ.get('CONDA_PREFIX')} VIRTUAL_ENV={os.environ.get('VIRTUAL_ENV')})")
            return None

        def _clean_env():
            # Strip build-venv vars that break subprocess Python imports
            e = os.environ.copy()
            for var in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
                e.pop(var, None)
            return e

        pip_cmd = _get_pip_cmd()
        if pip_cmd is None:
            print(f"  [uv-ffi] pip unavailable — FFI unavailable, daemon path will be used")
            return

        install_result = subprocess.run(
            pip_cmd + ["install", "--force-reinstall", str(wheels[0])],
            capture_output=True, text=True, env=_clean_env()
        )
        if install_result.returncode == 0:
            import importlib.util, site
            sp = site.getsitepackages()
            print("  [uv-ffi] ✅ PyO3 FFI extension built and installed")
            print(f"  [uv-ffi]    site-packages : {sp[0] if sp else 'unknown'}")
        else:
            print(f"  [uv-ffi] pip install failed — FFI unavailable, daemon path will be used")
            print(f"  {install_result.stderr[-500:].strip()}")
    except Exception as e:
        print(f"  [uv-ffi] Skipping: {e}")


# ── Atomic extension ─────────────────────────────────────────────────────────

import platform as _platform

_c_args = ["-O3"]
_machine = _platform.machine().lower()
_system  = _platform.system()

if _system == "Linux":
    # -march=native is UNSAFE in QEMU cross-compile: the host CPU feature
    # set leaks through QEMU into the container, producing march strings
    # (e.g. lse128+gcs) that the container's GCC doesn't know.
    # Use explicit safe baselines per architecture instead.
    if _machine in ("x86_64", "i686", "i386"):
        _c_args.append("-march=x86-64")      # SSE2 baseline, safe everywhere
    elif _machine == "aarch64":
        _c_args.append("-march=armv8-a")      # base AArch64, no exotic extensions
    elif _machine == "armv7l":
        _c_args.append("-march=armv7-a")
    # ppc64le, s390x, riscv64: no -march flag, let GCC pick its own default
# macOS: no -march flag (Universal builds break with -march=native)
# Windows: MSVC flags handled separately

atomic_extension = Extension(
    name="omnipkg.isolation.omnipkg_atomic",
    sources=["src/omnipkg/isolation/atomic_ops.c"],
    extra_compile_args=_c_args,
    optional=True,
    py_limited_api=True,
    define_macros=[('Py_LIMITED_API', '0x03070000')],
)

def _print_exotic_platform_hint():
    machine = _platform.machine().lower()
    if machine not in ("armv7l", "armv7", "s390x", "riscv64"):
        return
    # Detect musl
    is_musl = False
    try:
        with open("/proc/self/maps") as f:
            is_musl = "musl" in f.read()
    except Exception:
        pass
    if not is_musl:
        return
    print()
    print("=" * 60)
    print(f"  EXOTIC PLATFORM DETECTED: {_platform.machine()} / musl")
    print()
    print("  Some dependencies (cryptography, psutil) have no")
    print("  prebuilt wheels for this platform on PyPI and will")
    print("  attempt to compile from source, which requires Rust")
    print("  and can take 20-40 minutes or fail entirely.")
    print()
    print("  Pre-built wheels available at:")
    print("  https://1minds3t.github.io/exotic-wheels/")
    print()
    print("  For a faster install run:")
    print(f"  pip install omnipkg \\")
    print(f"    --extra-index-url https://1minds3t.github.io/exotic-wheels/")
    print("=" * 60)
    print()


# OptionalBuildExt is ALWAYS registered so that dispatcher + uv-ffi are
# attempted by modern pip (which calls build_ext but never install/develop).
# When SKIP_C_EXTENSIONS=1 (noarch wheel build), ext_modules is empty so
# build_extension() is never called — but run() still fires for dispatcher/ffi,
# which is fine because those functions are already no-ops when tools are absent.
# For the noarch wheel we explicitly skip dispatcher too via the env var guard
# inside run() below.
class OptionalBuildExt(build_ext):
    def build_extension(self, ext):
        so_path = self.get_ext_fullpath(ext.name)
        print(f"  [atomic]   source        : {Path(ext.sources[0]).resolve()}")
        print(f"  [atomic]   output (.so)  : {Path(so_path).resolve()}")
        try:
            super().build_extension(ext)
            print(f"  [atomic]   ✅ built to   : {Path(so_path).resolve()}")
        except Exception as e:
            print(f"\n{'!'*60}")
            print(f"WARNING: OmniPkg Hardware Atomics failed to compile.")
            print(f"Reason: {e}")
            print(f"Installing successfully with Python-speed fallback.")
            print(f"{'!'*60}\n")
        finally:
            _print_exotic_platform_hint()

    def run(self):
        super().run()  # compiles ext_modules (empty list = no-op for noarch)
        if SKIP_C_EXTENSIONS:
            # Noarch wheel build — skip everything, pure Python only
            return
        # For platform wheel builds AND sdist installs: attempt dispatcher + ffi.
        # Both functions are silent no-ops if compiler/cargo/maturin are absent.
        try:
            _install_dispatcher_binary(Path(sys.executable).parent)
        except Exception as e:
            print(f"  [dispatcher] Skipping: {e}")
        try:
            _build_uv_ffi(Path(sys.executable).parent)
        except Exception as e:
            print(f"  [uv-ffi] Skipping: {e}")


if SKIP_C_EXTENSIONS:
    print("=" * 60)
    print("NOARCH BUILD MODE: Skipping C extension compilation")
    print("Pure Python fallback will be used for atomic operations")
    print("=" * 60)
    ext_modules = []
else:
    ext_modules = [atomic_extension]

cmdclass = {'build_ext': OptionalBuildExt}


# ── Combined install/develop commands (single definition) ────────────────────

class InstallWithDispatcher(install):
    def run(self):
        super().run()
        _install_dispatcher_binary(self.install_scripts)
        _build_uv_ffi(self.install_scripts)


class DevelopWithDispatcher(develop):
    def run(self):
        super().run()
        _install_dispatcher_binary(Path(sys.executable).parent)
        _build_uv_ffi(Path(sys.executable).parent)

# Force the wheel to be tagged as cp37-abi3 (only when building C ext;
# noarch wheel gets its own py3-none-any tag via OMNIPKG_SKIP_C_EXT=1)
if not SKIP_C_EXTENSIONS:
    try:
        from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
        class BdistWheelCommand(_bdist_wheel):
            def get_tag(self):
                python, abi, plat = super().get_tag()
                if python.startswith("cp"):
                    return "cp37", "abi3", plat
                return python, abi, plat
        cmdclass['bdist_wheel'] = BdistWheelCommand
    except ImportError:
        pass


cmdclass['install'] = InstallWithDispatcher
cmdclass['develop'] = DevelopWithDispatcher

setup(
    ext_modules=ext_modules,
    cmdclass=cmdclass,
)