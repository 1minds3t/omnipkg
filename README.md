<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>
<p align="center">
  <strong>One environment. Infinite packages. Zero conflicts.</strong>
  <br>
<p align="center">
  <!-- Core Project Info -->
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-AGPLv3-d94c31?logo=gnu" alt="License">
  </a>
  <img src="https://img.shields.io/badge/Redis-Required-%2523DC382D?logo=redis&logoColor=white" alt="Redis Required">
  <a href="https://pepy.tech/projects/omnipkg">
    <img src="https://static.pepy.tech/badge/omnipkg" alt="Downloads">
  </a>
    <a href="https://clickpy.clickhouse.com/dashboard/omnipkg">
    <img src="https://img.shields.io/badge/global_reach-40+_countries-green?logo=globe" alt="Global Reach Badge">
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
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/language_test.yml">
    <img src="https://img.shields.io/badge/💥_Breaking_Language_Barriers-24_Languages-success?logo=babel&logoColor=white" alt="24 Languages">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/numpy_scipy_test.yml">
    <img src="https://img.shields.io/badge/🚀_Live_NumPy+SciPy_Hot--Swapping-passing-success?logo=github-actions" alt="Hot-Swapping">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/releases">
    <img src="https://img.shields.io/badge/🔥_Python_Interpreter_Hot--Swapping-Live-orange?logo=python&logoColor=white" alt="Python Hot-Swapping">
  </a>
</p>


---

`omnipkg` radically simplifies Python dependency management, providing a robust alternative to tools like `pipx`, `uv`, `conda`, and `Docker` for handling conflicting packages. Born from a real-world nightmare—a forced downgrade that wrecked a `conda-forge` environment on a Friday night—`omnipkg` was built in a weekend to solve what others couldn't: running multiple versions of the same package in one environment without conflicts.

---

## 🔥 **BREAKTHROUGH: Python Interpreter Hot-Swapping is Here**

The impossible is now routine. Switch Python versions on the fly without containers, virtual environments, or process restarts. Watch `omnipkg` automatically switch from Python 3.12 to 3.11 when a script requires it, proving true multi-interpreter freedom.

---

<!-- COMPARISON_STATS_START -->
## ⚖️ Multi-Version Support

[![omnipkg](https://img.shields.io/badge/omnipkg-228%20Wins-brightgreen?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![pip](https://img.shields.io/badge/pip-231%20Failures-red?logo=pypi&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![uv](https://img.shields.io/badge/uv-231%20Failures-red?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)

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

### 1. Dynamic Version Switching [![💥 Nuclear Test: NumPy+SciPy](https://img.shields.io/badge/💥_Nuclear_Test:NumPy+SciPy-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml)

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

### 2. Python Interpreter Hot-Swapping [![🐍 Multi-Interpreter Freedom](https://img.shields.io/badge/🐍_Multi--Interpreter_Freedom-Live-orange?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/releases)

Switch between Python versions **on the fly**, without restarting your shell or script. `omnipkg` provides true multi-interpreter freedom with zero-friction adoption of your system's native Python. This is ideal for running legacy code and modern packages in the same terminal session.

**Key Architecture:**
- **Zero-Friction Adoption**: Your native Python is automatically managed on first run.
- **Control Plane Stability**: A dedicated Python 3.11 control plane ensures bulletproof operations.
- **Automatic Context Switching**: Demos and scripts can trigger an interpreter swap automatically.

**Key Terminal Output (Automatic Swapping):**
```bash
(evocoder_env) [minds3t@aiminingrig:~/omnipkg]$ omnipkg demo
Current Python version: 3.12
Select a demo to run:
4. TensorFlow test (complex dependency switching)

============================================================
  ⚠️  This Demo Requires Python 3.11
============================================================
Current Python version: 3.12
omnipkg will now attempt to automatically configure the correct interpreter.
------------------------------------------------------------
🔄 Swapping active interpreter to Python 3.11 for the demo...
🐍 Switching active Python context to version 3.11...
   - Found managed interpreter at: /opt/conda/envs/evocoder_env/bin/python3.11
   - ✅ Configuration saved.
   - ✅ Default Python links updated to use Python 3.11.

🎉 Successfully switched omnipkg context to Python 3.11!
   Just kidding, omnipkg handled it for you automatically!
✅ Environment successfully configured for Python 3.11.
🚀 Proceeding to run the demo...
```
---

### 3. 🌍 Global Intelligence & AI-Driven Localization [![🤖 AI-Powered: 24 Languages](https://img.shields.io/badge/🤖_AI--Powered-24_Languages-brightgreen?logo=openai&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/language_test.yml)

`omnipkg` eliminates language barriers with advanced AI localization supporting 24+ languages, making package management accessible to developers worldwide in their native language.

**Key Features**: Auto-detection from system locale, competitive AI translation models, context-aware technical term handling, and continuous self-improvement from user feedback.

```bash
# Set language permanently
omnipkg config set language zh_CN
# ✅ Language permanently set to: 中文 (简体)

# Temporary language override
omnipkg --lang es install requests

# Interactive language picker
omnipkg reset-config

# View current configuration
cat ~/.config/omnipkg/config.json
```

Zero setup required—works in your language from first run with graceful fallbacks and clear beta transparency.

### 4. Lightweight Isolation 

Conflicting versions are isolated in lightweight, self-contained "bubbles" containing only necessary files. Compatible dependencies are shared with the main environment, potentially slashing disk space by **up to 60%**.

**Key CI Output Excerpt:**
```bash
🛡️ DOWNGRADE PROTECTION ACTIVATED!
    -> Fixing downgrade: typing_extensions from v4.14.1 to v4.5.0
🫧 Creating isolated bubble for typing_extensions v4.5.0
    📊 Space efficiency: 75.0% saved.
    📝 Created manifest and registered bubble for 1 packages (0.18 MB).
    🔄 Restoring 'typing_extensions' to safe version v4.14.1
✅ Environment protection complete!
```
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

### 6. Python Library, Binary, & C-Extension Support [![⚡ UV Binary Test](https://img.shields.io/badge/⚡_UV_Binary_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test-uv-binary-switching.yml) [![💥 TensorFlow Hot-Swap](https://img.shields.io/badge/💥_TensorFlow_Hot_Swap-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test-tensorflow-switching.yml) [![🧪 Rich Version Juggling](https://img.shields.io/badge/🧪_Rich_Version_Juggling-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/rich-module-switching-test.yml)

`omnipkg` seamlessly switches binary tools (e.g., `uv`) and complex C-extension version combinations (e.g., `tensorflow`, `numpy`, `scipy`) during runtime, a feat traditional tools struggle with.

**Key CI Output Excerpts (TensorFlow):**

```bash
🔧 Testing initial state: tensorflow==2.13.0 with typing-extensions==4.14.1 and keras==2.13.1 (main)
TensorFlow version: 2.13.0
Typing Extensions version: 4.14.1
Keras version: 2.13.1
✅ Model created successfully

🫧 Testing switch to typing-extensions==4.5.0 bubble
🌀 omnipkg loader: Activating typing_extensions==4.5.0...
 ✅ Activated bubble: /opt/hostedtoolcache/Python/3.11.13/x64/lib/python3.11/site-packages/.omnipkg_versions/typing_extensions-4.5.0
TensorFlow version: 2.13.0
Typing Extensions version: 4.5.0
Keras version: 2.13.1
✅ Model created successfully with typing-extensions 4.5.0 bubble
✅ Successfully switched to older version: typing-extensions=4.5.0
😎 TensorFlow escaped the matrix! 🚀
```

---
### 7. Deep Package Intelligence [![🔍 Package Discovery Demo - Omnipkg Intelligence](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml)

Unlike tools that only track "package installed/not installed," `omnipkg` builds a knowledge base with 60+ metadata fields per package version, stored in Redis for instant analysis.

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

---[ Build Info ]---
⏰ Last Indexed: 2025-08-17T12:51:28
🔐 Checksum: b7b75f1e...cdd22
```

| **Intelligence Includes** | **Redis Superpowers** |
|--------------------------|-----------------------|
| • Binary Analysis (ELF validation, file sizes) | • 0.2ms metadata lookups |
| • CLI Command Mapping (all subcommands/flags) | • Compressed storage for large data |
| • Security Audits (vulnerability scans) | • Atomic transaction safety |
| • Dependency Graphs (conflict detection) | • Intelligent caching of expensive operations |
| • Import Validation (runtime testing) | • Enables future C-extension symlinking |

---

### 8. Instant Environment Recovery
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
⚙️ Running pip install for: uv==0.8.11...
      Successfully uninstalled uv-0.7.13
Successfully installed uv-0.8.11
✅ Environment successfully reverted to the last known good state.

--- Verifying UV version after omnipkg revert ---
uv 0.8.11
```

**UV is saved, along with any deps!**

---

### 🏗️ The Architecture: How Hot-Swapping Works

Solving interpreter hot-swapping required a complete architectural reimagining. The core challenges we solved:

*   **The State Problem**: Python interpreters maintain complex internal state. Our solution creates isolated execution contexts while maintaining a seamless user experience.
*   **The Control Plane Solution**: All sensitive operations execute through a dedicated Python 3.11 "control plane," ensuring reliability regardless of your active interpreter version.
*   **Native Adoption Breakthrough**: The biggest user friction point—getting "stuck" after switching away from your native Python—is eliminated by automatically managing your existing interpreter from day one.

---

## 🛠️ Get Started in 60 Seconds

### Step 1: Start Redis (Required) <img src="https://img.shields.io/badge/Redis-Required-%2523DC382D?logo=redis&logoColor=white" alt="Redis Required">
`omnipkg` uses Redis for fast metadata management. It **must be running** before `omnipkg` is used.


*   **Linux (Ubuntu/Debian)**:
    ```bash
    sudo apt-get update
    sudo apt-get install redis-server
    sudo systemctl enable redis
    sudo systemctl start redis
    ```

*   **macOS (Homebrew)**:
    ```bash
    brew install redis
    brew services start redis
    ```

*   **Windows**: Use WSL2 or Docker:
    ```bash
    docker run -d -p 6379:6379 --name redis-omnipkg redis
    ```

*   Verify Redis: `redis-cli ping` (should return `PONG`)

### Step 2: Install `omnipkg`

Choose your preferred installation method:

#### 📦 PyPI (Recommended)
```bash
pip install omnipkg
```

#### 🍺 Homebrew (macOS/Linux)
```bash
# Add the tap first
brew tap 1minds3t/omnipkg
# Install omnipkg
brew install omnipkg
```

#### 🐍 Anaconda/Conda
```bash
conda install minds3t::omnipkg
```

**Alternative channels:**
```bash
# Direct from user channel  
conda install -c minds3t omnipkg
```

### Step 3: Run the Demo
```bash
omnipkg demo
```
Choose from:
1. Python module switching (`rich`)
2. Binary switching (`uv`)
3. C-extension switching (`numpy`, `scipy`)
4. Complex dependency switching (`tensorflow`)

### Step 4: Experience Python Hot-Swapping
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

### Step 5: Try the Stress Test
```bash
omnipkg stress-test
```
Watch `omnipkg` juggle complex `numpy` and `scipy` versions flawlessly!

---

## 🔬 How It Works (Simplified Flow)

1.  **Adopt Interpreters**: On first run, `omnipkg` automatically adopts your native Python. Add more with `omnipkg python adopt <version>`.
2.  **Install Packages**: Use `omnipkg install uv==0.7.13 uv==0.7.14` or `omnipkg install -r req.txt`
3.  **Conflict Detection**: `omnipkg` spots version clashes and isolates them in bubbles.
4.  **Dynamic Package Switching**: Use `omnipkgLoader` to switch package versions mid-script.
5.  **Interpreter Hot-Swapping**: Switch your shell's active Python instantly with `omnipkg swap python <version>`.
6.  **Redis-Powered Speed**: A high-performance knowledge base is built for all your packages on install at ~9 packages/second.
7.  **Atomic Snapshots**: Instant rollback with `omnipkg revert`.

**Example: Safe Flask-Login Downgrade:**
```bash
omnipkg install flask-login==0.4.1
``````bash
📸 Taking LIVE pre-installation snapshot...
🛡️ DOWNGRADE PROTECTION ACTIVATED!
-> Detected conflict: flask-login v0.6.3 → v0.4.1
🫧 Creating bubble for flask-login v0.4.1... ✅ Done
🔄 Restoring flask-login v0.6.3... ✅ Environment secure
```

Verify:
```bash
omnipkg info flask-login
``````bash
📋 flask-login STATUS:
🎯 Active: 0.6.3 (main)
🫧 Available: 0.4.1 (bubble)
📊 Space Saved: 55.5%
```
You now have both versions available in one environment, ready for use anytime!

---

## 🌟 Coming Soon

*   **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.
*   **Bubble validation**: Ensuring your bubbled packages are stored with functional dependencies by testing during installs.
*   **Enhanced CI Workflows**: Public CI demonstrating mid-script interpreter hot-swapping.

---

## 📚 Documentation

Learn more about `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation and setup.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): All `omnipkg` commands.
*   [**Python Hot-Swapping Guide**](docs/python_hot_swapping.md): Master multi-interpreter switching.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Master `omnipkgLoader` for dynamic, mid-script version changes.
*   [**Advanced Management**](docs/advanced_management.md): Redis interaction and troubleshooting.
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
| omnipkg: *swaps Python 3.8→3.11→3.12    |
| automatically for a demo*               |
| "Wait, that's illegal!"                 |
\_________________________________________/
        \   ^__^
         \  (🐍)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
```
