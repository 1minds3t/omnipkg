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
            compiler_args = ["-O2", "-o", "{out}", "{src}", "-ldl"]
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
    binary_out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [compiler] + [
        a.format(out=str(binary_out), src=str(c_source))
        for a in compiler_args
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=compile_env)
        if result.returncode != 0:
            print(f"  [dispatcher] Compilation failed: {result.stderr[:200]}")
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
        else:
            print(f"  [dispatcher] Bundling compiled C dispatcher into the wheel.")

    except Exception as e:
        print(f"  [dispatcher] Skipping C dispatcher: {e}")

def _build_uv_ffi(install_dir=None):
    """
    Build the uv_ffi PyO3 extension via maturin and install the .so
    into the active environment. Silently skips if Rust/maturin not available.
    """
    crate = Path(__file__).parent / "src/omnipkg/_vendor/uv/crates/uv-ffi"
    if not crate.exists():
        print("  [uv-ffi] crate not found, skipping")
        return

    if not shutil.which("cargo"):
        print("  [uv-ffi] cargo not found — FFI unavailable, daemon path will be used")
        return

    if not shutil.which("maturin"):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "maturin", "-q"],
                           check=True)
        except Exception:
            print("  [uv-ffi] maturin not available — FFI unavailable, daemon path will be used")
            return

    try:
        result = subprocess.run(
            ["maturin", "develop", "--release", "--manifest-path", str(crate / "Cargo.toml")],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("  [uv-ffi] ✅ PyO3 FFI extension built and installed")
        else:
            print(f"  [uv-ffi] build failed — FFI unavailable, daemon path will be used")
            print(f"  {result.stderr[-500:].strip()}")
    except Exception as e:
        print(f"  [uv-ffi] Skipping: {e}")


# ── Atomic extension ─────────────────────────────────────────────────────────

if SKIP_C_EXTENSIONS:
    print("=" * 60)
    print("NOARCH BUILD MODE: Skipping C extension compilation")
    print("Pure Python fallback will be used for atomic operations")
    print("=" * 60)
    ext_modules = []
    cmdclass = {}
else:
    import platform

    _c_args = ["-O3"]

    machine = platform.machine().lower()
    system  = platform.system()

    if system == "Linux":
        # -march=native is UNSAFE in QEMU cross-compile: the host CPU feature
        # set leaks through QEMU into the container, producing march strings
        # (e.g. lse128+gcs) that the container's GCC doesn't know.
        # Use explicit safe baselines per architecture instead.
        if machine in ("x86_64", "i686", "i386"):
            _c_args.append("-march=x86-64")      # SSE2 baseline, safe everywhere
        elif machine == "aarch64":
            _c_args.append("-march=armv8-a")      # base AArch64, no exotic extensions
        elif machine == "armv7l":
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
        # 3. FIX: You MUST define the macro for the target Python version (3.7)
        define_macros=[('Py_LIMITED_API', '0x03070000')],
    )

    class OptionalBuildExt(build_ext):
        def build_extension(self, ext):
            try:
                super().build_extension(ext)
            except Exception as e:
                print(f"\n{'!'*60}")
                print(f"WARNING: OmniPkg Hardware Atomics failed to compile.")
                print(f"Reason: {e}")
                print(f"Installing successfully with Python-speed fallback.")
                print(f"{'!'*60}\n")
            finally:
                _print_exotic_platform_hint()

    def _print_exotic_platform_hint():
        import platform, sys
        machine = platform.machine().lower()
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
        print(f"  EXOTIC PLATFORM DETECTED: {platform.machine()} / musl")
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

# 4. FIX: Force the wheel to be tagged as cp37-abi3 (only when building C ext)
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