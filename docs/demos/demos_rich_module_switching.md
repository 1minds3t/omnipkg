---
title: Rich Module Switching
doc_type: tutorial
status: stable
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

You are absolutely right to catch that. I missed the explicit **"How to Run"** command block in that specific file draft.

While the log title hinted at it, you need a dedicated code block so users know exactly what to type to reproduce the demo on their machine.

Here is the **corrected** version for `docs/demos/rich_module_switching.md`.

**Paste this into:** `docs/demos/rich_module_switching.md`

```markdown
---
title: Rich Module Switching
doc_type: demo
status: stable
generated: true
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# Rich Module Switching

!!! success "Demo Status: Verified"
    This demo is fully functional and runs automatically via the CLI.

This demo showcases OmniPkg's ability to manage **multiple concurrent versions** of a pure Python package (like `rich`) within a single environment, switching between them instantly using the **Worker Daemon**.

## Usage

To run this demo directly on your machine (skipping the main interactive menu), use the following command:

```bash
# Run Demo #1: Rich Module Switching
omnipkg demo 1
```

## What You'll Learn

1.  **Bubble Creation**: How OmniPkg installs conflicting versions into isolated "Bubbles" without breaking the main environment.
2.  **Daemon Proxying**: How to execute code against a specific version using the `DaemonProxy`.
3.  **Auto-Healing**: How the system detects corruption or missing files and repairs them on the fly.

## The Code

This script sets up a "Main" environment with `rich==13.7.1`, then creates isolated bubbles for older versions (`13.5.3` and `13.4.2`). It then verifies that each context loads the correct code.

```python title="demo_rich.py"
from omnipkg.common_utils import safe_print
from omnipkg.isolation.worker_daemon import DaemonClient, DaemonProxy

# 1. Setup Main Environment
omnipkg_core.smart_install(["rich==13.7.1"])

# 2. Create Isolated Bubbles
# These do NOT overwrite 13.7.1; they live in hidden side-dirs
omnipkg_core.smart_install(["rich==13.5.3"])
omnipkg_core.smart_install(["rich==13.4.2"])

# 3. Verify Main Environment (Local Process)
import rich
print(f"Main Process Version: {rich.__version__}") 
# Output: 13.7.1

# 4. Verify Bubbles (Daemon Process)
client = DaemonClient()

# Execute code inside the v13.5.3 bubble
proxy = DaemonProxy(client, "rich==13.5.3")
result = proxy.execute("""
import rich
from importlib.metadata import version
print(f"Daemon Version: {version('rich')}")
""")
# Output: 13.5.3
```

## Live Execution Log

Below is the actual output from running this demo. Notice how OmniPkg handles the installation, verification, and cleanup automatically.

```text title="omnipkg demo 1"
(evocoder_env) minds3t@aiminingrig:~/omnipkg$ omnipkg demo 1

================================================================================
  🚀 STEP 1: Environment Setup & Cleanup
================================================================================
   🧹 Force removing any existing Rich installation...
   ✅ Rich uninstalled successfully.
   ℹ️  Rich not found. Installing baseline v13.7.1...
   ✓ Pip validated 'rich==13.7.1' -> 'rich==13.7.1'
   ✅ Environment prepared

================================================================================
  🚀 STEP 2: Creating Test Bubbles
================================================================================
   🫧 Creating bubble for rich==13.5.3
   ⚠️  Detected 1 dependency changes:
      ⬇️  rich: v13.7.1 → v13.5.3 (downgrade)
   🛡️ STABILITY PROTECTION: Processing 1 changed package(s)
   🫧 Creating isolated bubble for rich v13.5.3 (Python 3.11 context)
   - 🚚 Moving verified build to bubble: .../.omnipkg_versions/rich-13.5.3
   ✅ Bubble created successfully

   🫧 Creating bubble for rich==13.4.2
   ⚠️  Detected 1 dependency changes:
      ⬇️  rich: v13.7.1 → v13.4.2 (downgrade)
   🛡️ STABILITY PROTECTION: Processing 1 changed package(s)
   - 🚚 Moving verified build to bubble: .../.omnipkg_versions/rich-13.4.2
   ✅ Bubble created successfully

================================================================================
  🚀 STEP 3: High-Speed Version Verification
================================================================================

--- Testing Main Environment (rich==13.7.1) ---
   🏠 Verifying v13.7.1 in Main Process...
   ✅ Verified version 13.7.1

--- Testing Bubble (rich==13.5.3) ---
   ⚡ Verifying v13.5.3 via Daemon Worker...
      - Version: 13.5.3
      - Path: .../site-packages/.omnipkg_versions/rich-13.5.3/rich/__init__.py
      - Latency: 1752.59ms
   ✅ Verified version 13.5.3

--- Testing Bubble (rich==13.4.2) ---
   ⚡ Verifying v13.4.2 via Daemon Worker...
      - Version: 13.4.2
      - Path: .../site-packages/.omnipkg_versions/rich-13.4.2/rich/__init__.py
      - Latency: 1723.25ms
   ✅ Verified version 13.4.2

================================================================================
  🚀 FINAL TEST RESULTS
================================================================================
   main-13.7.1               : ✅ PASSED
   bubble-13.5.3             : ✅ PASSED
   bubble-13.4.2             : ✅ PASSED

================================================================================
  🚀 STEP 4: Cleanup
================================================================================
   🧹 Removing test bubbles...
   🗑️  Deleting bubble directory: .../.omnipkg_versions/rich-13.5.3
   🗑️  Deleting bubble directory: .../.omnipkg_versions/rich-13.4.2
   ✅ Main environment (v13.7.1) preserved.
```

## How It Works

1.  **Stability Protection**: When the script requested `rich==13.5.3`, OmniPkg saw that `13.7.1` was already installed. Instead of downgrading (which would break the main environment), it triggered **Stability Protection**.
2.  **Bubble Storage**: The older versions were installed into hidden directories (`.omnipkg_versions/`).
3.  **Daemon Isolation**: The main process kept using v13.7.1. The verification requests for older versions were sent to the **Worker Daemon**, which dynamically loaded the bubble paths into `sys.path` for that specific execution context.
```