"""
Simple test: Does daemon worker communication work?
"""
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy

print("🧪 Testing daemon-loader communication...")
print("=" * 60)

# Test 1: Check if daemon is running
print("\n1️⃣ Checking daemon status...")
client = DaemonClient()
status = client.status()
print(f"   Status: {status}")

if not status.get("success"):
    print("   ❌ Daemon not running!")
    sys.exit(1)

print("   ✅ Daemon is running")

# Test 2: Try to execute simple code in main environment
print("\n2️⃣ Testing execution in MAIN environment (no package spec)...")
try:
    # Just try to execute code without any package loading
    proxy = DaemonProxy(client, None)  # No package spec
    result = proxy.execute("print('Hello from worker!')")
    
    if result.get("success"):
        print(f"   ✅ SUCCESS")
        print(f"   Output: {result.get('stdout', '').strip()}")
    else:
        print(f"   ❌ FAILED: {result.get('error')}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

# Test 3: Try with rich (already installed in main env)
print("\n3️⃣ Testing with rich (main env version)...")
try:
    code = """
from importlib.metadata import version
print(f"Rich version: {version('rich')}")
"""
    proxy = DaemonProxy(client, None)  # Still no spec, just use main env
    result = proxy.execute(code)
    
    if result.get("success"):
        print(f"   ✅ SUCCESS")
        print(f"   Output: {result.get('stdout', '').strip()}")
    else:
        print(f"   ❌ FAILED: {result.get('error')}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

# Test 4: Try with a package spec (this is where it might fail)
print("\n4️⃣ Testing with package spec (rich==13.5.3)...")
try:
    code = """
from importlib.metadata import version
print(f"Rich version: {version('rich')}")
"""
    proxy = DaemonProxy(client, "rich==13.5.3")
    result = proxy.execute(code)
    
    if result.get("success"):
        print(f"   ✅ SUCCESS")
        print(f"   Output: {result.get('stdout', '').strip()}")
    else:
        print(f"   ❌ FAILED: {result.get('error')}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

print("\n" + "=" * 60)
print("🏁 Test complete")
