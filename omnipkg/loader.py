import sys
import importlib
import shutil
import time
import gc
from pathlib import Path
import os
import subprocess
import json
import site
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

    def __init__(self, package_spec: str=None, config: dict=None):
        """
        Initializes the loader with enhanced Python version awareness.
        """
        self.config = config
        self.python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.python_version_nodot = f"{sys.version_info.major}{sys.version_info.minor}"
        
        print(_('🐍 [omnipkg loader] Running in Python {} context').format(self.python_version))
        
        # Initialize paths with strict version checking
        self._initialize_version_aware_paths()
        
        # Store original state with contamination filtering
        self._store_clean_original_state()
        
        # Loader state
        self._current_package_spec = package_spec
        self._activated_bubble_path = None
        self._cloaked_main_modules = []
        self._activation_successful = False
        
        # Performance timing attributes
        self._activation_start_time = None
        self._activation_end_time = None
        self._deactivation_start_time = None
        self._deactivation_end_time = None
        self._total_activation_time_ns = None
        self._total_deactivation_time_ns = None

        # Detect omnipkg's critical dependencies for subprocess support
        self._omnipkg_dependencies = self._detect_omnipkg_dependencies()

    def _initialize_version_aware_paths(self):
        """
        Initialize paths with strict Python version isolation.
        Ensures we only work with version-compatible directories.
        """
        if self.config and 'multiversion_base' in self.config and 'site_packages_path' in self.config:
            self.multiversion_base = Path(self.config['multiversion_base'])
            configured_site_packages = Path(self.config['site_packages_path'])
            
            # Validate that configured path matches current Python version
            if self._is_version_compatible_path(configured_site_packages):
                self.site_packages_root = configured_site_packages
                print(_('✅ [omnipkg loader] Using configured site-packages: {}').format(self.site_packages_root))
            else:
                print(_('⚠️ [omnipkg loader] Configured site-packages path is not compatible with Python {}. Auto-detecting...').format(self.python_version))
                self.site_packages_root = self._auto_detect_compatible_site_packages()
        else:
            print(_('⚠️ [omnipkg loader] Config not provided or incomplete. Auto-detecting Python {}-compatible paths.').format(self.python_version))
            self.site_packages_root = self._auto_detect_compatible_site_packages()
            self.multiversion_base = self.site_packages_root / '.omnipkg_versions'
        
        # Ensure multiversion_base exists
        if not self.multiversion_base.exists():
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                print(_('✅ [omnipkg loader] Created bubble directory: {}').format(self.multiversion_base))
            except Exception as e:
                raise RuntimeError(_('Failed to create bubble directory at {}: {}').format(self.multiversion_base, e))

    def _is_version_compatible_path(self, path: Path) -> bool:
        """
        Check if a path is compatible with the current Python version.
        Returns True if the path contains the current Python version or is version-neutral.
        """
        path_str = str(path).lower()
        
        # Check for explicit version compatibility
        current_version_patterns = [
            f'python{self.python_version}',
            f'python{self.python_version_nodot}',
            f'cp{self.python_version_nodot}',
            f'py{self.python_version_nodot}',
        ]
        
        # Check for incompatible versions (other Python versions)
        incompatible_patterns = []
        for major in [2, 3]:
            for minor in range(0, 20):  # Cover reasonable range of minor versions
                if f"{major}.{minor}" == self.python_version:
                    continue
                incompatible_patterns.extend([
                    f'python{major}.{minor}',
                    f'python{major}{minor}',
                    f'cp{major}{minor}',
                    f'py{major}{minor}',
                ])
        
        # If path contains incompatible version markers, reject it
        for pattern in incompatible_patterns:
            if pattern in path_str:
                print(_('🚫 [omnipkg loader] Rejecting incompatible path (contains {}): {}').format(pattern, path))
                return False
        
        # If path contains compatible version markers, accept it
        for pattern in current_version_patterns:
            if pattern in path_str:
                print(_('✅ [omnipkg loader] Accepting compatible path (contains {}): {}').format(pattern, path))
                return True
        
        # If no version markers found, it might be version-neutral, so we accept it cautiously
        print(_('⚪ [omnipkg loader] Version-neutral path accepted: {}').format(path))
        return True

    def _auto_detect_compatible_site_packages(self) -> Path:
        """
        Auto-detect site-packages path that's compatible with current Python version.
        """
        # Try site.getsitepackages() first
        try:
            for site_path in site.getsitepackages():
                candidate = Path(site_path)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    print(_('✅ [omnipkg loader] Auto-detected compatible site-packages: {}').format(candidate))
                    return candidate
        except (AttributeError, IndexError):
            pass
        
        # Fallback to sys.prefix-based detection
        python_version_path = f'python{self.python_version}'
        candidate = Path(sys.prefix) / 'lib' / python_version_path / 'site-packages'
        
        if candidate.exists():
            print(_('✅ [omnipkg loader] Using sys.prefix-based site-packages: {}').format(candidate))
            return candidate
        
        # Last resort: use the first entry in sys.path that looks like site-packages
        for path_str in sys.path:
            if 'site-packages' in path_str:
                candidate = Path(path_str)
                if candidate.exists() and self._is_version_compatible_path(candidate):
                    print(_('✅ [omnipkg loader] Using sys.path-derived site-packages: {}').format(candidate))
                    return candidate
        
        raise RuntimeError(_('Could not auto-detect Python {}-compatible site-packages directory').format(self.python_version))

    def _store_clean_original_state(self):
        """
        Store original state with contamination filtering to prevent cross-version issues.
        """
        # Filter sys.path to only include version-compatible paths
        self.original_sys_path = []
        contaminated_paths = []
        
        for path_str in sys.path:
            path_obj = Path(path_str)
            if self._is_version_compatible_path(path_obj):
                self.original_sys_path.append(path_str)
            else:
                contaminated_paths.append(path_str)
        
        if contaminated_paths:
            print(_('🧹 [omnipkg loader] Filtered out {} incompatible paths from sys.path:').format(len(contaminated_paths)))
            for path in contaminated_paths[:3]:  # Show first 3 for brevity
                print(_('   🚫 {}').format(path))
            if len(contaminated_paths) > 3:
                print(_('   ... and {} more').format(len(contaminated_paths) - 3))
        
        # Store other original state
        self.original_sys_modules_keys = set(sys.modules.keys())
        self.original_path_env = os.environ.get('PATH', '')
        self.original_pythonpath_env = os.environ.get('PYTHONPATH', '')
        
        print(_('✅ [omnipkg loader] Stored clean original state with {} compatible paths').format(len(self.original_sys_path)))

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

    def _detect_omnipkg_dependencies(self):
        """
        Detect critical omnipkg dependencies that are compatible with current Python version.
        """
        critical_deps = [
            'omnipkg', 'filelock', 'toml', 'packaging', 'requests', 'redis', 
            'colorama', 'click', 'rich', 'tabulate', 'psutil', 'distro'
        ]
        
        found_deps = {}
        for dep in critical_deps:
            try:
                dep_module = importlib.import_module(dep)
                if hasattr(dep_module, '__file__') and dep_module.__file__:
                    dep_path = Path(dep_module.__file__).parent
                    
                    # Ensure dependency is version-compatible and in our site-packages
                    if (self._is_version_compatible_path(dep_path) and 
                        (self.site_packages_root in dep_path.parents or dep_path == self.site_packages_root / dep)):
                        found_deps[dep] = dep_path
                        print(_('✅ [omnipkg loader] Found compatible dependency: {} at {}').format(dep, dep_path))
                    else:
                        print(_('🚫 [omnipkg loader] Skipped incompatible dependency: {} at {}').format(dep, dep_path))
            except ImportError:
                continue
            except Exception as e:
                print(_('⚠️ [omnipkg loader] Error detecting dependency {}: {}').format(dep, e))
                continue
        
        return found_deps

    def _ensure_omnipkg_access_in_bubble(self, bubble_path_str: str):
        """
        Ensure omnipkg's version-compatible dependencies remain accessible when bubble is active.
        """
        bubble_path = Path(bubble_path_str)
        
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            bubble_dep_path = bubble_path / dep_name
            
            # Skip if dependency already exists in bubble
            if bubble_dep_path.exists():
                continue
            
            # Double-check version compatibility before linking
            if not self._is_version_compatible_path(dep_path):
                print(_('🚫 [omnipkg loader] Skipping incompatible dependency link: {}').format(dep_name))
                continue
            
            try:
                if dep_path.is_dir():
                    bubble_dep_path.symlink_to(dep_path, target_is_directory=True)
                else:
                    bubble_dep_path.symlink_to(dep_path)
                print(_('🔗 [omnipkg loader] Linked version-compatible {} to bubble').format(dep_name))
            except Exception as e:
                print(_('⚠️ [omnipkg loader] Failed to link {} to bubble: {}').format(dep_name, e))
                # Fallback: add the original site-packages to sys.path in a strategic position
                site_packages_str = str(self.site_packages_root)
                if site_packages_str not in sys.path:
                    insertion_point = 1 if len(sys.path) > 1 else len(sys.path)
                    sys.path.insert(insertion_point, site_packages_str)
                    print(_('🔧 [omnipkg loader] Added compatible site-packages fallback at position {}').format(insertion_point))

    def __enter__(self):
        """Activates the specified package snapshot with strict version isolation."""
        self._activation_start_time = time.perf_counter_ns()
        
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec (e.g., 'pkg==ver') when used as a context manager.")
        
        print(_('\n🌀 omnipkg loader: Activating {} in Python {} context...').format(self._current_package_spec, self.python_version))
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
            pkg_name_normalized = pkg_name.lower().replace('-', '_')
        except ValueError:
            raise ValueError(_("Invalid package_spec format. Expected 'name==version', got '{}'.").format(self._current_package_spec))
        
        # Check if system version already matches (using compatible paths only)
        try:
            current_system_version = get_version(pkg_name)
            if current_system_version == requested_version:
                self._activation_end_time = time.perf_counter_ns()
                self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
                print(_(' ✅ System version already matches requested version ({}). No bubble activation needed.').format(current_system_version))
                print(_(' ⏱️  Activation time: {:.3f} μs ({:,} ns)').format(self._total_activation_time_ns / 1000, self._total_activation_time_ns))
                self._activated_bubble_path = None
                self._activation_successful = True
                return self
        except PackageNotFoundError:
            pass
        except Exception as e:
            print(_('⚠️ [omnipkg loader] Error checking system version for {}: {}. Proceeding with bubble search.').format(pkg_name, e))
            pass
        
        # Find and activate bubble
        bubble_dir_name = f'{pkg_name_normalized}-{requested_version}'
        bubble_path = self.multiversion_base / bubble_dir_name
        
        if not bubble_path.is_dir():
            raise RuntimeError(_("Bubble not found for {} at {}. Please ensure it's installed via 'omnipkg install {}'.").format(self._current_package_spec, bubble_path, self._current_package_spec))
        
        try:
            # Clean up target package modules and cloak main installation
            self._aggressive_module_cleanup(pkg_name)
            self._cloak_main_package(pkg_name)
            
            bubble_path_str = str(bubble_path)
            
            # Update PATH if bubble has bin directory
            bubble_bin_path = bubble_path / 'bin'
            if bubble_bin_path.is_dir():
                os.environ['PATH'] = f'{str(bubble_bin_path)}{os.pathsep}{self.original_path_env}'
                print(_(' ⚙️ Added to PATH: {}').format(bubble_bin_path))
            
            # --- THIS IS THE DEFINITIVE FIX ---
            # Rebuild sys.path cleanly, filtering out contaminated paths.
            new_sys_path = []
            # 1. Add the bubble path. Highest priority.
            new_sys_path.append(bubble_path_str)

            # Get the version string of the CURRENTLY EXECUTING interpreter (e.g., 'python3.10')
            current_interpreter_version_str = f'python{sys.version_info.major}.{sys.version_info.minor}'

            # 2. Add back original paths, but ONLY if they are not from a different Python's stdlib.
            for p in self.original_sys_path:
                path_str = str(p)
                # The contamination check: is '/lib/pythonX.Y' in the path, and is it the WRONG X.Y?
                if '/lib/python' in path_str and current_interpreter_version_str not in path_str:
                    # This is a contaminated path from a different Python stdlib. SKIP IT.
                    continue
                
                # Also skip the original site-packages, which is handled later.
                if Path(p).resolve() == self.site_packages_root.resolve():
                    continue
                if p not in new_sys_path:
                    new_sys_path.append(p)

            # 3. Replace the global sys.path with our clean, decontaminated version.
            sys.path.clear()
            sys.path.extend(new_sys_path)
            # --- END OF THE DEFINITIVE FIX ---

            # Ensure omnipkg dependencies are accessible for subprocess operations
            self._ensure_omnipkg_access_in_bubble(bubble_path_str)
            
            # Add the original site-packages as a fallback (but lower priority)
            if str(self.site_packages_root) not in sys.path:
                sys.path.append(str(self.site_packages_root))
            
            self._activated_bubble_path = bubble_path_str
            
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
            
            print(_(' ✅ Activated bubble with Python {} isolation: {}').format(self.python_version, bubble_path_str))
            print(_(' 🔧 sys.path[0]: {}').format(sys.path[0]))
            print(_(' 🛡️ Version isolation: Only Python {}-compatible paths active').format(self.python_version))
            print(_(' 🔗 Ensured version-compatible omnipkg dependencies for subprocess support'))
            print(_(' ⏱️  Activation time: {:.3f} μs ({:,} ns)').format(self._total_activation_time_ns / 1000, self._total_activation_time_ns))
            
            # Show bubble info
            manifest_path = bubble_path / '.omnipkg_manifest.json'
            if manifest_path.exists():
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    pkg_count = len(manifest.get('packages', {}))
                    print(_(' ℹ️ Bubble contains {} packages (Python {} compatible).').format(pkg_count, self.python_version))
            
            self._activation_successful = True
            return self
            
        except Exception as e:
            print(_(' ❌ Activation failed: {}').format(str(e)))
            self._panic_restore_cloaks()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Deactivates the snapshot and restores the clean environment."""
        self._deactivation_start_time = time.perf_counter_ns()
        
        print(_('\n🌀 omnipkg loader: Deactivating {} (Python {} context)...').format(self._current_package_spec, self.python_version))
        
        if not self._activation_successful and (not self._cloaked_main_modules):
            return
        
        pkg_name = self._current_package_spec.split('==')[0]
        
        # Clean up omnipkg dependency links if we created them
        if self._activated_bubble_path:
            self._cleanup_omnipkg_links_in_bubble(self._activated_bubble_path)
        
        # Restore cloaked modules
        self._restore_cloaked_modules()
        
        # Restore sys.path to clean original state
        sys.path.clear()
        sys.path.extend(self.original_sys_path)
        
        # Clean up modules that were imported during bubble activation
        current_modules_keys = set(sys.modules.keys())
        for mod_name in current_modules_keys:
            if mod_name not in self.original_sys_modules_keys:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
        
        # Final cleanup of target package
        self._aggressive_module_cleanup(pkg_name)
        
        # Restore environment variables with version filtering
        os.environ['PATH'] = self.original_path_env
        if self.original_pythonpath_env:
            # Filter PYTHONPATH to maintain version compatibility
            filtered_pythonpath = self._filter_environment_paths('PYTHONPATH')
            if filtered_pythonpath:
                os.environ['PYTHONPATH'] = filtered_pythonpath
            else:
                if 'PYTHONPATH' in os.environ:
                    del os.environ['PYTHONPATH']
        elif 'PYTHONPATH' in os.environ:
            del os.environ['PYTHONPATH']
        
        # Invalidate import caches and collect garbage
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()
        gc.collect()
        
        self._deactivation_end_time = time.perf_counter_ns()
        self._total_deactivation_time_ns = self._deactivation_end_time - self._deactivation_start_time
        total_swap_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        
        print(_(' ✅ Environment restored to clean Python {} state.').format(self.python_version))
        print(_(' 🛡️ Version isolation maintained throughout operation'))
        print(_(' ⏱️  Deactivation time: {:.3f} μs ({:,} ns)').format(self._total_deactivation_time_ns / 1000, self._total_deactivation_time_ns))
        print(_(' 🎯 TOTAL SWAP TIME: {:.3f} μs ({:,} ns)').format(total_swap_time_ns / 1000, total_swap_time_ns))

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
                    # Don't print errors during cleanup, it's not critical
                    pass

    def debug_version_compatibility(self):
        """Debug helper to check version compatibility of current paths."""
        print(_('\n🔍 DEBUG: Python Version Compatibility Check'))
        print(_('Current Python version: {}').format(self.python_version))
        print(_('Site-packages root: {}').format(self.site_packages_root))
        print(_('Compatible: {}').format(self._is_version_compatible_path(self.site_packages_root)))
        
        print(_('\n🔍 Current sys.path compatibility ({} entries):').format(len(sys.path)))
        compatible_count = 0
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            is_compatible = self._is_version_compatible_path(path_obj)
            exists = path_obj.exists()
            status = "✅" if (exists and is_compatible) else "🚫" if exists else "❌"
            
            if is_compatible and exists:
                compatible_count += 1
            
            print(_('   [{}] {} {}').format(i, status, path))
        
        print(_('\n📊 Summary: {}/{} paths are Python {}-compatible').format(
            compatible_count, len(sys.path), self.python_version))
        print()

    # Keep all the existing methods from the original class
    def get_performance_stats(self):
        """Returns detailed performance statistics for CI/logging purposes."""
        if self._total_activation_time_ns is None or self._total_deactivation_time_ns is None:
            return None
        
        total_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        return {
            'package_spec': self._current_package_spec,
            'python_version': self.python_version,
            'activation_time_ns': self._total_activation_time_ns,
            'activation_time_us': self._total_activation_time_ns / 1000,
            'activation_time_ms': self._total_activation_time_ns / 1_000_000,
            'deactivation_time_ns': self._total_deactivation_time_ns,
            'deactivation_time_us': self._total_deactivation_time_ns / 1000,
            'deactivation_time_ms': self._total_deactivation_time_ns / 1_000_000,
            'total_swap_time_ns': total_time_ns,
            'total_swap_time_us': total_time_ns / 1000,
            'total_swap_time_ms': total_time_ns / 1_000_000,
            'swap_speed_description': self._get_speed_description(total_time_ns)
        }

    def _get_speed_description(self, time_ns):
        """Returns a human-readable description of swap speed."""
        if time_ns < 1_000:
            return f"Ultra-fast ({time_ns} nanoseconds)"
        elif time_ns < 1_000_000:
            return f"Lightning-fast ({time_ns/1000:.1f} microseconds)"
        elif time_ns < 1_000_000_000:
            return f"Very fast ({time_ns/1_000_000:.1f} milliseconds)"
        else:
            return f"Standard ({time_ns/1_000_000_000:.2f} seconds)"

    def print_ci_performance_summary(self):
        """Prints a CI-friendly performance summary."""
        stats = self.get_performance_stats()
        if not stats:
            print("⚠️  No performance data available")
            return
            
        print("\n" + "="*60)
        print("🚀 OMNIPKG PERFORMANCE REPORT")
        print("="*60)
        print(f"Package: {stats['package_spec']}")
        print(f"Python Version: {stats['python_version']}")
        print(f"Activation:   {stats['activation_time_us']:>8.3f} μs ({stats['activation_time_ns']:>10,} ns)")
        print(f"Deactivation: {stats['deactivation_time_us']:>8.3f} μs ({stats['deactivation_time_ns']:>10,} ns)")
        print(f"TOTAL SWAP:   {stats['total_swap_time_us']:>8.3f} μs ({stats['total_swap_time_ns']:>10,} ns)")
        print(f"Speed Class:  {stats['swap_speed_description']}")
        print("="*60)
        print("🛡️ Version-isolated environment - ZERO cross-contamination!")
        print("="*60 + "\n")

    def _get_package_modules(self, pkg_name: str):
        """Helper to find all modules related to a package in sys.modules."""
        pkg_name_normalized = pkg_name.replace('-', '_')
        return [mod for mod in list(sys.modules.keys()) 
                if mod.startswith(pkg_name_normalized + '.') or 
                   mod == pkg_name_normalized or 
                   mod.replace('_', '-').startswith(pkg_name.lower())]

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
        
        # Check various possible locations for the package
        paths_to_check = [
            self.site_packages_root / canonical_pkg_name,
            next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.dist-info'), None),
            next(self.site_packages_root.glob(f'{canonical_pkg_name}-*.egg-info'), None),
            self.site_packages_root / f'{canonical_pkg_name}.py'
        ]
        
        for original_path in paths_to_check:
            if original_path and original_path.exists():
                timestamp = int(time.time() * 1000)
                if original_path.is_dir():
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked')
                else:
                    cloak_path = original_path.with_name(f'{original_path.name}.{timestamp}_omnipkg_cloaked{original_path.suffix}')
                
                cloak_record = (original_path, cloak_path, False)
                
                # Clean up any existing cloak with the same name
                if cloak_path.exists():
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                    except Exception as e:
                        print(_(' ⚠️ Warning: Could not remove existing cloak {}: {}').format(cloak_path.name, e))
                
                try:
                    shutil.move(str(original_path), str(cloak_path))
                    cloak_record = (original_path, cloak_path, True)
                    print(_(' 🛡️ Cloaked main {} to {}').format(original_path.name, cloak_path.name))
                except Exception as e:
                    print(_(' ⚠️ Failed to cloak {}: {}').format(original_path.name, e))
                
                self._cloaked_main_modules.append(cloak_record)

    def _restore_cloaked_modules(self):
        """Restore all cloaked modules, with better error handling."""
        restored_count = 0
        failed_count = 0
        
        for original_path, cloak_path, was_successful in reversed(self._cloaked_main_modules):
            if not was_successful:
                continue
                
            if cloak_path.exists():
                # Remove any conflicting path that might have been created
                if original_path.exists():
                    try:
                        if original_path.is_dir():
                            shutil.rmtree(original_path, ignore_errors=True)
                        else:
                            os.unlink(original_path)
                    except Exception as e:
                        print(_(' ⚠️ Warning: Could not remove conflicting path {}: {}').format(original_path.name, e))
                
                try:
                    shutil.move(str(cloak_path), str(original_path))
                    print(_(' 🛡️ Restored {}').format(original_path.name))
                    restored_count += 1
                except Exception as e:
                    print(_(' ❌ Failed to restore {} from {}: {}').format(original_path.name, cloak_path.name, e))
                    failed_count += 1
                    # Try to clean up the orphaned cloak
                    try:
                        if cloak_path.is_dir():
                            shutil.rmtree(cloak_path, ignore_errors=True)
                        else:
                            os.unlink(cloak_path)
                        print(_(' 🧹 Cleaned up orphaned cloak {}').format(cloak_path.name))
                    except:
                        pass
            else:
                print(_(' ❌ CRITICAL: Cloaked path {} is missing! Package {} may be lost.').format(cloak_path.name, original_path.name))
                failed_count += 1
                
                # Check if package is still available in system
                pkg_name = self._current_package_spec.split('==')[0] if self._current_package_spec else 'unknown'
                try:
                    get_version(pkg_name)
                    print(_(' ℹ️ Package {} still appears to be installed in system.').format(pkg_name))
                except PackageNotFoundError:
                    print(_(' ❌ Package {} is no longer available in system. Consider reinstalling.').format(pkg_name))
                    print(_('   Suggestion: pip install --force-reinstall --no-deps {}').format(pkg_name))
        
        self._cloaked_main_modules.clear()
    
        if failed_count > 0:
            print(_(' ⚠️ Cloak restore summary: {} successful, {} failed').format(restored_count, failed_count))

    def _panic_restore_cloaks(self):
        """Emergency cloak restoration when activation fails."""
        print(_(' 🚨 Emergency cloak restoration in progress...'))
        self._restore_cloaked_modules()

    def cleanup_abandoned_cloaks(self):
        """
        Utility method to clean up any abandoned cloak files.
        Can be called manually if you suspect there are leftover cloaks.
        """
        print(_('🧹 Scanning for abandoned omnipkg cloaks...'))
        cloak_pattern = '*_omnipkg_cloaked*'
        found_cloaks = list(self.site_packages_root.glob(cloak_pattern))
        
        if not found_cloaks:
            print(_(' ✅ No abandoned cloaks found.'))
            return
        
        print(_(' 🔍 Found {} potential abandoned cloak(s):').format(len(found_cloaks)))
        for cloak_path in found_cloaks:
            print(_('   - {}').format(cloak_path.name))
        
        print(_(' ℹ️ To remove these manually: rm -rf /path/to/site-packages/*_omnipkg_cloaked*'))
        print(_(" ⚠️ WARNING: Only remove if you're sure no omnipkg operations are running!"))

    def debug_sys_path(self):
        """Debug helper to print current sys.path state."""
        print(_('\n🔍 DEBUG: Current sys.path ({} entries):').format(len(sys.path)))
        for i, path in enumerate(sys.path):
            path_obj = Path(path)
            status = "✅" if path_obj.exists() else "❌"
            print(_('   [{}] {} {}').format(i, status, path))
        print()

    def debug_omnipkg_dependencies(self):
        """Debug helper to show detected omnipkg dependencies."""
        print(_('\n🔍 DEBUG: Detected omnipkg dependencies:'))
        if not self._omnipkg_dependencies:
            print(_('   ❌ No dependencies detected'))
            return
        
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            status = "✅" if dep_path.exists() else "❌"
            print(_('   {} {}: {}').format(status, dep_name, dep_path))
        print()