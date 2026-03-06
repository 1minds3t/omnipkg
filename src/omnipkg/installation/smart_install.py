"""
omnipkg/installation/smart_install.py  (REFACTORED — fast shell return)

Key changes vs original:
  - Bubble creation (create_isolated_bubble) moved to background fork
  - hook_manager.refresh/validate moved to background
  - doctor() + _heal_conda_environment() stay foreground (only run when needed)
  - Background now owns: bubble creation, SMART verification, manifest,
    KB update, KB sync, snapshot, hash index, cloak cleanup
  - Per-package file locking prevents duplicate bubble work across concurrent ops
  - Atomic task claiming via os.rename() — last-write-wins safe
  - Every background stage is individually timed
  - Foreground critical path: preflight → uv install → change detection → fork
  - TARGET: <400ms foreground (from ~2000ms)
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from packaging.utils import canonicalize_name
from packaging.version import Version as parse_version

if TYPE_CHECKING:
    from omnipkg.core import omnipkg as OmnipkgCore

# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _DEBUG_TIMING() -> bool:
    return os.environ.get("OMNIPKG_DEBUG", "0") == "1"


def _fmt_ns(ns: int) -> str:
    if ns < 1_000:
        return f"{ns}ns"
    if ns < 1_000_000:
        return f"{ns / 1_000:.1f}µs"
    return f"{ns / 1_000_000:.3f}ms"


def _fmt_ms(ms: float) -> str:
    if ms < 1.0:
        return f"{ms * 1000:.0f}µs"
    return f"{ms:.1f}ms"


def _tprint(label: str, t0: float) -> None:
    if _DEBUG_TIMING():
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"[TIMING] {label}: {elapsed:.2f}ms", flush=True)


# ---------------------------------------------------------------------------
# Per-package bubble lock — prevents two concurrent ops racing on same pkg
# ---------------------------------------------------------------------------

_LOCK_DIR = "/tmp/omnipkg/locks"


class _BubbleLock:
    """
    Advisory exclusive lock on a (package, version) pair.
    Uses fcntl.LOCK_EX so it works across processes (daemon + forked children).
    Non-blocking variant raises BubbleLockBusy so callers can defer gracefully.
    """

    def __init__(self, pkg_name: str, version: str, blocking: bool = True):
        os.makedirs(_LOCK_DIR, exist_ok=True)
        safe = f"{pkg_name.replace('/', '_')}-{version}"
        self.path = os.path.join(_LOCK_DIR, f"{safe}.lock")
        self.blocking = blocking
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "w")
        flag = fcntl.LOCK_EX if self.blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(self._fd, flag)
        except BlockingIOError:
            self._fd.close()
            self._fd = None
            raise BubbleLockBusy(f"Another process is already creating bubble for {self.path}")
        return self

    def __exit__(self, *_):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()


class BubbleLockBusy(Exception):
    pass


# ---------------------------------------------------------------------------
# Minimal fast-path correctness marker written BEFORE fork
# Tells background child exactly what bubbles are expected, preventing
# duplicate work if a second swap races in before the first bg job finishes.
# ---------------------------------------------------------------------------

_TASK_DIR = "/tmp/omnipkg/bg_queue"


def _enqueue_bubble_tasks(tasks: list[dict]) -> str:
    """
    Write a JSON task file atomically. Returns the task file path.
    Uses a timestamp+pid key so concurrent callers never collide.
    """
    os.makedirs(_TASK_DIR, exist_ok=True)
    key = f"{int(time.time()*1000)}_{os.getpid()}"
    tmp = os.path.join(_TASK_DIR, f"{key}.tmp")
    final = os.path.join(_TASK_DIR, f"{key}.json")
    with open(tmp, "w") as f:
        json.dump(tasks, f)
    os.rename(tmp, final)  # atomic
    return final


def _claim_bubble_task(pkg_name: str, version: str) -> bool:
    """
    Returns True if THIS process should create the bubble.
    Writes a .claimed marker atomically — if it already exists, returns False.
    """
    os.makedirs(_TASK_DIR, exist_ok=True)
    safe = f"{pkg_name.replace('/', '_')}-{version}"
    marker = os.path.join(_TASK_DIR, f"claimed_{safe}.marker")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # Check if the claiming process is still alive — if not, steal the claim
        try:
            with open(marker) as _mf:
                _owner_pid = int(_mf.read().strip())
            try:
                os.kill(_owner_pid, 0)  # 0 = just check existence
                return False  # still alive, let it finish
            except (ProcessLookupError, PermissionError):
                # Dead process — steal the claim
                os.unlink(marker)
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
        except Exception:
            return False


def _release_bubble_claim(pkg_name: str, version: str) -> None:
    safe = f"{pkg_name.replace('/', '_')}-{version}"
    marker = os.path.join(_TASK_DIR, f"claimed_{safe}.marker")
    try:
        os.unlink(marker)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Background timing logger
# ---------------------------------------------------------------------------


class _BgTimer:
    def __init__(self, log_fn):
        self._log = log_fn
        self._stages: list[tuple[str, float]] = []

    def stage(self, label: str, t0: float):
        ms = (time.perf_counter() - t0) * 1000
        self._stages.append((label, ms))
        self._log(f"[TIMING] {label}: {ms:.1f}ms")

    def summary(self):
        if not self._stages:
            return
        total = sum(ms for _, ms in self._stages)
        self._log(f"\n[TIMING SUMMARY] {len(self._stages)} stages — {total:.0f}ms total")
        for label, ms in sorted(self._stages, key=lambda x: -x[1]):
            bar = "█" * max(1, int(ms / max(total, 1) * 30))
            self._log(f"  {ms:7.1f}ms  {bar:<30}  {label}")


# ===========================================================================
# SmartInstaller
# ===========================================================================


class SmartInstaller:
    """
    Orchestrates package installation for OmnipkgCore.
    Core delegates via a one-liner shim; all callers use core.smart_install() unchanged.
    """

    def __init__(self, core: "OmnipkgCore") -> None:
        self.core = core
        self.config = core.config
        self.multiversion_base: Path = core.multiversion_base

    @property
    def bubble_manager(self):
        return self.core.bubble_manager

    @property
    def hook_manager(self):
        return self.core.hook_manager

    @property
    def cache_client(self):
        return self.core.cache_client

    def _safe_print(self, *args, **kwargs) -> None:
        try:
            from omnipkg.common_utils import safe_print
            safe_print(*args, **kwargs)
        except ImportError:
            print(*args, **kwargs)

    def _is_quantum_error(self, e: Exception) -> bool:
        return type(e).__name__ == "NoCompatiblePythonError"
        
    def _parse_uv_changes(self, uv_stderr: str, packages_before: Dict[str, str]) -> Dict[str, str]:
        """
        Derive post-install state from uv's stderr diff output.
        Lines look like:
          ' - rich==14.3.2'   (removed/replaced)
          ' + rich==14.3.3'   (added/installed)
        Returns a new packages dict reflecting the changes.
        """
        result = dict(packages_before)
        for line in uv_stderr.splitlines():
            line = line.strip()
            if line.startswith("- ") and "==" in line:
                pkg, ver = line[2:].split("==", 1)
                result.pop(pkg.strip().lower(), None)
            elif line.startswith("+ ") and "==" in line:
                pkg, ver = line[2:].split("==", 1)
                result[pkg.strip().lower()] = ver.strip()
        return result

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def install(
        self,
        packages: List[str],
        dry_run: bool = False,
        force_reinstall: bool = False,
        override_strategy: Optional[str] = None,
        target_directory: Optional[Path] = None,
        preflight_compatibility_cache: Optional[Dict] = None,
        index_url: Optional[str] = None,
        extra_index_url: Optional[str] = None,
    ) -> int:

        t_install_start = time.perf_counter()
        print(f"[WALL] install() entered at {t_install_start:.6f}", flush=True)
        _tprint("install_entry", t_install_start)

        # ================================================================
        # ULTRA-FAST PREFLIGHT CHECK
        # ================================================================
        resolved_package_cache: Dict[str, str] = {}

        if not force_reinstall and packages:
            self._safe_print("⚡ Running ultra-fast preflight check...")
            preflight_start = time.perf_counter()
            configured_exe = self.config.get("python_executable", sys.executable)
            install_strategy = self.config.get("install_strategy", "stable-main")

            fully_resolved_specs = []
            needs_installation = []
            complex_spec_chars = ["<", ">", "~", "!", ","]

            for pkg_spec in packages:
                pkg_name, version = self.core._parse_package_spec(pkg_spec)
                is_complex_spec = any(op in pkg_spec for op in complex_spec_chars)

                if version and not is_complex_spec:
                    resolved_spec = pkg_spec
                else:
                    if is_complex_spec:
                        self._safe_print(f"   🔍 Detected complex specifier: '{pkg_spec}'")
                        try:
                            resolved_spec_str = self.core._find_best_version_for_spec(pkg_spec)
                            if resolved_spec_str:
                                resolved_spec = resolved_spec_str
                                pkg_name, version = self.core._parse_package_spec(resolved_spec)
                                self._safe_print(f"   ✅ Resolved '{pkg_spec}' to '{resolved_spec}'")
                            else:
                                needs_installation.append(pkg_spec)
                                continue
                        except Exception as e:
                            if self._is_quantum_error(e):
                                return self._handle_quantum_healing(
                                    e, packages, dry_run, force_reinstall,
                                    override_strategy, target_directory,
                                    index_url, extra_index_url,
                                )
                            needs_installation.append(pkg_spec)
                            continue
                    else:
                        try:
                            latest_version = self.core._get_latest_version_from_pypi(
                                self.core._bare_name(pkg_name)
                            )
                        except Exception as e:
                            if self._is_quantum_error(e):
                                return self._handle_quantum_healing(
                                    e, packages, dry_run, force_reinstall,
                                    override_strategy, target_directory,
                                    index_url, extra_index_url,
                                )
                            needs_installation.append(pkg_spec)
                            continue

                        if latest_version:
                            resolved_spec = f"{pkg_name}=={latest_version}"
                            version = latest_version
                        else:
                            needs_installation.append(pkg_spec)
                            continue

                is_installed, duration_ns = self.core.check_package_installed_fast(
                    configured_exe, self.core._bare_name(pkg_name), version
                )
                duration_str = _fmt_ns(duration_ns)

                if is_installed:
                    bubble_path = self.multiversion_base / f"{pkg_name}-{version}"
                    is_in_bubble = bubble_path.exists() and bubble_path.is_dir()

                    if not is_in_bubble:
                        self._safe_print(f"   ✓ {resolved_spec} [satisfied: {duration_str} - active in main env]")
                        fully_resolved_specs.append(resolved_spec)
                        resolved_package_cache[pkg_spec] = resolved_spec
                        continue
                    elif install_strategy == "stable-main":
                        self._safe_print(f"   ✓ {resolved_spec} [satisfied: {duration_str} - bubble]")
                        fully_resolved_specs.append(resolved_spec)
                        resolved_package_cache[pkg_spec] = resolved_spec
                        continue
                    else:
                        needs_installation.append(resolved_spec)
                        resolved_package_cache[pkg_spec] = resolved_spec
                        continue
                else:
                    needs_installation.append(resolved_spec)
                    resolved_package_cache[pkg_spec] = resolved_spec

            if not needs_installation:
                total_ns = int((time.perf_counter() - preflight_start) * 1_000_000_000)
                self._safe_print(
                    f"⚡ PREFLIGHT SUCCESS: All {len(packages)} package(s) already satisfied! ({_fmt_ns(total_ns)})"
                )
                return 0

            self._safe_print(f"\n📦 {len(needs_installation)} package(s) need installation/validation")
            validated_specs = []
            try:
                for spec in needs_installation:
                    pkg_name, version = self.core._parse_package_spec(spec)
                    if not version:
                        self._safe_print(f"   🔍 Resolving version for '{pkg_name}' with pip...")
                    else:
                        _main_sp = self.multiversion_base.parent
                        _bubble_path = self.multiversion_base / f"{pkg_name}-{version}"
                        _dist_in_main = (
                            list(_main_sp.glob(f"{pkg_name.replace('-','_')}-{version}.dist-info")) +
                            list(_main_sp.glob(f"{pkg_name}-{version}.dist-info"))
                        )
                        if _dist_in_main:
                            resolved_package_cache[spec] = spec
                            validated_specs.append(spec)
                            self._safe_print(f"   ✓ Disk-validated '{spec}' (skipping pip)")
                            continue
                        elif _bubble_path.exists():
                            resolved_package_cache[spec] = spec
                            validated_specs.append(spec)
                            self._safe_print(f"   ✓ Disk-validated '{spec}' (skipping pip)")
                            continue
                        # Check if any version of this package is already bubbled —
                        # if so we know it's a valid pypi package, skip the pip subprocess
                        _any_bubble = any(
                            p.is_dir() for p in self.multiversion_base.glob(f"{pkg_name}-*")
                        )
                        if _any_bubble:
                            resolved_package_cache[spec] = spec
                            validated_specs.append(spec)
                            self._safe_print(f"   ✓ Inferred-valid '{spec}' (known package, bubble pending)")
                            continue
                        self._safe_print(f"   ⚙️  Validating '{spec}' with pip...")

                    resolved_spec, _ = self.core._resolve_spec_with_pip(
                        spec, index_url=index_url, extra_index_url=extra_index_url
                    )
                    if resolved_spec:
                        self._safe_print(f"   ✓ Pip validated '{spec}' → '{resolved_spec}'")
                        validated_specs.append(resolved_spec)
                        resolved_package_cache[spec] = resolved_spec
                    else:
                        self._safe_print(f"\n❌ Could not find the specified version for '{pkg_name}'.")
                        return 1

            except Exception as e:
                if self._is_quantum_error(e):
                    return self._handle_quantum_healing(
                        e, packages, dry_run, force_reinstall,
                        override_strategy, target_directory,
                        index_url, extra_index_url,
                    )
                raise

            packages = validated_specs

        elif force_reinstall and packages:
            self._safe_print("⚡ Running preflight check with --force flag...")
            configured_exe = self.config.get("python_executable", sys.executable)
            for pkg_spec in packages:
                pkg_name, version = self.core._parse_package_spec(pkg_spec)
                if not version:
                    self._safe_print(f"   ⚠️  {pkg_spec} [no version pinned] → will install fresh")
                    continue
                is_installed, duration_ns = self.core.check_package_installed_fast(
                    configured_exe, pkg_name, version
                )
                duration_str = _fmt_ns(duration_ns)
                if is_installed:
                    self._safe_print(f"   🔧 {pkg_spec} [found: {duration_str}] → will force reinstall")
                else:
                    self._safe_print(f"   ⚠️  {pkg_spec} [not found: {duration_str}] → will install fresh")

        _tprint("preflight", t_install_start)
        print(f"[WALL] post-preflight: {(time.perf_counter()-t_install_start)*1000:.1f}ms elapsed", flush=True)

        # ================================================================
        # NORMAL INITIALISATION (only reached when real work is needed)
        # ================================================================
        original_strategy = None
        if override_strategy:
            original_strategy = self.config.get("install_strategy", "stable-main")
            if original_strategy != override_strategy:
                self._safe_print(f"   - 🔄 Temporarily switching install strategy to '{override_strategy}'...")
                self.config["install_strategy"] = override_strategy

        install_strategy = self.config.get("install_strategy", "stable-main")

        t_connect = time.perf_counter()
        if not self.core._connect_cache():
            return 1
        _tprint("connect_cache", t_connect)

        if dry_run:
            self._safe_print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0

        if not packages:
            self._safe_print("🚫 No packages specified for installation.")
            return 1

        # doctor+heal deferred to background — not needed for correctness of uv install
        _run_doctor_in_bg = True

        configured_exe = self.config.get("python_executable", sys.executable)
        # We ARE the running interpreter — no subprocess needed.
        python_context_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        install_strategy = self.config.get("install_strategy", "stable-main")
        packages_to_process = list(packages)

        # ================================================================
        # omnipkg special-case (identical to original)
        # ================================================================
        main_env_kb_updates: Dict[str, str] = {}
        bubbled_kb_updates: Dict[str, str] = {}
        any_installations_made = False
        protected_from_cleanup: Set[str] = set()
        final_main_state: Dict[str, str] = {}

        # Bubble tasks queued for background — list of dicts with all args needed
        # by create_isolated_bubble so the child never needs to re-derive them.
        _pending_bubble_tasks: list[dict] = []

        for pkg_spec in list(packages_to_process):
            pkg_name, requested_version = self.core._parse_package_spec(pkg_spec)
            if pkg_name.lower() != "omnipkg":
                continue

            packages_to_process.remove(pkg_spec)
            self._safe_print(f"✨ Special handling: omnipkg '{pkg_spec}' requested.")

            if not requested_version:
                resolved_spec = resolved_package_cache.get(pkg_spec)
                if not resolved_spec:
                    self._safe_print(f"  ❌ CRITICAL: Could not find pre-resolved version for '{pkg_spec}'. Skipping.")
                    continue
                pkg_name, requested_version = self.core._parse_package_spec(resolved_spec)
                self._safe_print(f"  -> Using pre-flight resolved version: {resolved_spec}")

            active_omnipkg_version = self.core._get_active_version_from_environment("omnipkg")

            if (
                not force_reinstall
                and active_omnipkg_version
                and parse_version(requested_version) == parse_version(active_omnipkg_version)
            ):
                self._safe_print(f"✅ omnipkg=={requested_version} is already the active version. No action needed.")
                continue

            is_upgrade = active_omnipkg_version and parse_version(requested_version) > parse_version(active_omnipkg_version)
            is_downgrade = active_omnipkg_version and parse_version(requested_version) < parse_version(active_omnipkg_version)

            if is_upgrade or is_downgrade:
                action = "Upgrading" if is_upgrade else "Downgrading"
                self._safe_print(f"🔄 {action} omnipkg from v{active_omnipkg_version} to v{requested_version}...")

                if active_omnipkg_version:
                    bubble_path = self.multiversion_base / f"omnipkg-{active_omnipkg_version}"
                    if not bubble_path.exists():
                        self._safe_print(f"🫧 Queuing bubble for current omnipkg v{active_omnipkg_version} (background)...")
                        # Queue instead of blocking on create_bubble_for_package
                        _pending_bubble_tasks.append({
                            "type": "create_bubble_for_package",
                            "pkg_name": "omnipkg",
                            "version": active_omnipkg_version,
                            "python_context_version": python_context_version,
                        })

                self._safe_print(f"📦 Installing omnipkg=={requested_version} to main environment...")
                t_pip = time.perf_counter()
                return_code, _ = self.core._run_pip_install(
                    [f"omnipkg=={requested_version}"],
                    target_directory=None,
                    force_reinstall=force_reinstall,
                )
                _tprint("omnipkg pip_install", t_pip)
                if return_code == 0:
                    any_installations_made = True
                    main_env_kb_updates["omnipkg"] = requested_version
                    self._safe_print(f"✅ omnipkg successfully {action.lower()}d to v{requested_version}!")
                else:
                    self._safe_print(f"❌ Failed to install omnipkg=={requested_version}.")
            else:
                bubble_path = self.multiversion_base / f"omnipkg-{requested_version}"
                if bubble_path.exists() and not force_reinstall:
                    self._safe_print(f"✅ Bubble for omnipkg=={requested_version} already exists.")
                    continue
                self._safe_print(f"🫧 Queuing isolated bubble for omnipkg v{requested_version} (background)...")
                _pending_bubble_tasks.append({
                    "type": "create_isolated_bubble",
                    "pkg_name": "omnipkg",
                    "version": requested_version,
                    "python_context_version": python_context_version,
                    "needs_kb_sync": True,  # original called _synchronize_knowledge_base_with_reality after
                })
                any_installations_made = True  # bg will handle KB

        if not packages_to_process:
            _tprint("pre_complete_fence", t_install_start)
            self._safe_print("\n🎉 All package operations complete.")
            if original_strategy and original_strategy != self.config.get("install_strategy"):
                self.config["install_strategy"] = original_strategy
                self._safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")
            return 0

        # ================================================================
        # Resolve + sort packages
        # ================================================================
        self._safe_print(f"🚀 Starting install with policy: '{install_strategy}'")

        t_omnipkg_scan = time.perf_counter()
        # (omnipkg special-case loop runs here)
        _tprint("omnipkg_special_scan", t_omnipkg_scan)

        t_resolve = time.perf_counter()
        try:
            if not force_reinstall and resolved_package_cache:
                resolved_packages = []
                for orig_pkg in packages_to_process:
                    if orig_pkg in resolved_package_cache:
                        resolved_packages.append(resolved_package_cache[orig_pkg])
                    else:
                        fresh = self.core._resolve_package_versions([orig_pkg])
                        if fresh:
                            resolved_packages.extend(fresh)
            else:
                resolved_packages = self.core._resolve_package_versions(packages_to_process)

            if not resolved_packages:
                self._safe_print("❌ Could not resolve any packages to install. Aborting.")
                return 1

            sorted_packages = self.core._sort_packages_for_install(
                resolved_packages, strategy=install_strategy
            )
        except Exception as e:
            if self._is_quantum_error(e):
                return self._handle_quantum_healing(
                    e, packages, dry_run, force_reinstall,
                    override_strategy, target_directory,
                    index_url, extra_index_url,
                )
            self._safe_print(f"\n❌ Resolution failed: {e}")
            return 1
        _tprint("resolve+sort", t_resolve)

        t_cnames = time.perf_counter()
        user_requested_cnames = {
            canonicalize_name(self.core._parse_package_spec(p)[0]) for p in packages
        }
        _tprint("build_cnames", t_cnames)

        # No pre-install live scan — uv stderr tells us exactly what changed.
        # Background does its own scan for KB/snapshot work.
        initial_packages_before: Dict[str, str] = {}
        packages_before: Dict[str, str] = {}
        _live_cache: Dict[str, str] = {}

        # ================================================================
        # Main install loop — pip installs only, NO bubble creation here
        # ================================================================
        for package_spec in sorted_packages:
            try:
                self._safe_print("\n" + "─" * 60)
                t_loop_entry = time.perf_counter()
                pkg_name, pkg_version = self.core._parse_package_spec(package_spec)
                _tprint(f"loop_parse_spec:{package_spec}", t_loop_entry)
                snapshot_key = None  # snapshot deferred to background

                if force_reinstall:
                    is_installed, _chk_time = self.core.check_package_installed_fast(
                        configured_exe, pkg_name, pkg_version
                    )
                    if is_installed:
                        self._safe_print(f"🔨 Force Reinstalling: {package_spec} (existing {is_installed})")
                    else:
                        self._safe_print(f"📦 Processing: {package_spec}")
                else:
                    self._safe_print(f"📦 Processing: {package_spec}")
                    self._safe_print("─" * 60)
                    # satisfaction_check removed — preflight already confirmed work needed;
                    # uv handles the already-satisfied case in ~4ms if it races

                # OPTIMIZATION: Reuse the cached state from the previous iteration (or initial scan)
                packages_before = _live_cache.copy()

                # ── BUBBLE HARD-LINK FAST-PATH ───────────────────────────
                # 1. uv --dry-run to get exact +/- list (real deps, no guessing)
                # 2. Check ALL + packages have bubbles
                # 3. If yes: parse each bubble's RECORD, hard-link only that
                #    package's own files (not bundled deps) into site-packages
                # 4. If any bubble missing: fall back to real uv
                t_pip = time.perf_counter()

                return_code, pkg_install_output = self.core._run_pip_install(
                    [package_spec],
                    target_directory=target_directory,
                    force_reinstall=force_reinstall,
                    index_url=index_url,
                    extra_index_url=extra_index_url,
                )
                _tprint(f"pip_install:{pkg_name}", t_pip)

                if return_code != 0:
                    self._safe_print(f"❌ Pip installation failed for {package_spec}.")
                    prior_ver = packages_before.get(pkg_name.lower())
                    if prior_ver:
                        self._safe_print(f"   🔄 Rolling back to {pkg_name}=={prior_ver}...")
                        rb_code, _ = self.core._run_pip_install(
                            [f"{pkg_name}=={prior_ver}"], force_reinstall=True
                        )
                        if rb_code == 0:
                            self._safe_print(f"   ✅ Rolled back to {pkg_name}=={prior_ver}")
                        else:
                            self._safe_print(f"   ❌ Rollback failed. Run: 8pkg revert")
                    else:
                        self._safe_print("   💡 No prior version known. Run: 8pkg revert")
                    continue

                any_installations_made = True
                t_get_after = time.perf_counter()
                _uv_stderr = pkg_install_output.get("stderr", "")
                if _uv_stderr and (" - " in _uv_stderr or " + " in _uv_stderr):
                    # Reconstruct packages_before from uv "-" lines so bubble queuing works
                    for _ul in _uv_stderr.splitlines():
                        _ul = _ul.strip()
                        if _ul.startswith("- ") and "==" in _ul:
                            _up, _uv = _ul[2:].split("==", 1)
                            packages_before[_up.strip().lower()] = _uv.strip()
                    # Fast path: derive state from uv's own diff output — no filesystem scan
                    packages_after = self._parse_uv_changes(_uv_stderr, packages_before)
                    _tprint(f"get_installed_after:{pkg_name}(uv-fast)", t_get_after)
                else:
                    # No +/- diff from uv = infer (no changes, or pip fallback)
                    packages_after = dict(packages_before)
                    packages_after[pkg_name.lower()] = pkg_version
                    _tprint(f"get_installed_after:{pkg_name}(inferred)", t_get_after)
                final_main_state = packages_after.copy()
                _live_cache = packages_after.copy()
                t_detect = time.perf_counter()
                all_changes = self.core._detect_all_changes(packages_before, packages_after)
                _tprint(f"change_detection:{pkg_name}", t_detect)

                if all_changes["downgrades"] or all_changes["upgrades"] or all_changes["removals"]:
                    total_changes = len(all_changes["downgrades"] + all_changes["upgrades"] + all_changes["removals"])
                    self._safe_print(f"\n⚠️  Detected {total_changes} dependency changes:")
                    for change in all_changes["downgrades"]:
                        self._safe_print(f"   ⬇️  {change['package']}: v{change['old_version']} → v{change['new_version']} (downgrade)")
                    for change in all_changes["upgrades"]:
                        self._safe_print(f"   ⬆️  {change['package']}: v{change['old_version']} → v{change['new_version']} (upgrade)")
                    for change in all_changes["removals"]:
                        self._safe_print(f"   🗑️  {change['package']}: v{change['version']} (removed)")

                # --------------------------------------------------------
                # Strategy: queue bubble work instead of blocking on it
                # --------------------------------------------------------
                if install_strategy == "stable-main":
                    packages_to_bubble = []
                    for change in all_changes["downgrades"] + all_changes["upgrades"]:
                        packages_to_bubble.append({
                            "package": change["package"],
                            "new_version": change["new_version"],
                            "old_version": change["old_version"],
                        })

                    if packages_to_bubble:
                        self._safe_print(f"\n🛡️ STABILITY PROTECTION: Queuing {len(packages_to_bubble)} bubble(s) for background")

                        # We still need the restore-to-stable-version here because stable-main
                        # MUST have the old version active. But bubble creation moves to bg.
                        # Ordering guarantee: restore pip install runs now; bubble creation
                        # in bg uses the already-installed new_version staging area.
                        restore_specs = [
                            f"{item['package']}=={item['old_version']}"
                            for item in packages_to_bubble
                        ]
                        t_restore = time.perf_counter()
                        restore_code, _ = self.core._run_pip_install(
                            restore_specs, force_reinstall=True, extra_flags=["--no-deps"]
                        )
                        _tprint(f"stable_restore:{pkg_name}", t_restore)

                        if restore_code == 0:
                            self._safe_print("   ✅ Stable versions restored to main env")
                            for item in packages_to_bubble:
                                main_env_kb_updates[item["package"]] = item["old_version"]
                                protected_from_cleanup.add(canonicalize_name(item["package"]))
                                bubbled_kb_updates[item["package"]] = item["new_version"]
                                # Queue bubble task for background
                                _pending_bubble_tasks.append({
                                    "type": "create_isolated_bubble",
                                    "pkg_name": item["package"],
                                    "version": item["new_version"],
                                    "python_context_version": python_context_version,
                                    "index_url": index_url,
                                    "extra_index_url": extra_index_url,
                                    "observed_dependencies": dict(packages_after),
                                    "snapshot_key": snapshot_key,
                                    "snapshot_fallback_pkg": pkg_name,
                                })
                                self._safe_print(f"   🫧 Queued bubble: {item['package']} v{item['new_version']} (background)")
                        else:
                            self._safe_print("   ❌ Restore failed — using snapshot fallback")
                            snapshot_data = self.cache_client.get(snapshot_key)
                            if snapshot_data:
                                snapshot_state = json.loads(snapshot_data)
                                self.core._safe_restore_from_snapshot(pkg_name, snapshot_state, force=True)

                elif install_strategy == "latest-active":
                    for pkg in set(packages_before.keys()) | set(packages_after.keys()):
                        old_version = packages_before.get(pkg)
                        new_version = packages_after.get(pkg)
                        if old_version and new_version and old_version != new_version:
                            bubbled_kb_updates[pkg] = old_version
                            main_env_kb_updates[pkg] = new_version
                            _pending_bubble_tasks.append({
                                "type": "create_isolated_bubble",
                                "pkg_name": pkg,
                                "version": old_version,
                                "python_context_version": python_context_version,
                                "index_url": index_url,
                                "extra_index_url": extra_index_url,
                                # hook_manager refresh/validate also deferred to bg
                                "do_hook_refresh": True,
                                "version_staying_active": new_version,
                            })
                            self._safe_print(
                                f"    🫧 Queued bubble for {pkg} v{old_version}, "
                                f"keeping v{new_version} active"
                            )
                        elif not old_version and new_version:
                            main_env_kb_updates[pkg] = new_version

            except Exception as e:
                if self._is_quantum_error(e):
                    return self._handle_quantum_healing(
                        e, packages, dry_run, force_reinstall,
                        override_strategy, target_directory,
                        index_url, extra_index_url,
                    )
                if isinstance(e, ValueError):
                    self._safe_print(f"\n❌ Aborting installation: {e}")
                    return 1
                raise

        # ================================================================
        # Shell return path — fork EVERYTHING remaining
        # ================================================================
        self._safe_print("\n🎉 All package operations complete.")

        if original_strategy and original_strategy != self.config.get("install_strategy"):
            self.config["install_strategy"] = original_strategy
            self._safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")

        _tprint("foreground_total", t_install_start)
        print(f"[WALL] pre-fork: {(time.perf_counter()-t_install_start)*1000:.1f}ms elapsed", flush=True)

        if any_installations_made or _pending_bubble_tasks:
            _bg_data = {
                "force_reinstall": force_reinstall,
                "protected_from_cleanup": list(protected_from_cleanup),
                "initial_packages": dict(initial_packages_before),
                "final_main_state": dict(final_main_state),
                "main_env_kb_updates": dict(main_env_kb_updates),
                "bubbled_kb_updates": dict(bubbled_kb_updates),
                "python_context_version": python_context_version,
                "priority_specs": self._build_priority_specs(
                    initial_packages_before, final_main_state,
                    main_env_kb_updates, bubbled_kb_updates,
                ),
                "bubble_paths_to_scan": {
                    pkg: str(self.multiversion_base / f"{pkg}-{ver}")
                    for pkg, ver in bubbled_kb_updates.items()
                    if (self.multiversion_base / f"{pkg}-{ver}").exists()
                },
                "pending_bubble_tasks": _pending_bubble_tasks,
                "run_doctor": True,
            }

            os.environ["OMNIPKG_BG_WORKER"] = "1"
            _bg_log_path = "/tmp/omnipkg_bg_latest.log"
            print(f"   📋 BG log: cat {_bg_log_path}", flush=True)

            # Double-fork: parent forks intermediate, intermediate forks grandchild
            # then exits immediately. Parent never waits — intermediate becomes a
            # brief zombie until the daemon's event loop reaps it naturally.
            # Grandchild is reparented to init and runs freely.
            _mid_pid = os.fork()
            if _mid_pid == 0:
                # intermediate child
                _gc_pid = os.fork()
                if _gc_pid == 0:
                    # grandchild — do all the work
                    try:
                        self._run_background(_bg_data, self.core)
                    except Exception:
                        pass
                    os._exit(0)
                else:
                    os._exit(0)  # intermediate exits, grandchild orphaned to init

            # parent continues immediately, never waits
            self._safe_print(f"   🔄 Background tasks running")

        return 0

    # -----------------------------------------------------------------------
    # Background child — runs after fork, owns all heavy work
    # -----------------------------------------------------------------------

    def _run_background(self, _bg_data: dict, _fg_core=None) -> None:
        import datetime

        # We are the grandchild process — fully detach from the parent's session
        # so TTY signals and terminal close don't affect us.
        try:
            os.setsid()
        except PermissionError:
            pass  # already a session leader (shouldn't happen, but safe to ignore)

        log_path = f"/tmp/omnipkg_bg_{os.getpid()}.log"
        _log = open(log_path, "w", buffering=1)

        # Redirect stdin/stdout/stderr so we're fully detached from the terminal
        try:
            _devnull_r = open(os.devnull, "r")
            os.dup2(_devnull_r.fileno(), 0)
            os.dup2(_log.fileno(), 1)
            os.dup2(_log.fileno(), 2)
            sys.stdout = _log
            sys.stderr = _log
        except Exception:
            pass

        try:
            _sym = "/tmp/omnipkg_bg_latest.log"
            if os.path.islink(_sym) or os.path.exists(_sym):
                os.remove(_sym)
            os.symlink(log_path, _sym)
        except Exception:
            pass

        def bg(msg: str) -> None:
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            _log.write(f"[{ts}] {msg}\n")
            _log.flush()

        timer = _BgTimer(bg)
        bg("=== omnipkg background tasks started (parent install finished) ===")
        import traceback as _traceback
        t_bg_total = time.perf_counter()

        try:
            # Use the already-initialized core passed from the parent process.
            # After fork() it's live in memory — no need to re-init (saves ~80ms).
            if _fg_core is not None:
                core = _fg_core
                bg("Using forked parent core (no re-init)")
            else:
                import omnipkg.cli as _cli_mod
                core = _cli_mod._PRELOADED_CORE
                if core is None:
                    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
                    core = OmnipkgCore(ConfigManager())
                    bg("WARNING: cold core init in background (preloaded core unavailable)")

            python_context_version = _bg_data["python_context_version"]

            # ----------------------------------------------------------
            # -1. Doctor + heal (moved from foreground)
            # ----------------------------------------------------------
            if _bg_data.get("run_doctor"):
                t0 = time.perf_counter()
                bg("Running doctor+heal...")
                try:
                    core.doctor(dry_run=False, force=True)
                    core._heal_conda_environment()
                except Exception as _de:
                    bg(f"doctor error: {_de}")
                timer.stage("doctor+heal", t0)

            # ----------------------------------------------------------
            # 0. Bubble creation — the big one, now lives here
            # ----------------------------------------------------------
            # Nuke stale claim markers — each bg task owns its own bubbles
            try:
                import glob as _glob
                for _m in _glob.glob(os.path.join(_TASK_DIR, "claimed_*.marker")):
                    os.unlink(_m)
            except Exception:
                pass
            pending = _bg_data.get("pending_bubble_tasks", [])
            if pending:
                bg(f"Bubble creation: {len(pending)} task(s)")
                for task in pending:
                    t0 = time.perf_counter()
                    pkg_name = task["pkg_name"]
                    version = task["version"]
                    task_type = task.get("type", "create_isolated_bubble")

                    # Atomic claim — skip if another worker already handling this
                    _my_bubbles = _bg_data.get("my_bubbles", set())
                    if not _claim_bubble_task(pkg_name, version) and f"{pkg_name}=={version}" not in _my_bubbles:
                        bg(f"  Skipping {pkg_name}=={version} — already claimed by another worker")
                        continue

                    try:
                        with _BubbleLock(pkg_name, version, blocking=True):
                            # Double-check bubble doesn't already exist (race with another swap)
                            bubble_path = Path(core.multiversion_base) / f"{pkg_name}-{version}"
                            if bubble_path.exists() and not _bg_data.get("force_reinstall"):
                                bg(f"  Bubble already exists: {pkg_name}=={version} — skipping")
                                continue

                            if task_type == "create_bubble_for_package":
                                bg(f"  Creating bubble (for_package): {pkg_name}=={version}")
                                result = core.bubble_manager.create_bubble_for_package(
                                    pkg_name, version,
                                    python_context_version=python_context_version,
                                )
                            else:
                                bg(f"  Creating isolated bubble: {pkg_name}=={version}")
                                result = core.bubble_manager.create_isolated_bubble(
                                    pkg_name, version,
                                    python_context_version=python_context_version,
                                    index_url=task.get("index_url"),
                                    extra_index_url=task.get("extra_index_url"),
                                    observed_dependencies=task.get("observed_dependencies"),
                                )

                            if result:
                                bg(f"  ✅ Bubble created: {pkg_name}=={version}")
                                # Hook refresh/validate (was foreground in latest-active)
                                if task.get("do_hook_refresh"):
                                    try:
                                        core.hook_manager.refresh_bubble_map(
                                            pkg_name, version, str(bubble_path)
                                        )
                                        core.hook_manager.validate_bubble(pkg_name, version)
                                    except Exception as _he:
                                        bg(f"  hook_manager error for {pkg_name}: {_he}")
                                # If omnipkg bubble needed a sync
                                if task.get("needs_kb_sync"):
                                    try:
                                        core._synchronize_knowledge_base_with_reality()
                                    except Exception as _se:
                                        bg(f"  sync after omnipkg bubble error: {_se}")
                            else:
                                bg(f"  ❌ Bubble FAILED: {pkg_name}=={version}")
                                # stable-main: attempt snapshot fallback
                                if task.get("snapshot_key") and task.get("snapshot_fallback_pkg"):
                                    try:
                                        snapshot_data = core.cache_client.get(task["snapshot_key"])
                                        if snapshot_data:
                                            snapshot_state = json.loads(snapshot_data)
                                            if core._safe_restore_from_snapshot(
                                                task["snapshot_fallback_pkg"], snapshot_state, force=True
                                            ):
                                                bg(f"  ✅ Snapshot restore succeeded for {task['snapshot_fallback_pkg']}")
                                            else:
                                                bg(f"  ❌ Snapshot restore also failed!")
                                    except Exception as _rfe:
                                        bg(f"  Snapshot restore error: {_rfe}")

                    except BubbleLockBusy:
                        bg(f"  Lock busy for {pkg_name}=={version} — another worker is handling it")
                    except Exception as _be:
                        bg(f"  Bubble error {pkg_name}=={version}: {_be}\n{traceback.format_exc()}")
                    finally:
                        _release_bubble_claim(pkg_name, version)

                    timer.stage(f"bubble:{pkg_name}=={version}", t0)

            # ----------------------------------------------------------
            # 1. KB priority update
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            priority_specs = _bg_data["priority_specs"]
            if priority_specs:
                bg(f"KB update: {len(priority_specs)} priority spec(s): {priority_specs}")
                try:
                    from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                    gatherer = omnipkgMetadataGatherer(
                        config=core.config,
                        env_id=core.env_id,
                        target_context_version=python_context_version,
                        force_refresh=True,
                        omnipkg_instance=core,
                    )
                    gatherer.cache_client = core.cache_client
                    gatherer.run(targeted_packages=list(priority_specs))
                    bg("KB gatherer.run() complete")
                except Exception as _e:
                    bg(f"KB gatherer error: {_e}\n{traceback.format_exc()}")
            timer.stage("kb_gatherer", t0)

            # ----------------------------------------------------------
            # 1b. Stale dist-info cleanup
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            try:
                import glob as _glob
                import shutil as _shutil2
                sp = core.config.get("site_packages_path", "")
                for pkg_name, new_active_ver in _bg_data["main_env_kb_updates"].items():
                    pkg_norm = pkg_name.replace("-", "_").lower()
                    pattern = os.path.join(sp, f"{pkg_norm}-*.dist-info")
                    for di in _glob.glob(pattern):
                        di_path = Path(di)
                        di_ver = di_path.name.replace(f"{pkg_norm}-", "").replace(".dist-info", "")
                        if di_ver != new_active_ver and di_path.is_dir():
                            _shutil2.rmtree(str(di_path), ignore_errors=True)
                            bg(f"  Removed stale dist-info: {di_path.name}")
            except Exception as _sde:
                bg(f"  Stale dist-info cleanup error: {_sde}")
            timer.stage("stale_distinfo_cleanup", t0)

            # ----------------------------------------------------------
            # 2. Demote stale active KB records
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Demoting stale active KB records...")
            for pkg_name, new_active_ver in _bg_data["main_env_kb_updates"].items():
                try:
                    inst_pattern = f"omnipkg:env_{core.env_id}:py3.11:inst:{pkg_name}:*"
                    for inst_key in (core.cache_client.keys(inst_pattern) or []):
                        inst_ver = core.cache_client.hget(inst_key, "Version")
                        inst_path = core.cache_client.hget(inst_key, "path") or ""
                        if inst_ver and inst_ver != new_active_ver:
                            current_type = core.cache_client.hget(inst_key, "install_type")
                            if current_type == "active":
                                core.cache_client.hset(inst_key, "install_type", "bubble")
                                bg(f"  Demoted {pkg_name}=={inst_ver} active→bubble (path={inst_path})")
                        elif inst_ver == new_active_ver:
                            if inst_path and not os.path.exists(inst_path):
                                core.cache_client.delete(inst_key)
                                bg(f"  Deleted ghost inst key for {pkg_name}=={inst_ver} (path gone: {inst_path})")
                except Exception as _de:
                    bg(f"  Demotion error for {pkg_name}: {_de}")
            timer.stage("kb_demotion", t0)

            # ----------------------------------------------------------
            # 3. Bubble cleanup
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Running bubble cleanup...")
            try:
                if not _bg_data["force_reinstall"]:
                    core._cleanup_redundant_bubbles(
                        protected_packages=set(_bg_data["protected_from_cleanup"]),
                        known_active=_bg_data["final_main_state"] or None,
                    )
                    bg("Bubble cleanup complete")
            except Exception as _e:
                bg(f"Bubble cleanup error: {_e}\n{traceback.format_exc()}")
            timer.stage("bubble_cleanup", t0)

            # ----------------------------------------------------------
            # 4. Full KB sync
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Running _synchronize_knowledge_base_with_reality()...")
            try:
                core._synchronize_knowledge_base_with_reality()
                bg("KB sync complete")
            except Exception as _e:
                bg(f"KB sync error: {_e}\n{traceback.format_exc()}")
            timer.stage("kb_sync", t0)

            # ----------------------------------------------------------
            # 5. Snapshot
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Saving snapshot...")
            try:
                core._save_last_known_good_snapshot(
                    known_state=_bg_data["final_main_state"] or None
                )
                bg("Snapshot saved")
            except Exception as _e:
                bg(f"Snapshot error: {_e}\n{traceback.format_exc()}")
            timer.stage("snapshot", t0)

            # ----------------------------------------------------------
            # 6. Hash index
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Updating hash index...")
            try:
                core._update_hash_index_for_delta(
                    _bg_data["initial_packages"],
                    _bg_data["final_main_state"],
                )
                bg("Hash index updated")
            except Exception as _e:
                bg(f"Hash index error: {_e}\n{traceback.format_exc()}")
            timer.stage("hash_index", t0)

            # ----------------------------------------------------------
            # 7. Cloak cleanup
            # ----------------------------------------------------------
            t0 = time.perf_counter()
            bg("Running cloak cleanup...")
            try:
                core._cleanup_all_cloaks_globally()
                bg("Cloak cleanup complete")
            except Exception as _e:
                bg(f"Cloak cleanup error: {_e}\n{traceback.format_exc()}")
            timer.stage("cloak_cleanup", t0)

            timer.stage("bg_total", t_bg_total)
            timer.summary()
            bg(f"=== background tasks complete in {(time.perf_counter()-t_bg_total)*1000:.0f}ms ===")

        except Exception as _fatal:
            try:
                _log.write(f"[FATAL] {_fatal}\n{traceback.format_exc()}\n")
                _log.flush()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_priority_specs(
        self,
        initial_packages: Dict[str, str],
        final_main_state: Dict[str, str],
        main_env_kb_updates: Dict[str, str],
        bubbled_kb_updates: Dict[str, str],
    ) -> list:
        specs = set()
        for name, ver in final_main_state.items():
            if name not in initial_packages or initial_packages[name] != ver:
                specs.add(f"{name}=={ver}")
        for pkg, ver in bubbled_kb_updates.items():
            specs.add(f"{pkg}=={ver}")
        for pkg, ver in main_env_kb_updates.items():
            specs.add(f"{pkg}=={ver}")
        return list(specs)

    def _handle_quantum_healing(
        self,
        error,
        packages,
        dry_run,
        force_reinstall,
        override_strategy,
        target_directory,
        index_url=None,
        extra_index_url=None,
    ) -> int:
        from omnipkg.cli import handle_python_requirement
        from omnipkg.core import ConfigManager

        self._safe_print("\n" + "=" * 60)
        self._safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected")
        self._safe_print("=" * 60)

        compatible_py = getattr(error, "compatible_python", None)
        pkg_name = getattr(error, "package_name", "unknown")
        self._safe_print(f"   - Diagnosis: Cannot install '{pkg_name}' on current Python.")
        if compatible_py:
            self._safe_print(f"   - Prescription: This package requires Python {compatible_py}.")

        if not compatible_py or compatible_py == "unknown":
            self._safe_print("❌ Healing failed: Could not determine compatible Python version.")
            return 1

        if not handle_python_requirement(compatible_py, self.core, "omnipkg"):
            self._safe_print(f"❌ Healing failed: Could not automatically switch to Python {compatible_py}.")
            return 1

        self._safe_print(f"\n🚀 Retrying original command in new Python {compatible_py} context...")
        new_core = self.core.__class__(ConfigManager())
        return SmartInstaller(new_core).install(
            packages,
            dry_run=dry_run,
            force_reinstall=force_reinstall,
            override_strategy=override_strategy,
            target_directory=target_directory,
            index_url=index_url,
            extra_index_url=extra_index_url,
        )