# Enhanced loader for better C extension handling

import sys
import json
from pathlib import Path
import site
from importlib.metadata import version as get_version, PackageNotFoundError
import importlib

class omnipkgLoader:
    """
    Activates isolated package environments (bubbles) created by omnipkg,
    or confirms if the requested version is already active in the system.
    """
    def __init__(self):
        # Auto-discover the multiversion base path from the installed package location
        try:
            site_packages_path = next(p for p in sys.path if 'site-packages' in p and Path(p).is_dir())
            self.multiversion_base = Path(site_packages_path) / ".omnipkg_versions"
        except StopIteration:
            print("⚠️ [omnipkg loader] Could not auto-detect site-packages path.")
            self.multiversion_base = None
        
        # Track active bubbles for cleanup
        self.active_bubbles = set()
        self.original_sys_path = sys.path.copy()

    def _get_package_modules(self, pkg_name: str):
        """Get all modules related to a package"""
        pkg_name_normalized = pkg_name.replace('-', '_')
        return [mod for mod in list(sys.modules.keys()) 
                if mod.startswith(pkg_name_normalized) or 
                   mod.replace('_', '-').startswith(pkg_name)]

    def _aggressive_module_cleanup(self, pkg_name: str):
        """Aggressively clean package modules from sys.modules"""
        modules_to_clear = self._get_package_modules(pkg_name)
        
        for mod_name in modules_to_clear:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        
        # Force garbage collection
        import gc
        gc.collect()
        
        # Invalidate import caches
        if hasattr(importlib, 'invalidate_caches'):
            importlib.invalidate_caches()

    def _deactivate_package_bubbles(self, pkg_name: str):
        """Remove any active bubbles for the given package from sys.path"""
        if not self.multiversion_base:
            return
            
        bubbles_to_remove = []
        for path_str in sys.path[:]:  # Create a copy to iterate over
            path = Path(path_str)
            # Check if this path is a bubble for the given package
            if (path.parent == self.multiversion_base and 
                path.name.startswith(f"{pkg_name}-")):
                bubbles_to_remove.append(path_str)
                
        for bubble_path in bubbles_to_remove:
            sys.path.remove(bubble_path)
            self.active_bubbles.discard(bubble_path)
            print(f" 🧹 Deactivated bubble: {Path(bubble_path).name}")

    def _prioritize_bubble_in_path(self, bubble_path_str: str):
        """Ensure bubble is at the very front of sys.path"""
        # Remove from current position if it exists
        if bubble_path_str in sys.path:
            sys.path.remove(bubble_path_str)
        
        # Insert at position 0 (highest priority)
        sys.path.insert(0, bubble_path_str)
        
        # Also remove any site-packages paths that might contain the same package
        # and move them after our bubble
        site_packages_paths = []
        for i, path in enumerate(sys.path[1:], 1):  # Skip our bubble at index 0
            if 'site-packages' in path and Path(path).exists():
                site_packages_paths.append((i, path))
        
        # Move site-packages to after our bubble (but keep their relative order)
        for i, (original_index, path) in enumerate(reversed(site_packages_paths)):
            sys.path.remove(path)
            sys.path.insert(i + 1, path)  # Insert after bubble

    def activate_snapshot(self, package_spec: str) -> bool:
        """
        Activates a specific package version bubble, or confirms if the
        version is already the active system version.
        Example: activate_snapshot("flask-login==0.4.1")
        """
        print(f"\n🌀 omnipkg loader: Activating {package_spec}...")
        
        try:
            pkg_name, requested_version = package_spec.split('==')
        except ValueError:
            print(f" ❌ Invalid package_spec format. Expected 'name==version', got '{package_spec}'.")
            return False

        # First, deactivate any existing bubbles for this package
        self._deactivate_package_bubbles(pkg_name)
        
        # Aggressively clear modules for this package (silent)
        self._aggressive_module_cleanup(pkg_name)

        # Check if we need a bubble (system version doesn't match)
        try:
            active_version = get_version(pkg_name)
            if active_version == requested_version:
                print(f" ✅ System version already matches requested version ({active_version}). No bubble activation needed.")
                return True
        except PackageNotFoundError:
            # The package isn't in the main environment, so we must use a bubble.
            pass

        # Find and activate the bubble
        if not self.multiversion_base or not self.multiversion_base.exists():
            print(f" ❌ Bubble directory not found at {self.multiversion_base}")
            return False

        try:
            bubble_dir_name = f"{pkg_name}-{requested_version}"
            bubble_path = self.multiversion_base / bubble_dir_name
            
            if not bubble_path.is_dir():
                print(f" ❌ Bubble not found for {package_spec} at {bubble_path}")
                return False

            bubble_path_str = str(bubble_path)
            
            # Prioritize this bubble in sys.path
            self._prioritize_bubble_in_path(bubble_path_str)
            self.active_bubbles.add(bubble_path_str)
            
            print(f" ✅ Activated bubble: {bubble_path_str}")
            print(f" 🔧 sys.path[0]: {sys.path[0]}")
            
            # Show bubble info if manifest exists
            manifest_path = bubble_path / '.omnipkg_manifest.json'
            if manifest_path.exists():
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    pkg_count = len(manifest.get('packages', {}))
                    print(f" ℹ️ Bubble contains {pkg_count} packages.")
            
            return True
            
        except Exception as e:
            print(f" ❌ Error during bubble activation for {package_spec}: {e}")
            return False

    def reset_environment(self):
        """Reset sys.path to its original state"""
        print(" 🔄 Resetting environment to original state...")
        sys.path.clear()
        sys.path.extend(self.original_sys_path)
        self.active_bubbles.clear()