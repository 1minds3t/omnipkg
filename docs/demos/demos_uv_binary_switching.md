---
title: UV Binary Switching
doc_type: demo
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# UV Binary Test

!!! success "Demo Status: Verified"
    This demo validates OS-level binary path manipulation (PATH injection).

This demo demonstrates a capability that most Python virtual environment managers cannot perform: **swapping binary executables on the fly** without changing the global system configuration.

It uses the high-performance `uv` package manager as a test case, proving that OmniPkg can manage multiple conflicting versions of a binary tool simultaneously.

## Usage

To run this demo locally:

```bash
# Run Demo #2: UV Binary Switching
omnipkg demo 2
```

**Try it now:** Cloud users can click the play button to run this demo instantly:
```bash
omnipkg demo 2
```

## What You'll Learn

1.  **PATH Injection**: How `omnipkgLoader` temporarily prepends bubble directories to the `PATH` environment variable.
2.  **Binary Isolation**: How distinct binary versions (`0.9.5`, `0.4.30`, `0.5.11`) coexist on the same disk.
3.  **Context Context**: How the active Python context can "see" a different binary version than the rest of the OS.

## The Code

This script verifies that we can execute different versions of the `uv` binary depending on which context is active.

```python title="demo_uv.py"
from omnipkg.loader import omnipkgLoader
import subprocess
import shutil

# 1. Setup Main Environment (e.g. uv 0.9.5)
# This is the version installed in the main site-packages
run_command(["uv", "--version"])
# Output: uv 0.9.5

# 2. Test Swapped Execution (e.g. uv 0.4.30)
# We use the omnipkgLoader context manager to activate a bubble
with omnipkgLoader("uv==0.4.30", force_activation=True):
    
    # Debug: Check which binary the OS sees
    print(f"Which uv: {shutil.which('uv')}")
    # Output: .../.omnipkg_versions/uv-0.4.30/bin/uv

    # Execute it!
    subprocess.run(["uv", "--version"])
    # Output: uv 0.4.30

# 3. Context Exit
# Outside the block, we are back to the main version
subprocess.run(["uv", "--version"])
# Output: uv 0.9.5
```

## Live Execution Log

This log proves that the `PATH` variable is being modified in real-time (look at the "First 3 PATH entries" debug line).

```text title="omnipkg demo 2"
(evocoder_env) minds3t@aiminingrig:~/omnipkg$ omnipkg demo 2

================================================================================
  🚀 🚨 OMNIPKG UV BINARY STRESS TEST (NO CLEANUP) 🚨
================================================================================

================================================================================
  🚀 STEP 1: Environment Setup & Cleanup
================================================================================
   ℹ️  'uv' not found in main environment. Installing a baseline version (0.9.5)...
   ✅ Environment prepared

================================================================================
  🚀 STEP 2: Creating Test Bubbles for Older Versions
================================================================================
   ✅ Bubble for uv==0.4.30 already exists.
   ✅ Bubble for uv==0.5.11 already exists.

================================================================================
  🚀 STEP 3: Comprehensive UV Version Testing
================================================================================

--- Testing Main Environment (uv==0.9.5) ---
   🔬 Testing binary at: .../bin/uv
   ✅ Main environment version: 0.9.5

--- Testing Bubble (uv==0.4.30) ---
   🔧 Testing swapped binary execution via omnipkgLoader...
   🔍 First 3 PATH entries: [
       '.../site-packages/.omnipkg_versions/uv-0.4.30/bin',  <-- INJECTED!
       '/home/minds3t/.local/bin', 
       '/home/minds3t/miniconda3/envs/evocoder_env/bin'
   ]
   🔍 Which uv: .../.omnipkg_versions/uv-0.4.30/bin/uv
   📍 Version via PATH: 0.4.30
   ✅ Swapped binary reported: 0.4.30
   🎯 Swapped binary test: PASSED

--- Testing Bubble (uv==0.5.11) ---
   🔧 Testing swapped binary execution via omnipkgLoader...
   🔍 First 3 PATH entries: [
       '.../site-packages/.omnipkg_versions/uv-0.5.11/bin',  <-- INJECTED!
       ...
   ]
   📍 Version via PATH: 0.5.11
   ✅ Swapped binary reported: 0.5.11
   🎯 Swapped binary test: PASSED

================================================================================
  🚀 FINAL TEST RESULTS
================================================================================
📊 Test Summary:
   bubble-0.4.30            : ✅ PASSED
   bubble-0.5.11            : ✅ PASSED
   main-0.9.5               : ✅ PASSED

🎉🎉🎉 ALL UV BINARY TESTS PASSED! 🎉🎉🎉
```

## How It Works

1.  **Binary Detection**: OmniPkg detects that `uv` provides a binary executable (by checking the `bin/` directory in the wheel).
2.  **Loader Activation**: When `omnipkgLoader("uv==0.4.30")` is called, it finds the isolated bubble path.
3.  **PATH Manipulation**: It prepends the bubble's `bin/` directory to `os.environ["PATH"]`.
4.  **Process Inheritance**: Any `subprocess.run` calls made inside that block inherit the modified environment, causing the OS to find the bubbled binary first.