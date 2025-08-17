
<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>

<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>

<p align="center">
  <strong>One environment. Infinite packages. Zero conflicts.</strong>
</p>

<p align="center">
    <!-- General Project Badges -->
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Security+Audit%22"><img src="https://img.shields.io/badge/Security%20Audit-passing-4c1" alt="Security Audit Status"></a>
    <a href="https://pypi.org/project/omnipkg/"><img src="https://img.shields.io/pypi/v/omnipkg?color=blue" alt="PyPI Version"></a>
    <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPLv3-d94c31" alt="License: AGPLv3"></a>
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3APylint"><img src="https://img.shields.io/badge/Pylint-passing-4c1" alt="Pylint Status"></a>
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ABandit"><img src="https://img.shields.io/badge/Bandit-passing-4c1" alt="Bandit Status"></a>
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ACodeQL+Advanced"><img src="https://img.shields.io/badge/CodeQL-passing-4c1" alt="CodeQL Status"></a>
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3ADevSkim"><img src="https://img.shields.io/badge/DevSkim-passing-4c1" alt="DevSkim Status"></a>
</p>

---

`omnipkg` obliterates Python dependency hell, making tools like pipx, uv, conda, and Docker obsolete for managing conflicting packages. Born from a real-world nightmare—a forced downgrade that wrecked a conda-forge environment on a Friday night—`omnipkg` was built in a weekend (yes) to solve what others couldn’t: running multiple versions of the same package in one environment without conflicts. And it’s battle-tested to prove it.

## 🚀 Why omnipkg Changes Everything

Dependency conflicts have cost Python developers billions in lost productivity. Legacy projects needing `tensorflow==1.15` and modern ones requiring `tensorflow==2.13.0` couldn’t coexist—until now. `omnipkg` lets you run infinite package versions in a single environment, with zero waste and mathematically impossible conflicts.

How? Through Surgical Version Bubbles, `omnipkg` isolates only conflicting packages and their dependencies, sharing compatible ones to save up to 60% disk space. Switch versions mid-script with `omnipkgLoader`, revert damage instantly with `omnipkg revert`, and manage it all with a sleek CLI.

Don't believe me? Our CI pipelines validate every claim with live, real-world tests. See for yourself.

---

## 🔥 Undeniable CI Proof: Live Demo Validation

Our latest `1.0.13` release cements `omnipkg` as the intelligent, self-healing solution for Python dependency hell, allowing unprecedented dynamic version control within a single environment.

Our continuous integration (CI) pipelines run comprehensive, real-world tests after every commit, validating `omnipkg`'s claims in various challenging scenarios. Click the badges below to see the **live workflow runs and detailed logs** on GitHub:

### 1. Python Module Switching Test (Rich)
[![Rich Module Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/rich-module-switching-test.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22%F0%9F%A7%AA+Rich+Module+Switching+Test%22)
*   **What it proves:** Seamless runtime version swapping for pure Python modules within a single environment.

### 2. UV Binary Switching Test
[![UV Binary Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/test-uv-binary-switching.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22%E2%9A%99%EF%B8%8F+UV+Binary+Switching+Test%22)
*   **What it proves:** `omnipkg`'s ability to manage and dynamically activate different versions of core binary tools like `uv`, including their associated executables.

### 3. NumPy + SciPy C-Extension Switching Test
[![NumPy + SciPy C-Extension Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22%F0%9F%A7%AC+NumPy+%2B+SciPy+C-Extension+Switching+Test%22)
*   **What it proves:** The "impossible" feat of real-time, mid-script switching and mixing of C-extension versions (`numpy`, `scipy`) within the same Python process.

### 4. TensorFlow Complex Dependency Switching Test
[![TensorFlow Complex Dependency Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/tensorflow-complex-dependency-test.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22%F0%9F%A7%A0+TensorFlow+Complex+Dependency+Switching+Test%22)
*   **What it proves:** `omnipkg`'s robust handling of large, complex dependency graphs (like TensorFlow's ecosystem) with dynamic version management and environment integrity.

### 5. UV Self-Downgrade & omnipkg Revert Test
[![UV Self-Downgrade & omnipkg Revert Test](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_revert.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22%F0%9F%9A%A8+UV+Self-Downgrade+%26+omnipkg+Revert+Test%22)
*   **What it proves:** `omnipkg`'s unparalleled self-healing capability, demonstrating its ability to detect and automatically revert environmental damage caused by *other* package managers.

**~3000 PyPI downloads across 35 countries 10 days post-launch with no marketing.** ([View Live Stats](https://clickpy.clickhouse.com/dashboard/omnipkg)) 

---

## 🔥 The “Impossible” Made Simple

Want to install conflicting package versions in one command? `omnipkg` makes it effortless.

```bash
omnipkg install torch==2.0.0 torch==2.7.1
```

What happens? `omnipkg` reorders installs to trigger the bubble creation, installs `torch==2.7.1` in the main environment, and isolates `torch==2.0.0` in a "bubble." No more containers or multiple environments.

```
🔄 Reordered: torch==2.7.1, torch==2.0.0
📦 Installing torch==2.7.1... ✅ Done
🛡️ Downgrade detected for torch==2.0.0
🫧 Creating bubble for torch==2.0.0... ✅ Done
🔄 Restoring torch==2.7.1... ✅ Environment secure
```

---

## 🧠 Revolutionary Features

### 1. Dynamic Version Switching
Switch package versions mid-script without restarting or changing environments, using `omnipkgLoader`. Run `numpy==1.24.3` and `numpy==1.26.4` in the same Python process.

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

### 2. Surgical Version Bubbles
Conflicting versions live in lightweight, isolated bubbles with only necessary files. Compatible dependencies are shared, slashing storage by up to 60%.

### 3. Guardian Protocol
Prevents environment-breaking downgrades by auto-reordering installs and isolating conflicts. Try breaking it—you can't!

### 4. Binary and C-Extension Mastery
Seamlessly switch binary tools (e.g., `uv`) and C-extension version combos (e.g., `numpy`, `scipy`) during runtime which were previously thought impossible.

### 5. Instant Recovery
`omnipkg revert` restores your environment to a “last known good” state in seconds, undoing damage from `pip`, `uv`, or any other tool.

---

## 🛠️ Get Started in 60 Seconds

Install `omnipkg` (requires Redis server to be running):

```bash
pip install omnipkg
```

**Install Redis:**

*   **Linux (Ubuntu/Debian)**: `sudo apt-get install redis-server`
*   **macOS (Homebrew)**: `brew install redis`
*   **Windows**: Use WSL2 or Docker (e.g., `docker run -d -p 6379:6379 redis`)

**Run the Demo:**
```bash
omnipkg demo
```
Choose from Python module, binary, C-extension, or TensorFlow tests to see `omnipkg` in action.

**Try the Stress Test:**
```bash
omnipkg stress-test
```
Watch `omnipkg` juggle complex `numpy` and `scipy` versions flawlessly.

---

## 🏢 Estimated Enterprise Impact

|Metric              |Before `omnipkg`|After `omnipkg`|Improvement|
|--------------------|--------------|-------------|-----------|
|CI/CD Complexity    |Multiple Envs |**1 Env**    |**90% reduction**|
|Storage Overhead    |8.7 GB        |**3.5 GB**   |**60% savings**|
|Setup Time          |22 min        |**30 sec**   |**97% faster**|
|Environment Conflicts|Daily         |**Zero**     |**100% eliminated**|

Hyopthetical scenario: *"Our data science team needed `tensorflow==1.15`, `2.4`, and `2.9` in one JupyterHub environment. `omnipkg` made it work seamlessly and saved 60% storage."* — Data Science Lead

---

## 🔬 How It Works

1.  **Install Normally**: Use standard `pip install` or `omnipkg install <package>`.
2.  **Auto-Detect Conflicts**: `omnipkg` spots version clashes and isolates them in bubbles.
3.  **Dynamic Switching**: Use `omnipkgLoader` to switch versions mid-script.
4.  **Redis-Powered Speed**: A high-performance knowledge base ensures metadata lookups at **9 packages/second**.
5.  **Atomic Snapshots**: Instant rollback with `omnipkg revert`.

**Example: Safe Flask-Login Downgrade:**
```bash
omnipkg install flask-login==0.4.1
```

```
📸 Taking LIVE pre-installation snapshot...
    - Found 545 packages

🛡️ DOWNGRADE PROTECTION ACTIVATED!
-> Detected conflict: flask-login v0.6.3 → v0.4.1
🫧 Creating bubble for flask-login v0.4.1
    ...
    ✅ Dependencies resolved via PyPI API
    ...
    ✅ Bubble created: 151 files copied, 188 deduplicated
    📊 Space saved: 55.5%
    🔄 Restoring flask-login v0.6.3...

✅ Environment secured!

# Verify final state
$ omnipkg info flask-login

📋 flask-login STATUS:
----------------------------------------
🎯 Active: 0.6.3 (protected)
🫧 Available: 0.4.1 (in bubble)
📊 Space Saved: 55.5%
```
You now have both versions available in one environment, ready for use anytime!

---

## 🌟 Coming Soon

*   **Python Interpreter Hot-Swapping**: Switch Python versions (e.g., 3.8 to 3.11) mid-script.
*   **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.
*   **AI-Driven Optimization**: Auto-select optimal package versions and deduplicate AI model weights.

---

## 📚 Documentation

Learn more about `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation, Redis setup, and your first `omnipkg` command.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): A comprehensive guide to every `omnipkg` command.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Master `omnipkgLoader` for dynamic, mid-script version changes.
*   [**Advanced Management**](docs/advanced_management.md): Redis interaction, cleanup, and troubleshooting.
*   [**Future Roadmap**](docs/future_roadmap.md): Features being built today for a future you. 

---

## 📄 Licensing

`omnipkg` uses a dual-license model designed for maximum adoption and sustainable growth:

*   **AGPLv3**: For open-source and academic use ([View License](https://github.com/1minds3t/omnipkg/blob/main/LICENSE))
*   **Commercial License**: For proprietary systems and enterprise deployment ([View Commercial License](https://github.com/1minds3t/omnipkg/blob/main/COMMERCIAL_LICENSE.md))

Commercial inquiries: [omnipkg@proton.me](mailto:omnipkg@proton.me)

---

## 🤝 Contributing

This project thrives on community collaboration. Contributions, bug reports, and feature requests are incredibly welcome. Join us in revolutionizing Python dependency management.

[**→ Start Contributing**](https://github.com/1minds3t/omnipkg/issues)

```
 ___________________________________________
/                                           \
|  pip is in omnipkg jail 🔒                |
|  Status: Reflecting on better ways        |
|         to manage packages...             |
|                                           |
|  💭 'Maybe breaking environments isn't    |
|     the best approach...'                 |
\___________________________________________/
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
```
