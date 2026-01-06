<p align="center"> 
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>
<p align="center">
  <p align="center">
    <p align="center">
  <strong><strong>Run infinite Python packages and interpreters concurrently in one environment with zero conflicts.</strong>
    
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
    <img src="https://img.shields.io/badge/<600ms 3 Py Interps 1 Script 1 Env-passing-success?logo=python&logoColor=white" alt="Concurrent Python Interpreters">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/numpy_scipy_test.yml">
    <img src="https://img.shields.io/badge/🚀0.25s_Live_NumPy+SciPy_Hot--Swapping-passing-success?logo=github-actions" alt="Hot-Swapping">
  </a>
<a href="https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml">
  <img src="https://img.shields.io/badge/🔥_0.25s_Python_Interpreter_Hot--Swapping-Live-orange?logo=python&logoColor=white" alt="Python Hot-Swapping">
</a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/old_rich_test.yml">
  <img src="https://img.shields.io/badge/⚡_Auto--Healing-7.76x_Faster_than_UV-gold?logo=lightning&logoColor=white" alt="Auto-Healing Performance">
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

### 1. Multiverse Orchestration & Python Hot-Swapping [![<600ms 3 Py Interps 1 Script 1 Env](https://img.shields.io/badge/<600ms%203%20Py%20Interps%201%20Script%201%20Env-passing-success?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/multiverse_test.yml)

## The "Quantum Multiverse Warp": 3 Pythons, 1 Script, < 600ms

Our "Quantum Multiverse Warp" demo, validated live in our CI, executes a single script across three different Python interpreters and three package versions **concurrently** in the same environment. The results are not just fast; they redefine what's possible for CI/CD.

| Task (Same Script, Same Environment) | Execution Time |
| ------------------------------------ | :------------: |
| 🧵 **Thread 1:** Python 3.9 + Rich 13.4.2  | ✅ **579.6ms**   |
| 🧵 **Thread 2:** Python 3.10 + Rich 13.6.0 | ✅ **548.4ms**   |
| 🧵 **Thread 3:** Python 3.11 + Rich 13.7.1 | ✅ **571.8ms**   |
| 🏆 **Total Concurrent Runtime**        | **580.1ms**      |

This isn't just a speedup; it's a paradigm shift. What traditionally takes minutes with Docker or complex venv scripting, `omnipkg` accomplishes in **under 600 milliseconds**. This isn't a simulation; it's a live, production-ready capability for high-performance Python automation.

Don't believe it? See the live proof, then run **Demo 8** to experience it yourself.
```bash
uv pip install omnipkg && omnipkg demo

**Live CI Output from Multiverse Analysis:**
```bash
🚀 Launching multiverse analysis from Python 3.11…

📦 Step 1: Swapping to Python 3.9…
🐍 Active interpreter switched in <1 second!
✅ All dependencies auto-healed
   - NumPy 1.26.4
   - SciPy 1.13.1
🧪 SciPy result: 225

📦 Step 2: Swapping back to Python 3.11…
🐍 Hot-swapped Python interpreter instantly
✅ TensorFlow 2.20.0 ready to go
🧪 TensorFlow prediction: SUCCESS

🌀 SAFETY PROTOCOL: Returned to original Python 3.11 environment
```
**Key Achievement:** Total test runtime only ~10 seconds for complete multiverse analysis, with automatic healing when NumPy compatibility issues arise. Interpreter swaps finish in just 0.25 seconds!

---

### 2. Real-Time Auto-Healing & Environment Repair [![⚡ Auto-Healing: 7.76x Faster than UV](https://img.shields.io/badge/⚡_Auto--Healing-7.76x_Faster_than_UV-gold?logo=lightning&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/old_rich_test.yml)

`omnipkg` is the first tool that **actively repairs** your environment while you work.
- **`omnipkg run`**: Our intelligent script runner detects `ModuleNotFoundError`, `AssertionError` (for version conflicts), and even NumPy C-extension failures. It then automatically creates and activates a version-specific "bubble" to heal the script, often completing the entire process **faster than other tools take to simply fail.**
- **The Ghost Hunter**: On every startup, `omnipkg` proactively scans all managed `site-packages` for corrupted installations (e.g., `~temp-name.dist-info`) left behind by failed `pip` installs and surgically removes them, preventing environment rot before it starts.

**Live CI Output from Auto-Healing:**
```bash
⏱️  UV run failed in: 5379.237 ms (5,379,236,666 ns)
🔍 NumPy 2.0 compatibility issue detected. Auto-healing with numpy downgrade...
   - Downgrading to numpy<2.0 for compatibility
✅ Using bubble: numpy-1.26.4

🚀 Re-running with omnipkg auto-heal...
✅ Script completed successfully inside omnipkg bubble.

======================================================================
🚀 PERFORMANCE COMPARISON: UV vs OMNIPKG
======================================================================
UV Failed Run:      5379.237 ms  (5,379,236,666 ns)
omnipkg Healing:     693.212 ms  ( 693,211,844 ns)
----------------------------------------------------------------------
🎯 omnipkg is   7.76x FASTER than UV!
💥 That's   675.99% improvement!
======================================================================
```

---

### 3. Dynamic Package Switching [![💥 Nuclear Test: NumPy+SciPy](https://img.shields.io/badge/💥_Nuclear_Test:NumPy+SciPy-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml)

Switch package versions mid-script using `omnipkgLoader`, without restarting or changing environments. `omnipkg` seamlessly juggles C-extension packages like `numpy` and `scipy` in the same Python process. The loader even handles complex **nested dependency contexts**, a feat unmatched by other tools.

**Example Code:**
```python
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager # Recommended for robust path discovery

config = ConfigManager().config # Load your omnipkg config once

with omnipkgLoader("numpy==1.24.3", config=config):
    import numpy
    print(numpy.__version__)  # Outputs: 1.24.3
import numpy # Re-import/reload might be needed if numpy was imported before the 'with' block
print(numpy.__version__)  # Outputs: Original main env version (e.g., 1.26.4)
```

**Key CI Output Excerpts (Nested Loaders):**
```bash
--- Nested Loader Test ---
🌀 Testing nested loader usage...
✅ Outer context - Typing Extensions: 4.5.0
🌀 omnipkg loader: Activating tensorflow==2.13.0...
✅ Inner context - TensorFlow: 2.13.0
✅ Inner context - Typing Extensions: 4.5.0
✅ Nested loader test: Model created successfully
```
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

#### 🏠 Conda (Two Channels Available)

<a href="https://anaconda.org/conda-forge/omnipkg">
<img src="https://anaconda.org/conda-forge/omnipkg/badges/platforms.svg" alt="Platforms / Noarch">
</a>
<a href="https://anaconda.org/conda-forge/omnipkg">
<img src="https://img.shields.io/badge/conda--forge-omnipkg-brightgreen?logo=anaconda&logoColor=white" alt="Conda-forge">
</a>
<a href="https://anaconda.org/minds3t/omnipkg">
<img src="https://img.shields.io/badge/conda--channel-minds3t-blue?logo=anaconda&logoColor=white" alt="Minds3t Conda Channel">
</a>

**Official conda-forge (Recommended):**
```bash
# Using conda
conda install -c conda-forge omnipkg

# Using mamba (faster)
mamba install -c conda-forge omnipkg
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

