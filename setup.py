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
from pathlib import Path

SKIP_C_EXTENSIONS = os.environ.get('OMNIPKG_SKIP_C_EXT', '0') == '1'

# ── Dispatcher binary install (shared by both install and develop commands) ──

def _install_dispatcher_binary(install_dir=None):
    """
    Compile the C dispatcher and overwrite the pip-generated 8pkg/omnipkg
    wrapper scripts with the fast binary.
    Silently skips if gcc isn't available or compilation fails.
    """
    repo_root = Path(__file__).parent
    c_source = repo_root / "tools" / "dispatcher_bin" / "dispatcher.c"

    if not c_source.exists():
        print("  [dispatcher] No C source found, skipping binary install")
        return

    if not shutil.which("gcc"):
        print("  [dispatcher] gcc not found, skipping binary install (Python dispatcher will be used)")
        return

    if install_dir is None:
        install_dir = Path(sys.executable).parent  # $VENV/bin

    binary_out = Path(install_dir) / "_omnipkg_dispatch_bin"

    try:
        result = subprocess.run(
            ["gcc", "-O2", "-o", str(binary_out), str(c_source)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  [dispatcher] Compilation failed, using Python dispatcher:")
            subprocess.run(
                ["gcc", "-v", "-O2", "-o", str(binary_out), str(c_source)],
                capture_output=False,
                text=True
            )
            return

        import time
        print(f"  [dispatcher] Looking for scripts in: {install_dir}")
        print(f"  [dispatcher] Files in dir: {list(Path(install_dir).glob('*pkg*'))}")
        for attempt in range(10):
            replaced = []
            for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
                target = Path(install_dir) / name
                if target.exists():
                    shutil.copy2(str(binary_out), str(target))
                    os.chmod(str(target), 0o755)
                    replaced.append(name)
            if replaced:
                break
            time.sleep(0.5)

        binary_out.unlink()
        if replaced:
            print(f"  [dispatcher] ✅ Fast C dispatcher installed in {install_dir}")
        else:
            print(f"  [dispatcher] No entry points found in {install_dir} — run after pip installs scripts")

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


cmdclass['install'] = InstallWithDispatcher
cmdclass['develop'] = DevelopWithDispatcher

setup(
    ext_modules=ext_modules,
    cmdclass=cmdclass,
)