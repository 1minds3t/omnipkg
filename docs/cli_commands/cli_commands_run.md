---
title: Run
doc_type: reference
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: cli_commands
---

# Run

## Overview

# omnipkg run: Auto-Healing Script Runner

## The Problem omnipkg Solves

Traditional Python scripts fail immediately when:
- A module is missing (`ModuleNotFoundError`)
- Version conflicts exist (`AssertionError`)
- C-extensions are incompatible (NumPy ABI errors)

**omnipkg run** catches these errors in real-time and automatically heals your environment **faster than other tools take to fail.**

## Usage
```bash
omnipkg run  [args]
```

## Live Example: 7.76× Faster Than UV

This real CI output shows omnipkg auto-healing a NumPy compatibility issue:
```bash
⏱️  UV run failed in: 5379.237 ms
🔍 NumPy 2.0 compatibility issue detected. Auto-healing...
✅ Using bubble: numpy-1.26.4

🚀 Re-running with omnipkg auto-heal...
✅ Script completed successfully inside omnipkg bubble.

======================================================================
🎯 omnipkg is 7.76x FASTER than UV!
💥 That's 675.99% improvement!
======================================================================
```

## How It Works

1. **Script Execution**: Runs your script in a monitored subprocess
2. **Error Detection**: Captures `ModuleNotFoundError`, `AssertionError`, NumPy C-API errors
3. **Bubble Creation**: Instantly creates/activates version-specific environment
4. **Auto-Retry**: Re-runs script in healed environment
5. **Success**: Your script completes without manual intervention

## Supported Error Types

- ✅ **Missing Modules**: Automatically installs required packages
- ✅ **Version Conflicts**: Creates bubbles for conflicting versions
- ✅ **C-Extension Failures**: Downgrades NumPy/SciPy for compatibility
- ✅ **Import Errors**: Resolves circular dependencies

## Example: Healing Legacy TensorFlow Code
```python
# legacy_model.py
import tensorflow as tf  # Requires old NumPy
model = tf.keras.models.load_model("old_model.h5")
```
```bash
# Without omnipkg - FAILS immediately
python legacy_model.py
# ImportError: NumPy 2.0 incompatible with TensorFlow 2.13

# With omnipkg - HEALS automatically
omnipkg run legacy_model.py
# 🔍 NumPy compatibility issue detected. Auto-healing...
# ✅ Script completed successfully!
```

## Try It Now
```bash
omnipkg demo 7  # Auto-healing demo
```

## Performance Metrics

| Operation | Traditional | omnipkg run | Speedup |
|-----------|-------------|-------------|---------|
| Detect error | 5.4s | 5.4s | - |
| Fix environment | Minutes (manual) | 0.69s | **468×** |
| Re-run script | 5.4s | <1s (cached) | - |
| **Total** | **~Minutes** | **~6s** | **>10×** |

## Advanced Options
```bash
# Verbose output
omnipkg run --verbose script.py

# Specify Python version
omnipkg run --python 3.9 script.py

# Force bubble creation
omnipkg run --force-bubble script.py
```

## RCE Protection

For security, `omnipkg run` is **disabled in the web bridge** to prevent remote code execution. Use it locally for maximum safety.

## See Also

- [daemon System](daemon-system.md) - Background worker for faster healing
- [Auto-Healing Demo](../demos/demos-auto-healing.md) - Live example