---
title: LibResolver - System Library Version Swapper
doc_type: guide
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
---

# LibResolver - System Library Version Swapper

## Overview
LibResolver is a sophisticated system library management tool that enables compatibility testing across different versions of critical system libraries.

## Key Features

### 1. Multi-Version Library Management
- Downloads and compiles system libraries from source (glibc, OpenSSL, zlib, libpng)
- Maintains isolated versions in `/opt/omnilibs`
- Computes ABI hashes for compatibility tracking

### 2. Runtime Environment Isolation
Uses `LD_LIBRARY_PATH` and `LD_PRELOAD` to create isolated runtime environments:
```python
with swapper.runtime_environment("glibc", "2.35"):
    # Your code runs with glibc 2.35 instead of system default
    import some_package
```

### 3. Automated Compatibility Testing
Tests Python packages against different system library combinations:
- Installs package in isolated temp environment
- Tests import and basic functionality
- Records results in compatibility matrix

### 4. Compatibility Database
Maintains `compatibility.json` with:
- Known working combinations
- Known broken combinations  
- Test history and results

### 5. Runtime Healing (Experimental)
Automatically detects library errors and retries with known-good library versions.

## Use Cases
- Testing packages across different Linux distributions
- Ensuring compatibility with older/newer glibc versions
- Debugging library-related import failures
- Building portable Python applications

## Status
- **Currently**: Dormant (not integrated into main CLI)
- **Location**: `omnipkg/libresolver.py`
- **Lines**: 700+ lines of production-ready code
- **Dependencies**: Requires build tools (gcc, make, etc.)

## Future Integration Plans
1. Add `omnipkg test-compat` CLI command
2. Integrate with package installation workflow
3. Auto-detect library issues and suggest fixes
4. Build compatibility reports for packages

## Example Usage
```python
from omnipkg.libresolver import SysLibSwapper

swapper = SysLibSwapper()

# Ensure glibc 2.35 is available
swapper.ensure_library_version("glibc", "2.35")

# Test package compatibility
result = swapper.test_compatibility(
    "numpy", "1.24.0",
    {"glibc": "2.35", "openssl": "3.0.8"}
)
```

## Security Note
Requires root/sudo for `/opt/omnilibs` access. Consider user-space alternatives for production.
