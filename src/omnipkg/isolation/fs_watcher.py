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
import re
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from omnipkg.common_utils import _is_relative_to_win, _relative_to_win
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

        try:
            shm = shared_memory.SharedMemory(create=True, size=SHM_FLAG_SIZE,
                                             name=SHM_FLAG_NAME)
            # Zero-init → version=0, dirty=0, watcher_pid=current PID
            struct.pack_into(_FLAG_STRUCT, shm.buf, 0, 0, 0, os.getpid())
        except Exception:  # 🔥 Catch ALL errors here (WinError 183, OSError, etc.)
            # WINDOWS FIX: If workers are still holding handles, unlink fails silently.
            # We just attach to the existing block and reset it.
            shm = shared_memory.SharedMemory(create=False, name=SHM_FLAG_NAME)
            ver, _, _ = struct.unpack_from(_FLAG_STRUCT, shm.buf, 0)
            # Reset dirty=0, update watcher_pid=current PID, keep existing version
            struct.pack_into(_FLAG_STRUCT, shm.buf, 0, ver, 0, os.getpid())

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
        return struct.unpack_from(_FLAG_STRUCT, self._shm.buf, 0)

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

        NEW: uses a monotonically-incrementing sequence number stored in the
        watcher_pid slot (as a negative value) rather than a wall-clock timestamp.
        The watcher suppresses the SINGLE next event that matches our sequence,
        then clears the sentinel immediately.  This means:

          - Rapid back-to-back installs each get their own suppression token.
          - A suppression from install N cannot accidentally suppress the
            fs event from install N+1 that arrived within OUR_WRITE_GRACE_MS.

        Encoding: negative value in watcher_pid slot = our sentinel.
        The absolute value is a monotonic ms timestamp (as before, for the
        grace-period check that remains as a secondary guard).
        """
        epoch_ms = int(time.monotonic() * 1000)
        ver, dirty, _ = self._read()
        # Negative epoch_ms = "our write" sentinel.  Watcher checks _is_our_write()
        # and clears the sentinel after suppressing exactly one event.
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
#: Keep this tight: our symlink installs complete in <10ms.  A 3-second window
#: causes the watcher to suppress REAL external changes that arrive within 3s
#: of any FFI write, making rapid back-to-back swaps appear as stale cache hits.
OUR_WRITE_GRACE_MS = 100   # 100ms — generously covers <10ms install + inotify lag

# Minimum debounce for dist-info events. pip's uninstall→temp-write→rename
# sequence spans ~100ms; firing at 5ms catches the temp file mid-op.
# 150ms lets the full sequence settle before we parse the delta.
_DIST_INFO_DEBOUNCE_S = 0.150   # 150ms — covers pip's full install sequence
_OTHER_DEBOUNCE_S     = 0.250   # 250ms — noisy __pycache__ / .pth writes


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
        # Label for log lines: extract python version from path, e.g. "py3.11"
        _m = re.search(r'python(\d+\.\d+)', site_packages_path)
        self._label = f"[py{_m.group(1)}]" if _m else f"[{self._sp_path.parent.name}]"
        self._debounce_lock   = threading.Lock()
        self._pending_timer: Optional[threading.Timer] = None
        self._last_our_write  = 0   # monotonic ms of last known FFI write
        # Delta accumulator — reset on each debounce flush
        self._pending_created: Set[Path] = set()
        self._pending_deleted: Set[Path] = set()

        # 🔥 NEW: State for validating events during long Omnipkg installs
        self._omnipkg_op_in_progress: bool = False
        self._pending_validation_events: List[tuple] = []  # (created: bool, path: str)

    # ── public methods for daemon control ──────────────────────────────────

    def start_omnipkg_op(self):
        """Low-level setter — prefer start_omnipkg_op / end_omnipkg_op for daemon control."""
        with self._debounce_lock:
            self._omnipkg_op_in_progress = True
            self._pending_validation_events.clear()
        # 🔥 REMOVED THE INFO LOG FROM HERE

    def end_omnipkg_op(self, installed: list, removed: list):
        """
        Validates any queued FS events against the official changelog.
        Only processes events that happened in this handler's directory.
        """
        def _norm(n): return __import__("re").sub(r"[-_.]+", "-", n).lower()
        official = {_norm(pkg[0]) for pkg in installed} | {_norm(pkg[0]) for pkg in removed}
        culprit_count = 0

        with self._debounce_lock:
            pending = list(self._pending_validation_events)
            self._pending_validation_events.clear()
            self._omnipkg_op_in_progress = False

        # 🔥 SILENCE: If this specific handler saw no activity, 
        # do not log anything and exit immediately.
        if not pending:
            return

        for created, path_str in pending:
            p = Path(path_str)
            stem = p.name[:-len(".dist-info")] if p.name.endswith(".dist-info") else p.name
            name = stem.rsplit("-", 1)[0] if "-" in stem else stem

            if _norm(name) not in official:
                log.info("[fs_watcher] 🚨 EXTERNAL CULPRIT during op: %s", name)
                culprit_count += 1
                parsed = self._parse_dist_info_name(p)
                culprit_installed = [list(parsed)] if (parsed and created) else []
                culprit_removed   = [list(parsed)] if (parsed and not created) else []
                try:
                    self._invalidate_fn(
                        site_packages_path=str(self._sp_path),
                        installed=culprit_installed,
                        removed=culprit_removed,
                    )
                except Exception as exc:
                    log.warning("[fs_watcher] culprit patch failed: %s", exc)
                finally:
                    self._flag.mark_clean()
            else:
                log.debug("[fs_watcher] Verified change: %s was Omnipkg", name)

        log.info("[fs_watcher] %s Op validated in %s. %d events, %d culprits.",
                 self._label, self._sp_path, len(pending), culprit_count)

    def set_omnipkg_op_status(self, in_progress: bool):
        """Low-level setter — prefer start_omnipkg_op / end_omnipkg_op for daemon control."""
        with self._debounce_lock:
            self._omnipkg_op_in_progress = in_progress
            if not in_progress:
                self._pending_validation_events.clear()
            log.debug("[fs_watcher] Omnipkg operation status -> in_progress=%s", in_progress)

    # ── internal helpers ─────────────────────────────────────────────────────

    def _is_our_write(self) -> bool:
        """
        Return True if the event looks like an echo of our own FFI install,
        and clear the sentinel immediately so the NEXT event is not suppressed.

        One-shot suppression: the sentinel is consumed on first use.
        This prevents a 100ms (OUR_WRITE_GRACE_MS) window from swallowing a
        real external change that follows immediately after our own install.
        """
        ver, dirty, pid_slot = struct.unpack_from(_FLAG_STRUCT, self._flag._shm.buf, 0)
        if pid_slot >= 0:
            return False     # positive → real watcher PID, not our sentinel
        our_write_ms = -pid_slot
        now_ms = int(time.monotonic() * 1000)
        if (now_ms - our_write_ms) < OUR_WRITE_GRACE_MS:
            # Consume the sentinel — restore real watcher PID so next event is NOT suppressed
            struct.pack_into(_FLAG_STRUCT, self._flag._shm.buf, 0,
                             ver, dirty, os.getpid())
            return True
        # Grace period expired — clear stale sentinel too
        struct.pack_into(_FLAG_STRUCT, self._flag._shm.buf, 0,
                         ver, dirty, os.getpid())
        return False

    def _is_dist_info(self, path: str) -> bool:
        """Only care about .dist-info dirs at depth-1 — that's where name+version live."""
        try:
            p = Path(path).resolve()
            rel = _relative_to_win(p, self._sp_path)
            parts = rel.parts
            return len(parts) == 1 and parts[0].endswith(".dist-info")
        except ValueError:
            return False

    def _is_relevant(self, path: str) -> bool:
        """
        Trigger on depth-1 events inside site-packages (dist-info, .data, .pth, packages).
        Ignore:
          - __pycache__ and nested writes inside already-installed packages
          - ~ prefixed temp files (pip writes ~pkg-x.y.z.dist-info then renames)
          - .omnipkg_versions/ subtree (our own bubble dir — never external)
          - direct writes inside existing package dirs (depth > 1, non-dist-info)
        """
        try:
            p = Path(path).resolve()
            rel = _relative_to_win(p, self._sp_path)
            parts = rel.parts
            if not parts:
                return False
            name = parts[0]
            # Skip pip temp files (~rich-14.3.3.dist-info style)
            if name.startswith("~"):
                return False
            # Skip our own bubble directory entirely
            if name == ".omnipkg_versions":
                return False
            if len(parts) == 1:
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

        Two-tier debounce:
          • dist-info events: fire after 5ms — they carry name+version immediately
            and are the only thing we care about for cache patching.  No need to
            wait for all the wheel files to land; the dist-info dir is written last
            by uv after the package is fully installed.
          • Everything else: 50ms — avoids hammering on noisy __pycache__ writes.

        The lock covers both accumulation AND timer reset so concurrent events
        from inotify can't each spawn their own timer.
        """
        is_di = self._is_dist_info(path)
        # dist-info: wait for pip's full uninstall→temp→rename sequence to settle
        # other:     wider window to absorb noisy __pycache__ / .pth bursts
        debounce_s = _DIST_INFO_DEBOUNCE_S if is_di else _OTHER_DEBOUNCE_S

        with self._debounce_lock:
            # Accumulate .dist-info dirs only — they carry name+version for free
            if is_di:
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
            self._pending_timer = threading.Timer(debounce_s, self._do_patch)
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _do_patch(self):
        """
        Surgically patch the Rust SITE_PACKAGES_CACHE with the accumulated delta.
        Now logs exactly which files triggered the event and ignores noise.
        """
        with self._debounce_lock:
            created = set(self._pending_created)
            deleted = set(self._pending_deleted)
            self._pending_created.clear()
            self._pending_deleted.clear()

        # 1. Identify actual package changes
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

        # 2. If no actual package delta, this is just FS noise (e.g. .lock files, __pycache__)
        if not installed and not removed:
            # Log the noise for debugging, but do NOT mark dirty and do NOT invalidate
            all_changes = list(created | deleted)
            if all_changes:
                log.debug("[fs_watcher] Noise ignored in %s: %s", self._sp_path, all_changes[0].name)
            return

        # 3. This is a REAL external change. Log it and invalidate.
        log.info(
            "[fs_watcher] %s 🚨 EXTERNAL CHANGE in %s\n"
            "   - Installed: %s\n"
            "   - Removed:   %s\n"
            "   - Triggered by: %s",
            self._label, self._sp_path, installed, removed,
            list(created | deleted)[0].name,
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
        # 🔥 If a long Omnipkg install is happening, queue dist-info events for
        # validation in end_omnipkg_op.
        if self._omnipkg_op_in_progress:
            if self._is_dist_info(event.src_path):
                created = event.event_type in ("created", "modified")
                with self._debounce_lock:
                    self._pending_validation_events.append((created, event.src_path))
                log.debug("[fs_watcher] [%s] Queued for validation: %s %s", 
                          self._sp_path, event.event_type, event.src_path)
            return

        if self._is_our_write():
            return
        if not self._is_relevant(event.src_path):
            return

        # Log exactly which path triggered the event so we can debug "Why is this firing?"
        log.debug("[fs_watcher] [%s] event: %s %s", 
                  self._sp_path, event.event_type, event.src_path)

        created = event.event_type in ("created", "modified")
        self._schedule_invalidation(event.src_path, created=created)

    on_created  = _handle
    on_deleted  = _handle
    on_modified = _handle

    def on_moved(self, event):
        # pip pattern: write ~pkg-x.y.z.dist-info (temp) → rename to pkg-x.y.z.dist-info
        # We only care about the FINAL rename destination, not the temp src.
        # _is_relevant() already filters ~ names so src fires are silently dropped.
        if self._is_our_write():
            return
        if self._is_relevant(event.dest_path):
            self._schedule_invalidation(event.dest_path, created=True)
        # src: only fire deleted if it was a real dist-info (not a ~ temp rename)
        src_name = Path(event.src_path).name
        if not src_name.startswith("~") and self._is_relevant(event.src_path):
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
        self._snapshot: Dict[str, Set[str]] = {} # Map of dir -> set of .dist-info names
        self._thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()
        self._omnipkg_op_in_progress = False
        self._pending_validation_events = set()  # (is_created: bool, path: str)
        self._last_op_end_time = 0.0             # Timestamp of last Omnipkg op


    @staticmethod
    def _dir_label(d: Path) -> str:
        """Return a short label like '[py3.12]' derived from the watched dir path."""
        for part in reversed(d.parts):
            m = re.search(r'python(\d+\.\d+)', part)
            if m:
                return f"[py{m.group(1)}]"
        return f"[{d.parent.name}]"

    def _current_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def start(self):
        # Record baseline mtimes and initial folder snapshot
        for d in self._dirs:
            self._mtimes[str(d)] = self._current_mtime(d)
            self._snapshot[str(d)] = {
                n for n in os.listdir(d) 
                if n.endswith(".dist-info") or n.endswith(".data")
            } if d.exists() else set()

        self._thread = threading.Thread(
            target=self._loop, name="omnipkg-mtime-watcher", daemon=True)
        self._thread.start()
        log.info("[fs_watcher] mtime fallback watcher started (install watchdog for inotify)")

    def _loop(self):
        # 🔥 DIRECT DEBUG LOGGING TO A TOTALLY SEPARATE FILE
        debug_file = os.path.join(__import__("tempfile").gettempdir(), "omnipkg_watcher_debug.txt")
        with open(debug_file, "a", encoding="utf-8") as f:
            f.write(f"\n--- Fallback Watcher Started. Watching {len(self._dirs)} dirs ---\n")

        loop_count = 0
        while not self._stop_event.wait(self.POLL_INTERVAL):
            loop_count += 1
            if loop_count % 10 == 0:  # Every 5 seconds, write a heartbeat so we know it's alive
                try:
                    with open(debug_file, "a", encoding="utf-8") as f:
                        f.write(f"Heartbeat {loop_count}: still polling...\n")
                except Exception: pass

            for d in self._dirs:
                key = str(d)
                new_mtime = self._current_mtime(d)
                old_mtime = self._mtimes.get(key, 0.0)
                if new_mtime > old_mtime:
                    self._mtimes[key] = new_mtime

                    # Surgical check: What actually changed in site-packages?
                    current_items = {
                        n for n in os.listdir(d) 
                        if n.endswith(".dist-info") or n.endswith(".data")
                    } if d.exists() else set()
                    old_items = self._snapshot.get(key, set())

                    diff = (current_items - old_items) | (old_items - current_items)
                    self._snapshot[key] = current_items # Update snapshot

                    if not diff:
                        # Windows noise (e.g. .omnipkg_versions changed, but Lib\site-packages mtime updated)
                        continue

                    # 🔥 ATOMIC HANDSHAKE: Check for the marker file on disk.
                    # If the marker exists, it means an Omnipkg operation is in progress
                    # even if the IPC 'START' signal hasn't arrived yet.
                    # 🔥 SETTLE PERIOD: Ignore all changes for 1 second after an op ends.
                    # This prevents "ghost" external changes caused by Windows I/O lag.
                    # 🔥 LOCK-FILE PRIORITY: The marker file is the ultimate source of truth.
                    # Even if the IPC 'end' signal arrived, if the lock file is still here,
                    # Omnipkg is still touching the disk (bubbling/KB updates).
                    marker_file = d / ".omnipkg_op.lock"
                    if self._omnipkg_op_in_progress or marker_file.exists():
                        for item in diff:
                            self._pending_validation_events.add((item in current_items, str(d / item)))
                        continue

                    # Check grace period via flag's our_write sentinel
                    _, _, pid_slot = struct.unpack_from(_FLAG_STRUCT, self._flag._shm.buf, 0)
                    if pid_slot < 0:
                        our_write_ms = -pid_slot
                        now_ms = int(time.monotonic() * 1000)
                        if (now_ms - our_write_ms) < OUR_WRITE_GRACE_MS:
                            with open(debug_file, "a", encoding="utf-8") as f:
                                f.write(f"[{time.time()}] Ignored mtime change in {d} (was our own FFI write)\n")
                            continue   # our own FFI write, skip

                    # Parse name+version from each changed .dist-info dirname.
                    # Skip ~ prefixed temp names — pip/uv write these atomically
                    # (e.g. ~v_ffi-x.y.z.dist-info → uv_ffi-x.y.z.dist-info).
                    # If we poll mid-rename we'd see the temp name as a real change;
                    # ignore it and let the next poll catch the final name.
                    installed_delta = []
                    removed_delta   = []
                    for item in diff:
                        if item.startswith("~"):
                            log.debug("%s Skipping temp rename artifact: %s", self._dir_label(d), item)
                            continue
                        parsed = SitePackagesEventHandler._parse_dist_info_name(Path(item))
                        is_created = item in current_items
                        msg = f"[{time.time()}] {self._dir_label(d)} 🚨 EXTERNAL CHANGE ({'CREATED' if is_created else 'REMOVED'}): {item} in {d}\n"
                        with open(debug_file, "a", encoding="utf-8") as f:
                            f.write(msg)
                        log.info(msg.strip())
                        print(msg.strip(), file=sys.stderr)
                        if parsed:
                            if is_created:
                                installed_delta.append(list(parsed))
                            else:
                                removed_delta.append(list(parsed))

                    # If every item in diff was a ~ temp artifact, nothing real changed.
                    if not installed_delta and not removed_delta:
                        continue

                    self._flag.mark_dirty()
                    try:
                        self._invalidate(str(d), installed_delta, removed_delta)
                        patch_summary = f"installed={installed_delta} removed={removed_delta}"
                        log.info(
                            "[fs_watcher] %s ✅ patch sent: %s",
                            self._dir_label(d), patch_summary,
                        )
                        with open(debug_file, "a", encoding="utf-8") as f:
                            f.write(f"[{time.time()}] Sent patch to daemon: {patch_summary}\n")


                    except Exception as exc:
                        log.warning("[fs_watcher] %s ❌ Patch send failed: %s", self._dir_label(d), exc)
                        with open(debug_file, "a", encoding="utf-8") as f:
                            f.write(f"[{time.time()}] Patch failed: {exc}\n")
                    finally:
                        self._flag.mark_clean()

    def start_omnipkg_op(self):
        """
        Called when omnipkg signals it's about to start an FFI install.

        We force an immediate snapshot refresh here — before the FFI touches
        any dist-info — so our pre-op baseline is at most milliseconds old,
        not up to 500ms stale from the poll cycle.  This costs one os.listdir()
        per watched dir (~0.1ms total on NVMe) and runs in the watcher thread,
        not on the install path.

        The refreshed snapshot becomes _pre_op_snapshot.  end_omnipkg_op diffs
        it against the post-op state to detect any external changes that snuck
        in during the op window, comparing against the FFI's official changelog.
        """
        log.info("[fs_watcher] Omnipkg op START — refreshing snapshot baseline. (dirs=%s)",
                 [self._dir_label(d) for d in self._dirs])
        self._pending_validation_events.clear()

        # Force-refresh snapshot before marking in_progress so the poll loop
        # doesn't race and overwrite with a stale read.
        for d in self._dirs:
            key = str(d)
            if d.exists():
                self._snapshot[key] = {
                    n for n in os.listdir(d)
                    if n.endswith(".dist-info") or n.endswith(".data")
                }
                self._mtimes[key] = self._current_mtime(d)

        # Freeze a copy as the pre-op baseline AFTER the fresh read.
        self._pre_op_snapshot: Dict[str, Set[str]] = {
            key: set(val) for key, val in self._snapshot.items()
        }

        self._omnipkg_op_in_progress = True
        log.debug("[fs_watcher] Snapshot baseline locked. Entering collection mode.")

    def end_omnipkg_op(self, installed: list, removed: list):
        """
        Called with the FFI's official changelog once the op completes.

        We diff _pre_op_snapshot against the freshly-read post-op state.
        Anything in the diff that matches the official changelog → verified ours.
        Anything that doesn't → external culprit, patch it out immediately.

        For fast ops (<500ms) the poll never fired, so _pending_validation_events
        is empty — but the snapshot diff still captures everything because we took
        a fresh baseline at start_omnipkg_op.
        """
        log.info("[WATCHER-RECEIVER] Received end_op: INST=%s, REM=%s", installed, removed)

        inst_str = ", ".join([f"{n}=={v}" for n, v in installed])
        rem_str  = ", ".join([f"{n}=={v}" for n, v in removed])
        log.debug("[fs_watcher] Omnipkg official changes: [INSTALLED: %s] [REMOVED: %s]",
                 inst_str or "none", rem_str or "none")

        def _norm(n): return __import__("re").sub(r"[-_.]+", "-", n).lower()
        official = {_norm(pkg[0]) for pkg in installed} | {_norm(pkg[0]) for pkg in removed}

        # Refresh snapshot post-op BEFORE clearing in_progress.
        # The poll loop checks _omnipkg_op_in_progress first — if we cleared it
        # before refreshing we'd get a spurious EXTERNAL CHANGE for our own install.
        for d in self._dirs:
            key = str(d)
            if d.exists():
                self._snapshot[key] = {
                    n for n in os.listdir(d)
                    if n.endswith(".dist-info") or n.endswith(".data")
                }
                self._mtimes[key] = self._current_mtime(d)

        # Diff pre-op baseline against post-op state.
        # Union with any events the poll managed to queue during a slow op.
        pre_op = getattr(self, "_pre_op_snapshot", {})
        disk_events: Set[tuple] = set()
        for key, old_items in pre_op.items():
            d = Path(key)
            current_items = self._snapshot.get(key, old_items)
            for item in (current_items - old_items):
                disk_events.add((True,  str(d / item)))
            for item in (old_items - current_items):
                disk_events.add((False, str(d / item)))

        all_events: Set[tuple] = self._pending_validation_events | disk_events

        culprit_count  = 0
        verified_count = 0
        for created, path_str in all_events:
            p    = Path(path_str)
            if p.name.startswith("~"):
                log.debug("[fs_watcher] Skipping temp rename artifact in culprit scan: %s", p.name)
                continue
            stem = p.name[:-len(".dist-info")] if p.name.endswith(".dist-info") else p.name
            name = stem.rsplit("-", 1)[0] if "-" in stem else stem
            if _norm(name) not in official:
                log.info("[fs_watcher] 🚨 EXTERNAL CULPRIT during op: %s", name)
                culprit_count += 1
                parsed = SitePackagesEventHandler._parse_dist_info_name(p)
                c_inst = [list(parsed)] if (parsed and created)     else []
                c_rem  = [list(parsed)] if (parsed and not created) else []
                self._flag.mark_dirty()
                try:
                    log.info("[fs_watcher] patch→ site_packages=%s inst=%s rem=%s", str(p.parent), c_inst, c_rem)
                    self._invalidate(str(p.parent), c_inst, c_rem)
                    log.info("[fs_watcher] ✅ Culprit patch sent: %s", name)
                except Exception as exc:
                    log.warning("[fs_watcher] ❌ Culprit patch failed: %s", exc)
                finally:
                    self._flag.mark_clean()
            else:
                log.debug("[fs_watcher] ✓ Verified: %s was our change", name)
                verified_count += 1

        log.info("[fs_watcher] Op FINISHED. %d disk change(s): "
                 "%d ours (verified), %d external culprit(s). patch target=%s",
                 len(all_events), verified_count, culprit_count,
                 [self._dir_label(Path(path_str)) for _, path_str in all_events],
        )
        self._pending_validation_events.clear()
        self._last_op_end_time = time.monotonic()
        # ✅ Clear LAST — poll loop is now safe with fresh snapshot.
        self._omnipkg_op_in_progress = False

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
        if installed or removed:
            # Surgical delta patch — ~25µs in Rust, no disk I/O
            patch_msg = json.dumps({
                "type": "patch_site_packages_cache",
                "site_packages_path": site_packages_path,
                "installed": installed,
                "removed": removed,
            })
            # Piggyback KB sentinel on same connection
            sentinel_msg = json.dumps({
                "type": "kb_sentinel",
                "installed": installed,
                "removed":   removed,
            })
            msgs = [patch_msg, sentinel_msg]
        else:
            # Delta unavailable (unparseable dist-info name or edge case) — full rescan
            log.warning(
                "[fs_watcher] No parseable delta for %s — falling back to full invalidate",
                site_packages_path,
            )
            msgs = [json.dumps({
                "type": "invalidate_site_packages_cache",
                "site_packages_path": site_packages_path,
            })]
        try:
            for msg in msgs:
                payload = len(msg).to_bytes(8, "big") + msg.encode()
                if platform.system() == "Windows":
                    self._send_windows(payload)
                else:
                    self._send_unix(payload)
        except Exception as exc:
            log.warning("[fs_watcher] daemon message failed: %s", exc)

    def _send_raw(self, msg: str):
        """Send an arbitrary JSON message string to the daemon socket."""
        payload = len(msg).to_bytes(8, "big") + msg.encode()
        if platform.system() == "Windows":
            self._send_windows(payload)
        else:
            self._send_unix(payload)

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
        debug_file = os.path.join(__import__("tempfile").gettempdir(), "omnipkg_watcher_debug.txt")
        try:
            # On Windows, socket_path is a file containing {"port": 12345}
            with open(self._socket_path, "r") as f:
                port = json.load(f).get("port")

            if not port:
                with open(debug_file, "a", encoding="utf-8") as f: 
                    f.write("Failed to read port from socket file.\n")
                return

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", port))
                s.sendall(payload)
                try:
                    s.settimeout(0.5)
                    hdr = s.recv(8)
                    if hdr and len(hdr) == 8:
                        s.recv(int.from_bytes(hdr, "big"))
                except Exception as e:
                    with open(debug_file, "a", encoding="utf-8") as f: 
                        f.write(f"Socket recv timeout/error (safe to ignore): {e}\n")
        except Exception as exc:
            with open(debug_file, "a", encoding="utf-8") as f: 
                f.write(f"Failed to connect to daemon port: {exc}\n")


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
        self._handlers: List[SitePackagesEventHandler] = []  # populated by _start_watchdog
        self._fallback        = None
        self._running         = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True

        if not log.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [fs_watcher] %(levelname)s %(message)s",
                datefmt="%H:%M:%S",
            ))
            log.addHandler(handler)
            log.setLevel(logging.DEBUG)

        self._flag = SharedWatchFlag.create()

        # 🔥 ADD IDENTITY: Tell us exactly which process is running the watcher
        log.info("[fs_watcher] Watcher Process Identity: PID=%s, Python=%s", 
                 os.getpid(), sys.executable)

        log.info("[fs_watcher] Shared-memory flag created (name=%s)", SHM_FLAG_NAME)

        patch_fn = DaemonPatchSender(self._socket_path)

        if _HAS_WATCHDOG and self._dirs:
            self._start_watchdog(patch_fn)
        else:
            if not _HAS_WATCHDOG:
                log.warning("[fs_watcher] watchdog not installed — using mtime polling.")
            self._start_fallback(patch_fn)

    def _start_watchdog(self, invalidate_fn):
        self._observer = Observer()
        self._handlers: List[SitePackagesEventHandler] = []  # cross-platform handler registry
        for sp_dir in self._dirs:
            handler = SitePackagesEventHandler(
                watch_flag=self._flag,
                invalidate_fn=invalidate_fn,
                site_packages_path=sp_dir,
            )
            self._observer.schedule(handler, sp_dir, recursive=False)
            self._handlers.append(handler)
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
            self._handlers.append(handler)  # keep our registry in sync
            log.info("[fs_watcher] Dynamically added watch: %s", sp_dir)

    def start_omnipkg_op(self):
        """Broadcasts the start of a long Omnipkg FFI operation to all handlers."""
        for handler in self._handlers:
            handler.start_omnipkg_op()
        if self._fallback:
            self._fallback.start_omnipkg_op()

    def end_omnipkg_op(self, installed: list, removed: list):
        """Broadcasts the end of an op and the official changelog for validation."""
        for handler in self._handlers:
            handler.end_omnipkg_op(installed, removed)
        if self._fallback:
            self._fallback.end_omnipkg_op(installed, removed)

    def set_omnipkg_op_status(self, in_progress: bool):
        """Broadcasts the start/end of a long Omnipkg FFI operation to all handlers."""
        for handler in self._handlers:
            if hasattr(handler, 'set_omnipkg_op_status'):
                handler.set_omnipkg_op_status(in_progress)

    def stop(self):
        """Stop the watcher. Guaranteed not to block process exit."""
        try:
            if self._observer is not None:
                # Force daemon=True on ALL internal threads before stopping
                # This ensures sys.exit() won't block even if join() times out
                try:
                    for t in self._observer._threads if hasattr(self._observer, '_threads') else []:
                        try:
                            t.daemon = True
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    self._observer.stop()
                except Exception:
                    pass

                try:
                    self._observer.join(timeout=2.0)
                except Exception:
                    pass

                # If still alive after join, it's a daemon thread now so exit won't block
                self._observer = None
        except Exception:
            pass

        # Stop the shared memory flag
        try:
            if self._shm_flag is not None:
                self._shm_flag.close()
                self._shm_flag.unlink()
                self._shm_flag = None
        except Exception:
            pass


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
        Caches the result but re-validates on each call: on Windows the watcher
        process can restart and create a new SHM block, leaving workers with a
        handle to a dead block that silently reads zeros (dirty=0 forever).
        """
        if cls._instance is not None and cls._instance._flag is not None:
            # Probe the block — if the watcher restarted it will have written
            # a fresh watcher_pid (positive, != our own PID).  A dead/stale
            # block on Windows typically returns all-zeros or raises; either
            # way we force a fresh attach so workers track the live block.
            try:
                ver, dirty, pid_slot = cls._instance._flag._read()
                if pid_slot == 0:
                    # All-zeros → block was unlinked and re-created; re-attach.
                    cls._instance._flag.close()
                    cls._instance = None
            except Exception:
                # Block handle is dead (OSError, struct.error, etc.) — re-attach.
                try:
                    cls._instance._flag.close()
                except Exception:
                    pass
                cls._instance = None

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