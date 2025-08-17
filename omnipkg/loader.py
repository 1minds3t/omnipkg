import sys
import importlib
import shutil
import time
import gc # For aggressive garbage collection
from pathlib import Path
import os
import subprocess
import json # To read manifest files from bubbles
import site # Import site to get site-packages path reliably
from importlib.metadata import version as get_version, PackageNotFoundError # Added for direct version check

class omnipkgLoader:
    """
    Activates isolated package environments (bubbles) created by omnipkg.
    Designed to be used as a context manager for seamless, temporary version switching.

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
    
    def __init__(self, package_spec: str = None, config: dict = None):
        """
        Initializes the loader. If used as a context manager, package_spec is required.
        Config is highly recommended for robust path discovery.
        """
        self.config = config # Store the config

        # Determine multiversion_base and site_packages_root using config or auto-detection
        if self.config and "multiversion_base" in self.config and "site_packages_path" in self.config:
            self.multiversion_base = Path(self.config["multiversion_base"])
            self.site_packages_root = Path(self.config["site_packages_path"])
        else:
            # Fallback logic if config is not provided or incomplete
            print("⚠️ [omnipkg loader] Config not provided or incomplete. Attempting auto-detection of paths.")
            try:
                # Use site.getsitepackages for the most reliable site-packages path
                self.site_packages_root = Path(site.getsitepackages()[0])
                self.multiversion_base = self.site_packages_root / ".omnipkg_versions"
            except (IndexError, AttributeError):
                # Fallback if site.getsitepackages fails (e.g., in weird virtualenvs)
                print("⚠️ [omnipkg loader] Could not auto-detect site-packages path reliably. Falling back to sys.prefix.")
                self.site_packages_root = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
                self.multiversion_base = self.site_packages_root / ".omnipkg_versions"
            
        # Ensure the multiversion base directory exists
        if not self.multiversion_base.exists():
            # If it doesn't exist, try to create it. If it fails, let the error propagate.
            try:
                self.multiversion_base.mkdir(parents=True, exist_ok=True)
                print(f"⚠️ [omnipkg loader] Bubble directory {self.multiversion_base} did not exist and was created.")
            except Exception as e:
                raise RuntimeError(f"Failed to create bubble directory at {self.multiversion_base}: {e}")


        # Store original environment state for restoration
        self.original_sys_path = sys.path.copy()
        self.original_sys_modules_keys = set(sys.modules.keys()) # Only track keys to avoid deep copying module objects
        self.original_path_env = os.environ.get('PATH', '')
        self.original_pythonpath_env = os.environ.get('PYTHONPATH', '')

        self._current_package_spec = package_spec
        self._activated_bubble_path = None
        self._cloaked_main_modules = [] # Stores list of (original_path, cloaked_path) tuples for restoration

    def __enter__(self):
        """Activates the specified package snapshot for the 'with' block."""
        if not self._current_package_spec:
            raise ValueError("omnipkgLoader must be instantiated with a package_spec (e.g., 'pkg==ver') when used as a context manager.")
        
        print(f"\n🌀 omnipkg loader: Activating {self._current_package_spec}...")
        
        try:
            pkg_name, requested_version = self._current_package_spec.split('==')
            pkg_name_normalized = pkg_name.lower().replace('-', '_') # For matching bubble dir names
        except ValueError:
            raise ValueError(f"Invalid package_spec format. Expected 'name==version', got '{self._current_package_spec}'.")

        # --- CRITICAL FIX: Handle case where requested version is already the system version ---
        try:
            current_system_version = get_version(pkg_name)
            if current_system_version == requested_version:
                print(f" ✅ System version already matches requested version ({current_system_version}). No bubble activation needed.")
                self._activated_bubble_path = None # Indicate that no bubble was used
                return self # System environment is already the target.
        except PackageNotFoundError:
            # Package not found in the main env, proceed to cloak and activate bubble.
            pass
        except Exception as e:
            # Catch other potential errors during get_version, treat as not found.
            print(f"⚠️ [omnipkg loader] Error checking system version for {pkg_name}: {e}. Proceeding with bubble search.")
            pass


        # If we reach here, the requested version is NOT the current active system version,
        # so we proceed with cloaking and bubble activation.

        # 1. Aggressively clear modules for this package from sys.modules
        self._aggressive_module_cleanup(pkg_name)

        # 2. Temporarily hide the main environment's installation of the package
        self._cloak_main_package(pkg_name)
        
        # 3. Find and activate the bubble
        # self.multiversion_base is guaranteed to exist by __init__
        
        bubble_dir_name = f"{pkg_name_normalized}-{requested_version}"
        bubble_path = self.multiversion_base / bubble_dir_name
        
        if not bubble_path.is_dir():
            # If the bubble doesn't exist, this is a failure.
            raise RuntimeError(f"Bubble not found for {self._current_package_spec} at {bubble_path}. "
                               f"Please ensure it's installed via 'omnipkg install {self._current_package_spec}'.")
        
        bubble_path_str = str(bubble_path)
        
        # Adjust PATH environment variable for executables inside the bubble
        bubble_bin_path = bubble_path / "bin"
        if bubble_bin_path.is_dir():
            os.environ['PATH'] = f"{str(bubble_bin_path)}{os.pathsep}{self.original_path_env}"
            print(f" ⚙️ Added to PATH: {bubble_bin_path}")

        # Manipulate sys.path: clear it and insert bubble path first, then original non-site-packages paths
        sys.path.clear()
        sys.path.insert(0, bubble_path_str)
        
        # Add original paths back, ensuring the bubble path remains at the front
        # We also need to avoid adding the cloaked main site-packages if it was previously in sys.path.
        # This also re-adds common paths like /usr/lib/pythonX.Y/ or virtualenv's lib-dynload.
        for p in self.original_sys_path:
            if Path(p).resolve() == self.site_packages_root.resolve(): # Avoid re-adding the original site-packages that was cloaked
                continue
            if p not in sys.path: # Avoid duplicates
                sys.path.append(p)

        self._activated_bubble_path = bubble_path_str
        
        print(f" ✅ Activated bubble: {bubble_path_str}")
        print(f" 🔧 sys.path[0]: {sys.path[0]}")
        
        # Show bubble info if manifest exists
        manifest_path = bubble_path / '.omnipkg_manifest.json'
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                pkg_count = len(manifest.get('packages', {}))
                print(f" ℹ️ Bubble contains {pkg_count} packages.")
        
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Deactivates the snapshot and restores the environment to its original state."""
        print(f"\n🌀 omnipkg loader: Deactivating {self._current_package_spec}...")
        
        pkg_name = self._current_package_spec.split('==')[0]

        # 1. Restore cloaked main modules
        for original_path, cloaked_path in reversed(self._cloaked_main_modules):
            if cloaked_path.exists():
                # Ensure original path is clear before moving back
                if original_path.exists():
                    # Attempt to remove existing dir if it somehow reappeared
                    shutil.rmtree(original_path, ignore_errors=True) 
                try:
                    shutil.move(cloaked_path, original_path)
                    print(f" 🛡️ Restored {original_path.name}")
                except Exception as e:
                    print(f" ⚠️ Failed to restore {original_path.name} from {cloaked_path.name}: {e}")
            else:
                print(f" ⚠️ Cloaked path {cloaked_path.name} not found during restore. Already gone?")
        self._cloaked_main_modules.clear()

        # 2. Restore original sys.path
        sys.path.clear()
        sys.path.extend(self.original_sys_path)

        # 3. Restore original sys.modules
        # Clear all modules that were not present before activation, or
        # reload modules that might have been affected.
        current_modules_keys = set(sys.modules.keys())
        for mod_name in current_modules_keys:
            if mod_name not in self.original_sys_modules_keys:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
        
        # For the package itself and its submodules, run aggressive cleanup one last time
        # This ensures fresh import from the restored main environment.
        self._aggressive_module_cleanup(pkg_name) 

        # 4. Restore original environment variables
        os.environ['PATH'] = self.original_path_env
        if self.original_pythonpath_env: # Only restore if it was originally set
            os.environ['PYTHONPATH'] = self.original_pythonpath_env 
        else:
            if 'PYTHONPATH' in os.environ:
                del os.environ['PYTHONPATH'] # Remove it if it wasn't there originally

        # 5. Invalidate import caches for a clean slate
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()
        
        # 6. Force garbage collection to release old module objects
        gc.collect()

        print(f" ✅ Environment restored to system state.")

    def _get_package_modules(self, pkg_name: str):
        """Helper to find all modules related to a package in sys.modules."""
        pkg_name_normalized = pkg_name.replace('-', '_') # Handle 'flask-login' vs 'flask_login'
        return [mod for mod in list(sys.modules.keys())
                if mod.startswith(pkg_name_normalized + '.') or # Submodules (e.g., flask_login.config)
                   mod == pkg_name_normalized or                 # Top-level module (e.g., flask_login)
                   mod.replace('_', '-').startswith(pkg_name.lower()) # Canonical name check
                ]

    def _aggressive_module_cleanup(self, pkg_name: str):
        """Removes specified package's modules from sys.modules and invalidates caches."""
        modules_to_clear = self._get_package_modules(pkg_name)
        
        for mod_name in modules_to_clear:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        
        gc.collect() # Ensure objects are garbage collected
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()

    def _cloak_main_package(self, pkg_name: str):
        """
        Temporarily renames the main environment installation of a package
        and its .dist-info/ .egg-info directories to hide them.
        """
        canonical_pkg_name = pkg_name.lower().replace('-', '_')
        
        # List of potential paths to cloak: module dir, dist-info, egg-info, single-file .py
        paths_to_check = [
            self.site_packages_root / canonical_pkg_name, # e.g., site-packages/flask_login/
            next(self.site_packages_root.glob(f"{canonical_pkg_name}-*.dist-info"), None), # e.g., site-packages/flask_login-X.Y.Z.dist-info/
            next(self.site_packages_root.glob(f"{canonical_pkg_name}-*.egg-info"), None),  # For older installations
            self.site_packages_root / f"{canonical_pkg_name}.py", # For single-file packages (e.g., simple scripts)
        ]
        
        for original_path in paths_to_check:
            if original_path and original_path.exists():
                timestamp = int(time.time() * 1000)
                # Create a unique cloaked name
                if original_path.is_dir():
                    cloak_path = original_path.with_name(f"{original_path.name}.{timestamp}_omnipkg_cloaked")
                else: # Must be a file (e.g., .py file)
                    cloak_path = original_path.with_name(f"{original_path.name}.{timestamp}_omnipkg_cloaked{original_path.suffix}")

                # Clean up any previous cloaked version *of this specific original path* for safety
                if cloak_path.exists():
                    if cloak_path.is_dir():
                        shutil.rmtree(cloak_path, ignore_errors=True)
                    else:
                        os.unlink(cloak_path)

                try:
                    shutil.move(original_path, cloak_path)
                    self._cloaked_main_modules.append((original_path, cloak_path))
                    print(f" 🛡️ Cloaked main {original_path.name} to {cloak_path.name}")
                except Exception as e:
                    print(f" ⚠️ Failed to cloak {original_path.name}: {e}")