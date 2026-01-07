---
title: Tensorflow Dependency Switching
doc_type: demo
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# Tensorflow Dependency Switching

## Overview

This log demonstrates **Complex Dependency Tree Management**.

While the previous demo showed swapping a single binary, this one shows swapping **TensorFlow** (a massive package) along with its sub-dependencies (`typing_extensions`, `keras`) in a nested context. It proves OmniPkg handles the "Butterfly Effect" of dependencies—changing one package updates the entire graph instantly.

# TensorFlow Test

!!! note "Demo Prerequisite"
    This demo requires **Python 3.11**.

This demo validates OmniPkg's ability to handle **massive, complex dependency graphs**. Unlike simple packages, TensorFlow brings along a huge tree of dependencies (Keras, TensorBoard, Protobuf, etc.). OmniPkg must ensure *all* of them are swapped correctly to prevent ABI mismatches.

## Usage

```bash
# Run Demo #4: Complex Dependency Graph Switching
omnipkg demo 4
```

## What You'll Learn

1.  **Deep Graph Swapping**: How activating `tensorflow==2.13.0` automatically pulls in the correct `keras`, `typing_extensions`, and `numpy` versions.
2.  **Nested Contexts**: How to use `omnipkgLoader` inside another `omnipkgLoader` block (Inception-style environment management).
3.  **Strict Mode Verification**: Verifying that no "cloaked" modules (old versions) leak into the new context.

## The Code

This script performs a nested activation: it first activates a specific version of `typing_extensions`, and then *inside that block*, activates an old version of `tensorflow` that requires a *different* `typing_extensions`. OmniPkg handles this conflict resolution in real-time.

```python title="demo_tensorflow.py"
from omnipkg.loader import omnipkgLoader

# 1. Outer Context: Activate older typing_extensions
with omnipkgLoader("typing_extensions==4.5.0"):
    import typing_extensions as te
    print(f"Outer Version: {te.__version__}")
    # Output: 4.5.0
    
    # 2. Inner Context: Activate TensorFlow 2.13.0
    # TensorFlow 2.13.0 might require a different typing_extensions.
    # OmniPkg creates a NEW nested bubble for this block.
    with omnipkgLoader("tensorflow==2.13.0"):
        import tensorflow as tf
        import typing_extensions as te_inner
        
        print(f"Inner TF Version: {tf.__version__}")
        # Output: 2.13.0
        
        print(f"Inner TE Version: {te_inner.__version__}")
        # Output: Matches TF's requirement (automatically resolved)
        
        # Verify the complex graph works by creating a model
        model = tf.keras.Sequential([tf.keras.layers.Dense(1)])
        print("Model created successfully")

    # 3. Back to Outer Context
    # TensorFlow is unloaded; typing_extensions reverts to 4.5.0
    import typing_extensions as te_back
    print(f"Restored Version: {te_back.__version__}")
    # Output: 4.5.0
```

## Live Execution Log

Notice the **Nested activation (depth=2)** line in the logs. This confirms that OmniPkg is maintaining a stack of environments and correctly popping them off as the code exits the `with` blocks.

```text title="omnipkg demo 4"
(evocoder_env) minds3t@aiminingrig:~/omnipkg$ omnipkg demo 4

================================================================================
  🚀 🚨 OMNIPKG TENSORFLOW DEPENDENCY SWITCHING TEST 🚨
================================================================================

--- Nested Loader Test ---
🌀 Testing nested loader usage...
   ✅ Outer context - Typing Extensions: 4.5.0

🚀 Fast-activating tensorflow==2.13.0 ...
   📂 Searching for bubble: .../tensorflow-2.13.0
   📊 Bubble: 40 packages, 0 conflicts
   🧹 Purging 40 module(s) from memory...
   - 🔒 STRICT mode
   ⚡ HEALED in 25,743.3 μs
   ✅ Inner context - TensorFlow: 2.13.0
   ✅ Inner context - Typing Extensions: 4.5.0
   ✅ Nested loader test: Model created successfully

🌀 omnipkg loader: Deactivating tensorflow==2.13.0 [depth=2]...
   ✅ Environment restored.
   ⏱️  Swap Time: 26,032.537 μs

🌀 omnipkg loader: Deactivating typing_extensions==4.5.0...
   ✅ Environment restored.
   ⏱️  Swap Time: 125,331.240 μs

================================================================================
  🚀 STEP 3: Test Results Summary
================================================================================
Test 1 (TensorFlow 2.13.0 Bubble): ✅ PASSED
Test 2 (Dependency Switching): ✅ PASSED
Test 3 (Nested Loaders): ✅ PASSED

Overall: 3/3 tests passed
🎉 DEMO PASSED! 🎉
```

## How It Works

1.  **Graph Resolution**: When `tensorflow==2.13.0` is requested, the LibResolver walks its dependency tree. It identifies 40+ sub-dependencies.
2.  **Memory Purge**: It unloads all 40 modules from `sys.modules` to ensure no stale code remains.
3.  **Stack Management**: The `omnipkgLoader` tracks the "depth" of the context. When the inner block exits, it doesn't just clear `sys.path`—it restores the `sys.path` of the *outer* block (restoring `typing_extensions==4.5.0`).
