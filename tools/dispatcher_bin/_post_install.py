"""
_post_install.py — called by the custom build backend wrapper (build_hooks.py)
after pip finishes installing entry point scripts.

Can also be run manually:
    python tools/dispatcher_bin/_post_install.py
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path


def install_dispatcher_binary(install_dir: Path = None) -> bool:
    repo_root = Path(__file__).parent.parent.parent  # tools/dispatcher_bin/ -> repo root
    c_source = Path(__file__).parent / "dispatcher.c"

    if not c_source.exists():
        print(f"  [dispatcher] Source not found: {c_source}")
        return False

    if not shutil.which("gcc"):
        print("  [dispatcher] gcc not found — using Python dispatcher")
        return False

    if install_dir is None:
        install_dir = Path(sys.executable).parent

    binary_tmp = install_dir / "_omnipkg_dispatch_tmp"

    try:
        result = subprocess.run(
            ["gcc", "-O2", "-o", str(binary_tmp), str(c_source)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  [dispatcher] Compile failed — using Python dispatcher")
            print(f"  {result.stderr.strip()}")
            return False

        replaced = []
        for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
            target = install_dir / name
            if target.exists():
                shutil.copy2(str(binary_tmp), str(target))
                os.chmod(str(target), 0o755)
                replaced.append(name)

        binary_tmp.unlink()

        if replaced:
            print(f"  [dispatcher] ✅ C dispatcher installed → {', '.join(replaced)} in {install_dir}")
            return True
        else:
            print(f"  [dispatcher] No entry points found in {install_dir} — run after pip installs scripts")
            return False

    except Exception as e:
        if binary_tmp.exists():
            binary_tmp.unlink()
        print(f"  [dispatcher] Skipped: {e}")
        return False


if __name__ == "__main__":
    install_dispatcher_binary()