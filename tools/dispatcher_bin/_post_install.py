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
            capture_output=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            print(f"  [dispatcher] Compile failed — using Python dispatcher")
            print(f"  {result.stderr.strip()}")
            return False

        import time

        print(f"  [dispatcher] install_dir={install_dir}")
        print(f"  [dispatcher] files in dir: {[f.name for f in install_dir.iterdir() if 'pkg' in f.name.lower() or 'omnipkg' in f.name.lower()]}")
        for attempt in range(10):
            replaced = []
            for name in ("8pkg", "omnipkg", "OMNIPKG", "8PKG"):
                target = install_dir / name
                if target.exists():
                    shutil.copy2(str(binary_tmp), str(target))
                    os.chmod(str(target), 0o755)
                    replaced.append(name)
            if replaced:
                break
            time.sleep(0.5)

        binary_tmp.unlink()
        # After the binary is installed, pre-create all versioned shims
        ALL_VERSIONS = ["37","38","39","310","311","312","313","314","315"]
        for flat in ALL_VERSIONS:
            for base in ("8pkg", "omnipkg"):
                src = install_dir / base
                if not src.exists():
                    continue
                if sys.platform == "win32":
                    link = install_dir / f"{base}{flat}.bat"
                    maj, min_ = flat[0], flat[1:]
                    link.write_text(f'@echo off\r\n"{src}" --python {maj}.{min_} %*\r\n', encoding="ascii")
                else:
                    link = install_dir / f"{base}{flat}"
                    if link.exists() or link.is_symlink():
                        link.unlink()
                    link.symlink_to(src.name)
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