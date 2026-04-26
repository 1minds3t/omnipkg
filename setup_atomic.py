from setuptools import setup, Extension
import platform

_c_args = ["-O3"]

machine = platform.machine().lower()
system  = platform.system()

if system == "Linux":
    # Safe explicit baselines — do NOT use -march=native under QEMU cross-compile.
    # The host CPU feature set leaks through QEMU and produces arch strings
    # (e.g. lse128+gcs) that the container GCC may not recognise.
    if machine in ("x86_64", "i686", "i386"):
        _c_args.append("-march=x86-64")
    elif machine == "aarch64":
        _c_args.append("-march=armv8-a")
    elif machine == "armv7l":
        _c_args.append("-march=armv7-a")
    # ppc64le / s390x / riscv64: let GCC pick its own default

module = Extension(
    'omnipkg.isolation.omnipkg_atomic',
    sources=['src/omnipkg/isolation/atomic_ops.c'],
    extra_compile_args=_c_args
)

setup(
    name='omnipkg_atomic',
    version='1.1',
    description='Hardware Atomics for OmniPkg',
    package_dir={'': 'src'},
    ext_modules=[module]
)