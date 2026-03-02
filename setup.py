#!/usr/bin/env python
"""
Minimal setup.py bridge for Python 3.7 compatibility.
"""
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

    # Where to put the binary — same dir as the 8pkg wrapper script
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
            print(f"  {result.stderr.strip()}")
            return

        # Overwrite the pip-generated wrapper scripts
        for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
            target = Path(install_dir) / name
            if target.exists():
                shutil.copy2(str(binary_out), str(target))
                os.chmod(str(target), 0o755)

        binary_out.unlink()  # clean up temp, the copies are the real ones
        print(f"  [dispatcher] ✅ Fast C dispatcher installed in {install_dir}")

    except Exception as e:
        print(f"  [dispatcher] Skipping C dispatcher: {e}")


class InstallWithDispatcher(install):
    def run(self):
        super().run()
        _install_dispatcher_binary(self.install_scripts)


class DevelopWithDispatcher(develop):
    def run(self):
        super().run()
        # In editable installs, scripts go next to sys.executable
        _install_dispatcher_binary(Path(sys.executable).parent)


# ── Existing C extension setup (unchanged) ──────────────────────────────────

if SKIP_C_EXTENSIONS:
    print("=" * 60)
    print("NOARCH BUILD MODE: Skipping C extension compilation")
    print("Pure Python fallback will be used for atomic operations")
    print("=" * 60)
    ext_modules = []
    cmdclass = {}
else:
    import platform
    
    # Use hardware-specific instructions where supported
    # Linux: -march=native enables AVX/AVX2/Atom
    # macOS: -march=native fails on Universal (dual-arch) builds
    # Windows: Uses MSVC flags (handled separately)
    
    _c_args = ["-O3"]
    
    # Only enable native optimization on Linux
    if platform.system() == "Linux":
        _c_args.append("-march=native")

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

# Inject dispatcher commands (always, regardless of SKIP_C_EXTENSIONS)
cmdclass['install'] = InstallWithDispatcher
cmdclass['develop'] = DevelopWithDispatcher

setup(
    ext_modules=ext_modules,
    cmdclass=cmdclass,
)