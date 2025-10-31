#!/usr/bin/env python3
"""
Debug script for omnipkg metadata builder issues
"""
import os
import sys
import json
import importlib.metadata
import redis
from pathlib import Path
from packaging.utils import canonicalize_name

def debug_omnipkg_metadata():
    print("🔍 omnipkg Metadata Builder Debug Script")
    print("=" * 50)
    
    # Try to load config
    try:
        # Assume ConfigManager is available
        from omnipkg.core import ConfigManager
        config = ConfigManager().config
        print("✅ Config loaded successfully")
        print(f"   Redis: {config['redis_host']}:{config['redis_port']}")
        print(f"   Site packages: {config['site_packages_path']}")
        print(f"   Multiversion base: {config['multiversion_base']}")
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
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
    
    # Check force refresh logic
    print(f"\n🔧 Force Refresh Analysis:")
    print(f"   Command line args: {sys.argv}")
    force_detected = '--force' in sys.argv or '-f' in sys.argv
    print(f"   Force flag detected: {force_detected}")
    
    print("\n💡 Recommendations:")
    print("-" * 20)
    if not packages:
        print("   - No packages discovered - check your Python environment")
    if len(existing_keys) > 0 and not force_detected:
        print("   - Existing data found - try using --force flag")
    print("   - Run with increased verbosity to see individual package processing")
    print("   - Check if the checksum logic is preventing updates")

if __name__ == "__main__":
    debug_omnipkg_metadata()