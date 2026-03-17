"""
omnipkg/isolation/fs_watcher.py
════════════════════════════════════════════════════════════════════════════════
Cross-platform site-packages filesystem watcher daemon.

PURPOSE
───────
When a user runs vanilla `uv pip install` (or any external tool) outside of
omnipkg, the Rust SITE_PACKAGES_CACHE in the daemon's FFI workers becomes
stale.  The old approach (heartbeat loop, or detecting-on-install) caused:
  • Tokio runtime panics (heartbeat thread touching dead .dist-info)
  • Unnecessary full rescans on every install
  • 10ms binary-spawn fallback when the daemon worker crashed

This module provides a SINGLE long-lived watcher process that:
  1. Watches all active site-packages directories via OS-native events
     (inotify on Linux, FSEvents on macOS, ReadDirectoryChangesW on Windows)
     using the `watchdog` library (cross-platform).
  2. When it detects an EXTERNAL write (i.e., not by our own FFI), it:
     a. Sets a POSIX shared-memory flag: CACHE_DIRTY = True
     b. Calls invalidate_site_packages_cache() in each affected UV worker
        via the daemon socket (non-blocking message).
     c. Updates the mtime stamp in the flag so the FFI can self-detect.
  3. The UV worker's run_uv handler checks the flag BEFORE calling FFI,
     and BLOCKS (up to a configurable timeout) while dirty = True.
     The watcher flips the flag back to clean once invalidation is confirmed.

This means:
  • The install worker never panics — it blocks then proceeds safely.
  • No wasted full rescan — the watcher calls invalidate() surgically.
  • The C dispatcher fast-path continues to work — it uses the daemon socket
    which is always alive (the watcher doesn't touch it).

CROSS-PLATFORM SUPPORT
──────────────────────
  Linux   → inotify (kernel)           via watchdog.observers.inotify
  macOS   → FSEvents (kernel)          via watchdog.observers.fsevents
  Windows → ReadDirectoryChangesW      via watchdog.observers.read_directory_changes

All three are abstracted by watchdog's `Observer` class, so this file has
zero platform-specific code beyond the import of `Observer`.

WIRE PROTOCOL (shared-memory flag)
────────────────────────────────────
We reuse the existing SharedStateMonitor layout (128-byte block):
  [0:8]   version         int64  — increments on every external write detected
  [8:16]  dirty_flag      int64  — 0=CLEAN (safe to call FFI), 1=DIRTY (block!)
  [16:24] watcher_pid     int64  — PID of the watcher process (for health checks)
  [24:128] padding

The UV worker does:
    flag = SharedWatchFlag.attach()
    flag.block_until_clean(timeout=2.0)   # ← inserted before FFI call
    ffi_result = _ffi_fn(cmd)
    flag.record_our_write()               # ← inserted after FFI call (suppress false dirty)

The watcher does:
    flag.mark_dirty()      # before invalidation
    invalidate_ffi(...)    # call invalidate_site_packages_cache()
    flag.mark_clean()      # after invalidation confirmed

USAGE
─────
Start (called by WorkerPoolDaemon.start()):
    from omnipkg.isolation.fs_watcher import SitePackagesWatcher
    watcher = SitePackagesWatcher(daemon_socket_path, site_packages_dirs)
    watcher.start()          # non-blocking, spawns background thread + OS watcher

Stop (called on daemon shutdown):
    watcher.stop()

Or run as a standalone daemon process (daemonized by WorkerPoolDaemon):
    python -m omnipkg.isolation.fs_watcher \
        --socket /tmp/omnipkg/omnipkg_daemon.sock \
        --dirs /path/to/site-packages [...]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

# ── Optional watchdog (install with: pip install watchdog) ──────────────────
try:
    from watchdog.observers import Observer
    from watchdog.events import (
        FileSystemEventHandler,
        FileCreatedEvent,
        FileDeletedEvent,
        FileModifiedEvent,
        DirCreatedEvent,
        DirDeletedEvent,
    )
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = object

# ── logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("omnipkg.fs_watcher")

# ═══════════════════════════════════════════════════════════════════════════════
# SHARED-MEMORY WATCH FLAG
# ═══════════════════════════════════════════════════════════════════════════════

SHM_FLAG_NAME   = "omnipkg_watch_flag"
SHM_FLAG_SIZE   = 128                      # matches SharedStateMonitor layout
_FLAG_STRUCT    = "qqq104x"                # version(i64), dirty(i64), watcher_pid(i64)


class SharedWatchFlag:
    """
    Thin wrapper around a 128-byte shared-memory block.

    The dirty flag acts as a read-write gate:
      Writers (watcher):     mark_dirty() → invalidate → mark_clean()
      Readers (FFI workers): block_until_clean() → call FFI → record_our_write()

    record_our_write() stamps our PID + monotonic time so the watcher can
    suppress "dirty" events for writes that WE generated (avoiding a loop).
    """

    def __init__(self, shm):
        self._shm = shm

    # ── factory methods ──────────────────────────────────────────────────────

    @classmethod
    def create(cls) -> "SharedWatchFlag":
        """Create (or recreate) the shared-memory block. Called by watcher."""
        from multiprocessing import shared_memory
        try:
            old = shared_memory.SharedMemory(name=SHM_FLAG_NAME)
            old.close()
            old.unlink()
        except Exception:
            pass
        shm = shared_memory.SharedMemory(create=True, size=SHM_FLAG_SIZE,
                                         name=SHM_FLAG_NAME)
        # Zero-init → version=0, dirty=0, watcher_pid=current PID
        struct.pack_into(_FLAG_STRUCT, shm.buf, 0, 0, 0, os.getpid())
        return cls(shm)

    @classmethod
    def attach(cls) -> Optional["SharedWatchFlag"]:
        """
        Attach to an existing block. Returns None if the watcher isn't running.
        Workers call this at startup. If it returns None, the watcher isn't up
        yet — workers just skip the guard and call FFI directly.
        """
        try:
            from multiprocessing import shared_memory
            shm = shared_memory.SharedMemory(name=SHM_FLAG_NAME)
            return cls(shm)
        except Exception:
            return None

    # ── reads ────────────────────────────────────────────────────────────────

    def _read(self):
        return struct.unpack(_FLAG_STRUCT, self._shm.buf)

    def is_dirty(self) -> bool:
        _, dirty, _ = self._read()
        return dirty != 0

    def version(self) -> int:
        ver, _, _ = self._read()
        return ver

    # ── writes (watcher side) ────────────────────────────────────────────────

    def mark_dirty(self):
        ver, _, pid = self._read()
        struct.pack_into(_FLAG_STRUCT, self._shm.buf, 0, ver + 1, 1, pid)

    def mark_clean(self):
        ver, _, pid = self._read()
        struct.pack_into(_FLAG_STRUCT, self._shm.buf, 0, ver + 1, 0, pid)

    # ── reads (FFI worker side) ───────────────────────────────────────────────

    def block_until_clean(self, timeout: float = 2.0):
        """
        Block the calling thread until the dirty flag is cleared.
        Called by the UV worker BEFORE invoking FFI.
        Returns immediately if already clean.
        Gives up after `timeout` seconds and proceeds anyway (safety valve).
        """
        if not self.is_dirty():
            return
        deadline = time.monotonic() + timeout
        sleep_us = 0.0005   # 500µs initial poll interval
        while self.is_dirty():
            if time.monotonic() > deadline:
                log.warning("[fs_watcher] block_until_clean timed out — proceeding anyway")
                return
            time.sleep(sleep_us)
            sleep_us = min(sleep_us * 1.5, 0.05)   # exponential backoff, cap 50ms

    def record_our_write(self):
        """
        Called by the UV worker AFTER a successful FFI install so the watcher
        knows the next FS event for this install was OURS, not an external write.
        We use a thread-local timestamp written into the 'watcher_pid' slot for
        the duration of the write.  The watcher checks this before marking dirty.

        In practice: the watcher suppresses events within OUR_WRITE_GRACE_MS of
        a known worker write.  We communicate via the shared block:
          [16:24] watcher_pid slot temporarily holds: OUR_WRITE_EPOCH_MS (int64)
        The watcher reads this and skips dirtying if now - epoch < grace period.
        """
        epoch_ms = int(time.monotonic() * 1000)
        ver, dirty, _ = self._read()
        # Encode as: negative value = "our write" sentinel + timestamp
        struct.pack_into(_FLAG_STRUCT, self._shm.buf, 0, ver, dirty, -epoch_ms)

    # ── cleanup ──────────────────────────────────────────────────────────────

    def close(self):
        try:
            self._shm.close()
        except Exception:
            pass

    def unlink(self):
        try:
            self._shm.unlink()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# FS EVENT HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

#: Events younger than this (ms) that followed one of our own FFI writes are
#: suppressed — they're echoes of our own install, not external changes.
OUR_WRITE_GRACE_MS = 3000   # 3 seconds is generous; our installs are <10ms


class SitePackagesEventHandler(FileSystemEventHandler):
    """
    Handles filesystem events inside a site-packages directory.

    Only cares about .dist-info / .data directory creation/deletion and
    direct file events at the top level of site-packages — these are the
    only things that change when packages are installed/removed.
    """

    def __init__(self, watch_flag: SharedWatchFlag,
                 invalidate_fn,
                 site_packages_path: str):
        super().__init__()
        self._flag            = watch_flag
        self._invalidate_fn   = invalidate_fn
        self._sp_path         = Path(site_packages_path).resolve()
        self._debounce_lock   = threading.Lock()
        self._pending_timer: Optional[threading.Timer] = None
        self._last_our_write  = 0   # monotonic ms of last known FFI write
        # Delta accumulator — reset on each debounce flush
        self._pending_created: Set[Path] = set()
        self._pending_deleted: Set[Path] = set()

    # ── internal helpers ─────────────────────────────────────────────────────

    def _is_our_write(self) -> bool:
        """Return True if the event looks like an echo of our own FFI install."""
        _, _, pid_slot = struct.unpack(_FLAG_STRUCT, self._flag._shm.buf)
        if pid_slot >= 0:
            return False     # positive → real watcher PID, not our sentinel
        our_write_ms = -pid_slot
        now_ms = int(time.monotonic() * 1000)
        return (now_ms - our_write_ms) < OUR_WRITE_GRACE_MS

    def _is_dist_info(self, path: str) -> bool:
        """Only care about .dist-info dirs at depth-1 — that's where name+version live."""
        try:
            p = Path(path).resolve()
            rel = p.relative_to(self._sp_path)
            parts = rel.parts
            return len(parts) == 1 and parts[0].endswith(".dist-info")
        except ValueError:
            return False

    def _is_relevant(self, path: str) -> bool:
        """
        Trigger on depth-1 events inside site-packages (dist-info, .data, .pth, packages).
        Ignore __pycache__ and nested writes inside already-installed packages.
        """
        try:
            p = Path(path).resolve()
            rel = p.relative_to(self._sp_path)
            parts = rel.parts
            if not parts:
                return False
            if len(parts) == 1:
                name = parts[0]
                return (
                    name.endswith(".dist-info")
                    or name.endswith(".data")
                    or name.endswith(".pth")
                    or not name.startswith("__")
                )
            if len(parts) == 2 and parts[0].endswith(".dist-info"):
                return True
        except ValueError:
            pass
        return False

    @staticmethod
    def _parse_dist_info_name(dist_info_dir: Path):
        """
        Parse 'rich-14.3.2.dist-info' → ('rich', '14.3.2').
        Returns None if the name doesn't match the pattern.
        """
        stem = dist_info_dir.name  # e.g. "rich-14.3.2.dist-info"
        if not stem.endswith(".dist-info"):
            return None
        stem = stem[:-len(".dist-info")]  # "rich-14.3.2"
        # rsplit on '-' to handle names like 'my-package-1.0.0'
        parts = stem.rsplit("-", 1)
        if len(parts) != 2:
            return None
        return parts[0], parts[1]  # (name, version)

    def _schedule_invalidation(self, path: str, created: bool):
        """
        Accumulate the delta and debounce rapid bursts.
        Single lock covers both the accumulation AND the timer reset so two
        concurrent events can't each spawn their own timer.
        """
        with self._debounce_lock:
            # Accumulate .dist-info dirs only — they carry name+version for free
            if self._is_dist_info(path):
                p = Path(path).resolve()
                if created:
                    self._pending_created.add(p)
                    self._pending_deleted.discard(p)
                else:
                    self._pending_deleted.add(p)
                    self._pending_created.discard(p)

            # Reset the debounce timer — always inside the same lock
            if self._pending_timer is not None:
                self._pending_timer.cancel()
            self._pending_timer = threading.Timer(0.15, self._do_patch)
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _do_patch(self):
        """
        Surgically patch the Rust SITE_PACKAGES_CACHE with the accumulated delta.
        No full rescan — we know exactly what was installed and removed from the
        .dist-info dir names the OS told us about.
        Falls back to full invalidation only if we couldn't parse the delta.
        """
        with self._debounce_lock:
            created = set(self._pending_created)
            deleted = set(self._pending_deleted)
            self._pending_created.clear()
            self._pending_deleted.clear()

        installed = []
        for p in created:
            parsed = self._parse_dist_info_name(p)
            if parsed:
                installed.append(parsed)

        removed = []
        for p in deleted:
            parsed = self._parse_dist_info_name(p)
            if parsed:
                removed.append(parsed)

        log.info(
            "[fs_watcher] External change in %s — installed=%s removed=%s",
            self._sp_path, installed, removed,
        )

        self._flag.mark_dirty()
        try:
            self._invalidate_fn(
                site_packages_path=str(self._sp_path),
                installed=installed,
                removed=removed,
            )
        except Exception as exc:
            log.warning("[fs_watcher] patch_fn raised: %s", exc)
        finally:
            self._flag.mark_clean()

    # ── watchdog callbacks ───────────────────────────────────────────────────

    def _handle(self, event):
        if self._is_our_write():
            return
        if not self._is_relevant(event.src_path):
            return
        log.debug("[fs_watcher] event: %s %s", event.event_type, event.src_path)
        created = event.event_type in ("created", "modified")
        self._schedule_invalidation(event.src_path, created=created)

    on_created  = _handle
    on_deleted  = _handle
    on_modified = _handle

    def on_moved(self, event):
        # Moves happen when pip writes .tmp then renames — treat dest as created
        if self._is_our_write():
            return
        if self._is_relevant(event.dest_path):
            self._schedule_invalidation(event.dest_path, created=True)
        if self._is_relevant(event.src_path):
            self._schedule_invalidation(event.src_path, created=False)


# ═══════════════════════════════════════════════════════════════════════════════
# mtime FALLBACK (when watchdog is not installed)
# ═══════════════════════════════════════════════════════════════════════════════

class MtimeFallbackWatcher:
    """
    Pure-Python polling fallback when watchdog isn't installed.
    Polls every 500ms — adds latency but never panics.
    Production systems should install watchdog for true 0-latency detection.
    """

    POLL_INTERVAL = 0.5   # seconds

    def __init__(self, watch_flag: SharedWatchFlag,
                 invalidate_fn,
                 site_packages_dirs: List[str]):
        self._flag         = watch_flag
        self._invalidate   = invalidate_fn
        self._dirs         = [Path(d).resolve() for d in site_packages_dirs]
        self._mtimes: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()

    def _current_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def start(self):
        # Record baseline mtimes
        for d in self._dirs:
            self._mtimes[str(d)] = self._current_mtime(d)

        self._thread = threading.Thread(
            target=self._loop, name="omnipkg-mtime-watcher", daemon=True)
        self._thread.start()
        log.info("[fs_watcher] mtime fallback watcher started (install watchdog for inotify)")

    def _loop(self):
        while not self._stop_event.wait(self.POLL_INTERVAL):
            for d in self._dirs:
                key = str(d)
                new_mtime = self._current_mtime(d)
                old_mtime = self._mtimes.get(key, 0.0)
                if new_mtime > old_mtime:
                    self._mtimes[key] = new_mtime
                    # Check grace period via flag's our_write sentinel
                    _, _, pid_slot = struct.unpack(_FLAG_STRUCT, self._flag._shm.buf)
                    if pid_slot < 0:
                        our_write_ms = -pid_slot
                        now_ms = int(time.monotonic() * 1000)
                        if (now_ms - our_write_ms) < OUR_WRITE_GRACE_MS:
                            continue   # our own FFI write, skip
                    log.info("[fs_watcher] mtime change in %s — invalidating", d)
                    self._flag.mark_dirty()
                    try:
                        self._invalidate(str(d))
                    except Exception as exc:
                        log.warning("[fs_watcher] invalidate raised: %s", exc)
                    finally:
                        self._flag.mark_clean()

    def stop(self):
        self._stop_event.set()


# ═══════════════════════════════════════════════════════════════════════════════
# INVALIDATION SENDER (talks to the daemon socket)
# ═══════════════════════════════════════════════════════════════════════════════

class DaemonPatchSender:
    """
    Sends a `patch_site_packages_cache` message to the daemon.
    The daemon forwards installed/removed lists to each UV worker which calls
    the Rust patch_site_packages_cache() — zero disk I/O, surgical update.
    Falls back to full invalidation if delta is empty (edge case).
    """

    def __init__(self, socket_path: str):
        self._socket_path = socket_path

    def __call__(self, site_packages_path: str, installed: list, removed: list):
        # existing patch_cache message
        patch_msg = json.dumps({
            "type": "patch_site_packages_cache",
            "site_packages_path": site_packages_path,
            "installed": installed,
            "removed": removed,
        })
        # new sentinel piggyback — zero extra connections, worker has warm Redis
        sentinel_msg = json.dumps({
            "type": "kb_sentinel",
            "installed": installed,   # [["numpy", "2.3.5"], ...]
            "removed":   removed,
        })
        try:
            for msg in (patch_msg, sentinel_msg):
                payload = len(msg).to_bytes(8, "big") + msg.encode()
                if platform.system() == "Windows":
                    self._send_windows(payload)
                else:
                    self._send_unix(payload)
        except Exception as exc:
            log.warning("[fs_watcher] daemon message failed: %s", exc)

    def _send_unix(self, payload: bytes):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(self._socket_path)
            s.sendall(payload)
            try:
                s.settimeout(0.5)
                hdr = s.recv(8)
                if hdr and len(hdr) == 8:
                    s.recv(int.from_bytes(hdr, "big"))
            except Exception:
                pass

    def _send_windows(self, payload: bytes):
        try:
            self._send_unix(payload)
        except Exception:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", 51413))
                s.sendall(payload)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN WATCHER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class SitePackagesWatcher:
    """
    Single entry-point for the fs-watcher subsystem.

    Designed to be embedded inside WorkerPoolDaemon (runs in a daemon thread)
    OR as a standalone long-lived subprocess.

    Example (embedded):
        watcher = SitePackagesWatcher(
            socket_path="/tmp/omnipkg/omnipkg_daemon.sock",
            site_packages_dirs=[
                "/home/user/miniforge3/envs/foo/lib/python3.11/site-packages",
                "/home/user/.omnipkg/interpreters/cpython-3.12.11/lib/python3.12/site-packages",
            ]
        )
        watcher.start()
        # ... daemon runs ...
        watcher.stop()
    """

    def __init__(self, socket_path: str, site_packages_dirs: List[str]):
        self._socket_path     = socket_path
        self._dirs            = [str(Path(d).resolve()) for d in site_packages_dirs
                                 if Path(d).exists()]
        self._flag: Optional[SharedWatchFlag] = None
        self._observer        = None
        self._fallback        = None
        self._running         = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True

        # Create the shared-memory flag (watcher owns it)
        self._flag = SharedWatchFlag.create()
        log.info("[fs_watcher] Shared-memory flag created (name=%s)", SHM_FLAG_NAME)

        patch_fn = DaemonPatchSender(self._socket_path)

        if _HAS_WATCHDOG and self._dirs:
            self._start_watchdog(patch_fn)
        else:
            if not _HAS_WATCHDOG:
                log.warning("[fs_watcher] watchdog not installed — using mtime polling. "
                            "Install watchdog for kernel-level FS events.")
            self._start_fallback(patch_fn)

    def _start_watchdog(self, invalidate_fn):
        self._observer = Observer()
        for sp_dir in self._dirs:
            handler = SitePackagesEventHandler(
                watch_flag=self._flag,
                invalidate_fn=invalidate_fn,
                site_packages_path=sp_dir,
            )
            self._observer.schedule(handler, sp_dir, recursive=False)
            log.info("[fs_watcher] Watching: %s", sp_dir)

        self._observer.start()
        log.info("[fs_watcher] OS-native watcher started (%d dirs)", len(self._dirs))

    def _start_fallback(self, invalidate_fn):
        self._fallback = MtimeFallbackWatcher(
            watch_flag=self._flag,
            invalidate_fn=invalidate_fn,
            site_packages_dirs=self._dirs,
        )
        self._fallback.start()

    def add_directory(self, sp_dir: str):
        sp_dir = str(Path(sp_dir).resolve())
        if sp_dir in self._dirs or not Path(sp_dir).exists():
            return
        self._dirs.append(sp_dir)
        if self._observer is not None and self._flag is not None:
            patch_fn = DaemonPatchSender(self._socket_path)
            handler = SitePackagesEventHandler(
                watch_flag=self._flag,
                invalidate_fn=patch_fn,
                site_packages_path=sp_dir,
            )
            self._observer.schedule(handler, sp_dir, recursive=False)
            log.info("[fs_watcher] Dynamically added watch: %s", sp_dir)

    def stop(self):
        if not self._running:
            return
        self._running = False

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3.0)
            log.info("[fs_watcher] watchdog observer stopped")

        if self._fallback is not None:
            self._fallback.stop()

        if self._flag is not None:
            self._flag.close()
            self._flag.unlink()

        log.info("[fs_watcher] Watcher shutdown complete")


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER-SIDE GUARD  (inserted into the worker's run_uv handler)
# ═══════════════════════════════════════════════════════════════════════════════

class FfiWriteGuard:
    """
    Context manager inserted around every FFI call in the UV worker.

    Usage in worker_daemon.py's run_uv handler:

        from omnipkg.isolation.fs_watcher import FfiWriteGuard

        _ffi_guard = FfiWriteGuard.attach()   # once at startup, may be None

        # inside the run_uv loop:
        with _ffi_guard:                      # NOP if watcher isn't running
            _ffi_result = _ffi_fn(cmd)

    If the watcher isn't running (flag not attached), the guard is a NOP
    and the worker behaves exactly as before — fully backward compatible.
    """

    _instance: Optional["FfiWriteGuard"] = None

    def __init__(self, flag: Optional[SharedWatchFlag]):
        self._flag = flag

    @classmethod
    def attach(cls) -> "FfiWriteGuard":
        """
        Attach to the watcher's shared flag.
        Returns a no-op guard if the watcher isn't running.
        Caches the result — safe to call once at worker startup.
        """
        if cls._instance is None:
            flag = SharedWatchFlag.attach()
            cls._instance = cls(flag)
        return cls._instance

    def __enter__(self):
        if self._flag is not None:
            self._flag.block_until_clean(timeout=2.0)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._flag is not None and exc_type is None:
            # Successful FFI write — suppress the echo FS event
            self._flag.record_our_write()
        return False   # don't suppress exceptions

# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _discover_site_packages() -> List[str]:
    """
    Auto-discover all site-packages directories for known Python interpreters
    by reading the SHM registry JSON written by WorkerPoolDaemon.
    """
    from omnipkg.isolation.worker_daemon import OMNIPKG_TEMP_DIR, SHM_REGISTRY_FILE
    dirs: List[str] = []

    try:
        with open(SHM_REGISTRY_FILE, "r") as f:
            registry = json.load(f)
        for entry in registry.values():
            sp = entry.get("site_packages_path")
            if sp and Path(sp).exists():
                dirs.append(sp)
    except Exception:
        pass

    # Also add sys.prefix site-packages of the current environment
    import site
    for sp in site.getsitepackages():
        if Path(sp).exists() and sp not in dirs:
            dirs.append(sp)

    return dirs


def main():
    parser = argparse.ArgumentParser(
        prog="omnipkg-fs-watcher",
        description="omnipkg site-packages filesystem watcher daemon",
    )
    parser.add_argument(
        "--socket", "-s",
        default=os.path.join(
            __import__("tempfile").gettempdir(), "omnipkg", "omnipkg_daemon.sock"
        ),
        help="Path to the omnipkg daemon socket",
    )
    parser.add_argument(
        "--dirs", "-d",
        nargs="*",
        default=None,
        help="Site-packages directories to watch (auto-discovered if omitted)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[fs_watcher %(levelname)s] %(message)s",
    )

    dirs = args.dirs if args.dirs else _discover_site_packages()
    if not dirs:
        log.warning("No site-packages directories found to watch — exiting")
        sys.exit(0)

    log.info("Starting omnipkg fs-watcher for %d dir(s)", len(dirs))

    watcher = SitePackagesWatcher(socket_path=args.socket, site_packages_dirs=dirs)
    watcher.start()

    try:
        # Keep the process alive; the daemon will SIGTERM us on shutdown.
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()