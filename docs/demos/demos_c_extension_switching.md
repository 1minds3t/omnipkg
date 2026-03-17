---
title: C Extension Switching
doc_type: demo
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# C Extension Switching

## Overview

# NumPy + SciPy Stress Test

!!! danger "The Holy Grail of Python Conflicts"
    This demo performs an action considered "impossible" in standard Python: **Swapping C-extension modules (NumPy/SciPy) mid-execution without crashing the interpreter.**

This demo is the ultimate stress test for OmniPkg's **LibResolver**. It forces the interpreter to load, unload, and reload incompatible C-extension binaries (shared objects/DLLs) within the same process.

## Usage

**Prerequisite:** This demo requires **Python 3.11** (due to specific binary wheels used in the test).
**Try it now:** Cloud users can click the play button to run this demo instantly:
```bash
omnipkg demo 3
```
To run this demo locally:

```bash
# Run Demo #3: The "Nuclear" Option
omnipkg demo 3
```

## What You'll Learn

1.  **C-Extension Hot-Swapping**: How OmniPkg manages the memory lifecycle of shared libraries (`.so` / `.pyd`).
2.  **Binary Compatibility**: How it ensures `numpy==1.24.3` (linked to older BLAS) doesn't crash when swapped for `1.26.4`.
3.  **The "Nuclear" Swap**: Watch it combine incompatible pairs (`numpy==1.24.3 + scipy==1.12.0`) vs (`numpy==1.26.4 + scipy==1.16.1`) in real-time.

## The Code

This script performs a series of high-speed swaps between incompatible versions of NumPy and SciPy, verifying that the math operations (which rely on C code) return correct results every time.

```python title="demo_numpy_scipy.py"
from omnipkg.loader import omnipkgLoader
import numpy as np

# 1. Baseline: NumPy 1.26.4 (Main Env)
print(f"Baseline Version: {np.__version__}")
# Output: 1.26.4

# 2. The "Impossible" Swap: NumPy 1.24.3
# This involves unloading C-extensions and reloading older binaries
with omnipkgLoader("numpy==1.24.3"):
    import numpy as np
    
    # Verify we are running C-code from 1.24.3
    print(f"Swapped Version: {np.__version__}")
    # Output: 1.24.3
    
    # Verify math accuracy (ensure memory isn't corrupted)
    print(f"Array Sum: {np.array([1, 2, 3]).sum()}") 
    # Output: 6

# 3. Restore: Back to 1.26.4
# OmniPkg handles the cleanup and re-linking automatically
import numpy as np
print(f"Restored Version: {np.__version__}")
# Output: 1.26.4
```

## Live Execution Log

Pay attention to the **Activation Time**. OmniPkg swaps these massive C-libraries in **~118ms**.

```text title="omnipkg demo 3"
(evocoder_env) minds3t@aiminingrig:~/omnipkg$ omnipkg demo 3

============================================================
  🚀 STEP 3: Executing the Nuclear Test
============================================================

💥 NUMPY VERSION JUGGLING:

⚡ Switching to numpy==1.24.3
   🧹 Purging 1 module(s) from memory...
   - 🔒 STRICT mode
   - 🔩 Activating binary path: .../numpy-1.24.3/bin
   ⚡ HEALED in 21,684.8 μs
   ✅ Version: 1.24.3
   🔢 Array sum: 6
   ⚡ Activation time: 118.52ms
   🎯 Version verification: PASSED
   
🌀 omnipkg loader: Deactivating numpy==1.24.3...
   ✅ Environment restored.
   ⏱️  Swap Time: 47,614.668 μs

⚡ Switching to numpy==1.26.4
   ✅ Version: 1.26.4
   🔢 Array sum: 6
   ⚡ Activation time: 119.48ms
   🎯 Version verification: PASSED

🤯 NUMPY + SCIPY VERSION MIXING:

🌀 COMBO: numpy==1.24.3 + scipy==1.12.0
   🧪 numpy: 1.24.3, scipy: 1.12.0
   🔗 Compatibility check: [1. 2. 3.]
   🎯 Version verification: BOTH PASSED!
   ⚡ Total combo time: 162.69ms

🌀 COMBO: numpy==1.26.4 + scipy==1.16.1
   🧪 numpy: 1.26.4, scipy: 1.16.1
   🔗 Compatibility check: [1. 2. 3.]
   🎯 Version verification: BOTH PASSED!
   ⚡ Total combo time: 130.39ms

🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇
```

## How It Works

1.  **Memory Purge**: Before swapping, OmniPkg forces a garbage collection and manually unregisters the C-extension modules from `sys.modules`.
2.  **Binary Path Injection**: It injects the `bin` path of the target version into `LD_LIBRARY_PATH` (Linux) or `PATH` (Windows) to ensure the dynamic linker finds the correct `.so` / `.dll` files.
3.  **Strict Mode**: For packages identified as "C-Heavy" (like NumPy), OmniPkg enables Strict Mode, which performs deeper verification to prevent segmentation faults.