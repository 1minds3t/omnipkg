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
    Designed to be used as a context manager for seamless, temporary version switching.
    Now with improved subprocess support that preserves access to omnipkg's own dependencies.

    Usage:
        from omnipkg.loader import omnipkgLoader
        from omnipkg.core import ConfigManager # Recommended to pass config

        config_manager = ConfigManager() # Get your omnipkg config
        
        with omnipkgLoader("my-package==1.2.3", config=config_manager.config):
            import my_package
            print(my_package.__version__)
        # Outside the 'with' block, the environment is restored
        # to its original state (e.g., system's my_package version)
    """

    def __init__(self, package_spec: str=None, config: dict=None):
        """
        Initializes the loader. If used as a context manager, package_spec is required.
        Config is highly recommended for robust path discovery.
        """
        self.config = config
        if self.config and 'multiversion_base' in self.config and ('site_packages_path' in self.config):
            self.multiversion_base = Path(self.config['multiversion_base'])
            self.site_packages_root = Path(self.config['site_packages_path'])
        else:
            print(_('⚠️ [omnipkg loader] Config not provided or incomplete. Attempting auto-detection of paths.'))
            try:
                self.site_packages_root = Path(site.getsitepackages()[0])
                self.multiversion_base = self.site_packages_root / '.omnipkg_versions'
            except (IndexError, AttributeError):
                print(_('⚠️ [omnipkg loader] Could not auto-detect site-packages path reliably. Falling back to sys.prefix.'))
                python_version = f'python{sys.version_info.major}.{sys.version_info.minor}'
                self.site_packages_root = Path(sys.prefix) / 'lib' / python_version / 'site-packages'
                self.multiversion_base = self.site_packages_root / '.omnipkg_versions'
        
        if not self.multiversion_base.exists():
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                print(_('⚠️ [omnipkg loader] Bubble directory {} did not exist and was created.').format(self.multiversion_base))
            except Exception as e:
                raise RuntimeError(_('Failed to create bubble directory at {}: {}').format(self.multiversion_base, e))
        
        # Store original state
        self.original_sys_path = sys.path.copy()
        self.original_sys_modules_keys = set(sys.modules.keys())
        self.original_path_env = os.environ.get('PATH', '')
        self.original_pythonpath_env = os.environ.get('PYTHONPATH', '')
        
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

    def _detect_omnipkg_dependencies(self):
        """
        Detect critical omnipkg dependencies that need to remain accessible
        even when a bubble is active. This is crucial for subprocess operations.
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
                    # Make sure it's in our site-packages
                    if self.site_packages_root in dep_path.parents or dep_path == self.site_packages_root / dep:
                        found_deps[dep] = dep_path
            except (ImportError, Exception):
                continue
        
        # Summarized logging
        if found_deps:
            print(_('🔧 [omnipkg loader] Detected {} critical dependencies for subprocess support').format(len(found_deps)))
        
        return found_deps

    def _ensure_omnipkg_access_in_bubble(self, bubble_path_str: str):
        """
        Ensure omnipkg's dependencies remain accessible when bubble is active.
        Creates symlinks ONLY if the dependency doesn't already exist in the bubble,
        preventing corruption of the bubble's own package versions.
        """
        bubble_path = Path(bubble_path_str)
        linked_count = 0
        preserved_count = 0
        
        for dep_name, dep_path in self._omnipkg_dependencies.items():
            # Check if dependency already exists in bubble (directory or file)
            bubble_dep_dir = bubble_path / dep_name
            bubble_dep_file = bubble_path / f"{dep_name}.py"
            
            # If the dependency ALREADY exists in the bubble, preserve it
            if bubble_dep_dir.exists() or bubble_dep_file.exists():
                preserved_count += 1
                continue
            
            # Safe to create symlink - dependency doesn't exist in bubble
            try:
                if dep_path.is_dir():
                    bubble_dep_dir.symlink_to(dep_path, target_is_directory=True)
                else:
                    bubble_dep_file.symlink_to(dep_path)
                linked_count += 1
            except Exception as e:
                # Fallback: add the original site-packages to sys.path in a strategic position
                if str(self.site_packages_root) not in sys.path:
                    insertion_point = 1 if len(sys.path) > 1 else len(sys.path)
                    sys.path.insert(insertion_point, str(self.site_packages_root))
        
        # Summarized logging
        if linked_count > 0 or preserved_count > 0:
            print(_(' 🔗 Dependency management: {} linked, {} preserved for subprocess support').format(linked_count, preserved_count))
    
    def _cleanup_omnipkg_links_in_bubble(self, bubble_path_str: str):
        """
        Clean up symlinks created for omnipkg dependencies in the bubble.
        Only removes symlinks, never touches actual package directories.
        """
        bubble_path = Path(bubble_path_str)
        cleaned_count = 0
        
        for dep_name in self._omnipkg_dependencies.keys():
            bubble_dep_path = bubble_path / dep_name
            
            # Only clean up if it's actually a symlink we created
            if bubble_dep_path.is_symlink():
                try:
                    bubble_dep_path.unlink()
                    cleaned_count += 1
                except Exception:
                    pass  # Silent cleanup - don't spam logs
        
        # Summarized logging
        if cleaned_count > 0:
            print(_(' 🧹 Cleaned up {} dependency symlinks').format(cleaned_count))
            
       

    def __enter__(self):
        """Activates the specified package snapshot for the 'with' block."""
        self._activation_start_time = time.perf_counter_ns()
        
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec (e.g., 'pkg==ver') when used as a context manager.")
        
        print(_('\n🌀 omnipkg loader: Activating {}...').format(self._current_package_spec))
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
            pkg_name_normalized = pkg_name.lower().replace('-', '_')
        except ValueError:
            raise ValueError(_("Invalid package_spec format. Expected 'name==version', got '{}'.").format(self._current_package_spec))
        
        # Check if system version already matches
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
            
            # Rebuild sys.path with bubble taking precedence
            sys.path.clear()
            sys.path.insert(0, bubble_path_str)
            
            # Add back original paths, skipping the original site-packages
            for p in self.original_sys_path:
                if Path(p).resolve() == self.site_packages_root.resolve():
                    continue
                if p not in sys.path:
                    sys.path.append(p)
            
            # Ensure omnipkg dependencies are accessible for subprocess operations
            self._ensure_omnipkg_access_in_bubble(bubble_path_str)
            
            # Add the original site-packages as a fallback (but lower priority)
            if str(self.site_packages_root) not in sys.path:
                sys.path.append(str(self.site_packages_root))
            
            self._activated_bubble_path = bubble_path_str
            
            self._activation_end_time = time.perf_counter_ns()
            self._total_activation_time_ns = self._activation_end_time - self._activation_start_time
            
            print(_(' ✅ Activated bubble: {}').format(bubble_path_str))
            print(_(' 🔧 sys.path[0]: {}').format(sys.path[0]))
            print(_(' 🔗 Ensured omnipkg dependency access for subprocess support'))
            print(_(' ⏱️  Activation time: {:.3f} μs ({:,} ns)').format(self._total_activation_time_ns / 1000, self._total_activation_time_ns))
            
            # Show bubble info
            manifest_path = bubble_path / '.omnipkg_manifest.json'
            if manifest_path.exists():
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    pkg_count = len(manifest.get('packages', {}))
                    print(_(' ℹ️ Bubble contains {} packages.').format(pkg_count))
            
            self._activation_successful = True
            return self
            
        except Exception as e:
            print(_(' ❌ Activation failed: {}').format(str(e)))
            self._panic_restore_cloaks()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Deactivates the snapshot and restores the environment to its original state."""
        self._deactivation_start_time = time.perf_counter_ns()
        
        print(_('\n🌀 omnipkg loader: Deactivating {}...').format(self._current_package_spec))
        
        if not self._activation_successful and (not self._cloaked_main_modules):
            return
        
        pkg_name = self._current_package_spec.split('==')[0]
        
        # Clean up omnipkg dependency links if we created them
        if self._activated_bubble_path:
            self._cleanup_omnipkg_links_in_bubble(self._activated_bubble_path)
        
        # Restore cloaked modules
        self._restore_cloaked_modules()
        
        # Restore sys.path
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
        
        # Restore environment variables
        os.environ['PATH'] = self.original_path_env
        if self.original_pythonpath_env:
            os.environ['PYTHONPATH'] = self.original_pythonpath_env
        elif 'PYTHONPATH' in os.environ:
            del os.environ['PYTHONPATH']
        
        # Invalidate import caches and collect garbage
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()
        gc.collect()
        
        self._deactivation_end_time = time.perf_counter_ns()
        self._total_deactivation_time_ns = self._deactivation_end_time - self._deactivation_start_time
        total_swap_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        
        print(_(' ✅ Environment restored to system state.'))
        print(_(' ⏱️  Deactivation time: {:.3f} μs ({:,} ns)').format(self._total_deactivation_time_ns / 1000, self._total_deactivation_time_ns))
        print(_(' 🎯 TOTAL SWAP TIME: {:.3f} μs ({:,} ns)').format(total_swap_time_ns / 1000, total_swap_time_ns))

    def get_performance_stats(self):
        """Returns detailed performance statistics for CI/logging purposes."""
        if self._total_activation_time_ns is None or self._total_deactivation_time_ns is None:
            return None
        
        total_time_ns = self._total_activation_time_ns + self._total_deactivation_time_ns
        return {
            'package_spec': self._current_package_spec,
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
        print(f"Activation:   {stats['activation_time_us']:>8.3f} μs ({stats['activation_time_ns']:>10,} ns)")
        print(f"Deactivation: {stats['deactivation_time_us']:>8.3f} μs ({stats['deactivation_time_ns']:>10,} ns)")
        print(f"TOTAL SWAP:   {stats['total_swap_time_us']:>8.3f} μs ({stats['total_swap_time_ns']:>10,} ns)")
        print(f"Speed Class:  {stats['swap_speed_description']}")
        print("="*60)
        print("🎯 Same environment, same script runtime - ZERO downtime swapping!")
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
        """
        Temporarily renames the main environment installation of a package
        and its .dist-info/ .egg-info directories to hide them.
        Now with improved error handling and tracking.
        """
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