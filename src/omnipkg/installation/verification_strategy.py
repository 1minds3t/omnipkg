from omnipkg.common_utils import safe_print

"""
Smart Verification Strategy Module

Handles intelligent import testing of packages, respecting interdependencies
and testing related packages together when necessary.

This prevents false negatives from naive per-package testing.

CRITICAL FIX V2: Now uses sterile subprocess isolation to prevent ABI conflicts
AND includes already-created dependency bubbles so keras can find tensorflow!
"""

import importlib
import subprocess
import os
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from omnipkg.i18n import _

try:
    from .common_utils import safe_print
except ImportError:
    pass

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
    - keras failing without tensorflow
    - scipy failing without numpy
    
    CRITICAL V2: Uses sterile subprocess AND includes dependency bubbles.
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

    def _get_actual_import_names(self, dist) -> List[str]:
        """
        Read top_level.txt to get actual import names.
        
        This fixes issues where package name != import name (e.g. tomli -> tomli, autocommand -> autocommand).
        """
        try:
            if hasattr(dist, "read_text"):
                content = dist.read_text("top_level.txt")
                if content:
                    return [n for n in content.splitlines() if n and n.isidentifier()]
            # Fallback for older dist objects
            elif hasattr(dist, "files") and dist.files:
                for f in dist.files:
                    if f.name == "top_level.txt":
                        content = f.read_text()
                        return [n for n in content.splitlines() if n and n.isidentifier()]
        except Exception:
            pass
        return []

    def verify_packages_in_staging(
        self,
        staging_path: Path,
        target_package: str,
        all_dists: List,
        target_version: str = "unknown",
        existing_bubble_paths: List[Path] = None,  # NEW!
    ) -> Tuple[bool, List[VerificationResult]]:
        """
        Verify all packages in staging area using smart grouping.

        Args:
            staging_path: Path to staging directory
            target_package: The primary package being installed
            all_dists: List of distribution objects from metadata gatherer
            target_version: Version of the target package
            existing_bubble_paths: Paths to already-created dependency bubbles

        Returns:
            Tuple of (success: bool, results: List[VerificationResult])
        """
        if existing_bubble_paths is None:
            existing_bubble_paths = []
            
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

        safe_print(_('      Found {} package(s) in staging area').format(len(all_dists)))
        if len(packages_by_group) > 1:
            safe_print(_('      Organized into {} verification group(s)').format(len(packages_by_group)))

        # Step 2: Verify each group IN STERILE SUBPROCESS WITH BUBBLE PATHS
        all_results = []
        group_success = {}

        for group_name, group_info in packages_by_group.items():
            group_results = self._verify_group(
                group_name, 
                group_info["dists"], 
                group_info["group_def"], 
                staging_path,
                existing_bubble_paths  # NEW!
            )

            all_results.extend(group_results)

            # Group succeeds if all members succeed
            group_success[group_name] = all(r.success for r in group_results)

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
                    _('   ⚠️  Note: {} dependency/dependencies failed, but main package is OK.').format(failed_count)
                )
            return True, all_results
        else:
            safe_print(
                f"   ❌ CRITICAL: Main package '{target_package}' failed import verification."
            )
            return False, all_results

    def _organize_into_groups(self, all_dists: List) -> Dict[str, Dict]:
        """
        Organize distributions into verification groups with Namespace Clustering.

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
                standalone_packages.append(dist)
        
        # [SMART STRATEGY] Namespace Clustering
        # Group packages that share a common prefix (e.g., jaraco.text, jaraco.functools)
        # to ensure they are verified in the same context.
        namespace_clusters = {}
        remaining_standalone = []
        
        for dist in standalone_packages:
            pkg_name = dist.metadata["Name"]
            if "." in pkg_name:
                # Use the root namespace as the cluster key (e.g., 'jaraco', 'backports')
                root = pkg_name.split(".")[0]
                if root not in namespace_clusters:
                    namespace_clusters[root] = []
                namespace_clusters[root].append(dist)
            else:
                remaining_standalone.append(dist)
        
        # Add clusters to groups
        for root, cluster_dists in namespace_clusters.items():
            # If only 1 package, treat as standalone to avoid unnecessary grouping overhead
            if len(cluster_dists) == 1:
                remaining_standalone.extend(cluster_dists)
            else:
                groups[f"namespace:{root}"] = {"dists": cluster_dists, "group_def": None}

        # Each remaining standalone package gets its own "group"
        for dist in remaining_standalone:
            pkg_name = dist.metadata["Name"]
            groups[f"standalone:{pkg_name}"] = {"dists": [dist], "group_def": None}

        return groups

    def _verify_group(
        self, 
        group_name: str, 
        dists: List, 
        group_def: Optional[VerificationGroup], 
        staging_path: Path,
        existing_bubble_paths: List[Path] = None  # NEW!
    ) -> List[VerificationResult]:
        """
        Verify all packages in a group together IN A STERILE SUBPROCESS.
        
        CRITICAL FIX V2: Now includes dependency bubbles so keras can import tensorflow!
        """
        
        if existing_bubble_paths is None:
            existing_bubble_paths = []
        
        # Prepare the list of packages to test
        packages_to_test = []
        
        if group_def:
            safe_print(_("      - Testing group '{}' ({} packages together)...").format(group_name, len(dists)))
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
        
        # Build a simple list of dicts to send to the subprocess
        for dist in dists:
            pkg_name = dist.metadata["Name"]
            version = dist.metadata.get("Version", "unknown")
            
            # [SMART STRATEGY] Use top_level.txt for accurate import names
            candidates = self._get_actual_import_names(dist)
            if not candidates:
                # Fallback to heuristics
                candidates = self.gatherer._get_import_candidates(dist, pkg_name)
            
            if candidates:
                packages_to_test.append({
                    "name": pkg_name,
                    "version": version,
                    "modules": [c for c in candidates if c.isidentifier()]
                })
            else:
                safe_print(_('         🟡 Skipping {}: No importable modules').format(pkg_name))

        if not packages_to_test:
            return []

        # RUN THE STERILE SUBPROCESS WITH BUBBLE PATHS
        return self._run_sterile_verification(
            str(staging_path), 
            packages_to_test, 
            group_def is not None,
            [str(p) for p in existing_bubble_paths]  # NEW!
        )

    def _run_sterile_verification(
        self, 
        staging_path: str, 
        packages: List[Dict], 
        is_group: bool,
        bubble_paths: List[str] = None
    ) -> List[VerificationResult]:
        if bubble_paths is None: bubble_paths = []

        # Determine the omnipkg src root so the worker can import omnipkg internals
        # (e.g. rich, i18n) — but we deliberately EXCLUDE site-packages.
        # Injecting site-packages causes ABI crashes: e.g. numpy 2.x C extensions
        # get loaded alongside numpy 1.x pure-Python files from staging → instant ImportError.
        _this_file = Path(__file__).resolve()
        # Walk up to the 'src' directory (src/omnipkg/installation/verification_strategy.py)
        _omnipkg_src = str(_this_file.parent.parent.parent)

        # Only inject: stdlib entries (no path / zip files) + the omnipkg src root.
        # Explicitly exclude anything that looks like a site-packages or dist-packages dir.
        def _is_safe_path(p: str) -> bool:
            if not p:
                return False
            pl = p.lower()
            return "site-packages" not in pl and "dist-packages" not in pl

        safe_parent_paths = [_omnipkg_src] + [
            p for p in sys.path
            if p and _is_safe_path(p) and p != staging_path and p not in bubble_paths
        ]

        # Filter bubble_paths: drop any bubble that shadows a package we're staging
        _staged_names = {d.name for d in Path(staging_path).iterdir() if d.is_dir()} if Path(staging_path).exists() else set()
        bubble_paths_filtered = [
            p for p in bubble_paths
            if not any((Path(p) / n).exists() for n in _staged_names)
        ]

        worker_script = """
import sys, json, importlib, traceback

staging_path = {staging_path!r}
bubble_paths = {bubble_paths!r}
safe_parent_paths = {safe_parent_paths!r}

# Build sys.path from scratch:
#   1. staging (highest priority — the version we just installed)
#   2. dependency bubbles (already-built sibling packages)
#   3. safe parent paths (stdlib, omnipkg src — NO site-packages)
# This guarantees the staged package's C extensions and pure-Python files
# are always loaded together, preventing numpy-style ABI mismatch crashes.
sys.path = (
    [staging_path]
    + [p for p in bubble_paths if p]
    + [p for p in safe_parent_paths if p and p not in ([staging_path] + bubble_paths)]
)

import sys as _dbg; print("DEBUG_SYSPATH=" + repr(_dbg.path), file=_dbg.stderr, flush=True)

results = []
packages_data = {packages_json}

for pkg in packages_data:
    try:
        for module in pkg['modules']:
            importlib.import_module(module)
        results.append({{"package_name": pkg['name'], "version": pkg['version'], "success": True, "error": None}})
    except Exception:
        results.append({{"package_name": pkg['name'], "version": pkg['version'], "success": False, "error": traceback.format_exc()}})

print(json.dumps(results))
"""
        script = worker_script.format(
            staging_path=staging_path,
            bubble_paths=bubble_paths_filtered,
            safe_parent_paths=safe_parent_paths,
            packages_json=json.dumps(packages),
        )

        try:
            _env = os.environ.copy(); _env["PYTHONNOUSERSITE"] = "1"; _env["PYTHONPATH"] = ""
            proc = subprocess.run([sys.executable, "-I", "-c", script], env=_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            if proc.returncode != 0 and not proc.stdout.strip():
                # Provide stderr in crash report
                return [VerificationResult(p['name'], p['version'], False, f"Crash: {proc.stderr}") for p in packages]
            
            # Handle potential JSON decode errors if stdout contains garbage
            try:
                raw_results = json.loads(proc.stdout)
                return [VerificationResult(r['package_name'], r['version'], r['success'], r['error']) for r in raw_results]
            except json.JSONDecodeError:
                 return [VerificationResult(p['name'], p['version'], False, _('JSON Error: {}...').format(proc.stdout[:200])) for p in packages]
                 
        except Exception as e:
            return [VerificationResult(p['name'], p['version'], False, str(e)) for p in packages]

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

            safe_print(_('      {} {}: {}').format(status, result.package_name, detail))


# ============================================================================
# INTEGRATION HELPER
# ============================================================================


def verify_bubble_with_smart_strategy(
    parent_omnipkg, 
    package_name: str, 
    version: str, 
    staging_path: Path, 
    gatherer,
    existing_bubble_paths: List[Path] = None  # NEW!
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
        existing_bubble_paths: Paths to dependency bubbles (NEW!)

    Returns:
        True if verification passed, False otherwise
    """
    if existing_bubble_paths is None:
        existing_bubble_paths = []
        
    all_dists = gatherer._discover_distributions(
        targeted_packages=None, search_path_override=str(staging_path)
    )

    strategy = SmartVerificationStrategy(parent_omnipkg, gatherer)
    success, results = strategy.verify_packages_in_staging(
        staging_path,
        package_name,
        all_dists,
        target_version=version,
        existing_bubble_paths=existing_bubble_paths,  # NEW!
    )

    return success