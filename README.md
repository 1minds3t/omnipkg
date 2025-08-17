<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>

<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>

<p align="center">
  <strong>One environment. Infinite packages. Zero conflicts. Undeniable Proof: </strong>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml">
    <img src="https://img.shields.io/badge/🚀_Live_NumPy+SciPy_Hot--Swapping-passing-success?logo=github-actions" alt="NumPy+SciPy Hot-Swapping Test">
  </a>
</p>

<p align="center">
  <!-- Core Badges -->
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg?color=blue&logo=pypi" alt="PyPI">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-AGPLv3-d94c31?logo=gnu" alt="License">
  </a>
  
  <!-- Quality Badges -->
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
</p>

---

`omnipkg` radically simplifies Python dependency management, providing a robust alternative to tools like `pipx`, `uv`, `conda`, and `Docker` for handling conflicting packages. Born from a real-world nightmare—a forced downgrade that wrecked a `conda-forge` environment on a Friday night—`omnipkg` was built in a weekend to solve what others couldn’t: running multiple versions of the same package in one environment without conflicts.

---

## 💡 Why This Matters

**Data Science Reality**: Modern ML projects routinely need multiple TensorFlow versions (legacy models vs. current training), different NumPy versions (compatibility vs. performance), and various PyTorch builds (CPU vs. GPU). Traditional solutions like Docker containers, virtual environments, or complex scripts lead to bloated storage, maintenance headaches, and deployment failures.

**`omnipkg` Solution**: One environment, one script, zero compromise. Run `torch==2.0.0` and `torch==2.7.1` seamlessly, switch `numpy` versions mid-script, and recover from environment damage instantly.

---

## 🔥 Undeniable CI Proof: Live Demo Validation

`omnipkg` has reached **~3000 PyPI downloads across 35 countries 10 days post-launch with zero marketing.** ([View Live Stats](https://clickpy.clickhouse.com/dashboard/omnipkg)) Our latest `1.0.13` release demonstrates `omnipkg`'s intelligent, self-healing capabilities, allowing unprecedented dynamic version control within a single environment.

Our continuous integration (CI) pipelines run comprehensive, real-world tests after every commit, validating `omnipkg`'s core claims in various challenging scenarios.

---

## 🧠 Revolutionary Core Features

### 1. Dynamic Version Switching [![💥 Nuclear Test: NumPy+SciPy](https://img.shields.io/badge/💥_Nuclear_Test:NumPy+SciPy-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml)

Switch package versions mid-script using `omnipkgLoader`, without restarting or changing environments. `omnipkg` seamlessly juggles C-extension packages like `numpy` and `scipy` in the same Python process.

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

**Key CI Output Excerpts:**
```plaintext
🤯 NUMPY + SCIPY VERSION MIXING:

🌀 COMBO: numpy==1.24.3 + scipy==1.12.0
🔍 Python path (first 5 entries):
0: /opt/hostedtoolcache/Python/3.11.13/x64/lib/python3.11/site-packages/.omnipkg_versions/numpy-1.24.3
1: /opt/hostedtoolcache/Python/3.11.13/x64/lib/python3.11/site-packages/.omnipkg_versions/scipy-1.12.0
🧪 numpy: 1.24.3, scipy: 1.12.0
🎯 Version verification: BOTH PASSED!

🌀 COMBO: numpy==1.26.4 + scipy==1.16.1
🔍 Python path (first 5 entries):
0: /home/runner/work/omnipkg # Back to main environment context
🧪 numpy: 1.26.4, scipy: 1.16.1
🎯 Version verification: BOTH PASSED!

🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇
```
---

### 2. Lightweight Isolation [![🧪 Rich Version Juggling](https://img.shields.io/badge/🧪_Rich_Version_Juggling-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/rich-module-switching-test.yml)

Conflicting versions are isolated in lightweight, self-contained “bubbles” containing only necessary files. Compatible dependencies are shared with the main environment, potentially slashing disk space by **up to 60%**.

**Key CI Output Excerpt:**
```plaintext
🛡️ DOWNGRADE PROTECTION ACTIVATED!
    -> Fixing downgrade: typing_extensions from v4.14.1 to v4.5.0
🫧 Creating isolated bubble for typing_extensions v4.5.0
    📊 Space efficiency: 75.0% saved.
    📝 Created manifest and registered bubble for 1 packages (0.18 MB).
    🔄 Restoring 'typing_extensions' to safe version v4.14.1
✅ Environment protection complete!
```
---

### 3. Downgrade Protection & Conflict Resolution [![🔧 Simple UV Multi-Version Test](https://img.shields.io/badge/🔧_Simple_UV_Multi--Version_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_install.yml)

`omnipkg` automatically reorders installations and isolates conflicts, preventing environment-breaking downgrades.

**Example: Conflicting `torch` versions:**
```bash
omnipkg install torch==2.0.0 torch==2.7.1
```

**What happens?** `omnipkg` reorders installs to trigger the bubble creation, installs `torch==2.7.1` in the main environment, and isolates `torch==2.0.0` in a lightweight “bubble,” sharing compatible dependencies to save space. No virtual environments or containers needed.

```plaintext
🔄 Reordered: torch==2.7.1, torch==2.0.0
📦 Installing torch==2.7.1... ✅ Done
🛡️ Downgrade detected for torch==2.0.0
🫧 Creating bubble for torch==2.0.0... ✅ Done
🔄 Restoring torch==2.7.1... ✅ Environment secure
```
---

### 4. Binary & C-Extension Support [![⚡ UV Binary Test](https://img.shields.io/badge/⚡_UV_Binary_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test-uv-binary-switching.yml) [![💥 TensorFlow Hot-Swap](https://img.shields.io/badge/💥_TensorFlow_Hot_Swap-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test-tensorflow-switching.yml)

`omnipkg` seamlessly switches binary tools (e.g., `uv`) and complex C-extension version combinations (e.g., `tensorflow`, `numpy`, `scipy`) during runtime, a feat traditional tools struggle with.

**Key CI Output Excerpts (TensorFlow):**

```plaintext
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
### 5. Deep Package Intelligence

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
| • Import Validation (runtime testing) | • Future-proof C-extension symlinking |

---

### 6. Instant Environment Recovery 
[![🛡️ UV Revert Test](https://img.shields.io/badge/🛡️_UV_Revert_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_revert.yml)

If an external tool (like `pip` or `uv`) causes damage, `omnipkg revert` restores your environment to a “last known good” state in seconds.

**Key CI Output Excerpt:**

```plaintext
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

**UV is saved (along with any deps)!!!**

---

## 🛠️ Get Started in 60 Seconds

### Step 1: Install and Start Redis (Required)
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
    Verify: `redis-cli ping`.

*   **Windows**:
    Use WSL2 (recommended) or Docker:
    ```bash
    docker run -d -p 6379:6379 --name some-redis redis
    ```

*   Verify: `redis-cli ping` (should return `PONG`).

### Step 2: Install `omnipkg`
```bash
pip install omnipkg
```

### Step 3: Run the Demo
```bash
omnipkg demo
```
Choose from:
*   Python module switching (`rich`)
*   Binary switching (`uv`)
*   C-extension switching (`numpy`, `scipy`)
*   Complex dependency switching (`tensorflow`)
*   Flask test (under construction)

### Step 4: Try the Stress Test
```bash
omnipkg stress-test
```
Watch `omnipkg` juggle complex `numpy` and `scipy` versions flawlessly.

---

## 🔬 How It Works (Simplified Flow)

1.  **Install Packages**: Use `omnipkg install <package>` or `pip install`.
2.  **Conflict Detection**: `omnipkg` spots version clashes and isolates them in bubbles.
3.  **Dynamic Switching**: Use `omnipkgLoader` to switch versions mid-script.
4.  **Redis-Powered Speed**: A high-performance knowledge base ensures metadata lookups at ~9 packages/second.
5.  **Atomic Snapshots**: Instant rollback with `omnipkg revert`.

**Example: Safe Flask-Login Downgrade:**
```bash
omnipkg install flask-login==0.4.1
```
```plaintext
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
```plaintext
📋 flask-login STATUS:
🎯 Active: 0.6.3 (main)
🫧 Available: 0.4.1 (bubble)
📊 Space Saved: 55.5%
```
You now have both versions available in one environment, ready for use anytime!

---

## 🌟 Coming Soon

*   **Python Interpreter Hot-Swapping**: Seamlessly switch between different Python versions (e.g., 3.8 to 3.11) mid-script.
*   **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.
*   **Bubble Validation**: Testing functionality of your bubbles until functional dependencies are installed.

---

## 📚 Documentation

Learn more about `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation and setup.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): All `omnipkg` commands.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Master `omnipkgLoader` for dynamic, mid-script version changes.
*   [**Advanced Management**](docs/advanced_management.md): Redis interaction and troubleshooting.
*   [**Future Roadmap**](docs/future_roadmap.md): Features being built today for a future you. 

---

## 📄 Licensing

`omnipkg` uses a dual-license model designed for maximum adoption and sustainable growth:

*   **AGPLv3**: For open-source and academic use ([View License](https://github.com/1minds3t/omnipkg/blob/main/LICENSE)).
*   **Commercial License**: For proprietary systems and enterprise deployment ([View Commercial License](https://github.com/1minds3t/omnipkg/blob/main/COMMERCIAL_LICENSE.md)).

Commercial inquiries: [omnipkg@proton.me](mailto:omnipkg@proton.me)

---

## 🤝 Contributing

This project thrives on community collaboration. Contributions, bug reports, and feature requests are incredibly welcome. Join us in revolutionizing Python dependency management.

[**→ Start Contributing**](https://github.com/1minds3t/omnipkg/issues)

```
 ___________________________________________
/ pip in omnipkg jail 🔒                   \
| Status: Learning conflict-free ways       |
| 💭 'One environment might just work...'   |
\___________________________________________/
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
```
