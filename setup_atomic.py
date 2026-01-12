from setuptools import setup, Extension

module = Extension(
    'omnipkg.isolation.omnipkg_atomic',
    # 🔥 FIX 1: Point to the actual location in src/
    sources=['src/omnipkg/isolation/atomic_ops.c'],
    extra_compile_args=['-O3', '-march=native']
)

setup(
    name='omnipkg_atomic',
    version='1.0',
    description='Hardware Atomics for OmniPkg',
    # 🔥 FIX 2: Tell setuptools that packages are under src/
    # This ensures the .so is built into src/omnipkg/isolation/
    package_dir={'': 'src'},
    ext_modules=[module]
)