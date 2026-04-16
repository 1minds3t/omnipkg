#!/usr/bin/env python3
"""
Absolute Ground Truth Bubble Dependencies Verifier

This is the source of truth for verifying that resolved_bubble_deps in omnipkg
match what's actually installed on disk (by reading METADATA files).
"""

import sqlite3
import json
from pathlib import Path
from packaging.utils import canonicalize_name
import argparse
import sys
from typing import Dict, Tuple

def parse_metadata_file(meta_path: Path) -> tuple[str | None, str | None]:
    """Robustly extract Name and Version from METADATA or PKG-INFO.
    Takes the FIRST occurrence only (the real ones are always at the top)."""
    if not meta_path.exists():
        return None, None
    
    try:
        content = meta_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return None, None

    cur_name = None
    cur_ver = None

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('Name:') and cur_name is None:   # only take first
            cur_name = canonicalize_name(line.split(':', 1)[1].strip())
        elif line.startswith('Version:') and cur_ver is None:  # only take first
            cur_ver = line.split(':', 1)[1].strip()
        
        # Early exit once we have both (they are always near the top)
        if cur_name is not None and cur_ver is not None:
            break

    return cur_name, cur_ver


def verify_env(env_id: str, config_dir: Path):
    print(f'🚀 ABSOLUTE GROUND TRUTH VERIFICATION for Env: {env_id}')
    print(f'Scanning databases in: {config_dir}\n{"="*60}')

    total_checked = 0
    total_failures = 0
    failures_by_py = {}

    db_files = list(config_dir.glob(f'cache_{env_id}-py*.sqlite'))

    if not db_files:
        print(f"❌ No database files found for env {env_id}")
        return 1

    for db_path in sorted(db_files):
        py_ver = db_path.name.split('-py')[1].split('.sqlite')[0]
        print(f'🔍 Checking py{py_ver}...')

        failures_in_this_py = 0

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT key FROM hash_store WHERE field = 'install_type' AND value = 'bubble'"
            )
            bubble_keys = [row[0] for row in cursor.fetchall()]

            for key in bubble_keys:
                total_checked += 1

                cursor.execute(
                    "SELECT field, value FROM hash_store WHERE key = ? AND field IN ('path', 'resolved_bubble_deps')",
                    (key,)
                )
                kv_pairs = dict(cursor.fetchall())

                path_str = kv_pairs.get('path')
                deps_json = kv_pairs.get('resolved_bubble_deps')
                if not path_str or not deps_json:
                    continue

                try:
                    expected_deps: Dict[str, str] = {
                        canonicalize_name(k): v 
                        for k, v in json.loads(deps_json).items()
                    }

                    path_obj = Path(path_str)
                    
                    # Smart bubble_root detection based on actual omnipkg layout
                    if path_obj.suffix == '.dist-info' or path_obj.name.endswith('.dist-info'):
                        # Case 1: path points to .dist-info → go up one level to the versioned bubble dir
                        bubble_root = path_obj.parent
                    elif '.omnipkg_versions' in str(path_obj):
                        # Case 2: path is already inside a bubble or .omnipkg_versions
                        if path_obj.name.startswith(tuple(p + '-' for p in expected_deps.keys())) or any(
                            d in path_obj.name for d in ['dist-info', 'egg-info']
                        ):
                            bubble_root = path_obj.parent if path_obj.is_file() else path_obj
                        else:
                            bubble_root = path_obj
                    else:
                        bubble_root = path_obj.parent
                    
                    # Final safety: if we landed on .omnipkg_versions, go one level deeper if possible
                    if bubble_root.name == '.omnipkg_versions':
                        # Try to find the specific bubble dir by looking for the main package name
                        main_pkg = list(expected_deps.keys())[0]  # usually the first one is the main package
                        candidates = list(bubble_root.glob(f"{main_pkg}-*"))
                        if candidates:
                            bubble_root = candidates[0]
                    
                    print(f"   [DEBUG] For {key}: using bubble_root = {bubble_root}")

                    # Scan for metadata in the bubble_root
                    actual_dists: Dict[str, str] = {}
                    for pattern in ['*.dist-info', '*.egg-info']:
                        for info_dir in bubble_root.glob(pattern):
                            is_dist_info = info_dir.suffix == '.dist-info'
                            meta_file = info_dir / ('METADATA' if is_dist_info else 'PKG-INFO')
                            name, ver = parse_metadata_file(meta_file)
                            if name and ver:
                                actual_dists[name] = ver

                    # Also check one level deeper in case of nested layout
                    for info_dir in bubble_root.glob('**/*.dist-info'):
                        name, ver = parse_metadata_file(info_dir / 'METADATA')
                        if name and ver:
                            actual_dists[name] = ver

                    for pkg_name, exp_ver in expected_deps.items():
                        actual_ver = actual_dists.get(pkg_name)
                        if actual_ver is None:
                            print(f'❌ MISSING: {key} | {pkg_name}=={exp_ver} not found on disk!')
                            print(f'   → Scanned bubble_root: {bubble_root}')
                            total_failures += 1
                            failures_in_this_py += 1
                        elif actual_ver != exp_ver:
                            print(f'❌ MISMATCH: {key} | {pkg_name}: DB={exp_ver}, Disk={actual_ver}')
                            total_failures += 1
                            failures_in_this_py += 1

                except Exception as e:
                    print(f'⚠️ Error processing bubble {key}: {type(e).__name__}: {e}')
                    total_failures += 1
                    failures_in_this_py += 1

                except Exception as e:
                    print(f'⚠️ Error processing bubble {key}: {type(e).__name__}: {e}')
                    total_failures += 1
                    failures_in_this_py += 1

            conn.close()

        except Exception as e:
            print(f'❌ DB Error for py{py_ver}: {e}')
            failures_in_this_py += 1  # count the whole python version as problematic

        failures_by_py[py_ver] = failures_in_this_py

    print('\n' + '='*60)
    print(f'FINAL RESULT: {total_checked} Bubbles Checked | {total_failures} Failures')

    if total_failures == 0:
        print('🎉 ABSOLUTE SUCCESS: Your resolved_bubble_deps are 100% accurate.')
        return 0
    else:
        print('⚠️ Some real mismatches still exist.')
        for pyv, cnt in failures_by_py.items():
            if cnt > 0:
                print(f'   py{pyv}: {cnt} failures')
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-id', default='4db29431', help='Environment ID to verify')
    parser.add_argument('--config-dir', type=Path, 
                       default=Path('/home/minds3t/.config/omnipkg'),
                       help='Path to omnipkg config directory')
    
    args = parser.parse_args()
    
    sys.exit(verify_env(args.env_id, args.config_dir))