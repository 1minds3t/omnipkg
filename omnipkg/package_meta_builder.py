#!/usr/bin/env python3
"""
omnipkg_metadata_builder.py - v11 - The "Multi-Version Complete" Edition
A fully integrated, self-aware metadata gatherer with complete multi-version
support for robust, side-by-side package management.
"""
import os
import re
import json
import subprocess
import redis
import hashlib
import importlib.metadata
import zlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from omnipkg.core import ConfigManager
from packaging.utils import canonicalize_name

# Configuration and imports
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

def get_python_version():
    """Get current Python version in X.Y format"""
    return f"{sys.version_info.major}.{sys.version_info.minor}"

def get_site_packages_path():
    """Dynamically find the site-packages path"""
    import site
    # Get the first user or system site-packages directory
    site_packages_dirs = site.getsitepackages()
    if hasattr(site, 'getusersitepackages'):
        site_packages_dirs.append(site.getusersitepackages())

    # Prefer virtual environment site-packages if available
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        # We're in a virtual environment
        venv_site_packages = Path(sys.prefix) / "lib" / f"python{get_python_version()}" / "site-packages"
        if venv_site_packages.exists():
            return str(venv_site_packages)

    # Fall back to first available site-packages
    for sp in site_packages_dirs:
        if Path(sp).exists():
            return sp

    # Last resort fallback
    return str(Path(sys.executable).parent.parent / "lib" / f"python{get_python_version()}" / "site-packages")

def get_bin_paths():
    """Get binary paths to index"""
    paths = []

    # Add current Python's bin directory
    python_bin_dir = str(Path(sys.executable).parent)
    paths.append(python_bin_dir)

    # Add virtual environment bin if available
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        venv_bin = str(Path(sys.prefix) / 'bin')
        if venv_bin not in paths and Path(venv_bin).exists():
            paths.append(venv_bin)

    return paths

# Load configuration using ConfigManager
config_manager = ConfigManager()
config = config_manager.config

class omnipkgMetadataGatherer:
    def __init__(self, force_refresh: bool = False):
        self.redis_client = None
        self.force_refresh = force_refresh
        self.security_report = {}
        config_manager = ConfigManager()
        self.config = config_manager.config # This creates the attribute that was missing
        if self.force_refresh:
            print("🟢 --force flag detected. Caching will be ignored.")
        if not HAS_TQDM:
            print("⚠️ Install 'tqdm' for a better progress bar.")

    def connect_redis(self) -> bool:
        try:
            self.redis_client = redis.Redis(
                host=self.config["redis_host"],
                port=self.config["redis_port"],
                decode_responses=True
            )
            self.redis_client.ping()
            print("✅ Connected to Redis successfully.")
            return True
        except Exception as e:
            print(f"❌ Could not connect to Redis: {e}")
            return False

    def discover_all_packages(self) -> List[Tuple[str, str]]:
        from packaging.utils import canonicalize_name

        packages = {}
        isolated_packages_versions = {}
        active_packages = {}

        # 1. Discover packages from the main site-packages (active environment)
        try:
            for dist in importlib.metadata.distributions():
                # --- FIX: Check for a valid name before processing ---
                package_name_from_meta = dist.metadata.get("Name")
                if not package_name_from_meta:
                    # Silently skip corrupted packages without a name
                    continue

                pkg_name = canonicalize_name(package_name_from_meta)
                version = dist.metadata['Version']
                if pkg_name not in packages:
                    packages[pkg_name] = version
                    active_packages[pkg_name] = version
        except Exception as e:
            print(f"⚠️ Error discovering packages from importlib.metadata: {e}")

        # 2. Scan .dist-info/egg-info directly in main site-packages as a fallback
        site_packages = Path(self.config["site_packages_path"])
        if site_packages.is_dir():
            for item in site_packages.iterdir():
                if item.is_dir() and (item.name.endswith('.dist-info') or item.name.endswith('.egg-info')):
                    name_part = item.name.split('-')[0]
                    pkg_name = canonicalize_name(name_part)
                    if pkg_name not in packages:
                        pass

        # 3. Discover packages from the .omnipkg_versions isolation area
        multiversion_base_path = Path(self.config["multiversion_base"])
        if multiversion_base_path.is_dir():
            for isolated_pkg_dir in multiversion_base_path.iterdir():
                if isolated_pkg_dir.is_dir() and '-' in isolated_pkg_dir.name:
                    for dist_info in isolated_pkg_dir.glob("*.dist-info"):
                        try:
                            from importlib.metadata import PathDistribution
                            dist = PathDistribution(dist_info)
                            # --- FIX: Check for a valid name before processing ---
                            package_name_from_meta = dist.metadata.get("Name")
                            if not package_name_from_meta:
                                continue

                            pkg_name = canonicalize_name(package_name_from_meta)
                            pkg_version = dist.metadata['Version']
                            if pkg_name not in isolated_packages_versions:
                                isolated_packages_versions[pkg_name] = set()
                            isolated_packages_versions[pkg_name].add(pkg_version)
                        except Exception:
                            continue

        self._store_active_versions(active_packages)

        all_unique_package_versions = {}
        for pkg_name, version in packages.items():
            if pkg_name not in all_unique_package_versions:
                all_unique_package_versions[pkg_name] = set()
            all_unique_package_versions[pkg_name].add(version)

        for pkg_name, versions_set in isolated_packages_versions.items():
            if pkg_name not in all_unique_package_versions:
                all_unique_package_versions[pkg_name] = set()
            all_unique_package_versions[pkg_name].update(versions_set)

        result_list = []
        for pkg_name, versions_set in all_unique_package_versions.items():
            for version_str in versions_set:
                result_list.append((pkg_name, version_str))

        print(f"🔍 Discovered {len(all_unique_package_versions)} unique packages with {len(result_list)} total versions.")
        return sorted(result_list, key=lambda x: x[0])

    def _store_active_versions(self, active_packages: Dict[str, str]):
        """Store the active versions in Redis to fix the active version detection issue"""
        if not self.redis_client:
            return

        for pkg_name, version in active_packages.items():
            main_key = f"{self.config['redis_key_prefix']}{pkg_name}"
            try:
                self.redis_client.hset(main_key, "active_version", version)
            except Exception as e:
                print(f"⚠️ Failed to store active version for {pkg_name}: {e}")

    def run(self, targeted_packages: List[str] = None):
        print("🚀 Starting omnipkg Metadata Builder v11 (Multi-Version Complete Edition)...")
        if not self.connect_redis():
            return

        packages_to_process = []
        if targeted_packages:
            print(f"🎯 Running in targeted mode for {len(targeted_packages)} package(s).")
            for pkg_spec in targeted_packages:
                parts = pkg_spec.split('==')
                if len(parts) == 2:
                    packages_to_process.append((canonicalize_name(parts[0]), parts[1]))
        else:
            print("🔍 No specific targets provided. Discovering all installed packages...")
            packages_to_process = self.discover_all_packages()

        print("🛡️ Performing bulk security scan for active packages...")
        active_packages = {}
        try:
            for dist in importlib.metadata.distributions():
                # --- FIX: Check for a valid name before processing ---
                package_name_from_meta = dist.metadata.get("Name")
                if not package_name_from_meta:
                    continue # Skip this corrupted package
                active_packages[canonicalize_name(package_name_from_meta)] = dist.metadata['Version']
        except Exception as e:
            print(f"⚠️ Error preparing packages for security scan: {e}")

        self._run_bulk_security_check(active_packages)
        print(f"✅ Bulk security scan complete. Found {len(self.security_report)} potential issues.")

        package_iterator = tqdm(packages_to_process, desc="Processing packages", unit="pkg") if HAS_TQDM else packages_to_process
        processed_count = 0

        for package_name, version_str in package_iterator:
            if HAS_TQDM:
                package_iterator.set_postfix_str(f"Current: {package_name} v{version_str}", refresh=True)
            try:
                version_key = f"{self.config['redis_key_prefix']}{package_name}:{version_str}"
                previous_data = self.redis_client.hgetall(version_key)

                metadata = self._build_comprehensive_metadata(package_name, previous_data, version_str)

                current_checksum = self._generate_checksum(metadata)
                if not self.force_refresh and previous_data and previous_data.get('checksum') == current_checksum:
                    continue

                metadata['checksum'] = current_checksum
                self._store_in_redis(package_name, version_str, metadata)
                processed_count += 1

            except Exception as e:
                if HAS_TQDM:
                    package_iterator.write(f"    ❌ ERROR processing {package_name} v{version_str}: {e}")

        print(f"\n🎉 Metadata building complete! Updated {processed_count} package(s).")
    
    # ... (the rest of the file remains unchanged) ...
    def _build_comprehensive_metadata(self, package_name: str, previous_data: Dict, specific_version: str) -> Dict:
            metadata = {
                'name': package_name,
                'Version': specific_version,
                'last_indexed': datetime.now().isoformat()
            }
            
            # --- LOGIC FIX START ---
            # Initialize package_files EARLIER to ensure it always exists.
            package_files = {'binaries': []}
            found_specific_version_dist = False
            
            version_path = Path(self.config["multiversion_base"]) / f"{package_name}-{specific_version}"
            
            # Determine the search path: either the bubble or the main site-packages
            search_path = version_path if version_path.is_dir() else Path(self.config["site_packages_path"])
            
            # Use a helper to robustly find the distribution and its files
            dist = self._find_distribution_at_path(package_name, specific_version, search_path)

            if dist:
                found_specific_version_dist = True
                for k, v in dist.metadata.items():
                    metadata[k] = v
                metadata['dependencies'] = [str(req) for req in dist.requires] if dist.requires else []
                
                # Populate package_files now that we have a valid 'dist' object
                if dist.files:
                    package_files['binaries'] = [
                        str(search_path / file_path)
                        for file_path in dist.files
                        if "bin/" in str(file_path) and (search_path / file_path).exists()
                    ]

            if not found_specific_version_dist:
                # Fallback if we couldn't find a dist-info (less reliable)
                metadata.update(self._enrich_from_site_packages(package_name, specific_version))

            # --- LOGIC FIX END ---

            # Now, the rest of the function can safely use 'package_files'
            if self.force_refresh or 'help_text' not in previous_data:
                if package_files.get('binaries'):
                    metadata.update(self._get_help_output(package_files['binaries'][0]))
                else:
                    metadata['help_text'] = "No executable binary found."
            else:
                metadata['help_text'] = previous_data.get('help_text', "No help text available.")

            metadata['cli_analysis'] = self._analyze_cli(metadata.get('help_text', ''))
            metadata['security'] = self._get_security_info(package_name)
            
            # For health checks, we still check against the main installed environment
            # as it's the only one we can reliably execute code in.
            health_check_dist = self._get_distribution(package_name)
            health_check_files = self._find_package_files(health_check_dist, package_name)
            metadata['health'] = self._perform_health_checks(package_name, health_check_files)

            return metadata
    
    def _find_distribution_at_path(self, package_name: str, version: str, search_path: Path) -> Optional[importlib.metadata.Distribution]:
        """Helper to find a specific package version's distribution in a given path."""
        try:
            # importlib.metadata.distributions() can be slow, so we glob first
            # We need to handle variations in naming, e.g., "Flask_Login" vs "flask-login"
            normalized_name = package_name.replace("-", "_")
            for dist_info in search_path.glob(f"{normalized_name}-{version}*.dist-info"):
                if dist_info.is_dir():
                    from importlib.metadata import PathDistribution
                    dist = PathDistribution(dist_info)
                    # Verify the name and version from the metadata itself
                    if dist.metadata["Name"].lower() == package_name.lower() and dist.metadata["Version"] == version:
                        return dist
        except Exception:
            return None
        return None

    def _parse_metadata_file(self, metadata_content: str) -> Dict:
        metadata = {}
        current_key = None
        current_value = []
        for line in metadata_content.splitlines():
            if ': ' in line and not line.startswith(' '):
                if current_key:
                    metadata[current_key] = '\n'.join(current_value).strip() if current_value else ''
                current_key, value = line.split(': ', 1)
                current_value = [value]
            elif line.startswith(' ') and current_key:
                current_value.append(line.strip())
        if current_key:
            metadata[current_key] = '\n'.join(current_value).strip() if current_value else ''
        return metadata

    def _store_in_redis(self, package_name: str, version_str: str, metadata: Dict):
        """
        Store package metadata in Redis with proper indexing for discovery.
        
        This creates a hierarchical structure:
        - omnipkg:pkg:flask-login:0.4.1 -> detailed metadata for specific version
        - omnipkg:pkg:flask-login:installed_versions -> set of all versions  
        - omnipkg:pkg:versions -> master index for `omnipkg list` command
        - omnipkg:pkg:index -> set of all package names
        """
        pkg_name_lower = package_name.lower()
        
        # KEY 1: The detailed key for this specific version (e.g., omnipkg:pkg:flask-login:0.4.1)
        version_key = f"{self.config['redis_key_prefix']}{pkg_name_lower}:{version_str}"
        
        # KEY 2: The main key for the package (e.g., omnipkg:pkg:flask-login)
        main_key = f"{self.config['redis_key_prefix']}{pkg_name_lower}"

        # KEY 3: The master index of all packages for the `list` command
        master_versions_key = f"{self.config['redis_key_prefix']}versions"

        # --- Prepare the data (compression logic) ---
        data_to_store = metadata.copy()
        for field in ['help_text', 'readme_snippet', 'license_text', 'Description']:
            if field in data_to_store and isinstance(data_to_store[field], str) and len(data_to_store[field]) > 500:
                compressed = zlib.compress(data_to_store[field].encode('utf-8'))
                data_to_store[field] = compressed.hex()
                data_to_store[f"{field}_compressed"] = 'true'

        flattened_data = self._flatten_dict(data_to_store)
        
        # --- Execute all writes in a single, efficient transaction ---
        with self.redis_client.pipeline() as pipe:
            # 1. Write the detailed metadata for the specific version
            pipe.delete(version_key)
            pipe.hset(version_key, mapping=flattened_data)

            # 2. Update the package's set of known installed versions
            pipe.sadd(f"{main_key}:installed_versions", version_str)
            
            # 3. CRITICAL FIX: Update the master index for `omnipkg list`
            # ALWAYS add to master index so ALL packages show up in `omnipkg list`
            pipe.hset(master_versions_key, pkg_name_lower, version_str)
            pipe.hset(main_key, "name", package_name)  # Store original case name
            
            # 4. Track which version is ACTIVE vs in bubble
            version_path = Path(self.config["multiversion_base"]) / f"{package_name}-{version_str}"
            if not version_path.is_dir():
                # This version is active in main environment
                pipe.hset(main_key, "active_version", version_str)
            else:
                # This version is in a bubble - mark it as such
                pipe.hset(main_key, f"bubble_version:{version_str}", "true")

            # 4. Update the global index of all package names
            pipe.sadd(f"{self.config['redis_key_prefix']}index", pkg_name_lower)
            
            pipe.execute()

    def _perform_health_checks(self, package_name: str, package_files: Dict) -> Dict:
        health_data = {
            'import_check': self._verify_installation(package_name),
            'binary_checks': {
                Path(bin_path).name: self._check_binary_integrity(bin_path)
                for bin_path in package_files.get('binaries', [])
            }
        }
        oversized = [name for name, check in health_data['binary_checks'].items() if check.get('size', 0) > 10_000_000]
        if oversized:
            health_data['size_warnings'] = oversized
        return health_data

    def _verify_installation(self, package_name: str) -> Dict:
        script = f"import importlib.metadata; print(importlib.metadata.version('{package_name.replace('-', '_')}'))"
        try:
            result = subprocess.run(
                [self.config["python_executable"], "-c", script], 
                capture_output=True, text=True, check=True, timeout=5
            )
            return {'importable': True, 'version': result.stdout.strip()}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return {
                'importable': False, 
                'error': e.stderr.strip() if hasattr(e, 'stderr') else str(e)
            }

    def _check_binary_integrity(self, bin_path: str) -> Dict:
        if not os.path.exists(bin_path):
            return {'exists': False}
        integrity_report = {
            'exists': True,
            'size': os.path.getsize(bin_path),
            'is_elf': False,
            'valid_shebang': self._has_valid_shebang(bin_path)
        }
        try:
            with open(bin_path, 'rb') as f:
                if f.read(4) == b'\x7fELF':
                    integrity_report['is_elf'] = True
        except Exception:
            pass
        return integrity_report

    def _has_valid_shebang(self, path: str) -> bool:
        try:
            with open(path, 'r', errors='ignore') as f:
                return f.readline().startswith('#!')
        except Exception:
            return False

    def _find_package_files(self, dist, package_name: str) -> Dict:
        files = {'binaries': []}
        if dist and dist.files:
            for file_path in dist.files:
                full_path = Path(self.config["site_packages_path"]).parent / file_path
                if "bin/" in str(file_path) and full_path.exists():
                    files['binaries'].append(str(full_path))
        if not files['binaries']:
            for bin_dir in self.config["paths_to_index"]:
                potential_binary = Path(bin_dir) / package_name.lower()
                if potential_binary.exists() and os.access(potential_binary, os.X_OK):
                    files['binaries'].append(str(potential_binary))
                    break
        return files

    def _run_bulk_security_check(self, packages: Dict[str, str]):
        reqs_file_path = '/tmp/bulk_safety_reqs.txt'
        try:
            with open(reqs_file_path, 'w') as f:
                for name, version in packages.items():
                    f.write(f"{name}=={version}\n")
            result = subprocess.run([
                self.config["python_executable"], "-m", "safety", "check",
                "-r", reqs_file_path, "--json"
            ], capture_output=True, text=True, timeout=120)
            if result.stdout:
                self.security_report = json.loads(result.stdout)
        except Exception as e:
            print(f"    ⚠️ Bulk security scan failed: {e}")
        finally:
            if os.path.exists(reqs_file_path):
                os.remove(reqs_file_path)

    def _get_security_info(self, package_name: str) -> Dict:
        vulnerabilities = self.security_report.get(package_name.lower(), [])
        return {
            'audit_status': 'checked_in_bulk',
            'issues_found': len(vulnerabilities),
            'report': vulnerabilities
        }

    def _generate_checksum(self, metadata: Dict) -> str:
        core_data = {
            'Version': metadata.get('Version'),
            'dependencies': metadata.get('dependencies'),
            'help_text': metadata.get('help_text')
        }
        data_string = json.dumps(core_data, sort_keys=True)
        return hashlib.sha256(data_string.encode('utf-8')).hexdigest()

    def _get_help_output(self, executable_path: str) -> Dict:
        if not os.path.exists(executable_path):
            return {"help_text": "Executable not found."}
        for flag in ["--help", "-h"]:
            try:
                result = subprocess.run(
                    [executable_path, flag],
                    capture_output=True, text=True, timeout=3, errors='ignore'
                )
                output = (result.stdout or result.stderr).strip()
                if output and "usage:" in output.lower():
                    return {"help_text": output[:5000]}
            except Exception:
                continue
        return {"help_text": "No valid help output captured."}

    def _analyze_cli(self, help_text: str) -> Dict:
        if not help_text or "No valid help" in help_text:
            return {}
        analysis = {"common_flags": [], "subcommands": []}
        lines = help_text.split('\n')
        command_regex = re.compile(r'^\s*([a-zA-Z0-9_-]+)\s{2,}(.*)')
        in_command_section = False
        for line in lines:
            if re.search(r'^(commands|available commands):', line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and not line.strip():
                in_command_section = False
                continue
            if in_command_section:
                match = command_regex.match(line)
                if match:
                    command_name = match.group(1).strip()
                    if not command_name.startswith('-'):
                        analysis["subcommands"].append({
                            "name": command_name,
                            "description": match.group(2).strip()
                        })
        if not analysis["subcommands"]:
            analysis["subcommands"] = [
                {"name": cmd, "description": "N/A"}
                for cmd in self._fallback_analyze_cli(lines)
            ]
        analysis["common_flags"] = list(set(re.findall(r'--[a-zA-Z0-9][a-zA-Z0-9-]+', help_text)))
        return analysis

    def _fallback_analyze_cli(self, lines: list) -> list:
        subcommands = []
        in_command_section = False
        for line in lines:
            if re.search(r'commands:', line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and line.strip():
                match = re.match(r'^\s*([a-zA-Z0-9_-]+)', line)
                if match:
                    subcommands.append(match.group(1))
            elif in_command_section and not line.strip():
                in_command_section = False
        return list(set(subcommands))

    def _get_distribution(self, package_name: str):
        try:
            return importlib.metadata.distribution(package_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def _enrich_from_site_packages(self, name: str, version: str = None) -> Dict:
        enriched_data = {}
        guesses = set([name, name.lower().replace('-', '_')])
        base_path = Path(self.config["site_packages_path"])
        
        if version:
            base_path = Path(self.config["multiversion_base"]) / f"{name}-{version}"
        
        for g in guesses:
            pkg_path = base_path / g
            if pkg_path.is_dir():
                readme_path = next((p for p in pkg_path.glob('[Rr][Ee][Aa][Dd][Mm][Ee].*') if p.is_file()), None)
                if readme_path:
                    enriched_data['readme_snippet'] = readme_path.read_text(encoding='utf-8', errors='ignore')[:500]
                license_path = next((p for p in pkg_path.glob('[Ll][Ii][Cc][Ee][Nn][Ss]*') if p.is_file()), None)
                if license_path:
                    enriched_data['license_text'] = license_path.read_text(encoding='utf-8', errors='ignore')[:500]
                return enriched_data
        return {}

    def _flatten_dict(self, d: Dict, parent_key: str = '', sep: str = '.') -> Dict:
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, str(v)))
        return dict(items)

if __name__ == "__main__":
    force = '--force' in sys.argv or '-f' in sys.argv
    
    # New: Check for targeted packages passed as arguments
    targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith('--')]

    gatherer = omnipkgMetadataGatherer(force_refresh=force)
    
    # Pass the targeted packages to the run method
    gatherer.run(targeted_packages=targeted_packages)