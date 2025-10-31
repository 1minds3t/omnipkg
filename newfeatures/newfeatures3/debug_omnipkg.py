#!/usr/bin/env python3
"""
Debug script for omnipkg metadata builder issues
Save this as debug_omnipkg.py in your current directory
"""
import os
import sys
import json
import importlib.metadata
import redis
import traceback
from pathlib import Path

try:
    from packaging.utils import canonicalize_name
except ImportError:
    def canonicalize_name(name):
        return name.lower().replace('_', '-')

def debug_omnipkg_metadata():
    print("🔍 omnipkg Metadata Builder Debug Script")
    print("=" * 50)
    
    # Try to load config
    config = None
    try:
        # Assume ConfigManager is available
        from omnipkg.core import ConfigManager
        config = ConfigManager().config
        print("✅ Config loaded successfully")
        print(f"   Redis: {config['redis_host']}:{config['redis_port']}")
        print(f"   Site packages: {config['site_packages_path']}")
        print(f"   Multiversion base: {config['multiversion_base']}")
        print(f"   Redis key prefix: {config['redis_key_prefix']}")
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        print(f"   Full error: {traceback.format_exc()}")
        # Try to create a minimal config for testing
        try:
            config = {
                'redis_host': 'localhost',
                'redis_port': 6379,
                'redis_key_prefix': 'omnipkg:pkg:',
                'site_packages_path': '/path/to/site-packages',
                'multiversion_base': '/tmp/test'
            }
            print("   Using fallback config for testing")
        except:
            print("   Cannot continue without config")
            return
    
    # Test Redis connection
    try:
        redis_client = redis.Redis(
            host=config["redis_host"],
            port=config["redis_port"],
            decode_responses=True
        )
        redis_client.ping()
        print("✅ Redis connection successful")
        
        # Check existing data
        pattern = f"{config['redis_key_prefix']}*"
        existing_keys = redis_client.keys(pattern)
        print(f"   Found {len(existing_keys)} existing keys in Redis")
        
        if existing_keys:
            print("   Sample keys:")
            for key in existing_keys[:5]:
                print(f"     - {key}")
                
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print(f"   Full error: {traceback.format_exc()}")
        return
    
    # Discover packages using the same logic as your script
    print("\n📦 Package Discovery Analysis:")
    print("-" * 30)
    
    packages = {}
    active_packages = {}
    
    # Method 1: importlib.metadata
    try:
        dist_count = 0
        problematic_packages = []
        
        for dist in importlib.metadata.distributions():
            dist_count += 1
            package_name_from_meta = dist.metadata.get("Name")
            
            if not package_name_from_meta:
                problematic_packages.append(f"Distribution #{dist_count}: No Name in metadata")
                continue
                
            pkg_name = canonicalize_name(package_name_from_meta)
            version = dist.metadata.get('Version', 'unknown')
            
            packages[pkg_name] = version
            active_packages[pkg_name] = version
            
        print(f"✅ importlib.metadata found {len(packages)} valid packages")
        if problematic_packages:
            print(f"⚠️  Found {len(problematic_packages)} problematic distributions:")
            for prob in problematic_packages[:3]:  # Show first 3
                print(f"     - {prob}")
                
    except Exception as e:
        print(f"❌ importlib.metadata discovery failed: {e}")
    
    # Method 2: Direct site-packages scan
    site_packages = Path(config["site_packages_path"])
    if site_packages.is_dir():
        dist_info_count = 0
        for item in site_packages.iterdir():
            if item.is_dir() and (item.name.endswith('.dist-info') or item.name.endswith('.egg-info')):
                dist_info_count += 1
        print(f"✅ Found {dist_info_count} .dist-info/.egg-info directories in site-packages")
    else:
        print(f"❌ Site packages path doesn't exist: {site_packages}")
    
    # Method 3: Multiversion packages
    multiversion_base_path = Path(config["multiversion_base"])
    if multiversion_base_path.is_dir():
        isolated_count = 0
        for isolated_pkg_dir in multiversion_base_path.iterdir():
            if isolated_pkg_dir.is_dir() and '-' in isolated_pkg_dir.name:
                isolated_count += 1
        print(f"✅ Found {isolated_count} isolated package directories")
    else:
        print(f"⚠️  Multiversion base doesn't exist: {multiversion_base_path}")
    
    # Test metadata building for one package
    if packages:
        test_pkg_name = list(packages.keys())[0]
        test_version = packages[test_pkg_name]
        print(f"\n🧪 Testing metadata building for: {test_pkg_name} v{test_version}")
        print("-" * 40)
        
        try:
            # Simulate the metadata building process
            version_key = f"{config['redis_key_prefix']}{test_pkg_name}:{test_version}"
            previous_data = redis_client.hgetall(version_key)
            
            print(f"   Version key: {version_key}")
            print(f"   Previous data exists: {bool(previous_data)}")
            
            if previous_data:
                print(f"   Previous data keys: {list(previous_data.keys())}")
                print(f"   Has checksum: {'checksum' in previous_data}")
            
            # Test distribution finding
            try:
                dist = importlib.metadata.distribution(test_pkg_name)
                print(f"   ✅ Distribution found for {test_pkg_name}")
                print(f"   Metadata keys: {list(dist.metadata.keys())[:10]}...")
                print(f"   Files available: {dist.files is not None}")
                if dist.files:
                    print(f"   File count: {len(dist.files)}")
            except Exception as e:
                print(f"   ❌ Distribution lookup failed: {e}")
                
        except Exception as e:
            print(f"   ❌ Metadata test failed: {e}")
    
    # Now let's test the metadata builder directly
    print(f"\n🔧 Testing Metadata Builder Import:")
    print("-" * 40)
    
    try:
        # Try to run the metadata builder script directly
        print("   Attempting to import metadata builder...")
        
        # First try to run it as a module
        result = os.system("python3 -c 'from omnipkg.package_meta_builder import omnipkgMetadataGatherer; print(\"Import successful\")'")
        if result == 0:
            print("   ✅ Metadata builder imports successfully")
        else:
            print(f"   ❌ Metadata builder import failed with code {result}")
            
        # Try to run it directly
        print("   Attempting direct execution...")
        result = os.system("python3 -m omnipkg.package_meta_builder --help")
        print(f"   Direct execution result: {result}")
        
    except Exception as e:
        print(f"   ❌ Error testing metadata builder: {e}")
    
    # Test the specific part that might be failing
    print(f"\n🧪 Testing Package Discovery:")
    print("-" * 30)
    
    try:
        # Test importlib.metadata directly
        distributions = list(importlib.metadata.distributions())
        print(f"   ✅ Found {len(distributions)} distributions via importlib.metadata")
        
        # Test a few distributions
        valid_count = 0
        invalid_count = 0
        
        for i, dist in enumerate(distributions[:5]):  # Test first 5
            try:
                name = dist.metadata.get("Name")
                version = dist.metadata.get("Version") 
                if name and version:
                    canonical = canonicalize_name(name)
                    print(f"   ✅ {canonical} v{version}")
                    valid_count += 1
                else:
                    print(f"   ❌ Distribution {i}: Missing name or version")
                    invalid_count += 1
            except Exception as e:
                print(f"   ❌ Distribution {i}: Error {e}")
                invalid_count += 1
                
        print(f"   Summary: {valid_count} valid, {invalid_count} invalid")
        
    except Exception as e:
        print(f"   ❌ Package discovery test failed: {e}")
        print(f"   Full error: {traceback.format_exc()}")
    
    # Check force refresh logic
    print(f"\n🔧 Force Refresh Analysis:")
    print(f"   Command line args: {sys.argv}")
    force_detected = '--force' in sys.argv or '-f' in sys.argv
    print(f"   Force flag detected: {force_detected}")
    
    print("\n💡 Next Steps:")
    print("-" * 15)
    print("   1. Copy this debug script to your omnipkg directory as 'debug_omnipkg.py'")
    print("   2. Run: python3 debug_omnipkg.py")
    print("   3. Check the exact error from the metadata builder")
    print("   4. Try running the metadata builder directly:")
    print("      python3 -m omnipkg.package_meta_builder --force")
    
    return config

if __name__ == "__main__":
    debug_omnipkg_metadata()
