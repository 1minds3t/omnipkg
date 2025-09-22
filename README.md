<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>
<p align="center">
  <p align="center">
    <p align="center">
  <strong>One environment. Infinite package and Python versions. Zero conflicts.</strong>
<p align="center">
  <!-- Core Project Info -->
      <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-AGPLv3-d94c31?logo=gnu" alt="License">
      </a>
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
   </a>
  <a href="https://anaconda.org/conda-forge/omnipkg">
  <img src="https://img.shields.io/conda/vn/conda-forge/omnipkg?logo=conda-forge" alt="Conda Version">
</a>
<a href="https://pepy.tech/projects/omnipkg">
  <img src="https://static.pepy.tech/personalized-badge/omnipkg?period=total&units=INTERNATIONAL_SYSTEM&left_color=gray&right_color=blue&left_text=downloads" alt="PyPI Downloads">
</a>
<a href="https://hub.docker.com/r/1minds3t/omnipkg">
  <img src="https://img.shields.io/docker/pulls/1minds3t/omnipkg?logo=docker" alt="Docker Pulls">
</a>
  <a href="https://anaconda.org/conda-forge/omnipkg">
  <img src="https://anaconda.org/conda-forge/omnipkg/badges/platforms.svg" alt="Platforms / Noarch">
</a>
<a href="https://clickpy.clickhouse.com/dashboard/omnipkg">
  <img src="https://img.shields.io/badge/global_reach-55+_countries-228B22?logo=globe" alt="Global Reach Badge">
</a>
  <a href="https://pypi.org/project/omnipkg/">
  <img src="https://img.shields.io/pypi/pyversions/omnipkg?logo=python&logoColor=white" alt="Python Versions">
</a>
</p>



</p>
<p align="center">
  <!-- Quality & Security -->
  <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Security+Audit%22">
    <img src="https://img.shields.io/badge/Security-passing-success?logo=security" alt="Security">
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

`omnipkg` radically simplifies Python dependency management, providing a robust alternative to tools like `pipx`, `uv`, `conda`, and `Docker` for handling conflicting packages. Born from a real-world nightmare—a forced downgrade that wrecked a `conda-forge` environment on a Friday night—`omnipkg` was built in a weekend to solve what others couldn't: running multiple versions of the same package in one environment without conflicts.

<!-- COMPARISON_STATS_START -->
## ⚖️ Multi-Version Support

[![omnipkg](https://img.shields.io/badge/omnipkg-607%20Wins-brightgreen?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![pip](https://img.shields.io/badge/pip-610%20Failures-red?logo=pypi&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![uv](https://img.shields.io/badge/uv-610%20Failures-red?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)

*Multi-version installation tests run hourly. [Live results here.](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)*

---

<!-- COMPARISON_STATS_END -->

## 💡 Why This Matters

**Data Science Reality**: Modern ML projects routinely need multiple TensorFlow versions (legacy models vs. current training), different NumPy versions (compatibility vs. performance), and various PyTorch builds (CPU vs. GPU). Traditional solutions like Docker containers, virtual environments, or complex scripts lead to bloated storage, maintenance headaches, and deployment failures.

**Multi-Interpreter Reality**: Legacy codebases often require specific Python versions (e.g., Django on 3.8, modern ML on 3.11+). Traditional solutions force you to maintain separate environments and restart processes, killing productivity. `omnipkg` eliminates this friction entirely.

**Global Development**: Developers working on the same project deserve tools that speak their language, whether debugging in Mandarin, documenting in Spanish, or troubleshooting in Hindi.

**`omnipkg` Solution**: One environment, one script, everything **just works**. Run `torch==2.0.0` and `torch==2.7.1` seamlessly, switch `numpy` versions mid-script, recover from environment damage instantly—all in your native language.

---

## 🧠 Revolutionary Core Features

### 1. Multiverse Orchestration & Python Hot-Swapping [![🐍 Multi-Interpreter Freedom](https://img.shields.io/badge/🐍_Multi--Interpreter_Freedom-Live-orange?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/releases)

The impossible is now routine. Run a single script across multiple Python versions **in a single environment** with automatic dependency management and zero process restarts. `omnipkg` provides true multi-interpreter freedom, ideal for running legacy code and modern packages in the same terminal session.

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
**Key Achievement:** Total test runtime only ~12 seconds for complete multiverse analysis, with automatic healing when NumPy compatibility issues arise. Interpreter swaps finish in just 0.25 seconds!

---

### 2. Real-Time Auto-Healing [![⚡ Auto-Healing: 7.76x Faster than UV](https://img.shields.io/badge/⚡_Auto--Healing-7.76x_Faster_than_UV-gold?logo=lightning&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/old_rich_test.yml)

When external tools or scripts cause compatibility crashes, `omnipkg run` automatically detects, diagnoses, and fixes the issues in real-time, often faster than the original command took to fail.

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
**Auto-healing detects and fixes:** NumPy 2.0 issues, binary incompatibility errors, dependency version conflicts, C-extension loading failures, and missing packages.

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

### 6. Deep Package Intelligence [![🔍 Package Discovery Demo](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml)

Unlike tools that only track "package installed/not installed," `omnipkg` builds a knowledge base with 60+ metadata fields per package version, stored in Redis for instant analysis (or SQLite as fallback).

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
  uv pip install omnipkg && omnipkg demo to see the magic in under 30 seconds.

### Installation Options

#### ⚡ UV 

<a href="https://github.com/astral-sh/uv">
<img src="https://img.shields.io/badge/uv-install-blueviolet?logo=uv&logoColor=white" alt="uv Install">
</a>

```bash
uv pip install omnipkg
```

#### 📦 PyPi

  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
  </a>
  
```bash
pip install omnipkg
```

#### 🥧 piwheels (for Raspberry Pi)

<a href="https://www.piwheels.org/project/omnipkg/">
  <img src="https://img.shields.io/badge/piwheels-install-97BF0D?logo=raspberrypi&logoColor=white" alt="piwheels Install">
</a>

For users on Raspberry Pi, you can use the optimized wheels from piwheels for faster installation.

```bash
pip install --index-url=https://www.piwheels.org/simple/ omnipkg
```

#### 🏠 Official Conda-Forge Channel

  <a href="https://anaconda.org/conda-forge/omnipkg">
  <img src="https://anaconda.org/conda-forge/omnipkg/badges/platforms.svg" alt="Platforms / Noarch">
</a>
  <a href="https://anaconda.org/conda-forge/omnipkg">
  <img src="https://img.shields.io/badge/conda--forge-omnipkg-brightgreen?logo=anaconda&logoColor=white" alt="Conda-forge">
</a>

```bash
# Easiest guaranteed way
conda install -c conda-forge omnipkg

# Or with mamba if you prefer speed
mamba install -c conda-forge omnipkg
```

<a href="https://anaconda.org/minds3t/omnipkg">
  <img src="https://img.shields.io/badge/conda--channel-minds3t-blue?logo=anaconda&logoColor=white" alt="Minds3t Conda Channel">
</a>


```bash
conda install -c minds3t omnipkg
# Or with mamba
mamba install -c minds3t omnipkg
```

#### 🍺 Homebrew
```bash
# Add the tap first
brew tap 1minds3t/omnipkg
# Install omnipkg
brew install omnipkg
```

#### 🐋 Docker 
<a href="https://hub.docker.com/r/1minds3t/omnipkg">
  <img src="https://img.shields.io/docker/pulls/1minds3t/omnipkg?logo=docker" alt="Docker Pulls">
</a>

```bash
# Pull from Docker Hub
docker pull 1minds3t/omnipkg:latest

# Pull from GitHub Container Registry (GHCR)
docker pull ghcr.io/1minds3t/omnipkg:latest
```

### 🌱 GitHub
```bash
# Clone the repo
git clone https://github.com/1minds3t/omnipkg.git
cd omnipkg

# Install in editable mode (optional for dev)
pip install -e .
```

### Instant Demo
```bash
omnipkg demo
```

Choose from:
1. Python module switching (`rich`)
2. Binary switching (`uv`)
3. C-extension switching (`numpy`, `scipy`)
4. Complex dependency switching (`tensorflow`)

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

*   **Linux (Ubuntu/Debian)**:
    ```bash
    sudo apt-get update && sudo apt-get install redis-server
    sudo systemctl enable redis && sudo systemctl start redis
    ```

*   **macOS (Homebrew)**:
    ```bash
    brew install redis && brew services start redis
    ```

*   **Windows**: Use WSL2 or Docker:
    ```bash
    docker run -d -p 6379:6379 --name redis-omnipkg redis
    ```

*   Verify Redis: `redis-cli ping` (should return `PONG`)

---

## 🔬 How It Works (Simplified Flow)

1.  **Adopt Interpreters**: On first run, `omnipkg` automatically adopts your native Python. Add more with `omnipkg python adopt <version>`.
2.  **Install Packages**: Use `omnipkg install uv==0.7.13 uv==0.7.14` or `omnipkg install -r req.txt`
3.  **Conflict Detection**: `omnipkg` spots version clashes and isolates them in bubbles.
4.  **Dynamic Package Switching**: Use `omnipkgLoader` to switch package versions mid-script.
5.  **Interpreter Hot-Swapping**: Switch your shell's active Python instantly with `omnipkg swap python <version>`.
6.  **Intelligence Database**: High-performance knowledge base built for all packages (Redis preferred, SQLite fallback).
7.  **Auto-healing**: `omnipkg run` automatically fixes compatibility issues in real-time.
8.  **Atomic Snapshots**: Instant rollback with `omnipkg revert`.

**Example: Safe Flask-Login Downgrade:**
```bash
omnipkg install flask-login==0.4.1
```
```bash
📸 Taking LIVE pre-installation snapshot...
🛡️ DOWNGRADE PROTECTION ACTIVATED!
-> Detected conflict: flask-login v0.6.3 → v0.4.1
🫧 Creating bubble for flask-login v0.4.1... ✅ Done
🔄 Restoring flask-login v0.6.3... ✅ Environment secure
```

Verify:
```bash
omnipkg info flask-login
```
```bash
📋 flask-login STATUS:
🎯 Active: 0.6.3 (main)
🫧 Available: 0.4.1 (bubble)
📊 Space Saved: 55.5%
```
You now have both versions available in one environment, ready for use anytime!

---

## 🌟 Coming Soon

*   **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.
*   **Concurrent 3x Python & Package Versions Running in Single Script, Single Environment**: I've proven it works locally and am creating a demo and CI.
*   **Ensuring Python-Interpreter Hotswaps Work Flawlessly In CI/CD**: Improving code so that CI can consistently execute mid-script interpreter hot-swapping without issues.

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
 _________________________________________
/ Other tools: "You need Docker for       \
| different Python versions!"             |
|                                         |
| omnipkg: *runs multiverse analysis      |
| across 3 Python versions in 12 seconds  |
| in one environment with auto-healing*   |
| "Wait, that's illegal!"                 |
\_________________________________________/
        \   ^__^
         \  (🐍)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
```
