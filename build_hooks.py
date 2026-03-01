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


def _run_post_install():
    hook = Path(__file__).parent / "tools" / "dispatcher_bin" / "_post_install.py"
    if hook.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_post_install", hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.install_dispatcher_binary(Path(sys.executable).parent)


# Override build_editable — this is what `pip install -e .` calls
def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools.build_meta import build_editable as _build_editable
    result = _build_editable(wheel_directory, config_settings, metadata_directory)
    _run_post_install()
    _heal_entrypoints()  # add this
    return result

def _heal_entrypoints():
    """Ensure all entry point scripts exist after editable install."""
    import shutil
    bin_dir = Path(sys.executable).parent
    base = bin_dir / "omnipkg"
    if not base.exists():
        return
    for name in ["8pkg", "OMNIPKG", "8PKG"]:
        target = bin_dir / name
        if not target.exists():
            shutil.copy2(str(base), str(target))
            print(f"  [build_hooks] healed missing entrypoint: {name}")

# Override build_wheel — this is what `pip install .` (non-editable) calls
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools.build_meta import build_wheel as _build_wheel
    result = _build_wheel(wheel_directory, config_settings, metadata_directory)
    _run_post_install()
    return result