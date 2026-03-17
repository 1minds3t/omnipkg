from setuptools import setup, Extension

import platform

# 🚀 Smart Flag Selection
# Only enable -march=native on Linux to avoid Universal build crashes on macOS
_c_args = ["-O3"]
if platform.system() == "Linux":
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