"""
omnipkg/installation/smart_install.py

SmartInstaller: orchestrates package installation for OmnipkgCore.
core.py delegates via a one-liner shim; all callers use core.smart_install() unchanged.

Key design:
  - uv-ffi plan callback intercepts bubble swaps atomically via os.rename (sub-ms)
  - Bubble creation (create_isolated_bubble / install_and_verify) lives here
  - KB update stays on the foreground critical path (loader depends on it)
  - Background fork owns: snapshot, hash index, redundant bubble cleanup
"""

from __future__ import annotations

import contextlib
import sys
if sys.platform != "win32":
    import fcntl
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from packaging.utils import canonicalize_name
from packaging.version import Version as parse_version

if TYPE_CHECKING:
    from omnipkg.core import omnipkg as OmnipkgCore

try:
    from omnipkg.common_utils import safe_print
except ImportError:
    safe_print = print

try:
    from omnipkg.installation.installers import _pop_callback_result
except ImportError:
    def _pop_callback_result():
        return None, None

try:
    from omnipkg.i18n import _
except ImportError:
    unused = lambda s: s  # noqa: E731


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _DEBUG_TIMING() -> bool:
    return os.environ.get("OMNIPKG_DEBUG", "0") == "1"


def _tprint(label: str, t0: float) -> None:
    if _DEBUG_TIMING():
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"[TIMING] {label}: {elapsed:.2f}ms", flush=True)


# ---------------------------------------------------------------------------
# Per-package bubble lock
# ---------------------------------------------------------------------------

_LOCK_DIR = "/tmp/omnipkg/locks"


class BubbleLockBusy(Exception):
    pass


class _BubbleLock:
    def __init__(self, pkg_name: str, version: str, blocking: bool = True):
        os.makedirs(_LOCK_DIR, exist_ok=True)
        safe = f"{pkg_name.replace('/', '_')}-{version}"
        self.path = os.path.join(_LOCK_DIR, f"{safe}.lock")
        self.blocking = blocking
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "w")
        try:
            if _sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK if not self.blocking else msvcrt.LK_LOCK, 1)
            else:
                flag = fcntl.LOCK_EX if self.blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(self._fd, flag)
        except (BlockingIOError, OSError):
            self._fd.close()
            self._fd = None
            raise BubbleLockBusy(f"Another process is already creating bubble for {self.path}")
        return self
    def __exit__(self, *unused):
        if self._fd:
            if _sys.platform != "win32":
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()


# ---------------------------------------------------------------------------
# Background task queue
# ---------------------------------------------------------------------------

_TASK_DIR = "/tmp/omnipkg/bg_queue"


def _claim_bubble_task(pkg_name: str, version: str) -> bool:
    os.makedirs(_TASK_DIR, exist_ok=True)
    safe = f"{pkg_name.replace('/', '_')}-{version}"
    marker = os.path.join(_TASK_DIR, f"claimed_{safe}.marker")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            with open(marker) as mf:
                owner_pid = int(mf.read().strip())
            try:
                os.kill(owner_pid, 0)
                return False
            except (ProcessLookupError, PermissionError):
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


# ===========================================================================
# Helpers
# ===========================================================================

def _get_package_toplevel_items(sp: Path, pkg_name: str, version: str) -> list:
    """
    Read RECORD from dist-info to find ALL top-level site-packages items owned
    by this package — e.g. numpy/, numpy.libs/, numpy-1.26.4.dist-info/.
    """
    # Packages often use underscores in filenames even if the canonical name has dashes
    dist_info = sp / f"{pkg_name}-{version}.dist-info"
    alt_name = pkg_name.replace('-', '_')
    if not dist_info.exists():
        dist_info = sp / f"{alt_name}-{version}.dist-info"
        if not dist_info.exists():
            # Total fallback if we can't find the dist-info
            return [pkg_name, alt_name, f"{pkg_name}-{version}.dist-info", f"{alt_name}-{version}.dist-info"]
            
    base_dist_info = dist_info.name
    top_level: set = {base_dist_info}
    record = dist_info / "RECORD"
    
    if record.exists():
        try:
            with open(record) as f:
                for line in f:
                    part = line.split(",")[0].strip()
                    if not part:
                        continue
                    top = part.split("/")[0]
                    if top and not top.startswith("..") and (sp / top).exists():
                        top_level.add(top)
        except Exception:
            pass
    else:
        # fallback: no RECORD — just guess pkg dirs
        top_level.add(pkg_name)
        top_level.add(alt_name)
        
    return list(top_level)


# ===========================================================================
# SmartInstaller
# ===========================================================================

class SmartInstaller:
    """
    Orchestrates package installation for OmnipkgCore.
    Instantiated fresh per install call via the core.smart_install() shim.
    """

    def __init__(self, core: "OmnipkgCore") -> None:
        self.core = core
        self.config = core.config
        self.config_manager = core.config_manager
        self.multiversion_base: Path = core.multiversion_base
        self.env_id = core.env_id
        self.cache_client = core.cache_client

    # --- property shims so methods can use self.X as before ---

    @property
    def bubble_manager(self):
        return self.core.bubble_manager

    @property
    def hook_manager(self):
        return self.core.hook_manager

    @property
    def site_packages(self) -> Path:
        return Path(self.config.get("site_packages_path", ""))

    # --- delegate frequently-called core methods ---

    def _parse_package_spec(self, spec):
        return self.core._parse_package_spec(spec)

    def _bare_name(self, name):
        return self.core._bare_name(name)

    def check_package_installed_fast(self, *a, **kw):
        return self.core.check_package_installed_fast(*a, **kw)

    def _find_best_version_for_spec(self, *a, **kw):
        return self.core._find_best_version_for_spec(*a, **kw)

    def _get_latest_version_from_pypi(self, *a, **kw):
        return self.core._get_latest_version_from_pypi(*a, **kw)

    def _resolve_spec_with_pip(self, *a, **kw):
        return self.core._resolve_spec_with_pip(*a, **kw)

    def _resolve_package_versions(self, *a, **kw):
        return self.core._resolve_package_versions(*a, **kw)

    def _sort_packages_for_install(self, *a, **kw):
        return self.core._sort_packages_for_install(*a, **kw)

    def _check_package_satisfaction(self, *a, **kw):
        return self.core._check_package_satisfaction(*a, **kw)

    def _detect_all_changes(self, *a, **kw):
        return self.core._detect_all_changes(*a, **kw)

    def _run_pip_install(self, *a, **kw):
        return self.core._run_pip_install(*a, **kw)

    def get_installed_packages(self, *a, **kw):
        return self.core.get_installed_packages(*a, **kw)

    @contextlib.contextmanager
    def _stash_for_stable_main(self, packages_before: dict, python_context_version: str,
                                index_url=None, extra_index_url=None):
        """
        Stable-main fast path: instead of install→create_bubble→restore, we:
        1. Rename current pkg dirs to *.___stash___ so uv skips uninstalling them
        2. Let uv install the new version into main normally
        3. Move the newly installed version to a bubble (verify it there)
        4. Rename stash back to main

        Yields a result dict that gets populated after the install:
          {'bubbled': {pkg: (new_ver, old_ver)}, 'failed': [pkg]}
        Falls back cleanly if anything goes wrong — stash always gets restored.
        """
        sp = self.site_packages
        versions = self.multiversion_base

        # Collect what's currently in main that we want to preserve
        # (only packages uv is likely to uninstall — populated by caller)
        stashed: dict[str, tuple[str, list[Path]]] = {}  # pkg → (version, [stashed_paths])
        result = {'bubbled': {}, 'failed': [], 'used_stash': False}

        try:
            # Phase 1: stash current pkg dirs before uv runs
            for pkg_name, old_ver in packages_before.items():
                dirs_to_stash = []
                for suffix in _get_package_toplevel_items(sp, pkg_name, old_ver):
                    src = sp / suffix
                    if src.exists():
                        stash_dst = sp / f'{suffix}.___stash___'
                        if stash_dst.exists():
                            shutil.rmtree(stash_dst) if stash_dst.is_dir() else stash_dst.unlink()
                        os.rename(src, stash_dst)
                        dirs_to_stash.append((stash_dst, src))
                if dirs_to_stash:
                    stashed[pkg_name] = (old_ver, dirs_to_stash)
                    result['used_stash'] = True

            yield result  # caller runs _run_pip_install here

            # Phase 2: move what uv installed → bubble, restore stash → main
            for pkg_name, (old_ver, stash_pairs) in stashed.items():
                new_ver = result.get('new_versions', {}).get(pkg_name)
                if not new_ver:
                    continue  # uv didn't install this pkg, just restore

                bubble_path = versions / f'{pkg_name}-{new_ver}'
                bubble_path.mkdir(parents=True, exist_ok=True)

                # Move uv's new install from main → bubble
                moved_to_bubble = False
                for suffix in _get_package_toplevel_items(sp, pkg_name, new_ver):
                    src = sp / suffix
                    dst = bubble_path / suffix
                    if src.exists():
                        if dst.exists():
                            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                        os.rename(src, dst)
                        moved_to_bubble = True

                if not moved_to_bubble:
                    result['failed'].append(pkg_name)
                    continue

                # Copy transitive deps into the bubble so the verifier finds them.
                # We copy (not rename) because deps remain in main env too.
                # resolved_deps is pre-computed by the caller via _collect_resolved_deps
                # and stored in result before _stash_ctx.__exit__ is called.
                resolved_deps = result.get('resolved_deps', {})
                pkg_cname = canonicalize_name(pkg_name)
                for dep_name, dep_ver in resolved_deps.items():
                    if canonicalize_name(dep_name) == pkg_cname:
                        continue  # already moved above
                        
                    # FIX: Use the helper to get ALL folders, including .libs!
                    for suffix in _get_package_toplevel_items(sp, dep_name, dep_ver):
                        src = sp / suffix
                        dst = bubble_path / suffix
                        try:
                            if src.exists() and not dst.exists():
                                if src.is_dir():
                                    shutil.copytree(src, dst)
                                else:
                                    shutil.copy2(src, dst)
                        except Exception:
                            pass  # best-effort; verifier will catch missing deps

                # Verify the bubble in-place — files are already there from the rename,
                # so we call the verifier directly instead of install_and_verify (which
                # would nuke bubble_path and reinstall from scratch).
                try:
                    from omnipkg.installation.verification_strategy import verify_bubble_with_smart_strategy
                    from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                    gatherer = omnipkgMetadataGatherer(
                        config=self.config,
                        env_id=self.env_id,
                        omnipkg_instance=self.core,
                        target_context_version=python_context_version,
                    )
                    gatherer.cache_client = self.cache_client
                    success = verify_bubble_with_smart_strategy(
                        self.core, pkg_name, new_ver, bubble_path, gatherer,
                    )
                except Exception:
                    success = False
                if not success:
                    # Verification failed — move back to main, don't stash-restore for this pkg
                    for suffix in [pkg_name, f'{pkg_name}-{new_ver}.dist-info']:
                        src = bubble_path / suffix
                        dst = sp / suffix
                        if src.exists():
                            os.rename(src, dst)
                    shutil.rmtree(bubble_path, ignore_errors=True)
                    result['failed'].append(pkg_name)
                    continue

                # Write bubble manifest so completeness checks pass
                try:
                    installed_tree = self._analyze_installed_tree(bubble_path)
                    stats = {
                        "total_files": sum(len(info.get("files", [])) for info in installed_tree.values()),
                        "copied_files": sum(len(info.get("files", [])) for info in installed_tree.values()),
                        "deduplicated_files": 0,
                        "c_extensions": [], "binaries": [], "python_files": 0,
                    }
                    self._create_bubble_manifest(
                        bubble_path, installed_tree, stats,
                        python_context_version, None
                    )
                except Exception as e:
                    safe_print(f"    ⚠️ Warning: Failed to create manifest in stash path: {e}")

                # Restore stash (old version) back to main
                for stash_path, original_path in stash_pairs:
                    if stash_path.exists():
                        if original_path.exists():
                            shutil.rmtree(original_path) if original_path.is_dir() else original_path.unlink()
                        os.rename(stash_path, original_path)

                result['bubbled'][pkg_name] = (new_ver, old_ver)

        except Exception:
            # Always restore stash on error
            for pkg_name, (old_ver, stash_pairs) in stashed.items():
                if pkg_name in result.get('bubbled', {}):
                    continue  # already completed cleanly
                for stash_path, original_path in stash_pairs:
                    if stash_path.exists() and not original_path.exists():
                        os.rename(stash_path, original_path)
            raise
        finally:
            # Safety net: any remaining stashes that weren't handled → restore
            for pkg_name, (old_ver, stash_pairs) in stashed.items():
                for stash_path, original_path in stash_pairs:
                    if stash_path.exists() and not original_path.exists():
                        os.rename(stash_path, original_path)

    def _collect_resolved_deps(self, pkg_name: str, packages_after: dict) -> dict:
        """
        BFS through pkg's Requires-Dist using all dists in site-packages as the
        available set.  Returns ``{canonical_name: version}`` for the full
        transitive closure including self.

        Uses PathDistribution — no subprocess, no IO beyond what's already open.
        Called immediately after a successful stash install so that resolved_deps
        is available before the async KB scan runs.
        """
        from importlib.metadata import PathDistribution, distributions
        from packaging.requirements import Requirement

        sp = str(self.site_packages)
        available: dict[str, PathDistribution] = {}
        for d in distributions(path=[sp]):
            n = d.metadata.get("Name", "")
            if n:
                available[canonicalize_name(n)] = d

        resolved: dict[str, str] = {}
        visited: set = set()
        root = available.get(canonicalize_name(pkg_name))
        if not root:
            return resolved
        queue = [root]
        while queue:
            current = queue.pop()
            c_name = canonicalize_name(current.metadata.get("Name", ""))
            if c_name in visited:
                continue
            visited.add(c_name)
            resolved[c_name] = current.version
            for req_str in (current.metadata.get_all("Requires-Dist") or []):
                try:
                    req = Requirement(req_str)
                    if req.marker and not req.marker.evaluate({"extra": ""}):
                        continue
                    dep_name = canonicalize_name(req.name)
                    if dep_name not in visited and dep_name in available:
                        queue.append(available[dep_name])
                except Exception:
                    continue
        return resolved

    def _create_pre_install_snapshot(self, *a, **kw):
        return self.core._create_pre_install_snapshot(*a, **kw)

    def _restore_from_pre_install_snapshot(self, *a, **kw):
        return self.core._restore_from_pre_install_snapshot(*a, **kw)

    def _safe_restore_from_snapshot(self, *a, **kw):
        return self.core._safe_restore_from_snapshot(*a, **kw)

    def _save_last_known_good_snapshot(self, *a, **kw):
        return self.core._save_last_known_good_snapshot(*a, **kw)

    def _synchronize_knowledge_base_with_reality(self, *a, **kw):
        return self.core._synchronize_knowledge_base_with_reality(*a, **kw)

    def _cleanup_redundant_bubbles(self, *a, **kw):
        return self.core._cleanup_redundant_bubbles(*a, **kw)

    def _update_hash_index_for_delta(self, *a, **kw):
        return self.core._update_hash_index_for_delta(*a, **kw)

    def _get_active_version_from_environment(self, *a, **kw):
        return self.core._get_active_version_from_environment(*a, **kw)

    def _find_compatible_python_version(self, *a, **kw):
        return self.core._find_compatible_python_version(*a, **kw)

    def _connect_cache(self):
        return self.core._connect_cache()

    def doctor(self, *a, **kw):
        return self.core.doctor(*a, **kw)

    def _heal_conda_environment(self):
        return self.core._heal_conda_environment()

    def _handle_quantum_healing(self, *a, **kw):
        return self.core._handle_quantum_healing(*a, **kw)

    def _schedule_background_kb_scan(self, bubble_paths: Dict[str, Path], python_version: str):
        import subprocess
        bubble_list = [f"{name}:{path}" for name, path in bubble_paths.items()]
        scan_cmd = [
            sys.executable, "-m", "omnipkg.background_scanner",
            "--bubbles", ",".join(bubble_list),
            "--python-version", python_version,
            "--env-id", self.env_id,
        ]
        subprocess.Popen(
            scan_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        safe_print(f"    💫 Background scan started for {len(bubble_paths)} bubble(s) (non-blocking)...")

    # -----------------------------------------------------------------------
    # uv-ffi plan callback — registered once per install() call
    # Intercepts bubble swaps atomically via os.rename (sub-ms).
    # Returns True  → uv aborts, Python already did the work.
    # Returns False → fall through to normal uv install.
    # -----------------------------------------------------------------------

    def _register_plan_callback(self, sp: Path) -> None:
        try:
            from omnipkg._vendor.uv_ffi import set_plan_callback, patch_site_packages_cache
            from omnipkg.installation.installers import _set_callback_result
        except ImportError:
            return

        versions = self.multiversion_base

        def _plan_callback(plan):
            if any(a == 'remote' for unused, unused, a in plan):
                return False  # needs network, let uv handle it

            coming_in = [(n, v) for n, v, a in plan if a == 'cached']
            going_out = [(n, v) for n, v, a in plan if a == 'reinstall']

            if not coming_in:
                return False  # nothing to pull from bubble

            # Only intercept pure single-package version swaps.
            # If uv wants to move multiple packages (e.g. rich + pygments), the
            # dep versions may not match what's in the existing bubble — fall back
            # to let uv resolve and install_and_verify rebuild the bubble correctly.
            coming_in_names = {n for n, v in coming_in}
            going_out_names  = {n for n, v in going_out}
            if coming_in_names != going_out_names or len(coming_in_names) != 1:
                return False

            # The bubble we're pulling from must exist and be verified (has manifest)
            for name, ver in coming_in:
                bubble = versions / f'{name}-{ver}'
                if not bubble.exists():
                    return False
                if not (bubble / '.omnipkg_manifest.json').exists():
                    return False  # unverified bubble — let install_and_verify handle it

            t = time.perf_counter()

            # Pass 1: main env → bubble (evict)
            for name, ver, action in plan:
                if action == 'reinstall':
                    bubble = versions / f'{name}-{ver}'
                    bubble.mkdir(parents=True, exist_ok=True)
                    for suffix in [name, f'{name}-{ver}.dist-info']:
                        src = sp / suffix
                        dst = bubble / suffix
                        if src.exists():
                            if dst.exists():
                                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                            os.rename(src, dst)

            # Pass 2: bubble → main env (install)
            src_bubble = None
            for name, ver, action in plan:
                if action == 'cached':
                    src_bubble = versions / f'{name}-{ver}'
                    for suffix in [name, f'{name}-{ver}.dist-info']:
                        src = src_bubble / suffix
                        dst = sp / suffix
                        if src.exists():
                            if dst.exists():
                                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                            os.rename(src, dst)

            # Pass 3: migrate shared deps src_bubble → dst_bubble
            if src_bubble and src_bubble.exists():
                skip = {n for n, v in coming_in}
                skip |= {f'{n}-{v}.dist-info' for n, v in coming_in}
                for name, ver, action in plan:
                    if action != 'reinstall':
                        continue
                    dst_bubble = versions / f'{name}-{ver}'
                    for item in src_bubble.iterdir():
                        if item.name in skip:
                            continue
                        dst_item = dst_bubble / item.name
                        if not dst_item.exists():
                            os.rename(item, dst_item)

            patch_site_packages_cache(
                [[n, v] for n, v, a in plan if a == 'cached'],
                [[n, v] for n, v, a in plan if a == 'reinstall'],
            )

            # Tell the outer _run_pip_install wrapper what actually changed so
            # its start_op/end_op reports correct lists to fs_watcher.
            # (uv itself returns installed=[], removed=[] when callback intercepts.)
            _set_callback_result(
                installed=[[n, v] for n, v in coming_in],
                removed=[[n, v] for n, v in going_out],
            )

            if _DEBUG_TIMING():
                print(f"[SWAP] plan callback: {(time.perf_counter()-t)*1000:.2f}ms", flush=True)
            return True

        set_plan_callback(_plan_callback)

    # -----------------------------------------------------------------------
    # Bubble creation — used by install() and by bubble_manager shim
    # -----------------------------------------------------------------------

    def create_isolated_bubble(
        self,
        package_name: str,
        target_version: str,
        python_context_version: str,
        index_url: Optional[str] = None,
        extra_index_url: Optional[str] = None,
        observed_dependencies: Optional[Dict[str, str]] = None,
    ) -> bool:
        safe_print(
            _("🫧 Creating isolated bubble for {} v{} (Python {} context)").format(
                package_name, target_version, python_context_version
            )
        )
        bubble_path = self.multiversion_base / f"{package_name}-{target_version}"
        return self.install_and_verify(
            package_name,
            target_version,
            python_context_version,
            destination_path=bubble_path,
            index_url=index_url,
            extra_index_url=extra_index_url,
            observed_dependencies=observed_dependencies,
        )

    def install_and_verify(
        self,
        package_name: str,
        version: str,
        python_context_version: str,
        destination_path: Path,
        index_url: Optional[str] = None,
        extra_index_url: Optional[str] = None,
        python_exe_override: Optional[str] = None,
        observed_dependencies: Optional[Dict[str, str]] = None,
    ) -> bool:
        _dbg = os.environ.get("OMNIPKG_DEBUG") == "1"

        def _tp(label, t):
            if _dbg:
                print(f"[TIMING] {label}: {(time.perf_counter()-t)*1000:.2f}ms", flush=True)

        _t0 = time.perf_counter()

        # Evict stale bubble cache entry before nuking the dir
        if destination_path.exists():
            try:
                from omnipkg._vendor.uv_ffi import evict_bubble_cache
                evict_bubble_cache()
            except Exception:
                pass
            shutil.rmtree(destination_path, ignore_errors=True)

        destination_path.mkdir(parents=True, exist_ok=True)
        safe_print(f"   - 🏗️  Installing bubble for {package_name}=={version}...")

        return_code, install_output = self._run_pip_install(
            [f"{package_name}=={version}"],
            target_directory=destination_path,
            force_reinstall=False,
            index_url=index_url,
            extra_index_url=extra_index_url,
        )
        _tp("iav: pip_install", _t0)

        verification_passed = False
        if return_code == 0:
            _tv = time.perf_counter()
            safe_print("   - 🧪 Running SMART import verification...")
            try:
                from omnipkg.installation.verification_strategy import verify_bubble_with_smart_strategy
                from omnipkg.package_meta_builder import omnipkgMetadataGatherer
            except ImportError:
                from omnipkg.installation.verification_strategy import verify_bubble_with_smart_strategy  # noqa
                from omnipkg.package_meta_builder import omnipkgMetadataGatherer  # noqa

            gatherer = omnipkgMetadataGatherer(
                config=self.config,
                env_id=self.env_id,
                omnipkg_instance=self.core,
                target_context_version=python_context_version,
            )
            gatherer.cache_client = self.cache_client

            existing_bubble_paths = []
            try:
                from omnipkg.installation.verification_groups import find_verification_group
            except ImportError:
                from omnipkg.installation.verification_groups import find_verification_group  # noqa

            canonical_name = package_name.lower().replace("_", "-")
            group_def = find_verification_group(canonical_name)
            if group_def:
                existing_bubble_paths = self._find_dependency_bubbles(package_name, destination_path.parent)

            verification_passed = verify_bubble_with_smart_strategy(
                self.core, package_name, version, destination_path, gatherer,
                existing_bubble_paths=existing_bubble_paths,
            )
            _tp("iav: verify_bubble", _tv)

        # Time Machine fallback
        if not verification_passed:
            safe_print("\n" + "=" * 60)
            if return_code != 0:
                safe_print(f"🕰️  TIME MACHINE: Modern install failed for {package_name}=={version}.")
            else:
                safe_print(f"🕰️  TIME MACHINE: Verification failed for {package_name}=={version}.")
            safe_print("   - Attempting to rebuild from the past using historical dependencies...")
            safe_print("=" * 60)

            shutil.rmtree(destination_path)
            destination_path.mkdir(exist_ok=True)
            _ttm = time.perf_counter()
            historical_success = self.core._run_historical_install_fallback(
                package_name, version,
                target_directory_override=destination_path,
                index_url=index_url,
                extra_index_url=extra_index_url,
            )
            if not historical_success:
                safe_print(f"   ❌ TIME MACHINE: Historical rebuild failed for {package_name}=={version}.")
                return False

            safe_print(_('\n   ✅ TIME MACHINE: Successfully rebuilt {}=={} into staging area.').format(package_name, version))
            _tp("iav: time_machine_install", _ttm)

            safe_print("   - 🧪 Running SMART import verification...")
            try:
                from omnipkg.installation.verification_strategy import verify_bubble_with_smart_strategy
                from omnipkg.package_meta_builder import omnipkgMetadataGatherer
            except ImportError:
                pass

            _tv2 = time.perf_counter()
            gatherer = omnipkgMetadataGatherer(
                config=self.config,
                env_id=self.env_id,
                omnipkg_instance=self.core,
                target_context_version=python_context_version,
            )
            existing_bubble_paths = []
            if group_def:
                existing_bubble_paths = self._find_dependency_bubbles(package_name, destination_path.parent)

            verification_passed = verify_bubble_with_smart_strategy(
                self.core, package_name, version, destination_path, gatherer,
                existing_bubble_paths=existing_bubble_paths,
            )
            _tp("iav: verify_bubble_post_tm", _tv2)

            if not verification_passed:
                safe_print(f"   ❌ CRITICAL: Smart verification failed for '{package_name}' even after TIME MACHINE.")
                return False

        # Finalize
        if verification_passed:
            safe_print(_('   - ✅ Bubble created successfully at {}').format(destination_path))

            # Patch bubble cache so uv knows what's there — avoids cold rescan next time
            try:
                from omnipkg._vendor.uv_ffi import patch_bubble_site_packages_cache
                patch_bubble_site_packages_cache([[package_name, version]], [])
            except Exception:
                pass

            enriched_meta = None
            try:
                from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                from importlib.metadata import PathDistribution
                gatherer = omnipkgMetadataGatherer(
                    config=self.config,
                    env_id=self.env_id,
                    omnipkg_instance=self.core,
                    target_context_version=python_context_version,
                )
                canonical_pkg = canonicalize_name(package_name)
                for dist_info in destination_path.glob("*.dist-info"):
                    dist = PathDistribution(dist_info)
                    if canonicalize_name(dist.metadata.get("Name", "")) == canonical_pkg:
                        enriched_meta = gatherer._build_comprehensive_metadata(dist)
                        break
            except Exception as e:
                safe_print(f"    ⚠️ Warning: Failed to extract Global Cache metadata: {e}")

            safe_print(_('   - 📝 Creating bubble manifest...'))
            _ta = time.perf_counter()
            installed_tree = self._analyze_installed_tree(destination_path)
            stats = {
                "total_files": sum(len(info.get("files", [])) for info in installed_tree.values()),
                "copied_files": sum(len(info.get("files", [])) for info in installed_tree.values()),
                "deduplicated_files": 0,
                "c_extensions": [],
                "binaries": [],
                "python_files": 0,
            }
            _tp("iav: analyze_installed_tree", _ta)
            _tc = time.perf_counter()
            self._create_bubble_manifest(
                destination_path, installed_tree, stats,
                python_context_version, observed_dependencies,
                enriched_meta=enriched_meta,
            )
            _tp("iav: create_bubble_manifest", _tc)
            return True
        else:
            safe_print(_('   - ❌ Verification failed, bubble not created'))
            if destination_path.exists():
                shutil.rmtree(destination_path, ignore_errors=True)
            return False

    def _find_dependency_bubbles(self, package_name: str, bubble_base: Path) -> list:
        return self.bubble_manager._find_dependency_bubbles(package_name, bubble_base)

    def _analyze_installed_tree(self, destination_path: Path) -> dict:
        return self.bubble_manager._analyze_installed_tree(destination_path)

    def _create_bubble_manifest(self, *a, **kw):
        return self.bubble_manager._create_bubble_manifest(*a, **kw)

    # -----------------------------------------------------------------------
    # Main entry point
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
        extra_flags: Optional[List[str]] = None,
    ) -> int:
        from omnipkg.core import ConfigManager, NoCompatiblePythonError

        resolved_package_cache: Dict[str, str] = {}

        # Register plan callback so bubble swaps are intercepted atomically
        sp = Path(self.config.get("site_packages_path", ""))
        if sp.exists():
            self._register_plan_callback(sp)

        # ====================================================================
        # ULTRA-FAST PREFLIGHT CHECK
        # ====================================================================
        if not force_reinstall and not target_directory and packages:
            safe_print("⚡ Running ultra-fast preflight check...")
            preflight_start = time.perf_counter()
            configured_exe = self.config.get("python_executable", sys.executable)
            install_strategy = self.config.get("install_strategy", "stable-main")
            safe_print(f"   🐍 preflight python: {configured_exe}")

            fully_resolved_specs = []
            needs_installation = []
            complex_spec_chars = ["<", ">", "~", "!", ","]

            for pkg_spec in packages:
                pkg_name, version = self._parse_package_spec(pkg_spec)
                is_complex_spec = any(op in pkg_spec for op in complex_spec_chars)

                if version and not is_complex_spec:
                    resolved_spec = pkg_spec
                else:
                    if is_complex_spec:
                        safe_print(_("   🔍 Detected complex specifier: '{}'").format(pkg_spec))
                        try:
                            resolved_spec_str = self._find_best_version_for_spec(pkg_spec)
                            if resolved_spec_str:
                                resolved_spec = resolved_spec_str
                                pkg_name, version = self._parse_package_spec(resolved_spec)
                                safe_print(_("   ✅ Resolved '{}' to '{}'").format(pkg_spec, resolved_spec))
                            else:
                                needs_installation.append(pkg_spec)
                                continue
                        except NoCompatiblePythonError as e:
                            return self._handle_quantum_healing(
                                e, packages, dry_run, force_reinstall, override_strategy, target_directory,
                            )
                    else:
                        try:
                            latest_version = self._get_latest_version_from_pypi(self._bare_name(pkg_name))
                        except NoCompatiblePythonError as e:
                            return self._handle_quantum_healing(
                                e, packages, dry_run, force_reinstall, override_strategy, target_directory,
                            )
                        if latest_version:
                            resolved_spec = f"{pkg_name}=={latest_version}"
                            version = latest_version
                        else:
                            needs_installation.append(pkg_spec)
                            continue

                is_installed, duration_ns = self.check_package_installed_fast(
                    configured_exe, self._bare_name(pkg_name), version
                )

                if duration_ns < 1_000:
                    duration_str = f"{duration_ns}ns"
                elif duration_ns < 1_000_000:
                    duration_str = f"{duration_ns / 1_000:.1f}µs"
                else:
                    duration_str = f"{duration_ns / 1_000_000:.3f}ms"

                if is_installed:
                    bubble_path = self.multiversion_base / f"{pkg_name}-{version}"
                    bubble_path_alt = self.multiversion_base / f"{pkg_name.replace('-', '_')}-{version}"
                    is_in_bubble = (bubble_path.exists() and bubble_path.is_dir()) or \
                                   (bubble_path_alt.exists() and bubble_path_alt.is_dir())

                    if not is_in_bubble:
                        safe_print(_('   ✓ {} [satisfied: {} - active in main env]').format(resolved_spec, duration_str))
                        fully_resolved_specs.append(resolved_spec)
                        continue
                    if install_strategy == "stable-main":
                        safe_print(_('   ✓ {} [satisfied: {} - bubble @ {}]').format(resolved_spec, duration_str, bubble_path))
                        fully_resolved_specs.append(resolved_spec)
                        continue
                    needs_installation.append(resolved_spec)
                    continue
                else:
                    needs_installation.append(resolved_spec)

            needs_installation = [s for s in needs_installation if s not in fully_resolved_specs]
            if not needs_installation:
                total_check_time_ns = int((time.perf_counter() - preflight_start) * 1_000_000_000)
                total_time_str = (
                    f"{total_check_time_ns / 1_000:.1f}µs"
                    if total_check_time_ns < 1_000_000
                    else f"{total_check_time_ns / 1_000_000:.3f}ms"
                )
                safe_print(_('⚡ PREFLIGHT SUCCESS: All {} package(s) already satisfied! ({})').format(len(packages), total_time_str))
                return 0

            if needs_installation:
                safe_print(_('\n📦 {} package(s) need installation/validation').format(len(needs_installation)))
                validated_specs = []
                try:
                    for spec in needs_installation:
                        pkg_name, version = self._parse_package_spec(spec)
                        if not version:
                            safe_print(f"   🔍 Resolving version for '{pkg_name}' with pip...")
                        else:
                            safe_print(f"   ⚙️  Validating '{spec}' with pip...")
                        resolved_spec, pip_output = self._resolve_spec_with_pip(
                            spec, index_url=index_url, extra_index_url=extra_index_url
                        )
                        if resolved_spec:
                            safe_print(f"   ✓ Pip validated '{spec}' -> '{resolved_spec}'")
                            validated_specs.append(resolved_spec)
                        else:
                            safe_print(f"\n❌ Could not find the specified version for '{pkg_name}'.")
                            return 1
                except NoCompatiblePythonError as e:
                    return self._handle_quantum_healing(
                        e, packages, dry_run, force_reinstall, override_strategy,
                        target_directory, index_url, extra_index_url,
                    )
                packages = validated_specs

        elif force_reinstall and packages:
            safe_print("⚡ Running preflight check with --force flag...")
            preflight_start = time.perf_counter()
            configured_exe = self.config.get("python_executable", sys.executable)
            packages_found = []
            packages_not_found = []

            for pkg_spec in packages:
                pkg_name, version = self._parse_package_spec(pkg_spec)
                if not version:
                    packages_not_found.append(pkg_spec)
                    continue
                is_installed, duration_ns = self.check_package_installed_fast(configured_exe, pkg_name, version)
                duration_str = (
                    f"{duration_ns}ns" if duration_ns < 1_000
                    else f"{duration_ns / 1_000:.1f}µs" if duration_ns < 1_000_000
                    else f"{duration_ns / 1_000_000:.3f}ms"
                )
                if is_installed:
                    packages_found.append((pkg_spec, duration_str, is_installed))
                    safe_print(_('   🔧 {} [found: {} - {}] → will force reinstall').format(pkg_spec, duration_str, is_installed))
                else:
                    packages_not_found.append(pkg_spec)
                    safe_print(_('   ⚠️  {} [not found: {}] → will install fresh').format(pkg_spec, duration_str))

            total_check_time_ns = int((time.perf_counter() - preflight_start) * 1_000_000_000)
            total_time_str = (
                f"{total_check_time_ns / 1_000:.1f}µs" if total_check_time_ns < 1_000_000
                else f"{total_check_time_ns / 1_000_000:.3f}ms"
            )
            if packages_found:
                safe_print(f"\n🔨 FORCE REINSTALL: Triggering repair for {len(packages_found)} existing package(s) ({total_time_str})")
            if packages_not_found:
                safe_print(f"📦 Fresh install needed for {len(packages_not_found)} package(s)")

        # ====================================================================
        # NORMAL INITIALIZATION
        # ====================================================================
        original_strategy = None
        if override_strategy:
            original_strategy = self.config.get("install_strategy", "stable-main")
            if original_strategy != override_strategy:
                safe_print(_('   - 🔄 Using override strategy: {}').format(override_strategy))
                self.config["install_strategy"] = override_strategy
        install_strategy = self.config.get("install_strategy", "stable-main")

        if not self._connect_cache():
            return 1
        if dry_run:
            safe_print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0
        if not packages:
            safe_print("🚫 No packages specified for installation.")
            return 1

        self.doctor(dry_run=False, force=True)
        self._heal_conda_environment()

        # --- UNIFIED SMART PREFLIGHT CHECK (second pass with full strategy awareness) ---
        if not force_reinstall and not target_directory:
            safe_print("⚡ Running preflight satisfaction check...")
            preflight_start = time.perf_counter()
            configured_exe = self.config.get("python_executable", sys.executable)
            install_strategy = self.config.get("install_strategy", "stable-main")

            resolved_package_cache = {}
            main_env_kb_updates: Dict[str, str] = {}
            bubbled_kb_updates: Dict[str, str] = {}
            any_installations_made = False
            all_packages_satisfied = True
            processed_packages: List[str] = []
            needs_resolution: List[str] = []
            needs_kb_check: List[str] = []

            for pkg_spec in packages:
                if "==" in pkg_spec:
                    pkg_name, version = self._parse_package_spec(pkg_spec)
                    resolved_package_cache[pkg_spec] = pkg_spec
                    install_status, check_time = self.check_package_installed_fast(configured_exe, pkg_name, version)

                    if install_status == "active":
                        safe_print(_('✅ {} already satisfied (active in main env)').format(pkg_spec))
                        processed_packages.append(pkg_spec)
                        continue
                    if install_status == "bubble":
                        if install_strategy == "stable-main":
                            safe_print(f"✅ {pkg_spec} already satisfied (found as bubble)")
                            processed_packages.append(pkg_spec)
                            continue
                        all_packages_satisfied = False
                        break
                    elif install_status is None:
                        needs_kb_check.append(pkg_spec)
                else:
                    needs_resolution.append(pkg_spec)

            if needs_resolution and all_packages_satisfied:
                try:
                    for pkg_spec in needs_resolution:
                        safe_print(f"  🔍 Resolving version for {pkg_spec}...")
                        try:
                            resolved = self._resolve_package_versions([pkg_spec])
                            if not resolved:
                                all_packages_satisfied = False
                                break
                        except ValueError as e:
                            safe_print(_("❌ Failed to resolve '{}': {}").format(pkg_spec, e))
                            all_packages_satisfied = False
                            break
                        resolved_spec = resolved[0]
                        resolved_package_cache[pkg_spec] = resolved_spec
                        pkg_name, version = self._parse_package_spec(resolved_spec)
                        install_status, _out = self.check_package_installed_fast(configured_exe, pkg_name, version)
                        if install_status == "active":
                            safe_print(_('✅ {} already satisfied (active in main env)').format(resolved_spec))
                            processed_packages.append(resolved_spec)
                        elif install_status == "bubble" and install_strategy == "stable-main":
                            safe_print(f"✅ {resolved_spec} already satisfied (found as bubble)")
                            processed_packages.append(resolved_spec)
                        elif install_status == "bubble" and install_strategy != "stable-main":
                            all_packages_satisfied = False
                            break
                        else:
                            needs_kb_check.append(resolved_spec)
                    if all_packages_satisfied and not needs_kb_check:
                        preflight_time = (time.perf_counter() - preflight_start) * 1000
                        safe_print(f"✅ PREFLIGHT SUCCESS: All {len(processed_packages)} package(s) already satisfied! ({preflight_time:.1f}ms)")
                        return 0
                except NoCompatiblePythonError as e:
                    new_config_manager = ConfigManager()
                    new_instance = self.__class__(new_config_manager)
                    return new_instance.install(packages, dry_run, force_reinstall, target_directory=target_directory)

            if needs_kb_check and all_packages_satisfied:
                safe_print(_('🔍 Checking {} package(s) requiring deeper verification...').format(len(needs_kb_check)))
                self._synchronize_knowledge_base_with_reality(verbose=False)
                kb_satisfied = True
                for pkg_spec in needs_kb_check:
                    nested_found = False
                    if not nested_found:
                        kb_satisfied = False
                        break
                    safe_print(_('✅ {} already satisfied (nested)').format(pkg_spec))
                    processed_packages.append(pkg_spec)
                all_packages_satisfied = kb_satisfied

            if not all_packages_satisfied:
                preflight_time = (time.perf_counter() - preflight_start) * 1000
                safe_print(f"📦 Preflight detected packages need installation ({preflight_time:.1f}ms)")

        # --- MAIN INSTALLATION LOGIC ---
        protected_from_cleanup: Set[str] = set()
        configured_exe = self.config.get("python_executable", sys.executable)
        version_tuple = self.config_manager._verify_python_version(configured_exe)
        python_context_version = (
            f"{version_tuple[0]}.{version_tuple[1]}" if version_tuple else "unknown"
        )

        if python_context_version == "unknown":
            safe_print("⚠️ CRITICAL: Could not determine Python context.")

        install_strategy = self.config.get("install_strategy", "stable-main")
        packages_to_process = list(packages)
        main_env_kb_updates: Dict[str, str] = {}
        bubbled_kb_updates: Dict[str, str] = {}
        any_installations_made = False

        # Handle omnipkg special case
        for pkg_spec in list(packages_to_process):
            pkg_name, requested_version = self._parse_package_spec(pkg_spec)
            if pkg_name.lower() != "omnipkg":
                continue
            packages_to_process.remove(pkg_spec)
            safe_print("✨ Special handling: omnipkg '{}' requested.".format(pkg_spec))

            if not requested_version:
                resolved_spec = resolved_package_cache.get(pkg_spec)
                if not resolved_spec:
                    safe_print(f"  ❌ CRITICAL: Could not find pre-resolved version for '{pkg_spec}'. Skipping.")
                    continue
                pkg_name, requested_version = self._parse_package_spec(resolved_spec)
                safe_print(_('  -> Using pre-flight resolved version: {}').format(resolved_spec))

            active_omnipkg_version = self._get_active_version_from_environment("omnipkg")
            if (not force_reinstall and active_omnipkg_version
                    and parse_version(requested_version) == parse_version(active_omnipkg_version)):
                safe_print("✅ omnipkg=={} is already the active version.".format(requested_version))
                continue

            is_upgrade = active_omnipkg_version and parse_version(requested_version) > parse_version(active_omnipkg_version)
            is_downgrade = active_omnipkg_version and parse_version(requested_version) < parse_version(active_omnipkg_version)

            if is_upgrade or is_downgrade:
                action = "Upgrading" if is_upgrade else "Downgrading"
                safe_print(f"🔄 {action} omnipkg from v{active_omnipkg_version} to v{requested_version}...")
                if active_omnipkg_version:
                    bubble_path = self.multiversion_base / f"omnipkg-{active_omnipkg_version}"
                    if not bubble_path.exists():
                        bubble_created = self.create_isolated_bubble(
                            "omnipkg", active_omnipkg_version, python_context_version=python_context_version,
                        )
                        safe_print(_('✅ Bubbled omnipkg v{}').format(active_omnipkg_version) if bubble_created
                                   else _('⚠️  Failed to bubble current version v{}').format(active_omnipkg_version))

                self._installed_packages_cache = None
                return_code, _out = self._run_pip_install(
                    [f"omnipkg=={requested_version}"],
                    target_directory=None,
                    force_reinstall=force_reinstall,
                )
                if return_code != 0:
                    safe_print(_('❌ Failed to install omnipkg=={}.').format(requested_version))
                    continue
                packages_after = self.get_installed_packages(live=True)
                any_installations_made = True
                final_main_state = packages_after.copy()
                main_env_kb_updates["omnipkg"] = requested_version
                safe_print(_('✅ omnipkg successfully {}d to v{}!').format(action.lower(), requested_version))
            else:
                bubble_path = self.multiversion_base / f"omnipkg-{requested_version}"
                if bubble_path.exists() and not force_reinstall:
                    safe_print(f"✅ Bubble for omnipkg=={requested_version} already exists.")
                    continue
                bubble_created = self.create_isolated_bubble(
                    "omnipkg", requested_version, python_context_version=python_context_version,
                )
                if bubble_created:
                    safe_print("✅ omnipkg=={} successfully bubbled and registered.".format(requested_version))
                    self._synchronize_knowledge_base_with_reality()
                else:
                    safe_print(f"❌ Failed to create bubble for omnipkg=={requested_version}.")

        if not packages_to_process:
            safe_print(_("\n🎉 All package operations complete."))
            return 0

        safe_print("🚀 Starting install with policy: '{}'".format(install_strategy))

        try:
            if not force_reinstall and resolved_package_cache:
                resolved_packages = []
                for orig_pkg in packages_to_process:
                    if orig_pkg in resolved_package_cache:
                        resolved_packages.append(resolved_package_cache[orig_pkg])
                    else:
                        resolved = self._resolve_package_versions([orig_pkg])
                        if resolved:
                            resolved_packages.extend(resolved)
            else:
                resolved_packages = self._resolve_package_versions(packages_to_process)

            if not resolved_packages:
                safe_print(_("❌ Could not resolve any packages to install. Aborting."))
                return 1

            sorted_packages = self._sort_packages_for_install(resolved_packages, strategy=install_strategy)

        except ValueError as e:
            safe_print(_('\n❌ Resolution failed: {}').format(e))
            return 1
        except NoCompatiblePythonError as e:
            safe_print("\n" + "=" * 60)
            safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected")
            safe_print("=" * 60)
            safe_print(_("   - Diagnosis: Cannot install '{}' on your current Python ({}).").format(e.package_name, e.current_python))
            safe_print(_('   - Prescription: This package requires Python {}.').format(e.compatible_python))
            from omnipkg.cli import handle_python_requirement
            if not e.compatible_python or e.compatible_python == "unknown":
                safe_print(f"❌ Healing failed: Could not determine a compatible Python version for '{e.package_name}'.")
                return 1
            if not handle_python_requirement(e.compatible_python, self.core, "omnipkg"):
                safe_print(_('❌ Healing failed: Could not automatically switch to Python {}.').format(e.compatible_python))
                return 1
            safe_print(_('\n🚀 Retrying original `install` command in the new Python {} context...').format(e.compatible_python))
            new_config_manager = ConfigManager()
            new_omnipkg_instance = self.core.__class__(new_config_manager)
            return new_omnipkg_instance.smart_install(
                packages, dry_run=dry_run, force_reinstall=force_reinstall,
                target_directory=target_directory, index_url=index_url,
                extra_index_url=extra_index_url, extra_flags=extra_flags,
            )

        user_requested_cnames = {canonicalize_name(self._parse_package_spec(p)[0]) for p in packages}
        initial_packages_before = self.get_installed_packages(live=True)
        final_main_state: Dict[str, str] = {}
        packages_before = initial_packages_before.copy()

        for package_spec in sorted_packages:
            try:
                safe_print("\n" + "─" * 60)
                pkg_name, pkg_version = self._parse_package_spec(package_spec)
                snapshot_key = self._create_pre_install_snapshot(pkg_name)

                if force_reinstall:
                    is_installed, _chk_time = self.check_package_installed_fast(
                        self.config.get("python_executable", sys.executable), pkg_name, pkg_version,
                    )
                    if is_installed:
                        safe_print(_('🔨 Force Reinstalling: {} (existing {})').format(package_spec, is_installed))
                    else:
                        safe_print(_('📦 Processing: {}').format(package_spec))
                else:
                    safe_print(_('📦 Processing: {}').format(package_spec))
                    safe_print("─" * 60)
                    safe_print("   📸 Pre-install snapshot created")
                    satisfaction_check = self._check_package_satisfaction([package_spec], strategy=install_strategy)
                    if satisfaction_check["all_satisfied"] and not target_directory:
                        safe_print("✅ Requirement already satisfied: {}".format(package_spec))
                        continue

                packages_before = self.get_installed_packages(live=True)
                safe_print("⚙️ Running pip install for: {}...".format(package_spec))

                # stable-main fast path: stash current pkg so uv skips uninstalling it,
                # then move uv's new install → bubble and restore stash after.
                _stash_result = None
                _stash_pkg_name = self._parse_package_spec(package_spec)[0] if install_strategy == "stable-main" else None
                _stash_before = {_stash_pkg_name: packages_before[_stash_pkg_name]} \
                    if _stash_pkg_name and _stash_pkg_name in packages_before else {}

                if install_strategy == "stable-main" and _stash_before and not target_directory:
                    _stash_ctx = self._stash_for_stable_main(
                        _stash_before, python_context_version,
                        index_url=index_url, extra_index_url=extra_index_url,
                    )
                    _stash_result = _stash_ctx.__enter__()
                    try:
                        return_code, pkg_install_output = self._run_pip_install(
                            [package_spec],
                            target_directory=target_directory,
                            force_reinstall=force_reinstall,
                            index_url=index_url,
                            extra_index_url=extra_index_url,
                            extra_flags=extra_flags,
                        )
                        # Tell stash context what uv actually installed
                        _cb_inst, _cb_rem = _pop_callback_result() or (None, None)
                        if return_code == 0:
                            pkg_name_parsed, ver_parsed = self._parse_package_spec(package_spec)
                            _stash_result['new_versions'] = {pkg_name_parsed: ver_parsed}
                            # Compute transitive closure now, before the async KB scan fires.
                            # _stash_for_stable_main Phase 2 reads this to copy dep dirs into bubble.
                            try:
                                _stash_result['resolved_deps'] = self._collect_resolved_deps(
                                    pkg_name_parsed, self.get_installed_packages(live=True)
                                )
                            except Exception:
                                _stash_result['resolved_deps'] = {}
                        _stash_ctx.__exit__(None, None, None)
                    except Exception as _e:
                        _stash_ctx.__exit__(type(_e), _e, _e.__traceback__)
                        raise
                else:
                    return_code, pkg_install_output = self._run_pip_install(
                        [package_spec],
                        target_directory=target_directory,
                        force_reinstall=force_reinstall,
                        index_url=index_url,
                        extra_index_url=extra_index_url,
                        extra_flags=extra_flags,
                    )

                if return_code != 0:
                    safe_print(f"❌ Pip installation failed for {package_spec}.")
                    safe_print("\n🔄 Restoring environment from pre-install snapshot...")
                    if self._restore_from_pre_install_snapshot(snapshot_key):
                        safe_print("   ✅ Environment restored to pre-install state")
                    else:
                        safe_print("   ❌ CRITICAL: Snapshot restore failed!")
                        safe_print("   💡 You may need to run: omnipkg revert")
                    continue

                any_installations_made = True
                self._installed_packages_cache = None
                packages_after = self.get_installed_packages(live=True)
                final_main_state = packages_after.copy()
                all_changes = self._detect_all_changes(packages_before, packages_after)

                if all_changes["downgrades"] or all_changes["upgrades"] or all_changes["removals"]:
                    safe_print(_('\n⚠️  Detected {} dependency changes:').format(
                        len(all_changes['downgrades'] + all_changes['upgrades'] + all_changes['removals'])
                    ))
                    for change in all_changes["downgrades"]:
                        safe_print(_('   ⬇️  {}: v{} → v{} (downgrade)').format(change['package'], change['old_version'], change['new_version']))
                    for change in all_changes["upgrades"]:
                        safe_print(_('   ⬆️  {}: v{} → v{} (upgrade)').format(change['package'], change['old_version'], change['new_version']))
                    for change in all_changes["removals"]:
                        safe_print(_('   🗑️  {}: v{} (removed)').format(change['package'], change['version']))

                if install_strategy == "stable-main" and _stash_result and _stash_result.get('used_stash'):
                    # Stash path handled the bubble+restore atomically — just wire up KB/hooks.
                    for bubbled_pkg, (new_ver, old_ver) in _stash_result.get('bubbled', {}).items():
                        bubble_path = self.multiversion_base / f'{bubbled_pkg}-{new_ver}'
                        bubbled_kb_updates[bubbled_pkg] = new_ver
                        main_env_kb_updates[bubbled_pkg] = old_ver
                        protected_from_cleanup.add(canonicalize_name(bubbled_pkg))
                        self.hook_manager.refresh_bubble_map(bubbled_pkg, new_ver, str(bubble_path))
                        
                        # Create manifest BEFORE validating
                        try:
                            installed_tree = self._analyze_installed_tree(bubble_path)
                            stats = {"total_files": 0, "copied_files": 0, "deduplicated_files": 0, "c_extensions": [], "binaries": [], "python_files": 0}
                            self._create_bubble_manifest(bubble_path, installed_tree, stats, python_context_version, None)
                        except Exception:
                            pass
                            
                        self.hook_manager.validate_bubble(bubbled_pkg, new_ver)
                        # Write resolved_deps eagerly so the loader has it before the
                        # async KB scan fires.  Format matches package_meta_builder output.
                        try:
                            resolved_deps = _stash_result.get('resolved_deps', {})
                            if resolved_deps and self.cache_client:
                                from omnipkg.kb_utils import build_inst_key  # noqa: F401
                                record_hash = ""
                                try:
                                    from importlib.metadata import PathDistribution
                                    dist_info = next(
                                        (bubble_path / f'{bubbled_pkg}-{new_ver}.dist-info',
                                         bubble_path / f'{bubbled_pkg.replace("-", "_")}-{new_ver}.dist-info'),
                                        None
                                    )
                                    if dist_info and dist_info.exists():
                                        from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                                        _tmp_g = omnipkgMetadataGatherer.__new__(omnipkgMetadataGatherer)
                                        _tmp_g.config = self.config
                                        _tmp_d = PathDistribution(dist_info)
                                        record_hash = _tmp_g._get_exact_record_hash(_tmp_d)
                                except Exception:
                                    pass
                                inst_prefix = self.core.redis_key_prefix.replace(':pkg:', ':inst:')
                                inst_key = f"{inst_prefix}{bubbled_pkg}:{new_ver}:{record_hash}"
                                self.cache_client.hset(inst_key, "resolved_deps", json.dumps(resolved_deps))
                        except Exception:
                            pass
                        safe_print(f"   ✅ Stash path: bubbled {bubbled_pkg} v{new_ver}, restored v{old_ver} to main")
                    for failed_pkg in _stash_result.get('failed', []):
                        safe_print(f"   ⚠️  Stash path: verification failed for {failed_pkg} — bubble not registered")

                elif install_strategy == "stable-main":
                    # Legacy path: stash wasn't applicable (no prior version in main, or
                    # target_directory set). Fall through to full create_isolated_bubble+restore.
                    packages_to_bubble = []
                    packages_to_restore = []

                    for change in all_changes["downgrades"] + all_changes["upgrades"]:
                        packages_to_bubble.append({
                            "package": change["package"],
                            "new_version": change["new_version"],
                            "old_version": change["old_version"],
                            "is_removal": False,
                        })
                    for change in all_changes["removals"]:
                        newly_installed = packages_after.get(change["package"]) or packages_after.get(change["package"].replace("_", "-"))
                        if not newly_installed:
                            continue
                        packages_to_bubble.append({
                            "package": change["package"],
                            "new_version": newly_installed,
                            "old_version": change["version"],
                            "is_removal": True,
                        })

                    if packages_to_bubble:
                        safe_print(_('\n🛡️ STABILITY PROTECTION: Processing {} changed package(s)').format(len(packages_to_bubble)))
                        bubble_tracker = {}

                        for item in packages_to_bubble:
                            safe_print(f"\n   🫧 Creating bubble for {item['package']} v{item['new_version']}...")
                            bubble_created = self.create_isolated_bubble(
                                item["package"], item["new_version"],
                                python_context_version=python_context_version,
                                index_url=index_url,
                                extra_index_url=extra_index_url,
                                observed_dependencies=packages_after,
                            )
                            if bubble_created:
                                bubble_path = self.multiversion_base / f"{item['package']}-{item['new_version']}"
                                bubble_tracker[item["package"]] = bubble_path
                                bubbled_kb_updates[item["package"]] = item["new_version"]
                                safe_print("   ✅ Bubble created successfully")
                                packages_to_restore.append(item)
                            else:
                                safe_print(f"   ❌ Bubble creation FAILED for {item['package']} v{item['new_version']}")
                                safe_print("   🚨 CRITICAL: Cannot guarantee stability without this bubble!")
                                safe_print("\n   🔄 Initiating safe restore from snapshot...")
                                snapshot_data = self.cache_client.get(snapshot_key)
                                if snapshot_data:
                                    snapshot_state = json.loads(snapshot_data)
                                    if self._safe_restore_from_snapshot(pkg_name, snapshot_state, force=True):
                                        safe_print("   ✅ Environment safely restored to pre-install state")
                                    else:
                                        safe_print("   ❌ Restore failed - environment may be unstable!")
                                else:
                                    safe_print("   ❌ Snapshot not available - cannot restore!")
                                break

                        if len(bubble_tracker) == len(packages_to_bubble):
                            safe_print("\n   ✅ All bubbles created successfully")
                            safe_print("   🔄 Restoring stable versions to main environment...")
                            restore_specs = [
                                f"{item['package']}=={item['old_version']}"
                                for item in packages_to_restore
                            ]
                            # _plan_callback intercepts this if bubble exists — atomic rename
                            restore_code, _out = self._run_pip_install(
                                restore_specs,
                                force_reinstall=True,
                                extra_flags=["--no-deps"],
                            )
                            if restore_code == 0:
                                safe_print("   ✅ All stable versions restored")
                                for item in packages_to_restore:
                                    main_env_kb_updates[item["package"]] = item["old_version"]
                                    protected_from_cleanup.add(canonicalize_name(item["package"]))
                            else:
                                safe_print("   ❌ Restore failed - using snapshot fallback")
                                snapshot_data = self.cache_client.get(snapshot_key)
                                if snapshot_data:
                                    snapshot_state = json.loads(snapshot_data)
                                    self._safe_restore_from_snapshot(pkg_name, snapshot_state, force=True)

                elif install_strategy == "latest-active":
                    versions_to_bubble = []
                    norm_before = {k.replace("-", "_"): v for k, v in packages_before.items()}
                    norm_after = {k.replace("-", "_"): v for k, v in packages_after.items()}

                    for pkg_n in set(norm_before.keys()) | set(norm_after.keys()):
                        old_version = norm_before.get(pkg_n)
                        new_version = norm_after.get(pkg_n)
                        if old_version and new_version and old_version != new_version:
                            change_type = "upgraded" if parse_version(new_version) > parse_version(old_version) else "downgraded"
                            versions_to_bubble.append({
                                "package": pkg_n,
                                "version_to_bubble": old_version,
                                "version_staying_active": new_version,
                                "change_type": change_type,
                                "user_requested": canonicalize_name(pkg_n) in user_requested_cnames,
                            })
                        elif not old_version and new_version:
                            main_env_kb_updates[pkg_n] = new_version

                    if versions_to_bubble:
                        safe_print(_("🛡️ LATEST-ACTIVE STRATEGY: Preserving replaced versions"))
                        for item in versions_to_bubble:
                            # Check if old version already has a bubble — if so, plan callback
                            # will handle the swap atomically via rename on the next install.
                            existing_bubble = self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}"
                            if existing_bubble.exists():
                                safe_print(f"    ⚡ Bubble already exists for {item['package']} v{item['version_to_bubble']} — swap handled via plan callback")
                                bubbled_kb_updates[item["package"]] = item["version_to_bubble"]
                                main_env_kb_updates[item["package"]] = item["version_staying_active"]
                                self.hook_manager.refresh_bubble_map(
                                    item["package"], item["version_to_bubble"],
                                    str(existing_bubble),
                                )
                                self.hook_manager.validate_bubble(item["package"], item["version_to_bubble"])
                                continue

                            bubble_created = self.create_isolated_bubble(
                                item["package"], item["version_to_bubble"],
                                python_context_version=python_context_version,
                            )
                            if bubble_created:
                                bubbled_kb_updates[item["package"]] = item["version_to_bubble"]
                                bubble_path_str = str(self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}")
                                self.hook_manager.refresh_bubble_map(item["package"], item["version_to_bubble"], bubble_path_str)
                                self.hook_manager.validate_bubble(item["package"], item["version_to_bubble"])
                                main_env_kb_updates[item["package"]] = item["version_staying_active"]
                                safe_print("    ✅ Bubbled {} v{}, keeping v{} active".format(
                                    item["package"], item["version_to_bubble"], item["version_staying_active"],
                                ))
                            else:
                                safe_print("    ❌ Failed to bubble {} v{}".format(item["package"], item["version_to_bubble"]))

            except NoCompatiblePythonError as e:
                safe_print("\n" + "=" * 60)
                safe_print("🌌 QUANTUM HEALING: Python Incompatibility Detected")
                safe_print("=" * 60)
                safe_print(_("   - Diagnosis: Cannot install '{}' on current Python {}.").format(e.package_name, python_context_version))
                from omnipkg.cli import handle_python_requirement
                compatible_py_ver = self._find_compatible_python_version(
                    e.package_name, self._parse_package_spec(package_spec)[1]
                )
                if not compatible_py_ver:
                    safe_print(f"❌ Healing failed: Could not find any compatible Python version for '{e.package_name}'.")
                    return 1
                if not handle_python_requirement(compatible_py_ver, self.core, "omnipkg"):
                    safe_print(_('❌ Healing failed: Could not automatically switch to Python {}.').format(compatible_py_ver))
                    return 1
                safe_print(_('\n🚀 Retrying original command in the new Python {} context...').format(compatible_py_ver))
                new_config_manager = ConfigManager()
                new_omnipkg_instance = self.core.__class__(new_config_manager)
                return new_omnipkg_instance.smart_install(packages, dry_run, force_reinstall, target_directory)

            except ValueError as e:
                safe_print(_('\n❌ Aborting installation: {}').format(e))
                return 1

        if not force_reinstall:
            self._cleanup_redundant_bubbles(protected_packages=protected_from_cleanup)

        # Knowledge base update
        safe_print(_("\n🧠 Updating knowledge base (priority packages only)..."))
        priority_specs: Set[str] = set()
        bubble_paths_to_scan: Dict[str, Path] = {}

        for name, ver in final_main_state.items():
            if name not in initial_packages_before or initial_packages_before[name] != ver:
                priority_specs.add(f"{name}=={ver}")

        for pkg_name, version in bubbled_kb_updates.items():
            priority_specs.add(f"{pkg_name}=={version}")
            bubble_path = self.multiversion_base / f"{pkg_name}-{version}"
            if bubble_path.exists():
                bubble_paths_to_scan[pkg_name] = bubble_path

        for pkg_name, version in main_env_kb_updates.items():
            priority_specs.add(f"{pkg_name}=={version}")

        if priority_specs:
            safe_print(_('    ⚡ Updating {} priority package(s) immediately...').format(len(priority_specs)))
            try:
                from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                gatherer = omnipkgMetadataGatherer(
                    config=self.config,
                    env_id=self.env_id,
                    target_context_version=python_context_version,
                    force_refresh=True,
                    omnipkg_instance=self.core,
                )
                gatherer.cache_client = self.cache_client
                gatherer.run(targeted_packages=list(priority_specs), skip_nested_discovery=True)
                safe_print("    ✅ Priority packages indexed")

                if bubble_paths_to_scan:
                    safe_print(_('    🔄 Scheduling background scan of {} bubble(s)...').format(len(bubble_paths_to_scan)))
                    self._schedule_background_kb_scan(bubble_paths_to_scan, python_context_version)

                if hasattr(self.core, "_info_cache"):
                    self.core._info_cache.clear()
                else:
                    self.core._info_cache = {}
                self.core._installed_packages_cache = None
                self._update_hash_index_for_delta(initial_packages_before, final_main_state)
                safe_print(_("    ✅ Knowledge base updated successfully."))
            except Exception as e:
                safe_print("    ⚠️ Failed to run consolidated knowledge base update: {}".format(e))
                traceback.print_exc()
        else:
            safe_print(_("    ✅ Knowledge base is already up to date."))

        safe_print(_("\n🎉 All package operations complete."))
        self._save_last_known_good_snapshot(target_directory=target_directory)
        self._synchronize_knowledge_base_with_reality()
        return 0