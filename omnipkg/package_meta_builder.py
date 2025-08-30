from omnipkg.i18n import _
'\nomnipkg_metadata_builder.py - v11 - The "Multi-Version Complete" Edition\nA fully integrated, self-aware metadata gatherer with complete multi-version\nsupport for robust, side-by-side package management.\n'
import os
import re
import json
import subprocess
import redis
import hashlib
import importlib.metadata
import zlib
import sys
import concurrent.futures
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from packaging.utils import canonicalize_name
from tqdm import tqdm
HAS_TQDM = True
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

def get_python_version():
    """Get current Python version in X.Y format"""
    return _('{}.{}').format(sys.version_info.major, sys.version_info.minor)

def get_site_packages_path():
    """Dynamically find the site-packages path"""
    import site
    site_packages_dirs = site.getsitepackages()
    if hasattr(site, 'getusersitepackages'):
        site_packages_dirs.append(site.getusersitepackages())
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        venv_site_packages = Path(sys.prefix) / 'lib' / _('python{}').format(get_python_version()) / 'site-packages'
        if venv_site_packages.exists():
            return str(venv_site_packages)
    for sp in site_packages_dirs:
        if Path(sp).exists():
            return sp
    return str(Path(sys.executable).parent.parent / 'lib' / _('python{}').format(get_python_version()) / 'site-packages')

def get_bin_paths():
    """Get binary paths to index"""
    paths = [str(Path(sys.executable).parent)]
    if hasattr(sys, 'prefix') and sys.prefix != sys.base_prefix:
        venv_bin = str(Path(sys.prefix) / 'bin')
        if venv_bin not in paths and Path(venv_bin).exists():
            paths.append(venv_bin)
    return paths

class omnipkgMetadataGatherer:

    def __init__(self, config: Dict, force_refresh: bool=False):
        self.redis_client = None
        self.force_refresh = force_refresh
        self.security_report = {}
        self.config = config
        self.package_path_registry = {}
        if self.force_refresh:
            print(_('🟢 --force flag detected. Caching will be ignored.'))
        if not HAS_TQDM:
            print(_("⚠️ Install 'tqdm' for a better progress bar."))

    def connect_redis(self) -> bool:
        try:
            self.redis_client = redis.Redis(host=self.config['redis_host'], port=self.config['redis_port'], decode_responses=True)
            self.redis_client.ping()
            print(_('✅ Connected to Redis successfully.'))
            return True
        except Exception as e:
            print(_('❌ Could not connect to Redis: {}').format(e))
            return False

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
        redis_key = '{}bubble:{}:{}:path'.format(self.config['redis_key_prefix'], pkg_name, version)
        self.redis_client.set(redis_key, str(bubble_path))
        self.package_path_registry[pkg_name] = self.package_path_registry.get(pkg_name, {})
        self.package_path_registry[pkg_name][version] = str(bubble_path)

    def _store_active_versions(self, active_packages: Dict[str, str]):
        if not self.redis_client:
            return
        for pkg_name, version in active_packages.items():
            main_key = f"{self.config['redis_key_prefix']}{pkg_name}"
            try:
                self.redis_client.hset(main_key, 'active_version', version)
            except Exception as e:
                print(_('⚠️ Failed to store active version for {}: {}').format(pkg_name, e))

    def run(self, targeted_packages: List[str]=None):
        print(_('🚀 Starting omnipkg Metadata Builder v11 (Multi-Version Complete Edition)...'))
        if not self.connect_redis():
            return
        packages_to_process = []
        if targeted_packages:
            print(_('🎯 Running in targeted mode for {} package(s).').format(len(targeted_packages)))
            for pkg_spec in targeted_packages:
                parts = pkg_spec.split('==')
                if len(parts) == 2:
                    packages_to_process.append((canonicalize_name(parts[0]), parts[1]))
        else:
            print(_('🔍 No specific targets provided. Discovering all installed packages...'))
            packages_to_process = self.discover_all_packages()
        print(_('🛡️ Performing bulk security scan for active packages...'))
        active_packages = {}
        try:
            for dist in importlib.metadata.distributions():
                package_name_from_meta = dist.metadata.get('Name')
                if not package_name_from_meta:
                    continue
                active_packages[canonicalize_name(package_name_from_meta)] = dist.metadata['Version']
        except Exception as e:
            print(_('⚠️ Error preparing packages for security scan: {}').format(e))
        self._run_bulk_security_check(active_packages)
        print(_('✅ Bulk security scan complete. Found {} potential issues.').format(len(self.security_report)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self._process_package, package_name, version_str) for package_name, version_str in packages_to_process]
            processed_count = 0
            for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc='Processing packages'):
                if future.result():
                    processed_count += 1
        print(_('\n🎉 Metadata building complete! Updated {} package(s).').format(processed_count))

    def _process_package(self, package_name: str, version_str: str) -> bool:
        try:
            # FIX: Define pkg_name_lower from the function's argument.
            pkg_name_lower = package_name.lower()
            version_key = f"{self.config['redis_key_prefix']}{pkg_name_lower}:{version_str}"
            previous_data = self.redis_client.hgetall(version_key)
            metadata = self._build_comprehensive_metadata(package_name, previous_data, version_str)
            current_checksum = self._generate_checksum(metadata)
            if not self.force_refresh and previous_data and (previous_data.get('checksum') == current_checksum):
                return False
            metadata['checksum'] = current_checksum
            self._store_in_redis(package_name, version_str, metadata)
            return True
        except Exception as e:
            print(_('    ❌ ERROR processing {} v{}: {}').format(package_name, version_str, e))
            return False

    def _build_comprehensive_metadata(self, package_name: str, previous_data: Dict, specific_version: str) -> Dict:
        metadata = {'name': package_name, 'Version': specific_version, 'last_indexed': datetime.now().isoformat()}
        package_files = {'binaries': []}
        found_specific_version_dist = False
        version_path = Path(self.config['multiversion_base']) / '{}-{}'.format(package_name, specific_version)
        search_path = version_path if version_path.is_dir() else Path(self.config['site_packages_path'])
        dist = self._find_distribution_at_path(package_name, specific_version, search_path)
        if dist:
            found_specific_version_dist = True
            for k, v in dist.metadata.items():
                metadata[k] = v
            metadata['dependencies'] = [str(req) for req in dist.requires] if dist.requires else []
            if dist.files:
                package_files['binaries'] = [str(search_path / file_path) for file_path in dist.files if 'bin/' in str(file_path) and (search_path / file_path).exists()]
        if not found_specific_version_dist:
            metadata.update(self._enrich_from_site_packages(package_name, specific_version))
        if self.force_refresh or 'help_text' not in previous_data:
            if package_files.get('binaries'):
                metadata.update(self._get_help_output(package_files['binaries'][0]))
            else:
                metadata['help_text'] = 'No executable binary found.'
        else:
            metadata['help_text'] = previous_data.get('help_text', 'No help text available.')
        metadata['cli_analysis'] = self._analyze_cli(metadata.get('help_text', ''))
        metadata['security'] = self._get_security_info(package_name)
        health_check_dist = self._get_distribution(package_name)
        health_check_files = self._find_package_files(health_check_dist, package_name)
        metadata['health'] = self._perform_health_checks(package_name, health_check_files)
        return metadata

    def _find_distribution_at_path(self, package_name: str, version: str, search_path: Path) -> Optional[importlib.metadata.Distribution]:
        normalized_name = package_name.replace('-', '_')
        for dist_info in search_path.glob('{}-{}*.dist-info'.format(normalized_name, version)):
            if dist_info.is_dir():
                from importlib.metadata import PathDistribution
                dist = PathDistribution(dist_info)
                
                # --- START: THE FIX ---
                # Use .get() to safely handle missing 'Name' metadata.
                metadata_name = dist.metadata.get('Name', '') 
                
                if metadata_name.lower() == package_name.lower() and dist.metadata['Version'] == version:
                    return dist
                # --- END: THE FIX ---
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

    def _store_in_redis(self, package_name: str, version_str: str, metadata: Dict):
        pkg_name_lower = package_name.lower()
        version_key = f"{self.config['redis_key_prefix']}{pkg_name_lower}:{version_str}"
        main_key = f"{self.config['redis_key_prefix']}{pkg_name_lower}"
        master_versions_key = f"{self.config['redis_key_prefix']}versions"
        data_to_store = metadata.copy()
        for field in ['help_text', 'readme_snippet', 'license_text', 'Description']:
            if field in data_to_store and isinstance(data_to_store[field], str) and (len(data_to_store[field]) > 500):
                compressed = zlib.compress(data_to_store[field].encode('utf-8'))
                data_to_store[field] = compressed.hex()
                data_to_store[_('{}_compressed').format(field)] = 'true'
        flattened_data = self._flatten_dict(data_to_store)
        with self.redis_client.pipeline() as pipe:
            pipe.delete(version_key)
            pipe.hset(version_key, mapping=flattened_data)
            if not hasattr(self, '_cleaned_packages'):
                self._cleaned_packages = set()
            if pkg_name_lower not in self._cleaned_packages:
                for key in self.redis_client.hkeys(main_key):
                    if key.startswith('bubble_version:'):
                        pipe.hdel(main_key, key)
                self._cleaned_packages.add(pkg_name_lower)
            pipe.delete(version_key)
            pipe.hset(version_key, mapping=flattened_data)
            pipe.sadd(f"{main_key}:installed_versions", version_str)
            pipe.hset(master_versions_key, pkg_name_lower, version_str)
            pipe.hset(main_key, 'name', package_name)
            version_path = Path(self.config['multiversion_base']) / f"{package_name}-{version_str}"
            if not version_path.is_dir():
                pipe.hset(main_key, 'active_version', version_str)
            else:
                pipe.hset(main_key, f"bubble_version:{version_str}", 'true')
            pipe.sadd(f"{self.config['redis_key_prefix']}index", pkg_name_lower)
            pipe.execute()

    def _perform_health_checks(self, package_name: str, package_files: Dict) -> Dict:
        health_data = {'import_check': self._verify_installation(package_name), 'binary_checks': {Path(bin_path).name: self._check_binary_integrity(bin_path) for bin_path in package_files.get('binaries', [])}}
        oversized = [name for name, check in health_data['binary_checks'].items() if check.get('size', 0) > 10000000]
        if oversized:
            health_data['size_warnings'] = oversized
        return health_data

    def _verify_installation(self, package_name: str) -> Dict:
        script = _("import importlib.metadata; print(importlib.metadata.version('{}'))").format(package_name.replace('-', '_'))
        try:
            result = subprocess.run([self.config['python_executable'], '-c', script], capture_output=True, text=True, check=True, timeout=5)
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

    def _find_package_files(self, dist, package_name: str) -> Dict:
        files = {'binaries': []}
        if dist and dist.files:
            for file_path in dist.files:
                full_path = Path(self.config['site_packages_path']).parent / file_path
                if 'bin/' in str(file_path) and full_path.exists():
                    files['binaries'].append(str(full_path))
        if not files['binaries']:
            for bin_dir in self.config['paths_to_index']:
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
                    f.write(_('{}=={}\n').format(name, version))
            result = subprocess.run([self.config['python_executable'], '-m', 'safety', 'check', '-r', reqs_file_path, '--json'], capture_output=True, text=True, timeout=120)
            if result.stdout:
                self.security_report = json.loads(result.stdout)
        except Exception as e:
            print(_('    ⚠️ Bulk security scan failed: {}').format(e))
        finally:
            if os.path.exists(reqs_file_path):
                os.remove(reqs_file_path)

    def _get_security_info(self, package_name: str) -> Dict:
        vulnerabilities = self.security_report.get(package_name.lower(), [])
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

    def _get_distribution(self, package_name: str):
        try:
            return importlib.metadata.distribution(package_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def _enrich_from_site_packages(self, name: str, version: str=None) -> Dict:
        enriched_data = {}
        guesses = set([name, name.lower().replace('-', '_')])
        base_path = Path(self.config['site_packages_path'])
        if version:
            base_path = Path(self.config['multiversion_base']) / _('{}-{}').format(name, version)
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
    from omnipkg.core import ConfigManager
    config_manager = ConfigManager()
    config = config_manager.config
    force = '--force' in sys.argv or '-f' in sys.argv
    targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
    gatherer = omnipkgMetadataGatherer(config=config, force_refresh=force)
    gatherer.run(targeted_packages=targeted_packages)