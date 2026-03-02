from setuptools import setup, Extension

import platform

# 🚀 Smart Flag Selection
# Keep -march=native for Linux/Intel speed, but drop it for Apple Silicon/Windows
_c_args = ["-O3"]
if platform.system() != "Windows" and not (platform.system() == "Darwin" and platform.machine() == "arm64"):
    _c_args.append("-march=native")

module = Extension(
    'omnipkg.isolation.omnipkg_atomic',
    # 🔥 FIX 1: Point to the actual location in src/
    sources=['src/omnipkg/isolation/atomic_ops.c'],
    extra_compile_args=_c_args
)

setup(
    name='omnipkg_atomic',
    version='1.1',
    description='Hardware Atomics for OmniPkg',
    # 🔥 FIX 2: Tell setuptools that packages are under src/
    # This ensures the .so is built into src/omnipkg/isolation/
    package_dir={'': 'src'},
    ext_modules=[module]
)