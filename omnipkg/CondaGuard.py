from __future__ import annotations  # Python 3.6+ compatibility
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
"""
CondaGuard: A lightweight, robust system to protect Conda environments
from metadata corruption during high-intensity filesystem operations like
hotswapping, bubbling, or cloud sync interference.

Can be used as a decorator, a context manager, or a standalone fixer.
"""
import os
import shutil
import functools
import json
import logging
from omnipkg.i18n import _
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(asctime)s - CondaGuard - %(levelname)s - %(message)s')

class CondaGuard:
    """
    Protects critical Conda metadata files by creating temporary backups
    before a sensitive operation and restoring them if corruption is detected.
    """
    PROTECTED_FILES = {'/opt/conda/envs/evocoder_env/conda-meta/nodejs-24.4.1-heeeca48_0.json'}

    def __init__(self):
        self._backup_paths = {}

    def __enter__(self):
        """Context manager entry: backup protected files."""
        self.backup()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Context manager exit: check and restore if needed."""
        restored_files = self.restore()
        if restored_files:
            logging.warning(_('🛡️ Restored corrupted metadata for: {}').format(', '.join(restored_files)))
        else:
            logging.info('✅ Post-op check complete. All protected files are healthy.')

    def backup(self):
        """Creates temporary backups of all protected files."""
        self._backup_paths.clear()
        for filepath_str in self.PROTECTED_FILES:
            src_path = Path(filepath_str)
            if src_path.exists() and src_path.stat().st_size > 0:
                backup_path = src_path.with_suffix(f'{src_path.suffix}.guardbak')
                try:
                    shutil.copy2(src_path, backup_path)
                    self._backup_paths[src_path] = backup_path
                    logging.info(f"Backed up '{src_path.name}'")
                except Exception as e:
                    logging.error(_("Failed to back up '{}': {}").format(src_path.name, e))
            else:
                logging.warning(f"Source file for backup not found or is empty: '{filepath_str}'")

    def restore(self) -> list:
        """
        Checks all protected files for corruption (missing, empty, or invalid JSON).
        If corrupted, restores it from its backup. Returns a list of restored files.
        """
        restored = []
        for src_path, backup_path in self._backup_paths.items():
            is_corrupted = False
            if not src_path.exists() or src_path.stat().st_size == 0:
                is_corrupted = True
                logging.warning(_("Detected corruption (file missing or empty): '{}'").format(src_path.name))
            elif src_path.suffix == '.json':
                try:
                    with open(src_path, 'r') as f:
                        json.load(f)
                except (json.JSONDecodeError, OSError):
                    is_corrupted = True
                    logging.warning(_("Detected corruption (invalid JSON): '{}'").format(src_path.name))
            if is_corrupted:
                if backup_path.exists():
                    try:
                        shutil.copy2(backup_path, src_path)
                        restored.append(src_path.name)
                        logging.info(f"Successfully restored '{src_path.name}' from backup.")
                    except Exception as e:
                        logging.error(_("Failed to restore '{}': {}").format(src_path.name, e))
                else:
                    logging.error(_("Cannot restore '{}', backup file is missing!").format(src_path.name))
        return restored

    def cleanup(self):
        """Removes any temporary backup files."""
        for backup_path in self._backup_paths.values():
            if backup_path.exists():
                try:
                    os.remove(backup_path)
                    logging.info(_("Cleaned up backup: '{}'").format(backup_path.name))
                except Exception as e:
                    logging.error(_("Failed to clean up backup '{}': {}").format(backup_path.name, e))
        self._backup_paths.clear()

def protected_operation(func):
    """Decorator to wrap any function with CondaGuard protection."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        guard = CondaGuard()
        with guard:
            return func(*args, **kwargs)
    return wrapper

def fix_conda_corruption():
    """
    A standalone function to manually trigger a check and restore.
    This is useful for scripts or manual repair. It creates its own backups
    and cleans them up.
    """
    safe_print(_('--- Running Standalone Conda Corruption Check & Fix ---'))
    guard = CondaGuard()
    guard.backup()
    restored_files = guard.restore()
    guard.cleanup()
    if restored_files:
        safe_print(_('🔧 Fix applied. Restored files: {}').format(', '.join(restored_files)))
        return True
    else:
        safe_print(_('✅ No corruption found in protected files.'))
        return False
if __name__ == '__main__':
    fix_conda_corruption()
    safe_print('\n' + '=' * 50 + '\n')

    @protected_operation
    def my_risky_hotswap_function(file_to_corrupt):
        safe_print(_('-> Running a risky function that might corrupt Conda...'))
        p = Path(file_to_corrupt)
        if p.exists():
            safe_print(_("   ...simulating corruption of '{}'").format(p.name))
            with open(p, 'w') as f:
                f.write('this is corrupted json')
        safe_print(_('-> Risky function finished.'))
    safe_print(_('🧪 Testing decorator...'))
    my_risky_hotswap_function('/opt/conda/envs/evocoder_env/conda-meta/nodejs-24.4.1-heeeca48_0.json')
    safe_print('\n' + '=' * 50 + '\n')
    safe_print(_('🧪 Testing context manager...'))
    with CondaGuard():
        safe_print('-> Entering safe context for a risky operation...')
        file_to_corrupt = '/opt/conda/envs/evocoder_env/conda-meta/nodejs-24.4.1-heeeca48_0.json'
        p = Path(file_to_corrupt)
        if p.exists():
            safe_print(_("   ...simulating deletion of '{}'").format(p.name))
            os.remove(p)
        safe_print(_('-> Leaving safe context...'))
    safe_print(_('\n✅ Demo finished.'))
