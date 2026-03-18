"""
omnipkg/isolation/fs_lock_queue.py
═══════════════════════════════════════════════════════════════════════════════
Cross-process MPMC-safe filesystem operations queue for omnipkg cloak/uncloak.

THE PROBLEM THIS SOLVES
───────────────────────
loader.py and worker_daemon.py both mutate the same site-packages filesystem.
Their existing filelock usage is per-package but has three gaps:

  1. Emergency restore paths (panic_restore_cloaks, __exit__ ABI branch,
     _simple_restore_all_cloaks) call shutil.move() directly — bypassing the
     per-package FileLock entirely. Two processes can both try to rename the
     same directory simultaneously, causing FileNotFoundError or two
     half-complete moves that leave the FS in an inconsistent state.

  2. glob/os.scandir calls are not protected. Process A's rename puts the
     filesystem in a transient state (old name gone, new name not yet visible)
     that is window-observable by Process B's concurrent scan.

  3. Daemon workers pre-warm by calling omnipkgLoader.__init__ which caches
     _dependency_cache in-process. That snapshot becomes stale the moment any
     other process cloak/uncloaks. Workers then confidently try to move paths
     that no longer exist under their expected names.

THE FIX
───────
This module provides three things:

  A. FsOpLock — a thin wrapper that EVERY rename/move in the cloaking system
     must go through. It combines:
       • A per-package filelock (existing — serialises same-package ops)
       • A short-held global scan-guard filelock (new — prevents concurrent
         glob/scandir from observing mid-rename transient states)

  B. safe_cloak / safe_uncloak — drop-in replacements for the raw shutil.move
     calls scattered across loader.py. They acquire the right locks, handle
     the "already done by someone else" case gracefully (returning False rather
     than raising), and write to the invalidation sentinel on every mutation.

  C. DepCacheSentinel — a tiny file-based signal. Every call to safe_cloak or
     safe_uncloak touches this file. Every daemon worker checks it at the start
     of __enter__ and drops its in-process _dependency_cache if the mtime is
     newer than when the cache was built. No polling. No IPC. Just stat().

USAGE
─────
In loader.py, replace every bare shutil.move for cloak/uncloak with:

    from omnipkg.isolation.fs_lock_queue import safe_cloak, safe_uncloak

    # Instead of:
    shutil.move(str(bubble_path), str(cloak_path))

    # Use:
    ok = safe_cloak(bubble_path, cloak_path, locks_dir, pkg_name)
    if not ok:
        pass  # already cloaked by someone else — that's fine

    # Instead of:
    shutil.move(str(cloak_path), str(original_path))

    # Use:
    ok = safe_uncloak(cloak_path, original_path, locks_dir, pkg_name)
    # ok=False means another process already restored it — still fine

In omnipkgLoader.__enter__ (any path), add at the very top:

    from omnipkg.isolation.fs_lock_queue import DepCacheSentinel
    sentinel = DepCacheSentinel(self.multiversion_base)
    if sentinel.is_dirty_since(omnipkgLoader._dep_cache_built_at):
        omnipkgLoader._dependency_cache = None   # force re-detect

In every place that calls shutil.move for main-env cloaks
(_batch_cloak_packages, _restore_cloaked_modules, emergency paths in
__enter__ and __exit__), replace with safe_cloak / safe_uncloak.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

import filelock

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_SCAN_LOCK_NAME  = "_omnipkg_scan.lock"
_SENTINEL_NAME   = ".omnipkg_cache_dirty"

# Module-level cache of FileLock objects so we don't recreate them on every call.
_pkg_lock_cache: dict[str, filelock.FileLock] = {}
_scan_lock_cache: dict[str, filelock.FileLock] = {}


def _get_pkg_lock(locks_dir: Path, pkg_name: str) -> filelock.FileLock:
    canonical = pkg_name.lower().replace("-", "_")
    key = str(locks_dir / f"{canonical}.lock")
    if key not in _pkg_lock_cache:
        locks_dir.mkdir(parents=True, exist_ok=True)
        _pkg_lock_cache[key] = filelock.FileLock(key, timeout=10)
    return _pkg_lock_cache[key]


def _get_scan_lock(locks_dir: Path) -> filelock.FileLock:
    key = str(locks_dir / _SCAN_LOCK_NAME)
    if key not in _scan_lock_cache:
        locks_dir.mkdir(parents=True, exist_ok=True)
        _scan_lock_cache[key] = filelock.FileLock(key, timeout=3)
    return _scan_lock_cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# A. FsOpLock
# ─────────────────────────────────────────────────────────────────────────────

class FsOpLock:
    """
    Dual lock: holds the per-package lock AND the short scan-guard lock for
    the duration of a rename.  The scan-guard is held only during the actual
    os.rename call (microseconds), not for the entire with-block.

    Usage:
        with FsOpLock(locks_dir, "numpy") as guard:
            guard.rename(src, dst)
    """

    def __init__(self, locks_dir: Path, pkg_name: str, pkg_timeout: float = 10.0):
        self._pkg_lock  = _get_pkg_lock(locks_dir, pkg_name)
        self._scan_lock = _get_scan_lock(locks_dir)
        self._pkg_timeout = pkg_timeout

    def __enter__(self):
        try:
            self._pkg_lock.acquire(timeout=self._pkg_timeout)
        except filelock.Timeout:
            # Non-fatal: another process holds the lock. Caller checks return
            # value of safe_cloak / safe_uncloak to decide what to do.
            self._pkg_lock = None
        return self

    def __exit__(self, *_):
        if self._pkg_lock is not None:
            try:
                self._pkg_lock.release()
            except Exception:
                pass

    @property
    def acquired(self) -> bool:
        return self._pkg_lock is not None

    def rename(self, src: Path, dst: Path) -> bool:
        """
        Atomically rename src → dst under the scan guard.
        Returns True on success, False if src has vanished (another process
        already did this rename).
        """
        if not src.exists():
            return False  # already done by someone else
        try:
            with self._scan_lock.acquire(timeout=2):
                if not src.exists():
                    return False  # lost the race, but that's OK
                # os.rename is atomic on POSIX (same filesystem).
                # shutil.move falls back to copy+delete across filesystems.
                try:
                    os.rename(str(src), str(dst))
                except OSError:
                    # Cross-device or Windows — fall back to shutil
                    shutil.move(str(src), str(dst))
                return True
        except filelock.Timeout:
            # Scan lock unavailable — proceed anyway.  This is the safety
            # valve: if we can't get the scan lock, we still hold the pkg
            # lock, so at most we expose a transient FS state to a concurrent
            # scanner.  The scanner has retry logic, so this is tolerable.
            if not src.exists():
                return False
            try:
                os.rename(str(src), str(dst))
            except OSError:
                shutil.move(str(src), str(dst))
            return True


# ─────────────────────────────────────────────────────────────────────────────
# B. safe_cloak / safe_uncloak
# ─────────────────────────────────────────────────────────────────────────────

def safe_cloak(
    src: Path,
    dst: Path,
    locks_dir: Path,
    pkg_name: str,
    active_cloaks: Optional[dict] = None,
    owner_id: Optional[int] = None,
    sentinel_base: Optional[Path] = None,
    timeout: float = 10.0,
) -> bool:
    """
    Rename src → dst (cloak) under the dual lock.

    Returns True if the rename happened.
    Returns False if:
      - src doesn't exist (already cloaked by someone else — not an error)
      - lock could not be acquired within timeout

    If active_cloaks dict and owner_id are provided, registers the new cloak.
    If sentinel_base is provided, touches the cache-dirty sentinel.
    """
    with FsOpLock(locks_dir, pkg_name, pkg_timeout=timeout) as guard:
        if not guard.acquired:
            return False
        if not src.exists():
            return False
        ok = guard.rename(src, dst)
        if ok:
            if active_cloaks is not None and owner_id is not None:
                active_cloaks[str(dst)] = owner_id
            if sentinel_base is not None:
                DepCacheSentinel(sentinel_base).touch()
        return ok


def safe_uncloak(
    src: Path,  # the cloaked path (the .XYZ_omnipkg_cloaked name)
    dst: Path,  # the original path to restore
    locks_dir: Path,
    pkg_name: str,
    active_cloaks: Optional[dict] = None,
    sentinel_base: Optional[Path] = None,
    timeout: float = 10.0,
) -> bool:
    """
    Rename src → dst (uncloak / restore) under the dual lock.

    Returns True if the rename happened OR if src was already gone (meaning
    another process already restored it — caller should treat this as success).
    Returns False only if the lock timed out.

    If active_cloaks is provided, removes the entry on success.
    If sentinel_base is provided, touches the cache-dirty sentinel.
    """
    with FsOpLock(locks_dir, pkg_name, pkg_timeout=timeout) as guard:
        if not guard.acquired:
            return False
        if not src.exists():
            # Already restored by another process — unregister and report success.
            if active_cloaks is not None:
                active_cloaks.pop(str(src), None)
            return True
        # If destination already exists (shouldn't normally happen), remove it
        # so the rename doesn't fail.
        if dst.exists():
            try:
                if dst.is_dir():
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    dst.unlink(missing_ok=True)
            except Exception:
                pass
        ok = guard.rename(src, dst)
        if ok:
            if active_cloaks is not None:
                active_cloaks.pop(str(src), None)
            if sentinel_base is not None:
                DepCacheSentinel(sentinel_base).touch()
        return ok


# ─────────────────────────────────────────────────────────────────────────────
# C. DepCacheSentinel
# ─────────────────────────────────────────────────────────────────────────────

class DepCacheSentinel:
    """
    Tiny file-based invalidation signal for _dependency_cache.

    Every safe_cloak / safe_uncloak call touches this file.
    Daemon workers call is_dirty_since(ts) at the start of __enter__ and
    drop their in-process cache if it's stale.

    No polling. No IPC. One stat() call per __enter__.
    """

    def __init__(self, base_dir: Path):
        self._path = base_dir / _SENTINEL_NAME

    def touch(self):
        """Mark the cache as dirty. Called after every FS mutation."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        except Exception:
            pass

    def mtime(self) -> float:
        """Return the sentinel's mtime, or 0.0 if it doesn't exist."""
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0

    def is_dirty_since(self, cache_built_at: float) -> bool:
        """
        Returns True if the sentinel was touched after cache_built_at,
        meaning some process mutated the FS since the cache was populated.
        """
        return self.mtime() > cache_built_at


# ─────────────────────────────────────────────────────────────────────────────
# D. Protected scan helper
# ─────────────────────────────────────────────────────────────────────────────

def scan_for_cloaks(directory: Path, pattern: str, locks_dir: Path) -> list[Path]:
    """
    Scan directory for entries matching glob pattern, holding the scan lock
    so the results are not observed mid-rename by another process.

    Drop-in for: list(directory.glob(pattern))
    or:          [Path(e.path) for e in os.scandir(str(directory)) if ...]
    """
    scan_lock = _get_scan_lock(locks_dir)
    try:
        with scan_lock.acquire(timeout=2):
            return list(directory.glob(pattern))
    except filelock.Timeout:
        # Couldn't get the lock — proceed anyway. Worst case is a transient
        # inconsistency that the caller's retry/fallback logic handles.
        return list(directory.glob(pattern))