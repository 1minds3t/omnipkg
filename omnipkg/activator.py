import sys
import os
import importlib.util
from pathlib import Path
from omnipkg.core import ConfigManager
from omnipkg.i18n import _

def get_config():
    """Load config using ConfigManager"""
    config_manager = ConfigManager()
    return config_manager.config

def get_multiversion_base():
    """Get base path from config with fallbacks"""
    try:
        config = get_config()
        base = config.get('multiversion_base')
        if base:
            return base
    except Exception:
        pass
    return str(Path(__file__).parent.parent / '.omnipkg_versions')

class ImportHookManager:

    def __init__(self, multiversion_base: str):
        self.multiversion_base = Path(multiversion_base)
        self.version_map = {}
        self.load_version_map()

    def load_version_map(self):
        if not self.multiversion_base.exists():
            return
        for version_dir in self.multiversion_base.iterdir():
            if version_dir.is_dir() and '-' in version_dir.name:
                parts = version_dir.name.rsplit('-', 1)
                if len(parts) == 2:
                    pkg_name, version = parts
                    if pkg_name not in self.version_map:
                        self.version_map[pkg_name] = {}
                    self.version_map[pkg_name][version] = str(version_dir)

    def get_package_path(self, package_name: str, version: str):
        return self.version_map.get(package_name.lower(), {}).get(version)

class MultiversionFinder:

    def __init__(self, hook_manager: ImportHookManager):
        self.hook_manager = hook_manager

    def find_spec(self, fullname, path, target=None):
        top_level = fullname.split('.')[0]
        env_var_name = _('_omnipkg_ACTIVE_{}').format(top_level.upper().replace('-', '_'))
        activated_version = os.environ.get(env_var_name)
        if activated_version:
            pkg_path = self.hook_manager.get_package_path(top_level, activated_version)
            if pkg_path:
                module_path = Path(pkg_path) / top_level
                if module_path.is_dir() and (module_path / '__init__.py').exists():
                    return importlib.util.spec_from_file_location(fullname, str(module_path / '__init__.py'), submodule_search_locations=[str(module_path)])
        return None
_hook_manager = ImportHookManager(get_multiversion_base())

def install_hook():
    """Installs the omnipkg import hook if not already active"""
    if not any((isinstance(finder, MultiversionFinder) for finder in sys.meta_path)):
        sys.meta_path.insert(0, MultiversionFinder(_hook_manager))