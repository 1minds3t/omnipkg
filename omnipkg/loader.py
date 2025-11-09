from __future__ import annotations  # Python 3.6+ compatibility

try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sys
try:
    # --- ADD THIS LINE ---
    from .common_utils import safe_print, UVFailureDetector
except ImportError:
    # --- AND ADD THIS LINE ---
    from omnipkg.common_utils import safe_print, UVFailureDetector
_builtin_print = print
def safe_print(*args, **kwargs):
    """
    A self-contained, robust print function for the omnipkgLoader.
    It handles UnicodeEncodeError and is immune to sys.path changes
    made by the loader itself.
    """
    try:
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            safe_args = [
                str(arg).encode(encoding, 'replace').decode(encoding)
                for arg in args
            ]
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            _builtin_print("[omnipkgLoader: A message could not be displayed due to an encoding error.]")
import sys
import importlib
import shutil
import time
import gc
from pathlib import Path
import os
import subprocess
import re
import textwrap
import tempfile
from typing import Optional, Dict, Any, List, Tuple
import json
import site
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version as get_version, PackageNotFoundError
from omnipkg.i18n import _

class omnipkgLoader:
    """
    Activates isolated package environments (bubbles) created by omnipkg.
    Now with strict Python version isolation to prevent cross-version contamination.
    
    Key improvements:
    - Detects and enforces Python version boundaries
    - Prevents 3.11 paths from contaminating 3.9 environments
    - Maintains clean version-specific site-packages isolation
    - Enhanced path validation and cleanup
    """
    _dependency_cache: Optional[Dict[str, Path]] = None

    def __init__(self, package_spec: str=None, config: dict=None, quiet: bool=False, force_activation: bool=False, isolation_mode: str='strict'):

        """
        Initializes the loader with enhanced Python version awareness.
        """
        if config is None:
            # If no config is passed, become self-sufficient and load it.
            # Lazy import to prevent circular dependencies.
            from omnipkg.core import ConfigManager
            try:
                # Suppress messages because this is a background load.
                cm = ConfigManager(suppress_init_messages=True)
                self.config = cm.config
            except Exception:
                # If config fails to load for any reason, proceed with None.
                # The auto-detection logic will still serve as a fallback.
                self.config = {}
        else:
            self.config = config
        self.quiet = quiet
        self.python_version = f'{sys.version_info.major}.{sys.version_info.minor}'
        self.python_version_nodot = f'{sys.version_info.major}{sys.version_info.minor}'
        self.force_activation = force_activation
        if not self.quiet:
            safe_print(_('🐍 [omnipkg loader] Running in Python {} context').format(self.python_version))
        self._initialize_version_aware_paths()
        self._store_clean_original_state()
        self._current_package_spec = package_spec
        self._activated_bubble_path = None
        self._cloaked_main_modules = []
        self.isolation_mode = isolation_mode
        self._activation_successful = False
        self._activation_start_time = None
        self._activation_end_time = None
        self._deactivation_start_time = None
        self._deactivation_end_time = None
        self._total_activation_time_ns = None
        self._total_deactivation_time_ns = None
        self._omnipkg_dependencies = self._get_omnipkg_dependencies()
        self._activated_bubble_dependencies = [] # To track everything we need to exorcise


    def _initialize_version_aware_paths(self):
        """
        Initialize paths with strict Python version isolation.
        Ensures we only work with version-compatible directories.
        """
        if self.config and 'multiversion_base' in self.config and ('site_packages_path' in self.config):
            self.multiversion_base = Path(self.config['multiversion_base'])
            configured_site_packages = Path(self.config['site_packages_path'])
            if self._is_version_compatible_path(configured_site_packages):
                self.site_packages_root = configured_site_packages
                if not self.quiet:
                    safe_print(_('✅ [omnipkg loader] Using configured site-packages: {}').format(self.site_packages_root))
            else:
                if not self.quiet:
                    safe_print(_('⚠️ [omnipkg loader] Configured site-packages path is not compatible with Python {}. Auto-detecting...').format(self.python_version))
                self.site_packages_root = self._auto_detect_compatible_site_packages()
        else:
            if not self.quiet:
                safe_print(_('⚠️ [omnipkg loader] Config not provided or incomplete. Auto-detecting Python {}-compatible paths.').format(self.python_version))
            self.site_packages_root = self._auto_detect_compatible_site_packages()
            self.multiversion_base = self.site_packages_root / '.omnipkg_versions'
        if not self.multiversion_base.exists():
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                if not self.quiet:
                    safe_print(_('✅ [omnipkg loader] Created bubble directory: {}').format(self.multiversion_base))
            except Exception as e:
                raise RuntimeError(_('Failed to create bubble directory at {}: {}').format(self.multiversion_base, e))

    def _is_version_compatible_path(self, path: Path) -> bool:
        """
        Performs a robust check to see if a given path belongs to the
        currently running Python interpreter's version, preventing
        cross-version contamination.
        """
        path_str = str(path).lower()
        match = re.search('python(\\d+\\.\\d+)', path_str)
        if not match:
            return True
        path_version = match.group(1)
        if path_version == self.python_version:
            return True
        else:
            if not self.quiet:
                safe_print(_('🚫 [omnipkg loader] Rejecting incompatible path (contains python{}) for context python{}: {}').format(path_version, self.python_version, path))
            return False

    def _auto_detect_compatible_site_packages(self) -> Path:
        """
        Auto-detect site-packages path that's compatible with current Python version.
        """
        try:
            for site_path in site.getsitepackages():
                candidate = Path(site_path)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    if not self.quiet:
                        safe_print(_('✅ [omnipkg loader] Auto-detected compatible site-packages: {}').format(candidate))
                    return candidate
        except (AttributeError, IndexError):
            pass
        python_version_path = f'python{self.python_version}'
        candidate = Path(sys.prefix) / 'lib' / python_version_path / 'site-packages'
        if candidate.exists():
            if not self.quiet:
                safe_print(_('✅ [omnipkg loader] Using sys.prefix-based site-packages: {}').format(candidate))
            return candidate
        for path_str in sys.path:
            if 'site-packages' in path_str:
                candidate = Path(path_str)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    if not self.quiet:
                        safe_print(_('✅ [omnipkg loader] Using sys.path-derived site-packages: {}').format(candidate))
                    return candidate
        raise RuntimeError(_('Could not auto-detect Python {}-compatible site-packages directory').format(self.python_version))

    def _store_clean_original_state(self):
        """
        Store original state with contamination filtering to prevent cross-version issues.
        """
        self.original_sys_path = []
        contaminated_paths = []
        for path_str in sys.path:
            path_obj = Path(path_str)
            if self._is_version_compatible_path(path_obj):
                self.original_sys_path.append(path_str)
            else:
                contaminated_paths.append(path_str)
        if contaminated_paths and not self.quiet:
            safe_print(_('🧹 [omnipkg loader] Filtered out {} incompatible paths from sys.path').format(len(contaminated_paths)))
        self.original_sys_modules_keys = set(sys.modules.keys())
        self.original_path_env = os.environ.get('PATH', '')
        self.original_pythonpath_env = os.environ.get('PYTHONPATH', '')
        if not self.quiet:
            safe_print(_('✅ [omnipkg loader] Stored clean original state with {} compatible paths').format(len(self.original_sys_path)))

    def _filter_environment_paths(self, env_var: str) -> str:
        """
        Filter environment variable paths to remove incompatible Python versions.
        """
        if env_var not in os.environ:
            return ''
        original_paths = os.environ[env_var].split(os.pathsep)
        filtered_paths = []
        for path_str in original_paths:
            if self._is_version_compatible_path(Path(path_str)):
                filtered_paths.append(path_str)
        return os.pathsep.join(filtered_paths)
    
    def _get_omnipkg_dependencies(self) -> Dict[str, Path]:
        """
        (UPGRADED WITH FILE CACHING) Gets omnipkg's dependency paths, using a
        two-layer cache (in-memory and file-based) to ensure maximum performance
        across separate process invocations.
        """
        # --- Tier 1: Check the fast in-memory class cache ---
        if omnipkgLoader._dependency_cache is not None:
            return omnipkgLoader._dependency_cache

        # --- Tier 2: Check the persistent file cache ---
        cache_file = self.multiversion_base / '.cache' / f'loader_deps_{self.python_version}.json'
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    # Load the paths from the file
                    cached_paths_str = json.load(f)
                    # Convert string paths back to Path objects
                    dependencies = {name: Path(path) for name, path in cached_paths_str.items()}
                
                # Populate the in-memory cache for this run
                omnipkgLoader._dependency_cache = dependencies
                if not self.quiet:
                    safe_print(f"🎯 [omnipkg loader] Using cached dependencies from file ({len(dependencies)} deps)")
                return dependencies
            except (json.JSONDecodeError, IOError):
                # If the cache file is corrupt, we'll just overwrite it.
                pass

        # --- Tier 3: If all caches miss, compute, then save ---
        if not self.quiet:
            safe_print(_('🔍 [omnipkg loader] Running dependency detection (first time)...'))
        
        dependencies = self._detect_omnipkg_dependencies()
        
        # Populate the in-memory cache for this run
        omnipkgLoader._dependency_cache = dependencies
        
        # Save to the file cache for the *next* run
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Convert Path objects to strings for JSON serialization
            paths_to_save = {name: str(path) for name, path in dependencies.items()}
            with open(cache_file, 'w') as f:
                json.dump(paths_to_save, f)
            if not self.quiet:
                safe_print(_('💾 [omnipkg loader] Cached {} dependencies to file for future use').format(len(dependencies)))
        except IOError as e:
            if not self.quiet:
                safe_print(f"⚠️ [omnipkg loader] Could not write dependency cache file: {e}")

        return dependencies
    
    def _compute_omnipkg_dependencies(self) -> Dict[str, Path]:
        """
        (CORRECTED) Gets omnipkg's dependency paths, using a class-level
        cache to ensure the expensive detection runs only once per session.
        """
        # --- Check the cache first ---
        if omnipkgLoader._dependency_cache is not None:
            return omnipkgLoader._dependency_cache

        # --- If cache is empty, run the original detection logic ---
        # FIXED: Call the actual implementation instead of recursing
        dependencies = self._detect_omnipkg_dependencies()
        
        # --- Store the result in the cache for next time ---
        omnipkgLoader._dependency_cache = dependencies
        return dependencies

    def _detect_omnipkg_dependencies(self):
        """
        Detects the filesystem paths of omnipkg's own critical dependencies
        so they can be made available inside a bubble.
        """
        critical_deps = ['omnipkg', 'filelock', 'toml', 'packaging', 'requests', 'redis', 'colorama', 'click', 'rich', 'tabulate', 'psutil', 'distro', 'pydantic', 'pydantic_core', 'ruamel.yaml', 'safety_schemas']
        found_deps = {}
        for dep in critical_deps:
            try:
                dep_module = importlib.import_module(dep)
                if hasattr(dep_module, '__file__') and dep_module.__file__:
                    dep_path = Path(dep_module.__file__).parent
                    if self._is_version_compatible_path(dep_path) and (self.site_packages_root in dep_path.parents or dep_path == self.site_packages_root / dep):
                        found_deps[dep] = dep_path
            except ImportError:
                continue
            except Exception as e:
                if not self.quiet:
                    safe_print(_('⚠️ [omnipkg loader] Error detecting dependency {}: {}').format(dep, e))
                continue
        return found_deps

    def _ensure_omnipkg_access_in_bubble(self, bubble_path_str: str):
        """
        Ensure omnipkg's version-compatible dependencies remain accessible when bubble is active.
        """
        bubble_path = Path(bubble_path_str)
        linked_count = 0
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            bubble_dep_path = bubble_path / dep_name
            if bubble_dep_path.exists():
                continue
            if not self._is_version_compatible_path(dep_path):
                continue
            try:
                if dep_path.is_dir():
                    bubble_dep_path.symlink_to(dep_path, target_is_directory=True)
                else:
                    bubble_dep_path.symlink_to(dep_path)
                linked_count += 1
            except Exception as e:
                site_packages_str = str(self.site_packages_root)
                if site_packages_str not in sys.path:
                    insertion_point = 1 if len(sys.path) > 1 else len(sys.path)
                    sys.path.insert(insertion_point, site_packages_str)
        if linked_count > 0 and not self.quiet:
            safe_print(_('🔗 [omnipkg loader] Linked {} compatible dependencies to bubble').format(linked_count))

    def _get_bubble_dependencies(self, bubble_path: Path) -> dict:
        """
        (CORRECTED) Gets all packages from a bubble.
        Prioritizes reading the manifest, falls back to a fast scan for small
        bubbles, and uses a thorough scan for large bubbles.
        """
        # Strategy 1: Read the manifest (ultra-fast, always preferred)
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                return {
                    name.lower().replace('-', '_'): info.get('version')
                    for name, info in manifest.get('packages', {}).items()
                }
            except Exception:
                pass # Fall through to scanning if manifest is corrupt

        # If no manifest, proceed with scanning
        dependencies = {}
        dist_infos = list(bubble_path.rglob('*.dist-info'))

        # THE FIX: This is the actual implementation that was missing.
        for dist_info in dist_infos:
            if dist_info.is_dir():
                try:
                    from importlib.metadata import PathDistribution
                    dist = PathDistribution(dist_info)
                    pkg_name = dist.metadata['Name'].lower().replace('-', '_')
                    dependencies[pkg_name] = dist.version
                except Exception:
                    continue
        
        return dependencies

    def _get_bubble_package_version(self, bubble_path: Path, pkg_name: str) -> str:
        """Get version of a package from bubble manifest."""
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                packages = manifest.get('packages', {})
                return packages.get(pkg_name, {}).get('version')
        return None

    def _batch_cloak_packages(self, package_names: list):
        """
        Cloak multiple packages in a single filesystem operation batch.
        """
        timestamp = int(time.time() * 1000)
        cloak_operations = []
        
        # Prepare all operations first
        for pkg_name in package_names:
            canonical_pkg_name = pkg_name.lower().replace('-', '_')
            paths_to_check = [
                self.site_packages_root / canonical_pkg_name,
                next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.dist-info'), None),
                self.site_packages_root / f'{canonical_pkg_name}.py'
            ]
            
            for original_path in paths_to_check:
                if original_path and original_path.exists():
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked')
                    cloak_operations.append((original_path, cloak_path))
        
        # Execute all moves at once
        successful_cloaks = []
        for original_path, cloak_path in cloak_operations:
            try:
                shutil.move(str(original_path), str(cloak_path))
                successful_cloaks.append((original_path, cloak_path, True))
            except Exception:
                successful_cloaks.append((original_path, cloak_path, False))
        
        self._cloaked_main_modules.extend(successful_cloaks)
        return len([c for c in successful_cloaks if c[2]])


    def _is_main_site_packages(self, path: str) -> bool:
        """Check if a path points to the main site-packages directory."""
        try:
            path_obj = Path(path).resolve()
            main_site_packages = self.site_packages_root.resolve()
            return path_obj == main_site_packages
        except:
            return False

    def _bubble_needs_fallback(self, bubble_path: Path) -> bool:
        """Determine if bubble needs access to main site-packages for dependencies."""
        # Check if bubble has all critical dependencies
        critical_deps = ['setuptools', 'pip', 'wheel']
        
        for dep in critical_deps:
            dep_path = bubble_path / dep
            dist_info_path = next(bubble_path.glob(f"{dep}-*.dist-info"), None)
            
            if not (dep_path.exists() or dist_info_path):
                return True
        
        return False

    def _add_selective_fallbacks(self, bubble_path: Path):
        """Add only specific non-conflicting packages from main environment."""
        bubble_packages = set(self._get_bubble_dependencies(bubble_path))
        
        # Only allow these safe packages from main environment
        safe_packages = {'setuptools', 'pip', 'wheel', 'certifi', 'urllib3'}
        
        # Create a restricted view of main site-packages
        main_site_packages = str(self.site_packages_root)
        
        # Only add main site-packages if we need safe packages
        needed_safe_packages = safe_packages - bubble_packages
        if needed_safe_packages and main_site_packages not in sys.path:
            sys.path.append(main_site_packages)

    def __enter__(self):
        """
        (V5 - RESTORED INTELLIGENCE) Activates the snapshot, but first checks
        if the main environment already satisfies the requirement.
        """
        self._activation_start_time = time.perf_counter_ns()
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec.")

        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
        except ValueError:
            raise ValueError(f"Invalid package_spec format: '{self._current_package_spec}'")

        # --- STEP 1: Check if main env already satisfies (QUICK CHECK) ---
        try:
            current_system_version = get_version(pkg_name)
            
            if current_system_version == requested_version and not self.force_activation:
                if not self.quiet:
                    safe_print(_('✅ System version already matches requested version ({}). No bubble needed.').format(current_system_version))
                self._activation_successful = True
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                return self  # Exit early - perfect match in main env
            else:
                # Version mismatch - need bubble
                if not self.quiet:
                    safe_print(f"   🔍 Main env has {pkg_name}=={current_system_version}, need {requested_version}. Looking for bubble...")
        except PackageNotFoundError:
            # Not in main env - definitely need bubble
            if not self.quiet:
                safe_print(f"   🔍 {pkg_name} not in main environment. Looking for bubble...")

        # --- STEP 2: Try to use BUBBLE (PREFERRED) ---
        if not self.quiet:
            safe_print(_('🚀 Fast-activating {} ...').format(self._current_package_spec))
        
        bubble_path = self.multiversion_base / f'{pkg_name}-{requested_version}'
        
        # ✅ DEBUG: Show what we're looking for
        if not self.quiet:
            safe_print(f"   📂 Searching for bubble: {bubble_path}")
        
        if not bubble_path.is_dir():
            # ❌ Bubble not found - show debug info and try fallback
            if not self.quiet:
                safe_print(f"   ⚠️  Bubble not found at: {bubble_path}")
                
                # Show what bubbles DO exist
                parent_dir = bubble_path.parent
                if parent_dir.exists():
                    available_bubbles = [d.name for d in parent_dir.iterdir() if d.is_dir()]
                    if available_bubbles:
                        # Show related bubbles
                        related = [b for b in available_bubbles if b.startswith(pkg_name)]
                        if related:
                            safe_print(f"   📦 Available {pkg_name} bubbles: {', '.join(related)}")
                        else:
                            safe_print(f"   📦 No {pkg_name} bubbles found. Total bubbles: {len(available_bubbles)}")
                            if len(available_bubbles) <= 10:
                                safe_print(f"      All bubbles: {', '.join(available_bubbles)}")
                    else:
                        safe_print(f"   📦 Bubble directory is empty: {parent_dir}")
                else:
                    safe_print(f"   ❌ Bubble directory doesn't exist: {parent_dir}")
            
            # --- STEP 3: FALLBACK to main environment ---
            if not self.quiet:
                safe_print(f"   🔄 Falling back to main environment check...")
            
            try:
                import importlib.metadata
                installed_version = importlib.metadata.version(pkg_name)
                
                if installed_version == requested_version:
                    # ✅ Found exact version in main site-packages
                    if not self.quiet:
                        safe_print(f"   ✅ Found {pkg_name}=={requested_version} in main site-packages")
                        safe_print(f"      ⚠️  WARNING: Using main env (not isolated). Consider creating bubble.")
                    
                    self._activation_successful = True
                    self._activation_end_time = time.perf_counter_ns()
                    self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                    return self  # Fallback successful
                else:
                    # Wrong version in main env
                    if not self.quiet:
                        safe_print(f"   ❌ Main env has {pkg_name}=={installed_version}, need {requested_version}")
                    raise RuntimeError(
                        f"Package {pkg_name}=={requested_version} not available\n"
                        f"  Bubble not found: {bubble_path}\n"
                        f"  Main env has: {installed_version}\n"
                        f"  Hint: Try 'omnipkg install {pkg_name}=={requested_version}'"
                    )
            except importlib.metadata.PackageNotFoundError:
                # Not in main env either
                if not self.quiet:
                    safe_print(f"   ❌ {pkg_name} not found in main environment either")
                raise RuntimeError(
                    f"Package {pkg_name}=={requested_version} not found anywhere\n"
                    f"  Bubble not found: {bubble_path}\n"
                    f"  Not in main site-packages\n"
                    f"  Hint: Install with 'omnipkg install {pkg_name}=={requested_version}'"
                )

        # --- STEP 4: BUBBLE FOUND - Activate it ---
        try:
            if not self.quiet:
                safe_print(f"   ✅ Bubble found: {bubble_path}")
            
            self._activated_bubble_dependencies = list(self._get_bubble_dependencies(bubble_path).keys())
            
            # Now, when we cloak and clean, we do it for EVERYTHING.
            for pkg in self._activated_bubble_dependencies:
                self._aggressive_module_cleanup(pkg)
            
            self._batch_cloak_packages(self._activated_bubble_dependencies)
            
            # Use fast dependency detection
            bubble_packages = self._get_bubble_dependencies(bubble_path)
            
            # Batch cleanup all modules at once
            all_package_names = list(bubble_packages.keys())
            for pkg in all_package_names:
                self._aggressive_module_cleanup(pkg)
            
            # Batch cloak operations
            cloaked_count = self._batch_cloak_packages(all_package_names)
            
            # Fast path setup (minimal logging)
            bubble_path_str = str(bubble_path)
            bubble_bin_path = bubble_path / 'bin'
            if bubble_bin_path.is_dir():
                os.environ['PATH'] = f'{str(bubble_bin_path)}{os.pathsep}{self.original_path_env}'

            # Single sys.path operation
            new_sys_path = [bubble_path_str] + [p for p in self.original_sys_path if not self._is_main_site_packages(p)]
            sys.path[:] = new_sys_path  # In-place replacement is faster
            
            self._ensure_omnipkg_access_in_bubble(bubble_path_str)
            
            self._activated_bubble_path = bubble_path_str
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
            
            if not self.quiet:
                safe_print(f"   ⚡ HEALED in {self._total_activation_time_ns / 1000:,.1f} μs")
            
            self._activation_successful = True
            return self

        except Exception as e:
            safe_print(_('   ❌ Activation failed: {}').format(str(e)))
            self._panic_restore_cloaks()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Enhanced deactivation with cleanup of isolation markers."""
        self._deactivation_start_time = time.perf_counter_ns()
        
        safe_print(f'🌀 omnipkg loader: Deactivating {self._current_package_spec}...')
        
        if not self._activation_successful and not self._cloaked_main_modules:
            return
        
        # --- START: HYPER-AGGRESSIVE CLEANUP ---

        # 1. Restore cloaked modules and original PATH first.
        # This makes the main environment's packages visible again.
        self._restore_cloaked_modules()
        os.environ['PATH'] = self.original_path_env

        # 2. Restore the original sys.path.
        sys.path[:] = self.original_sys_path
        
        # 3. Purge every single module that was part of the bubble.
        # This is the exorcism that kills the ghosts.
        if not self.quiet and self._activated_bubble_dependencies:
            safe_print(f"   - 👻 Exorcising {len(self._activated_bubble_dependencies)} bubble modules from memory...")
        
        for pkg_name in self._activated_bubble_dependencies:
            self._aggressive_module_cleanup(pkg_name)
        
        # Also clean up the main package spec just in case
        main_pkg_name = self._current_package_spec.split('==')[0]
        self._aggressive_module_cleanup(main_pkg_name)
        
        # 4. Invalidate import caches to force Python to re-evaluate the now-clean path.
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()
        
        # 5. Run garbage collection to be extra sure.
        gc.collect()
        
        self._deactivation_end_time = time.perf_counter_ns()
        self._total_deactivation_time_ns = self._deactivation_end_time - self._deactivation_start_time
        total_swap_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        
        safe_print(f'   ✅ Environment fully restored.')
        safe_print(f'   ⏱️  Total Swap Time: {total_swap_time_ns / 1000:,.3f} μs ({total_swap_time_ns:,} ns)')

    def _cleanup_omnipkg_links_in_bubble(self, bubble_path_str: str):
        """
        Clean up symlinks created for omnipkg dependencies in the bubble.
        """
        bubble_path = Path(bubble_path_str)
        for dep_name in self._omnipkg_dependencies.keys():
            bubble_dep_path = bubble_path / dep_name
            if bubble_dep_path.is_symlink():
                try:
                    bubble_dep_path.unlink()
                except Exception:
                    pass

    def debug_version_compatibility(self):
        """Debug helper to check version compatibility of current paths."""
        safe_print(_('\n🔍 DEBUG: Python Version Compatibility Check'))
        safe_print(_('Current Python version: {}').format(self.python_version))
        safe_print(_('Site-packages root: {}').format(self.site_packages_root))
        safe_print(_('Compatible: {}').format(self._is_version_compatible_path(self.site_packages_root)))
        safe_print(_('\n🔍 Current sys.path compatibility ({} entries):').format(len(sys.path)))
        compatible_count = 0
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            is_compatible = self._is_version_compatible_path(path_obj)
            exists = path_obj.exists()
            status = '✅' if exists and is_compatible else '🚫' if exists else '❌'
            if is_compatible and exists:
                compatible_count += 1
            safe_print(_('   [{}] {} {}').format(i, status, path))
        safe_print(_('\n📊 Summary: {}/{} paths are Python {}-compatible').format(compatible_count, len(sys.path), self.python_version))
        safe_print()

    def get_performance_stats(self):
        """Returns detailed performance statistics for CI/logging purposes."""
        if self._total_activation_time_ns is None or self._total_deactivation_time_ns is None:
            return None
        total_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        return {'package_spec': self._current_package_spec, 'python_version': self.python_version, 'activation_time_ns': self._total_activation_time_ns, 'activation_time_us': self._total_activation_time_ns / 1000, 'activation_time_ms': self._total_activation_time_ns / 1000000, 'deactivation_time_ns': self._total_deactivation_time_ns, 'deactivation_time_us': self._total_deactivation_time_ns / 1000, 'deactivation_time_ms': self._total_deactivation_time_ns / 1000000, 'total_swap_time_ns': total_time_ns, 'total_swap_time_us': total_time_ns / 1000, 'total_swap_time_ms': total_time_ns / 1000000, 'swap_speed_description': self._get_speed_description(total_time_ns)}

    def _get_speed_description(self, time_ns):
        """Returns a human-readable description of swap speed."""
        if time_ns < 1000:
            return f'Ultra-fast ({time_ns} nanoseconds)'
        elif time_ns < 1000000:
            return f'Lightning-fast ({time_ns / 1000:.1f} microseconds)'
        elif time_ns < 1000000000:
            return f'Very fast ({time_ns / 1000000:.1f} milliseconds)'
        else:
            return f'Standard ({time_ns / 1000000000:.2f} seconds)'

    def print_ci_performance_summary(self):
        """Prints a CI-friendly performance summary focused on healing success."""
        safe_print('\n' + '=' * 70)
        safe_print('🚀 EXECUTION ANALYSIS: Standard Runner vs. Omnipkg Auto-Healing')
        safe_print('=' * 70)

        loader_stats = self.get_performance_stats()


        uv_failed_ms = uv_failure_detector.get_execution_time_ms()
        
        omnipkg_heal_and_run_ms = loader_stats.get('total_swap_time_ms', 0) if loader_stats else 0
        
        total_omnipkg_time_ms = uv_failed_ms + omnipkg_heal_and_run_ms

        safe_print(f"  - Standard Runner (uv):   [ FAILED ] at {uv_failed_ms:>8.3f} ms")
        safe_print(f"  - Omnipkg Healing & Run:  [ SUCCESS ] in {omnipkg_heal_and_run_ms:>8.3f} ms")
        safe_print('-' * 70)
        safe_print(f"  - Total Time to Success via Omnipkg: {total_omnipkg_time_ms:>8.3f} ms")
        safe_print('=' * 70)
        safe_print("🌟 Verdict:")
        safe_print("   A standard runner fails instantly. Omnipkg absorbs the failure,")
        safe_print("   heals the environment in microseconds, and completes the job.")
        safe_print('=' * 70)

    def _get_package_modules(self, pkg_name: str):
        """Helper to find all modules related to a package in sys.modules."""
        pkg_name_normalized = pkg_name.replace('-', '_')
        return [mod for mod in list(sys.modules.keys()) if mod.startswith(pkg_name_normalized + '.') or mod == pkg_name_normalized or mod.replace('_', '-').startswith(pkg_name.lower())]

    def _aggressive_module_cleanup(self, pkg_name: str):
        """Removes specified package's modules from sys.modules and invalidates caches."""
        modules_to_clear = self._get_package_modules(pkg_name)
        for mod_name in modules_to_clear:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        gc.collect()
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()

    def _cloak_main_package(self, pkg_name: str):
        """Temporarily renames the main environment installation of a package."""
        canonical_pkg_name = pkg_name.lower().replace('-', '_')
        paths_to_check = [self.site_packages_root / canonical_pkg_name, next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.dist-info'), None), next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.egg-info'), None), self.site_packages_root / f'{canonical_pkg_name}.py']
        for original_path in paths_to_check:
            if original_path and original_path.exists():
                timestamp = int(time.time() * 1000)
                if original_path.is_dir():
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked')
                else:
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked{original_path.suffix}')
                cloak_record = (original_path, cloak_path, False)
                if cloak_path.exists():
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                    except Exception as e:
                        if not self.quiet:
                            safe_print(_(' ⚠️ Warning: Could not remove existing cloak {}: {}').format(cloak_path.name, e))
                try:
                    shutil.move(str(original_path), str(cloak_path))
                    cloak_record = (original_path, cloak_path, True)
                except Exception as e:
                    if not self.quiet:
                        safe_print(_(' ⚠️ Failed to cloak {}: {}').format(original_path.name, e))
                self._cloaked_main_modules.append(cloak_record)

    def _restore_cloaked_modules(self):
        """Restore all cloaked modules, with better error handling."""
        restored_count = 0
        failed_count = 0
        for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
            if not was_successful:
                continue
            if cloak_path.exists():
                if original_path.exists():
                    try:
                        if original_path.is_dir():
                            shutil.rmtree(original_path, ignore_errors=True)
                        else:
                            os.unlink(original_path)
                    except Exception as e:
                        if not self.quiet:
                            safe_print(_(' ⚠️ Warning: Could not remove conflicting path {}: {}').format(original_path.name, e))
                try:
                    shutil.move(str(cloak_path), str(original_path))
                    restored_count += 1
                except Exception as e:
                    if not self.quiet:
                        safe_print(_(' ❌ Failed to restore {} from {}: {}').format(original_path.name, cloak_path.name, e))
                    failed_count += 1
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                    except:
                        pass
            else:
                if not self.quiet:
                    safe_print(_(' ❌ CRITICAL: Cloaked path {} is missing! Package {} may be lost.').format(cloak_path.name, original_path.name))
                failed_count += 1
                pkg_name = self._current_package_spec.split('==')[0] if self._current_package_spec else 'unknown'
                try:
                    get_version(pkg_name)
                    if not self.quiet:
                        safe_print(_(' ℹ️ Package {} still appears to be installed in system.').format(pkg_name))
                except PackageNotFoundError:
                    if not self.quiet:
                        safe_print(_(' ❌ Package {} is no longer available in system. Consider reinstalling.').format(pkg_name))
                        safe_print(_('   Suggestion: pip install --force-reinstall --no-deps {}').format(pkg_name))
        self._cloaked_main_modules.clear()
        if failed_count > 0 and not self.quiet:
            safe_print(_(' ⚠️ Cloak restore summary: {} successful, {} failed').format(restored_count, failed_count))

    def _panic_restore_cloaks(self):
        """Emergency cloak restoration when activation fails."""
        if not self.quiet:
            safe_print(_(' 🚨 Emergency cloak restoration in progress...'))
        self._restore_cloaked_modules()

    def cleanup_abandoned_cloaks(self):
        """
        Utility method to clean up any abandoned cloak files.
        Can be called manually if you suspect there are leftover cloaks.
        """
        safe_print(_('🧹 Scanning for abandoned omnipkg cloaks...'))
        cloak_pattern = '*_omnipkg_cloaked*'
        found_cloaks = list(self.site_packages_root.glob(cloak_pattern))
        if not found_cloaks:
            safe_print(_(' ✅ No abandoned cloaks found.'))
            return
        safe_print(_(' 🔍 Found {} potential abandoned cloak(s):').format(len(found_cloaks)))
        for cloak_path in found_cloaks:
            safe_print(_('   - {}').format(cloak_path.name))
        safe_print(_(' ℹ️ To remove these manually: rm -rf /path/to/site-packages/*_omnipkg_cloaked*'))
        safe_print(_(" ⚠️ WARNING: Only remove if you're sure no omnipkg operations are running!"))

    def debug_sys_path(self):
        """Debug helper to print current sys.path state."""
        safe_print(_('\n🔍 DEBUG: Current sys.path ({} entries):').format(len(sys.path)))
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            status = '✅' if path_obj.exists() else '❌'
            safe_print(_('   [{}] {} {}').format(i, status, path))
        safe_print()

    def debug_omnipkg_dependencies(self):
        """Debug helper to show detected omnipkg dependencies."""
        safe_print(_('\n🔍 DEBUG: Detected omnipkg dependencies:'))
        if not self._omnipkg_dependencies:
            safe_print(_('   ❌ No dependencies detected'))
            return
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            status = '✅' if dep_path.exists() else '❌'
            safe_print(_('   {} {}: {}').format(status, dep_name, dep_path))
        safe_print()