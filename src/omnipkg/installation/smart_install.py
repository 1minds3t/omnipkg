"""
omnipkg/installation/smart_install.py

SmartInstaller — faithful port of the working OmnipkgCore.smart_install().

Design principles (do NOT break these):
  - Logic order is IDENTICAL to the original core.py version that worked for months
  - _synchronize_knowledge_base_with_reality() is called exactly where the original called it
  - gatherer.run() is called exactly as the original called it (no _discover_distributions_fast)
  - doctor() + _heal_conda_environment() only run when real work is needed (preflight gate)
  - Shell is non-blocking: KB update + sync + snapshot + bubble cleanup run in background fork
  - Background fork logs EVERYTHING to /tmp/omnipkg_bg_<pid>.log
    (future CLI flag `8pkg logs` can tail/cat this)
  - The shim in core.py stays as-is
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from packaging.utils import canonicalize_name
from packaging.version import Version as parse_version

if TYPE_CHECKING:
    from omnipkg.core import omnipkg as OmnipkgCore

# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

_DEBUG_TIMING = os.environ.get("OMNIPKG_DEBUG", "0") == "1"


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
    if _DEBUG_TIMING:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"[TIMING] {label}: {elapsed:.2f}ms", flush=True)


# ---------------------------------------------------------------------------
# SmartInstaller
# ---------------------------------------------------------------------------

class SmartInstaller:
    """
    Orchestrates package installation for OmnipkgCore.
    Core delegates via a one-liner shim; all callers use core.smart_install() unchanged.
    """

    def __init__(self, core: "OmnipkgCore") -> None:
        self.core = core
        self.config = core.config
        self.multiversion_base: Path = core.multiversion_base

    # ------------------------------------------------------------------
    # Lazy properties
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public entry point — mirrors original smart_install() signature exactly
    # ------------------------------------------------------------------

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

        # ================================================================
        # ULTRA-FAST PREFLIGHT CHECK (before any heavy initialisation)
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

            # Phase 1: resolve versions + fast satisfaction check
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

            # Phase 2: everything satisfied?
            if not needs_installation:
                total_ns = int((time.perf_counter() - preflight_start) * 1_000_000_000)
                self._safe_print(
                    f"⚡ PREFLIGHT SUCCESS: All {len(packages)} package(s) already satisfied! ({_fmt_ns(total_ns)})"
                )
                return 0

            # Phase 3: pip-validate unresolved specs
            self._safe_print(f"\n📦 {len(needs_installation)} package(s) need installation/validation")
            validated_specs = []
            try:
                for spec in needs_installation:
                    pkg_name, version = self.core._parse_package_spec(spec)
                    if not version:
                        self._safe_print(f"   🔍 Resolving version for '{pkg_name}' with pip...")
                    else:
                        # fast disk check before hitting pip
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
            preflight_start = time.perf_counter()
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

        if not self.core._connect_cache():
            return 1

        if dry_run:
            self._safe_print("🔬 Running in --dry-run mode. No changes will be made.")
            return 0

        if not packages:
            self._safe_print("🚫 No packages specified for installation.")
            return 1

        # doctor + conda heal — only now that we know real work is needed
        self.core.doctor(dry_run=False, force=True)
        self.core._heal_conda_environment()

        # ================================================================
        # Resolve Python context version
        # ================================================================
        configured_exe = self.config.get("python_executable", sys.executable)
        version_tuple = self.core.config_manager._verify_python_version(configured_exe)
        python_context_version = (
            f"{version_tuple[0]}.{version_tuple[1]}" if version_tuple else "unknown"
        )
        if python_context_version == "unknown":
            self._safe_print("⚠️ CRITICAL: Could not determine Python context. Manifests may be stamped incorrectly.")

        install_strategy = self.config.get("install_strategy", "stable-main")
        packages_to_process = list(packages)

        # ================================================================
        # omnipkg special-case handling (identical to original)
        # ================================================================
        main_env_kb_updates: Dict[str, str] = {}
        bubbled_kb_updates: Dict[str, str] = {}
        any_installations_made = False
        protected_from_cleanup: set = set()
        final_main_state: Dict[str, str] = {}

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
                        self._safe_print(f"🫧 Creating bubble for current version (v{active_omnipkg_version})...")
                        bubble_created = self.bubble_manager.create_bubble_for_package(
                            "omnipkg", active_omnipkg_version,
                            python_context_version=python_context_version,
                        )
                        self._safe_print(
                            f"{'✅ Bubbled' if bubble_created else '⚠️  Failed to bubble'} omnipkg v{active_omnipkg_version}"
                        )

                self._safe_print(f"📦 Installing omnipkg=={requested_version} to main environment...")
                return_code, _ = self.core._run_pip_install(
                    [f"omnipkg=={requested_version}"],
                    target_directory=None,
                    force_reinstall=force_reinstall,
                )
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
                self._safe_print(f"🫧 Creating isolated bubble for omnipkg v{requested_version}...")
                bubble_created = self.bubble_manager.create_isolated_bubble(
                    "omnipkg", requested_version,
                    python_context_version=python_context_version,
                )
                if bubble_created:
                    self._safe_print(f"✅ omnipkg=={requested_version} successfully bubbled and registered.")
                    self.core._synchronize_knowledge_base_with_reality()
                    any_installations_made = True
                else:
                    self._safe_print(f"❌ Failed to create bubble for omnipkg=={requested_version}.")

        if not packages_to_process:
            self._safe_print("\n🎉 All package operations complete.")
            if original_strategy and original_strategy != self.config.get("install_strategy"):
                self.config["install_strategy"] = original_strategy
                self._safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")
            return 0

        # ================================================================
        # Resolve + sort packages
        # ================================================================
        self._safe_print(f"🚀 Starting install with policy: '{install_strategy}'")

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

        user_requested_cnames = {
            canonicalize_name(self.core._parse_package_spec(p)[0]) for p in packages
        }

        initial_packages_before = self.core.get_installed_packages(live=True)
        packages_before = initial_packages_before.copy()

        # ================================================================
        # Main install loop (identical logic to original)
        # ================================================================
        for package_spec in sorted_packages:
            try:
                self._safe_print("\n" + "─" * 60)
                pkg_name, pkg_version = self.core._parse_package_spec(package_spec)
                snapshot_key = self.core._create_pre_install_snapshot(pkg_name)

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
                    self._safe_print("   📸 Pre-install snapshot created")

                    satisfaction_check = self.core._check_package_satisfaction(
                        [package_spec], strategy=install_strategy
                    )
                    if satisfaction_check["all_satisfied"]:
                        self._safe_print(f"✅ Requirement already satisfied: {package_spec}")
                        continue

                packages_before = self.core.get_installed_packages(live=True)

                return_code, pkg_install_output = self.core._run_pip_install(
                    [package_spec],
                    target_directory=target_directory,
                    force_reinstall=force_reinstall,
                    index_url=index_url,
                    extra_index_url=extra_index_url,
                )

                if return_code != 0:
                    self._safe_print(f"❌ Pip installation failed for {package_spec}.")
                    self._safe_print("\n🔄 Restoring environment from pre-install snapshot...")
                    if self.core._restore_from_pre_install_snapshot(snapshot_key):
                        self._safe_print("   ✅ Environment restored to pre-install state")
                    else:
                        self._safe_print("   ❌ CRITICAL: Snapshot restore failed!")
                        self._safe_print("   💡 You may need to run: omnipkg revert")
                    continue

                any_installations_made = True
                packages_after = self.core.get_installed_packages(live=True)
                final_main_state = packages_after.copy()

                # Change detection
                all_changes = self.core._detect_all_changes(packages_before, packages_after)

                if all_changes["downgrades"] or all_changes["upgrades"] or all_changes["removals"]:
                    total_changes = len(all_changes["downgrades"] + all_changes["upgrades"] + all_changes["removals"])
                    self._safe_print(f"\n⚠️  Detected {total_changes} dependency changes:")
                    for change in all_changes["downgrades"]:
                        self._safe_print(f"   ⬇️  {change['package']}: v{change['old_version']} → v{change['new_version']} (downgrade)")
                    for change in all_changes["upgrades"]:
                        self._safe_print(f"   ⬆️  {change['package']}: v{change['old_version']} → v{change['new_version']} (upgrade)")
                    for change in all_changes["removals"]:
                        self._safe_print(f"   🗑️  {change['package']}: v{change['version']} (removed)")

                # Strategy handling
                if install_strategy == "stable-main":
                    packages_to_bubble = []
                    packages_to_restore = []

                    for change in all_changes["downgrades"] + all_changes["upgrades"]:
                        packages_to_bubble.append({
                            "package": change["package"],
                            "new_version": change["new_version"],
                            "old_version": change["old_version"],
                        })

                    if packages_to_bubble:
                        self._safe_print(f"\n🛡️ STABILITY PROTECTION: Processing {len(packages_to_bubble)} changed package(s)")
                        bubble_tracker = {}

                        for item in packages_to_bubble:
                            self._safe_print(f"\n   🫧 Creating bubble for {item['package']} v{item['new_version']}...")
                            bubble_created = self.bubble_manager.create_isolated_bubble(
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
                                self._safe_print("   ✅ Bubble created successfully")
                                packages_to_restore.append(item)
                            else:
                                self._safe_print(f"   ❌ Bubble creation FAILED for {item['package']} v{item['new_version']}")
                                self._safe_print("   🚨 CRITICAL: Cannot guarantee stability without this bubble!")
                                self._safe_print("\n   🔄 Initiating safe restore from snapshot...")
                                snapshot_data = self.cache_client.get(snapshot_key)
                                if snapshot_data:
                                    snapshot_state = json.loads(snapshot_data)
                                    if self.core._safe_restore_from_snapshot(pkg_name, snapshot_state, force=True):
                                        self._safe_print("   ✅ Environment safely restored to pre-install state")
                                    else:
                                        self._safe_print("   ❌ Restore failed — environment may be unstable!")
                                else:
                                    self._safe_print("   ❌ Snapshot not available — cannot restore!")
                                break

                        if len(bubble_tracker) == len(packages_to_bubble):
                            self._safe_print("\n   ✅ All bubbles created successfully")
                            self._safe_print("   🔄 Restoring stable versions to main environment...")
                            restore_specs = [
                                f"{item['package']}=={item['old_version']}"
                                for item in packages_to_restore
                            ]
                            restore_code, _ = self.core._run_pip_install(
                                restore_specs, force_reinstall=True, extra_flags=["--no-deps"]
                            )
                            if restore_code == 0:
                                self._safe_print("   ✅ All stable versions restored")
                                for item in packages_to_restore:
                                    main_env_kb_updates[item["package"]] = item["old_version"]
                                    protected_from_cleanup.add(canonicalize_name(item["package"]))
                            else:
                                self._safe_print("   ❌ Restore failed — using snapshot fallback")
                                snapshot_data = self.cache_client.get(snapshot_key)
                                if snapshot_data:
                                    snapshot_state = json.loads(snapshot_data)
                                    self.core._safe_restore_from_snapshot(pkg_name, snapshot_state, force=True)

                elif install_strategy == "latest-active":
                    versions_to_bubble = []
                    for pkg in set(packages_before.keys()) | set(packages_after.keys()):
                        old_version = packages_before.get(pkg)
                        new_version = packages_after.get(pkg)
                        if old_version and new_version and old_version != new_version:
                            versions_to_bubble.append({
                                "package": pkg,
                                "version_to_bubble": old_version,
                                "version_staying_active": new_version,
                                "user_requested": canonicalize_name(pkg) in user_requested_cnames,
                                "python_context_version": python_context_version,
                            })
                        elif not old_version and new_version:
                            main_env_kb_updates[pkg] = new_version

                    if versions_to_bubble:
                        self._safe_print("🛡️ LATEST-ACTIVE STRATEGY: Preserving replaced versions")
                        for item in versions_to_bubble:
                            bubble_created = self.bubble_manager.create_isolated_bubble(
                                item["package"], item["version_to_bubble"],
                                python_context_version=python_context_version,
                            )
                            if bubble_created:
                                bubbled_kb_updates[item["package"]] = item["version_to_bubble"]
                                bubble_path_str = str(
                                    self.multiversion_base / f"{item['package']}-{item['version_to_bubble']}"
                                )
                                self.hook_manager.refresh_bubble_map(
                                    item["package"], item["version_to_bubble"], bubble_path_str
                                )
                                self.hook_manager.validate_bubble(
                                    item["package"], item["version_to_bubble"]
                                )
                                main_env_kb_updates[item["package"]] = item["version_staying_active"]
                                self._safe_print(
                                    f"    ✅ Queued bubble for {item['package']} v{item['version_to_bubble']}, "
                                    f"keeping v{item['version_staying_active']} active"
                                )
                            else:
                                self._safe_print(f"    ❌ Failed to bubble {item['package']} v{item['version_to_bubble']}")

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
        # Post-install: bubble cleanup (non-blocking, happens in background)
        # ================================================================
        self._safe_print("\n🎉 All package operations complete.")

        if original_strategy and original_strategy != self.config.get("install_strategy"):
            self.config["install_strategy"] = original_strategy
            self._safe_print(f"   - ✅ Strategy restored to '{original_strategy}'")

        if any_installations_made:
            # Capture everything the background child will need — no shared state after fork
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
            }

            pid = os.fork()
            if pid == 0:
                # --------------------------------------------------------
                # CHILD PROCESS — all heavy post-install work happens here
                # --------------------------------------------------------
                try:
                    import datetime
                    log_path = f"/tmp/omnipkg_bg_{os.getpid()}.log"
                    _log = open(log_path, "w", buffering=1)
                    devnull_r = open(os.devnull, "r")
                    os.setsid()
                    os.dup2(devnull_r.fileno(), 0)
                    os.dup2(_log.fileno(), 1)
                    os.dup2(_log.fileno(), 2)
                    sys.stdout = _log
                    sys.stderr = _log

                    def bg(msg):
                        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        _log.write(f"[{ts}] {msg}\n")
                        _log.flush()

                    bg(f"=== omnipkg background tasks started (parent install finished) ===")
                    t_bg = time.time()

                    import omnipkg.cli as _cli_mod
                    core = _cli_mod._PRELOADED_CORE
                    if core is None:
                        from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
                        core = OmnipkgCore(ConfigManager())

                    # 1. KB priority update — same gatherer.run() the original used
                    priority_specs = _bg_data["priority_specs"]
                    if priority_specs:
                        bg(f"KB update: {len(priority_specs)} priority spec(s): {priority_specs}")
                        try:
                            from omnipkg.package_meta_builder import omnipkgMetadataGatherer
                            gatherer = omnipkgMetadataGatherer(
                                config=core.config,
                                env_id=core.env_id,
                                target_context_version=_bg_data["python_context_version"],
                                force_refresh=True,
                                omnipkg_instance=core,
                            )
                            gatherer.cache_client = core.cache_client
                            gatherer.run(targeted_packages=list(priority_specs))
                            bg("KB gatherer.run() complete")
                        except Exception as _e:
                            bg(f"KB gatherer error: {_e}\n{traceback.format_exc()}")

                    # 1b. Clean up stale dist-infos left behind by uv in main site-packages.
                    #     uv uses --link-mode symlink and sometimes leaves old dist-info dirs
                    #     behind after a swap. Delete any dist-info for a package whose active
                    #     version has changed, so scanners don't find ghost active installs.
                    try:
                        import glob as _glob
                        sp = core.config.get("site_packages_path", "")
                        for pkg_name, new_active_ver in _bg_data["main_env_kb_updates"].items():
                            pkg_norm = pkg_name.replace("-", "_").lower()
                            pattern = os.path.join(sp, f"{pkg_norm}-*.dist-info")
                            for di in _glob.glob(pattern):
                                di_path = Path(di)
                                # Extract version from dir name e.g. rich-14.3.3.dist-info
                                di_ver = di_path.name.replace(f"{pkg_norm}-", "").replace(".dist-info", "")
                                if di_ver != new_active_ver and di_path.is_dir():
                                    import shutil as _shutil2
                                    _shutil2.rmtree(str(di_path), ignore_errors=True)
                                    bg(f"  Removed stale dist-info: {di_path.name}")
                    except Exception as _sde:
                        bg(f"  Stale dist-info cleanup error: {_sde}")
                    # 2. Demote stale active KB records for packages that changed
                    #    (this is what was missing — old main-env inst keys that survive a swap)
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
                                    # Verify path still exists; delete if ghost
                                    if inst_path and not os.path.exists(inst_path):
                                        core.cache_client.delete(inst_key)
                                        bg(f"  Deleted ghost inst key for {pkg_name}=={inst_ver} (path gone: {inst_path})")
                        except Exception as _de:
                            bg(f"  Demotion error for {pkg_name}: {_de}")

                    # 3. Bubble cleanup
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

                    # 4. Full KB sync — the one that actually catches all ghosts
                    bg("Running _synchronize_knowledge_base_with_reality()...")
                    try:
                        core._synchronize_knowledge_base_with_reality()
                        bg("KB sync complete")
                    except Exception as _e:
                        bg(f"KB sync error: {_e}\n{traceback.format_exc()}")

                    # 5. Save last-known-good snapshot
                    bg("Saving snapshot...")
                    try:
                        core._save_last_known_good_snapshot(
                            known_state=_bg_data["final_main_state"] or None
                        )
                        bg("Snapshot saved")
                    except Exception as _e:
                        bg(f"Snapshot error: {_e}\n{traceback.format_exc()}")

                    # 6. Hash index update
                    bg("Updating hash index...")
                    try:
                        core._update_hash_index_for_delta(
                            _bg_data["initial_packages"],
                            _bg_data["final_main_state"],
                        )
                        bg("Hash index updated")
                    except Exception as _e:
                        bg(f"Hash index error: {_e}\n{traceback.format_exc()}")

                    # 7. Cloak cleanup
                    bg("Running cloak cleanup...")
                    try:
                        core._cleanup_all_cloaks_globally()
                        bg("Cloak cleanup complete")
                    except Exception as _e:
                        bg(f"Cloak cleanup error: {_e}\n{traceback.format_exc()}")

                    bg(f"=== background tasks complete in {(time.time()-t_bg)*1000:.0f}ms ===")

                except Exception as _fatal:
                    try:
                        _log.write(f"[FATAL] {_fatal}\n{traceback.format_exc()}\n")
                        _log.flush()
                    except Exception:
                        pass

                os._exit(0)
            else:
                self._safe_print(f"   🔄 Forked background tasks process (PID: {pid})")
                self._safe_print(f"   📋 Background log: /tmp/omnipkg_bg_{pid}.log")

        return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
