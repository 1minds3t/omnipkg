from omnipkg.common_utils import safe_print

"""
Smart Verification Strategy Module

Handles intelligent import testing of packages, respecting interdependencies
and testing related packages together when necessary.

This prevents false negatives from naive per-package testing.
"""

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

try:
    from .verification_groups import (
        VerificationGroup,
        find_verification_group,
        get_affected_groups,
        get_group_members,
    )
except ImportError:
    from omnipkg.installation.verification_groups import (
        VerificationGroup,
        find_verification_group,
    )


@dataclass
class VerificationResult:
    """Result of a package verification test."""

    package_name: str
    version: str
    success: bool
    error: Optional[str] = None
    tested_with: Optional[List[str]] = None  # Other packages tested together


class SmartVerificationStrategy:
    """
    Smart verification that tests packages together when needed.

    This prevents issues like:
    - h11 failing when httpcore/httpx aren't loaded
    - tensorboard failing without tensorflow
    - scipy failing without numpy
    """

    def __init__(self, parent_omnipkg, gatherer):
        """
        Initialize the verification strategy.

        Args:
            parent_omnipkg: The main OmnipkgCore instance
            gatherer: omnipkgMetadataGatherer instance for package discovery
        """
        self.parent_omnipkg = parent_omnipkg
        self.gatherer = gatherer
        self.original_sys_path = None

    def verify_packages_in_staging(
        self,
        staging_path: Path,
        target_package: str,
        all_dists: List,
        target_version: str = "unknown",
    ) -> Tuple[bool, List[VerificationResult]]:
        """
        Verify all packages in staging area using smart grouping.

        Args:
            staging_path: Path to staging directory
            target_package: The primary package being installed
            all_dists: List of distribution objects from metadata gatherer
            target_version: Version of the target package

        Returns:
            Tuple of (success: bool, results: List[VerificationResult])
        """
        if not all_dists:
            safe_print("   ❌ Verification failed: No valid packages in staging.")
            return False, []

        # Run PRE_VERIFICATION hooks
        try:
            from .verification_hooks import HookContext, HookType, run_hooks

            hook_context = HookContext(
                package_name=target_package,
                version=target_version,
                staging_path=staging_path,
                parent_omnipkg=self.parent_omnipkg,
                gatherer=self.gatherer,
            )

            if not run_hooks(HookType.PRE_VERIFICATION, hook_context):
                safe_print("   ❌ Pre-verification hooks failed")
                return False, []
        except ImportError:
            # Hooks not available, continue without them
            hook_context = None

        # Step 1: Organize packages into verification groups
        packages_by_group = self._organize_into_groups(all_dists)

        safe_print(f"      Found {len(all_dists)} package(s) in staging area")
        if len(packages_by_group) > 1:
            safe_print(f"      Organized into {len(packages_by_group)} verification group(s)")

        # Step 2: Verify each group
        all_results = []
        group_success = {}

        self.original_sys_path = sys.path[:]

        try:
            sys.path.insert(0, str(staging_path))

            for group_name, group_info in packages_by_group.items():
                group_results = self._verify_group(
                    group_name, group_info["dists"], group_info["group_def"]
                )

                all_results.extend(group_results)

                # Group succeeds if all members succeed
                group_success[group_name] = all(r.success for r in group_results)

        finally:
            # Restore sys.path and clean up imports
            sys.path[:] = self.original_sys_path
            self._cleanup_imports(all_dists)

        # Step 3: Print summary
        self._print_verification_summary(all_results)

        # Step 4: Check if main package succeeded
        canonical_target = target_package.lower().replace("_", "-")
        target_result = next(
            (
                r
                for r in all_results
                if r.package_name.lower().replace("_", "-") == canonical_target
            ),
            None,
        )

        # Run success/failure hooks
        if hook_context:
            try:
                if target_result and target_result.success:
                    run_hooks(HookType.ON_SUCCESS, hook_context)
                    run_hooks(HookType.POST_VERIFICATION, hook_context)
                else:
                    run_hooks(HookType.ON_FAILURE, hook_context)
            except:
                pass  # Don't let hook failures break verification

        if target_result and target_result.success:
            safe_print(f"   ✅ Main package '{target_package}' passed verification.")
            failed_count = sum(1 for r in all_results if not r.success)
            if failed_count > 0:
                safe_print(
                    f"   ⚠️  Note: {failed_count} dependency/dependencies failed, but main package is OK."
                )
            return True, all_results
        else:
            safe_print(
                f"   ❌ CRITICAL: Main package '{target_package}' failed import verification."
            )
            return False, all_results

    def _organize_into_groups(self, all_dists: List) -> Dict[str, Dict]:
        """
        Organize distributions into verification groups.

        Returns:
            Dict mapping group_name -> {dists: [...], group_def: VerificationGroup}
        """
        groups = {}
        standalone_packages = []

        for dist in all_dists:
            pkg_name = dist.metadata["Name"]
            canonical = pkg_name.lower().replace("_", "-")

            group_def = find_verification_group(canonical)

            if group_def:
                group_name = group_def.name
                if group_name not in groups:
                    groups[group_name] = {"dists": [], "group_def": group_def}
                groups[group_name]["dists"].append(dist)
            else:
                # Standalone package (no group)
                standalone_packages.append(dist)

        # Each standalone package gets its own "group"
        for dist in standalone_packages:
            pkg_name = dist.metadata["Name"]
            groups[f"standalone:{pkg_name}"] = {"dists": [dist], "group_def": None}

        return groups

    def _verify_group(
        self, group_name: str, dists: List, group_def: Optional[VerificationGroup]
    ) -> List[VerificationResult]:
        """
        Verify all packages in a group together.

        This is the KEY IMPROVEMENT: Instead of testing each package alone,
        we test them with their dependencies loaded.
        """
        results = []

        if group_def:
            safe_print(f"      - Testing group '{group_name}' ({len(dists)} packages together)...")
            test_order = group_def.test_order if group_def.test_order else None
        else:
            test_order = None

        # Sort distributions by test order if specified
        if test_order:
            dist_map = {d.metadata["Name"].lower().replace("_", "-"): d for d in dists}
            sorted_dists = []
            for pkg in test_order:
                if pkg in dist_map:
                    sorted_dists.append(dist_map[pkg])
            # Add any packages not in test_order
            for d in dists:
                if d not in sorted_dists:
                    sorted_dists.append(d)
            dists = sorted_dists

        # Test each package IN ORDER with previous ones already loaded
        loaded_modules = set()

        for dist in dists:
            pkg_name = dist.metadata["Name"]
            version = dist.metadata.get("Version", "unknown")

            import_candidates = self.gatherer._get_import_candidates(dist, pkg_name)

            if not import_candidates:
                safe_print(f"         🟡 Skipping {pkg_name}: No importable modules")
                continue

            # Attempt to import this package
            try:
                for candidate in import_candidates:
                    if candidate.isidentifier():
                        importlib.import_module(candidate)
                        loaded_modules.add(candidate)

                # Success!
                tested_with = list(loaded_modules - {pkg_name}) if group_def else None
                results.append(
                    VerificationResult(
                        package_name=pkg_name,
                        version=version,
                        success=True,
                        tested_with=tested_with,
                    )
                )

            except Exception as e:
                # Failure
                error_msg = str(e)
                # Truncate very long errors
                if len(error_msg) > 100:
                    error_msg = error_msg[:97] + "..."

                results.append(
                    VerificationResult(
                        package_name=pkg_name,
                        version=version,
                        success=False,
                        error=error_msg,
                        tested_with=list(loaded_modules) if group_def else None,
                    )
                )

        return results

    def _cleanup_imports(self, all_dists: List):
        """Clean up any modules imported during verification."""
        for dist in all_dists:
            pkg_name = dist.metadata["Name"]
            candidates = self.gatherer._get_import_candidates(dist, pkg_name)
            for candidate in candidates:
                if candidate in sys.modules:
                    del sys.modules[candidate]

    def _print_verification_summary(self, results: List[VerificationResult]):
        """Print a formatted summary of verification results."""
        safe_print("      " + "=" * 30)
        safe_print("      VERIFICATION SUMMARY")
        safe_print("      " + "=" * 30)

        for result in results:
            if result.success:
                status = "✅"
                detail = "OK"
            else:
                status = "❌"
                detail = f"FAILED ({result.error})"

            safe_print(f"      {status} {result.package_name}: {detail}")


# ============================================================================
# INTEGRATION HELPER
# ============================================================================


def verify_bubble_with_smart_strategy(
    parent_omnipkg, package_name: str, version: str, staging_path: Path, gatherer
) -> bool:
    """
    Verify a bubble using the smart strategy.

    This is the main entry point for integration with existing code.

    Args:
        parent_omnipkg: OmnipkgCore instance
        package_name: Name of primary package
        version: Version of primary package
        staging_path: Path to staging directory
        gatherer: omnipkgMetadataGatherer instance

    Returns:
        True if verification passed, False otherwise
    """
    all_dists = gatherer._discover_distributions(
        targeted_packages=None, search_path_override=str(staging_path)
    )

    strategy = SmartVerificationStrategy(parent_omnipkg, gatherer)
    success, results = strategy.verify_packages_in_staging(
        staging_path,
        package_name,
        all_dists,
        target_version=version,  # Pass version for hooks
    )

    return success
