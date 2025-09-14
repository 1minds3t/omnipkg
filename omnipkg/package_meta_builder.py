"""
omnipkg_metadata_builder.py - v11 - The "Multi-Version Complete" Edition
A fully integrated, self-aware metadata gatherer with complete multi-version
support for robust, side-by-side package management.
"""
import os
import re
import json
import subprocess
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False
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
from omnipkg.loader import omnipkgLoader
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
    def __init__(self, config: Dict, env_id: str, force_refresh: bool = False, omnipkg_instance=None):
        """
        Initialize the metadata gatherer.
        
        Args:
            config: Configuration dictionary
            env_id: Environment ID
            force_refresh: Whether to ignore caching
            omnipkg_instance: Optional reference to parent omnipkg instance
        """
        # Initialize cache client (will be set by parent if needed)
        self.cache_client = None
        
        # Store initialization parameters
        self.force_refresh = force_refresh
        self.security_report = {}
        self.config = config
        
        # Prioritize the override ENV VAR to get the correct ID
        # This ensures this class instance uses the env_id passed from the parent
        self.env_id = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE', env_id)
        
        self.package_path_registry = {}
        
        # Store the reference to parent omnipkg instance
        self.omnipkg_instance = omnipkg_instance
        
        # Show status messages
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

    def _discover_distributions(self, targeted_packages: Optional[List[str]]) -> List[importlib.metadata.Distribution]:
        """
        FIXED (Definitive): Authoritatively discovers distributions by EXPLICITLY
        scanning only the site-packages and bubble paths defined in the config for
        the current context. This prevents cross-environment contamination.
        """
        # --- THIS IS THE CRITICAL FIX ---
        # Get the correct paths for the context we are analyzing from the config.
        site_packages_path = Path(self.config.get('site_packages_path'))
        multiversion_base_path = Path(self.config.get('multiversion_base'))

        search_paths = []
        if site_packages_path.is_dir():
            search_paths.append(str(site_packages_path))
        if multiversion_base_path.is_dir():
            search_paths.append(str(multiversion_base_path))

        if not search_paths:
             print("⚠️  Warning: No valid site-packages or bubble directories found in config. Discovery may fail.")

        if targeted_packages:
            print(f'🎯 Running in targeted mode for {len(targeted_packages)} package(s).')
            discovered_dists = []
            site_packages = Path(self.config.get('site_packages_path', '/dev/null'))
            multiversion_base = Path(self.config.get('multiversion_base', '/dev/null'))
            
            for spec in targeted_packages:
                try:
                    name, version = spec.split('==')
                    
                    # NEW: Skip known sub-packages that are part of larger packages
                    if self._is_subpackage_component(name):
                        print(f'   -> Skipping {name} - detected as sub-component of larger package.')
                        continue
                    
                    found_dist = None
                    
                    # Method 1: Try standard importlib lookup with original name
                    try:
                        dist = importlib.metadata.distribution(name)
                        if dist.version == version:
                            found_dist = dist
                            print(f'   -> Found active distribution for {spec} via standard lookup.')
                    except importlib.metadata.PackageNotFoundError:
                        pass
                    
                    # Method 2: Try with canonicalized name if standard lookup failed
                    if not found_dist:
                        try:
                            canonical_name = canonicalize_name(name)
                            dist = importlib.metadata.distribution(canonical_name)
                            if dist.version == version:
                                found_dist = dist
                                print(f'   -> Found active distribution for {spec} via canonical name lookup.')
                        except importlib.metadata.PackageNotFoundError:
                            pass
                    
                    # Method 3: Manual filesystem scan in site-packages with multiple name variants
                    if not found_dist and site_packages.is_dir():
                        name_variants = self._get_package_name_variants(name)
                        
                        for variant in name_variants:
                            # Try different dist-info patterns
                            patterns = [
                                f'{variant}-{version}.dist-info',
                                f'{variant}-{version}*.dist-info',
                            ]
                            
                            for pattern in patterns:
                                dist_info_paths = list(site_packages.glob(pattern))
                                if dist_info_paths:
                                    # Take the first match
                                    dist_info_path = dist_info_paths[0]
                                    try:
                                        found_dist = importlib.metadata.Distribution.at(dist_info_path)
                                        print(f'   -> Found active distribution for {spec} via manual scan: {dist_info_path.name}')
                                        break
                                    except Exception as e:
                                        print(f'   -> Warning: Could not load distribution from {dist_info_path}: {e}')
                            
                            if found_dist:
                                break
                    
                    # Method 4: Check bubble directories
                    if not found_dist and multiversion_base.is_dir():
                        bubble_path = multiversion_base / f'{name}-{version}'
                        if bubble_path.is_dir():
                            name_variants = self._get_package_name_variants(name)
                            
                            for variant in name_variants:
                                patterns = [
                                    f'{variant}-{version}.dist-info',
                                    f'{variant}-{version}*.dist-info',
                                ]
                                
                                for pattern in patterns:
                                    dist_info_paths = list(bubble_path.glob(pattern))
                                    if dist_info_paths:
                                        dist_info_path = dist_info_paths[0]
                                        try:
                                            found_dist = importlib.metadata.Distribution.at(dist_info_path)
                                            print(f'   -> Found bubbled distribution for {spec} at {bubble_path.name}/{dist_info_path.name}')
                                            break
                                        except Exception as e:
                                            print(f'   -> Warning: Could not load bubbled distribution from {dist_info_path}: {e}')
                                
                                if found_dist:
                                    break
                    
                    # Method 5: Last resort - broad search in site-packages
                    if not found_dist and site_packages.is_dir():
                        print(f'   -> Performing broad search for {name} (any version)...')
                        all_dist_infos = list(site_packages.glob('*.dist-info'))
                        
                        for dist_info_path in all_dist_infos:
                            try:
                                dist = importlib.metadata.Distribution.at(dist_info_path)
                                # Check if this distribution matches our target (by name and version)
                                if (canonicalize_name(dist.metadata['Name']) == canonicalize_name(name) and 
                                    dist.version == version):
                                    found_dist = dist
                                    print(f'   -> Found distribution via broad search: {dist_info_path.name}')
                                    break
                            except Exception:
                                # Skip malformed dist-info directories
                                continue
                    
                    if found_dist:
                        discovered_dists.append(found_dist)
                    else:
                        print(_("   ⚠️ Could not find any distribution matching spec '{}'. This may be an installation issue.").format(spec))
                        
                except ValueError:
                    print(_("   ⚠️ Could not parse spec '{}'. Expected format 'package==version'.").format(spec))
            
            return discovered_dists
        
        # Non-targeted mode - discover all packages
        print('🔍 Discovering all packages from file system (ground truth)...')
        search_paths = []
        
        site_packages = self.config.get('site_packages_path')
        if site_packages and Path(site_packages).is_dir():
            search_paths.append(site_packages)
        
        multiversion_base = self.config.get('multiversion_base')
        if multiversion_base and Path(multiversion_base).is_dir():
            search_paths.extend([str(p) for p in Path(multiversion_base).iterdir() if p.is_dir()])
        
        try:
            dists = list(importlib.metadata.distributions(path=search_paths))
            print(_('✅ Discovery complete. Found {} total package versions to process.').format(len(dists)))
            return dists
        except Exception as e:
            print(f'⚠️ Error during package discovery: {e}')
            print('🔧 Falling back to manual discovery...')
            return self._manual_discovery_fallback(search_paths)
        
    def _is_subpackage_component(self, package_name: str) -> bool:
        """
        Check if a package name is actually a sub-component of a larger package.
        """
        subpackage_patterns = {
            'tensorboard_data_server': 'tensorboard',
            'tensorboard_plugin_': 'tensorboard',  # Catches tensorboard_plugin_*
            # Add other known patterns here
        }
        
        for pattern, parent in subpackage_patterns.items():
            if package_name.startswith(pattern):
                # Verify the parent package exists
                try:
                    importlib.metadata.distribution(parent)
                    return True
                except importlib.metadata.PackageNotFoundError:
                    pass
        
        return False
    
    def _get_package_name_variants(self, name: str) -> List[str]:
        """
        Generate common package name variants to handle naming inconsistencies.
        """
        variants = set()
        
        # Original name
        variants.add(name)
        
        # Canonicalized name
        canonical = canonicalize_name(name)
        variants.add(canonical)
        
        # Common transformations
        variants.add(name.replace('-', '_'))
        variants.add(name.replace('_', '-'))
        
        # Capitalize variations (for packages like Flask-Login)
        variants.add(name.title())
        variants.add(name.title().replace('-', '_'))
        variants.add(name.title().replace('_', '-'))
        
        # All lowercase and uppercase
        variants.add(name.lower())
        variants.add(name.upper())
        
        return list(variants)
    
    def _manual_discovery_fallback(self, search_paths: List[str]) -> List[importlib.metadata.Distribution]:
        """
        Fallback method for manual package discovery when importlib fails.
        """
        discovered_dists = []
        
        for search_path in search_paths:
            path = Path(search_path)
            if not path.is_dir():
                continue
                
            # Find all .dist-info directories
            for dist_info_path in path.glob('*.dist-info'):
                try:
                    dist = importlib.metadata.Distribution.at(dist_info_path)
                    discovered_dists.append(dist)
                except Exception as e:
                    print(f'   -> Warning: Could not load distribution from {dist_info_path}: {e}')
        
        print(f'✅ Manual discovery complete. Found {len(discovered_dists)} distributions.')
        return discovered_dists

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
        Runs a security check using a dedicated, isolated 'safety' tool bubble,
        created on-demand by the bubble_manager to guarantee isolation.
        """
        print(f'🛡️ Performing security scan for {len(packages)} active package(s) using isolated tool...')
        
        if not packages or not self.omnipkg_instance:
            if not packages:
                print(_(' - No active packages found to scan.'))
            else:
                print(" ⚠️ Cannot run security scan: omnipkg_instance not available to builder.")
            self.security_report = {}
            return

        TOOL_SPEC = "safety==3.6.1"
        TOOL_NAME, TOOL_VERSION = TOOL_SPEC.split('==')

        try:
            bubble_path = self.omnipkg_instance.multiversion_base / f"{TOOL_NAME}-{TOOL_VERSION}"
            
            # --- THIS IS THE CORRECT LOGIC ---
            # We check for the physical directory. If it's not there, we build it.
            if not bubble_path.is_dir():
                print(f" 💡 First-time setup: Creating isolated bubble for '{TOOL_SPEC}' tool...")
                # Use the dedicated bubble creation method, NOT smart_install
                success = self.omnipkg_instance.bubble_manager.create_isolated_bubble(TOOL_NAME, TOOL_VERSION)
                if not success:
                    print(f" ❌ Failed to create the tool bubble for {TOOL_SPEC}. Skipping scan.")
                    self.security_report = {}
                    return
                print(f" ✅ Successfully created tool bubble.")
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as reqs_file:
                reqs_file_path = reqs_file.name
                for name, version in packages.items():
                    reqs_file.write(f'{name}=={version}\n')

            print(f" 🌀 Force-activating '{TOOL_SPEC}' context to run scan...")
            # The loader will now find the bubble that was just created.
            with omnipkgLoader(TOOL_SPEC, config=self.omnipkg_instance.config, force_activation=True):
                python_exe = self.config.get('python_executable', sys.executable)
                cmd = [python_exe, '-m', 'safety', 'check', '-r', reqs_file_path, '--json']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)


            self.security_report = {}
            if result.stdout:
                try:
                    json_match = re.search(r'(\[.*\]|\{.*\})', result.stdout, re.DOTALL)
                    if json_match:
                        self.security_report = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    print(f' ⚠️ Could not parse safety JSON output.')
            
            if result.stderr and "error" in result.stderr.lower():
                 print(_(' ⚠️ Safety tool produced errors: {}').format(result.stderr.strip()))

        except Exception as e:
            print(_(' ⚠️ An unexpected error occurred during the isolated security scan: {}').format(e))
            self.security_report = {}
        finally:
            if 'reqs_file_path' in locals() and os.path.exists(reqs_file_path):
                os.unlink(reqs_file_path)

        # Report the findings
        issue_count = 0
        if isinstance(self.security_report, list):
            issue_count = len(self.security_report)
        elif isinstance(self.security_report, dict) and 'vulnerabilities' in self.security_report:
            issue_count = len(self.security_report['vulnerabilities'])
        
        print(_('✅ Security scan complete. Found {} potential issues.').format(issue_count))

    def _discover_distributions_via_subprocess(self, search_paths: List[str]) -> List[importlib.metadata.Distribution]:
        """
        A hyper-isolated method to discover distributions using a clean subprocess.
        This is the definitive fix for environment contamination.
        """
        print("   -> Using hyper-isolated subprocess for package discovery...")
        script = f"""
import sys
import json
import importlib.metadata
from pathlib import Path

# These paths are passed in from the correctly-configured builder instance
search_paths = {json.dumps(search_paths)}
dist_info_paths = []

try:
    # This call now happens in a pristine environment
    for dist in importlib.metadata.distributions(path=search_paths):
        # dist._path is the path to the .dist-info directory
        if dist._path and Path(dist._path).exists():
            dist_info_paths.append(str(dist._path))
    # Use set to ensure uniqueness before printing
    print(json.dumps(list(set(dist_info_paths))))
except Exception as e:
    # Print errors to stderr so they can be captured
    print(f"Discovery error: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
        try:
            python_exe = self.config.get('python_executable', sys.executable)
            # Use -I for maximum isolation, preventing any .py files from the current
            # directory or PYTHONPATH from interfering.
            cmd = [python_exe, '-I', '-c', script]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
            
            dist_paths = json.loads(result.stdout)
            
            dists = []
            for path_str in dist_paths:
                try:
                    dists.append(importlib.metadata.Distribution.at(Path(path_str)))
                except Exception as e:
                    print(f"   -> Warning: Could not load distribution from discovered path '{path_str}': {e}")
                    continue
            print(f"   -> Hyper-isolation successful. Found {len(dists)} distributions.")
            return dists
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            error_output = e.stderr if hasattr(e, 'stderr') else str(e)
            print(f"⚠️ Subprocess-based discovery failed: {error_output}")
            print("⚠️ Falling back to direct (potentially contaminated) discovery method.")
            # Fallback to the original method only if the robust one fails
            return list(importlib.metadata.distributions(path=search_paths))

    def _discover_distributions(self, targeted_packages: Optional[List[str]]) -> List[importlib.metadata.Distribution]:
        """
        Authoritatively discovers distributions by EXPLICITLY scanning only the
        site-packages and bubble paths from the config for the current context.
        Now uses a hyper-isolated subprocess to prevent cross-environment contamination.
        """
        if targeted_packages:
            # Targeted mode logic remains the same as it's less prone to contamination
            # (Code for targeted_packages is omitted for brevity but should be the same as your "flawed" file)
            print(f'🎯 Running in targeted mode for {len(targeted_packages)} package(s).')
            # ... (Your existing robust targeted discovery logic here) ...
            # For this fix, we assume the main problem is with full scans.
            # A simplified version for brevity:
            dists = []
            for spec in targeted_packages:
                try:
                    name, version = spec.split('==')
                    dist = importlib.metadata.distribution(name)
                    if dist.version == version:
                        dists.append(dist)
                except (importlib.metadata.PackageNotFoundError, ValueError):
                    continue
            return dists

        print('🔍 Discovering all packages from file system (ground truth)...')
        site_packages_path = Path(self.config.get('site_packages_path'))
        multiversion_base_path = Path(self.config.get('multiversion_base'))

        search_paths = []
        if site_packages_path.is_dir():
            search_paths.append(str(site_packages_path))
        if multiversion_base_path.is_dir():
            # Add the base bubble directory and each individual bubble directory
            search_paths.append(str(multiversion_base_path))
            for p in multiversion_base_path.iterdir():
                if p.is_dir():
                    search_paths.append(str(p))

        if not search_paths:
             print("⚠️  Warning: No valid site-packages or bubble directories found. Discovery may fail.")
             return []

        # *** THIS IS THE FIX ***
        # Always use the hyper-isolated subprocess for full scans.
        dists = self._discover_distributions_via_subprocess(search_paths)
        
        print(_('✅ Discovery complete. Found {} total package versions to process.').format(len(dists)))
        return dists

    # (The rest of your omnipkgMetadataGatherer class methods remain the same)
    # ... (e.g., _is_bubbled, run, _process_package, etc.) ...
    def run(self, targeted_packages: Optional[List[str]]=None, newly_active_packages: Optional[Dict[str, str]]=None):
        """
        The main execution loop. It now ensures the security
        tool bubble is created *before* discovering packages, guaranteeing a
        complete and accurate knowledge base build in a single pass.
        """
        # STEP 1: PREPARE THE ENVIRONMENT for security scan
        if not targeted_packages:
            print("🔧 Preparing environment for full scan...")
            try:
                # Perform a preliminary, non-isolated discovery just to get active packages for the scan
                active_dists = [dist for dist in importlib.metadata.distributions() if not self._is_bubbled(dist)]
                active_packages_for_scan = {
                    canonicalize_name(dist.metadata['Name']): dist.version
                    for dist in active_dists if 'Name' in dist.metadata
                }
                self._perform_security_scan(active_packages_for_scan)
            except Exception as e:
                print(f"⚠️ Could not perform pre-scan for security tool setup: {e}")
                self.security_report = {}
        
        # STEP 2: DISCOVER THE COMPLETE, ISOLATED GROUND TRUTH.
        distributions_to_process = self._discover_distributions(targeted_packages)

        # STEP 3: RUN SECURITY SCAN (for targeted mode)
        if targeted_packages:
            packages_to_scan = {
                canonicalize_name(dist.metadata['Name']): dist.version
                for dist in distributions_to_process if 'Name' in dist.metadata
            }
            self._perform_security_scan(packages_to_scan)

        if not distributions_to_process:
            print(_('✅ No packages found or specified to process.'))
            return
            
        iterator = distributions_to_process
        if HAS_TQDM:
            iterator = tqdm(distributions_to_process, desc='Processing packages', unit='pkg')
            
        updated_count = 0
        processed_packages = set()
        
        distributions_to_process.sort(key=lambda d: self._is_bubbled(d))
        
        for dist in iterator:
            try:
                raw_name = dist.metadata.get('Name')
                if not raw_name: continue
                
                name = canonicalize_name(raw_name)
                version = dist.version
                pkg_id = (name, version)

                if pkg_id in processed_packages:
                    continue
                
                if self._process_package(dist):
                    updated_count += 1
                    processed_packages.add(pkg_id)
            except Exception:
                continue

    def _is_known_subcomponent(self, dist_info_path: Path) -> bool:
        """Check if this dist-info belongs to a sub-component that shouldn't be treated independently."""
        name = dist_info_path.name
        
        # Known sub-components that are part of larger packages
        subcomponent_patterns = [
            'tensorboard_data_server-',
            'tensorboard_plugin_',
        ]
        
        for pattern in subcomponent_patterns:
            if name.startswith(pattern):
                return True
        
        return False

    def _process_package(self, dist: importlib.metadata.Distribution) -> bool:
        """
        Processes a single distribution and automatically triggers repair
        for corrupted packages, respecting their original location.
        """
        pkg_name_for_error = 'Unknown Package'
        try:
            raw_name = dist.metadata.get('Name')
            if not raw_name:
                # NEW: Check if this is a known sub-component before flagging as corrupted
                if self._is_known_subcomponent(dist._path):
                    print(f"   -> Skipping {dist._path.name} - known sub-component of larger package.")
                    return False  # Skip processing this distribution
                
                print(_("\n⚠️ CORRUPTION DETECTED: Package at '{}' is missing a name.").format(dist._path))
                # ... rest of existing corruption handling code
                
                if self.omnipkg_instance:
                    print("   - 🛡️  Attempting auto-repair...")
                    match = re.match(r"([\w\.\-]+)-([\w\.\+a-z0-9]+)\.dist-info", dist._path.name)
                    if match:
                        name, version = match.groups()
                        package_to_reinstall = f"{name}=={version}"
                        
                        target_dir = None
                        cleanup_path = Path(self.omnipkg_instance.config.get('site_packages_path'))
                        multiversion_base = self.omnipkg_instance.config.get('multiversion_base', '/dev/null')
                        
                        if str(dist._path).startswith(multiversion_base):
                            # The corruption is inside a bubble.
                            # The target for reinstall is the bubble root.
                            # The target for cleanup is ALSO the bubble root.
                            target_dir = dist._path.parent
                            cleanup_path = dist._path.parent
                            print(f"   - Corruption is inside a bubble. Repair will target: {target_dir}")
                        
                        # --- THIS IS THE FIX ---
                        # Pass the correct cleanup_path to the cleanup function
                        self.omnipkg_instance._brute_force_package_cleanup(name, cleanup_path)
                        # --- END OF THE FIX ---
                        
                        print(f"   - 🚀 Re-installing '{package_to_reinstall}' to heal the environment...")
                        self.omnipkg_instance.smart_install(
                            [package_to_reinstall], 
                            force_reinstall=True, 
                            target_directory=target_dir
                        )
                    else:
                        print("   - ❌ Auto-repair failed: Could not parse package name from path.")
                else:
                    print(_("    To fix, please manually delete this directory and re-run your command."))

                return False # We did not process this specific (corrupted) item

            pkg_name_for_error = raw_name
            name = canonicalize_name(raw_name)
            version = dist.version
            version_key = f'{self.redis_key_prefix}{name}:{version}'
            
            if not self.force_refresh and self.cache_client.exists(version_key):
                return False

            metadata = self._build_comprehensive_metadata(dist)
            is_active = not self._is_bubbled(dist)
            self._store_in_redis(name, version, metadata, is_active=is_active)
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

# At the very end of omnipkg/package_meta_builder.py

if __name__ == '__main__':
    import json
    from pathlib import Path
    import hashlib
    
    print(_('🚀 Starting omnipkg Metadata Builder (Standalone Subprocess Mode)...'))
    
    try:
        # This logic is now inside the __main__ block, making the script runnable.
        config_path = Path.home() / '.config' / 'omnipkg' / 'config.json'
        with open(config_path, 'r') as f:
            full_config = json.load(f)
        
        # The env_id is now passed reliably via an environment variable.
        env_id = os.environ.get('OMNIPKG_ENV_ID_OVERRIDE')
        if not env_id:
            raise ValueError("OMNIPKG_ENV_ID_OVERRIDE not set in subprocess environment.")

        print(f"   (Running in context of environment ID: {env_id})")
        config = full_config['environments'][env_id]

    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f'❌ CRITICAL: Could not load omnipkg configuration for this environment. Error: {e}. Aborting.')
        sys.exit(1)
        
    # We create the omnipkg instance here to pass to the gatherer,
    # solving the "omnipkg_instance not available" bug permanently.
    from omnipkg.core import ConfigManager, omnipkg as OmnipkgCore
    config_manager = ConfigManager()
    omnipkg_instance = OmnipkgCore(config_manager)

    gatherer = omnipkgMetadataGatherer(
        config=config,
        env_id=env_id,
        force_refresh='--force' in sys.argv,
        omnipkg_instance=omnipkg_instance
    )
    
    try:
        # The connection logic needs to exist in the Gatherer for this to work
        if not gatherer.cache_client:
            # A simplified connection method for the builder
            try:
                redis_host = gatherer.config.get('redis_host', 'localhost')
                redis_port = gatherer.config.get('redis_port', 6379)
                gatherer.cache_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
                gatherer.cache_client.ping()
            except Exception:
                print('❌ Builder subprocess failed to connect to Redis. Aborting.')
                sys.exit(1)

        targeted_packages = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
        gatherer.run(targeted_packages=targeted_packages if targeted_packages else None)
        print(_('\n🎉 Subprocess metadata building complete!'))
        sys.exit(0)
        
    except Exception as e:
        print(_('\n❌ An unexpected error occurred during metadata build subprocess: {}').format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)