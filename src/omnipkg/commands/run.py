from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print, sync_context_to_runtime
import importlib
from omnipkg.core import ConfigManager
from omnipkg.core import omnipkg as OmnipkgCore
from omnipkg.i18n import _
from omnipkg.utils.ai_import_healer import heal_code_string

# omnipkg/commands/run.py
try:
    from ..common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path

# THE FIX: HAS_SELECT is only True on non-Windows (POSIX) systems.
HAS_SELECT = os.name == "posix"

try:
    from omnipkg.utils.flask_port_finder import auto_patch_flask_port
except ImportError:
    auto_patch_flask_port = None

# --- PROJECT PATH SETUP ---
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


# Global variable to store initial run timing for comparison
@contextmanager
def temporary_install_strategy(omnipkg_instance, strategy):
    """
    Temporarily switches the install strategy (e.g., to 'stable-main')
    to prevent breaking the main environment during auto-healing operations.
    """
    # Save original state
    original_strategy = omnipkg_instance.config.get("install_strategy")

    # Apply temporary strategy if different
    if original_strategy != strategy:
        omnipkg_instance.config["install_strategy"] = strategy

    try:
        yield
    finally:
        # Restore original state
        omnipkg_instance.config["install_strategy"] = original_strategy


# CRITICAL FIXES FOR JAX/JAXLIB PAIRING AND LOADER HINT DETECTION


# Fix 1: Enhanced loader hint detection (supports multiple specs)
def detect_loader_hints(stderr: str, healing_plan: set):
    """Detect and add all loader hint specs to healing plan"""
    # Match both single-quoted and potential multi-spec hints
    loader_hint_matches = re.findall(r"Hint: Install with 'omnipkg install ([^']+)'", stderr)
    if loader_hint_matches:
        for hint_line in loader_hint_matches:
            # Split by spaces to handle multiple specs in one hint
            specs = hint_line.strip().split()
            for missing_spec in specs:
                missing_spec = missing_spec.strip()
                if missing_spec and missing_spec not in healing_plan:
                    safe_print(f"\n🔍 omnipkgLoader requested missing dependency: {missing_spec}")
                    healing_plan.add(missing_spec)
    return healing_plan


# Fix 2: Comprehensive JAX/JAXlib pairing logic
def ensure_jax_jaxlib_pairing(stderr: str, healing_plan: set):
    """
    Ensures JAX and JAXlib are always paired with matching versions.
    Strategy:
    1. If user specified jax version -> use it as master, ensure jaxlib matches
    2. If user only specified jaxlib -> use it as master, ensure jax matches
    3. If runtime mismatch detected -> use requested jax version as master
    4. Always add BOTH to healing plan with matching versions
    """
    MIN_AVAILABLE_JAX_VER = "0.4.6"

    # First, check for runtime mismatch errors
    jax_mismatch = re.search(
        r"jaxlib version ([\d]+(?:\.\d+)*) is .*? jax version ([\d]+(?:\.\d+)*)", stderr
    )
    if jax_mismatch:
        jaxlib_env_ver = jax_mismatch.group(1)  # What's in main env (wrong)
        jax_requested_ver = jax_mismatch.group(2)  # What loader is trying to load

        safe_print("\n🔍 JAX/JAXlib Version Mismatch Detected.")
        safe_print(f"   - Main environment has: jaxlib=={jaxlib_env_ver}")
        safe_print(f"   - Loader attempting: jax=={jax_requested_ver}")
        safe_print("   - ⚠️  These versions are incompatible!")

        # Check if requested version is available
        try:
            from pkg_resources import parse_version
        except ImportError:

            def parse_version(v):
                return tuple(map(int, v.split(".")))

        if parse_version(jax_requested_ver) < parse_version(MIN_AVAILABLE_JAX_VER):
            # Requested version too old, force upgrade both
            target_ver = MIN_AVAILABLE_JAX_VER
            safe_print(f"   - 🛑 jax=={jax_requested_ver} unavailable on PyPI (yanked)")
            safe_print(f"   - 🛡️  Force upgrading to minimum available: {target_ver}")
        else:
            # Use the requested version
            target_ver = jax_requested_ver
            safe_print("   - 🛡️  Solution: Load BOTH jax and jaxlib as paired bubbles")

        # Add BOTH packages as a matched pair
        healing_plan.add(f"jax=={target_ver}")
        healing_plan.add(f"jaxlib=={target_ver}")
        safe_print(f"   - 📦 Added to healing plan: jax=={target_ver}, jaxlib=={target_ver}")

    # CRITICAL: Check what user actually requested in healing_plan and ensure pairing
    # Extract any jax/jaxlib specs already in plan
    jax_specs_in_plan = [
        s
        for s in healing_plan
        if s.startswith("jax==") or s.startswith("jax<") or s.startswith("jax>")
    ]
    jaxlib_specs_in_plan = [
        s
        for s in healing_plan
        if s.startswith("jaxlib==") or s.startswith("jaxlib<") or s.startswith("jaxlib>")
    ]

    if jax_specs_in_plan or jaxlib_specs_in_plan:
        safe_print("\n🔍 JAX/JAXlib pairing check...")

        # Determine master version
        master_version = None
        master_source = None

        if jax_specs_in_plan:
            # JAX is master (priority)
            jax_spec = jax_specs_in_plan[0]
            if "==" in jax_spec:
                master_version = jax_spec.split("==")[1]
                master_source = "jax"
                safe_print(f"   - Using jax=={master_version} as master version")

        if not master_version and jaxlib_specs_in_plan:
            # JAXlib is master (only if jax not specified)
            jaxlib_spec = jaxlib_specs_in_plan[0]
            if "==" in jaxlib_spec:
                master_version = jaxlib_spec.split("==")[1]
                master_source = "jaxlib"
                safe_print(f"   - Using jaxlib=={master_version} as master version")

        if master_version:
            # Ensure BOTH are in healing plan with matching versions
            paired_jax = f"jax=={master_version}"
            paired_jaxlib = f"jaxlib=={master_version}"

            # Remove any old specs
            healing_plan -= set(jax_specs_in_plan)
            healing_plan -= set(jaxlib_specs_in_plan)

            # Add matched pair
            healing_plan.add(paired_jax)
            healing_plan.add(paired_jaxlib)

            safe_print(f"   - ✅ Ensured matched pair: {paired_jax}, {paired_jaxlib}")
            safe_print(f"   - 🎯 Master source: {master_source}")
        else:
            safe_print("   - ⚠️  Could not extract explicit version from specs")
            safe_print(f"   - Found: {jax_specs_in_plan + jaxlib_specs_in_plan}")

    return healing_plan

    # ============================================================================
    # EXECUTE HEALING PLAN (with JAX/JAXlib awareness)
    # ============================================================================


def analyze_runtime_failure_and_heal(
    stderr: str,
    cmd_args: list,
    original_script_path_for_analysis: Path,
    config_manager: ConfigManager,
    is_context_aware_run: bool,
    cli_owner_spec: str = None,
    omnipkg_instance=None,
    verbose=False,
    retry_count=0,
    attempted_fixes=None,
    initial_failure_duration_ns=0,
):
    """
    Analyzes runtime failures and attempts to heal them automatically.
    Tracks attempted fixes to prevent infinite loops.
    PRESERVES ALL ORIGINAL ROBUST LOGIC + adds JAX/bubble detection.
    """
    # Initialize tracking
    if attempted_fixes is None:
        attempted_fixes = set()

    # Max retry protection
    if retry_count >= 3:
        safe_print("\n❌ Max auto-healing retries reached.")
        safe_print("\n📄 LAST ERROR OUTPUT:")
        print(stderr)
        return 1, None

    healing_plan = set()
    final_specs = []  # FIX: Initialize here to ensure it's always bound.

    # Fix 3: Use passed instance
    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)

    if cli_owner_spec:
        command = cmd_args[0] if cmd_args else None
        if command and final_specs:
            safe_print(f"♻️  Loading: {final_specs}")
            return run_cli_with_healing_wrapper(
                final_specs,
                command,
                cmd_args[1:],
                config_manager,
                initial_failure_duration_ns=initial_failure_duration_ns,
            )

    # ============================================================================
    # NEW PRIORITY LISTENERS - Check omnipkg-specific issues first
    # ============================================================================

    # --- [NEW LISTENER 1] OMNIPKG LOADER HINTS ---
    # NEW PRIORITY LISTENERS - Check omnipkg-specific issues first
    # ============================================================================

    # --- [LISTENER 1] OMNIPKG LOADER HINTS ---
    healing_plan = detect_loader_hints(stderr, healing_plan)

    # --- [LISTENER 2] JAX/JAXLIB VERSION PAIRING ---
    healing_plan = ensure_jax_jaxlib_pairing(stderr, healing_plan)

    # --- [LISTENER 3] GENERIC BUBBLE NOT FOUND ---
    bubble_miss_match = re.search(r"Bubble not found: .*?/([\w\-\.]+)-([\d\.]+)", stderr)
    if bubble_miss_match:
        pkg = bubble_miss_match.group(1)
        ver = bubble_miss_match.group(2)
        pkg = pkg.replace("_", "-")
        spec = f"{pkg}=={ver}"
        if spec not in healing_plan:
            safe_print(f"\n🔍 Loader reported missing bubble: {spec}")
            healing_plan.add(spec)
    # ============================================================================
    # ORIGINAL ROBUST PATTERN MATCHING - ALL PRESERVED
    # ============================================================================

    # Pattern 1: Prioritize specific, known issues like NumPy 2.0 incompatibility.
    numpy_patterns = [
        r"A module that was compiled using NumPy 1\.x cannot be run in[\s\S]*?NumPy 2\.0",
        r"numpy\.dtype size changed, may indicate binary incompatibility",
        r"AttributeError: _ARRAY_API not found",
        r"ImportError: numpy\.core\._multiarray_umath failed to import",
    ]
    if any(re.search(p, stderr, re.MULTILINE) for p in numpy_patterns):
        healing_plan.add("numpy==1.26.4")

    # Pattern 1.5: Deep ABI incompatibility issues that require package reinstallation
    abi_incompatibility_patterns = [
        (
            r"tensorflow\.python\.framework\.errors_impl\.NotFoundError:.*?undefined symbol.*?tensorflow",
            "tensorflow",
            "TensorFlow ABI incompatibility",
        ),
        (
            r"ImportError:.*?undefined symbol.*?(_ZN\w+)",
            None,
            "Generic ABI incompatibility",
        ),
        (
            r"OSError:.*?cannot open shared object file.*?No such file or directory",
            None,
            "Missing shared library",
        ),
        (
            r"ImportError:.*?DLL load failed.*?The specified module could not be found",
            None,
            "Windows DLL load failure",
        ),
    ]

    for regex, target_package, description in abi_incompatibility_patterns:
        match = re.search(regex, stderr, re.MULTILINE | re.DOTALL)
        if match:
            safe_print(f"\n🔍 {description} detected. This requires package reinstallation...")

            if target_package:
                safe_print(f"   - The issue is with '{target_package}' package")
                safe_print(
                    "   - This package was likely compiled against incompatible dependencies"
                )
                safe_print(
                    f"🚀 Auto-healing by reinstalling '{target_package}' to rebuild against current environment..."
                )
                return heal_with_package_reinstall(
                    target_package,
                    original_script_path_for_analysis,
                    cmd_args[1:],
                    config_manager,
                    omnipkg_instance=omnipkg_instance,
                )
            else:
                import_matches = re.findall(r"File \".*?/site-packages/(\w+)/", stderr)
                if import_matches:
                    problematic_package = import_matches[-1]
                    safe_print(f"   - The issue appears to be with '{problematic_package}' package")
                    safe_print(
                        "   - This package likely has ABI incompatibilities with current dependencies"
                    )
                    safe_print(
                        f"🚀 Auto-healing by reinstalling '{problematic_package}' to rebuild against current environment..."
                    )
                    return heal_with_package_reinstall(
                        problematic_package,
                        original_script_path_for_analysis,
                        cmd_args[1:],
                        config_manager,
                        omnipkg_instance=omnipkg_instance,
                    )
                else:
                    safe_print("   - Could not identify the specific problematic package")
                    safe_print("❌ Auto-healing aborted. Manual intervention may be required.")
                    return 1, None

    # Pattern 2: Handle explicit version conflicts from requirements.
    conflict_patterns = [
        (
            r"AssertionError: Incorrect ([\w\-]+) version! Expected ([\d\.]+)",
            1,
            2,
            "Runtime version assertion",
        ),
        (
            r"requires ([\w\-]+)==([\d\.]+), but you have",
            1,
            2,
            "Import-time dependency conflict",
        ),
        (
            r"assert\s+([\w\.]+)(?:\.__version__)?\s*==\s*['\"]([\d\.]+)['\"]",
            1,
            2,
            "Source code assertion",
        ),
        (
            r"VersionConflict:.*?Requirement\.parse\('([\w\-]+)==([\d\.]+)'\)",
            1,
            2,
            "Setuptools VersionConflict",
        ),
    ]
    for regex, pkg_group, ver_group, description in conflict_patterns:
        match = re.search(regex, stderr)
        if match:
            raw_pkg_name = match.group(pkg_group)

            # Strip common attribute artifacts
            clean_pkg_name = raw_pkg_name.replace(".__version__", "").replace(".version", "")

            # Strip common variable name suffixes
            if clean_pkg_name.endswith("_version"):
                clean_pkg_name = clean_pkg_name[:-8]
            elif clean_pkg_name.endswith("_ver"):
                clean_pkg_name = clean_pkg_name[:-4]

            # STDLIB CHECK
            if is_stdlib_module(clean_pkg_name):
                if verbose:
                    safe_print(
                        f"   ⚠️  Ignoring version assertion for standard library module '{clean_pkg_name}'."
                    )
                continue

            # Convert to real PyPI name
            pkg_name = convert_module_to_package_name(clean_pkg_name).lower()
            expected_version = match.group(ver_group)
            failed_spec = f"{pkg_name}=={expected_version}"

            if failed_spec not in healing_plan:
                safe_print(f"\n🔍 {description} failed. Auto-healing with omnipkg bubbles...")
                safe_print(_("   - Conflict identified for: {}").format(failed_spec))
                healing_plan.add(failed_spec)

    # Pattern 3: Heuristically handle AttributeErrors
    if "AttributeError:" in stderr:
        importer_matches = re.findall(r"from ([\w\.]+) import", stderr)

        if importer_matches:
            culprit_package = importer_matches[-1].split(".")[0]
            script_dir = original_script_path_for_analysis.parent
            is_local_module = (script_dir / culprit_package).is_dir() or (
                script_dir / f"{culprit_package}.py"
            ).is_file()

            if not is_local_module:
                if is_stdlib_module(culprit_package):
                    safe_print(
                        f"\n❌ AttributeError in standard library module '{culprit_package}' detected."
                    )
                    safe_print("   This indicates a code error, not a missing package.")
                    return 1, None

                safe_print("\n🔍 Deep dependency conflict detected (AttributeError).")
                safe_print(
                    f"   - The root cause appears to be the '{culprit_package}' package or its dependencies."
                )
                safe_print(
                    f"🚀 Auto-healing by creating an isolated bubble for '{culprit_package}'..."
                )
                healing_plan.add(culprit_package)

        # Fallback regex
        fallback_match = re.search(r"AttributeError: module '([\w\-\.]+)' has no attribute", stderr)
        if fallback_match:
            pkg_name_to_upgrade = fallback_match.group(1)

            if is_stdlib_module(pkg_name_to_upgrade):
                safe_print(
                    f"\n❌ AttributeError in standard library module '{pkg_name_to_upgrade}' detected."
                )
                safe_print("   This indicates a code error, not a missing package.")
                return 1, None

            return heal_with_missing_package(
                pkg_name_to_upgrade,
                Path(cmd_args[0]),
                cmd_args[1:],
                original_script_path_for_analysis,
                config_manager,
                is_context_aware_run,
                omnipkg_instance=omnipkg_instance,
                attempted_fixes=attempted_fixes,
                error_context=stderr,  # <--- PASS STDERR HERE
            )

    # Pattern 4: Handle missing modules
    missing_module_patterns = [
        (r"ModuleNotFoundError: No module named '([\w\-\.]+)'", 1, "Missing module"),
        (
            r"ImportError: No module named ([\w\-\.]+)",
            1,
            "Missing module (ImportError)",
        ),
    ]
    for regex, pkg_group, description in missing_module_patterns:
        match = re.search(regex, stderr)
        if match:
            full_module_name = match.group(pkg_group)
            top_level_module = full_module_name.split(".")[0]

            # STDLIB CHECK
            if is_stdlib_module(top_level_module):
                safe_print(f"\n❌ Missing standard library module '{top_level_module}'.")
                safe_print("   This indicates a corrupted Python environment or invalid import.")
                return 1, None

            script_dir = original_script_path_for_analysis.parent

            # Check if it's a local module
            potential_local_path_dir = script_dir / top_level_module
            potential_local_path_file = script_dir / f"{top_level_module}.py"

            if potential_local_path_dir.is_dir() or potential_local_path_file.is_file():
                safe_print(f"\n🔍 {description} detected - This appears to be a LOCAL IMPORT.")
                safe_print(f"   - The script failed to import '{full_module_name}'.")
                safe_print(
                    f"   - A local module '{top_level_module}' was found in the project directory."
                )
                safe_print(_("🚀 Attempting a context-aware re-run..."))
                return _run_script_with_healing(
                    script_path=original_script_path_for_analysis,
                    script_args=cmd_args[1:],
                    config_manager=config_manager,
                    original_script_path_for_analysis=original_script_path_for_analysis,
                    heal_type="local_context_run",
                    is_context_aware_run=True,
                    omnipkg_instance=omnipkg_instance,
                    attempted_fixes=attempted_fixes,
                )

            # Check if it's a local installable project
            parent_dir = script_dir.parent
            potential_parent_module_dir = parent_dir / top_level_module
            potential_setup_py = parent_dir / "setup.py"
            potential_pyproject_toml = parent_dir / "pyproject.toml"

            if potential_parent_module_dir.is_dir() and (
                potential_setup_py.exists() or potential_pyproject_toml.exists()
            ):
                safe_print(f"\n🔍 {description} detected - this appears to be a PROJECT PACKAGE.")
                safe_print(
                    "\n💡 This is likely a package that needs to be installed in editable mode."
                )
                safe_print(f"   1. Try installing with: pip install -e {parent_dir}")
                safe_print(
                    "\n❌ Auto-healing aborted. Please install the local project package manually."
                )
                return 1, None

            # It's a missing PyPI package
            safe_print(
                f"\n🔍 {description} detected. Auto-healing by installing missing package..."
            )
            pkg_name = convert_module_to_package_name(top_level_module)

            # --- GHOST PACKAGE DETECTION ---

            if is_package_corrupted(pkg_name, top_level_module):

                safe_print(
                    f"\n👻 Ghost package detected: '{pkg_name}' metadata exists, but '{top_level_module}' is missing."
                )

                safe_print(f"   - Action: Force reinstalling '{pkg_name}' to fix corruption.")

                if not omnipkg_instance:

                    omnipkg_instance = OmnipkgCore(config_manager)

                # Loop prevention

                ghost_key = f"ghost_fix_{pkg_name}"

                if attempted_fixes is not None:

                    if ghost_key in attempted_fixes:

                        safe_print(
                            "❌ Corruption persists after reinstall. Aborting to prevent loop."
                        )

                        return 1, None

                    attempted_fixes.add(ghost_key)

                # Force reinstall using stable-main (fix the environment)

                with temporary_install_strategy(omnipkg_instance, "stable-main"):

                    ret = omnipkg_instance.smart_install([pkg_name], force_reinstall=True)

                if ret == 0:

                    safe_print("✅ Reinstall successful. Restarting script...")

                    return heal_with_missing_package(
                        pkg_name,
                        Path(cmd_args[0]),
                        cmd_args[1:],
                        original_script_path_for_analysis,
                        config_manager,
                        is_context_aware_run,
                        omnipkg_instance=omnipkg_instance,
                        attempted_fixes=attempted_fixes,
                        error_context=stderr,  # <--- PASS STDERR HERE
                    )

            # -------------------------------

            return heal_with_missing_package(
                pkg_name,
                Path(cmd_args[0]),
                cmd_args[1:],
                original_script_path_for_analysis,
                config_manager,
                is_context_aware_run,
                omnipkg_instance=omnipkg_instance,
                attempted_fixes=attempted_fixes,
            )

    # Pattern 5: Handle missing required packages
    missing_package_patterns = [
        (r"No module named '(\w+)'", 1, "Missing module import"),
        (r"Please make sure you have `(\w+)` installed", 1, "Missing required package"),
        (r"Recommended: pip install (\w+)", 1, "Missing recommended package"),
        (r"ModuleNotFoundError: No module named '([\w\.]+)'", 1, "Module not found"),
        (
            r"ImportError: cannot import name '[\w]+' from '([\w\.]+)'",
            1,
            "Import error from module",
        ),
        (r"requires (\w+) to be installed", 1, "Dependency requirement"),
    ]

    for regex, pkg_group, description in missing_package_patterns:
        match = re.search(regex, stderr)
        if match:
            pkg_name = match.group(pkg_group).lower()

            # If it's a dotted module path, extract the base package
            if "." in pkg_name:
                pkg_name = pkg_name.split(".")[0]

            if is_stdlib_module(pkg_name):
                continue

            # Handle special cases
            package_map = {
                "sentencepiece": "sentencepiece",
                "sklearn": "scikit-learn",
                "cv2": "opencv-python",
                "PIL": "Pillow",
            }
            pkg_name = package_map.get(pkg_name, pkg_name)
            failed_spec = pkg_name
            safe_print(f"\n🔍 {description} detected. Auto-healing with omnipkg bubbles...")
            safe_print(_("   - Installing missing package: {}").format(failed_spec))
            healing_plan.add(failed_spec)

    # ============================================================================
    # EXECUTE HEALING PLAN
    # ============================================================================

    if healing_plan:
        # Clean up conflicting specs (BUT PRESERVE JAX/JAXLIB PAIRS!)
        versioned_pkgs = set()
        for spec in healing_plan:
            if "==" in spec or "<" in spec or ">" in spec:
                pkg = re.split(r"[<>=!]", spec)[0]
                versioned_pkgs.add(pkg)

        cleaned_plan = set()
        for spec in healing_plan:
            is_generic = not any(op in spec for op in ["==", "<", ">", "!", "~"])
            if is_generic:
                # Skip generic if versioned exists, UNLESS it's jax/jaxlib (always keep pairs)
                pkg_name = spec.split("==")[0] if "==" in spec else spec
                if pkg_name in versioned_pkgs and pkg_name not in ["jax", "jaxlib"]:
                    continue
            cleaned_plan.add(spec)

        # FINAL JAX/JAXLIB VALIDATION
        jax_in_plan = any("jax==" in s for s in cleaned_plan)
        jaxlib_in_plan = any("jaxlib==" in s for s in cleaned_plan)

        if jax_in_plan and not jaxlib_in_plan:
            # Extract jax version and add matching jaxlib
            jax_spec = [s for s in cleaned_plan if "jax==" in s][0]
            jax_ver = jax_spec.split("==")[1]
            cleaned_plan.add(f"jaxlib=={jax_ver}")
            safe_print(f"   🔧 Auto-added jaxlib=={jax_ver} to match jax")

        if jaxlib_in_plan and not jax_in_plan:
            # Extract jaxlib version and add matching jax
            jaxlib_spec = [s for s in cleaned_plan if "jaxlib==" in s][0]
            jaxlib_ver = jaxlib_spec.split("==")[1]
            cleaned_plan.add(f"jax=={jaxlib_ver}")
            safe_print(f"   🔧 Auto-added jax=={jaxlib_ver} to match jaxlib")

        specs_to_heal = sorted(list(cleaned_plan))
        safe_print(
            f"\n🔍 Comprehensive Healing Plan Compiled (Attempt {retry_count + 1}): {specs_to_heal}"
        )

        # Loop detection
        specs_tuple = tuple(specs_to_heal)
        force_reinstall = False

        if specs_tuple in attempted_fixes:
            safe_print("⚠️  Repeated failure with same specs. Forcing rebuild of bubbles.")
            force_reinstall = True

        attempted_fixes.add(specs_tuple)

        # REMOVED: final_specs = [] -> This is now initialized at the function top.
        original_strategy = omnipkg_instance.config.get("install_strategy")
        omnipkg_instance.config["install_strategy"] = "stable-main"

        try:
            for spec in specs_to_heal:
                if "==" not in spec:
                    # RESOLVE LATEST VERSION FROM PYPI FIRST
                    safe_print(f"🔍 Resolving latest version for '{spec}'...")
                    latest_ver = omnipkg_instance._get_latest_version_from_pypi(spec)

                    if latest_ver:
                        target_spec = f"{spec}=={latest_ver}"

                        # CHECK IF THIS SPECIFIC VERSION IS ALREADY INSTALLED
                        safe_name = spec.lower().replace("-", "_")
                        bubble_path = (
                            omnipkg_instance.multiversion_base / f"{safe_name}-{latest_ver}"
                        )

                        if bubble_path.is_dir() and not force_reinstall:
                            safe_print(
                                f"   🚀 INSTANT HIT: Found existing bubble {target_spec} in KB"
                            )
                            final_specs.append(target_spec)
                            continue

                        # Check Main Env
                        active_ver = omnipkg_instance.get_active_version(spec)
                        if active_ver == latest_ver and not force_reinstall:
                            safe_print(f"   ✅ Main environment already has {target_spec}")
                            final_specs.append(target_spec)
                            continue

                        # IF NOT FOUND, INSTALL IT
                        safe_print(f"🛠️  Installing bubble for {target_spec}...")
                        omnipkg_instance.smart_install([target_spec])
                        final_specs.append(target_spec)

                    else:
                        safe_print(
                            f"⚠️  Could not resolve latest version for {spec}. Trying generic install."
                        )
                        final_specs.append(spec)
                        if (
                            not (
                                omnipkg_instance.multiversion_base / spec.replace("==", "-")
                            ).exists()
                            or force_reinstall
                        ):
                            omnipkg_instance.smart_install([spec])

                else:
                    # Explicitly versioned specs
                    final_specs.append(spec)
                    pkg_name = spec.split("==")[0]
                    pkg_ver = spec.split("==")[1]
                    safe_name = pkg_name.lower().replace("-", "_")
                    bubble_path = omnipkg_instance.multiversion_base / f"{safe_name}-{pkg_ver}"

                    if not bubble_path.is_dir() or force_reinstall:
                        safe_print(f"🛠️  Installing bubble for {spec}...")
                        omnipkg_instance.smart_install([spec])

        finally:
            if original_strategy:
                omnipkg_instance.config["install_strategy"] = original_strategy

        # --- EXECUTION LOGIC ---

        # If it's a CLI tool
        if cli_owner_spec:
            command = cmd_args[0] if cmd_args else None
            if command and final_specs:
                safe_print(f"♻️  Loading: {final_specs}")
                return run_cli_with_healing_wrapper(
                    final_specs, command, cmd_args[1:], config_manager
                )

        # If it's a script run
        if final_specs:
            return heal_with_bubble(
                final_specs,
                original_script_path_for_analysis,
                cmd_args[1:],
                config_manager,
                omnipkg_instance=omnipkg_instance,
                verbose=verbose,
                retry_count=retry_count + 1,
                attempted_fixes=attempted_fixes,
                force_reinstall=force_reinstall,
            )

    # === FALLBACK ===
    safe_print("❌ Error could not be auto-healed or no healing plan found.")
    safe_print("\n📄 ERROR OUTPUT:")
    print(stderr)
    return 1, None


def heal_with_package_reinstall(
    package_name: str,
    script_path: Path,
    script_args: list,
    config_manager: ConfigManager,
    omnipkg_instance=None,
):
    """Reinstalls a package completely to fix ABI/compilation issues.
    This is more aggressive than bubbling and is used when packages have been
    compiled against incompatible dependencies.
    """
    safe_print(f"🔄 Starting package reinstallation for '{package_name}'...")

    try:
        # Step 1: Uninstall the problematic package completely
        safe_print(f"🗑️  Uninstalling '{package_name}' completely...")
        uninstall_result = subprocess.run(
            [sys.executable, "-m", "8pkg", "uninstall", package_name, "-y"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if uninstall_result.returncode == 0:
            safe_print(f"✅ Successfully uninstalled '{package_name}'")
        else:
            safe_print(f"⚠️  Uninstall had issues, but continuing: {uninstall_result.stderr}")

        # Step 2: Clear pip cache to ensure fresh download
        safe_print(f"🧹 Clearing pip cache for '{package_name}'...")
        cache_clear_result = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "remove", package_name],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if cache_clear_result.returncode == 0:
            safe_print(f"✅ Successfully cleared cache for '{package_name}'")
        else:
            safe_print("⚠️  Cache clear had issues, but continuing...")

        # Step 3: Reinstall the package
        safe_print(f"📦 Reinstalling '{package_name}' with fresh compilation...")
        install_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                package_name,
                "--no-cache-dir",
                "--force-reinstall",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )

        if install_result.returncode != 0:
            safe_print(f"❌ Failed to reinstall '{package_name}': {install_result.stderr}")
            return 1, None

        safe_print(f"✅ Successfully reinstalled '{package_name}'")

        # Step 4: Re-run the original script
        safe_print(f"🚀 Re-running script after '{package_name}' reinstallation...")
        return _run_script_with_healing(
            script_path=script_path,
            script_args=script_args,
            config_manager=config_manager,
            original_script_path_for_analysis=script_path,
            heal_type=f"package_reinstall_{package_name}",
            is_context_aware_run=False,
            omnipkg_instance=omnipkg_instance,  # ✅ Pass omnipkg_instance recursively
        )

    except subprocess.TimeoutExpired:
        safe_print(f"❌ Package reinstallation timed out for '{package_name}'")
        return 1, None
    except Exception as e:
        safe_print(f"❌ Unexpected error during package reinstallation: {e}")
        return 1, None


def is_stdlib_module(module_name: str) -> bool:
    """
    Determines if a module name belongs to the Python Standard Library.
    """
    if not module_name:
        return False

    base_name = module_name.split(".")[0]

    # 1. Use the definitive source in Python 3.10+
    if sys.version_info >= (3, 10):
        if base_name in sys.stdlib_module_names:
            return True

    # 2. Check built-in modules (sys, gc, etc.)
    if base_name in sys.builtin_module_names:
        return True

    # 3. Fallback hardcoded list for older Pythons or edge cases
    # (Common stdlibs that users might accidentally try to install)
    COMMON_STDLIBS = {
        "os",
        "sys",
        "re",
        "json",
        "math",
        "random",
        "datetime",
        "subprocess",
        "pathlib",
        "typing",
        "collections",
        "itertools",
        "functools",
        "io",
        "pickle",
        "copy",
        "enum",
        "dataclasses",
        "abc",
        "contextlib",
        "argparse",
        "shutil",
        "threading",
        "multiprocessing",
        "asyncio",
        "socket",
        "ssl",
        "sqlite3",
        "csv",
        "time",
        "logging",
        "warnings",
        "traceback",
        "inspect",
        "ast",
        "platform",
        "urllib",
        "http",
        "email",
        "xml",
        "html",
        "unittest",
        "venv",
        "pydoc",
        "pdb",
        "profile",
        "cProfile",
        "timeit",
    }

    return base_name in COMMON_STDLIBS


def is_package_corrupted(pkg_name, missing_module_name):
    """
    Checks if a package is 'Ghosted' (metadata exists so pip thinks it's installed,
    but the actual module cannot be imported).
    """
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata
    import importlib.util

    # 1. Is it installed according to metadata?
    try:
        importlib.metadata.distribution(pkg_name)
    except importlib.metadata.PackageNotFoundError:
        return False  # Not installed -> Not corrupted (just missing)

    # 2. Can we actually find the module spec?
    try:
        # If find_spec returns None, the python files are missing
        if importlib.util.find_spec(missing_module_name) is None:
            return True
    except (ImportError, ValueError, AttributeError):
        return True  # Import system choked on it -> Corrupted

    return False


def convert_module_to_package_name(module_name: str, error_message: str = None) -> str:
    """
    Convert a module name to its likely PyPI package name.
    Handles common cases where module names differ from package names.
    """
    # Common module -> package mappings
    module_to_package = {
        "yaml": "pyyaml",
        "cv2": "opencv-python",
        "PIL": "pillow",
        "sklearn": "scikit-learn",
        "bs4": "beautifulsoup4",
        "requests_oauthlib": "requests-oauthlib",
        "google.auth": "google-auth",
        "google.cloud": "google-cloud-core",
        "jwt": "pyjwt",
        "absl": "absl-py",  # <--- ADD THIS LINE
        "dateutil": "python-dateutil",
        "magic": "python-magic",
        "psutil": "psutil",
        "lxml": "lxml",
        "numpy": "numpy",
        "pandas": "pandas",
        "matplotlib": "matplotlib",
        "seaborn": "seaborn",
        "plotly": "plotly",
        "dash": "dash",
        "flask": "flask",
        "django": "django",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "gunicorn": "gunicorn",
        "celery": "celery",
        "redis": "redis",
        "pymongo": "pymongo",
        "sqlalchemy": "sqlalchemy",
        "alembic": "alembic",
        "psycopg2": "psycopg2-binary",
        "mysqlclient": "mysqlclient",
        "pytest": "pytest",
        "black": "black",
        "flake8": "flake8",
        "mypy": "mypy",
        "isort": "isort",
        "pre_commit": "pre-commit",
        "click": "click",
        "typer": "typer",
        "rich": "rich",
        "colorama": "colorama",
        "tqdm": "tqdm",
        "joblib": "joblib",
        "multiprocess": "multiprocess",
        "dask": "dask",
        "scipy": "scipy",
        "sympy": "sympy",
        "networkx": "networkx",
        "igraph": "python-igraph",
        "graph_tool": "graph-tool",
        "tensorflow": "tensorflow",
        "torch": "torch",
        "torchvision": "torchvision",
        "transformers": "transformers",
        "datasets": "datasets",
        "accelerate": "accelerate",
        "wandb": "wandb",
        "mlflow": "mlflow",
        "optuna": "optuna",
        "hyperopt": "hyperopt",
        "xgboost": "xgboost",
        "lightgbm": "lightgbm",
        "catboost": "catboost",
        "shap": "shap",
        "lime": "lime",
        "eli5": "eli5",
        "boto3": "boto3",
        "botocore": "botocore",
        "azure": "azure",
        "google": "google-cloud",
        "openai": "openai",
        "anthropic": "anthropic",
        "langchain": "langchain",
        "llama_index": "llama-index",
        "chromadb": "chromadb",
        "pinecone": "pinecone-client",
        "weaviate": "weaviate-client",
        "faiss": "faiss-cpu",
        "annoy": "annoy",
        "hnswlib": "hnswlib",
        "streamlit": "streamlit",
        "gradio": "gradio",
        "jupyterlab": "jupyterlab",
        "notebook": "notebook",
        "ipython": "ipython",
        "ipykernel": "ipykernel",
        "ipywidgets": "ipywidgets",
        "voila": "voila",
        "papermill": "papermill",
        "nbconvert": "nbconvert",
        "sphinx": "sphinx",
        "mkdocs": "mkdocs",
        "docutils": "docutils",
        "jinja2": "jinja2",
        "mako": "mako",
        "pydantic": "pydantic",
        "attrs": "attrs",
        "marshmallow": "marshmallow",
        "cerberus": "cerberus",
        "schema": "schema",
        "jsonschema": "jsonschema",
        "toml": "toml",
        "tomli": "tomli",
        "configparser": "configparser",
        "dotenv": "python-dotenv",
        "decouple": "python-decouple",
        "environs": "environs",
        "click_log": "click-log",
        "loguru": "loguru",
        "structlog": "structlog",
        "sentry_sdk": "sentry-sdk",
        "rollbar": "rollbar",
        "bugsnag": "bugsnag",
        "newrelic": "newrelic",
        "datadog": "datadog",
        "prometheus_client": "prometheus-client",
        "statsd": "statsd",
        "influxdb": "influxdb",
        "elasticsearch": "elasticsearch",
        "kafka": "kafka-python",
        "pika": "pika",
        "kombu": "kombu",
        "amqp": "amqp",
        "paramiko": "paramiko",
        "fabric": "fabric",
        "invoke": "invoke",
        "ansible": "ansible",
        "docker": "docker",
        "kubernetes": "kubernetes",
        "terraform": "python-terraform",
        "pulumi": "pulumi",
        "cloudformation": "troposphere",
        "boto": "boto",
        "moto": "moto",
        "localstack": "localstack",
        "pytest_mock": "pytest-mock",
        "pytest_cov": "pytest-cov",
        "pytest_xdist": "pytest-xdist",
        "pytest_html": "pytest-html",
        "pytest_json_report": "pytest-json-report",
        "coverage": "coverage",
        "codecov": "codecov",
        "bandit": "bandit",
        "safety": "safety",
        "pip_audit": "pip-audit",
        "semgrep": "semgrep",
        "vulture": "vulture",
        "radon": "radon",
        "xenon": "xenon",
        "mccabe": "mccabe",
        "pylint": "pylint",
        "pycodestyle": "pycodestyle",
        "pydocstyle": "pydocstyle",
        "pyflakes": "pyflakes",
        "autopep8": "autopep8",
        "yapf": "yapf",
        "rope": "rope",
        "jedi": "jedi",
        "parso": "parso",
        "pygments": "pygments",
        "colorlog": "colorlog",
        "termcolor": "termcolor",
        "blessed": "blessed",
        "asciimatics": "asciimatics",
        "urwid": "urwid",
        "npyscreen": "npyscreen",
        "textual": "textual",
        "prompt_toolkit": "prompt-toolkit",
        "inquirer": "inquirer",
        "questionary": "questionary",
        "pick": "pick",
        "halo": "halo",
        "yaspin": "yaspin",
        "grpc": "grpcio",
        "alive_progress": "alive-progress",
        "progress": "progress",
        "enlighten": "enlighten",
        "fire": "fire",
        "argparse": "argparse",  # Built-in, but sometimes needs backport
        "configargparse": "configargparse",
        "plac": "plac",
        "docopt": "docopt",
        "cliff": "cliff",
        "cement": "cement",
        "cleo": "cleo",
        "baker": "baker",
        "begins": "begins",
        "delegator": "delegator.py",
        "sh": "sh",
        "pexpect": "pexpect",
        "ptyprocess": "ptyprocess",
        "winpty": "pywinpty",
        "coloredlogs": "coloredlogs",
        "humanfriendly": "humanfriendly",
        "tabulate": "tabulate",
        "prettytable": "prettytable",
        "texttable": "texttable",
        "terminaltables": "terminaltables",
        "rich_table": "rich",
        "asciitable": "asciitable",
        "csvkit": "csvkit",
        "xlrd": "xlrd",
        "xlwt": "xlwt",
        "xlsxwriter": "xlsxwriter",
        "openpyxl": "openpyxl",
        "xlwings": "xlwings",
        "pandas_datareader": "pandas-datareader",
        "yfinance": "yfinance",
        "alpha_vantage": "alpha-vantage",
        "quandl": "quandl",
        "fredapi": "fredapi",
        "investpy": "investpy",
        "ccxt": "ccxt",
        "binance": "python-binance",
        "coinbase": "coinbase",
        "kraken": "krakenex",
        "bittrex": "python-bittrex",
        "poloniex": "poloniex",
        "gdax": "gdax",
        "gemini": "gemini-python",
        "blockchain": "blockchain",
        "web3": "web3",
        "eth_account": "eth-account",
        "eth_hash": "eth-hash",
        "eth_typing": "eth-typing",
        "eth_utils": "eth-utils",
        "solcx": "py-solc-x",
        "vyper": "vyper",
        "brownie": "eth-brownie",
        "ape": "eth-ape",
        "hardhat": "hardhat",
        "truffle": "truffle",
        "ganache": "ganache-cli",
        "infura": "web3[infura]",
        "alchemy": "web3[alchemy]",
        "moralis": "moralis",
        "thegraph": "thegraph",
        "qiskit": "qiskit-aer",
        "qiskit-ibm": "qiskit-ibm",
        "qiskit-ignis": "qiskit-ignis",
        "qiskit-terra": "qiskit-terra",
        "qiskit-nature": "qiskit-nature",
        "qiskit-finance": "qiskit-finance",
        "qiskit-machine-learning": "qiskit-machine-learning",
        "cirq": "cirq",
        "pennylane": "pennylane",
        "braket": "amazon-braket-sdk",
        "dwave": "dwave-ocean-sdk",
        "ocean": "ocean-sdk",
        "pyquil": "pyquil",
        "forest": "forest-sdk",
        "qsharp": "qsharp",
        "iqsharp": "iqsharp",
        "pytorch": "torch",
        "jax": "jax",
        "flax": "flax",
        "haiku": "dm-haiku",
        "optax": "optax",
        "chex": "chex",
        "dm_control": "dm-control",
        "rlax": "rlax",
        "acme": "acme",
        "trax": "trax",
        "alpa": "alpa",
        "t5x": "t5x",
        "bigscience": "bigscience",
        "peft": "peft",
        "bitsandbytes": "bitsandbytes",
        "deepspeed": "deepspeed",
        "fairseq": "fairseq",
        "sentencepiece": "sentencepiece",
        "chainlink": "chainlink",
        "uniswap": "uniswap-python",
        "compound": "compound-python",
        "aave": "aave-python",
        "maker": "maker-python",
        "curve": "curve-python",
        "yearn": "yearn-python",
        "synthetix": "synthetix-python",
        "balancer": "balancer-python",
        "sushiswap": "sushiswap-python",
        "pancakeswap": "pancakeswap-python",
        "quickswap": "quickswap-python",
        "honeyswap": "honeyswap-python",
        "spookyswap": "spookyswap-python",
        "spiritswap": "spiritswap-python",
        "traderjoe": "traderjoe-python",
        "pangolin": "pangolin-python",
        "lydia": "lydia-python",
        "elk": "elk-python",
        "oliveswap": "oliveswap-python",
        "comethswap": "comethswap-python",
        "dfyn": "dfyn-python",
        "polyswap": "polyswap-python",
        "polydex": "polydex-python",
        "apeswap": "apeswap-python",
        "jetswap": "jetswap-python",
        "mdex": "mdex-python",
        "biswap": "biswap-python",
        "babyswap": "babyswap-python",
        "nomiswap": "nomiswap-python",
        "cafeswap": "cafeswap-python",
        "cheeseswap": "cheeseswap-python",
        "julswap": "julswap-python",
        "kebabswap": "kebabswap-python",
        "burgerswap": "burgerswap-python",
        "goosedefi": "goosedefi-python",
        "alpaca": "alpaca-python",
        "autofarm": "autofarm-python",
        "belt": "belt-python",
        "bunny": "bunny-python",
        "cream": "cream-python",
        "fortress": "fortress-python",
        "venus": "venus-python",
        "wault": "wault-python",
        "acryptos": "acryptos-python",
        "beefy": "beefy-python",
        "harvest": "harvest-python",
        "pickle": "pickle-python",
        "convex": "convex-python",
        "ribbon": "ribbon-python",
        "tokemak": "tokemak-python",
        "olympus": "olympus-python",
        "wonderland": "wonderland-python",
        "klima": "klima-python",
        "rome": "rome-python",
        "redacted": "redacted-python",
        "spell": "spell-python",
        "mim": "mim-python",
        "frax": "frax-python",
        "fei": "fei-python",
        "terra": "terra-python",
        "anchor": "anchor-python",
        "mirror": "mirror-python",
        "astroport": "astroport-python",
        "prism": "prism-python",
        "loop": "loop-python",
        "mars": "mars-python",
        "stader": "stader-python",
        "pylon": "pylon-python",
        "nebula": "nebula-python",
        "starterra": "starterra-python",
        "orion": "orion-python",
        "valkyrie": "valkyrie-python",
        "apollo": "apollo-python",
        "spectrum": "spectrum-python",
        "eris": "eris-python",
        "edge": "edge-python",
        "whitewhale": "whitewhale-python",
        "backbone": "backbone-python",
        "luart": "luart-python",
        "terraswap": "terraswap-python",
        "phoenix": "phoenix-python",
        "coinhall": "coinhall-python",
        "smartstake": "smartstake-python",
        "extraterrestrial": "extraterrestrial-python",
        "tfm": "tfm-python",
        "knowhere": "knowhere-python",
        "delphi": "delphi-python",
        "galactic": "galactic-python",
        "kinetic": "kinetic-python",
        "reactor": "reactor-python",
        "protorev": "protorev-python",
        "white_whale": "white-whale-python",
        "mars_protocol": "mars-protocol-python",
        "astro_generator": "astro-generator-python",
        "apollo_dao": "apollo-dao-python",
        "eris_protocol": "eris-protocol-python",
        "backbone_labs": "backbone-labs-python",
        "luart_io": "luart-io-python",
        "terraswap_io": "terraswap-io-python",
        "phoenix_protocol": "phoenix-protocol-python",
        "coinhall_org": "coinhall-org-python",
        "smartstake_io": "smartstake-io-python",
        "extraterrestrial_money": "extraterrestrial-money-python",
        "tfm_dev": "tfm-dev-python",
        "knowhere_art": "knowhere-art-python",
        "delphi_digital": "delphi-digital-python",
        "galactic_punks": "galactic-punks-python",
    }

    # Check for direct mapping first
    if module_name in module_to_package:
        return module_to_package[module_name]

    # Step 2: If it's a dotted module (e.g., google.cloud.storage),
    # check if the base part has a mapping (e.g., google -> google-cloud-core).
    base_module = module_name.split(".")[0] if "." in module_name else module_name

    if base_module in module_to_package:
        safe_print(
            f"INFO: Found direct mapping for '{base_module}'. Package is '{module_to_package[base_module]}'"
        )
        return module_to_package[base_module]

    # Step 3: HEURISTIC FOR REFACTORED LIBRARIES (THE QISKIT FIX)
    # Look for patterns like "cannot import name 'aer' from 'qiskit'"
    if error_message:
        import_match = re.search(r"cannot import name \'(\w+)\' from \'([\w\.]+)\'", error_message)
        if import_match:
            name_to_import = import_match.group(1)
            module_it_failed_on = import_match.group(2).split(".")[0]

            # Construct a guess like "qiskit-aer"
            heuristic_package_name = f"{module_it_failed_on}-{name_to_import.lower()}"
            safe_print(
                f"INFO: Applying refactor heuristic. Guessing package is '{heuristic_package_name}'"
            )
            return heuristic_package_name

    # Step 4: HEURISTIC FOR NAMESPACE PACKAGES (DOTTED-NAME LOGIC)
    # e.g., 'google.cloud.storage' -> 'google-cloud-storage'
    if "." in module_name:
        namespace_package_name = module_name.replace(".", "-")
        safe_print(
            f"INFO: Applying namespace heuristic. Guessing package is '{namespace_package_name}'"
        )
        return namespace_package_name

    # Step 5: FINAL FALLBACK
    # The simplest case: if no other rules match, assume the module name is the package name.
    safe_print(
        f"INFO: No specific rule matched. Falling back to module name '{module_name}' as the package name."
    )
    return module_name


def heal_with_missing_package(
    pkg_name: str,
    temp_script_path: Path,
    temp_script_args: list,
    original_script_path_for_analysis: Path,
    config_manager,
    is_context_aware_run: bool,
    omnipkg_instance=None,
    attempted_fixes=None,
    error_context=None,
):
    """Installs/upgrades a package. Escalates to force_reinstall if needed, then aborts to prevent loops."""

    if attempted_fixes is None:
        attempted_fixes = set()

    # Generate a unique key for this specific package action
    action_key = f"install_{pkg_name}"

    force = False

    # --- ESCALATION LOGIC ---
    if action_key in attempted_fixes:
        # We have already tried a standard install.

        if f"{action_key}_forced" in attempted_fixes:
            # We have ALREADY tried a force reinstall. This is Strike 3 -> STOP.
            safe_print(f"\n❌ Auto-healing failed for '{pkg_name}'.")
            safe_print("   1️⃣  Attempt 1 (Standard Install): Completed, but script still failed.")
            safe_print("   2️⃣  Attempt 2 (Force Reinstall): Completed, but script still failed.")
            safe_print("   🛑  Giving up to prevent infinite loop.")

            # Add generic, helpful context
            safe_print("\n   💡 Troubleshooting Insights:")
            safe_print(
                f"      • Version Mismatch: The package may be incompatible with Python {sys.version_info.major}.{sys.version_info.minor}."
            )
            safe_print(
                "      • System Dependencies: Missing external libraries (e.g., C++ runtimes, libGL, CUDA)."
            )
            safe_print(
                "      • Ghost Package: The package claims to be installed but is empty or corrupted."
            )
            safe_print(
                "      • Import Name: The PyPI package name might differ from the import name."
            )

            if error_context:
                safe_print("\n📄 LAST ERROR OUTPUT:")
                print(error_context)

            return 1, None  # STOP THE LOOP

        # This is Strike 2. Escalate to Force Reinstall.
        safe_print(
            "\n⚠️  Attempt 1 (Standard Install) verified the package is present, but the issue persists."
        )
        safe_print(
            f"🚀 Attempt 2: FORCE REINSTALLING '{pkg_name}' to fix potential binary corruption..."
        )
        force = True
        attempted_fixes.add(f"{action_key}_forced")

    else:
        # This is Strike 1. Standard Install.
        safe_print(_("🚀 Attempt 1: Auto-installing missing package: {}").format(pkg_name))
        attempted_fixes.add(action_key)
    # -------------------------------

    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)

    # Use stable-main strategy to protect the environment during this repair
    # We pass force_reinstall=force to the core installer
    with temporary_install_strategy(omnipkg_instance, "stable-main"):
        return_code = omnipkg_instance.smart_install([pkg_name], force_reinstall=force)

    if return_code != 0:
        safe_print(_("\n❌ Auto-install failed for {}.").format(pkg_name))
        return 1, None

    safe_print(_("\n✅ Package operation successful for: {}").format(pkg_name))
    safe_print(_("🚀 Re-running script..."))

    # Pass the history (attempted_fixes) to the next run so it remembers!
    return _run_script_with_healing(
        temp_script_path,
        temp_script_args,
        config_manager,
        original_script_path_for_analysis,
        heal_type="package_install",
        is_context_aware_run=is_context_aware_run,
        omnipkg_instance=omnipkg_instance,
        attempted_fixes=attempted_fixes,
    )


def heal_with_bubble(
    required_specs,
    original_script_path,
    original_script_args,
    config_manager,
    omnipkg_instance=None,
    verbose=False,
    retry_count=0,
    attempted_fixes=None,
    force_reinstall=False,
):
    """
    Creates ISOLATED bubbles for the requested specs and re-runs the script.

    CRITICAL FIX: Healing plan specs have ABSOLUTE PRIORITY.
    Script analysis only finds ADDITIONAL dependencies.
    """
    if not omnipkg_instance:
        omnipkg_instance = OmnipkgCore(config_manager)

    if isinstance(required_specs, str):
        required_specs = [required_specs]

    final_specs = []

    # Get Python context
    current_python_exe = config_manager.config.get("python_executable", sys.executable)
    version_tuple = config_manager._verify_python_version(current_python_exe)
    python_context = (
        f"{version_tuple[0]}.{version_tuple[1]}"
        if version_tuple
        else f"{sys.version_info.major}.{sys.version_info.minor}"
    )

    # ═══════════════════════════════════════════════════════════
    # STEP 0: Build healing plan lookup (HIGHEST PRIORITY)
    # ═══════════════════════════════════════════════════════════
    healing_plan_versions = {}
    for spec in required_specs:
        if "==" in spec:
            pkg_name, pkg_version = spec.split("==", 1)
            healing_plan_versions[pkg_name.lower()] = pkg_version
            safe_print(f"🎯 LOCKED (healing plan): {pkg_name} -> {pkg_version}")

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Process Healing Plan Specs
    # ═══════════════════════════════════════════════════════════
    for spec in required_specs:
        safe_print(f"🛠️  Resolving isolation strategy for: {spec}")

        # Parse Name and Version
        if "==" in spec:
            pkg_name, pkg_version = spec.split("==", 1)
        else:
            # Unversioned: Resolve to LATEST
            pkg_name = spec
            safe_print(f"   🔍 Resolving latest version for '{pkg_name}'...")
            pkg_version = omnipkg_instance._get_latest_version_from_pypi(pkg_name)

            if not pkg_version:
                safe_print(f"❌ Could not resolve version for {spec}.")
                return 1, None

            safe_print(f"   🎯 Resolved {pkg_name} -> {pkg_version}")
            # Add to healing plan lookup
            healing_plan_versions[pkg_name.lower()] = pkg_version

        bubble_name = f"{pkg_name.lower().replace('-', '_')}-{pkg_version}"
        bubble_path = omnipkg_instance.multiversion_base / bubble_name

        # Check if main env already has exact version
        active_ver = omnipkg_instance.get_active_version(pkg_name)

        if active_ver == pkg_version:
            safe_print(f"   ✅ Main env has {pkg_name}=={pkg_version} (exact match)")
            safe_print("   💡 Skipping bubble - loader will use main env version")
            # Still add to final_specs - loader needs to know about it
            final_specs.append(f"{pkg_name}=={pkg_version}")
            continue

        # Smart Cache Check
        if bubble_path.exists() and not force_reinstall:
            safe_print(f"   🚀 CACHE HIT: Bubble exists for {pkg_name}=={pkg_version}")
        else:
            safe_print(f"   📦 Force-creating ISOLATED bubble: {bubble_name}...")
            safe_print(f"   💡 Main env has {active_ver or 'nothing'}, need {pkg_version}")
            success = omnipkg_instance.bubble_manager.create_isolated_bubble(
                pkg_name, pkg_version, python_context
            )

            if not success:
                safe_print(f"❌ Failed to create isolated bubble for {pkg_name}=={pkg_version}")
                return 1, None

            safe_print("   ✅ Bubble created successfully.")

            # Rebuild Knowledge Base for new bubble
            safe_print("   🧠 Indexing new bubble in Knowledge Base...")
            omnipkg_instance.rebuild_package_kb(
                [f"{pkg_name}=={pkg_version}"], target_python_version=python_context
            )

        final_specs.append(f"{pkg_name}=={pkg_version}")

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Smart Dependency Analysis (SKIP packages in healing plan!)
    # ═══════════════════════════════════════════════════════════
    safe_print(_("\n🔍 Analyzing script for additional dependencies..."))
    additional_packages = set()

    # CRITICAL: Track ALL packages already handled (healing plan + their normalized names)
    already_handled = set()
    for spec in final_specs:
        pkg_name = spec.split("==")[0]
        already_handled.add(pkg_name.lower())
        already_handled.add(pkg_name.lower().replace("-", "_"))
        already_handled.add(pkg_name.lower().replace("_", "-"))

    try:
        if original_script_path.exists():
            code = original_script_path.read_text(encoding="utf-8", errors="ignore")
            imports = re.findall(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", code, re.MULTILINE)

            for imp in imports:
                if is_stdlib_module(imp):
                    continue

                try:
                    pkg_name_found = convert_module_to_package_name(imp)
                    if not pkg_name_found:
                        continue

                    # CRITICAL: Check if this package is in healing plan
                    pkg_normalized = pkg_name_found.lower()

                    if pkg_normalized in already_handled:
                        safe_print(f"   ⏭️  Skipping {pkg_name_found} (already in healing plan)")
                        continue

                    # Check with alternate normalization
                    pkg_alt1 = pkg_normalized.replace("-", "_")
                    pkg_alt2 = pkg_normalized.replace("_", "-")

                    if pkg_alt1 in already_handled or pkg_alt2 in already_handled:
                        safe_print(
                            f"   ⏭️  Skipping {pkg_name_found} (already in healing plan as {pkg_alt1 or pkg_alt2})"
                        )
                        continue

                    # Not in healing plan - add it
                    additional_packages.add(pkg_name_found)

                except Exception:
                    pass

            # Don't auto-install omnipkg itself
            additional_packages.discard("omnipkg")

            if additional_packages:
                additional_list = sorted(list(additional_packages))
                safe_print(
                    _("   📦 Found additional dependencies: {}").format(", ".join(additional_list))
                )

                # Recursively create bubbles for additional dependencies
                for dep in additional_list:
                    safe_print(
                        f" -> Finding latest COMPATIBLE version for '{dep}' using background caching..."
                    )
                    dep_version = omnipkg_instance._get_latest_version_from_pypi(dep)

                    if dep_version:
                        safe_print(f"   💫 Quick compatibility check for {dep}")
                        dep_bubble = f"{dep.lower().replace('-', '_')}-{dep_version}"
                        dep_path = omnipkg_instance.multiversion_base / dep_bubble

                        if not dep_path.exists():
                            safe_print(
                                f"   📦 Creating bubble for dependency: {dep}=={dep_version}"
                            )
                            success = omnipkg_instance.bubble_manager.create_isolated_bubble(
                                dep, dep_version, python_context
                            )

                            if not success:
                                safe_print(
                                    f"   ⚠️  Failed to create bubble for {dep}=={dep_version}, continuing anyway..."
                                )
                                continue

                        final_specs.append(f"{dep}=={dep_version}")
            else:
                safe_print("   ✅ No additional dependencies needed")

    except Exception as e:
        safe_print(f"⚠️  Dependency analysis warning: {e}")

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Final Validation - Remove Duplicates (keep healing plan version)
    # ═══════════════════════════════════════════════════════════
    # Build final spec list with healing plan taking priority
    final_spec_map = {}

    # First pass: Add all specs
    for spec in final_specs:
        pkg_name = spec.split("==")[0].lower()
        pkg_version = spec.split("==")[1] if "==" in spec else None

        if pkg_name in final_spec_map:
            # Duplicate! Check if it's from healing plan
            if pkg_name in healing_plan_versions:
                # Keep healing plan version
                final_spec_map[pkg_name] = f"{pkg_name}=={healing_plan_versions[pkg_name]}"
                safe_print(
                    f"   ⚠️  Duplicate detected: {pkg_name} - using healing plan version {healing_plan_versions[pkg_name]}"
                )
            else:
                # Keep first occurrence
                safe_print(f"   ⚠️  Duplicate detected: {pkg_name} - keeping first occurrence")
        else:
            final_spec_map[pkg_name] = spec

    # Rebuild final_specs from map
    final_specs = list(final_spec_map.values())

    # ═══════════════════════════════════════════════════════════
    # STEP 4: Execute with Bubbles
    # ═══════════════════════════════════════════════════════════
    safe_print(_("\n✅ Bubbles ready. Activating: {}").format(final_specs))

    global _initial_run_time_ns

    return run_with_healing_wrapper(
        final_specs,
        original_script_path,
        original_script_args,
        config_manager,
        initial_failure_duration_ns=_initial_run_time_ns or 0,  # Pass it here
        isolation_mode="overlay",
        verbose=verbose,
    )


# -------------------------------------------------------------------
# NEW: Dispatcher and CLI Handling Logic
# ------------------------------------------------------------------------------


def _auto_inject_stdlibs(code: str) -> str:
    """
    Scans code for usage of common standard libraries (e.g. 'json.dumps', 'sys.exit')
    and injects the import if it's missing. Handles edge cases like aliases and
    attribute access patterns.
    """
    # Common stdlibs people forget to import in quick scripts
    # We only auto-inject these because they are unambiguous.
    detectable_stdlibs = {
        "sys",
        "os",
        "json",
        "re",
        "time",
        "random",
        "pathlib",
        "shutil",
        "subprocess",
        "math",
        "platform",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "typing",
        "copy",
        "glob",
        "tempfile",
        "pickle",
    }

    # Special cases: classes/functions that indicate module usage
    stdlib_indicators = {
        "Path": "pathlib",  # Path(...) without importing pathlib
        "defaultdict": "collections",
        "Counter": "collections",
        "namedtuple": "collections",
        "partial": "functools",
        "deepcopy": "copy",
    }

    injections = []

    # Check for direct module usage (e.g., "sys.argv", "os.path.join")
    for lib in detectable_stdlibs:
        # Pattern: matches "lib." but not "mylib." or inside strings
        # Also checks it's not already used as a variable name
        usage_pattern = rf'(?<!["\'])(?<!\w){lib}\.'

        if re.search(usage_pattern, code):
            # Check if already imported (handles various import styles)
            import_patterns = [
                rf"^\s*import\s+{lib}\b",  # import sys
                rf"^\s*import\s+.*\b{lib}\b.*",  # import sys, os
                rf"^\s*from\s+{lib}\s+import",  # from sys import argv
                rf"^\s*import\s+{lib}\s+as\s+\w+",  # import sys as system
            ]

            already_imported = any(
                re.search(pattern, code, re.MULTILINE) for pattern in import_patterns
            )

            if not already_imported:
                injections.append(f"import {lib}")

    # Check for class/function indicators (e.g., Path(), defaultdict())
    for indicator, lib in stdlib_indicators.items():
        # Pattern: matches "Path(" or "Path()" but not "MyPath(" or inside strings
        usage_pattern = rf'(?<!["\'])(?<!\w){indicator}\s*\('

        if re.search(usage_pattern, code):
            # Check if the specific class/function is already imported
            import_patterns = [
                # from pathlib import Path
                rf"^\s*from\s+{lib}\s+import\s+.*\b{indicator}\b",
                # import pathlib (then use pathlib.Path)
                rf"^\s*import\s+{lib}\b",
            ]

            already_imported = any(
                re.search(pattern, code, re.MULTILINE) for pattern in import_patterns
            )

            if not already_imported:
                # Import the specific class/function
                injections.append(f"from {lib} import {indicator}")

    # Remove duplicates while preserving order
    seen = set()
    unique_injections = []
    for imp in injections:
        if imp not in seen:
            seen.add(imp)
            unique_injections.append(imp)

    if unique_injections:
        # Find where to insert imports (after shebang/encoding/docstring if present)
        lines = code.split("\n")
        insert_index = 0

        # Skip shebang
        if lines and lines[0].startswith("#!"):
            insert_index = 1

        # Skip encoding declaration
        if insert_index < len(lines) and "coding" in lines[insert_index]:
            insert_index += 1

        # Skip module docstring
        if insert_index < len(lines):
            # Check for triple-quoted string at the start
            rest_of_file = "\n".join(lines[insert_index:])
            docstring_match = re.match(r'^\s*("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', rest_of_file)
            if docstring_match:
                # Count newlines in the docstring to skip past it
                docstring_lines = docstring_match.group(1).count("\n")
                insert_index += docstring_lines + 1

        # Insert the imports at the appropriate location
        header = "\n".join(unique_injections)

        if insert_index == 0:
            return f"{header}\n{code}"
        else:
            before = "\n".join(lines[:insert_index])
            after = "\n".join(lines[insert_index:])
            return f"{before}\n{header}\n{after}"

    return code


def _handle_cli_execution(command, args, config_manager, omnipkg_core):
    """Enhanced CLI execution with unified healing analysis and performance stats."""

    # 1. Try to run the command
    error_output = ""
    start_time_ns = time.perf_counter_ns()

    if shutil.which(command):
        try:
            result = subprocess.run(
                [command] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            # If success, just print output and exit
            if result.returncode == 0:
                print(result.stdout, end="")
                return 0

            # If failed, capture output for analysis
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            error_output = result.stderr + "\n" + result.stdout
            safe_print(
                f"\n⚠️  Command '{command}' failed (exit code {result.returncode}). Starting Auto-Healer..."
            )

        except Exception as e:
            safe_print(f"\n⚠️  Execution failed: {e}")
            return 1
    else:
        # Command not found in PATH
        safe_print(f"🔍 Command '{command}' not found in PATH. Checking Knowledge Base...")

    # Stop the timer for the failed run
    end_time_ns = time.perf_counter_ns()
    initial_failure_duration_ns = end_time_ns - start_time_ns

    # 2. Identify owner package
    owning_package = omnipkg_core.get_package_for_command(command)
    if not owning_package:
        safe_print(f"❌ Unknown command '{command}'.")
        return 1

    # 3. Build initial healing plan with owner package
    active_version = omnipkg_core.get_active_version(owning_package)
    owner_spec = f"{owning_package}=={active_version}" if active_version else owning_package

    # 4. Analyze error, heal, and re-run
    if error_output:
        safe_print("🔍 Analyzing error output to build comprehensive healing plan...")

        # ✅ Pass empty set for initial CLI run and omnipkg_core instance
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            error_output,
            [command] + args,
            Path.cwd(),
            config_manager,
            is_context_aware_run=False,
            cli_owner_spec=owner_spec,
            omnipkg_instance=omnipkg_core,  # ✅ Pass the omnipkg instance
            verbose=False,
            attempted_fixes=set(),  # ✅ Initialize with empty set
            initial_failure_duration_ns=initial_failure_duration_ns,
        )

        # ✅ FIX: If healing failed, show the user the error output
        if exit_code != 0:
            safe_print(f"\n❌ Auto-healing failed for command '{command}'.")
            safe_print("\n📄 ERROR OUTPUT:")
            print(error_output)
        else:
            # Print performance stats if healing succeeded
            if heal_stats:
                # DETECT SHELL HERE
                shell_name = _detect_shell_name()
                _print_performance_comparison(
                    initial_failure_duration_ns, heal_stats, runner_name=shell_name
                )

        return exit_code
    else:
        safe_print("❌ Command failed but no error output to analyze.")
        return 1


_initial_run_time_ns = None


def _run_script_with_healing(
    script_path,
    script_args,
    config_manager,
    original_script_path_for_analysis,
    heal_type="execution",
    is_context_aware_run=False,
    omnipkg_instance=None,
    attempted_fixes=None,
):
    """
    Common function to run a script and automatically heal any failures.
    Can inject the original script's directory into PYTHONPATH for local imports.

    CRITICAL FIXES:
    1. Always passes [script_path, ...args] to healer (never the interpreter)
    2. Properly handles interactive script failures
    3. Shows error output when healing fails
    4. Correctly defines and uses _initial_run_time_ns for performance stats.
    """
    # Reference the module-level variable to ensure we are modifying the same
    # instance throughout the healing process.
    global _initial_run_time_ns

    if attempted_fixes is None:
        attempted_fixes = set()

    python_exe = config_manager.config.get("python_executable", sys.executable)
    run_cmd = [python_exe, str(script_path)] + script_args

    # --- CONTEXT INJECTION LOGIC ---
    env = os.environ.copy()
    if is_context_aware_run:
        project_dir = original_script_path_for_analysis.parent
        if heal_type == "local_context_run":
            safe_print(
                _("   - Injecting project directory into PYTHONPATH: {}").format(project_dir)
            )

        current_python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{project_dir}{os.pathsep}{current_python_path}"

    start_time_ns = time.perf_counter_ns()

    # PHASE 1: Quick test run to detect if script is interactive or has errors
    safe_print("🔍 Testing script for interactivity and errors...")

    test_process = subprocess.Popen(
        run_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=Path.cwd(),
        env=env,
    )

    # Give the script a brief moment to start and show any immediate output
    try:
        output, _stderr = test_process.communicate(timeout=2)
        test_return_code = test_process.returncode
    except subprocess.TimeoutExpired:
        # Script is likely interactive or long-running
        test_process.terminate()
        try:
            test_process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            test_process.kill()
            test_process.wait()

        safe_print("📱 Interactive script detected - switching to direct mode...")

        # PHASE 2: Run interactively for interactive scripts
        try:
            interactive_process = subprocess.Popen(
                run_cmd,
                stdin=None,  # Use parent's stdin directly
                stdout=None,  # Use parent's stdout directly
                stderr=None,  # Use parent's stderr directly
                cwd=Path.cwd(),
                env=env,
            )

            return_code = interactive_process.wait()

            # ✅ FIX: Handle interactive script failures
            if return_code != 0:
                safe_print(f"\n❌ Interactive script crashed (Exit Code {return_code}).")

                # Capture the runtime of this FIRST failure, if not already captured.
                if _initial_run_time_ns is None:
                    _initial_run_time_ns = time.perf_counter_ns() - start_time_ns

                safe_print("🔍 Re-running in capture mode to analyze the error for healing...")

                # Re-run purely to capture the error (user won't see this run, it's for the healer)
                capture_process = subprocess.Popen(
                    run_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=Path.cwd(),
                    env=env,
                )

                try:
                    full_output, _stderr = capture_process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    capture_process.kill()
                    full_output = "Error: Could not capture output (timeout)"

                # ✅ CRITICAL FIX: Pass script_path as first arg, NOT the interpreter
                return analyze_runtime_failure_and_heal(
                    full_output,
                    [str(script_path)] + script_args,  # ✅ Script path first, not interpreter
                    original_script_path_for_analysis,
                    config_manager,
                    is_context_aware_run,
                    omnipkg_instance=omnipkg_instance,
                    attempted_fixes=attempted_fixes,
                )

            # Interactive script succeeded
            end_time_ns = time.perf_counter_ns()

            heal_stats = {
                "total_swap_time_ns": end_time_ns - start_time_ns,
                "activation_time_ns": 0,
                "deactivation_time_ns": 0,
                "type": heal_type,
            }

            # Show success message for interactive scripts
            # This check now works because _initial_run_time_ns is defined.
            if _initial_run_time_ns:
                safe_print("\n" + "🎯 " + "=" * 60)
                safe_print("🚀 SUCCESS! Auto-healing completed.")
                _print_performance_comparison(_initial_run_time_ns, heal_stats)
                safe_print("🎮 Interactive script completed successfully...")
                safe_print("=" * 68 + "\n")

            return return_code, heal_stats

        except KeyboardInterrupt:
            safe_print("\n🛑 Interactive process interrupted by user")
            interactive_process.terminate()
            interactive_process.wait()
            return 130, None

    # PHASE 3: Handle non-interactive scripts or scripts with errors
    if test_return_code != 0:
        # Script failed, analyze the error
        safe_print(f"❌ Script failed with return code {test_return_code}")

        # Capture the runtime of this FIRST failure, if not already captured.
        if _initial_run_time_ns is None:
            _initial_run_time_ns = time.perf_counter_ns() - start_time_ns

        # ✅ CRITICAL FIX: Pass script_path as first arg, NOT the interpreter
        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            output,
            # ✅ Script path first, not interpreter
            [str(script_path)] + script_args,
            original_script_path_for_analysis,
            config_manager,
            is_context_aware_run,
            omnipkg_instance=omnipkg_instance,
            attempted_fixes=attempted_fixes,
        )

        # ✅ FIX: If healing failed, show the error output
        if exit_code != 0:
            safe_print("\n📄 SCRIPT ERROR OUTPUT:")
            safe_print(output)

        return exit_code, heal_stats

    # Script completed successfully and non-interactively
    end_time_ns = time.perf_counter_ns()
    heal_stats = {
        "total_swap_time_ns": end_time_ns - start_time_ns,
        "activation_time_ns": 0,
        "deactivation_time_ns": 0,
        "type": heal_type,
    }

    # Show any output that was captured
    if output:
        safe_print(output, end="")

    # Show success stats if this was a healed run
    # This check now works correctly.
    if _initial_run_time_ns:
        safe_print("\n" + "🎯 " + "=" * 60)
        safe_print("🚀 SUCCESS! Auto-healing completed.")
        _print_performance_comparison(_initial_run_time_ns, heal_stats)
        safe_print("✅ Script executed successfully...")
        safe_print("=" * 68 + "\n")
    else:
        # This branch handles the case where the script succeeded on the very first try.
        safe_print("\n" + "=" * 60)
        safe_print("✅ Script executed successfully on the first attempt.")
        safe_print("=" * 60)

    return test_return_code, heal_stats


def _print_performance_comparison(initial_ns, heal_stats, runner_name="UV"):
    """Prints the final performance summary comparing Runner failure time to omnipkg execution time."""
    if not heal_stats:
        return

    initial_ns_from_stats = heal_stats.get("initial_failure_duration_ns", initial_ns)
    if not initial_ns_from_stats:
        return

    failure_time_ms = initial_ns_from_stats / 1_000_000

    # Use 'activation_time_ns' for bubbles.
    if heal_stats.get("type") == "package_install":
        execution_time_ms = heal_stats["total_swap_time_ns"] / 1_000_000
        execution_ns = heal_stats["total_swap_time_ns"]
        omni_label = "omnipkg Execution"
    else:
        # Use activation only (Fair comparison)
        ns_val = heal_stats.get("activation_time_ns", heal_stats["total_swap_time_ns"])
        execution_time_ms = ns_val / 1_000_000
        execution_ns = int(ns_val)
        omni_label = "omnipkg Activation"

    if execution_time_ms <= 0:
        return

    speed_ratio = failure_time_ms / execution_time_ms
    speed_percentage = ((failure_time_ms - execution_time_ms) / execution_time_ms) * 100

    runner_upper = runner_name.upper()
    runner_label = f"{runner_name} Failed Run"

    # --- TABLE ALIGNMENT LOGIC ---
    max_label_width = max(len(runner_label), len(omni_label))
    row_fmt = f"{{:<{max_label_width}}} : {{:>10.3f}} ms  ({{:>15,}} ns)"

    safe_print("\n" + "=" * 70)
    safe_print(f"🚀 PERFORMANCE COMPARISON: {runner_upper} vs OMNIPKG")
    safe_print("=" * 70)

    safe_print(row_fmt.format(runner_label, failure_time_ms, initial_ns))
    safe_print(row_fmt.format(omni_label, execution_time_ms, execution_ns))

    safe_print("-" * 70)

    # --- SUMMARY ALIGNMENT LOGIC ---
    # 1. Format the numbers based on magnitude (preserving original logic)
    if speed_ratio >= 1000:
        ratio_str = f"{speed_ratio:>10.0f}"
    elif speed_ratio >= 100:
        ratio_str = f"{speed_ratio:>10.1f}"
    else:
        ratio_str = f"{speed_ratio:>10.2f}"

    if speed_percentage >= 10000:
        perc_str = f"{speed_percentage:>10.0f}"
    elif speed_percentage >= 1000:
        perc_str = f"{speed_percentage:>10.1f}"
    else:
        perc_str = f"{speed_percentage:>10.2f}"

    # 2. Print with fixed label width (15 chars covers "🎯 omnipkg is" and padding for "💥 That's")
    summary_label_width = 15

    # FIX: Extract the label strings first to avoid backslash in f-string
    label1 = "🎯 omnipkg is"
    label2 = "💥 That's"

    safe_print(f"{label1:<{summary_label_width}}{ratio_str}x FASTER than {runner_name}!")
    safe_print(f"{label2:<{summary_label_width}}{perc_str}% improvement!")

    safe_print("=" * 70)
    safe_print("🌟 Same environment, zero downtime, microsecond swapping!")
    safe_print("=" * 70 + "\n")


def _detect_shell_name():
    """Detects the likely shell name for display purposes."""
    # 1. Check common shell env var (Linux/Mac)
    shell_path = os.environ.get("SHELL")
    if shell_path:
        return Path(shell_path).stem.capitalize()  # e.g., /bin/bash -> Bash

    # 2. Check ComSpec (Windows CMD)
    comspec = os.environ.get("COMSPEC")
    if comspec:
        return "CMD"

    # 3. Check for PowerShell indicators
    if "PSModulePath" in os.environ:
        return "PowerShell"

    return "System"


def run_with_healing_wrapper(
    required_specs,
    original_script_path,
    original_script_args,
    config_manager,
    initial_failure_duration_ns,
    isolation_mode="strict",
    verbose=False,
):
    """
    Generates and executes the temporary wrapper script. This version creates a
    robust sys.path in the subprocess, enabling it to find both the omnipkg
    source and its installed dependencies like 'packaging'.

    NOW ACCEPTS: A list of package specs like ["numpy==1.26.4", "pandas==2.0.0"]
    """
    import site

    # Convert single string to list for backward compatibility
    if isinstance(required_specs, str):
        required_specs = [required_specs]

    if verbose:
        safe_print("\n🔍 PRE-WRAPPER DEBUGGING:")
        safe_print(f"   Current Python executable: {sys.executable}")
        safe_print(f"   Current working directory: {os.getcwd()}")
        safe_print(f"   Project root: {project_root}")

    # Check if packaging is available in current process
    try:
        import packaging

        safe_print(f"   ✅ packaging found at: {packaging.__file__}")
    except ImportError as e:
        safe_print(f"   ❌ packaging not available in current process: {e}")

    # Get all possible site-packages paths
    site_packages_paths = []

    # From config
    config_site_packages = config_manager.config.get("site_packages_path")
    if config_site_packages:
        site_packages_paths.append(config_site_packages)
        if verbose:
            safe_print(f"   Config site-packages: {config_site_packages}")

    # From site module
    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)
            if verbose:
                safe_print(f"   Site getsitepackages: {path}")

    # From site.USER_SITE
    if hasattr(site, "USER_SITE") and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)
            if verbose:
                safe_print(f"   Site USER_SITE: {site.USER_SITE}")

    # Check current sys.path for site-packages
    for path in sys.path:
        if "site-packages" in path and path not in site_packages_paths:
            site_packages_paths.append(path)
            if verbose:
                safe_print(f"   Current sys.path site-packages: {path}")

    # Check each site-packages path for packaging module
    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, "packaging")
            packaging_init = os.path.join(sp_path, "packaging", "__init__.py")
            if os.path.exists(packaging_path) and os.path.exists(packaging_init):
                packaging_locations.append(sp_path)
                if verbose:
                    safe_print(f"   📦 packaging found in: {sp_path}")
        else:
            if verbose:
                safe_print(f"   ❌ site-packages path doesn't exist: {sp_path}")

    if not packaging_locations:
        safe_print("   ⚠️  WARNING: No packaging module found in any site-packages!")

    # Use the first site-packages path that has packaging, or fallback to config
    site_packages_path = packaging_locations[0] if packaging_locations else config_site_packages
    if not site_packages_path and site_packages_paths:
        site_packages_path = site_packages_paths[0]

    safe_print(f"   🎯 Selected site-packages for wrapper: {site_packages_path}")

    # Build the nested loaders structure OUTSIDE the wrapper
    nested_loaders_str = ""
    indentation = "    "  # Start with 4 spaces for inside the try block
    for spec in required_specs:
        pkg_name = re.sub(r"[^a-zA-Z0-9_]", "_", spec.split("==")[0])
        nested_loaders_str += f"{indentation}with omnipkgLoader('{spec}', config=config, isolation_mode='{isolation_mode}', force_activation=True) as loader_{pkg_name}:\n"
        nested_loaders_str += f"{indentation}    loader_instances.append(loader_{pkg_name})\n"
        indentation += "    "

    # The code that runs at the deepest nesting level
    run_script_code = textwrap.indent(
        f"""\
local_project_path = r"{str(original_script_path.parent)}"
if local_project_path not in sys.path:
    sys.path.insert(0, local_project_path)
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
safe_print(f"\\n🚀 Running target script inside the combined bubble + local context...")
sys.argv = [{str(original_script_path)!r}] + {original_script_args!r}
runpy.run_path({str(original_script_path)!r}, run_name="__main__")
""",
        prefix=indentation,
    )

    full_loader_block = nested_loaders_str + run_script_code

    # Enhanced wrapper content with comprehensive debugging
    # NOTE: Using .format() at the END to inject the full_loader_block
    wrapper_template = """\
import sys, os, runpy, json, re
from pathlib import Path
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print

# --- VERBOSITY CONTROL ---
VERBOSE_MODE = {verbose_flag}

def vprint(msg):
    '''Only print if verbose mode is enabled'''
    if VERBOSE_MODE:
        safe_print(msg)

# DEBUGGING: Show initial state (only if verbose)
vprint("🔍 WRAPPER SUBPROCESS DEBUGGING:")
vprint(f"   Python executable: {{sys.executable}}")
vprint(f"   Initial sys.path length: {{len(sys.path)}}")
vprint(f"   Working directory: {{os.getcwd()}}")

# Show first few sys.path entries
if VERBOSE_MODE:
    for i, path in enumerate(sys.path[:5]):
        vprint(f"   sys.path[{{i}}]: {{path}}")
    if len(sys.path) > 5:
        vprint(f"   ... and {{len(sys.path) - 5}} more entries")

# --- COMPLETE SYS.PATH INJECTION ---
project_root_path = r"{project_root_path}"
main_site_packages = r"{site_packages_path}"

vprint(f"\\n   🔧 Adding project root: {{project_root_path}}")
if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)
    vprint(f"      ✅ Added to sys.path[0]")
else:
    vprint(f"      ⚠️  Already in sys.path")

vprint(f"   🔧 Adding site-packages: {{main_site_packages}}")
if main_site_packages and main_site_packages not in sys.path:
    sys.path.insert(1, main_site_packages)
    vprint(f"      ✅ Added to sys.path[1]")
else:
    vprint(f"      ⚠️  Already in sys.path or None")

# Add all potential site-packages paths
additional_paths = {additional_paths_repr}
vprint(f"   🔧 Adding {{len(additional_paths)}} additional paths...")
for add_path in additional_paths:
    if add_path and os.path.exists(add_path) and add_path not in sys.path:
        sys.path.append(add_path)
        vprint(f"      ✅ Added: {{add_path}}")

vprint(f"\\n   📊 Final sys.path length: {{len(sys.path)}}")

# Test critical imports before proceeding
vprint("\\n   🧪 Testing critical imports...")

# Test packaging import
try:
    import packaging
    vprint(f"      ✅ packaging: {{packaging.__file__}}")
except ImportError as e:
    vprint(f"      ❌ packaging failed: {{e}}")
    if VERBOSE_MODE:
        safe_print("      🔍 Searching for packaging in sys.path...")
        for i, path in enumerate(sys.path):
            packaging_path = os.path.join(path, 'packaging')
            if os.path.exists(packaging_path):
                safe_print(f"         Found packaging dir in sys.path[{{i}}]: {{path}}")
                init_file = os.path.join(packaging_path, '__init__.py')
                safe_print(f"         __init__.py exists: {{os.path.exists(init_file)}}")

# Test omnipkg imports
try:
    from omnipkg.loader import omnipkgLoader
    vprint(f"      ✅ omnipkgLoader imported")
except ImportError as e:
    vprint(f"      ❌ omnipkgLoader failed: {{e}}")
    
try:
    from omnipkg.i18n import _
    vprint(f"      ✅ omnipkg.i18n imported")
except ImportError as e:
    vprint(f"      ❌ omnipkg.i18n failed: {{e}}")
# --- END OF PATH INJECTION ---

# With a correct path, these imports will now succeed.
try:
    from omnipkg.loader import omnipkgLoader
    from omnipkg.i18n import _
except ImportError as e:
    # This is a fallback error for debugging if the path injection fails.
    safe_print(f"\\nFATAL: Could not import omnipkg modules after path setup. Error: {{e}}")
    safe_print(f"\\nDEBUG: Final sys.path ({{len(sys.path)}} entries):")
    for i, path in enumerate(sys.path):
        exists = "✅" if os.path.exists(path) else "❌"
        safe_print(f"   [{{i:2d}}] {{exists}} {{path}}")
    
    # Check for omnipkg specifically
    safe_print(f"\\nDEBUG: Checking for omnipkg module...")
    for i, path in enumerate(sys.path):
        omnipkg_path = os.path.join(path, 'omnipkg')
        if os.path.exists(omnipkg_path):
            safe_print(f"   Found omnipkg dir in sys.path[{{i}}]: {{path}}")
            loader_file = os.path.join(omnipkg_path, 'loader.py')
            safe_print(f"   loader.py exists: {{os.path.exists(loader_file)}}")
    sys.exit(1)

lang_from_env = os.environ.get('OMNIPKG_LANG')
if lang_from_env: _.set_language(lang_from_env)

config = json.loads(r'''{config_json}''')
loader_instances = []

safe_print(f"\\n🌀 omnipkg auto-heal: Wrapping script with loaders for {required_specs_repr}...")
safe_print('-' * 60)

try:
{FULL_LOADER_BLOCK_PLACEHOLDER}
except Exception:
    import traceback

    traceback.print_exc()
    sys.exit(1)
finally:
    # --- FIX: AGGREGATE STATS FROM ALL LAYERS ---
    if loader_instances:
        total_activation_ns = 0
        last_stats = None
        
        for loader in loader_instances:
            if loader is None:
                continue
            
            stats = loader.get_performance_stats()
            if stats:
                total_activation_ns += stats.get('activation_time_ns', 0)
                last_stats = stats
        
        if last_stats:
            last_stats['activation_time_ns'] = total_activation_ns
            # ADD THE INITIAL FAILURE TIME TO THE STATS
            last_stats['initial_failure_duration_ns'] = {initial_failure_duration_ns}
            print(f"OMNIPKG_STATS_JSON:{{json.dumps(last_stats)}}", flush=True)

safe_print('-' * 60)
safe_print(_("✅ Script completed successfully inside omnipkg bubble."))
"""

    # Now inject all the variables using .format()
    wrapper_content = wrapper_template.format(
        verbose_flag=str(verbose),
        project_root_path=project_root,
        site_packages_path=site_packages_path,
        additional_paths_repr=repr(site_packages_paths),
        config_json=json.dumps(config_manager.config),
        required_specs_repr=repr(required_specs),
        FULL_LOADER_BLOCK_PLACEHOLDER=full_loader_block,
        initial_failure_duration_ns=initial_failure_duration_ns,
    )

    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(wrapper_content)
            temp_script_path = f.name

        safe_print(f"   💾 Temporary wrapper script: {temp_script_path}")

        heal_command = [
            config_manager.config.get("python_executable", sys.executable),
            temp_script_path,
        ]
        safe_print(_("\n🚀 Re-running with omnipkg auto-heal..."))

        process = subprocess.Popen(
            heal_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        output_lines = []
        for line in iter(process.stdout.readline, ""):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                safe_print(line, end="")
            output_lines.append(line)

        return_code = process.wait()
        heal_stats = None

        full_output = "".join(output_lines)
        for line in full_output.splitlines():
            if line.startswith("OMNIPKG_STATS_JSON:"):
                try:
                    stats_json = line.split(":", 1)[1]
                    heal_stats = json.loads(stats_json)
                    break
                except (IndexError, json.JSONDecodeError):
                    continue

        return return_code, heal_stats
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)


def execute_run_command(
    cmd_args: list,
    config_manager: ConfigManager,
    verbose: bool = False,
    omnipkg_core=None,
):
    """
    Enhanced to properly handle Python stdin mode and ensure healing works correctly.
    """
    # ADD THIS LINE - Propagate verbose flag to subprocesses
    if verbose:
        os.environ["OMNIPKG_VERBOSE"] = "1"

    from omnipkg.i18n import _

    if not cmd_args:
        safe_print(_("❌ Error: No script or command specified to run."))
        return 1

    # 1. Reuse provided Core or Initialize if missing
    if omnipkg_core is None:
        omnipkg_core = OmnipkgCore(config_manager)

    target = cmd_args[0]
    target_path = Path(target)

    # BRANCH 0: Python Inline Command (-c flag)
    if target in ["python", "python3", "python.exe"] and "-c" in cmd_args:
        try:
            c_index = cmd_args.index("-c")
            if c_index + 1 < len(cmd_args):
                code_string = cmd_args[c_index + 1]
                script_args = cmd_args[c_index + 2 :]
                safe_print("✨ Detected inline Python code. Converting to virtual script...")
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as f:
                    f.write(code_string)
                    temp_source_path = Path(f.name)
                try:
                    return _run_script_logic(
                        temp_source_path,
                        script_args,
                        config_manager,
                        verbose,
                        omnipkg_core=omnipkg_core,
                    )
                finally:
                    if temp_source_path.exists():
                        temp_source_path.unlink()
        except ValueError:
            pass

    # BRANCH 0.5: Python stdin mode (python - << 'EOF' or echo "code" | python -)
    # CRITICAL: Check for '-' argument OR if target is python with single arg AND stdin is not a TTY
    elif target in ["python", "python3", "python.exe"]:
        is_stdin_mode = False
        script_args = []

        # Check if '-' is explicitly passed
        if len(cmd_args) > 1 and cmd_args[1] == "-":
            is_stdin_mode = True
            script_args = cmd_args[2:]  # Everything after '-'
        # Check if only 'python' with no other args and stdin has data
        elif len(cmd_args) == 1 and not sys.stdin.isatty():
            is_stdin_mode = True
            script_args = []

        if is_stdin_mode:
            safe_print("🐍 Detected Python stdin mode. Reading from stdin...")

            # Read ALL stdin content
            try:
                stdin_content = sys.stdin.read()
            except Exception as e:
                safe_print(f"❌ Failed to read stdin: {e}")
                return 1

            if not stdin_content.strip():
                safe_print("❌ No input provided via stdin")
                return 1

            # Write to temp file with a stable name for error tracking
            temp_script_path = None
            try:
                # Use a more stable temp file that can be referenced in errors
                temp_dir = Path(tempfile.gettempdir()) / "omnipkg_stdin"
                temp_dir.mkdir(exist_ok=True)

                # Create unique but trackable filename
                timestamp = int(time.time() * 1000)
                temp_script_path = temp_dir / f"stdin_script_{timestamp}.py"

                with open(temp_script_path, "w", encoding="utf-8") as f:
                    f.write(stdin_content)

                safe_print(f"📝 Saved stdin to temporary script: {temp_script_path}")

                # CRITICAL: Run with _run_script_logic which has proper healing support
                # Pass script_args (everything after '-' if present)
                return _run_script_logic(
                    temp_script_path,
                    script_args,
                    config_manager,
                    verbose,
                    omnipkg_core=omnipkg_core,
                )

            finally:
                # Clean up temp file after execution
                if temp_script_path and temp_script_path.exists():
                    try:
                        temp_script_path.unlink()
                    except:
                        pass  # Ignore cleanup errors

    # BRANCH 1: Python Script (.py file)
    if (target_path.exists() and target_path.is_file()) or target.endswith(".py"):
        return _run_script_logic(
            target_path.resolve(),
            cmd_args[1:],
            config_manager,
            verbose,
            omnipkg_core=omnipkg_core,
        )

    # BRANCH 2: CLI Command (executables in PATH or registered with omnipkg)
    elif shutil.which(target) or omnipkg_core.get_package_for_command(target):
        return _handle_cli_execution(target, cmd_args[1:], config_manager, omnipkg_core)

    else:
        safe_print(
            _(
                "❌ Error: Target '{}' is neither a valid script file nor a recognized command."
            ).format(target)
        )
        return 1


def run_cli_with_healing_wrapper(
    required_specs, command, command_args, config_manager, initial_failure_duration_ns=0
):
    """
    Like run_with_healing_wrapper, but for CLI binaries instead of Python scripts.
    Uses the SAME robust nested loader logic.
    """
    import site

    if isinstance(required_specs, str):
        required_specs = [required_specs]

    # Get site-packages (same logic as your script wrapper)
    site_packages_paths = []
    config_site_packages = config_manager.config.get("site_packages_path")
    if config_site_packages:
        site_packages_paths.append(config_site_packages)

    for path in site.getsitepackages():
        if path not in site_packages_paths:
            site_packages_paths.append(path)

    if hasattr(site, "USER_SITE") and site.USER_SITE:
        if site.USER_SITE not in site_packages_paths:
            site_packages_paths.append(site.USER_SITE)

    packaging_locations = []
    for sp_path in site_packages_paths:
        if os.path.exists(sp_path):
            packaging_path = os.path.join(sp_path, "packaging")
            if os.path.exists(packaging_path):
                packaging_locations.append(sp_path)

    site_packages_path = packaging_locations[0] if packaging_locations else config_site_packages

    # Build nested loaders (SAME as your script version)
    nested_loaders_str = ""
    indentation = "    "
    for spec in required_specs:
        pkg_name = re.sub(r"[^a-zA-Z0-9_]", "_", spec.split("==")[0])
        nested_loaders_str += f"{indentation}with omnipkgLoader('{spec}', config=config, isolation_mode='overlay', force_activation=True) as loader_{pkg_name}:\n"
        nested_loaders_str += f"{indentation}    loader_instances.append(loader_{pkg_name})\n"
        indentation += "    "

    # FIX 1: Added 'f' prefix to interpolate command/args and escape inner braces
    run_cli_code = textwrap.indent(
        f"""\
try:
    from omnipkg.common_utils import safe_print
except ImportError:
    def safe_print(msg, **kwargs): print(msg, **kwargs)

env = os.environ.copy()
env['PYTHONPATH'] = os.pathsep.join(sys.path)

cmd_path = shutil.which({command!r}) or {command!r}
full_cmd = [cmd_path] + {command_args!r}

safe_print(f"🚀 Re-launching '{{cmd_path}}' in healed environment...")
safe_print("-" * 60)

try:
    sys.stdout.flush()
    sys.stderr.flush()
    result = subprocess.run(full_cmd, env=env)
    sys.exit(result.returncode)
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)
except Exception as e:
    safe_print(f"❌ Error: {{e}}")
    sys.exit(1)
""",
        prefix=indentation,
    )

    full_loader_block = nested_loaders_str + run_cli_code
    indented_loader_block = textwrap.indent(full_loader_block, "    ")

    # FIX 2: Added 'f' prefix to interpolate variables like {indented_loader_block}
    wrapper_content = f"""\
import sys, os, subprocess, shutil, json, re
from pathlib import Path

# Path injection (same as your script wrapper)
project_root_path = r"{project_root}"
main_site_packages = r"{site_packages_path}"

if project_root_path not in sys.path:
    sys.path.insert(0, project_root_path)
if main_site_packages and main_site_packages not in sys.path:
    sys.path.insert(1, main_site_packages)

additional_paths = {site_packages_paths!r}
for add_path in additional_paths:
    if add_path and os.path.exists(add_path) and add_path not in sys.path:
        sys.path.append(add_path)

try:
    from omnipkg.common_utils import safe_print
except ImportError:
    def safe_print(msg, **kwargs): print(msg, **kwargs)

try:
    # CRITICAL: Export current sys.path to PYTHONPATH so subprocesses (like Flask) inherit it
    os.environ['PYTHONPATH'] = os.pathsep.join(sys.path)
    
    from omnipkg.loader import omnipkgLoader
    from omnipkg.i18n import _
except ImportError as e:
    safe_print(f"FATAL: Could not import omnipkg modules. Error: {{e}}")
    sys.exit(1)

config = json.loads(r'''{json.dumps(config_manager.config)}''')
loader_instances = []

safe_print(f"🌀 omnipkg auto-heal: Wrapping CLI with loaders for {required_specs!r}...")
safe_print('-' * 60)

try:
{indented_loader_block}
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    if loader_instances:
        total_activation_ns = 0
        last_stats = None
        
        for loader in loader_instances:
            if loader is None:
                continue
            stats = loader.get_performance_stats()
            if stats:
                total_activation_ns += stats.get('activation_time_ns', 0)
                last_stats = stats
        
        if last_stats:
            last_stats['activation_time_ns'] = total_activation_ns
            last_stats['initial_failure_duration_ns'] = {initial_failure_duration_ns}
            safe_print(f"OMNIPKG_STATS_JSON:{{json.dumps(last_stats)}}", flush=True)

safe_print('-' * 60)
safe_print("✅ CLI command completed successfully inside omnipkg bubble.")
"""

    # Execute wrapper (same as your script version)
    temp_script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(wrapper_content)
            temp_script_path = f.name

        heal_command = [
            config_manager.config.get("python_executable", sys.executable),
            temp_script_path,
        ]

        process = subprocess.Popen(
            heal_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        output_lines = []
        for line in iter(process.stdout.readline, ""):
            if not line.startswith("OMNIPKG_STATS_JSON:"):
                safe_print(line, end="")
            output_lines.append(line)

        return_code = process.wait()
        heal_stats = None

        full_output = "".join(output_lines)
        for line in full_output.splitlines():
            if line.startswith("OMNIPKG_STATS_JSON:"):
                try:
                    stats_json = line.split(":", 1)[1]
                    heal_stats = json.loads(stats_json)
                except:
                    pass

        return return_code, heal_stats
    finally:
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)

def _run_script_logic(
    source_script_path: Path,
    script_args: list,
    config_manager: ConfigManager,
    verbose: bool = False,
    omnipkg_core=None,
):
    """Main script execution logic with robust interactive handling."""
    if not omnipkg_core:
        omnipkg_core = OmnipkgCore(config_manager)

    if not source_script_path.exists():
        safe_print(_("❌ Error: Script not found at '{}'").format(source_script_path))
        return 1

    temp_script_path = None
    try:
        # 1. Read the original code
        code_str = source_script_path.read_text(encoding="utf-8")

        # Auto-inject missing standard library imports
        code_str = _auto_inject_stdlibs(code_str)

        # Heal AI hallucinations
        healed_code = heal_code_string(code_str, verbose=verbose)

        # Patch Flask
        if auto_patch_flask_port:
            final_code = auto_patch_flask_port(healed_code)
        else:
            final_code = healed_code

        # Write to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as temp_script:
            temp_script_path = Path(temp_script.name)
            temp_script.write(final_code)

        safe_cmd_args = [str(temp_script_path)] + script_args

        safe_print(_("🔄 Syncing omnipkg context..."))
        sync_context_to_runtime()
        safe_print(_("✅ Context synchronized."))

        python_exe = config_manager.config.get("python_executable", sys.executable)

        # Set environment
        env = os.environ.copy()
        env["PYTHONWARNINGS"] = (
            "ignore::DeprecationWarning:pkg_resources,ignore::UserWarning:pkg_resources"
        )

        # === EXECUTION STRATEGY SELECTION ===
        # Strategy A: Use 'uv run' if on Python 3.8+ (fast, isolated)
        # Strategy B: Use direct 'python' if on Python < 3.8 (uv not supported)
        
        initial_cmd = []
        
        if sys.version_info >= (3, 8):
            # Try using uv if available
            uv_path = shutil.which("uv")
            if uv_path:
                initial_cmd = [
                    "uv",
                    "run",
                    "--no-project",
                    "--python",
                    python_exe,
                    "--",
                ] + safe_cmd_args
        
        # Fallback if uv not used/available
        if not initial_cmd:
            initial_cmd = [python_exe] + safe_cmd_args

        # =========================================================================
        # DIRECT EXECUTION (Interactive Friendly)
        # =========================================================================
        safe_print(_("🚀 Executing script directly..."))
        start_time_ns = time.perf_counter_ns()

        # 1. Run interactively attached to terminal
        try:
            direct_process = subprocess.Popen(
                initial_cmd,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=Path.cwd(),
                env=env,
            )
        except FileNotFoundError:
            # Fallback for when 'uv' command fails completely (e.g. not in path)
            if initial_cmd[0] == "uv":
                safe_print("⚠️  'uv' not found, falling back to direct python execution...")
                initial_cmd = [python_exe] + safe_cmd_args
                direct_process = subprocess.Popen(
                    initial_cmd,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    cwd=Path.cwd(),
                    env=env,
                )
            else:
                raise

        try:
            return_code = direct_process.wait()
            end_time_ns = time.perf_counter_ns()
            failure_duration_ns = end_time_ns - start_time_ns

            full_output = ""

            # 2. If failed, re-run in capture mode (Silent) to get error for Healer
            if return_code != 0:
                safe_print(f"\n❌ Script exited with code: {return_code}")
                safe_print("🤖 [AI-INFO] Attempting to capture error log for healing...")

                capture_process = subprocess.Popen(
                    initial_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,  # Pipe empty stdin to prevent hangs
                    text=True,
                    encoding="utf-8",
                    cwd=Path.cwd(),
                    env=env,
                )
                try:
                    full_output, _ignored = capture_process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    capture_process.kill()
                    full_output = (
                        "Error: Script crashed interactively but timed out during error capture."
                    )

        except KeyboardInterrupt:
            safe_print("\n🛑 Process interrupted by user")
            direct_process.terminate()
            direct_process.wait()
            return 130

        # =========================================================================
        # ANALYSIS & HEALING
        # =========================================================================

        # Cleanup output for analysis
        filtered_lines = []
        skip_next = False
        for line in full_output.split("\n"):
            if "pkg_resources" in line or "UserWarning" in line:
                skip_next = True
                continue
            if skip_next and line.strip().startswith("from pkg_resources"):
                skip_next = False
                continue
            skip_next = False
            filtered_lines.append(line)
        cleaned_output = "\n".join(filtered_lines)

        # Basic Success Check
        if return_code == 0:
            safe_print("\n✅ Script executed successfully.")
            return 0

        safe_print("🤖 [AI-INFO] Script execution failed. Analyzing for auto-healing...")

        global _initial_run_time_ns
        _initial_run_time_ns = failure_duration_ns

        exit_code, heal_stats = analyze_runtime_failure_and_heal(
            cleaned_output,
            safe_cmd_args,
            source_script_path,
            config_manager,
            is_context_aware_run=False,
            omnipkg_instance=omnipkg_core,
        )

        if heal_stats:
            _print_performance_comparison(_initial_run_time_ns, heal_stats)

        return exit_code

    finally:
        if temp_script_path and temp_script_path.exists():
            try:
                temp_script_path.unlink()
            except OSError:
                pass