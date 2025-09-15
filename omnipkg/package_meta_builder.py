"""
omnipkg_metadata_builder.py - v11 - The "Multi-Version Complete" Edition
A fully integrated, self-aware metadata gatherer with complete multi-version
support for robust, side-by-side package management.
"""
import os
import re
import json
import subprocess
import hashlib
import importlib.metadata
import zlib
import sys
import tempfile
import concurrent.futures
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from packaging.utils import canonicalize_name
from omnipkg.loader import omnipkgLoader
from omnipkg.i18n import _
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
        
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

  

def get_python_version():
    """Get current Python version in X.Y format"""
    return f'{sys.version_info.major}.{sys.version_info.minor}'

def get_site_packages_path():
    """Dynamically find the site-packages path"""
    import site
    site_packages_dirs = site.getsitepackages()
    if hasattr(site, 'getusersitepackages'):
        site_packages_dirs.append(site.getusersitepackages())
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        venv_site_packages = Path(sys.prefix) / 'lib' / f'python{get_python_version()}' / 'site-packages'
        if venv_site_packages.exists():
            return str(venv_site_packages)
    for sp in site_packages_dirs:
        if Path(sp).exists():
            return sp
    return str(Path(sys.executable).parent.parent / 'lib' / f'python{get_python_version()}' / 'site-packages')

def get_bin_paths():
    """Get binary paths to index"""
    paths = [str(Path(sys.executable).parent)]
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        venv_bin = str(Path(sys.prefix) / 'bin')
        if venv_bin not in paths and Path(venv_bin).exists():
            paths.append(venv_bin)
    return paths

class omnipkgMetadataGatherer:
    def __init__(self, config: Dict, env_id: str, force_refresh: bool = False, omnipkg_instance=None, target_context_version: Optional[str] = None):
        self.cache_client = None
        self.omnipkg_instance = omnipkg_instance
        self.cache_client = self.omnipkg_instance.cache_client if self.omnipkg_instance else None
        self.force_refresh = force_refresh
        self.security_report = {}
        self.target_context_version = target_context_version
        self.config = config

        # --- CORE FIX: Prioritize the override ENV VAR to get the correct ID ---
        # This ensures this class instance uses the env_id passed from the parent.
        self.env_id = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE', env_id)
        # --- END CORE FIX ---

        self.package_path_registry = {}
        if self.force_refresh:
            print(_('🟢 --force flag detected. Caching will be ignored.'))
        if not HAS_TQDM:
            print(_("⚠️ Install 'tqdm' for a better progress bar."))

    @property
    def redis_key_prefix(self) -> str:
        """
        FIXED: Dynamically generates a unique redis key prefix based on the
        ACTIVE Python version from the CONFIGURATION, not the running script's version.
        This is critical for correct multi-python support.
        """
        python_exe_path = self.config.get('python_executable', sys.executable)
        py_ver_str = 'py_unknown'
        match = re.search('(\\d+\\.\\d+)', python_exe_path)
        if match:
            py_ver_str = f'py{match.group(1)}'
        else:
            try:
                result = subprocess.run([python_exe_path, '-c', "import sys; print(f'py{sys.version_info.major}.{sys.version_info.minor}')"], capture_output=True, text=True, check=True, timeout=2)
                py_ver_str = result.stdout.strip()
            except Exception:
                py_ver_str = f'py{sys.version_info.major}.{sys.version_info.minor}'
        base_prefix = self.config.get('redis_key_prefix', 'omnipkg:pkg:')
        base = base_prefix.split(':')[0]
        suffix = base_prefix.split(':', 1)[1] if ':' in base_prefix else 'pkg:'
        return f'{base}:env_{self.env_id}:{py_ver_str}:{suffix}'

    def _get_package_name_variants(self, name: str) -> List[str]:
        """
        Generates comprehensive package name variants to handle ALL Python packaging
        naming conventions including dots, hyphens, underscores.
        """
        # Using a set automatically handles duplicates
        variants = {
            name,
            canonicalize_name(name),
            name.replace('-', '_'),
            name.replace('_', '-'),
            name.replace('-', '.'),  # Handle ruamel-yaml -> ruamel.yaml
            name.replace('.', '-'),  # Handle ruamel.yaml -> ruamel-yaml
            name.replace('_', '.'),  # Handle ruamel_yaml -> ruamel.yaml
            name.replace('.', '_'),  # Handle ruamel.yaml -> ruamel_yaml
            name.lower(),
            name.upper(),
        }
        
        # Add variants with common prefixes/suffixes removed
        clean_name = name.lower()
        if clean_name.startswith('python-'):
            base = clean_name[7:]
            variants.update({
                base, base.replace('-', '_'), base.replace('-', '.'), 
                base.replace('_', '.'), base.replace('_', '-')
            })
        if clean_name.startswith('py-'):
            base = clean_name[3:]
            variants.update({
                base, base.replace('-', '_'), base.replace('-', '.'),
                base.replace('_', '.'), base.replace('_', '-')
            })
        if clean_name.endswith('-python'):
            base = clean_name[:-7]
            variants.update({
                base, base.replace('-', '_'), base.replace('-', '.'),
                base.replace('_', '.'), base.replace('_', '-')
            })
        
        return list(variants)

    def _discover_distributions(self, targeted_packages: Optional[List[str]]) -> List[importlib.metadata.Distribution]:
        """
        FIXED DISCOVERY V7: Now properly handles nested directory structures 
        where dist-info is inside package version directories.
        """
        if targeted_packages:
            print(f'🎯 Running in targeted mode for {len(targeted_packages)} package(s).')
            discovered_dists = []
            site_packages = Path(self.config.get('site_packages_path', '/dev/null'))
            multiversion_base = Path(self.config.get('multiversion_base', '/dev/null'))
            
            for spec in targeted_packages:
                try:
                    if '==' in spec:
                        name, version = spec.split('==', 1)
                    else:
                        name = spec
                        version = None
                    
                    found_dist = None
                    name_variants = self._get_package_name_variants(name)
                    
                    print(f"   🔍 Searching for '{spec}' with variants: {name_variants}")
                    
                    # Search all relevant paths
                    for base_path in [p for p in [site_packages, multiversion_base] if p and p.is_dir()]:
                        print(f"      -> Searching in: {base_path}")
                        
                        # Strategy 1: Direct dist-info pattern matching (flat structure)
                        for variant in name_variants:
                            if version:
                                # Looking for specific version - be more flexible with patterns
                                patterns = [
                                    f'{variant}-{version}.dist-info',
                                    f'{variant}-{version}-*.dist-info',  # Handle build tags
                                    f'{variant.replace(".", "_")}-{version}.dist-info',  # Canonical form
                                    f'{variant.replace(".", "_")}-{version}-*.dist-info',
                                ]
                            else:
                                # Looking for any version
                                patterns = [
                                    f'{variant}-*.dist-info',
                                    f'{variant.replace(".", "_")}-*.dist-info',
                                ]
                            
                            for pattern in patterns:
                                matching_paths = list(base_path.glob(pattern))
                                print(f"         Pattern '{pattern}' found: {len(matching_paths)} matches")
                                
                                for dist_info_path in matching_paths:
                                    if not dist_info_path.is_dir():
                                        continue
                                        
                                    try:
                                        dist = importlib.metadata.Distribution.at(dist_info_path)
                                        dist_name = dist.metadata.get('Name', '')
                                        
                                        # More flexible name matching
                                        if (canonicalize_name(dist_name) == canonicalize_name(name) or
                                            dist_name.lower() == name.lower() or
                                            dist_name.replace('.', '-').lower() == name.replace('.', '-').lower() or
                                            dist_name.replace('-', '.').lower() == name.replace('-', '.').lower()):
                                            
                                            if version is None or dist.version == version:
                                                found_dist = dist
                                                print(f'         ✅ Found {dist_name} v{dist.version} at {dist_info_path}')
                                                break
                                            else:
                                                print(f'         ⚠️  Found {dist_name} v{dist.version} but need v{version}')
                                        else:
                                            print(f'         ❌ Name mismatch: found "{dist_name}", looking for "{name}"')
                                            
                                    except Exception as e:
                                        print(f'         ❌ Error reading {dist_info_path}: {e}')
                                        continue
                                
                                if found_dist:
                                    break
                            
                            if found_dist:
                                break
                        
                        # Strategy 2: NESTED SEARCH - look inside subdirectories for dist-info
                        # This handles the case where structure is: multiversion_base/package-version/package-version.dist-info
                        if not found_dist:
                            print(f"      -> Searching nested directories for dist-info...")
                            
                            # Look for directories that might contain our package
                            for variant in name_variants:
                                if version:
                                    # Look for directories like: uv-0.8.16/ inside multiversion_base
                                    nested_dir_patterns = [
                                        f'{variant}-{version}',
                                        f'{variant.replace(".", "_")}-{version}',
                                    ]
                                else:
                                    # Look for any version directories
                                    nested_dir_patterns = [
                                        f'{variant}-*',
                                        f'{variant.replace(".", "_")}-*',
                                    ]
                                
                                for pattern in nested_dir_patterns:
                                    matching_dirs = list(base_path.glob(pattern))
                                    print(f"         Nested dir pattern '{pattern}' found: {len(matching_dirs)} directories")
                                    
                                    for nested_dir in matching_dirs:
                                        if not nested_dir.is_dir():
                                            continue
                                        
                                        # Now look for dist-info inside this directory
                                        dist_info_pattern = '*.dist-info'
                                        dist_infos = list(nested_dir.glob(dist_info_pattern))
                                        
                                        for dist_info_path in dist_infos:
                                            if not dist_info_path.is_dir():
                                                continue
                                            
                                            try:
                                                dist = importlib.metadata.Distribution.at(dist_info_path)
                                                dist_name = dist.metadata.get('Name', '')
                                                
                                                # Check if this matches what we're looking for
                                                name_matches = any(
                                                    canonicalize_name(dist_name) == canonicalize_name(v) 
                                                    for v in name_variants
                                                )
                                                
                                                if name_matches:
                                                    if version is None or dist.version == version:
                                                        found_dist = dist
                                                        print(f'         ✅ Found nested {dist_name} v{dist.version} at {dist_info_path}')
                                                        break
                                                    else:
                                                        print(f'         ⚠️  Found nested {dist_name} v{dist.version} but need v{version}')
                                                        
                                            except Exception as e:
                                                print(f'         ❌ Error reading nested {dist_info_path}: {e}')
                                                continue
                                        
                                        if found_dist:
                                            break
                                    
                                    if found_dist:
                                        break
                                
                                if found_dist:
                                    break
                        
                        # Strategy 3: Fallback - scan ALL dist-info directories in path AND subdirectories
                        if not found_dist:
                            print(f"      -> Fallback: scanning all .dist-info directories...")
                            
                            # First try direct children
                            all_dist_infos = list(base_path.glob('*.dist-info'))
                            
                            # Then try nested (one level deep)
                            all_dist_infos.extend(list(base_path.glob('*/*.dist-info')))
                            
                            print(f"         Found {len(all_dist_infos)} dist-info directories to check")
                            
                            for dist_info_path in all_dist_infos:
                                if not dist_info_path.is_dir():
                                    continue
                                
                                try:
                                    dist = importlib.metadata.Distribution.at(dist_info_path)
                                    dist_name = dist.metadata.get('Name', '')
                                    
                                    # Check if this matches what we're looking for
                                    name_matches = any(
                                        canonicalize_name(dist_name) == canonicalize_name(variant)
                                        for variant in name_variants
                                    )
                                    
                                    if name_matches:
                                        if version is None or dist.version == version:
                                            found_dist = dist
                                            print(f'         ✅ Fallback found {dist_name} v{dist.version}')
                                            break
                                        else:
                                            print(f'         ⚠️  Fallback found {dist_name} v{dist.version} but need v{version}')
                                            
                                except Exception as e:
                                    continue
                        
                        if found_dist:
                            break
                    
                    if found_dist:
                        discovered_dists.append(found_dist)
                        if version:
                            print(f'   -> Found distribution for {name}=={version}')
                        else:
                            print(f'   -> Found distribution for {name} v{found_dist.version}')
                    else:
                        if version:
                            print(f"   ⚠️  Could not find any distribution matching '{name}=={version}'.")
                            print(f"      💡 Available variants tried: {name_variants}")
                        else:
                            print(f"   ⚠️  Could not find any distribution matching '{name}'.")
                            
                except ValueError as e:
                    print(f"   ⚠️  Could not parse spec '{spec}': {e}")
                    
            return discovered_dists
        
        # Full scan logic - also needs to handle nested structure
        print('🔍 Discovering all packages from file system (ground truth)...')
        search_paths = []
        site_packages_path = self.config.get('site_packages_path')
        if site_packages_path and Path(site_packages_path).is_dir():
            search_paths.append(site_packages_path)
        
        multiversion_base_path = self.config.get('multiversion_base')
        if multiversion_base_path and Path(multiversion_base_path).is_dir():
            # For full scan, we need to handle nested structure too
            base_path = Path(multiversion_base_path)
            
            # Add direct dist-info directories
            search_paths.append(str(base_path))
            
            # Add nested directories that contain dist-info
            for subdir in base_path.iterdir():
                if subdir.is_dir():
                    # Check if this subdirectory contains any dist-info directories
                    if list(subdir.glob('*.dist-info')):
                        search_paths.append(str(subdir))
        
        dists = list(importlib.metadata.distributions(path=search_paths))
        print(_('✅ Discovery complete. Found {} total package versions to process.').format(len(dists)))
        return dists

    def _is_bubbled(self, dist: importlib.metadata.Distribution) -> bool:
        multiversion_base = self.config.get('multiversion_base', '/dev/null')
        return str(dist._path).startswith(multiversion_base)

    def discover_all_packages(self) -> List[Tuple[str, str]]:
        """
        Authoritatively discovers all active and bubbled packages from the file system,
        and cleans up any "ghost" entries from the Redis index that no longer exist.
        """
        print(_('🔍 Discovering all packages from file system (ground truth)...'))
        from packaging.utils import canonicalize_name
        found_on_disk = {}
        active_packages = {}
        try:
            for dist in importlib.metadata.distributions():
                pkg_name = canonicalize_name(dist.metadata.get('Name', ''))
                if not pkg_name:
                    continue
                if pkg_name not in found_on_disk:
                    found_on_disk[pkg_name] = set()
                found_on_disk[pkg_name].add(dist.version)
                active_packages[pkg_name] = dist.version
        except Exception as e:
            print(_('⚠️ Error discovering active packages: {}').format(e))
        multiversion_base_path = Path(self.config['multiversion_base'])
        if multiversion_base_path.is_dir():
            for bubble_dir in multiversion_base_path.iterdir():
                dist_info = next(bubble_dir.glob('*.dist-info'), None)
                if dist_info:
                    try:
                        from importlib.metadata import PathDistribution
                        dist = PathDistribution(dist_info)
                        pkg_name = canonicalize_name(dist.metadata.get('Name', ''))
                        if not pkg_name:
                            continue
                        if pkg_name not in found_on_disk:
                            found_on_disk[pkg_name] = set()
                        found_on_disk[pkg_name].add(dist.version)
                    except Exception:
                        continue
        print(_('    -> Reconciling file system state with Redis knowledge base...'))
        self._store_active_versions(active_packages)
        result_list = []
        for pkg_name, versions_set in found_on_disk.items():
            for version_str in versions_set:
                result_list.append((pkg_name, version_str))
        print(_('✅ Discovery complete. Found {} unique packages with {} total versions to process.').format(len(found_on_disk), len(result_list)))
        return sorted(result_list, key=lambda x: x[0])

    def _register_bubble_path(self, pkg_name: str, version: str, bubble_path: Path):
        """Register bubble paths in Redis for dedup across bubbles and main env."""
        redis_key = f'{self.redis_key_prefix}bubble:{pkg_name}:{version}:path'
        self.cache_client.set(redis_key, str(bubble_path))
        self.package_path_registry[pkg_name] = self.package_path_registry.get(pkg_name, {})
        self.package_path_registry[pkg_name][version] = str(bubble_path)

    def _store_active_versions(self, active_packages: Dict[str, str]):
        if not self.cache_client:
            return
        prefix = self.redis_key_prefix
        for pkg_name, version in active_packages.items():
            main_key = f'{prefix}{pkg_name}'
            try:
                self.cache_client.hset(main_key, 'active_version', version)
            except Exception as e:
                print(_('⚠️ Failed to store active version for {}: {}').format(pkg_name, e))

    def _perform_security_scan(self, packages: Dict[str, str]):
        """
        FIXED: Runs a security check using `safety`. Now properly handles the JSON
        output wrapped with deprecation warnings and correctly counts vulnerabilities.
        """
        scan_type = 'bulk' if len(packages) > 1 else 'targeted'
        if len(packages) == 0:
            scan_type = 'targeted'
        
        print(f'🛡️ Performing {scan_type} security scan for {len(packages)} active package(s)...')
        
        if not packages:
            print(_(' - No active packages found to scan.'))
            self.security_report = {}
            return
        
        python_exe = self.config.get('python_executable', sys.executable)
        
        # Check if safety is available
        try:
            subprocess.run([python_exe, '-m', 'safety', '--version'], check=True, capture_output=True, timeout=10)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            print(f" ⚠️ Warning: The 'safety' package is not installed for the active Python interpreter ({Path(python_exe).name}).")
            print(_(" 💡 To enable this feature, run: '{} -m pip install safety'").format(python_exe))
            self.security_report = {}
            return
        
        # Create requirements file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as reqs_file:
            reqs_file_path = reqs_file.name
            for name, version in packages.items():
                reqs_file.write(f'{name}=={version}\n')
        
        try:
            cmd = [python_exe, '-m', 'safety', 'check', '-r', reqs_file_path, '--json']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding='utf-8')
            
            if result.stdout:
                raw_output = result.stdout.strip()
                
                # Find JSON boundaries - safety wraps JSON with deprecation warnings
                json_start = -1
                json_end = -1
                
                # Look for the opening brace/bracket
                brace_pos = raw_output.find('{')
                bracket_pos = raw_output.find('[')
                
                if brace_pos != -1 and bracket_pos != -1:
                    json_start = min(brace_pos, bracket_pos)
                elif brace_pos != -1:
                    json_start = brace_pos
                elif bracket_pos != -1:
                    json_start = bracket_pos
                
                if json_start != -1:
                    # Find the matching closing brace/bracket
                    opening_char = raw_output[json_start]
                    closing_char = '}' if opening_char == '{' else ']'
                    
                    # Count nesting to find the proper end
                    count = 0
                    in_string = False
                    escape_next = False
                    
                    for i in range(json_start, len(raw_output)):
                        char = raw_output[i]
                        
                        if escape_next:
                            escape_next = False
                            continue
                        
                        if char == '\\':
                            escape_next = True
                            continue
                        
                        if char == '"' and not escape_next:
                            in_string = not in_string
                            continue
                        
                        if not in_string:
                            if char == opening_char:
                                count += 1
                            elif char == closing_char:
                                count -= 1
                                if count == 0:
                                    json_end = i + 1
                                    break
                    
                    if json_end > json_start:
                        json_content = raw_output[json_start:json_end]
                        try:
                            self.security_report = json.loads(json_content)
                        except json.JSONDecodeError as e:
                            print(f' ⚠️ Could not parse safety JSON output: {e}')
                            self.security_report = {}
                    else:
                        # Fallback: try parsing from json_start to end and let json.loads find the boundary
                        try:
                            # This will likely fail due to extra content, but worth trying
                            self.security_report = json.loads(raw_output[json_start:])
                        except json.JSONDecodeError:
                            print(' ⚠️ Could not determine JSON boundaries')
                            self.security_report = {}
                else:
                    print(' ⚠️ No JSON found in safety output')
                    self.security_report = {}
                
                if result.stderr:
                    print(_(' ⚠️ Safety command produced warnings. Stderr: {}').format(result.stderr.strip()))
            else:
                self.security_report = {}
                if result.stderr:
                    print(_(' ⚠️ Safety command failed. Stderr: {}').format(result.stderr.strip()))
                    
        except Exception as e:
            print(_(' ⚠️ An unexpected error occurred during the security scan: {}').format(e))
            self.security_report = {}
        finally:
            if os.path.exists(reqs_file_path):
                os.unlink(reqs_file_path)
        
        # Count actual vulnerabilities from the parsed safety report
        issue_count = 0
        
        if isinstance(self.security_report, dict):
            # Modern safety format has a 'vulnerabilities' key with the actual vulnerabilities
            if 'vulnerabilities' in self.security_report:
                vulnerabilities = self.security_report['vulnerabilities']
                if isinstance(vulnerabilities, list):
                    issue_count = len(vulnerabilities)
            # Also check report_meta for vulnerabilities_found (more reliable)
            elif 'report_meta' in self.security_report:
                meta = self.security_report['report_meta']
                if isinstance(meta, dict) and 'vulnerabilities_found' in meta:
                    issue_count = int(meta['vulnerabilities_found'])
            # Legacy format fallback
            else:
                for key, value in self.security_report.items():
                    if isinstance(value, list) and key not in [
                        'scanned_packages', 'scanned', 'scanned_full_path', 
                        'target_languages', 'announcements', 'ignored_vulnerabilities'
                    ]:
                        issue_count += len(value)
        
        print(_('✅ Security scan complete. Found {} potential issues.').format(issue_count))

    def run(self, targeted_packages: Optional[List[str]]=None, newly_active_packages: Optional[Dict[str, str]]=None):
        """
        FIXED (v2): The main execution loop. It now safely handles corrupted
        package metadata during the pre-scan phase, preventing crashes.
        """
        if not self.cache_client:
            print(_('❌ Cache client not available to the builder. Aborting.'))
            return
        distributions_to_process = self._discover_distributions(targeted_packages)
        if targeted_packages:
            newly_active_packages_to_scan = {}
            for dist in distributions_to_process:
                if not self._is_bubbled(dist):
                    raw_name = dist.metadata.get('Name')
                    if raw_name:
                        newly_active_packages_to_scan[canonicalize_name(raw_name)] = dist.version
            self._perform_security_scan(newly_active_packages_to_scan)
        else:
            all_active_packages_to_scan = {}
            for dist in distributions_to_process:
                if not self._is_bubbled(dist):
                    raw_name = dist.metadata.get('Name')
                    if raw_name:
                        name = canonicalize_name(raw_name)
                        all_active_packages_to_scan[name] = dist.version
                    else:
                        print(_("\n⚠️  WARNING: Skipping corrupted package found at '{}'.").format(dist._path))
                        print(_("    This package's metadata is missing a name. This is often caused by an"))
                        print(_("    interrupted 'pip install'. Please manually delete this directory."))
            self._perform_security_scan(all_active_packages_to_scan)
        if not distributions_to_process:
            print(_('✅ No packages found or specified to process.'))
            return
        iterator = distributions_to_process
        if HAS_TQDM:
            iterator = tqdm(distributions_to_process, desc='Processing packages', unit='pkg')
        updated_count = 0
        for dist in iterator:
            if self._process_package(dist):
                updated_count += 1
        print(_('\n🎉 Metadata building complete! Updated {} package(s).').format(updated_count))

    def _process_package(self, dist) -> bool:
        """
        FIXED: Processes a single, definitive Distribution object and now gracefully
        handles corrupted packages by initializing error-reporting variables early.
        """
        # --- THIS IS THE FIX ---
        # Initialize variables for the error handler *before* any operation that might fail.
        # We can get the version safely, as it's a direct property.
        pkg_name_for_error = f"Unknown Package at {dist._path}"
        version_for_error = dist.version
        
        try:
            raw_name = dist.metadata.get('Name')
            if not raw_name:
                print(_("\n⚠️ CORRUPTION DETECTED: Package at '{}' is missing a name.").format(dist._path))
                # --- AUTO-REPAIR LOGIC ---
                if self.omnipkg_instance:
                    print(" - 🛡️ Attempting auto-repair...")
                    # Try to parse name and version from the directory name itself
                    match = re.match(r"([\w\.\-]+)-([\w\.\+a-z0-9]+)\.dist-info", dist._path.name)
                    if match:
                        name, version = match.groups()
                        package_to_reinstall = f"{name}=={version}"
                        # Determine if the corruption is in a bubble or the main env
                        is_in_bubble = str(dist._path).startswith(self.config.get('multiversion_base', '/dev/null'))
                        target_dir = dist._path.parent if is_in_bubble else None
                        cleanup_path = dist._path.parent if is_in_bubble else Path(self.config.get('site_packages_path'))
                        
                        print(f" - Cleaning up corrupted files for '{name}' at {cleanup_path}...")
                        self.omnipkg_instance._brute_force_package_cleanup(name, cleanup_path)
                        
                        print(f" - 🚀 Re-installing '{package_to_reinstall}' to heal the environment...")
                        self.omnipkg_instance.smart_install(
                            [package_to_reinstall], 
                            force_reinstall=True, 
                            target_directory=target_dir
                        )
                        # After reinstalling, we don't process the *old* corrupted object.
                        # The new one will be picked up by the next sync.
                        return False
                    else:
                        print(" - ❌ Auto-repair failed: Could not parse package name from path.")
                else:
                    print(_(" To fix, please manually delete this directory and re-run your command."))
                return False
            
            # --- CRITICAL FIX: Extract name from metadata ---
            name = raw_name.strip()  # This was missing!
            version = dist.version
            
            # Update error reporting variables with actual package info
            pkg_name_for_error = name
            version_for_error = version
            
            # --- START MODIFIED CODE ---
            metadata = self._build_comprehensive_metadata(dist)
            is_active = not self._is_bubbled(dist)  # Determine status from path
            self._store_in_redis(name, version, metadata, is_active=is_active)
            # --- END MODIFIED CODE ---
            
            return True
            
        except Exception as e:
            print(_('\n❌ Error processing {} (v{}): {}').format(pkg_name_for_error, version_for_error, e))
            import traceback
            traceback.print_exc()
            return False

    def _build_comprehensive_metadata(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        FIXED: Builds metadata exclusively from the provided Distribution object
        and now includes the physical path of the package.
        """
        package_name = canonicalize_name(dist.metadata['Name'])
        metadata = {k: v for k, v in dist.metadata.items()}
        
        # --- START NEW CODE ---
        try:
            # dist.locate_file('') gives the path to the top-level package directory
            package_path = dist.locate_file('')
            metadata['path'] = str(package_path)
        except Exception:
            # Fallback to the .dist-info path if the above fails
            metadata['path'] = str(dist._path)
        # --- END NEW CODE ---

        metadata['last_indexed'] = datetime.now().isoformat()

        context_version = self.target_context_version if self.target_context_version else get_python_version()
        metadata['indexed_by_python'] = context_version
        metadata['dependencies'] = [str(req) for req in dist.requires] if dist.requires else []
        package_files = self._find_package_files(dist)
        if package_files.get('binaries'):
            metadata['help_text'] = self._get_help_output(package_files['binaries'][0]).get('help_text', 'No executable binary found.')
        else:
            metadata['help_text'] = 'No executable binary found.'
        metadata['cli_analysis'] = self._analyze_cli(metadata.get('help_text', ''))
        metadata['security'] = self._get_security_info(package_name)
        metadata['health'] = self._perform_health_checks(dist, package_files)
        metadata['checksum'] = self._generate_checksum(metadata)
        return metadata

    def _find_distribution_at_path(self, package_name: str, version: str, search_path: Path) -> Optional[importlib.metadata.Distribution]:
        normalized_name_dash = canonicalize_name(package_name)
        normalized_name_under = normalized_name_dash.replace('-', '_')
        for name_variant in {normalized_name_dash, normalized_name_under}:
            for dist_info in search_path.glob(f'{name_variant}-{version}*.dist-info'):
                if dist_info.is_dir():
                    try:
                        from importlib.metadata import PathDistribution
                        dist = PathDistribution(dist_info)
                        metadata_name = dist.metadata.get('Name', '')
                        if canonicalize_name(metadata_name) == normalized_name_dash and dist.metadata.get('Version') == version:
                            return dist
                    except Exception:
                        continue
        return None

    def _parse_metadata_file(self, metadata_content: str) -> Dict:
        metadata = {}
        current_key = None
        current_value = []
        for line in metadata_content.splitlines():
            if ': ' in line and (not line.startswith(' ')):
                if current_key:
                    metadata[current_key] = '\n'.join(current_value).strip() if current_value else ''
                current_key, value = line.split(': ', 1)
                current_value = [value]
            elif line.startswith(' ') and current_key:
                current_value.append(line.strip())
        if current_key:
            metadata[current_key] = '\n'.join(current_value).strip() if current_value else ''
        return metadata

    def _store_in_redis(self, package_name: str, version_str: str, metadata: Dict, is_active: bool):
        pkg_name_lower = canonicalize_name(package_name)
        prefix = self.redis_key_prefix
        version_key = f'{prefix}{pkg_name_lower}:{version_str}'
        main_key = f'{prefix}{pkg_name_lower}'
        data_to_store = metadata.copy()
        for field in ['help_text', 'readme_snippet', 'license_text', 'Description']:
            if field in data_to_store and isinstance(data_to_store[field], str) and (len(data_to_store[field]) > 500):
                compressed = zlib.compress(data_to_store[field].encode('utf-8'))
                data_to_store[field] = compressed.hex()
                data_to_store[f'{field}_compressed'] = 'true'
        flattened_data = self._flatten_dict(data_to_store)
        with self.cache_client.pipeline() as pipe:
            pipe.delete(version_key)
            pipe.hset(version_key, mapping=flattened_data)
            pipe.hset(main_key, 'name', package_name)
            pipe.sadd(f'{main_key}:installed_versions', version_str)
            if is_active:
                pipe.hset(main_key, 'active_version', version_str)
            else:
                pipe.hset(main_key, f'bubble_version:{version_str}', 'true')
            index_key = f"{prefix.rsplit(':', 2)[0]}:index"
            pipe.sadd(index_key, pkg_name_lower)
            pipe.execute()

    def _perform_health_checks(self, dist: importlib.metadata.Distribution, package_files: Dict) -> Dict:
        """
        FIXED: Passes the specific distribution to the verification function.
        """
        health_data = {'import_check': self._verify_installation(dist), 'binary_checks': {Path(bin_path).name: self._check_binary_integrity(bin_path) for bin_path in package_files.get('binaries', [])}}
        oversized = [name for name, check in health_data['binary_checks'].items() if check.get('size', 0) > 10000000]
        if oversized:
            health_data['size_warnings'] = oversized
        return health_data

    def _verify_installation(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        SMART VERSION: Uses authoritative top_level.txt and fallback strategies
        to correctly verify importability of any package structure.
        """
        package_name = canonicalize_name(dist.metadata['Name'])
        is_bubbled = self._is_bubbled(dist)
        bubble_path = str(dist._path.parent) if is_bubbled else None
        
        # Strategy 1: Use authoritative top_level.txt
        import_candidates = self._get_import_candidates(dist, package_name)
        
        # Build the verification script
        script_lines = [
            'import sys',
            'import importlib',
            'import traceback',
            'results = []'
        ]
        
        if bubble_path:
            script_lines.append(f"sys.path.insert(0, r'{bubble_path}')")
        
        # Test each import candidate
        for candidate in import_candidates:
            script_lines.extend([
                f"# Testing import: {candidate}",
                "try:",
                f"    mod = importlib.import_module('{candidate}')",
                f"    version = getattr(mod, '__version__', None)",
                f"    results.append(('{candidate}', True, version))",
                "except Exception as e:",
                f"    results.append(('{candidate}', False, str(e)))"
            ])
        
        # Output results
        script_lines.extend([
            "import json",
            "print(json.dumps(results))"
        ])
        
        script = '\n'.join(script_lines)
        import json
        try:
            python_exe = self.config.get('python_executable', sys.executable)
            result = subprocess.run(
                [python_exe, '-c', script], 
                capture_output=True, 
                text=True, 
                check=True, 
                timeout=10
            )
            
            # Parse results
            import json
            test_results = json.loads(result.stdout.strip())
            
            # Analyze results
            successful_imports = [(name, version) for name, success, version in test_results if success]
            failed_imports = [(name, error) for name, success, error in test_results if not success]
            
            if successful_imports:
                # Package is importable - get version from successful import or metadata
                import_version = None
                for name, version in successful_imports:
                    if version and version != 'None':
                        import_version = version
                        break
                
                # Fallback to metadata version
                if not import_version:
                    try:
                        import_version = dist.version
                    except:
                        import_version = "unknown"
                
                return {
                    'importable': True,
                    'version': import_version,
                    'successful_modules': [name for name, _ in successful_imports],
                    'failed_modules': [name for name, _ in failed_imports] if failed_imports else []
                }
            else:
                return {
                    'importable': False,
                    'error': f"All import attempts failed: {dict(failed_imports)}",
                    'attempted_modules': import_candidates
                }
                
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            error_msg = e.stderr.strip() if hasattr(e, 'stderr') and e.stderr else str(e)
            return {
                'importable': False, 
                'error': f"Subprocess failed: {error_msg}",
                'attempted_modules': import_candidates
            }

    def _get_import_candidates(self, dist: importlib.metadata.Distribution, package_name: str) -> List[str]:
        """
        Get the authoritative list of import candidates for a package.
        Uses multiple strategies in order of reliability.
        """
        candidates = []
        
        # Strategy 1: Read top_level.txt (most authoritative)
        try:
            if hasattr(dist, 'read_text'):
                top_level_content = dist.read_text('top_level.txt')
                if top_level_content:
                    candidates.extend([
                        line.strip() for line in top_level_content.strip().split('\n') 
                        if line.strip()
                    ])
        except Exception:
            pass
        
        # Strategy 2: Parse RECORD file for top-level modules
        if not candidates:
            candidates.extend(self._parse_record_for_modules(dist))
        
        # Strategy 3: Use package structure heuristics
        if not candidates:
            candidates.extend(self._generate_import_heuristics(package_name))
        
        # Strategy 4: Last resort - the naive approach
        if not candidates:
            candidates.append(package_name.replace('-', '_'))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                unique_candidates.append(candidate)
        
        return unique_candidates

    def _parse_record_for_modules(self, dist: importlib.metadata.Distribution) -> List[str]:
        """
        Parse the RECORD file to identify top-level modules.
        """
        candidates = []
        try:
            if hasattr(dist, 'read_text'):
                record_content = dist.read_text('RECORD')
                if record_content:
                    import os
                    top_level_dirs = set()
                    for line in record_content.strip().split('\n'):
                        if line.strip():
                            file_path = line.split(',')[0]  # First part is the file path
                            # Get top-level directory/module
                            parts = file_path.split('/')
                            if parts and not parts[0].endswith('.dist-info'):
                                # Check if it's a module file or package directory
                                top_part = parts[0]
                                if '.' not in top_part or top_part.endswith('.py'):
                                    module_name = top_part.replace('.py', '')
                                    if module_name and not module_name.startswith('_'):
                                        top_level_dirs.add(module_name)
                    
                    candidates.extend(sorted(top_level_dirs))
        except Exception:
            pass
        
        return candidates

    def _generate_import_heuristics(self, package_name: str) -> List[str]:
        """
        Generate smart import candidates based on package name patterns.
        """
        candidates = []
        
        # Handle common namespace package patterns
        if '.' in package_name:
            # For packages like 'ruamel.yaml', try both 'ruamel.yaml' and 'ruamel'
            candidates.append(package_name)  # ruamel.yaml
            candidates.append(package_name.split('.')[0])  # ruamel
        
        # Handle dash-to-underscore conversion
        underscore_name = package_name.replace('-', '_')
        if underscore_name != package_name:
            candidates.append(underscore_name)
        
        # Handle common patterns
        if package_name.startswith('python-'):
            # python-requests -> requests
            candidates.append(package_name[7:])
            candidates.append(package_name[7:].replace('-', '_'))
        
        if package_name.endswith('-python'):
            # something-python -> something
            candidates.append(package_name[:-7])
            candidates.append(package_name[:-7].replace('-', '_'))
        
        # Handle PyPI vs import name mismatches (common cases)
        common_mappings = {
            'beautifulsoup4': ['bs4'],
            'pillow': ['PIL'],
            'pyyaml': ['yaml'],
            'msgpack-python': ['msgpack'],
            'protobuf': ['google.protobuf', 'google'],
            'python-dateutil': ['dateutil'],
            'setuptools-scm': ['setuptools_scm'],
        }
        
        canonical = canonicalize_name(package_name)
        if canonical in common_mappings:
            candidates.extend(common_mappings[canonical])
        
        return candidates

    def _check_binary_integrity(self, bin_path: str) -> Dict:
        if not os.path.exists(bin_path):
            return {'exists': False}
        integrity_report = {'exists': True, 'size': os.path.getsize(bin_path), 'is_elf': False, 'valid_shebang': self._has_valid_shebang(bin_path)}
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

    def _find_package_files(self, dist: importlib.metadata.Distribution) -> Dict:
        """
        FIXED: Authoritatively finds files belonging to the specific distribution.
        """
        files = {'binaries': []}
        if not dist or not dist.files:
            return files
        for file_path in dist.files:
            try:
                abs_path = dist.locate_file(file_path)
                if 'bin' in file_path.parts or 'Scripts' in file_path.parts:
                    if abs_path and abs_path.exists() and os.access(abs_path, os.X_OK):
                        files['binaries'].append(str(abs_path))
            except (FileNotFoundError, NotADirectoryError):
                continue
        return files

    def _run_bulk_security_check(self, packages: Dict[str, str]):
        reqs_file_path = '/tmp/bulk_safety_reqs.txt'
        try:
            with open(reqs_file_path, 'w') as f:
                for name, version in packages.items():
                    f.write(f'{name}=={version}\n')
            python_exe = self.config.get('python_executable', sys.executable)
            result = subprocess.run([python_exe, '-m', 'safety', 'check', '-r', reqs_file_path, '--json'], capture_output=True, text=True, timeout=120)
            if result.stdout:
                self.security_report = json.loads(result.stdout)
        except Exception as e:
            print(_('    ⚠️ Bulk security scan failed: {}').format(e))
        finally:
            if os.path.exists(reqs_file_path):
                os.remove(reqs_file_path)

    def _get_security_info(self, package_name: str) -> Dict:
        """
        FIXED: Parses the security report from `safety`, correctly handling both the
        legacy object format ({'pkg': [...]}) and the modern list format ([...]).
        """
        c_name = canonicalize_name(package_name)
        vulnerabilities = []
        if isinstance(self.security_report, dict):
            vulnerabilities = self.security_report.get(c_name, [])
        elif isinstance(self.security_report, list):
            vulnerabilities = [vuln for vuln in self.security_report if isinstance(vuln, dict) and canonicalize_name(vuln.get('package_name', '')) == c_name]
        return {'audit_status': 'checked_in_bulk', 'issues_found': len(vulnerabilities), 'report': vulnerabilities}

    def _generate_checksum(self, metadata: Dict) -> str:
        core_data = {'Version': metadata.get('Version'), 'dependencies': metadata.get('dependencies'), 'help_text': metadata.get('help_text')}
        data_string = json.dumps(core_data, sort_keys=True)
        return hashlib.sha256(data_string.encode('utf-8')).hexdigest()

    def _get_help_output(self, executable_path: str) -> Dict:
        if not os.path.exists(executable_path):
            return {'help_text': 'Executable not found.'}
        for flag in ['--help', '-h']:
            try:
                result = subprocess.run([executable_path, flag], capture_output=True, text=True, timeout=3, errors='ignore')
                output = (result.stdout or result.stderr).strip()
                if output and 'usage:' in output.lower():
                    return {'help_text': output[:5000]}
            except Exception:
                continue
        return {'help_text': 'No valid help output captured.'}

    def _analyze_cli(self, help_text: str) -> Dict:
        if not help_text or 'No valid help' in help_text:
            return {}
        analysis = {'common_flags': [], 'subcommands': []}
        lines = help_text.split('\n')
        command_regex = re.compile('^\\s*([a-zA-Z0-9_-]+)\\s{2,}(.*)')
        in_command_section = False
        for line in lines:
            if re.search('^(commands|available commands):', line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and (not line.strip()):
                in_command_section = False
                continue
            if in_command_section:
                match = command_regex.match(line)
                if match:
                    command_name = match.group(1).strip()
                    if not command_name.startswith('-'):
                        analysis['subcommands'].append({'name': command_name, 'description': match.group(2).strip()})
        if not analysis['subcommands']:
            analysis['subcommands'] = [{'name': cmd, 'description': 'N/A'} for cmd in self._fallback_analyze_cli(lines)]
        analysis['common_flags'] = list(set(re.findall('--[a-zA-Z0-9][a-zA-Z0-9-]+', help_text)))
        return analysis

    def _fallback_analyze_cli(self, lines: list) -> list:
        subcommands = []
        in_command_section = False
        for line in lines:
            if re.search('commands:', line, re.IGNORECASE):
                in_command_section = True
                continue
            if in_command_section and line.strip():
                match = re.match('^\\s*([a-zA-Z0-9_-]+)', line)
                if match:
                    subcommands.append(match.group(1))
            elif in_command_section and (not line.strip()):
                in_command_section = False
        return list(set(subcommands))

    def _get_distribution(self, package_name: str, version: str=None):
        try:
            dist = importlib.metadata.distribution(package_name)
            if version is None or dist.version == version:
                return dist
        except importlib.metadata.PackageNotFoundError:
            pass
        if version:
            bubble_path = Path(self.config['multiversion_base']) / f'{package_name}-{version}'
            return self._find_distribution_at_path(package_name, version, bubble_path)
        return None

    def _enrich_from_site_packages(self, name: str, version: str=None) -> Dict:
        enriched_data = {}
        guesses = set([name, name.lower().replace('-', '_')])
        base_path = Path(get_site_packages_path())
        if version:
            base_path = Path(self.config['multiversion_base']) / f'{name}-{version}'
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

    def _flatten_dict(self, d: Dict, parent_key: str='', sep: str='.') -> Dict:
        items = []
        for k, v in d.items():
            new_key = f'{parent_key}{sep}{k}' if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, str(v)))
        return dict(items)

if __name__ == '__main__':
    import json
    from pathlib import Path
    import hashlib
    # --- START OF CHANGE ---
    # Import the SQLite client needed for standalone execution
    from omnipkg.cache import SQLiteCacheClient
    # --- END OF CHANGE ---

    print(_('🚀 Starting omnipkg Metadata Builder v12 (SQLite/Redis Edition)...'))
    try:
        config_path = Path.home() / '.config' / 'omnipkg' / 'config.json'
        with open(config_path, 'r') as f:
            full_config = json.load(f)
        
        # --- CORE FIX: Determine the one true env_id, prioritizing the override ---
        env_id_from_os = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE')
        if env_id_from_os:
            env_id = env_id_from_os
            print(f"   (Inherited environment ID: {env_id})")
        else:
            # This is the fallback for when the script is run directly
            # We must find the venv root the same way ConfigManager does.
            current_dir = Path(sys.executable).resolve().parent
            venv_path = Path(sys.prefix) # fallback
            while current_dir != current_dir.parent:
                if (current_dir / 'pyvenv.cfg').exists():
                    venv_path = current_dir
                    break
                current_dir = current_dir.parent
            env_id = hashlib.md5(str(venv_path.resolve()).encode()).hexdigest()[:8]
            print(f"   (Calculated environment ID: {env_id})")
        # --- END CORE FIX ---

        config = full_config['environments'][env_id]
    except (FileNotFoundError, KeyError) as e:
        print(f'❌ CRITICAL: Could not load omnipkg configuration for this environment (ID: {env_id}). Error: {e}. Aborting.')
        sys.exit(1)
        
    # The gatherer is created without the omnipkg_instance, so its cache_client is initially None.
    gatherer = omnipkgMetadataGatherer(config=config, env_id=env_id, force_refresh='--force' in sys.argv)
    
    # --- START OF REPLACEMENT for connect_redis() ---
    try:
        # 1. Manually create the SQLiteCacheClient instance for standalone mode.
        #    It's best practice to get the cache path from the config.
        cache_dir = Path(config.get('cache_dir', Path.home() / '.cache' / 'omnipkg'))
        db_path = cache_dir / f'omnipkg_cache_{env_id}.db'
        
        print(f"   (Using SQLite cache at: {db_path})")
        
        # 2. Assign the created client directly to the gatherer instance.
        gatherer.cache_client = SQLiteCacheClient(db_path=db_path)

        # 3. Check if the connection is valid, similar to the old ping() check.
        if gatherer.cache_client and gatherer.cache_client.ping():
            targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
            if targeted_packages:
                gatherer.run(targeted_packages=targeted_packages)
            else:
                gatherer.run()
            print(_('\n🎉 Metadata building complete!'))
        else:
            print(_('❌ Failed to connect to SQLite cache. Aborting.'))
            sys.exit(1)
    # --- END OF REPLACEMENT for connect_redis() ---
    except Exception as e:
        print(_('\n❌ An unexpected error occurred during metadata build: {}').format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)