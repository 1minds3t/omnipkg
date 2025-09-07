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
import tempfile
import concurrent.futures
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from packaging.utils import canonicalize_name
from omnipkg.i18n import _
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

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

    def __init__(self, config: Dict, env_id: str, force_refresh: bool=False):
        self.redis_client = None
        self.force_refresh = force_refresh
        self.security_report = {}
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

    def connect_redis(self) -> bool:
        try:
            self.redis_client = redis.Redis(host=self.config['redis_host'], port=self.config['redis_port'], decode_responses=True)
            self.redis_client.ping()
            print(_('✅ Connected to Redis successfully.'))
            return True
        except Exception as e:
            print(_('❌ Could not connect to Redis: {}').format(e))
            return False

    def _discover_distributions(self, targeted_packages: Optional[List[str]]) -> List[importlib.metadata.Distribution]:
        """
        FIXED: Authoritatively discovers distributions. In targeted mode, it now
        uses a robust fallback to manually scan site-packages for .dist-info
        directories if the standard importlib lookup fails, which is crucial for
        binary-heavy packages like 'uv'.
        """
        if targeted_packages:
            print(f'🎯 Running in targeted mode for {len(targeted_packages)} package(s).')
            discovered_dists = []
            site_packages = Path(self.config.get('site_packages_path', '/dev/null'))
            multiversion_base = Path(self.config.get('multiversion_base', '/dev/null'))
            for spec in targeted_packages:
                try:
                    name, version = spec.split('==')
                    c_name = canonicalize_name(name)
                    found_dist = None
                    try:
                        dist = importlib.metadata.distribution(name)
                        if dist.version == version:
                            found_dist = dist
                            print(f'   -> Found active distribution for {spec} via standard lookup.')
                    except importlib.metadata.PackageNotFoundError:
                        pass
                    if not found_dist:
                        bubble_path = multiversion_base / f'{name}-{version}'
                        if bubble_path.is_dir():
                            dist_info_path = next(bubble_path.glob(f'{c_name}-{version}*.dist-info'), None)
                            if dist_info_path:
                                found_dist = importlib.metadata.Distribution.at(dist_info_path)
                                print(f'   -> Found bubbled distribution for {spec} at {dist_info_path.parent.name}')
                    if not found_dist and site_packages.is_dir():
                        dist_info_path = next(site_packages.glob(f'{c_name}-{version}*.dist-info'), None)
                        if dist_info_path:
                            found_dist = importlib.metadata.Distribution.at(dist_info_path)
                            print(f'   -> Found active distribution for {spec} via manual filesystem scan.')
                    if found_dist:
                        discovered_dists.append(found_dist)
                    else:
                        print(_("   ⚠️ Could not find any distribution matching spec '{}'. This may be an installation issue.").format(spec))
                except ValueError:
                    print(_("   ⚠️ Could not parse spec '{}'. Skipping.").format(spec))
            return discovered_dists
        print('🔍 Discovering all packages from file system (ground truth)...')
        search_paths = []
        site_packages = self.config.get('site_packages_path')
        if site_packages and Path(site_packages).is_dir():
            search_paths.append(site_packages)
        multiversion_base = self.config.get('multiversion_base')
        if multiversion_base and Path(multiversion_base).is_dir():
            search_paths.extend([str(p) for p in Path(multiversion_base).iterdir() if p.is_dir()])
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
        self.redis_client.set(redis_key, str(bubble_path))
        self.package_path_registry[pkg_name] = self.package_path_registry.get(pkg_name, {})
        self.package_path_registry[pkg_name][version] = str(bubble_path)

    def _store_active_versions(self, active_packages: Dict[str, str]):
        if not self.redis_client:
            return
        prefix = self.redis_key_prefix
        for pkg_name, version in active_packages.items():
            main_key = f'{prefix}{pkg_name}'
            try:
                self.redis_client.hset(main_key, 'active_version', version)
            except Exception as e:
                print(_('⚠️ Failed to store active version for {}: {}').format(pkg_name, e))

    def _perform_security_scan(self, packages: Dict[str, str]):
        """
        FIXED: Runs a security check using `safety`. The log output is now
        context-aware and gracefully handles cases where `safety` is not
        installed in the target Python interpreter. It is resilient to non-JSON output.
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
        try:
            subprocess.run([python_exe, '-m', 'safety', '--version'], check=True, capture_output=True, timeout=10)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            print(f" ⚠️ Warning: The 'safety' package is not installed for the active Python interpreter ({Path(python_exe).name}).")
            print(_(" 💡 To enable this feature, run: '{} -m pip install safety'").format(python_exe))
            self.security_report = {}
            return
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as reqs_file:
            reqs_file_path = reqs_file.name
            for name, version in packages.items():
                reqs_file.write(f'{name}=={version}\n')
        try:
            cmd = [python_exe, '-m', 'safety', 'check', '-r', reqs_file_path, '--json']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding='utf-8')
            if result.stdout:
                raw_output = result.stdout.strip()
                json_start_index = raw_output.find('[')
                if json_start_index == -1:
                    json_start_index = raw_output.find('{')
                if json_start_index != -1:
                    json_string = raw_output[json_start_index:]
                    bracket_count = 0
                    brace_count = 0
                    json_end_index = 0
                    in_string = False
                    escape_next = False
                    for i, char in enumerate(json_string):
                        if escape_next:
                            escape_next = False
                            continue
                        if char == '\\':
                            escape_next = True
                            continue
                        if char == '"' and (not escape_next):
                            in_string = not in_string
                            continue
                        if not in_string:
                            if char == '[':
                                bracket_count += 1
                            elif char == ']':
                                bracket_count -= 1
                            elif char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                            if bracket_count == 0 and brace_count == 0 and (i > 0):
                                json_end_index = i + 1
                                break
                    if json_end_index > 0:
                        clean_json = json_string[:json_end_index]
                    else:
                        clean_json = json_string
                    try:
                        self.security_report = json.loads(clean_json)
                    except json.JSONDecodeError:
                        lines = raw_output.split('\n')
                        json_lines = []
                        collecting = False
                        for line in lines:
                            if line.strip().startswith('[') or line.strip().startswith('{'):
                                collecting = True
                            if collecting:
                                json_lines.append(line)
                                try:
                                    potential_json = '\n'.join(json_lines)
                                    self.security_report = json.loads(potential_json)
                                    break
                                except json.JSONDecodeError:
                                    continue
                        if not hasattr(self, 'security_report') or not self.security_report:
                            self.security_report = {}
                else:
                    self.security_report = {}
                if result.stderr:
                    print(_(' ⚠️ Safety command produced warnings. Stderr: {}').format(result.stderr.strip()))
            else:
                self.security_report = {}
                if result.stderr:
                    print(_(' ⚠️ Safety command failed. Stderr: {}').format(result.stderr.strip()))
        except json.JSONDecodeError as e:
            print(f' ⚠️ Could not parse safety JSON output. This can happen with very old versions of `safety`. Error: {e}')
            self.security_report = {}
        except Exception as e:
            print(_(' ⚠️ An unexpected error occurred during the security scan: {}').format(e))
            self.security_report = {}
        finally:
            os.unlink(reqs_file_path)
        issue_count = len(self.security_report) if isinstance(self.security_report, (list, dict)) else 0
        print(_('✅ Security scan complete. Found {} potential issues.').format(issue_count))

    def run(self, targeted_packages: Optional[List[str]]=None, newly_active_packages: Optional[Dict[str, str]]=None):
        """
        FIXED (v2): The main execution loop. It now safely handles corrupted
        package metadata during the pre-scan phase, preventing crashes.
        """
        if not self.connect_redis():
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

    def _process_package(self, dist: importlib.metadata.Distribution) -> bool:
        """
        FIXED: Processes a single, definitive Distribution object and now gracefully
        handles corrupted packages that might lack a name or other critical metadata.
        """
        pkg_name_for_error = 'Unknown Package'
        try:
            raw_name = dist.metadata.get('Name')
            if not raw_name:
                print(_("\n⚠️  WARNING: Skipping corrupted package found at '{}'.").format(dist._path))
                print(_("    This package's metadata is missing a name. This often happens when an"))
                print(_('    install is interrupted. To fix, please manually delete this directory and'))
                print(_('    re-run your command.'))
                return False
            pkg_name_for_error = raw_name
            name = canonicalize_name(raw_name)
            version = dist.version
            version_key = f'{self.redis_key_prefix}{name}:{version}'
            if not self.force_refresh and self.redis_client.exists(version_key):
                return False
                        # --- START MODIFIED CODE ---
            metadata = self._build_comprehensive_metadata(dist)
            is_active = not self._is_bubbled(dist) # Determine status from path
            self._store_in_redis(name, version, metadata, is_active=is_active)
            # --- END MODIFIED CODE ---
            return True
            
        except Exception as e:
            print(_('\n❌ Error processing {} (v{}): {}').format(pkg_name_for_error, dist.version, e))
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
        metadata['indexed_by_python'] = get_python_version()
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
        with self.redis_client.pipeline() as pipe:
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
        FIXED: Uses a subprocess that can add a bubble's path to correctly test
        the importability of a bubbled package.
        """
        package_name = canonicalize_name(dist.metadata['Name'])
        import_name = package_name.replace('-', '_')
        is_bubbled = self._is_bubbled(dist)
        bubble_path = str(dist._path.parent) if is_bubbled else None
        script_lines = ['import sys']
        if bubble_path:
            script_lines.append(f"sys.path.insert(0, r'{bubble_path}')")
        script_lines.extend(['import importlib.metadata', f"print(importlib.metadata.version('{import_name}'))"])
        script = '; '.join(script_lines)
        try:
            python_exe = self.config.get('python_executable', sys.executable)
            result = subprocess.run([python_exe, '-c', script], capture_output=True, text=True, check=True, timeout=5)
            return {'importable': True, 'version': result.stdout.strip()}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return {'importable': False, 'error': e.stderr.strip() if hasattr(e, 'stderr') else str(e)}

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
    import hashlib # Ensure hashlib is imported here
    print(_('🚀 Starting omnipkg Metadata Builder v11 (Multi-Version Complete Edition)...'))
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
        
    gatherer = omnipkgMetadataGatherer(config=config, env_id=env_id, force_refresh='--force' in sys.argv)
    
    try:
        if gatherer.connect_redis():
            targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
            if targeted_packages:
                gatherer.run(targeted_packages=targeted_packages)
            else:
                gatherer.run()
            print(_('\n🎉 Metadata building complete!'))
        else:
            print(_('❌ Failed to connect to Redis. Aborting.'))
            sys.exit(1)
    except Exception as e:
        print(_('\n❌ An unexpected error occurred during metadata build: {}').format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)