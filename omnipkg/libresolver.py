from __future__ import annotations

from .common_utils import safe_print
#!/usr/bin/env python3
"""
System Library Version Swapper for OmniPkg
============================================

This module handles dynamic swapping of system libraries (glibc, libssl, zlib, etc.)
to test compatibility across different versions without touching the base OS.

Key Features:
- Downloads/builds multiple versions of system libraries
- Creates isolated runtime environments with custom library paths
- Tests compatibility matrices for any combination of syslibs
- Builds authoritative compatibility database
- Works via LD_PRELOAD, LD_LIBRARY_PATH, and namespace isolation
"""

import os
import sys
import subprocess
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager
import requests
import tarfile
import time


@dataclass
class SysLibVersion:
    name: str
    version: str
    url: str
    build_config: Dict[str, Any]
    abi_hash: str
    install_path: Path
    
    def to_dict(self):
        return {**asdict(self), 'install_path': str(self.install_path)}


class SysLibSwapper:
    """Manages multiple versions of system libraries for compatibility testing."""
    
    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or Path("/opt/omnilibs")
        self.store_dir = self.base_dir / "store"
        self.build_dir = self.base_dir / "build"
        self.cache_dir = self.base_dir / "cache"
        self.compat_db = self.base_dir / "compatibility.json"
        
        # Create directories
        for d in [self.store_dir, self.build_dir, self.cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        # Load existing compatibility data
        self.compatibility_matrix = self._load_compatibility_matrix()
        
        # Define library configurations
        self.lib_configs = {
            "glibc": {
                "versions": ["2.28", "2.31", "2.34", "2.35", "2.36", "2.37", "2.38"],
                "url_template": "https://ftp.gnu.org/gnu/glibc/glibc-{version}.tar.gz",
                "build_script": self._build_glibc,
                "test_script": self._test_glibc
            },
            "openssl": {
                "versions": ["1.0.2u", "1.1.1l", "1.1.1w", "3.0.8", "3.0.12", "3.1.4"],
                "url_template": "https://www.openssl.org/source/openssl-{version}.tar.gz",
                "build_script": self._build_openssl,
                "test_script": self._test_openssl
            },
            "zlib": {
                "versions": ["1.2.8", "1.2.11", "1.2.12", "1.2.13", "1.3"],
                "url_template": "https://zlib.net/fossils/zlib-{version}.tar.gz",
                "build_script": self._build_zlib,
                "test_script": self._test_zlib
            },
            "libpng": {
                "versions": ["1.6.34", "1.6.37", "1.6.39", "1.6.40"],
                "url_template": "https://download.sourceforge.net/libpng/libpng-{version}.tar.gz",
                "build_script": self._build_libpng,
                "test_script": self._test_libpng
            }
        }

    def _load_compatibility_matrix(self) -> Dict:
        """Load existing compatibility test results."""
        if self.compat_db.exists():
            try:
                with open(self.compat_db) as f:
                    return json.load(f)
            except Exception as e:
                safe_print(f"⚠️  Error loading compatibility DB: {e}")
        return {"tested_combinations": {}, "known_working": {}, "known_broken": {}}

    def _save_compatibility_matrix(self):
        """Save compatibility test results."""
        with open(self.compat_db, 'w') as f:
            json.dump(self.compatibility_matrix, f, indent=2)

    def _compute_abi_hash(self, lib_path: Path) -> str:
        """Compute ABI hash for a built library."""
        # Use objdump or readelf to extract ABI info, then hash it
        try:
            result = subprocess.run(
                ["objdump", "-T", str(lib_path)], 
                capture_output=True, text=True, check=True
            )
            abi_info = result.stdout
            return hashlib.sha256(abi_info.encode()).hexdigest()[:16]
        except subprocess.CalledProcessError:
            # Fallback to file hash
            return hashlib.sha256(lib_path.read_bytes()).hexdigest()[:16]

    def ensure_library_version(self, lib_name: str, version: str) -> Optional[SysLibVersion]:
        """Ensure a specific version of a system library is available."""
        safe_print(f"\n🔧 Ensuring {lib_name} {version} is available...")
        
        # Check if already built
        expected_path = self.store_dir / f"{lib_name}-{version}"
        if expected_path.exists():
            safe_print(f"✅ {lib_name} {version} already available")
            return self._load_syslib_metadata(expected_path)
        
        # Need to build it
        config = self.lib_configs.get(lib_name)
        if not config:
            safe_print(f"❌ Unknown library: {lib_name}")
            return None
        
        if version not in config["versions"]:
            safe_print(f"❌ Unsupported version {version} for {lib_name}")
            return None
        
        return self._build_library(lib_name, version, config)

    def _build_library(self, lib_name: str, version: str, config: Dict) -> Optional[SysLibVersion]:
        """Download and build a specific library version."""
        safe_print(f"📦 Building {lib_name} {version}...")
        
        # Download source
        url = config["url_template"].format(version=version)
        source_path = self._download_source(url, lib_name, version)
        if not source_path:
            return None
        
        # Build
        install_path = self.store_dir / f"{lib_name}-{version}"
        build_script = config["build_script"]
        
        if not build_script(source_path, install_path, version):
            safe_print(f"❌ Failed to build {lib_name} {version}")
            return None
        
        # Compute ABI hash
        main_lib = self._find_main_library(install_path, lib_name)
        abi_hash = self._compute_abi_hash(main_lib) if main_lib else "unknown"
        
        # Create metadata
        syslib = SysLibVersion(
            name=lib_name,
            version=version,
            url=url,
            build_config=config.get("build_config", {}),
            abi_hash=abi_hash,
            install_path=install_path
        )
        
        # Save metadata
        metadata_file = install_path / "omnipkg_metadata.json"
        with open(metadata_file, 'w') as f:
            json.dump(syslib.to_dict(), f, indent=2)
        
        safe_print(f"✅ Built {lib_name} {version} with ABI hash {abi_hash}")
        return syslib

    def _download_source(self, url: str, lib_name: str, version: str) -> Optional[Path]:
        """Download and extract source code."""
        cache_file = self.cache_dir / f"{lib_name}-{version}.tar.gz"
        
        if not cache_file.exists():
            safe_print(f"⬇️  Downloading {url}...")
            try:
                response = requests.get(url, stream=True, timeout=60)
                response.raise_for_status()
                with open(cache_file, 'wb') as f:
                    shutil.copyfileobj(response.raw, f)
            except Exception as e:
                safe_print(f"❌ Download failed: {e}")
                return None
        
        # Extract
        extract_dir = self.build_dir / f"{lib_name}-{version}-src"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        
        try:
            with tarfile.open(cache_file) as tar:
                tar.extractall(self.build_dir)
            
            # Find the actual source directory (might be nested)
            candidates = list(self.build_dir.glob(f"{lib_name}-{version}*"))
            if candidates:
                actual_src = candidates[0]
                if actual_src != extract_dir:
                    actual_src.rename(extract_dir)
                return extract_dir
        except Exception as e:
            safe_print(f"❌ Extraction failed: {e}")
        
        return None

    # Build scripts for each library type
    def _build_glibc(self, source_path: Path, install_path: Path, version: str) -> bool:
        """Build glibc with custom prefix."""
        build_dir = source_path / "build"
        build_dir.mkdir(exist_ok=True)
        
        try:
            # Configure
            subprocess.run([
                str(source_path / "configure"),
                f"--prefix={install_path}",
                "--disable-werror",
                "--enable-shared"
            ], cwd=build_dir, check=True, capture_output=True)
            
            # Build
            subprocess.run(["make", "-j4"], cwd=build_dir, check=True, capture_output=True)
            subprocess.run(["make", "install"], cwd=build_dir, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError as e:
            safe_print(f"Build error: {e.stderr.decode()}")
            return False

    def _build_openssl(self, source_path: Path, install_path: Path, version: str) -> bool:
        """Build OpenSSL with custom prefix."""
        try:
            # Configure
            subprocess.run([
                "./config",
                f"--prefix={install_path}",
                "--openssldir={}/ssl".format(install_path),
                "shared"
            ], cwd=source_path, check=True, capture_output=True)
            
            # Build
            subprocess.run(["make", "-j4"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(["make", "install"], cwd=source_path, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError:
            return False

    def _build_zlib(self, source_path: Path, install_path: Path, version: str) -> bool:
        """Build zlib with custom prefix."""
        try:
            subprocess.run([
                "./configure", f"--prefix={install_path}"
            ], cwd=source_path, check=True, capture_output=True)
            
            subprocess.run(["make", "-j4"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(["make", "install"], cwd=source_path, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError:
            return False

    def _build_libpng(self, source_path: Path, install_path: Path, version: str) -> bool:
        """Build libpng with custom prefix."""
        try:
            subprocess.run([
                "./configure", 
                f"--prefix={install_path}",
                "--enable-shared"
            ], cwd=source_path, check=True, capture_output=True)
            
            subprocess.run(["make", "-j4"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(["make", "install"], cwd=source_path, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError:
            return False

    @contextmanager
    def runtime_environment(self, syslib_versions: Dict[str, str]):
        """
        Context manager that creates an isolated runtime environment
        with specific system library versions.
        """
        safe_print(f"\n🌍 Creating runtime environment with: {syslib_versions}")
        
        # Ensure all requested libraries are available
        syslibs = {}
        for lib_name, version in syslib_versions.items():
            syslib = self.ensure_library_version(lib_name, version)
            if not syslib:
                raise RuntimeError(f"Failed to ensure {lib_name} {version}")
            syslibs[lib_name] = syslib
        
        # Build environment variables
        original_env = dict(os.environ)
        lib_paths = []
        preload_libs = []
        
        for lib_name, syslib in syslibs.items():
            lib_dir = syslib.install_path / "lib"
            if lib_dir.exists():
                lib_paths.append(str(lib_dir))
                
                # For critical libraries, use LD_PRELOAD for absolute control
                if lib_name in ["glibc", "openssl"]:
                    main_lib = self._find_main_library(syslib.install_path, lib_name)
                    if main_lib:
                        preload_libs.append(str(main_lib))
        
        # Set up environment
        new_env = original_env.copy()
        if lib_paths:
            existing_path = new_env.get("LD_LIBRARY_PATH", "")
            new_path = ":".join(lib_paths + [existing_path] if existing_path else lib_paths)
            new_env["LD_LIBRARY_PATH"] = new_path
        
        if preload_libs:
            existing_preload = new_env.get("LD_PRELOAD", "")
            new_preload = ":".join(preload_libs + [existing_preload] if existing_preload else preload_libs)
            new_env["LD_PRELOAD"] = new_preload
        
        try:
            # Apply environment
            os.environ.update(new_env)
            safe_print(f"✅ Runtime environment active")
            yield syslibs
        finally:
            # Restore original environment
            os.environ.clear()
            os.environ.update(original_env)
            safe_print("🔄 Runtime environment restored")

    def test_compatibility(self, package_name: str, package_version: str, 
                          syslib_versions: Dict[str, str]) -> Dict[str, Any]:
        """
        Test if a Python package works with specific system library versions.
        Returns detailed compatibility results.
        """
        test_id = f"{package_name}-{package_version}-" + "-".join(f"{k}_{v}" for k, v in syslib_versions.items())
        
        # Check cache first
        if test_id in self.compatibility_matrix["tested_combinations"]:
            safe_print(f"💾 Using cached result for {test_id}")
            return self.compatibility_matrix["tested_combinations"][test_id]
        
        safe_print(f"\n🧪 Testing compatibility: {package_name}=={package_version}")
        safe_print(f"    System libs: {syslib_versions}")
        
        result = {
            "package": package_name,
            "package_version": package_version,
            "syslib_versions": syslib_versions,
            "timestamp": time.time(),
            "success": False,
            "import_success": False,
            "basic_functionality": False,
            "errors": [],
            "warnings": [],
            "performance_metrics": {}
        }
        
        try:
            with self.runtime_environment(syslib_versions):
                # Test in isolated subprocess to avoid contaminating current process
                test_result = self._run_isolated_test(package_name, package_version)
                result.update(test_result)
                
        except Exception as e:
            result["errors"].append(f"Environment setup failed: {str(e)}")
        
        # Cache the result
        self.compatibility_matrix["tested_combinations"][test_id] = result
        
        # Update known working/broken lists
        if result["success"]:
            working_key = f"{package_name}-{package_version}"
            if working_key not in self.compatibility_matrix["known_working"]:
                self.compatibility_matrix["known_working"][working_key] = []
            self.compatibility_matrix["known_working"][working_key].append(syslib_versions)
        else:
            broken_key = f"{package_name}-{package_version}"
            if broken_key not in self.compatibility_matrix["known_broken"]:
                self.compatibility_matrix["known_broken"][broken_key] = []
            self.compatibility_matrix["known_broken"][broken_key].append({
                "syslib_versions": syslib_versions,
                "errors": result["errors"]
            })
        
        self._save_compatibility_matrix()
        return result

    def _run_isolated_test(self, package_name: str, package_version: str) -> Dict[str, Any]:
        """Run compatibility test in completely isolated subprocess."""
        test_script = f'''
import sys
import time
import traceback
import importlib
try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
result = {{
    "success": False,
    "import_success": False,
    "basic_functionality": False,
    "errors": [],
    "warnings": [],
    "performance_metrics": {{}}
}}

try:
    # Test 1: Can we import the package?
    start_time = time.time()
    module = importlib.import_module("{package_name}")
    import_time = time.time() - start_time
    
    result["import_success"] = True
    result["performance_metrics"]["import_time"] = import_time
    
    # Test 2: Basic functionality test
    if hasattr(module, '__version__'):
        if module.__version__ == "{package_version}":
            result["basic_functionality"] = True
        else:
            result["warnings"].append(f"Version mismatch: expected {package_version}, got {{module.__version__}}")
    
    # Test 3: Try some basic operations (library-specific)
    if "{package_name}" == "numpy":
        import numpy as np
        test_array = np.array([1, 2, 3])
        _ = np.sum(test_array)
    elif "{package_name}" == "requests":
        # Just test that SSL context can be created
        import ssl
        ssl.create_default_context()
    elif "{package_name}" == "Pillow":
        from PIL import Image
        # Test basic image creation
        img = Image.new('RGB', (100, 100), color='red')
    
    result["success"] = True
    
except ImportError as e:
    result["errors"].append(f"Import error: {{str(e)}}")
except Exception as e:
    result["errors"].append(f"Runtime error: {{str(e)}}")
    result["errors"].append(traceback.format_exc())

safe_print("OMNIPKG_TEST_RESULT:" + str(result))
'''
        
        try:
            # Install package in temp location first
            temp_dir = tempfile.mkdtemp(prefix="omnipkg-test-")
            subprocess.run([
                sys.executable, "-m", "pip", "install", 
                f"{package_name}=={package_version}",
                "--target", temp_dir
            ], check=True, capture_output=True)
            
            # Run test with temp location in PYTHONPATH
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{temp_dir}:{env.get('PYTHONPATH', '')}"
            
            proc = subprocess.run(
                [sys.executable, "-c", test_script],
                capture_output=True, text=True, env=env, timeout=30
            )
            
            # Parse result
            for line in proc.stdout.splitlines():
                if line.startswith("OMNIPKG_TEST_RESULT:"):
                    return eval(line.split(":", 1)[1])
            
            return {
                "success": False,
                "errors": [f"Test script failed: {proc.stderr}"],
                "import_success": False,
                "basic_functionality": False,
                "warnings": [],
                "performance_metrics": {}
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "errors": ["Test timed out"],
                "import_success": False,
                "basic_functionality": False,
                "warnings": [],
                "performance_metrics": {}
            }
        except Exception as e:
            return {
                "success": False,
                "errors": [f"Test execution failed: {str(e)}"],
                "import_success": False,
                "basic_functionality": False,
                "warnings": [],
                "performance_metrics": {}
            }
        finally:
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def bruteforce_compatibility_matrix(self, packages: List[Tuple[str, str]], 
                                      syslib_combos: List[Dict[str, str]] = None):
        """
        Brute force test all combinations of packages vs system library versions.
        This builds the authoritative compatibility database.
        """
        if syslib_combos is None:
            # Generate common combinations
            syslib_combos = [
                {"openssl": "1.1.1l", "zlib": "1.2.11"},
                {"openssl": "3.0.8", "zlib": "1.2.13"},
                {"openssl": "1.0.2u", "zlib": "1.2.8"},  # legacy combo
                {"glibc": "2.31", "openssl": "1.1.1l", "zlib": "1.2.11"},
                {"glibc": "2.38", "openssl": "3.0.12", "zlib": "1.3"},
            ]
        
        total_tests = len(packages) * len(syslib_combos)
        current_test = 0
        
        safe_print(f"\n🔥 BRUTE FORCE COMPATIBILITY TESTING")
        safe_print(f"    Packages: {len(packages)}")
        safe_print(f"    Syslib combinations: {len(syslib_combos)}")
        safe_print(f"    Total tests: {total_tests}")
        safe_print(f"    Estimated time: {total_tests * 45} seconds")
        
        for pkg_name, pkg_version in packages:
            for syslib_combo in syslib_combos:
                current_test += 1
                safe_print(f"\n[{current_test}/{total_tests}] Testing {pkg_name}=={pkg_version}")
                
                result = self.test_compatibility(pkg_name, pkg_version, syslib_combo)
                status = "✅ PASS" if result["success"] else "❌ FAIL"
                safe_print(f"    {status}")
                
                if result["errors"]:
                    safe_print(f"    Errors: {len(result['errors'])}")

    def find_working_combination(self, package_name: str, package_version: str) -> Optional[Dict[str, str]]:
        """Find a known working system library combination for a package."""
        working_key = f"{package_name}-{package_version}"
        
        if working_key in self.compatibility_matrix["known_working"]:
            combinations = self.compatibility_matrix["known_working"][working_key]
            if combinations:
                return combinations[0]  # Return first known working combo
        
        return None

    def heal_runtime_error(self, package_name: str, error_msg: str) -> Optional[Dict[str, str]]:
        """
        Analyze a runtime error and suggest system library versions that might fix it.
        This is the core of the "runtime healer" functionality.
        """
        safe_print(f"\n🔧 HEALING: {package_name} failed with: {error_msg}")
        
        # Error pattern matching
        if "SSL" in error_msg or "ssl" in error_msg:
            safe_print("    🔍 SSL-related error detected")
            # Try different OpenSSL versions
            for ssl_version in ["1.1.1l", "3.0.8", "1.0.2u"]:
                combo = {"openssl": ssl_version, "zlib": "1.2.11"}
                if self._quick_test_combo(package_name, combo, error_msg):
                    return combo
        
        elif "zlib" in error_msg or "compression" in error_msg:
            safe_print("    🔍 Compression-related error detected")
            for zlib_version in ["1.2.11", "1.2.13", "1.3"]:
                combo = {"zlib": zlib_version}
                if self._quick_test_combo(package_name, combo, error_msg):
                    return combo
        
        elif "GLIBC" in error_msg or "libc" in error_msg:
            safe_print("    🔍 GLIBC-related error detected")
            for glibc_version in ["2.31", "2.34", "2.38"]:
                combo = {"glibc": glibc_version}
                if self._quick_test_combo(package_name, combo, error_msg):
                    return combo
        
        safe_print("    ❌ No automatic fix found")
        return None

    def _quick_test_combo(self, package_name: str, syslib_combo: Dict[str, str], 
                         original_error: str) -> bool:
        """Quick test to see if a syslib combination fixes an error."""
        try:
            with self.runtime_environment(syslib_combo):
                # Try to import the problematic package
                proc = subprocess.run([
                    sys.executable, "-c", f"import {package_name}; safe_print('SUCCESS')"
                ], capture_output=True, text=True, timeout=10)
                
                return proc.returncode == 0 and "SUCCESS" in proc.stdout
        except Exception:
            return False

    def _find_main_library(self, install_path: Path, lib_name: str) -> Optional[Path]:
        """Find the main shared library file."""
        lib_dir = install_path / "lib"
        if not lib_dir.exists():
            return None
        
        # Common patterns for main library files
        patterns = {
            "glibc": ["libc.so.*", "libc-*.so"],
            "openssl": ["libssl.so.*", "libcrypto.so.*"],
            "zlib": ["libz.so.*"],
            "libpng": ["libpng*.so.*"]
        }
        
        for pattern in patterns.get(lib_name, [f"lib{lib_name}.so.*"]):
            matches = list(lib_dir.glob(pattern))
            if matches:
                return matches[0]
        
        return None

    def _load_syslib_metadata(self, install_path: Path) -> Optional[SysLibVersion]:
        """Load metadata for an already-built library."""
        metadata_file = install_path / "omnipkg_metadata.json"
        if not metadata_file.exists():
            return None
        
        try:
            with open(metadata_file) as f:
                data = json.load(f)
            return SysLibVersion(**{**data, 'install_path': Path(data['install_path'])})
        except Exception:
            return None

    def get_compatibility_report(self, package_name: str = None) -> str:
        """Generate a human-readable compatibility report."""
        report = []
        report.append("🔍 OMNIPKG COMPATIBILITY MATRIX REPORT")
        report.append("=" * 50)
        
        total_tests = len(self.compatibility_matrix["tested_combinations"])
        working_tests = sum(1 for r in self.compatibility_matrix["tested_combinations"].values() if r["success"])
        
        report.append(f"\nOverall Statistics:")
        report.append(f"  Total combinations tested: {total_tests}")
        report.append(f"  Working combinations: {working_tests}")
        report.append(f"  Success rate: {working_tests/total_tests*100:.1f}%" if total_tests > 0 else "  Success rate: N/A")
        
        if package_name:
            # Package-specific report
            report.append(f"\n📦 Report for {package_name}:")
            
            working_key = f"{package_name}"
            for test_id, test_result in self.compatibility_matrix["tested_combinations"].items():
                if test_result["package"] == package_name:
                    status = "✅" if test_result["success"] else "❌"
                    syslibs = ", ".join(f"{k}={v}" for k, v in test_result["syslib_versions"].items())
                    report.append(f"  {status} {test_result['package_version']} with {syslibs}")
        
        return "\n".join(report)


# Example usage and testing framework
class RuntimeHealer:
    """The runtime healer that automatically fixes import errors."""
    
    def __init__(self, swapper: SysLibSwapper):
        self.swapper = swapper
        
    def heal_import_error(self, script_path: Path, error_output: str) -> bool:
        """
        Analyze an import error and automatically fix it by finding compatible
        system library versions.
        """
        safe_print(f"\n🏥 RUNTIME HEALER: Analyzing error from {script_path}")
        
        # Extract package name from error
        package_name = self._extract_package_from_error(error_output)
        if not package_name:
            safe_print("❌ Could not identify problematic package")
            return False
        
        safe_print(f"🎯 Identified problematic package: {package_name}")
        
        # Try to find a working combination
        working_combo = self.swapper.find_working_combination(package_name, "latest")
        
        if not working_combo:
            # No known working combo, try to heal it
            working_combo = self.swapper.heal_runtime_error(package_name, error_output)
        
        if working_combo:
            safe_print(f"💊 Found healing combination: {working_combo}")
            # Re-run script with the working environment
            return self._rerun_script_with_environment(script_path, working_combo)
        
        return False
    
    def _extract_package_from_error(self, error_output: str) -> Optional[str]:
        """Extract the problematic package name from error output."""
        # Common patterns in import errors
        import re
        patterns = [
            r"No module named '([^']+)'",
            r"ImportError: cannot import name '([^']+)'",
            r"ModuleNotFoundError: No module named '([^']+)'",
            r"ImportError: ([^\s]+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, error_output)
