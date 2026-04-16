#!/usr/bin/env python3
import sqlite3
import json
import sys
from pathlib import Path
from packaging.utils import canonicalize_name

class KBSyncVerifier:
    def __init__(self, env_id: str, config_dir: str):
        self.env_id = env_id
        self.config_dir = Path(config_dir)
        self.total_bubbles_checked = 0
        self.total_failures = 0

    def get_disk_state(self, bubble_root: Path):
        """
        Scans a bubble folder and returns the absolute truth of what is installed.
        Returns: { 'canonical-name': 'version' }
        """
        actual_dists = {}
        for dist_info in bubble_root.glob('*.dist-info'):
            meta_file = dist_info / 'METADATA'
            if meta_file.exists():
                try:
                    content = meta_file.read_text(errors='ignore')
                    name, version = None, None
                    for line in content.splitlines():
                        if line.startswith('Name:'):
                            name = canonicalize_name(line.split(':', 1)[1].strip())
                        elif line.startswith('Version:'):
                            version = line.split(':', 1)[1].strip()
                        if name and version:
                            break
                    if name and version:
                        actual_dists[name] = version
                except Exception:
                    continue
        return actual_dists

    def verify_all_interpreters(self):
        print(f"🚀 Starting Full-Symmetry Verification for Env: {self.env_id}")
        print(f"Config Directory: {self.config_dir}\n" + "="*60)

        db_files = list(self.config_dir.glob(f'cache_{self.env_id}-py*.sqlite'))
        if not db_files:
            print(f"❌ No databases found for env {self.env_id}")
            return False

        for db_path in db_files:
            py_ver = db_path.name.split('-py')[1].split('.sqlite')[0]
            print(f"🔍 Checking Interpreter py{py_ver}...")
            
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Get all bubble keys
                cursor.execute("SELECT key FROM hash_store WHERE field = 'install_type' AND value = 'bubble'")
                bubble_keys = [row[0] for row in cursor.fetchall()]
                
                for key in bubble_keys:
                    self.total_bubbles_checked += 1
                    cursor.execute(
                        "SELECT field, value FROM hash_store WHERE key = ? AND field IN ('path', 'resolved_bubble_deps')", 
                        (key,)
                    )
                    kv = dict(cursor.fetchall())
                    
                    path_str = kv.get('path')
                    deps_json = kv.get('resolved_bubble_deps')
                    if not path_str or not deps_json:
                        continue
                        
                    try:
                        # 1. Setup expected state from DB
                        expected_deps = {canonicalize_name(k): v for k, v in json.loads(deps_json).items()}
                        bubble_root = Path(path_str).parent
                        
                        # 2. Setup actual state from Disk
                        actual_deps = self.get_disk_state(bubble_root)
                        
                        # --- FORWARD CHECK (DB -> Disk) ---
                        for pkg, ver in expected_deps.items():
                            if pkg not in actual_deps:
                                print(f"  ❌ MISSING: {key} | {pkg}=={ver} expected but not on disk")
                                self.total_failures += 1
                            elif actual_deps[pkg] != ver:
                                print(f"  ❌ MISMATCH: {key} | {pkg}: DB={ver}, Disk={actual_deps[pkg]}")
                                self.total_failures += 1
                        
                        # --- INVERSE CHECK (Disk -> DB) ---
                        for pkg, ver in actual_deps.items():
                            if pkg not in expected_deps:
                                print(f"  ⚠️ ORPHAN: {key} | {pkg}=={ver} found on disk but NOT in DB")
                                self.total_failures += 1
                            elif expected_deps[pkg] != ver:
                                # This is usually caught by the forward check, but kept for symmetry
                                print(f"  ❌ MISMATCH: {key} | {pkg}: Disk={ver}, DB={expected_deps[pkg]}")
                                self.total_failures += 1
                                
                    except Exception as e:
                        print(f"  ⚠️ Error processing bubble {key}: {e}")
                        self.total_failures += 1
                
                conn.close()
            except Exception as e:
                print(f"❌ Database Error {db_path}: {e}")

        print("\n" + "="*60)
        print(f"FINAL RESULT: {self.total_bubbles_checked} Bubbles Checked | {self.total_failures} Issues Found")
        return self.total_failures == 0

if __name__ == "__main__":
    # You can pass these as args if you want to integrate with a runner
    # For now, using your specific environment constants
    ENV_ID = '4db29431'
    CONFIG_DIR = '/home/minds3t/.config/omnipkg'
    
    verifier = KBSyncVerifier(ENV_ID, CONFIG_DIR)
    success = verifier.verify_all_interpreters()
    
    if not success:
        print("\n🔴 TEST FAILED: Disk and Database are out of sync.")
        sys.exit(1)
    else:
        print("\n🟢 TEST PASSED: Perfect symmetry between KB and Disk.")
        sys.exit(0)