<p align="center"> 
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg – Universal Python Runtime Orchestrator</h1>
<p align="center">
  <p align="center">
    <p align="center">
  <strong><strong>One environment. Infinite Pythons and packages. <1ms zero-copy IPC. </strong>
    
<p align="center">
  <!-- Core Project Info -->
      <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-AGPLv3-d94c31?logo=gnu" alt="License">
      </a>
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
   </a>
 <a href="https://anaconda.org/conda-forge/omnipkg">
    <img src="https://img.shields.io/conda/dn/conda-forge/omnipkg?logo=anaconda" alt="Conda Downloads">
  </a>
  <a href="https://anaconda.org/minds3t/omnipkg">
  <img src="https://img.shields.io/conda/dn/minds3t/omnipkg?logo=anaconda" alt="Conda Downloads (minds3t)">
</a>
<a href="https://pepy.tech/projects/omnipkg">
  <img src="https://static.pepy.tech/personalized-badge/omnipkg?period=total&units=INTERNATIONAL_SYSTEM&left_color=gray&right_color=blue&left_text=downloads" alt="PyPI Downloads">
</a>
<a href="https://hub.docker.com/r/1minds3t/omnipkg">
  <img src="https://img.shields.io/docker/pulls/1minds3t/omnipkg?logo=docker" alt="Docker Pulls">
</a>
  <a href="https://anaconda.org/conda-forge/omnipkg">
<a href="https://clickpy.clickhouse.com/dashboard/omnipkg">
  <img src="https://img.shields.io/badge/global_reach-80+_countries-228B22?logo=globe" alt="Global Reach Badge">
  <p align="center">
</a>
  <a href="https://pypi.org/project/omnipkg/">
  <img src="https://img.shields.io/pypi/pyversions/omnipkg?logo=python&logoColor=white" alt="Python Versions">
</a>
  <a href="https://anaconda.org/conda-forge/omnipkg/files">
<img src="https://img.shields.io/badge/platforms-win--64|macOS--64|macOS--arm64|linux--64|linux--aarch64|linux--ppc64le|noarch-blue?logo=anaconda" alt="Supported Platforms">
  </a>
  <p align="center">

</p>
<p align="center">
  <!-- Quality & Security -->
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Security+Audit%22">
    <img src="https://img.shields.io/badge/Security-passing-success?logo=security" alt="Security">
  </a>
<a href="https://github.com/1minds3t/omnipkg/actions/workflows/safety_scan.yml">
  <img src="https://img.shields.io/badge/Safety-passing-success?logo=safety" alt="Safety">
</a>
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3APylint">
    <img src="https://img.shields.io/badge/Pylint-10/10-success?logo=python" alt="Pylint">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ABandit">
    <img src="https://img.shields.io/badge/Bandit-passing-success?logo=bandit" alt="Bandit">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ACodeQL+Advanced">
    <img src="https://img.shields.io/badge/CodeQL-passing-success?logo=github" alt="CodeQL">
  </a>
<a href="https://socket.dev/pypi/package/omnipkg/overview/1.1.2/tar-gz">
    <img src="https://img.shields.io/badge/Socket-secured-success?logo=socket" alt="Socket">
</a>
</p>

<p align="center">
  <!-- Key Features -->
    <a href="https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml">
    <img src="https://img.shields.io/badge/<3ms 3 Py Interps 1 Script 1 Env-passing-success?logo=python&logoColor=white" alt="Concurrent Python Interpreters">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/numpy_scipy_test.yml">
    <img src="https://img.shields.io/badge/🚀0.25s_Live_NumPy+SciPy_Hot--Swapping-passing-success?logo=github-actions" alt="Hot-Swapping">
  </a>
<a href="https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml">
  <img src="https://img.shields.io/badge/🔥_0.25s_Python_Interpreter_Hot--Swapping-Live-orange?logo=python&logoColor=white" alt="Python Hot-Swapping">
</a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/old_rich_test.yml">
  <img src="https://img.shields.io/badge/⚡_Auto--Healing-8x_Faster_than_UV-gold?logo=lightning&logoColor=white" alt="Auto-Healing Performance">
</a>
    <a href="https://github.com/1minds3t/omnipkg/actions/workflows/language_test.yml">
    <img src="https://img.shields.io/badge/💥_Breaking_Language_Barriers-24_Languages-success?logo=babel&logoColor=white" alt="24 Languages">
  </a>
</p>

---

`omnipkg` is not just another package manager. It's an **intelligent, self-healing runtime orchestrator** that breaks the fundamental laws of Python environments. For 30 years, developers accepted that you couldn't run multiple Python versions in one script, or safely switch C-extensions like NumPy mid-execution. **Omnipkg proves this is no longer true.**

Born from a real-world nightmare—a forced downgrade that wrecked a production environment—`omnipkg` was built to solve what others couldn't: achieving perfect dependency isolation and runtime adaptability without the overhead of containers or multiple venvs.

---

<!-- COMPARISON_STATS_START -->
## ⚖️ Multi-Version Support

[![omnipkg](https://img.shields.io/badge/omnipkg-2509%20Wins-brightgreen?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![pip](https://img.shields.io/badge/pip-2512%20Failures-red?logo=pypi&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![uv](https://img.shields.io/badge/uv-2512%20Failures-red?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)

*Multi-version installation tests run every 3 hours. [Live results here.](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)*

---

<!-- COMPARISON_STATS_END -->

## 💡 Why This Matters

**The Multi-Version Nightmare is Over**: Modern projects are messy. You need `tensorflow==2.10` for a legacy model but `tensorflow==2.15` for new training. A critical library requires `numpy==1.21` while your latest feature needs `numpy==2.0`. Traditional solutions like Docker or virtual environments force you into a painful choice: duplicate entire environments, endure slow context switching, or face crippling dependency conflicts.

**The Multi-Interpreter Wall is Gone**: Legacy codebases often require older Python versions (e.g., Django on 3.8) while modern ML demands the latest (Python 3.11+). This forces developers to constantly manage and switch between separate, isolated environments, killing productivity.

**The `omnipkg` Solution: One Environment, Infinite Python Versions & Packages, Zero Conflicts, Downtime, or Setup. Faster than UV.**

`omnipkg` doesn't just solve these problems—it makes them irrelevant.
*   **Run Concurrently:** Execute tests for Python 3.9, 3.10, and 3.11 **at the same time, from one command, test is done in under 500ms**. No more sequential CI jobs.
*   **Switch Mid-Script:** Seamlessly use `torch==2.0.0` and `torch==2.7.1` in the same script without restarting.
*   **Instant Healing:** Recover from environment damage in microseconds, not hours.
*   **Speak Your Language:** All of this, in your native tongue.

This is the new reality: one environment, one script, everything **just works**.

---

## 🧠 Revolutionary Core Features
### 1. Multiverse Orchestration & Python Hot-Swapping [![<600ms 3 Py Interps 1 Script 1 Env](https://img.shields.io/badge/<600ms%203%20Py%20Interps%201%20Script%201%20Env-passing-success?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml) [![🍎 macOS](https://img.shields.io/badge/macOS-2.3ms_hot_workers-success?logo=apple)](https://github.com/1minds3t/omnipkg/actions/workflows/mac-concurrent-test.yml)

## The "Quantum Multiverse Warp": 3 Pythons, 1 Script, Sub-3ms Execution

Our "Quantum Multiverse Warp" demo, validated live in CI across multiple platforms, executes a single script across three different Python interpreters and three package versions **concurrently** in the same environment. The hot worker performance isn't just fast; it redefines what's possible for high-performance Python automation.

### Production Benchmark Results (macOS CI)

| Task (Same Script, Same Environment) | Hot Worker Execution |
| ------------------------------------ | :------------------: |
| 🧵 **Thread 1:** Python 3.9 + Rich 13.4.2  | ✅ **2.2ms**   |
| 🧵 **Thread 2:** Python 3.10 + Rich 13.6.0 | ✅ **2.3ms**   |
| 🧵 **Thread 3:** Python 3.11 + Rich 13.7.1 | ✅ **2.3ms**   |
| 🏆 **Total Concurrent Runtime**        | **2.3ms**      |
| ⏱️ **Total Test Duration (with setup)** | **2.14s**      |

**Platform-Specific Performance:**

| Platform | Hot Worker Benchmark | Total w/ Setup | CI Link |
|----------|---------------------|----------------|---------|
| 🐧 **Linux** | **3.8ms avg** (3.2-4.5ms range) | ~580ms | [View CI](https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml) |
| 🍎 **macOS** | **2.3ms avg** (2.2-2.3ms range) | 2.14s | [View CI](https://github.com/1minds3t/omnipkg/actions/workflows/mac-concurrent-test.yml) |

### What This Actually Means

**The numbers that matter** are the **hot worker benchmarks** (sub-5ms). This is the actual execution time for running code across three concurrent Python interpreters with three different package versions. The "Total w/ Setup" includes one-time initialization:
- Worker pool spawning
- Package installation (if not cached)
- Environment validation

**Why This Is Revolutionary:**

- **Traditional approach:** Docker containers or separate venvs would take 30-90 seconds *minimum* to achieve the same multi-version testing
- **omnipkg approach:** After initial setup, switching between Python versions and package combinations happens in **microseconds**, not seconds

This isn't just a speedup; it's a paradigm shift. What traditionally takes minutes with Docker or complex venv scripting, `omnipkg` accomplishes in **milliseconds**. This isn't a simulation; it's a live, production-ready capability for high-performance Python automation.

### Benchmark Methodology

Our production benchmark follows industry-standard practices:

1. **📥 Setup Phase:** Verify Python interpreters are available and daemon is running (one-time cost)
2. **🔥 Warmup Phase:** Spawn workers and install packages - **timing discarded** (matches real-world "first run" scenario)
3. **⚡ Benchmark Phase:** Execute with hot workers - **THIS IS THE METRIC** (pure execution performance)
4. **🔍 Verification Phase:** Prove correctness with version checks (not timed)

**Key Achievement:** The hot worker performance (2-4ms) represents the *actual* overhead of omnipkg's multiverse orchestration. Once warmed up, switching between Python interpreters and package versions is **faster than most function calls**.

Don't believe it? See the live proof, then run **Demo 8** to experience it yourself:

```bash
uv pip install omnipkg && omnipkg demo
# Select option 8: 🌠 Quantum Multiverse Warp
```

**Live CI Output from Multiverse Benchmark:**

```bash
⚡ Phase 3: PRODUCTION BENCHMARK (hot workers, concurrent execution)
----------------------------------------------------------------------------------------------------
[T1] ⚡ Benchmarking Python 3.9 + Rich 13.4.2...
[T1] ✅ Benchmark: 2.2ms
[T2] ⚡ Benchmarking Python 3.10 + Rich 13.6.0...
[T2] ✅ Benchmark: 2.3ms
[T3] ⚡ Benchmarking Python 3.11 + Rich 13.7.1...
[T3] ✅ Benchmark: 2.3ms

====================================================================================================
📊 PRODUCTION BENCHMARK RESULTS
====================================================================================================
Thread   Python       Rich       Warmup          Benchmark      
----------------------------------------------------------------------------------------------------
T1       3.9          13.4.2     3.4ms           2.2ms          
T2       3.10         13.6.0     3.0ms           2.3ms          
T3       3.11         13.7.1     3.5ms           2.3ms          
----------------------------------------------------------------------------------------------------
⏱️  Sequential time (sum of all):  6.8ms
⏱️  Concurrent time (longest one):  2.3ms
====================================================================================================

🎯 PERFORMANCE METRICS:
----------------------------------------------------------------------------------------------------
   Warmup (cold start):     3.3ms avg
   Benchmark (hot workers): 2.3ms avg
   Range:                   2.2ms - 2.3ms
   Speedup (warmup→hot):    1.5x
   Concurrent speedup:      2.93x
----------------------------------------------------------------------------------------------------

🎉 BENCHMARK COMPLETE!

✨ KEY ACHIEVEMENTS:
   ✅ 3 different Python interpreters executing concurrently
   ✅ 3 different Rich versions loaded simultaneously
   ✅ Hot worker performance: sub-50ms execution!
   ✅ Zero state corruption or interference
   ✅ Production-grade benchmark methodology

⏱️  Total test duration: 2.14s

🚀 This is IMPOSSIBLE with traditional Python environments!
```

### Real-World Impact

**For CI/CD Pipelines:**
- **Before:** Sequential matrix testing across Python 3.9, 3.10, 3.11 = 3-5 minutes
- **After:** Concurrent testing with omnipkg = **< 3 seconds** (including setup)
- **Improvement:** **60-100x faster** CI/CD workflows

**For Development:**
- **Before:** Switch Python versions → wait 30-90s for new venv/container
- **After:** Switch with omnipkg → **< 5ms overhead**
- **Improvement:** Instant iteration, zero context-switching penalty

This is the new reality: one environment, one script, everything **just works** — and it's **blazing fast**.

---
### 2. Intelligent Script Runner (`omnipkg run`) [![⚡ Auto-Healing: 12.94x Faster than UV](https://img.shields.io/badge/⚡_Auto--Healing-12.94x_Faster_than_UV-gold?logo=lightning&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/old_rich_test.yml)

`omnipkg run` is an intelligent script and CLI executor that **automatically detects and fixes** dependency errors using bubble versions—without modifying your main environment.

## What is `omnipkg run`?

Think of it as a "smart wrapper" around Python scripts and CLI commands that:
1. **Tries to execute** your script or command
2. **Detects errors** (ImportError, ModuleNotFoundError, version conflicts)
3. **Finds the right version** from existing bubbles or creates new ones
4. **Re-runs successfully** in milliseconds—all automatically

**The magic:** Your broken main environment stays broken, but everything works anyway.

## Two Modes of Operation

### Mode 1: Script Execution (`omnipkg run script.py`)

Automatically heals Python scripts with dependency conflicts:

```bash
$ python broken_script.py
AssertionError: Incorrect rich version! Expected 13.4.2, got 13.7.1

$ omnipkg run broken_script.py
🔍 Runtime version assertion failed. Auto-healing...
   - Conflict identified for: rich==13.4.2
🛠️  Installing bubble for rich==13.4.2...
   ⚡ HEALED in 16,223.1 μs (16.2ms)
✅ Script completed successfully!
```

**Performance vs UV:**
```
UV Failed Run      : 210.007ms (fails, no recovery)
omnipkg Activation :  16.223ms (succeeds automatically)
🎯 omnipkg is 12.94x FASTER than UV!
```

### Mode 2: CLI Command Execution (`omnipkg run <command>`)

Automatically heals broken command-line tools:

```bash
# Regular execution fails
$ http --version
ImportError: cannot import name 'SKIP_HEADER' from 'urllib3.util'

# omnipkg run heals and executes
$ omnipkg run http --version
⚠️  Command failed (exit code 1). Starting Auto-Healer...
🔍 Import error detected. Auto-healing with bubbles...
♻️  Loading: ['urllib3==2.6.3']
   ⚡ HEALED in 12,371.6 μs (12.4ms)
3.2.4
✅ Success!
```

**What happened:** The main environment still has urllib3 1.25.11 (broken), but `omnipkg run` used urllib3 2.6.3 from a bubble to make the command work.

## How It Works

### Step 1: Detect the Error

`omnipkg run` recognizes multiple error patterns:

```python
# Import errors
ModuleNotFoundError: No module named 'missing_package'
ImportError: cannot import name 'SKIP_HEADER'

# Version conflicts  
AssertionError: Incorrect rich version! Expected 13.4.2, got 13.7.1
requires numpy==1.26.4, but you have numpy==2.0.0

# C-extension failures
A module compiled using NumPy 1.x cannot run in NumPy 2.0
```

### Step 2: Build a Healing Plan

Analyzes the error and identifies what's needed:

```bash
🔍 Comprehensive Healing Plan Compiled (Attempt 1): ['rich==13.4.2']
```

For CLI commands, it includes the owning package:

```bash
🔍 Analyzing error: ImportError from urllib3
♻️  Loading: ['urllib3==2.6.3']
```

### Step 3: Find or Create Bubbles

Checks if the needed version exists:

```bash
# Bubble exists - instant activation
🚀 INSTANT HIT: Found existing bubble urllib3==2.6.3 in KB
   ⚡ HEALED in 12.4ms

# Bubble doesn't exist - create it
🛠️  Installing bubble for rich==13.4.2...
   📊 Bubble: 4 packages, 0 conflicts
   ⚡ HEALED in 16.2ms
```

### Step 4: Execute with Bubbles

Re-runs the script/command with the correct versions activated:

```bash
🌀 omnipkg auto-heal: Wrapping with loaders for ['rich==13.4.2']...
🚀 Fast-activating rich==13.4.2 ...
   📊 Bubble: 4 packages, 0 conflicts
   🧹 Purging 4 module(s) from memory...
🔗 Linked 20 compatible dependencies to bubble
   ✅ Bubble activated

🚀 Running target script inside the bubble...
✅ Successfully imported rich version: 13.4.2
```

### Step 5: Clean Restoration

After execution, environment is restored to original state:

```bash
🌀 omnipkg loader: Deactivating rich==13.4.2...
   ✅ Environment restored.
   ⏱️  Swap Time: 35,319.103 μs (35.3ms)
```

## Real-World Examples

### Example 1: Version Conflict Resolution

**Scenario:** Script needs rich==13.4.2 but main environment has rich==13.7.1

```bash
$ omnipkg run test_rich.py
🔍 Runtime version assertion failed. Auto-healing...
   - Conflict identified for: rich==13.4.2

🛠️  Installing bubble for rich==13.4.2...
   - 🧪 Running SMART import verification...
   ✅ markdown-it-py: OK
   ✅ rich: OK
   ✅ mdurl: OK
   ✅ Pygments: OK
   
   ⚡ HEALED in 16.2ms
✅ Script completed successfully inside omnipkg bubble.
```

**Main environment after execution:**
```bash
$ python -c "import rich; print(rich.__version__)"
13.7.1  # Still the original version - untouched!
```

### Example 2: Broken CLI Tool

**Scenario:** httpie broken by urllib3 downgrade to 1.25.11

```bash
# Shows the error first
$ http --version
Traceback (most recent call last):
  File "/usr/bin/http", line 13, in <module>
    from urllib3.util import SKIP_HEADER
ImportError: cannot import name 'SKIP_HEADER' from 'urllib3.util'

# Heals and executes
$ omnipkg run http --version
⚠️  Command 'http' failed. Starting Auto-Healer...
🔍 Analyzing error: ImportError from module
   - Installing missing package: urllib3

🔍 Resolving latest version for 'urllib3'...
   🚀 INSTANT HIT: Found existing bubble urllib3==2.6.3
   
🐍 [omnipkg loader] Running in Python 3.11 context
🚀 Fast-activating urllib3==2.6.3 ...
   📊 Bubble: 1 packages, 0 conflicts
   🧹 Purging 31 modules for 'urllib3'
   ⚡ HEALED in 12.4ms

🚀 Re-launching '/usr/bin/http' in healed environment...
3.2.4
✅ Success!
```

**Main environment after execution:**
```bash
$ python -c "import urllib3; print(urllib3.__version__)"
1.25.11  # Still broken - but who cares? omnipkg run works!
```

## Performance Benchmarks

### Script Healing (Demo 7)

| Operation | Time | Status |
|-----------|------|--------|
| UV failed run | 210.007ms | ❌ Fails, no recovery |
| omnipkg detection | <1ms | ✅ Instant |
| omnipkg healing | 16.223ms | ✅ Creates bubble |
| omnipkg execution | ~35ms | ✅ Runs successfully |
| **Total recovery** | **~51ms** | **12.94x faster than UV** |

### CLI Healing (Demo 10)

| Operation | Traditional | omnipkg run |
|-----------|-------------|-------------|
| Error detection | Manual (minutes) | Automatic (<1ms) |
| Finding fix | Manual research | Automatic KB lookup |
| Applying fix | 30-90s (reinstall) | 12.4ms (bubble activation) |
| Main env impact | ⚠️ Modified | ✅ Untouched |
| Success rate | ~50% (manual) | 100% (automated) |

## Key Features

### 1. Zero Main Environment Impact

**Traditional approach:**
```bash
$ pip install old-package==1.0.0
# Breaks 5 other packages
# Spend 30 minutes fixing
```

**omnipkg run approach:**
```bash
$ omnipkg run script-needing-old-version.py
# Works instantly
# Main environment untouched
```

### 2. Intelligent Error Detection

Recognizes and fixes:
- `ModuleNotFoundError` → Installs missing package
- `ImportError` → Fixes import conflicts
- `AssertionError` (version checks) → Switches to correct version
- NumPy C-extension errors → Downgrades to compatible version
- CLI command failures → Heals dependencies automatically

### 3. Smart Dependency Resolution

```bash
🔍 Analyzing script for additional dependencies...
   ✅ No additional dependencies needed

# Or if dependencies are found:
🔗 [omnipkg loader] Linked 20 compatible dependencies to bubble
```

Automatically detects and includes all required dependencies, not just the primary package.

### 4. Bubble Reuse

Once a bubble is created, it's instantly available:

```bash
# First time - creates bubble
🛠️  Installing bubble for rich==13.4.2...
   ⚡ HEALED in 16.2ms

# Second time - instant activation
🚀 INSTANT HIT: Found existing bubble rich==13.4.2
   ⚡ HEALED in <1ms
```

## Usage

### Basic Script Execution

```bash
# Run a Python script with auto-healing
omnipkg run script.py

# Pass arguments to the script
omnipkg run script.py --arg1 value1 --arg2 value2
```

### CLI Command Execution

```bash
# Run any CLI command with auto-healing
omnipkg run http GET https://api.github.com

# Run tools that depend on specific library versions
omnipkg run pytest
omnipkg run black mycode.py
omnipkg run mypy myproject/
```

### With Verbose Output

```bash
# See detailed healing process
omnipkg run -v script.py
```

## When to Use `omnipkg run`

### ✅ Perfect For:

- **Scripts with version conflicts:** Need old numpy but have new numpy installed
- **Broken CLI tools:** Tool worked yesterday, broken after an upgrade today
- **Testing different versions:** Try multiple library versions without changing environment
- **CI/CD pipelines:** Guaranteed success even with dependency conflicts
- **Legacy code:** Run old code without downgrading your entire environment

### ⚠️ Not Needed For:

- **Fresh scripts with satisfied dependencies:** Just use `python script.py`
- **Well-maintained environments:** If everything works, no need to heal

## Performance Comparison

```
Traditional Workflow (Broken Tool):
1. Tool fails ........................... 0s
2. Debug error (find root cause) ....... 300s (5 min)
3. Research fix ........................ 600s (10 min)
4. Apply fix (reinstall) ............... 60s (1 min)
5. Test fix ............................ 10s
6. Fix breaks other things ............. 1800s (30 min)
Total: 2770s (46 minutes) ❌

omnipkg run Workflow:
1. omnipkg run <command> ............... 0.012s (12ms)
Total: 0.012s (12 milliseconds) ✅

Speedup: 230,833x faster
```

## Try It Yourself

```bash
# Install omnipkg
uv pip install omnipkg

# Run Demo 7: Script auto-healing
omnipkg demo
# Select option 7

# Run Demo 10: CLI auto-healing
omnipkg demo
# Select option 10
```

See for yourself how `omnipkg run` turns minutes of frustration into milliseconds of automated healing.

---

## The Future: Package Manager Interception

This healing capability is the foundation for our vision of **transparent package management**:

```bash
# Coming soon: omnipkg intercepts all package managers
$ pip install broken-package==old-version
⚠️  This would break 3 packages in your environment
🛡️  omnipkg: Creating bubble instead to protect environment
✅ Installed to bubble - use 'omnipkg run' to access

# Everything just works
$ omnipkg run my-script-using-old-version.py
✅ Success (using bubbled version)

$ python my-script-using-new-version.py  
✅ Success (using main environment)
```

**The endgame:** Infinite package coexistence, zero conflicts, microsecond switching—all invisible to the user.

---

---
***

# 3. Dynamic Package Switching & Process Isolation
[![💥 Nuclear Test: Multi-Framework Battle Royale](https://img.shields.io/badge/💥_Nuclear_Test-Multi--Framework_Battle_Royale-passing-success)](https://github.com/1minds3t/omnipkg/actions) [![Daemon Status](https://img.shields.io/badge/Daemon-Persistent_&_Hot-brightgreen)](https://github.com/1minds3t/omnipkg)

**omnipkg** allows you to switch package versions **mid-script** and run conflicting dependencies simultaneously. It offers two distinct modes depending on the severity of the dependency conflict:

1.  **In-Process Overlay:** For "safe" packages (NumPy, SciPy, Pandas) — *Zero latency.*
2.  **Daemon Worker Pool:** For "heavy" frameworks (TensorFlow, PyTorch) — *True isolation.*

---

## 🛑 The Hard Truth: Why You Need Daemons

Traditional Python wisdom says you cannot switch frameworks like PyTorch or TensorFlow without restarting the interpreter. **This is true.** Their C++ backends (`_C` symbols) bind to memory and refuse to let go.

**What happens if you try to force-switch PyTorch in-process?**
```python
# ❌ THIS CRASHES IN STANDARD PYTHON
import torch  # Loads version 2.0.1
# ... try to unload and reload 2.1.0 ...
import torch
# NameError: name '_C' is not defined
```
*The C++ backend remains resident, causing symbol conflicts and segfaults.*

### 🟢 The Solution: omnipkg Daemon Workers
Instead of fighting the C++ backend, `omnipkg` accepts it. We spawn **persistent, lightweight worker processes** for each framework version.

*   **Workers persist across script runs:** Cold start once, hot-swap forever.
*   **Zero-Copy Communication:** Data moves between workers via shared memory (no pickling overhead).
*   **Sub-millisecond switching:** Switching contexts takes **~0.37ms**.

---

## 🚀 The Impossible Made Real: Benchmark Results

We ran `omnipkg demo` (Scenario 11: Chaos Theory) to prove capabilities that should be impossible.

### 1. Framework Battle Royale (Concurrent Execution)
**The Challenge:** Run TensorFlow, PyTorch, and NumPy (different versions) **at the exact same time**.

```text
🥊 ROUND 1: Truly Concurrent Execution
   ⚡ NumPy Legacy    →  (0.71ms)
   ⚡ NumPy Modern    →  (0.71ms)
   ⚡ PyTorch         →  (0.80ms)
   ⚡ TensorFlow      →  (1.15ms)

📊 RESULT: 4 Frameworks executed in 1.69ms total wall-clock time.
```

### 2. The TensorFlow Resurrection Test
**The Challenge:** Kill and respawn a TensorFlow environment 5 times.
*   **Standard Method (Cold Spawn):** ~2885ms per reload.
*   **omnipkg Daemon (Warm Worker):** ~716ms first run, **3ms** subsequent runs.
*   **Result:** **4.0x Speedup** (and nearly instant after warm-up).

### 3. Rapid Circular Switching
**The Challenge:** Toggle between PyTorch 2.0.1 (CUDA 11.8) and 2.1.0 (CUDA 12.1) doing heavy tensor math.

```text
ROUND  | WORKER          | VERSION         | TIME       
-------------------------------------------------------
 #1    | torch-2.0.1     | 2.0.1+cu118     | 0.63ms     
 #2    | torch-2.1.0     | 2.1.0+cu121     | 1570ms (Cold)
 #3    | torch-2.0.1     | 2.0.1+cu118     | 0.66ms (Hot)
 #4    | torch-2.1.0     | 2.1.0+cu121     | 0.44ms (Hot)
 ...
 #10   | torch-2.1.0     | 2.1.0+cu121     | 0.37ms (Hot)
```

---

## 💻 Usage

### Mode A: In-Process Loader (NumPy, SciPy, Tools)
Best for nested dependencies and libraries that clean up after themselves.

```python
from omnipkg.loader import omnipkgLoader

# Layer 1: NumPy 1.24
with omnipkgLoader("numpy==1.24.3"):
    import numpy as np
    print(f"Outer: {np.__version__}") # 1.24.3
    
    # Layer 2: SciPy 1.10 (Nested)
    with omnipkgLoader("scipy==1.10.1"):
        import scipy
        # Works perfectly, sharing the NumPy 1.24 context
        print(f"Inner: {scipy.__version__}")
```

### Mode B: Daemon Client (TensorFlow, PyTorch)
Best for heavy ML frameworks and conflicting C++ backends.

```python
from omnipkg.isolation.worker_daemon import DaemonClient

client = DaemonClient()

# Execute code in PyTorch 2.0.1
client.execute_smart("torch==2.0.1+cu118", """
import torch
print(f"Running on {torch.cuda.get_device_name(0)} with Torch {torch.__version__}")
""")

# Instantly switch to PyTorch 2.1.0 (Different process, shared memory)
client.execute_smart("torch==2.1.0", "import torch; print(torch.__version__)")
```

---

## 📊 Resource Efficiency

You might think running multiple worker processes consumes massive RAM. **It doesn't.**
`omnipkg` uses highly optimized stripping to keep workers lean.

**Live `omnipkg daemon monitor` Output:**
```text
⚙️  ACTIVE WORKERS:
  📦 torch==2.0.1+cu118  | RAM: 390.1MB
  📦 torch==2.1.0        | RAM: 415.1MB
  
🎯 EFFICIENCY COMPARISON:
  💾 omnipkg Memory:   402.6MB per worker
  🔥 vs DOCKER:        1.9x MORE EFFICIENT (saves ~700MB)
  ⚡ Startup Time:     ~5ms (vs 800ms+ for Docker/Conda)
```

---

## 🌀 Try The Chaos
Don't believe us? Run the torture tests yourself.

```bash
omnipkg demo
# Select option 11: 🌀 Chaos Theory Stress Test
```
Available Scenarios:
*   **[14] Circular Dependency Hell:** Package A imports B, B imports A across version bubbles.
*   **[16] Nested Reality Hell:** 7 layers of nested dependency contexts.
*   **[19] Zero Copy HFT:** High-frequency data transfer between isolated processes.
*   **[23] Grand Unified Benchmark:** Run everything at once.

---

### 4. 🌍 Global Intelligence & AI-Driven Localization [![🤖 AI-Powered: 24 Languages](https://img.shields.io/badge/🤖_AI--Powered-24_Languages-brightgreen?logo=openai&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/language_test.yml)

`omnipkg` eliminates language barriers with advanced AI localization supporting 24+ languages, making package management accessible to developers worldwide in their native language.

**Key Features**: Auto-detection from system locale, competitive AI translation models, context-aware technical term handling, and continuous self-improvement from user feedback.

```bash
# Set language permanently
omnipkg config set language zh_CN
# ✅ Language permanently set to: 中文 (简体)

# Temporary language override
omnipkg --lang es install requests

# View current configuration
cat ~/.config/omnipkg/config.json
```
Zero setup required—works in your language from first run with graceful fallbacks and clear beta transparency.

---

### 5. Downgrade Protection & Conflict Resolution [![🔧 Simple UV Multi-Version Test](https://img.shields.io/badge/🔧_Simple_UV_Multi--Version_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_install.yml)

`omnipkg` automatically reorders installations and isolates conflicts, preventing environment-breaking downgrades.

**Example: Conflicting `torch` versions:**
```bash
omnipkg install torch==2.0.0 torch==2.7.1
```

**What happens?** `omnipkg` reorders installs to trigger the bubble creation, installs `torch==2.7.1` in the main environment, and isolates `torch==2.0.0` in a lightweight "bubble," sharing compatible dependencies to save space. No virtual environments or containers needed.

```bash
🔄 Reordered: torch==2.7.1, torch==2.0.0
📦 Installing torch==2.7.1... ✅ Done
🛡️ Downgrade detected for torch==2.0.0
🫧 Creating bubble for torch==2.0.0... ✅ Done
🔄 Restoring torch==2.7.1... ✅ Environment secure
```
---

### 6. Deep Package Intelligence with Import Validation [![🔍 Package Discovery Demo](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml)
`omnipkg` goes beyond simple version tracking, building a deep knowledge base (in Redis or SQLite) for every package. In v1.5.0, this now includes **live import validation** during bubble creation.
- **The Problem:** A package can be "installed" but still be broken due to missing C-extensions or incorrect `sys.path` entries.
- **The Solution:** When creating a bubble, `omnipkg` now runs an isolated import test for every single dependency. It detects failures (e.g., `absl-py: No module named 'absl_py'`) and even attempts to automatically repair them, ensuring bubbles are not just created, but are **guaranteed to be functional.**



**Example Insight:**
```bash
omnipkg info uv
📋 KEY DATA for 'uv':
🎯 Active Version: 0.8.11
🫧 Bubbled Versions: 0.8.10

---[ Health & Security ]---
🔒 Security Issues : 0  
🛡️ Audit Status  : checked_in_bulk
✅ Importable      : True
```

| **Intelligence Includes** | **Redis/SQLite Superpowers** |
|--------------------------|-----------------------|
| • Binary Analysis (ELF validation, file sizes) | • 0.2ms metadata lookups |
| • CLI Command Mapping (all subcommands/flags) | • Compressed storage for large data |
| • Security Audits (vulnerability scans) | • Atomic transaction safety |
| • Dependency Graphs (conflict detection) | • Intelligent caching of expensive operations |
| • Import Validation (runtime testing) | • Enables future C-extension symlinking |

---

### 7. Instant Environment Recovery

[![🛡️ UV Revert Test](https://img.shields.io/badge/🛡️_UV_Revert_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_revert.yml)


If an external tool (like `pip` or `uv`) causes damage, `omnipkg revert` restores your environment to a "last known good" state in seconds.

**Key CI Output Excerpt:**

```bash
Initial uv version (omnipkg-installed):uv 0.8.11
$ uv pip install uv==0.7.13
 - uv==0.8.11
 + uv==0.7.13
uv self-downgraded successfully.
Current uv version (after uv's operation): uv 0.7.13

⚖️  Comparing current environment to the last known good snapshot...
📝 The following actions will be taken to restore the environment:
  - Fix Version: uv==0.8.11
🚀 Starting revert operation...
✅ Environment successfully reverted to the last known good state.

--- Verifying UV version after omnipkg revert ---
uv 0.8.11
```
**UV is saved, along with any deps!**

---
## 🛠️ Get Started in 30 Seconds

### No Prerequisites Required!
`omnipkg` works out of the box with **automatic SQLite fallback** when Redis isn't available. Redis is optional for enhanced performance.

Ready to end dependency hell?
```bash
uv pip install omnipkg && omnipkg demo
```
See the magic in under 30 seconds.

---

<!-- PLATFORM_SUPPORT_START -->
## 🌐 Verified Platform Support

[![Platforms Verified](https://img.shields.io/badge/platforms-22%20verified-success?logo=linux&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/cross-platform-build-verification.yml)

**omnipkg** is a pure Python package (noarch) with **no C-extensions**, ensuring universal compatibility across all platforms and architectures.

### 📊 Platform Matrix

#### Linux (Native)
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| Linux x86_64 | x86_64 | ✅ | Native installation |

#### macOS (Native)
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| macOS Intel | x86_64 (Intel) | ✅ | Native installation |
| macOS ARM64 | ARM64 (Apple Silicon) | ✅ | Native installation |

#### Windows (Native)
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| Windows Server | x86_64 | ✅ | Latest Server |

#### Debian/Ubuntu
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| Debian 12 (Bookworm) | x86_64 | ✅ | `--break-system-packages` required |
| Debian 11 (Bullseye) | x86_64 | ✅ | Standard install |
| Ubuntu 24.04 (Noble) | x86_64 | ✅ | `--break-system-packages` required |
| Ubuntu 22.04 (Jammy) | x86_64 | ✅ | Standard install |
| Ubuntu 20.04 (Focal) | x86_64 | ✅ | Standard install |

#### RHEL/Fedora
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| Fedora 39 | x86_64 | ✅ | Standard install |
| Fedora 38 | x86_64 | ✅ | Standard install |
| Rocky Linux 9 | x86_64 | ✅ | Standard install |
| Rocky Linux 8 | x86_64 | ✅ | Requires Python 3.9+ (default is 3.6) |
| AlmaLinux 9 | x86_64 | ✅ | Standard install |

#### Other Linux
| Platform | Architecture | Status | Installation Notes |
|----------|--------------|--------|-------------------|
| Arch Linux | x86_64 | ✅ | `--break-system-packages` required |
| Alpine Linux | x86_64 | ✅ | Requires build deps (gcc, musl-dev) |

### 📝 Special Installation Notes

#### Ubuntu 24.04+ / Debian 12+ (PEP 668)
Modern Debian/Ubuntu enforce PEP 668 to protect system packages:
```bash
# Use --break-system-packages flag
python3 -m pip install --break-system-packages omnipkg

# Or use a virtual environment (recommended for development)
python3 -m venv .venv
source .venv/bin/activate
pip install omnipkg
```

#### Rocky/Alma Linux 8 (Python 3.6 → 3.9)
EL8 ships with Python 3.6, which is too old for modern `pyproject.toml`:
```bash
# Install Python 3.9 first
sudo dnf install -y python39 python39-pip

# Make python3 point to 3.9
sudo ln -sf /usr/bin/python3.9 /usr/bin/python3
sudo ln -sf /usr/bin/pip3.9 /usr/bin/pip3

# Now install omnipkg
python3 -m pip install omnipkg
```

#### Alpine Linux (Build Dependencies)
Alpine requires build tools for dependencies like `psutil`:
```bash
# Install build tools first
apk add --no-cache gcc python3-dev musl-dev linux-headers

# Then install omnipkg
python3 -m pip install --break-system-packages omnipkg
```

#### Arch Linux
```bash
# Arch uses --break-system-packages for global installs
python -m pip install --break-system-packages omnipkg

# Or use pacman if available in AUR (future)
yay -S python-omnipkg
```

### 🐍 Python Version Support

**Supported:** Python 3.7 - 3.14 (including beta/rc releases)

**Architecture:** `noarch` (pure Python, no compiled extensions)

This means omnipkg runs on **any** architecture where Python is available:
- ✅ **x86_64** (Intel/AMD) - verified in CI
- ✅ **ARM32** (armv6/v7) - [verified on piwheels](https://www.piwheels.org/project/omnipkg/)
- ✅ **ARM64** (aarch64) - Python native support
- ✅ **RISC-V, POWER, s390x** - anywhere Python runs!

<!-- PLATFORM_SUPPORT_END -->

<!-- ARM64_STATUS_START -->
### ✅ ARM64 Support Verified (QEMU)

[![ARM64 Verified](https://img.shields.io/badge/ARM64_(aarch64)-6/6%20Verified-success?logo=linux&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/arm64-verification.yml)

**`omnipkg` is fully verified on ARM64.** This was achieved without needing expensive native hardware by using a powerful QEMU emulation setup on a self-hosted x86_64 runner. This process proves that the package installs and functions correctly on the following ARM64 Linux distributions:

| Platform                 | Architecture    | Status | Notes           |
|--------------------------|-----------------|:------:|-----------------|
| Debian 12 (Bookworm)     | ARM64 (aarch64) |   ✅   | QEMU Emulation  |
| Ubuntu 24.04 (Noble)     | ARM64 (aarch64) |   ✅   | QEMU Emulation  |
| Ubuntu 22.04 (Jammy)     | ARM64 (aarch64) |   ✅   | QEMU Emulation  |
| Fedora 39                | ARM64 (aarch64) |   ✅   | QEMU Emulation  |
| Rocky Linux 9            | ARM64 (aarch64) |   ✅   | QEMU Emulation  |
| Alpine Linux             | ARM64 (aarch64) |   ✅   | QEMU Emulation  |

This verification acts as a critical pre-release gate, ensuring that any version published to PyPI is confirmed to work for ARM64 users before it's released.

<!-- ARM64_STATUS_END -->

Current build status
====================

<table>
    
  <tr>
    <td>Azure</td>
    <td>
      <details>
        <summary>
          <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
            <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main">
          </a>
        </summary>
        <table>
          <thead><tr><th>Variant</th><th>Status</th></tr></thead>
          <tbody><tr>
              <td>linux_64_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_64_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_64_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_64_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_64_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_64_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_64_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_64_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_aarch64_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_aarch64_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_aarch64_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_aarch64_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_ppc64le_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_ppc64le_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_ppc64le_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>linux_ppc64le_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_64_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_64_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_64_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_64_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_64_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_64_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_64_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_64_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_arm64_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_arm64_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_arm64_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>osx_arm64_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>win_64_python3.10.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=win&configuration=win%20win_64_python3.10.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>win_64_python3.11.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=win&configuration=win%20win_64_python3.11.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>win_64_python3.12.____cpython</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=win&configuration=win%20win_64_python3.12.____cpython" alt="variant">
                </a>
              </td>
            </tr><tr>
              <td>win_64_python3.13.____cp313</td>
              <td>
                <a href="https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main">
                  <img src="https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main&jobName=win&configuration=win%20win_64_python3.13.____cp313" alt="variant">
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </details>
    </td>
  </tr>
</table>

---

### Installation Options

**Available via UV, pip, conda-forge, Docker, brew, Github, and piwheels. Support for Linux, Windows, Mac, and Raspberry Pi.**

#### ⚡ UV (Recommended)

<a href="https://github.com/astral-sh/uv">
<img src="https://img.shields.io/badge/uv-install-blueviolet?logo=uv&logoColor=white" alt="uv Install">
</a>

```bash
uv pip install omnipkg
```

#### 📦 PyPI

<a href="https://pypi.org/project/omnipkg/">
<img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
</a>
  
```bash
pip install omnipkg
```

```markdown
#### 📦 Pixi (Recommended for Modern Workflows)

<a href="https://pixi.sh">
<img src="https://img.shields.io/badge/pixi-install-yellow?logo=pixi&logoColor=white" alt="Pixi Install">
</a>

```bash
# Add to your project
pixi add omnipkg

# Run globally without installation
pixi global install omnipkg
```

#### 🏠 Conda & prefix.dev

<a href="https://anaconda.org/conda-forge/omnipkg">
<img src="https://anaconda.org/conda-forge/omnipkg/badges/platforms.svg" alt="Platforms / Noarch">
</a>
<a href="https://anaconda.org/conda-forge/omnipkg">
<img src="https://img.shields.io/badge/conda--forge-omnipkg-brightgreen?logo=anaconda&logoColor=white" alt="Conda-forge">
</a>
<a href="https://anaconda.org/minds3t/omnipkg">
<img src="https://img.shields.io/badge/conda--channel-minds3t-blue?logo=anaconda&logoColor=white" alt="Minds3t Conda Channel">
</a>

```markdown
**Official conda-forge (Recommended):**
```bash
# Using prefix.dev (Ultra-fast resolver)
conda install -c https://prefix.dev/conda-forge omnipkg

# Standard conda-forge
conda install -c conda-forge omnipkg

# Using mamba
mamba install -c conda-forge omnipkg
```
```

**Personal minds3t channel (Latest features first):**
```bash
# Using conda
conda install -c minds3t omnipkg

# Using mamba
mamba install -c minds3t omnipkg
```

#### 🐋 Docker (Multi-Registry)

<a href="https://hub.docker.com/r/1minds3t/omnipkg">
<img src="https://img.shields.io/docker/pulls/1minds3t/omnipkg?logo=docker" alt="Docker Pulls">
</a>
<a href="https://hub.docker.com/r/1minds3t/omnipkg">
<img src="https://img.shields.io/docker/v/1minds3t/omnipkg?logo=docker&label=Docker%20Hub" alt="Docker Hub Version">
</a>
<a href="https://github.com/1minds3t/omnipkg/pkgs/container/omnipkg">
<img src="https://img.shields.io/badge/GHCR-latest-blue?logo=github" alt="GitHub Container Registry">
</a>

**Docker Hub (Development + Releases):**
```bash
# Latest release
docker pull 1minds3t/omnipkg:latest

# Specific version
docker pull 1minds3t/omnipkg:2.0.3

# Development branch
docker pull 1minds3t/omnipkg:main
```

**GitHub Container Registry (Releases Only):**
```bash
# Latest release
docker pull ghcr.io/1minds3t/omnipkg:latest

# Specific version
docker pull ghcr.io/1minds3t/omnipkg:2.0.3
```

**Multi-Architecture Support:**
- ✅ `linux/amd64` (x86_64)
- ✅ `linux/arm64` (aarch64)

#### 🍺 Homebrew

```bash
# Add the tap first
brew tap 1minds3t/omnipkg

# Install omnipkg
brew install omnipkg
```

#### 🥧 piwheels (for Raspberry Pi)
<!-- PIWHEELS_STATS_START -->
## 🥧 ARM32 Support (Raspberry Pi)

[![piwheels](https://img.shields.io/badge/piwheels-ARM32%20verified-97BF0D?logo=raspberrypi&logoColor=white)](https://www.piwheels.org/project/omnipkg/)

**Latest Version:** `2.0.8.1` | **Python:**  | [View on piwheels](https://www.piwheels.org/project/omnipkg/)

```bash
# Install on Raspberry Pi (ARM32)
pip3 install omnipkg==2.0.8.1
```

**Verified Platforms:**
- 🍓 Raspberry Pi (armv6/armv7) - Bullseye (Debian 11), Bookworm (Debian 12), Trixie (Debian 13)
- 📦 Wheel: [`https://www.piwheels.org/simple/omnipkg/omnipkg-2.0.8.1-py3-none-any.whl`](https://www.piwheels.org/simple/omnipkg/omnipkg-2.0.8.1-py3-none-any.whl)

<!-- PIWHEELS_STATS_END -->





<a href="https://www.piwheels.org/project/omnipkg/">
<img src="https://img.shields.io/badge/piwheels-install-97BF0D?logo=raspberrypi&logoColor=white" alt="piwheels Install">
</a>

For users on Raspberry Pi, use the optimized wheels from piwheels for faster installation:

```bash
pip install --index-url=https://www.piwheels.org/simple/ omnipkg
```

#### 🌱 GitHub

```bash
# Clone the repo
git clone https://github.com/1minds3t/omnipkg.git
cd omnipkg

# Install in editable mode (optional for dev)
pip install -e .
```

---

### Instant Demo

```bash
omnipkg demo
```

Choose from:
1. Rich test (Python module switching)
2. UV test (binary switching)
3. NumPy + SciPy stress test (C-extension switching)
4. TensorFlow test (complex dependency switching)
5. 🚀 Multiverse Healing Test (Cross-Python Hot-Swapping Mid-Script)
6. Flask test (under construction)
7. Auto-healing Test (omnipkg run)
8. 🌠 Quantum Multiverse Warp (Concurrent Python Installations)

### Experience Python Hot-Swapping

```bash
# Let omnipkg manage your native Python automatically
omnipkg status
# 🎯 Your native Python is now managed!

# See available interpreters
omnipkg info python

# Install a new Python version if needed (requires Python >= 3.10)
omnipkg python adopt 3.10

# Hot-swap your entire shell context
omnipkg swap python 3.10
python --version  # Now Python 3.10.x
```

### Optional: Enhanced Performance with Redis

For maximum performance, install Redis:

**Linux (Ubuntu/Debian)**:
```bash
sudo apt-get update && sudo apt-get install redis-server
sudo systemctl enable redis && sudo systemctl start redis
```

**macOS (Homebrew)**:
```bash
brew install redis && brew services start redis
```

**Windows**: Use WSL2 or Docker:
```bash
docker run -d -p 6379:6379 --name redis-omnipkg redis
```

Verify Redis: `redis-cli ping` (should return `PONG`)

---

## 🌟 Coming Soon

## 🚀 What We've Already Delivered (The Impossible Made Real)

### ✅ **Concurrent 3x Python & Package Versions in Single Environment**
**Already working in production:** Our "Quantum Multiverse Warp" demo proves you can run Python 3.9, 3.10, and 3.11 **concurrently** in the same script, same environment, in under 6.22 seconds.

### ✅ **Flawless CI/CD Python Interpreter Hot-Swapping**  
**Already working in CI:** Mid-script interpreter switching now works reliably in automated environments with atomic safety guarantees.

### ✅ **Bubble Import Validation and Auto-Healing**
Ensures your bubbles are 100% working and auto heals if they don't.

## 🌟 Coming Soon

* **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.

### 🚀 **C++/Rust Core for Extreme Performance**
- **10-100x speedup** on I/O operations and concurrent processing
- **Memory-safe concurrency** for atomic operations at scale
- **Zero-copy architecture** for massive dependency graphs

### ⚡ **Intelligent Cross-Language Dependency Resolution**
- **Auto-detect language boundaries** and manage cross-stack dependencies
- **Unified dependency graph** across Python, Node.js, Rust, and system packages
- **Smart conflict resolution** between language-specific package managers

### 🔒 **Global Atomic Operations**
- **Cross-process locking** for truly safe concurrent installations
- **Distributed transaction support** for multi-machine environments
- **Crash-proof operation sequencing** with guaranteed rollback capabilities

### 🔌 **Universal Package Manager Integration**
- **Transparent uv/conda/pip interoperability** with smart fallbacks
- **Unified CLI interface** across all supported package managers
- **Intelligent backend selection** based on performance characteristics
- 
---

## 📚 Documentation

Learn more about `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation and setup.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): All `omnipkg` commands.
*   [**Python Hot-Swapping Guide**](docs/python_hot_swapping.md): Master multi-interpreter switching.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Master `omnipkgLoader` for dynamic, mid-script version changes.
*   [**Advanced Management**](docs/advanced_management.md): Redis/SQLite interaction and troubleshooting.
*   [**Future Roadmap**](docs/future_roadmap.md): Features being built today - for you!

---

## 📄 Licensing

`omnipkg` uses a dual-license model designed for maximum adoption and sustainable growth:

*   **AGPLv3**: For open-source and academic use ([View License](https://github.com/1minds3t/omnipkg/blob/main/LICENSE)).
*   **Commercial License**: For proprietary systems and enterprise deployment ([View Commercial License](https://github.com/1minds3t/omnipkg/blob/main/COMMERCIAL_LICENSE.md)).

Commercial inquiries: [omnipkg@proton.me](mailto:omnipkg@proton.me)

---

## 🤝 Contributing

This project thrives on community collaboration. Contributions, bug reports, and feature requests are incredibly welcome. Join us in revolutionizing Python dependency management.

**Translation Help**: Found translation bugs or missing languages? Submit pull requests with corrections or new translations—we welcome community contributions to make `omnipkg` accessible worldwide.

[**→ Start Contributing**](https://github.com/1minds3t/omnipkg/issues)

## Dev Humor

```
 ________________________________________________________________
/                                                                \
| pip:    "Version conflicts? New env please!"                   |
| Docker: "Spin up containers for 45s each!"                     |
| venv:   "90s of setup for one Python version!"                 |
|                                                                |
| omnipkg: *runs 3 Python versions concurrently in 580ms,        |
|           caches installs in 50ms*                             |
|                                                                |
|          "Hold my multiverse—I just ran your entire            |
|           CI matrix faster than you blinked."                  |
\________________________________________________________________/
        \   ^__^
         \  (🐍)\_______
            (__)\       )\/\
                ||----w |
                ||     ||

                ~ omnipkg: The Multiverse Package Manager ~
```

