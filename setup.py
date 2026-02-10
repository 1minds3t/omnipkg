#!/usr/bin/env python
"""
Minimal setup.py bridge for Python 3.7 compatibility.
Python 3.7's pip doesn't support PEP 660 editable installs from pyproject.toml alone.
This file bridges to pyproject.toml for metadata while supporting legacy editable installs.
"""
from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
import os

# Check if we should skip C extensions (for noarch builds)
SKIP_C_EXTENSIONS = os.environ.get('OMNIPKG_SKIP_C_EXT', '0') == '1'

if SKIP_C_EXTENSIONS:
    print("=" * 60)
    print("NOARCH BUILD MODE: Skipping C extension compilation")
    print("Pure Python fallback will be used for atomic operations")
    print("=" * 60)
    ext_modules = []
    cmdclass = {}
else:
    # 1. Define the Optional Extension
    # We point to the source in src/
    atomic_extension = Extension(
        name="omnipkg.isolation.omnipkg_atomic",
        sources=["src/omnipkg/isolation/atomic_ops.c"],
        extra_compile_args=["-O3", "-march=native"],
        optional=True  # Tells setuptools: "If this fails, don't crash the install"
    )

    # 2. Custom Build Command for Graceful Failure
    # (Standard setuptools 'optional=True' handles most cases, but this adds user visibility)
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

# 3. Call setup()
# It will pull name, version, deps from pyproject.toml
setup(
    ext_modules=ext_modules,
    cmdclass=cmdclass,
)