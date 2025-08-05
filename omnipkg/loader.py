import sys
import json
from pathlib import Path
import site
from importlib.metadata import version as get_version, PackageNotFoundError

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

    def _deactivate_package_bubbles(self, pkg_name: str):
        """Remove any active bubbles for the given package from sys.path"""
        if not self.multiversion_base:
            return
            
        bubbles_to_remove = []
        for path_str in sys.path:
            path = Path(path_str)
            # Check if this path is a bubble for the given package
            if (path.parent == self.multiversion_base and 
                path.name.startswith(f"{pkg_name}-")):
                bubbles_to_remove.append(path_str)
                
        for bubble_path in bubbles_to_remove:
            sys.path.remove(bubble_path)
            self.active_bubbles.discard(bubble_path)
            print(f" 🧹 Deactivated bubble: {Path(bubble_path).name}")

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
        
        # Clear any cached imports for this package to ensure fresh import
        modules_to_clear = [mod for mod in sys.modules.keys() if mod.startswith(pkg_name.replace('-', '_'))]
        for mod in modules_to_clear:
            del sys.modules[mod]

        # Check if the currently installed system version matches after bubble cleanup
        try:
            active_version = get_version(pkg_name)
            if active_version == requested_version:
                print(f" ✅ System version already matches requested version ({active_version}). No bubble activation needed.")
                return True
        except PackageNotFoundError:
            # The package isn't in the main environment, so we must use a bubble.
            pass

        # If the system version doesn't match, proceed to find and activate a bubble.
        if not self.multiversion_base or not self.multiversion_base.exists():
            print(f" ❌ Bubble directory not found at {self.multiversion_base}")
            return False

        try:
            bubble_dir_name = f"{pkg_name}-{requested_version}"
            bubble_path = self.multiversion_base / bubble_dir_name
            
            if not bubble_path.is_dir():
                print(f" ❌ Bubble not found for {package_spec} at {bubble_path}")
                return False

            # Activate the bubble by putting it at the front of sys.path
            bubble_path_str = str(bubble_path)
            sys.path.insert(0, bubble_path_str)
            self.active_bubbles.add(bubble_path_str)
            
            print(f" ✅ Activated bubble: {bubble_path_str}")
            
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