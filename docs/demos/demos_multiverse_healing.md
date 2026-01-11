---
title: Multiverse Healing
doc_type: demo
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# Multiverse Healing Test

!!! success "Demo Status: Verified"
    This demo is fully functional and runs automatically via the CLI.

## Overview

This demo demonstrates **Cross-Python Hot-Swapping Mid-Script** with automatic environment healing.

Unlike previous demos that swap packages within a single Python version, this one orchestrates a **multi-dimensional workflow**: executing code in Python 3.9 with legacy dependencies (NumPy 1.x, SciPy), then seamlessly transferring data to Python 3.11 running modern packages (TensorFlow 2.20). It proves OmniPkg can manage complex multi-version pipelines where different Python interpreters handle different stages of computation.

# Multiverse Analysis

!!! note "Demo Prerequisite"
    This demo requires **Python 3.11** as the orchestrator. It will automatically adopt and manage Python 3.9 for the legacy context.

This demo validates OmniPkg's ability to handle **cross-interpreter workflows** with automatic context switching and dependency healing. The test simulates a realistic scenario where legacy scientific libraries (requiring older Python/NumPy) need to pass data to modern ML frameworks (requiring newer Python/TensorFlow).

## Usage

For local execution, you can run the demo directly:

```bash
# Run Demo #5: Multiverse Healing Test
omnipkg demo 5
```

If you have the OmniPkg web bridge connected, you can run it live in the UI:

```bash
omnipkg demo 5
```

## What You'll Learn

1. **Cross-Python Context Switching**: How to orchestrate workflows across Python 3.9 and 3.11 in a single script
2. **Automatic Environment Healing**: How `omnipkg run` detects and fixes package conflicts automatically
3. **Isolated Subprocess Execution**: How to run payloads in different Python versions without context pollution
4. **Data Transfer Between Versions**: Passing computation results between incompatible Python environments

## The Workflow

This test orchestrates a two-stage analysis pipeline:

```
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR (Python 3.11)                                     │
│  └─> Coordinates the entire workflow                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ├─────────────────────────────────┐
                              ▼                                 ▼
                    ┌─────────────────────┐         ┌──────────────────────┐
                    │  PYTHON 3.9 CONTEXT │         │  PYTHON 3.11 CONTEXT │
                    │  Legacy Analysis    │────────>│  Modern Prediction   │
                    │                     │  JSON   │                      │
                    │  • NumPy 1.26.4     │  data   │  • TensorFlow 2.20   │
                    │  • SciPy 1.13.1     │         │  • NumPy 2.0+        │
                    └─────────────────────┘         └──────────────────────┘
```

## The Code

The test script has three main components:

### 1. Bootstrap Section

Ensures the orchestrator runs in Python 3.11, regardless of the user's current environment:

```python
# This guard ensures bootstrap only runs for main orchestrator
if "OMNIPKG_MAIN_ORCHESTRATOR_PID" not in os.environ:
    os.environ["OMNIPKG_MAIN_ORCHESTRATOR_PID"] = str(os.getpid())
    
    # Check if we're already on Python 3.11
    if sys.version_info[:2] != (3, 11):
        # Find Python 3.11 interpreter via omnipkg
        target_exe = omnipkg_instance.interpreter_manager.config_manager.get_interpreter_for_version("3.11")
        
        # Relaunch THIS script using Python 3.11
        os.execve(str(target_exe), [str(target_exe)] + sys.argv, os.environ)
    
    # Sync omnipkg configuration to Python 3.11 reality
    sync_context_to_runtime()
```

### 2. Payload Functions

These functions run in isolated subprocesses with different Python versions:

```python
def run_legacy_payload():
    """Executes in Python 3.9 with legacy NumPy/SciPy"""
    import scipy.signal
    import numpy
    import json
    
    data = numpy.array([1, 2, 3, 4, 5])
    analysis_result = {"result": int(scipy.signal.convolve(data, data).sum())}
    print(json.dumps(analysis_result))

def run_modern_payload(legacy_data_json: str):
    """Executes in Python 3.11 with TensorFlow"""
    import tensorflow as tf
    import json
    
    input_data = json.loads(legacy_data_json)
    legacy_value = input_data["result"]
    
    # Use legacy result to make prediction
    prediction = "SUCCESS" if legacy_value > 200 else "FAILURE"
    print(json.dumps({"prediction": prediction}))
```

### 3. Orchestrator Logic

Coordinates the multi-version workflow:

```python
def multiverse_analysis():
    # STEP 1: Python 3.9 Context
    run_command_with_isolated_context(
        ["omnipkg", "swap", "python", "3.9"],
        "Swapping to Python 3.9 context"
    )
    
    python_3_9_exe = get_interpreter_path("3.9")
    
    install_packages_with_omnipkg(
        ["numpy<2", "scipy"],
        "Installing legacy packages for Python 3.9"
    )
    
    # Execute legacy payload directly with Python 3.9
    result_3_9 = subprocess.run(
        [python_3_9_exe, __file__, "--run-legacy"],
        capture_output=True, text=True
    )
    legacy_data = json.loads(result_3_9.stdout.strip())
    
    # STEP 2: Python 3.11 Context
    run_command_with_isolated_context(
        ["omnipkg", "swap", "python", "3.11"],
        "Swapping back to Python 3.11 context"
    )
    
    install_packages_with_omnipkg(
        ["tensorflow"],
        "Installing modern packages for Python 3.11"
    )
    
    # Execute modern payload with auto-healing via omnipkg run
    omnipkg_run_command = [
        "omnipkg", "run", __file__,
        "--run-modern", json.dumps(legacy_data)
    ]
    
    modern_output = run_command_with_isolated_context(
        omnipkg_run_command,
        "Executing modern payload with auto-healing enabled"
    )
    
    final_prediction = json.loads(json_output)
    return final_prediction["prediction"] == "SUCCESS"
```

## Live Execution Log

The test produces detailed output showing each stage of the multi-version workflow:

```text
================================================================================
 [START] OMNIPKG MULTIVERSE ANALYSIS TEST
================================================================================

[STEP 1] MISSION STEP 1: Setting up Python 3.9 dimension...

>> Executing: Swapping to Python 3.9 context
 | 🐍 Switching active Python context to version 3.9...
 | ✅ Managed interpreter: .../cpython-3.9.23/bin/python3.9
 | 🎉 Switched to Python 3.9!
 | ⏱️  Swap completed in: 144.091ms

>> Executing: Installing numpy<2 scipy
 | 🔍 Detected complex specifier: 'numpy<2'
 | ✅ Resolved 'numpy<2' to 'numpy==1.26.4'
 | ⚡ Running preflight satisfaction check...
 | 📦 Preflight detected packages need installation
 | 🚀 Starting install with policy: 'stable-main'

 [TEST] Executing legacy payload in Python 3.9...
--- Executing in Python 3.9.23 with SciPy 1.13.1 & NumPy 1.24.3 ---
[OK] Artifact retrieved from 3.9: Scipy analysis complete. Result: 225

[STEP 2] MISSION STEP 2: Setting up Python 3.11 dimension...

>> Executing: Swapping back to Python 3.11 context
 | 🐍 Switching active Python context to version 3.11...
 | 🎉 Switched to Python 3.11!
 | ⏱️  Swap completed in: 178.441ms

>> Executing: Installing tensorflow
 | 🎯 Found compatible version: 2.20.0
 | ⚡ PREFLIGHT SUCCESS: All 1 package(s) already satisfied!

 [TEST] Executing modern payload using 'omnipkg run'...
 | 🔄 Syncing omnipkg context...
 | 🩹 [OMNIPKG] Healed NumPy's issubdtype for TensorFlow.
--- Executing in Python 3.11.14 with TensorFlow 2.20.0 ---
[OK] Artifact processed by 3.11: TensorFlow prediction complete. Prediction: 'SUCCESS'

================================================================================
 [SUMMARY] TEST SUMMARY
================================================================================
[SUCCESS] MULTIVERSE ANALYSIS COMPLETE! Context switching, package management, 
and auto-healing working perfectly!

[PERFORMANCE] Total test runtime: 31.64 seconds
```

## Key Features Demonstrated

### 1. Bootstrap Protection

The bootstrap mechanism ensures the orchestrator always runs in the correct Python version:

- Detects current Python version
- Automatically relaunches in Python 3.11 if needed
- Uses `os.execve` for clean process replacement
- Sets environment guards to prevent recursive relaunching

### 2. Isolated Context Execution

The `run_command_with_isolated_context` helper prevents parent context pollution:

```python
# Create clean environment
env = os.environ.copy()
env.pop("OMNIPKG_FORCE_CONTEXT", None)
env["OMNIPKG_DISABLE_AUTO_ALIGN"] = "1"
env["OMNIPKG_SUBPROCESS_MODE"] = "1"

process = subprocess.Popen(command, env=env, ...)
```

### 3. Automatic Healing

When using `omnipkg run`, the system automatically:

- Detects NumPy compatibility issues between versions
- Patches `numpy.issubdtype` for TensorFlow compatibility
- Ensures clean module imports without conflicts

### 4. Version Constraint Resolution

The system intelligently handles complex version specifications:

- Resolves `numpy<2` to the latest compatible version (1.26.4)
- Validates compatibility with the target Python version
- Creates isolated bubbles when needed for stability

## How It Works

1. **Orchestrator Launch**: Script ensures it's running in Python 3.11 via bootstrap
2. **Context Swap**: Uses `omnipkg swap` to change active Python version
3. **Package Installation**: Installs version-specific dependencies in each context
4. **Subprocess Execution**: Runs payload functions in isolated Python processes
5. **Data Transfer**: Passes JSON-serialized data between Python versions
6. **Auto-Healing**: `omnipkg run` patches compatibility issues on the fly
7. **Context Restoration**: Returns to original Python 3.11 environment

## Common Pitfalls Avoided

### Problem: Context Pollution
**Solution**: Environment variables prevent subprocess context auto-alignment

### Problem: NumPy ABI Incompatibility
**Solution**: Auto-healing patches NumPy functions for TensorFlow compatibility

### Problem: Version Resolution Failures
**Solution**: Test installations validate compatibility before committing changes

### Problem: Recursive Relaunching
**Solution**: `OMNIPKG_MAIN_ORCHESTRATOR_PID` guard prevents bootstrap loops

## Performance Metrics

From the live execution:

- **Python 3.9 swap**: 144ms
- **Python 3.11 swap**: 178ms
- **Package resolution**: ~30 seconds (includes test installs)
- **Total runtime**: 31.64 seconds

The majority of time is spent on initial package discovery and validation. Subsequent runs benefit from caching and are significantly faster.

## Real-World Applications

This pattern enables several practical use cases:

1. **Legacy System Integration**: Run old analysis code (Python 2.7/3.6) and modern ML pipelines together
2. **Version-Specific Testing**: Test packages across multiple Python versions in one script
3. **Gradual Migration**: Incrementally port code from old to new Python while maintaining functionality
4. **CI/CD Pipelines**: Run multi-version validation without Docker containers
5. **Scientific Workflows**: Combine specialized tools that require different Python versions

## Comparison to Traditional Approaches

| Approach | Setup Time | Isolation | Overhead | Flexibility |
|----------|------------|-----------|----------|-------------|
| **Docker** | Minutes | Full | High | Low |
| **Conda Envs** | Minutes | Medium | Medium | Medium |
| **venv + pip** | Minutes | Medium | Low | Low |
| **OmniPkg** | Seconds | Full | Minimal | High |

OmniPkg achieves Docker-level isolation with venv-level overhead, making it ideal for rapid development and testing workflows.
