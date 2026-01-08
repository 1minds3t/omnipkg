from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print
from omnipkg.i18n import _

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
"""
omnipkg_metadata_builder.py - v11 - The "Multi-Version Complete" Edition
A fully integrated, self-aware metadata gatherer with complete multi-version
support for robust, side-by-side package management.
import importlib
"""
import concurrent.futures
import hashlib
try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from packaging.utils import canonicalize_name
from packaging.version import parse as parse_version

from omnipkg.loader import omnipkgLoader

try:
    import safety

    SAFETY_AVAILABLE = True
except ImportError:
    SAFETY_AVAILABLE = False
# Add this global recursion tracking code at module level (after imports, before class definition)
_security_scan_depth = threading.local()
_max_depth = 10  # Adjust as needed
_security_scan_lock = threading.RLock()
_security_scan_running = threading.local()

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False


def get_python_version():
    """Get current Python version in X.Y format"""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def get_site_packages_path():
    """Dynamically find the site-packages path"""
    import site

    site_packages_dirs = site.getsitepackages()
    if hasattr(site, "getusersitepackages"):
        site_packages_dirs.append(site.getusersitepackages())
    if hasattr(sys, "prefix") and sys.prefix != sys.base_prefix:
        venv_site_packages = (
            Path(sys.prefix) / "lib" / f"python{get_python_version()}" / "site-packages"
        )
        if venv_site_packages.exists():
            return str(venv_site_packages)
    for sp in site_packages_dirs:
        if Path(sp).exists():
            return sp
    return str(
        Path(sys.executable).parent.parent
        / "lib"
        / f"python{get_python_version()}"
        / "site-packages"
    )


def get_bin_paths():
    """Get binary paths to index"""
    paths = [str(Path(sys.executable).parent)]
    if hasattr(sys, "prefix") and sys.prefix != sys.base_prefix:
        venv_bin = str(Path(sys.prefix) / "bin")
        if venv_bin not in paths and Path(venv_bin).exists():
            paths.append(venv_bin)
    return paths


class omnipkgMetadataGatherer:

    def __init__(
        self,
        config: Dict,
        env_id: str,
        force_refresh: bool = False,
        omnipkg_instance=None,
        target_context_version: Optional[str] = None,
    ):
        self.cache_client = None
        self.omnipkg_instance = omnipkg_instance
        self.cache_client = self.omnipkg_instance.cache_client if self.omnipkg_instance else None
        self.force_refresh = force_refresh
        self.target_context_version = target_context_version
        self.security_report = {}
        self.target_context_version = target_context_version
        self.config = config
        self.env_id = os.environ.get("OMNIPKG_ENV_ID_OVERRIDE", env_id)
        self.package_path_registry = {}
        if self.force_refresh:
            safe_print(_("🟢 --force flag detected. Caching will be ignored."))
        if not HAS_TQDM:
            safe_print(_("⚠️ Install 'tqdm' for a better progress bar."))

    @property
    def redis_env_prefix(self) -> str:
        """
        Delegates to the main omnipkg instance to get the correct,
        environment-specific key prefix.
        """
        if self.omnipkg_instance:
            return self.omnipkg_instance.redis_env_prefix
        # Fallback in case the main instance isn't available for some reason
        return self.redis_key_prefix.rsplit("pkg:", 1)[0]

    @property
    def redis_key_prefix(self) -> str:
        """
        (CORRECTED) This now DELEGATES to the main omnipkg instance to get the
        one, true, authoritative redis_key_prefix. This eliminates the mismatch bug.
        """
        if self.omnipkg_instance and hasattr(self.omnipkg_instance, "redis_key_prefix"):
            # This is the primary, correct path.
            return self.omnipkg_instance.redis_key_prefix

        # The following is a fallback for rare cases (like direct script execution)
        # and is now corrected to match the logic in core.py exactly.
        python_exe_path = self.config.get("python_executable", sys.executable)
        py_ver_str = "unknown"
        match = re.search("python(3\\.\\d+)", python_exe_path)
        if match:
            py_ver_str = f"py{match.group(1)}"
        else:
            try:
                result = subprocess.run(
                    [
                        python_exe_path,
                        "-c",
                        "import sys; print(f'py{sys.version_info.major}.{sys.version_info.minor}')",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=2,
                )
                py_ver_str = result.stdout.strip()
            except Exception:
                py_ver_str = f"py{sys.version_info.major}.{sys.version_info.minor}"

        return f"omnipkg:env_{self.env_id}:{py_ver_str}:pkg:"

    def _get_package_name_variants(self, name: str) -> List[str]:
        """
        Generates comprehensive package name variants to handle ALL Python packaging
        naming conventions including dots, hyphens, underscores.
        """
        variants = {
            name,
            canonicalize_name(name),
            name.replace("-", "_"),
            name.replace("_", "-"),
            name.replace("-", "."),
            name.replace(".", "-"),
            name.replace("_", "."),
            name.replace(".", "_"),
            name.lower(),
            name.upper(),
        }
        clean_name = name.lower()
        if clean_name.startswith("python-"):
            base = clean_name[7:]
            variants.update(
                {
                    base,
                    base.replace("-", "_"),
                    base.replace("-", "."),
                    base.replace("_", "."),
                    base.replace("_", "-"),
                }
            )
        if clean_name.startswith("py-"):
            base = clean_name[3:]
            variants.update(
                {
                    base,
                    base.replace("-", "_"),
                    base.replace("-", "."),
                    base.replace("_", "."),
                    base.replace("_", "-"),
                }
            )
        if clean_name.endswith("-python"):
            base = clean_name[:-7]
            variants.update(
                {
                    base,
                    base.replace("-", "_"),
                    base.replace("-", "."),
                    base.replace("_", "."),
                    base.replace("_", "-"),
                }
            )
        return list(variants)

    def _is_known_subcomponent(self, dist_info_path: Path) -> bool:
        """Check if this dist-info belongs to a sub-component that shouldn't be treated independently."""
        name = dist_info_path.name
        subcomponent_patterns = ["tensorboard_data_server-", "tensorboard_plugin_"]
        for pattern in subcomponent_patterns:
            if name.startswith(pattern):
                return True
        return False

        # ADD THIS HELPER METHOD TO omnipkgMetadataGatherer IN package_meta_builder.py

    def _is_dist_compatible_with_context(
        self, dist: importlib.metadata.Distribution, python_version: str
    ) -> bool:
        """Checks if a given distribution is compatible with the specified python_version context."""
        context_info = self._get_install_context(dist)
        install_type = context_info["install_type"]

        if install_type in ["active", "vendored", "unknown"]:
            return True

        if install_type in ["bubble", "nested"]:
            multiversion_base_path = Path(self.config.get("multiversion_base", "/dev/null"))
            try:
                relative_to_base = dist._path.relative_to(multiversion_base_path)
                bubble_root_name = relative_to_base.parts[0]
                bubble_root_path = multiversion_base_path / bubble_root_name
                manifest_file = bubble_root_path / ".omnipkg_manifest.json"

                if not manifest_file.exists():
                    return True  # Assume compatible if no manifest (legacy)

                with open(manifest_file, "r") as f:
                    manifest = json.load(f)

                return manifest.get("python_version") == python_version
            except (ValueError, IndexError, IOError, json.JSONDecodeError):
                return True  # Be safe, assume compatible on error

        return False

    def _parse_distribution_worker(
        self, dist_info_path: Path
    ) -> Optional[importlib.metadata.Distribution]:
        """
        Worker function for parallel discovery with AUTO-HEALING.
        If metadata is corrupt, attempts emergency repair before giving up.
        """
        try:
            from importlib.metadata import PathDistribution

            dist = PathDistribution(dist_info_path)

            # Basic validation: ensure it has a valid name string
            name = dist.metadata.get("Name")
            if name and isinstance(name, str):
                return dist

            # CORRUPTION DETECTED - ATTEMPT EMERGENCY HEAL
            metadata_file = dist_info_path / "METADATA"
            if metadata_file.exists():
                try:
                    content = metadata_file.read_text(encoding="utf-8", errors="ignore")

                    # Check if Name is really missing
                    if "Name:" not in content[:500]:
                        # Extract package name from folder
                        folder_name = dist_info_path.name
                        if folder_name.endswith(".dist-info"):
                            folder_name = folder_name[:-10]

                        # Parse name-version format
                        parts = folder_name.rsplit("-", 1)
                        if len(parts) >= 1:
                            pkg_name = parts[0]

                            # Inject Name field
                            fixed_content = f"Name: {pkg_name}\n{content}"

                            # Atomic write
                            temp_file = metadata_file.with_suffix(".tmp")
                            temp_file.write_text(fixed_content, encoding="utf-8")
                            temp_file.replace(metadata_file)

                            # Reload and try again
                            dist = PathDistribution(dist_info_path)
                            name = dist.metadata.get("Name")
                            if name and isinstance(name, str):
                                return dist
                except Exception:
                    pass  # Healing failed, continue to return None

        except Exception:
            pass  # Unreadable metadata

        return None

    def _run_strategy_1(
        self,
        base_path: Path,
        name_variants: List[str],
        version: Optional[str],
        verbose: bool,
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """Strategy 1: Check for vendored packages (SILENT except in verbose mode)"""
        results = []
        if verbose:
            safe_print("      -> Strategy 1: Checking for vendored packages...")

        vendored_dist_infos = list(base_path.rglob("*/_vendor/*.dist-info"))
        if verbose:
            safe_print(f"         Found {len(vendored_dist_infos)} vendored dist-info directories")

        for vendor_dist_info in vendored_dist_infos:
            if not vendor_dist_info.is_dir():
                continue
            try:
                dist = importlib.metadata.Distribution.at(vendor_dist_info)
                dist_name = dist.metadata.get("Name", "")
                name_matches = any(
                    (
                        canonicalize_name(dist_name) == canonicalize_name(variant)
                        for variant in name_variants
                    )
                )
                if name_matches and (version is None or dist.version == version):
                    results.append((dist, vendor_dist_info.resolve()))
                    if verbose:  # Only print in verbose mode
                        vendor_parent = str(vendor_dist_info).split("/_vendor/")[0].split("/")[-1]
                        safe_print(
                            f"✅ Found VENDORED {dist_name} v{dist.version} (inside {vendor_parent}) at {vendor_dist_info}"
                        )
            except Exception:
                continue
        return results

    def _run_strategy_2(
        self,
        base_path: Path,
        name: str,
        name_variants: List[str],
        version: Optional[str],
        verbose: bool,
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """Strategy 2: Direct pattern matching (SILENT except in verbose mode)"""
        results = []
        if verbose:
            safe_print("      -> Strategy 2: Direct pattern matching...")

        for variant in name_variants:
            if version:
                patterns = [
                    f"{variant}-{version}.dist-info",
                    f"{variant}-{version}-*.dist-info",
                    f"{variant.replace('.', '_')}-{version}.dist-info",
                    f"{variant.replace('.', '_')}-{version}-*.dist-info",
                ]
            else:
                patterns = [
                    f"{variant}-*.dist-info",
                    f"{variant.replace('.', '_')}-*.dist-info",
                ]

            for pattern in patterns:
                matching_paths = list(base_path.glob(pattern))
                for dist_info_path in matching_paths:
                    if not dist_info_path.is_dir():
                        continue
                    try:
                        dist = importlib.metadata.Distribution.at(dist_info_path)
                        dist_name = dist.metadata.get("Name", "")
                        if canonicalize_name(dist_name) == canonicalize_name(name):
                            if version is None or dist.version == version:
                                results.append((dist, dist_info_path.resolve()))
                                if verbose:  # Only print in verbose mode
                                    safe_print(
                                        f"✅ Found {dist_name} v{dist.version} at {dist_info_path}"
                                    )
                    except Exception:
                        continue
        return results

    def _run_strategy_3(
        self,
        base_path: Path,
        name_variants: List[str],
        version: Optional[str],
        verbose: bool,
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """Strategy 3: Nested directory search (SILENT except in verbose mode)"""
        results = []
        if verbose:
            safe_print("      -> Strategy 3: Searching nested directories...")

        # FIX: Deduplicate variants to reduce IO ops
        unique_variants = set(name_variants)

        for variant in unique_variants:
            # FIX: Always use wildcard patterns for directory discovery.
            # We rely on the internal metadata check to filter by version.
            # This handles cases where directory naming (underscores) doesn't match
            # standard normalization (dashes), or if suffixes exist.
            patterns = [f"{variant}-*", f"{variant.replace('.', '_')}-*"]

            for pattern in patterns:
                matching_dirs = list(base_path.glob(pattern))
                for nested_dir in matching_dirs:
                    if not nested_dir.is_dir():
                        continue

                    # Optimization: If we have a specific version, check if directory name
                    # strongly suggests a mismatch before parsing metadata (saves IO)
                    if (
                        version
                        and f"-{version}" not in nested_dir.name
                        and f"_{version}" not in nested_dir.name
                    ):
                        # Directory doesn't contain the version string?
                        # Only skip if we are fairly sure (e.g., standard bubble naming)
                        # But be careful not to skip valid loose matches.
                        pass

                    for dist_info_path in nested_dir.glob("*.dist-info"):
                        if not dist_info_path.is_dir():
                            continue
                        try:
                            # Must use PathDistribution for paths outside sys.path
                            from importlib.metadata import PathDistribution

                            dist = PathDistribution(dist_info_path)

                            dist_name = dist.metadata.get("Name", "")

                            # Authoritative check
                            name_matches = any(
                                (
                                    canonicalize_name(dist_name) == canonicalize_name(v)
                                    for v in name_variants
                                )
                            )

                            if name_matches:
                                if version is None or dist.version == version:
                                    results.append((dist, dist_info_path.resolve()))
                                    if verbose:  # Only print in verbose mode
                                        safe_print(
                                            f"✅ Found nested {dist_name} v{dist.version} at {dist_info_path}"
                                        )
                                elif verbose and version:
                                    safe_print(
                                        f"         Found {dist_name} in nested dir, but version mismatch ({dist.version} != {version})"
                                    )

                        except Exception:
                            continue
        return results

    def _run_strategy_4(
        self,
        base_path: Path,
        name_variants: List[str],
        version: Optional[str],
        verbose: bool,
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """Strategy 4: Comprehensive fallback scan (SILENT except in verbose mode)"""
        results = []
        if verbose:
            safe_print("      -> Strategy 4: Fallback comprehensive scan...")

        all_dist_infos = list(base_path.glob("*.dist-info"))
        all_dist_infos.extend(list(base_path.glob("*/*.dist-info")))
        all_dist_infos.extend(list(base_path.rglob("*.dist-info")))

        # Deduplicate
        seen = set()
        unique_dist_infos = []
        for path in all_dist_infos:
            if path not in seen:
                seen.add(path)
                unique_dist_infos.append(path)

        if verbose:
            safe_print(
                f"         Found {len(unique_dist_infos)} unique dist-info directories to check"
            )

        for dist_info_path in unique_dist_infos:
            if not dist_info_path.is_dir():
                continue
            try:
                dist = importlib.metadata.Distribution.at(dist_info_path)
                dist_name = dist.metadata.get("Name", "")
                name_matches = any(
                    (
                        canonicalize_name(dist_name) == canonicalize_name(variant)
                        for variant in name_variants
                    )
                )
                if name_matches and (version is None or dist.version == version):
                    results.append((dist, dist_info_path.resolve()))
                    if verbose:  # Only print in verbose mode
                        safe_print(f"✅ Found {dist_name} v{dist.version} at {dist_info_path}")
            except Exception:
                continue
        return results

    def _run_strategy_5(
        self, name: str, version: Optional[str], verbose: bool, search_paths: List[Path]
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """
        Strategy 5: Authoritative lookup via importlib.metadata, scoped to specific paths.
        """
        results = []
        if verbose:
            safe_print("      -> Strategy 5: Authoritative lookup via importlib.metadata...")

        target_canonical_name = canonicalize_name(name)

        try:
            # Search each path explicitly using PathDistribution
            for search_path in search_paths:
                if verbose:
                    safe_print(f"         -> Checking {search_path}")

                # Get all distributions from this specific path
                from importlib.metadata import distributions

                for dist in distributions(path=[str(search_path)]):
                    dist_name = dist.metadata.get("Name", "")
                    if canonicalize_name(dist_name) == target_canonical_name:
                        if version is None or dist.version == version:
                            results.append((dist, dist._path.resolve()))
                            if verbose:
                                safe_print(
                                    f"✅ Found AUTHORITATIVE {dist_name} v{dist.version} at {dist._path}"
                                )
        except Exception as e:
            if verbose:
                safe_print(f"      -> ⚠️  Strategy 5 failed: {e}")

        return results

    def _run_strategy_6(
        self,
        base_path: Path,
        name_variants: List[str],
        version: Optional[str],
        verbose: bool,
    ) -> List[Tuple[importlib.metadata.Distribution, Path]]:
        """
        Strategy 6: Recursive Exact Match (Surgical Strike).
        Specifically looks for 'package-version.dist-info' anywhere in the tree.
        Crucial for deep nesting (e.g. bubbles with full venv structures).
        """
        results = []
        if not version:
            return results  # This strategy requires a version to be efficient

        if verbose:
            safe_print(f"      -> Strategy 6: Recursive exact match for v{version}...")

        unique_variants = set(name_variants)
        for variant in unique_variants:
            # Generate the exact folder names we expect
            candidates = [
                f"{variant}-{version}.dist-info",
                f"{variant.replace('.', '_')}-{version}.dist-info",
                f"{variant.replace('-', '_')}-{version}.dist-info",
            ]

            for candidate in candidates:
                try:
                    # rglob is efficient when looking for a specific name
                    matches = list(base_path.rglob(candidate))
                    for dist_info_path in matches:
                        if not dist_info_path.is_dir():
                            continue

                        try:
                            # Use PathDistribution for isolation
                            from importlib.metadata import PathDistribution

                            dist = PathDistribution(dist_info_path)

                            dist_name = dist.metadata.get("Name", "")
                            # Verify name matches (ignore case/normalization)
                            if canonicalize_name(dist_name) == canonicalize_name(variant):
                                if dist.version == version:
                                    results.append((dist, dist_info_path.resolve()))
                                    if verbose:
                                        safe_print(
                                            f"✅ Found DEEP NESTED {dist_name} v{dist.version} at {dist_info_path}"
                                        )
                        except Exception:
                            continue
                except Exception:
                    continue

        return results

    def _discover_distributions(
        self,
        targeted_packages: Optional[List[str]],
        verbose: bool = False,
        search_path_override: Optional[str] = None,
        skip_existing_checksums: bool = False,
    ) -> List[importlib.metadata.Distribution]:
        """
        (V16 - SURGICAL FAST-PATH) Discovers distributions by prioritizing direct,
        fast-path lookups for targeted packages before falling back to comprehensive scans.
        This dramatically speeds up post-install KB updates.
        """
        # --- Stage 1: Determine search paths ---
        main_site_packages = Path(self.config.get("site_packages_path")).resolve()
        multiversion_base = Path(self.config.get("multiversion_base")).resolve()

        if search_path_override:
            search_paths = [Path(search_path_override).resolve()]
            if verbose:
                safe_print(
                    f"   - STRATEGY: Constrained search. ONLY this path will be used: {search_paths[0]}"
                )
        else:
            search_paths = [p for p in [main_site_packages, multiversion_base] if p.exists()]

        if not search_paths:
            safe_print("   - ❌ ERROR: No valid search paths determined. Aborting discovery.")
            return []

        # --- TARGETED PACKAGE MODE (WITH NEW FAST-PATH LOGIC) ---
        if targeted_packages:
            if verbose:
                safe_print(
                    f"🎯 Running SURGICAL targeted scan for {len(targeted_packages)} package(s)."
                )

            all_found_dists = (
                {}
            )  # Use dict to store {resolved_path: dist} for automatic deduplication

            for spec in targeted_packages:
                try:
                    name, version = self._parse_package_spec(spec)
                    if not version:
                        safe_print(
                            f"   ⚠️  Skipping '{spec}' in fast discovery - no version specified for direct lookup."
                        )
                        continue
                except ValueError as e:
                    safe_print(f"❌ Could not parse spec '{spec}': {e}")
                    continue

                if verbose:
                    safe_print(f"    surgically searching for '{spec}'...")

                found_for_spec = False

                # FAST-PATH 1: Check expected bubble location
                expected_bubble_path = multiversion_base / f"{name}-{version}"
                if expected_bubble_path.is_dir():
                    dist_info_paths = list(expected_bubble_path.glob("*.dist-info"))
                    if dist_info_paths:
                        try:
                            from importlib.metadata import PathDistribution

                            dist = PathDistribution(dist_info_paths[0])
                            if dist.version == version and canonicalize_name(
                                dist.metadata.get("Name", "")
                            ) == canonicalize_name(name):
                                all_found_dists[dist._path.resolve()] = dist
                                if verbose:
                                    safe_print(
                                        f"      ✅ Found via FAST-PATH 1 (bubble): {dist._path}"
                                    )
                                found_for_spec = True
                        except Exception:
                            pass

                if found_for_spec:
                    continue

                # FAST-PATH 2: Check main site-packages
                name_variants = self._get_package_name_variants(name)
                for variant in name_variants:
                    dist_info_path = main_site_packages / f"{variant}-{version}.dist-info"
                    if dist_info_path.is_dir():
                        try:
                            dist = importlib.metadata.Distribution.at(dist_info_path)
                            if dist.version == version and canonicalize_name(
                                dist.metadata.get("Name", "")
                            ) == canonicalize_name(name):
                                all_found_dists[dist._path.resolve()] = dist
                                if verbose:
                                    safe_print(
                                        f"      ✅ Found via FAST-PATH 2 (site-packages): {dist._path}"
                                    )
                                found_for_spec = True
                                break
                        except Exception:
                            pass

                if found_for_spec:
                    continue

                # FALLBACK: If fast-paths fail, run the comprehensive strategies for this spec only
                if verbose:
                    safe_print(
                        f"      -> Fast-path failed for '{spec}'. Using comprehensive fallback scan..."
                    )

                for base_path in search_paths:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                        future_s1 = executor.submit(
                            self._run_strategy_1,
                            base_path,
                            name_variants,
                            version,
                            verbose,
                        )
                        future_s2 = executor.submit(
                            self._run_strategy_2,
                            base_path,
                            name,
                            name_variants,
                            version,
                            verbose,
                        )
                        future_s3 = executor.submit(
                            self._run_strategy_3,
                            base_path,
                            name_variants,
                            version,
                            verbose,
                        )
                        future_s4 = executor.submit(
                            self._run_strategy_4,
                            base_path,
                            name_variants,
                            version,
                            verbose,
                        )
                        future_s5 = executor.submit(
                            self._run_strategy_5, name, version, verbose, search_paths
                        )
                        future_s6 = executor.submit(
                            self._run_strategy_6,
                            base_path,
                            name_variants,
                            version,
                            verbose,
                        )

                        futures = [
                            future_s1,
                            future_s2,
                            future_s3,
                            future_s4,
                            future_s5,
                            future_s6,
                        ]
                        for future in concurrent.futures.as_completed(futures):
                            try:
                                for dist, resolved_path in future.result():
                                    all_found_dists[resolved_path] = dist
                            except Exception:
                                pass

            final_list = list(all_found_dists.values())
            safe_print(
                f"   -> Found {len(final_list)} unique instance(s) across {len(targeted_packages)} target(s)."
            )
            return final_list

        # --- FULL DISCOVERY MODE (remains unchanged as it must be comprehensive) ---
        else:
            if verbose:
                safe_print("🔍 Running AUTHORITATIVE full discovery scan (no context bleed)...")

            # [ The existing code for the full discovery 'else' block remains exactly the same ]
            # Phase 1: Rapidly locate all potential package metadata files
            safe_print("   - Phase 1: Rapidly locating all potential package metadata files...")
            all_dist_info_paths = []

            for path in search_paths:
                if verbose:
                    safe_print(f"      -> Authoritative scan of: {path}")
                try:
                    for dist_info_path in path.rglob("*.dist-info"):
                        try:
                            if (
                                dist_info_path.name.startswith("~")
                                or not dist_info_path.exists()
                                or not dist_info_path.is_dir()
                            ):
                                continue
                            all_dist_info_paths.append(dist_info_path)
                        except (OSError, FileNotFoundError, PermissionError):
                            continue
                except (OSError, FileNotFoundError, PermissionError) as e:
                    safe_print(f"   - ⚠️  Could not scan {path}: {e}")
                    continue

            safe_print(
                f"   - Phase 2: Parsing {len(all_dist_info_paths)} metadata files in parallel..."
            )

            discovered_dists = []
            max_workers = min(32, (os.cpu_count() or 4) + 4)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                valid_paths = [p for p in all_dist_info_paths if p.is_dir()]
                future_to_path = {
                    executor.submit(self._parse_distribution_worker, path): path
                    for path in valid_paths
                }

                iterator = concurrent.futures.as_completed(future_to_path)
                if HAS_TQDM:
                    iterator = tqdm(
                        iterator,
                        total=len(future_to_path),
                        desc="      Parsing",
                        unit="pkg",
                    )

                for future in iterator:
                    try:
                        result = future.result()
                        if result:
                            discovered_dists.append(result)
                    except Exception:
                        continue

            unique_dists_by_path = {
                os.path.realpath(str(dist._path)): dist for dist in discovered_dists
            }
            final_dists = list(unique_dists_by_path.values())

            if verbose:
                safe_print(
                    f"✅ Authoritative discovery complete. Found {len(final_dists)} total package versions."
                )

            return final_dists

    # Add this optimized method to omnipkgMetadataGatherer class

    def _discover_distributions_fast(
        self,
        targeted_packages: List[str],
        known_bubble_paths: Optional[Dict[str, Path]] = None,
        verbose: bool = False,
    ) -> List[importlib.metadata.Distribution]:
        """
        ULTRA-FAST targeted discovery for known packages.
        Only searches expected locations instead of entire filesystem.

        Args:
            targeted_packages: List of "pkg==version" specs
            known_bubble_paths: Optional dict of {pkg_name: bubble_path} to skip searching
            verbose: Enable debug output

        Returns:
            List of Distribution objects for the targeted packages only
        """
        if verbose:
            safe_print(f"🚀 Fast targeted discovery for {len(targeted_packages)} package(s)")

        main_site_packages = Path(self.config.get("site_packages_path")).resolve()
        multiversion_base = Path(self.config.get("multiversion_base")).resolve()

        found_dists = []

        for pkg_spec in targeted_packages:
            pkg_name, version = self._parse_package_spec(pkg_spec)

            if not version:
                if verbose:
                    safe_print(f"   ⚠️  Skipping '{pkg_spec}' - no version specified")
                continue

            canonical_name = canonicalize_name(pkg_name)

            # PRIORITY 1: Check if we already know the bubble location
            if known_bubble_paths and canonical_name in known_bubble_paths:
                bubble_path = known_bubble_paths[canonical_name]
                if verbose:
                    safe_print(f"   📍 Using known location for {pkg_spec}: {bubble_path}")

                dist_info = list(bubble_path.glob("*.dist-info"))
                if dist_info:
                    try:
                        from importlib.metadata import PathDistribution

                        dist = PathDistribution(dist_info[0])
                        found_dists.append(dist)
                        continue
                    except Exception:
                        pass

            # PRIORITY 2: Check expected bubble location
            expected_bubble = multiversion_base / f"{pkg_name}-{version}"
            if expected_bubble.exists():
                if verbose:
                    safe_print(f"   🎯 Found expected bubble: {expected_bubble}")

                dist_infos = list(expected_bubble.glob("*.dist-info"))
                if dist_infos:
                    try:
                        from importlib.metadata import PathDistribution

                        dist = PathDistribution(dist_infos[0])
                        found_dists.append(dist)
                        continue
                    except Exception:
                        pass

            # PRIORITY 3: Check main site-packages (for active installs)
            name_variants = self._get_package_name_variants(pkg_name)
            for variant in name_variants:
                pattern = f"{variant}-{version}.dist-info"
                matches = list(main_site_packages.glob(pattern))

                if matches:
                    if verbose:
                        safe_print(f"   ✅ Found in main env: {matches[0]}")
                    try:
                        dist = importlib.metadata.Distribution.at(matches[0])
                        found_dists.append(dist)
                        break
                    except Exception:
                        continue

            # PRIORITY 4: Only if not found, do limited recursive search in multiversion_base
            # (for nested packages inside bubbles)
            if not any(
                d.metadata.get("Name") == pkg_name and d.version == version for d in found_dists
            ):
                if verbose:
                    safe_print(f"   🔍 Searching nested locations for {pkg_spec}...")

                for variant in name_variants:
                    pattern = f"*/{variant}-{version}.dist-info"
                    matches = list(multiversion_base.glob(pattern))

                    if matches:
                        if verbose:
                            safe_print(f"   ✅ Found nested: {matches[0]}")
                        try:
                            from importlib.metadata import PathDistribution

                            dist = PathDistribution(matches[0])
                            found_dists.append(dist)
                            break
                        except Exception:
                            continue

        if verbose:
            safe_print(
                f"   ✅ Fast discovery complete: {len(found_dists)}/{len(targeted_packages)} packages found"
            )

        return found_dists

    def _parse_package_spec(self, spec: str) -> Tuple[str, Optional[str]]:
        """Helper to parse 'pkg==version' into (name, version)"""
        if "==" in spec:
            parts = spec.split("==", 1)
            return parts[0].strip(), parts[1].strip()
        return spec.strip(), None

    def _is_bubbled(self, dist: importlib.metadata.Distribution) -> bool:
        multiversion_base = self.config.get("multiversion_base", "/dev/null")
        return str(dist._path).startswith(multiversion_base)

    def discover_all_packages(self) -> List[Tuple[str, str]]:
        """
        Authoritatively discovers all active and bubbled packages from the file system,
        and cleans up any "ghost" entries from the Redis index that no longer exist.
        """
        safe_print(_("🔍 Discovering all packages from file system (ground truth)..."))
        from packaging.utils import canonicalize_name

        found_on_disk = {}
        active_packages = {}
        try:
            for dist in importlib.metadata.distributions():
                pkg_name = canonicalize_name(dist.metadata.get("Name", ""))
                if not pkg_name:
                    continue
                if pkg_name not in found_on_disk:
                    found_on_disk[pkg_name] = set()
                found_on_disk[pkg_name].add(dist.version)
                active_packages[pkg_name] = dist.version
        except Exception as e:
            safe_print(_("⚠️ Error discovering active packages: {}").format(e))
        multiversion_base_path = Path(self.config["multiversion_base"])
        if multiversion_base_path.is_dir():
            for bubble_dir in multiversion_base_path.iterdir():
                dist_info = next(bubble_dir.glob("*.dist-info"), None)
                if dist_info:
                    try:
                        from importlib.metadata import PathDistribution

                        dist = PathDistribution(dist_info)
                        pkg_name = canonicalize_name(dist.metadata.get("Name", ""))
                        if not pkg_name:
                            continue
                        if pkg_name not in found_on_disk:
                            found_on_disk[pkg_name] = set()
                        found_on_disk[pkg_name].add(dist.version)
                    except Exception:
                        continue
        safe_print(_("    -> Reconciling file system state with Redis knowledge base..."))
        self._store_active_versions(active_packages)
        result_list = []
        for pkg_name, versions_set in found_on_disk.items():
            for version_str in versions_set:
                result_list.append((pkg_name, version_str))
        safe_print(
            _(
                "✅ Discovery complete. Found {} unique packages with {} total versions to process."
            ).format(len(found_on_disk), len(result_list))
        )
        return sorted(result_list, key=lambda x: x[0])

    def _register_bubble_path(self, pkg_name: str, version: str, bubble_path: Path):
        """Register bubble paths in Redis for dedup across bubbles and main env."""
        redis_key = f"{self.redis_key_prefix}bubble:{pkg_name}:{version}:path"
        self.cache_client.set(redis_key, str(bubble_path))
        self.package_path_registry[pkg_name] = self.package_path_registry.get(pkg_name, {})
        self.package_path_registry[pkg_name][version] = str(bubble_path)

    def _store_active_versions(self, active_packages: Dict[str, str]):
        if not self.cache_client:
            return
        prefix = self.redis_key_prefix
        for pkg_name, version in active_packages.items():
            main_key = f"{prefix}{pkg_name}"
            try:
                self.cache_client.hset(main_key, "active_version", version)
            except Exception as e:
                safe_print(_("⚠️ Failed to store active version for {}: {}").format(pkg_name, e))

    def _get_cached_safety_decision(self):
        cache_file = self.omnipkg_instance.multiversion_base / ".safety_upgrade_session"
        if cache_file.exists():
            try:
                decision = cache_file.read_text().strip()
                return decision == "yes"
            except:
                pass
        return None

    def _cache_safety_decision(self, decision: bool):
        """Cache decision to disk for this session"""
        cache_file = self.omnipkg_instance.multiversion_base / ".safety_upgrade_session"
        cache_file.write_text("yes" if decision else "no")

    def _should_upgrade_safety(self, current_version: str, latest_version: str) -> bool:
        # Check cached decision first
        cached = self._get_cached_safety_decision()
        if cached is not None:
            return cached

        # Check if running in CI/CD (non-interactive)
        ci_vars = [
            "CI",
            "CONTINUOUS_INTEGRATION",
            "GITHUB_ACTIONS",
            "GITLAB_CI",
            "CIRCLECI",
            "TRAVIS",
            "JENKINS_HOME",
        ]
        is_ci = any(os.environ.get(var) for var in ci_vars) or not sys.stdin.isatty()

        if is_ci:
            # CI/CD: Don't upgrade, just warn
            safe_print(
                f"    ⚠️  Safety tool outdated: v{current_version} (latest: v{latest_version})"
            )
            safe_print("    💡 Non-interactive mode detected - skipping auto-upgrade")
            safe_print("    📝 Manual upgrade: `8pkg upgrade safety")
            self._cache_safety_decision(False)
            return False

        # Interactive mode: Ask user once
        safe_print("")
        safe_print("=" * 60)
        safe_print("🔒 Security Tool Update Available")
        safe_print("=" * 60)
        safe_print(f"    Current: safety v{current_version}")
        safe_print(f"    Latest:  safety v{latest_version}")
        safe_print("")
        safe_print("    Safety scans for vulnerabilities in your packages.")
        safe_print("    Newer versions include updated vulnerability databases.")
        safe_print("")

        try:
            response = input("    Auto-upgrade safety for this session? [Y/n]: ").strip().lower()
            if response in ["", "y", "yes"]:
                safe_print("    ✅ Will auto-upgrade safety tool when needed")
                self._cache_safety_decision(True)
                return True
            else:
                safe_print(f"    ⏭️  Continuing with v{current_version} (newer available)")
                safe_print("    💡 To upgrade later: `8pkg upgrade safety`")
                self._cache_safety_decision(False)
                return False
        except (EOFError, KeyboardInterrupt):
            safe_print("")
            safe_print(f"    ⏭️  Continuing with v{current_version}")
            self._cache_safety_decision(False)
            return False

    def _perform_security_scan(self, all_packages_in_context: Dict[str, Set[str]]):
        """
        (V5 - BOOTSTRAP AWARE) Runs a security check on ALL packages.
        Detects if it's running during initial KB build and uses a safe fallback
        to prevent recursion and initialization deadlocks.
        """
        # --- FIX 1: DETECT BOOTSTRAP/INITIALIZATION STATE ---
        # If the 'bubble_manager' attribute (which holds the BubbleIsolationManager instance)
        # doesn't exist yet on the main omnipkg object, it means we are in the middle of the
        # very first KB build. We MUST NOT try to create a bubble here.
        if not hasattr(self.omnipkg_instance, "bubble_manager") or self.omnipkg_instance.bubble_manager is None:
            safe_print("🛡️  Initial KB build detected. Security scan will use a safe fallback.")
            safe_print("   - Using 'pip audit' to prevent initialization deadlock.")
            self._run_pip_audit_fallback(
                {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
            )
            return

        effective_version_str = self.target_context_version or get_python_version()

        # Check Python 3.14+ incompatibility
        is_incompatible_with_safety = False
        try:
            major, minor = map(int, effective_version_str.split(".")[:2])
            if (major, minor) >= (3, 14):
                is_incompatible_with_safety = True
        except (ValueError, TypeError):
            pass

        if is_incompatible_with_safety:
            safe_print(
                f"🛡️  'safety' is incompatible with Python {effective_version_str}. Using 'pip audit' as a fallback."
            )
            self._run_pip_audit_fallback(
                {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
            )
            return

        if not SAFETY_AVAILABLE:
            safe_print("⚠️  'safety' package not found. Attempting 'pip audit' fallback...")
            self._run_pip_audit_fallback(
                {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
            )
            return

        if not all_packages_in_context:
            safe_print(_(" - No packages found to scan."))
            self.security_report = {}
            return

        safe_print(
            f"🛡️  Performing security scan for {len(all_packages_in_context)} package(s) using isolated tool..."
        )

        try:
            TOOL_NAME = "safety"
            latest_compatible = self.omnipkg_instance._get_latest_version_from_pypi(
                TOOL_NAME, python_context_version=self.target_context_version
            )

            if not latest_compatible:
                safe_print(
                    f"⚠️  No compatible safety version found for Python {effective_version_str}"
                )
                self._run_pip_audit_fallback(
                    {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
                )
                return

            safe_print(
                f"   💾 Latest compatible version for Python {effective_version_str}: {latest_compatible}"
            )

            current_version = None
            for bubble in self.omnipkg_instance.multiversion_base.glob(f"{TOOL_NAME}-*"):
                current_version = bubble.name.split("-", 1)[1]
                safe_print(f"   -> Found existing 'safety' tool bubble: v{current_version}")
                break

            should_create_or_upgrade = False
            if not current_version:
                safe_print(f"   💡 No existing safety bubble for Python {effective_version_str}")
                should_create_or_upgrade = True
                tool_version_to_use = latest_compatible
            elif parse_version(current_version) < parse_version(latest_compatible):
                should_upgrade = self._should_upgrade_safety(current_version, latest_compatible)
                if should_upgrade:
                    should_create_or_upgrade = True
                    tool_version_to_use = latest_compatible
                else:
                    tool_version_to_use = current_version
            else:
                tool_version_to_use = current_version

            TOOL_SPEC = f"{TOOL_NAME}=={tool_version_to_use}"
            bubble_path = (
                self.omnipkg_instance.multiversion_base / f"{TOOL_NAME}-{tool_version_to_use}"
            )

            if should_create_or_upgrade or not bubble_path.is_dir():
                if should_create_or_upgrade and current_version:
                    safe_print(
                        f"📦 Upgrading safety tool: v{current_version} → v{tool_version_to_use}",
                        flush=True  # ADD THIS
                    )
                else:
                    safe_print(
                        f"💡 First-time setup: Creating isolated bubble for '{TOOL_SPEC}'...",
                        flush=True  # ADD THIS
                    )

                for old_bubble in self.omnipkg_instance.multiversion_base.glob(f"{TOOL_NAME}-*"):
                    if old_bubble.name != bubble_path.name:
                        safe_print(f"   -> Removing old tool bubble: {old_bubble.name}")
                        import shutil

                        shutil.rmtree(old_bubble)

                success = self.omnipkg_instance.bubble_manager.create_isolated_bubble(
                    TOOL_NAME,
                    tool_version_to_use,
                    python_context_version=self.target_context_version,
                )
                if not success:
                    safe_print("❌ Failed to create tool bubble. Using pip-audit fallback.")
                    self._run_pip_audit_fallback(
                        {
                            name: list(versions)[0]
                            for name, versions in all_packages_in_context.items()
                        }
                    )
                    return

                # --- FIX 2: REMOVE RECURSIVE KB REBUILD ---
                # The main KB build process that CALLED this function will
                # automatically discover the new safety bubble. Calling it again here
                # causes an infinite loop.
                # self.omnipkg_instance.rebuild_package_kb(...) # <-- DELETED

            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as reqs_file:
                reqs_file_path = reqs_file.name
                for name, versions in all_packages_in_context.items():
                    for version in versions:
                        reqs_file.write(f"{name}=={version}\n")

            safe_print(_("🌀 Force-activating '{}' context to run scan...").format(TOOL_SPEC))
            with omnipkgLoader(
                TOOL_SPEC,
                config=self.omnipkg_instance.config,
                force_activation=True,
                quiet=True,
                isolation_mode="strict",
            ):
                python_exe = self.config.get("python_executable", sys.executable)
                cmd = [
                    python_exe,
                    "-m",
                    "safety",
                    "check",
                    "-r",
                    reqs_file_path,
                    "--json",
                ]
                safe_print("   🔍 Running safety vulnerability scan...", flush=True)
                result = subprocess.run(
                    cmd, 
                    capture_output=True,  # Keep this for JSON parsing
                    text=True, 
                    timeout=180
                )
                safe_print("   ✓ Scan complete", flush=True)

            self.security_report = {}
            if result.stdout:
                try:
                    json_match = re.search("(\\[.*\\]|\\{.*\\})", result.stdout, re.DOTALL)
                    if json_match:
                        self.security_report = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    safe_print(_(" ⚠️ Could not parse safety JSON output."))

            if result.stderr and "error" in result.stderr.lower():
                safe_print(_(" ⚠️ Safety tool produced errors:"))
                safe_print(f"    STDERR: {result.stderr}")
                if result.stdout:
                    safe_print(f"    STDOUT: {result.stdout}")
                safe_print(_("    → Trying pip-audit fallback..."))
                self._run_pip_audit_fallback(
                    {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
                )
                return

        except Exception as e:
            safe_print(
                _(
                    " ⚠️ An error occurred during isolated security scan. Trying pip-audit fallback: {}"
                ).format(e)
            )
            self._run_pip_audit_fallback(
                {name: list(versions)[0] for name, versions in all_packages_in_context.items()}
            )
            return
        finally:
            if "reqs_file_path" in locals() and os.path.exists(reqs_file_path):
                os.unlink(reqs_file_path)

        issue_count = 0
        if isinstance(self.security_report, list):
            issue_count = len(self.security_report)
        elif isinstance(self.security_report, dict) and "vulnerabilities" in self.security_report:
            issue_count = len(self.security_report["vulnerabilities"])
        safe_print(_("✅ Security scan complete. Found {} potential issues.").format(issue_count))

    def _run_pip_audit_fallback(self, packages: Dict[str, str]):
        """Runs `pip audit` as a fallback security scanner."""
        if not packages:
            safe_print(_(" - No active packages found to scan."))
            self.security_report = {}
            return

        reqs_file_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as reqs_file:
                reqs_file_path = reqs_file.name
                for name, version in packages.items():
                    reqs_file.write(f"{name}=={version}\n")

            python_exe = self.config.get("python_executable", sys.executable)
            cmd = [python_exe, "-m", "pip", "audit", "--json", "-r", reqs_file_path]
            
            # ADD THIS:
            safe_print("   🔍 Running pip audit scan...", flush=True)
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            
            # ADD THIS:
            safe_print("   ✓ Audit complete", flush=True)

            if result.returncode == 0 and result.stdout:
                audit_data = json.loads(result.stdout)
                self.security_report = self._parse_pip_audit_output(audit_data)
            else:
                self.security_report = []  # No issues found or error occurred

            issue_count = len(self.security_report)
            safe_print(
                _("✅ Security scan complete (via pip audit). Found {} potential issues.").format(
                    issue_count
                )
            )

        except (
            json.JSONDecodeError,
            subprocess.SubprocessError,
            FileNotFoundError,
        ) as e:
            safe_print(_(" ⚠️ An error occurred during the pip audit fallback scan: {}").format(e))
            self.security_report = {}
        finally:
            if reqs_file_path and os.path.exists(reqs_file_path):
                os.unlink(reqs_file_path)

    def _parse_pip_audit_output(self, audit_data: List[Dict]) -> List[Dict]:
        """
        Parses the JSON output from `pip audit` and transforms it into the same
        format used by the `safety` tool for consistency.
        """
        report = []
        for item in audit_data:
            package_name = item.get("name")
            installed_version = item.get("version")
            for vuln in item.get("vulns", []):
                report.append(
                    {
                        "package_name": package_name,
                        "vulnerable_spec": f"<{','.join(vuln.get('fixed_in', []))}",
                        "analyzed_version": installed_version,
                        "advisory": vuln.get("summary", "N/A"),
                        "vulnerability_id": vuln.get("id", "N/A"),
                        "fixed_in": vuln.get("fixed_in", []),
                    }
                )
        return report

    def _emergency_heal_metadata(self, dist_info_path: Path) -> bool:
        """
        Emergency on-the-spot metadata healing when corruption is detected during KB scan.
        Returns True if healing succeeded, False otherwise.
        """
        try:
            metadata_file = dist_info_path / "METADATA"

            if not metadata_file.exists():
                safe_print("      -> No METADATA file found")
                return False

            # Read current content
            content = metadata_file.read_text(encoding="utf-8", errors="ignore")

            # Check if Name is actually missing
            if "Name:" in content[:500]:
                safe_print("      -> Name field exists, corruption is elsewhere")
                return False

            # Extract package name from folder
            folder_name = dist_info_path.name
            if folder_name.endswith(".dist-info"):
                folder_name = folder_name[:-10]

            # Parse name-version format
            parts = folder_name.rsplit("-", 1)
            if len(parts) == 2:
                pkg_name, pkg_version = parts
            else:
                safe_print(f"      -> Could not parse package name from: {folder_name}")
                return False

            # Create fixed content
            fixed_content = f"Name: {pkg_name}\n{content}"

            # Atomic write with backup
            backup_file = metadata_file.with_suffix(".backup")
            temp_file = metadata_file.with_suffix(".tmp")

            try:
                # Backup original
                import shutil

                shutil.copy2(metadata_file, backup_file)

                # Write fixed version to temp
                temp_file.write_text(fixed_content, encoding="utf-8")

                # Atomic replace
                temp_file.replace(metadata_file)

                safe_print(f"      -> Injected 'Name: {pkg_name}' into METADATA")

                # Clean up backup after success
                if backup_file.exists():
                    backup_file.unlink()

                return True

            except Exception as e:
                safe_print(f"      -> Write failed: {e}")
                # Restore from backup if it exists
                if backup_file.exists():
                    shutil.copy2(backup_file, metadata_file)
                return False

        except Exception as e:
            safe_print(f"      -> Emergency healing failed: {e}")
            return False

    def run(
        self,
        targeted_packages: Optional[List[str]] = None,
        search_path_override: Optional[str] = None,
        skip_existing_checksums: bool = False,
        pre_discovered_distributions: Optional[List[importlib.metadata.Distribution]] = None,
    ):
        """
        (V5.4 - ON-THE-SPOT HEALING) The main execution loop with immediate corruption repair.
        """
        if not self.cache_client:
            safe_print(_("❌ Cache client not available to the builder. Aborting."))
            return

        if pre_discovered_distributions is not None:
            safe_print("   -> Using pre-discovered distributions for surgical KB update...")
            all_discovered_dists = pre_discovered_distributions
        else:
            all_discovered_dists = self._discover_distributions(
                targeted_packages,
                search_path_override=search_path_override,
                skip_existing_checksums=skip_existing_checksums,
            )

        distributions_to_process = []
        safe_print(
            f"   -> Filtering {len(all_discovered_dists)} discovered packages for current Python {self.target_context_version} context..."
        )

        for dist in all_discovered_dists:
            context_info = self._get_install_context(dist)
            install_type = context_info["install_type"]

            if install_type in ["active", "vendored", "unknown"]:
                distributions_to_process.append(dist)
                continue

            if install_type in ["bubble", "nested"]:
                is_compatible = False
                multiversion_base_path = Path(self.config.get("multiversion_base", "/dev/null"))

                try:
                    relative_to_base = dist._path.relative_to(multiversion_base_path)
                    bubble_root_name = relative_to_base.parts[0]
                    bubble_root_path = multiversion_base_path / bubble_root_name
                    manifest_file = bubble_root_path / ".omnipkg_manifest.json"

                    if manifest_file.exists():
                        try:
                            with open(manifest_file, "r") as f:
                                manifest = json.load(f)
                            bubble_py_ver = manifest.get("python_version")
                            if bubble_py_ver == self.target_context_version:
                                is_compatible = True
                        except Exception:
                            is_compatible = True
                    else:
                        is_compatible = True
                except ValueError:
                    is_compatible = True

                if is_compatible:
                    distributions_to_process.append(dist)

        safe_print(
            f"   -> Found {len(distributions_to_process)} packages belonging to this context."
        )

        if not distributions_to_process:
            safe_print(_("✅ No packages found for the current context to process."))
            return []

        # --- MODIFIED: HEAL INSTEAD OF SKIP ---
        valid_distributions = []
        healed_count = 0

        for dist in distributions_to_process:
            if dist.metadata.get("Name"):
                valid_distributions.append(dist)
            else:
                # ATTEMPT ON-THE-SPOT HEALING
                safe_print(f"🔧 Detected corrupt metadata at: {dist._path}")
                safe_print("   -> Attempting emergency repair...")

                if self._emergency_heal_metadata(dist._path):
                    # Reload the distribution after healing
                    try:
                        from importlib.metadata import PathDistribution

                        healed_dist = PathDistribution(dist._path)
                        if healed_dist.metadata.get("Name"):
                            valid_distributions.append(healed_dist)
                            healed_count += 1
                            safe_print("   ✅ Successfully healed and reloaded distribution")
                        else:
                            safe_print("   ⚠️  Healing failed - still no Name field")
                    except Exception as e:
                        safe_print(f"   ⚠️  Could not reload after healing: {e}")
                else:
                    safe_print("   ⚠️  Emergency healing failed, skipping this distribution")

        if healed_count > 0:
            safe_print(f"🎉 Emergency healed {healed_count} corrupt distribution(s)")

        distributions_to_process = valid_distributions
        # ----------------------------------------------------------
        # NOTE: Removed orphaned dict comprehension that was causing hangs

        all_packages_to_scan = {}
        for dist in distributions_to_process:
            # Now safe because we filtered None names above
            c_name = canonicalize_name(dist.metadata["Name"])
            if c_name not in all_packages_to_scan:
                all_packages_to_scan[c_name] = set()
            all_packages_to_scan[c_name].add(dist.version)

        self._perform_security_scan(all_packages_to_scan)

        import time

        start_time = time.perf_counter()

        updated_count = 0
        max_workers = (os.cpu_count() or 4) * 2
        total_packages = len(distributions_to_process)
        safe_print(f"   🔄 Processing {total_packages} packages in parallel...", flush=True)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="omnipkg_builder"
        ) as executor:
            future_to_dist = {
                executor.submit(self._process_package, dist): dist
                for dist in distributions_to_process
            }
            iterator = concurrent.futures.as_completed(future_to_dist)
            if HAS_TQDM:
                iterator = tqdm(
                    iterator,
                    total=len(distributions_to_process),
                    desc="Processing packages",
                    unit="pkg",
                )

            for future in iterator:
                try:
                    if future.result():
                        updated_count += 1
                except Exception as exc:
                    dist = future_to_dist[future]
                    safe_print(f'\n❌ Error processing {dist.metadata["Name"]}: {exc}')

        end_time = time.perf_counter()
        total_time = end_time - start_time
        pkgs_per_sec = total_packages / total_time if total_time > 0 else float("inf")

        safe_print("\n" + "─" * 60)
        safe_print("🚀 KNOWLEDGE BASE BUILD - PERFORMANCE SUMMARY 🚀")
        safe_print(f"   - ⏱️  Total Time: {total_time:.2f}s for {total_packages} packages")
        safe_print(f"   - 🔥 Average Throughput: {pkgs_per_sec:.2f} pkg/s")
        safe_print("─" * 60)

        safe_print(
            _("🎉 Metadata building complete! Updated {} package(s) for this context.").format(
                updated_count
            )
        )
        return distributions_to_process

    def _get_install_context(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        (V4 - CANONICALIZATION FIX) Determines the precise installation context.
        Now properly handles the fact that bubble directory names use underscores
        while canonicalized names use hyphens.
        """
        dist_path = dist._path
        path_str = str(dist_path)
        multiversion_base = Path(self.config.get("multiversion_base", "/dev/null"))
        site_packages = Path(self.config.get("site_packages_path", "/dev/null"))

        # Vendored check (remains the same)
        if "_vendor/" in path_str or ".vendor/" in path_str:
            try:
                parent_path = dist_path
                while (
                    parent_path != site_packages
                    and parent_path != multiversion_base
                    and parent_path.parent != parent_path
                ):
                    parent_dist_info = next(parent_path.glob("*.dist-info"), None)
                    if parent_dist_info and ("_vendor" not in str(parent_dist_info)):
                        parent_dist = importlib.metadata.Distribution.at(parent_dist_info)
                        return {
                            "install_type": "vendored",
                            "owner_package": canonicalize_name(parent_dist.metadata["Name"]),
                        }
                    parent_path = parent_path.parent
            except Exception:
                pass
            return {"install_type": "vendored", "owner_package": "Unknown"}

        # Check if in multiversion_base
        try:
            relative_path = dist_path.relative_to(multiversion_base)
            bubble_dir_name = relative_path.parts[0]

            # Get the package's own name and version
            pkg_name_raw = dist.metadata["Name"]  # Keep the original name with underscores
            version = dist.version

            # Build the expected bubble name using the RAW package name (not canonicalized)
            expected_bubble_name = f"{pkg_name_raw}-{version}"

            # Compare directly
            if bubble_dir_name == expected_bubble_name:
                return {"install_type": "bubble", "owner_package": None}
            else:
                return {"install_type": "nested", "owner_package": bubble_dir_name}

        except ValueError:
            # Not in the multiversion_base
            pass

        # Active check
        try:
            dist_path.relative_to(site_packages)
            return {"install_type": "active", "owner_package": None}
        except ValueError:
            pass

        return {"install_type": "unknown", "owner_package": None}

    def _process_package(self, dist: importlib.metadata.Distribution) -> bool:
        """
        (V3.1 - Vendored Fix) Processes a single distribution, now correctly
        including vendored packages instead of skipping them.
        """
        try:
            raw_name = dist.metadata.get("Name")
            if not raw_name or not isinstance(raw_name, str):
                return False  # Silently skip corrupted metadata

            # --- FIX: REMOVED THE LOGIC THAT SKIPPED VENDORED PACKAGES ---
            # All discovered and filtered packages should be processed.
            context_info = self._get_install_context(dist)

            self._build_comprehensive_metadata(dist)
            is_active = context_info["install_type"] == "active"

            return self._store_in_redis(dist, is_active=is_active, context_info=context_info)

        except Exception as e:
            safe_print(f"\n❌ Error processing {dist._path}: {e}")
            return False

    def _build_comprehensive_metadata(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        FIXED: Builds metadata exclusively from the provided Distribution object
        and now includes the physical path of the package.
        """
        package_name = canonicalize_name(dist.metadata["Name"])
        metadata = {k: v for k, v in dist.metadata.items()}

        # FIX: Always use dist._path for consistency with hash computation
        metadata["path"] = str(Path(dist._path).resolve())

        metadata["last_indexed"] = datetime.now().isoformat()
        context_version = (
            self.target_context_version if self.target_context_version else get_python_version()
        )
        metadata["indexed_by_python"] = context_version

        # ✅ FIX: Canonicalize package names in dependencies
        if dist.requires:
            canonicalized_deps = []
            for req in dist.requires:
                req_str = str(req)
                # Extract package name (before any version specifiers or extras)
                # Examples: "PyYAML>=6.0" -> "pyyaml", "requests[security]>=2.0" -> "requests"
                pkg_name = (
                    req_str.split(">=")[0]
                    .split("==")[0]
                    .split("<")[0]
                    .split(">")[0]
                    .split("[")[0]
                    .split(";")[0]
                    .strip()
                )
                canonical_name = canonicalize_name(pkg_name)

                # Reconstruct the requirement with canonical name
                # If there are version specifiers, preserve them
                remainder = req_str[len(pkg_name) :].strip()
                if remainder:
                    canonicalized_deps.append(f"{canonical_name}{remainder}")
                else:
                    canonicalized_deps.append(canonical_name)
            metadata["dependencies"] = canonicalized_deps
        else:
            metadata["dependencies"] = []

        try:
            console_scripts = [
                {
                    "name": ep.name,
                    # This handles both modern EntryPoint objects and older formats
                    "module": (ep.module if hasattr(ep, "module") else ep.value.split(":")[0]),
                    "attr": (
                        ep.attr
                        if hasattr(ep, "attr")
                        else ep.value.split(":")[1] if ":" in ep.value else None
                    ),
                }
                for ep in dist.entry_points
                if ep.group == "console_scripts"
            ]
            metadata["entry_points"] = console_scripts
        except Exception:
            # If parsing fails for any reason, we robustly fall back to an empty list.
            metadata["entry_points"] = []

        package_files = self._find_package_files(dist)
        if package_files.get("binaries"):
            metadata["help_text"] = self._get_help_output(package_files["binaries"][0]).get(
                "help_text", "No executable binary found."
            )
        else:
            metadata["help_text"] = "No executable binary found."
        metadata["cli_analysis"] = self._analyze_cli(metadata.get("help_text", ""))
        metadata["security"] = self._get_security_info(package_name)
        metadata["health"] = self._perform_health_checks(dist, package_files)
        checksum = self._generate_checksum(metadata)
        metadata["checksum"] = checksum
        return metadata

    def _find_distribution_at_path(
        self, package_name: str, version: str, search_path: Path
    ) -> Optional[importlib.metadata.Distribution]:
        normalized_name_dash = canonicalize_name(package_name)
        normalized_name_under = normalized_name_dash.replace("-", "_")
        for name_variant in {normalized_name_dash, normalized_name_under}:
            for dist_info in search_path.glob(f"{name_variant}-{version}*.dist-info"):
                if dist_info.is_dir():
                    try:
                        from importlib.metadata import PathDistribution

                        dist = PathDistribution(dist_info)
                        metadata_name = dist.metadata.get("Name", "")
                        if (
                            canonicalize_name(metadata_name) == normalized_name_dash
                            and dist.metadata.get("Version") == version
                        ):
                            return dist
                    except Exception:
                        continue
        return None

    def _get_instance_key(self, dist: importlib.metadata.Distribution) -> str:
        """Generates a unique, deterministic Redis key for a specific package instance."""
        path_str = str(dist._path)
        # Use a short, stable hash of the unique path
        instance_hash = hashlib.sha256(path_str.encode()).hexdigest()[:12]

        pkg_name = canonicalize_name(dist.metadata["Name"])
        version = dist.version
        prefix = self.redis_key_prefix.replace(":pkg:", ":inst:")  # Change namespace to 'inst'

        return f"{prefix}{pkg_name}:{version}:{instance_hash}"

    def _parse_metadata_file(self, metadata_content: str) -> Dict:
        metadata = {}
        current_key = None
        current_value = []
        for line in metadata_content.splitlines():
            if ": " in line and (not line.startswith(" ")):
                if current_key:
                    metadata[current_key] = (
                        "\n".join(current_value).strip() if current_value else ""
                    )
                current_key, value = line.split(": ", 1)
                current_value = [value]
            elif line.startswith(" ") and current_key:
                current_value.append(line.strip())
        if current_key:
            metadata[current_key] = "\n".join(current_value).strip() if current_value else ""
        return metadata

    def _get_instance_hash(self, dist: importlib.metadata.Distribution) -> str:
        """
        (AUTHORITATIVE) Generates the one true, consistent instance hash for any
        distribution by using its real, canonical path.
        """
        import os

        # This is the single source of truth for a package's physical location.
        # os.path.realpath resolves symlinks and gives the canonical path.
        resolved_path_str = os.path.realpath(str(dist._path))

        # The identifier is a combination of its true path and version.
        unique_instance_identifier = f"{resolved_path_str}::{dist.version}"

        # Return the deterministic hash.
        return hashlib.sha256(unique_instance_identifier.encode()).hexdigest()[:12]

    def _store_in_redis(
        self, dist: importlib.metadata.Distribution, is_active: bool, context_info: Dict
    ):
        """
        Stores metadata using hash of resolved dist._path
        """
        try:
            metadata = self._build_comprehensive_metadata(dist)
            package_name = canonicalize_name(dist.metadata["Name"])
            version_str = dist.version

            # Compute hash from resolved path
            instance_hash = self._get_instance_hash(dist)

            # The path stored in metadata MUST match what the hash was generated from.
            # os.path.realpath is the key to consistency.
            import os

            resolved_path_str = os.path.realpath(str(dist._path))
            metadata["path"] = resolved_path_str

            instance_key = f"{self.redis_key_prefix.replace(':pkg:', ':inst:')}{package_name}:{version_str}:{instance_hash}"

            data_to_store = metadata.copy()
            data_to_store.update(context_info)
            data_to_store["installation_hash"] = instance_hash

            flattened_data = self._flatten_dict(data_to_store)

            main_key = f"{self.redis_key_prefix}{package_name}"
            index_key = f"{self.redis_env_prefix}index"

            with self.cache_client.pipeline() as pipe:
                pipe.delete(instance_key)
                pipe.hset(instance_key, mapping=flattened_data)
                pipe.sadd(f"{main_key}:installed_versions", version_str)
                pipe.sadd(f"{main_key}:{version_str}:instances", instance_hash)
                pipe.sadd(index_key, package_name)
                pipe.hset(main_key, "name", package_name)

                # vvvvvvvvv START OF NEW LOGIC vvvvvvvvv
                # Index CLI commands for fast O(1) lookup
                if "entry_points" in metadata and isinstance(metadata["entry_points"], list):
                    for ep in metadata["entry_points"]:
                        # The developer-port logic saves them as dicts: {'name': 'lollama', ...}
                        cmd_name = ep.get("name") if isinstance(ep, dict) else None
                        if cmd_name:
                            # Create the lookup key: omnipkg:env_ID:entrypoint:lollama -> lollama_pkg
                            ep_key = f"{self.redis_env_prefix}entrypoint:{cmd_name}"
                            pipe.set(ep_key, package_name)
                # ^^^^^^^^^ END OF NEW LOGIC ^^^^^^^^^

                if is_active:
                    pipe.hset(main_key, "active_version_instance_hash", instance_hash)
                    pipe.hset(main_key, "active_version", version_str)

                if context_info.get("install_type") == "bubble":
                    pipe.hset(main_key, f"bubble_version:{version_str}", "true")

                pipe.execute()
            return True

        except Exception as e:
            safe_print(f'\n❌ Error storing {dist.metadata.get("Name", "N/A")} in Redis: {e}')
            return False

    def _perform_health_checks(
        self, dist: importlib.metadata.Distribution, package_files: Dict
    ) -> Dict:
        """
        FIXED: Passes the specific distribution to the verification function.
        """
        health_data = {
            "import_check": self._verify_installation(dist),
            "binary_checks": {
                Path(bin_path).name: self._check_binary_integrity(bin_path)
                for bin_path in package_files.get("binaries", [])
            },
        }
        oversized = [
            name
            for name, check in health_data["binary_checks"].items()
            if check.get("size", 0) > 10000000
        ]
        if oversized:
            health_data["size_warnings"] = oversized
        return health_data

    def _run_import_verification_in_path(
        self, import_candidates: List[str], path_to_test: str
    ) -> Dict:
        """
        (THE ONE TRUE VERIFIER) Executes an import test for a list of candidate
        modules in a specific directory using an isolated subprocess.
        FIXED: Correctly handles invalid identifiers without crashing.
        """
        if not import_candidates:
            return {
                "importable": False,
                "error": "No import candidates found.",
                "attempted_modules": [],
            }

        # 1. Prepare the script header
        script_lines = [
            "import sys",
            "import importlib",
            "import json",
            "import traceback",
            "results = []",
            f"sys.path.insert(0, r'{path_to_test}')",
        ]

        # 2. Build the script body
        for candidate in import_candidates:
            if not candidate.isidentifier():
                # --- FIX IS HERE ---
                # We append the code to the script string, NOT to a local list variable.
                script_lines.append(
                    f"results.append(('{candidate}', False, 'Skipped: Not a valid identifier'))"
                )
                continue

            script_lines.extend(
                [
                    f"# Testing import: {candidate}",
                    "try:",
                    f"    mod = importlib.import_module('{candidate}')",
                    "    version = getattr(mod, '__version__', 'N/A')",
                    f"    results.append(('{candidate}', True, str(version)))",
                    "except Exception as e:",
                    f"    results.append(('{candidate}', False, traceback.format_exc()))",
                ]
            )

        # 3. Add the output command
        script_lines.append("print(json.dumps(results))")
        script = "\n".join(script_lines)

        try:
            # 4. Run the generated script
            python_exe = self.config.get("python_executable", sys.executable)
            result = subprocess.run(
                [python_exe, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )

            test_results = json.loads(result.stdout.strip())
            successful_imports = [(name, ver) for name, success, ver in test_results if success]
            failed_imports = [(name, err) for name, success, err in test_results if not success]

            if successful_imports:
                return {
                    "importable": True,
                    "successful_modules": [name for name, _ in successful_imports],
                    "failed_modules": (
                        [name for name, _ in failed_imports] if failed_imports else []
                    ),
                }
            else:
                return {
                    "importable": False,
                    "error": f"All import attempts failed: {dict(failed_imports)}",
                    "attempted_modules": import_candidates,
                }
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as e:
            error_msg = e.stderr.strip() if hasattr(e, "stderr") and e.stderr else str(e)
            return {
                "importable": False,
                "error": f"Subprocess failed: {error_msg}",
                "attempted_modules": import_candidates,
            }

    def _verify_installation(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        SMART VERSION: Uses the One True Verifier to check importability.
        """
        package_name = canonicalize_name(dist.metadata["Name"])
        is_bubbled = self._is_bubbled(dist)
        test_path = str(dist._path.parent) if is_bubbled else get_site_packages_path()

        # Get candidates using the robust, corrected logic from our previous fix
        import_candidates = self._get_import_candidates(dist, package_name)

        # Call the new central verifier
        verification_result = self._run_import_verification_in_path(import_candidates, test_path)

        # Process results
        if verification_result["importable"]:
            # Try to find a version number from the successful imports
            try:
                import_version = dist.version
            except Exception:
                import_version = "unknown"  # Fallback

            verification_result["version"] = import_version

        return verification_result

    def _get_import_candidates(
        self, dist: importlib.metadata.Distribution, package_name: str
    ) -> List[str]:
        """
        Get the authoritative list of import candidates for a package.
        Uses multiple strategies in order of reliability.
        """
        candidates = []
        try:
            if hasattr(dist, "read_text"):
                top_level_content = dist.read_text("top_level.txt")
                if top_level_content:
                    candidates.extend(
                        [
                            line.strip()
                            for line in top_level_content.strip().split("\n")
                            if line.strip()
                        ]
                    )
        except Exception:
            pass
        if not candidates:
            candidates.extend(self._parse_record_for_modules(dist))
        if not candidates:
            candidates.extend(self._generate_import_heuristics(package_name))
        if not candidates:
            candidates.append(package_name.replace("-", "_"))
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                unique_candidates.append(candidate)
        return unique_candidates

    def _parse_record_for_modules(self, dist: importlib.metadata.Distribution) -> List[str]:
        """
        Parse the RECORD file to identify top-level modules.
        """
        candidates = []
        try:
            if hasattr(dist, "read_text"):
                record_content = dist.read_text("RECORD")
                if record_content:

                    top_level_dirs = set()
                    for line in record_content.strip().split("\n"):
                        if line.strip():
                            file_path = line.split(",")[0]
                            parts = file_path.split("/")
                            if parts and (not parts[0].endswith(".dist-info")):
                                top_part = parts[0]
                                if "." not in top_part or top_part.endswith(".py"):
                                    module_name = top_part.replace(".py", "")
                                    if module_name and (not module_name.startswith("_")):
                                        top_level_dirs.add(module_name)
                    candidates.extend(sorted(top_level_dirs))
        except Exception:
            pass
        return candidates

    def _generate_import_heuristics(self, package_name: str) -> List[str]:
        """
        Generate smart import candidates based on package name patterns.
        """
        candidates = []
        if "." in package_name:
            candidates.append(package_name)
            candidates.append(package_name.split(".")[0])
        underscore_name = package_name.replace("-", "_")
        if underscore_name != package_name:
            candidates.append(underscore_name)
        if package_name.startswith("python-"):
            candidates.append(package_name[7:])
            candidates.append(package_name[7:].replace("-", "_"))
        if package_name.endswith("-python"):
            candidates.append(package_name[:-7])
            candidates.append(package_name[:-7].replace("-", "_"))
        common_mappings = {
            "beautifulsoup4": ["bs4"],
            "pillow": ["PIL"],
            "pyyaml": ["yaml"],
            "msgpack-python": ["msgpack"],
            "protobuf": ["google.protobuf", "google"],
            "python-dateutil": ["dateutil"],
            "setuptools-scm": ["setuptools_scm"],
        }
        canonical = canonicalize_name(package_name)
        if canonical in common_mappings:
            candidates.extend(common_mappings[canonical])
        return candidates

    def _check_binary_integrity(self, bin_path: str) -> Dict:
        if not os.path.exists(bin_path):
            return {"exists": False}
        integrity_report = {
            "exists": True,
            "size": os.path.getsize(bin_path),
            "is_elf": False,
            "valid_shebang": self._has_valid_shebang(bin_path),
        }
        try:
            with open(bin_path, "rb") as f:
                if f.read(4) == b"\x7fELF":
                    integrity_report["is_elf"] = True
        except Exception:
            pass
        return integrity_report

    def _has_valid_shebang(self, path: str) -> bool:
        try:
            with open(path, "r", errors="ignore") as f:
                return f.readline().startswith("#!")
        except Exception:
            return False

    def _find_package_files(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        FIXED: Authoritatively finds files belonging to the specific distribution.
        """
        files = {"binaries": []}
        if not dist or not dist.files:
            return files
        for file_path in dist.files:
            try:
                abs_path = dist.locate_file(file_path)
                if "bin" in file_path.parts or "Scripts" in file_path.parts:
                    if abs_path and abs_path.exists() and os.access(abs_path, os.X_OK):
                        files["binaries"].append(str(abs_path))
            except (FileNotFoundError, NotADirectoryError):
                continue
        return files

    def _run_bulk_security_check(self, packages: Dict[str, str]):
        reqs_file_path = "/tmp/bulk_safety_reqs.txt"
        try:
            with open(reqs_file_path, "w") as f:
                for name, version in packages.items():
                    f.write(f"{name}=={version}\n")
            python_exe = self.config.get("python_executable", sys.executable)
            result = subprocess.run(
                [python_exe, "-m", "safety", "check", "-r", reqs_file_path, "--json"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.stdout:
                self.security_report = json.loads(result.stdout)
        except Exception as e:
            safe_print(_("    ⚠️ Bulk security scan failed: {}").format(e))
        finally:
            if os.path.exists(reqs_file_path):
                os.remove(reqs_file_path)

    def _get_security_info(self, package_name: str) -> Dict:
        """
        FIXED: Parses the security report from `safety`, correctly handling both the
        legacy object format ({'pkg': [...]}) and the modern list format ([...]).
        """
        c_name = canonicalize_name(package_name)
        vulnerabilities = []
        if isinstance(self.security_report, dict):
            vulnerabilities = self.security_report.get(c_name, [])
        elif isinstance(self.security_report, list):
            vulnerabilities = [
                vuln
                for vuln in self.security_report
                if isinstance(vuln, dict)
                and canonicalize_name(vuln.get("package_name", "")) == c_name
            ]
        return {
            "audit_status": "checked_in_bulk",
            "issues_found": len(vulnerabilities),
            "report": vulnerabilities,
        }

    def _generate_checksum(self, metadata: Dict) -> str:
        core_data = {
            "Version": metadata.get("Version"),
            "dependencies": metadata.get("dependencies"),
            "help_text": metadata.get("help_text"),
        }
        data_string = json.dumps(core_data, sort_keys=True)
        return hashlib.sha256(data_string.encode("utf-8")).hexdigest()

    def _get_help_output(self, executable_path: str) -> Dict:
        if not os.path.exists(executable_path):
            return {"help_text": "Executable not found."}
        for flag in ["--help", "-h"]:
            try:
                result = subprocess.run(
                    [executable_path, flag],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    errors="ignore",
                )
                output = (result.stdout or result.stderr).strip()
                if output and "usage:" in output.lower():
                    return {"help_text": output[:5000]}
            except Exception:
                continue
        return {"help_text": "No valid help output captured."}

    def _analyze_cli(self, help_text: str) -> Dict:
        if not help_text or "No valid help" in help_text:
            return {}
        analysis = {"common_flags": [], "subcommands": []}
        lines = help_text.split("\n")
        command_regex = re.compile("^\\s*([a-zA-Z0-9_-]+)\\s{2,}(.*)")
        in_command_section = False
        for line in lines:
            if re.search("^(commands|available commands):", line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and (not line.strip()):
                in_command_section = False
                continue
            if in_command_section:
                match = command_regex.match(line)
                if match:
                    command_name = match.group(1).strip()
                    if not command_name.startswith("-"):
                        analysis["subcommands"].append(
                            {
                                "name": command_name,
                                "description": match.group(2).strip(),
                            }
                        )
        if not analysis["subcommands"]:
            analysis["subcommands"] = [
                {"name": cmd, "description": "N/A"} for cmd in self._fallback_analyze_cli(lines)
            ]
        analysis["common_flags"] = list(set(re.findall("--[a-zA-Z0-9][a-zA-Z0-9-]+", help_text)))
        return analysis

    def _fallback_analyze_cli(self, lines: list) -> list:
        subcommands = []
        in_command_section = False
        for line in lines:
            if re.search("commands:", line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and line.strip():
                match = re.match("^\\s*([a-zA-Z0-9_-]+)", line)
                if match:
                    subcommands.append(match.group(1))
            elif in_command_section and (not line.strip()):
                in_command_section = False
        return list(set(subcommands))

    def _get_distribution(self, package_name: str, version: str = None):
        try:
            dist = importlib.metadata.distribution(package_name)
            if version is None or dist.version == version:
                return dist
        except importlib.metadata.PackageNotFoundError:
            pass
        if version:
            bubble_path = Path(self.config["multiversion_base"]) / f"{package_name}-{version}"
            return self._find_distribution_at_path(package_name, version, bubble_path)
        return None

    def _enrich_from_site_packages(self, name: str, version: str = None) -> Dict:
        enriched_data = {}
        guesses = set([name, name.lower().replace("-", "_")])
        base_path = Path(get_site_packages_path())
        if version:
            base_path = Path(self.config["multiversion_base"]) / f"{name}-{version}"
        for g in guesses:
            pkg_path = base_path / g
            if pkg_path.is_dir():
                readme_path = next(
                    (p for p in pkg_path.glob("[Rr][Ee][Aa][Dd][Mm][Ee].*") if p.is_file()),
                    None,
                )
                if readme_path:
                    enriched_data["readme_snippet"] = readme_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )[:500]
                license_path = next(
                    (p for p in pkg_path.glob("[Ll][Ii][Cc][Ee][Nn][Ss]*") if p.is_file()),
                    None,
                )
                if license_path:
                    enriched_data["license_text"] = license_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )[:500]
                return enriched_data
        return {}

    def _flatten_dict(self, d: Dict, parent_key: str = "", sep: str = ".") -> Dict:
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, str(v)))
        return dict(items)


if __name__ == "__main__":
    import hashlib
    import json
    from pathlib import Path

    from omnipkg.cache import SQLiteCacheClient

    safe_print(_("🚀 Starting omnipkg Metadata Builder v12 (SQLite/Redis Edition)..."))
    try:
        config_path = Path.home() / ".config" / "omnipkg" / "config.json"
        with open(config_path, "r") as f:
            full_config = json.load(f)
        env_id_from_os = os.environ.get("OMNIPKG_ENV_ID_OVERRIDE")
        if env_id_from_os:
            env_id = env_id_from_os
            safe_print(_("   (Inherited environment ID: {})").format(env_id))
        else:
            current_dir = Path(sys.executable).resolve().parent
            venv_path = Path(sys.prefix)
            while current_dir != current_dir.parent:
                if (current_dir / "pyvenv.cfg").exists():
                    venv_path = current_dir
                    break
                current_dir = current_dir.parent
            env_id = hashlib.md5(str(venv_path.resolve()).encode()).hexdigest()[:8]
            safe_print(_("   (Calculated environment ID: {})").format(env_id))
        config = full_config["environments"][env_id]
    except (FileNotFoundError, KeyError) as e:
        safe_print(
            f"❌ CRITICAL: Could not load omnipkg configuration for this environment (ID: {env_id}). Error: {e}. Aborting."
        )
        sys.exit(1)
    gatherer = omnipkgMetadataGatherer(
        config=config, env_id=env_id, force_refresh="--force" in sys.argv
    )
    try:
        cache_dir = Path(config.get("cache_dir", Path.home() / ".cache" / "omnipkg"))
        db_path = cache_dir / f"omnipkg_cache_{env_id}.db"
        safe_print(_("   (Using SQLite cache at: {})").format(db_path))
        gatherer.cache_client = SQLiteCacheClient(db_path=db_path)
        if gatherer.cache_client and gatherer.cache_client.ping():
            targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
            if targeted_packages:
                gatherer.run(targeted_packages=targeted_packages)
            else:
                gatherer.run()
            safe_print(_("\n🎉 Metadata building complete!"))
        else:
            safe_print(_("❌ Failed to connect to SQLite cache. Aborting."))
            sys.exit(1)
    except Exception as e:
        safe_print(_("\n❌ An unexpected error occurred during metadata build: {}").format(e))
        import traceback

        traceback.print_exc()
        sys.exit(1)
