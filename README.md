<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>
<h1 align="center">omnipkg - The Ultimate Python Dependency Resolver</h1>
<p align="center">
  <strong>One environment. Any Python. Infinite packages. Zero conflicts.</strong>
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

**[See the live proof →](#2-python-interpreter-hot-swapping)**

---

<!-- COMPARISON_STATS_START -->
## ⚖️ Multi-Version Support

[![omnipkg](https://img.shields.io/badge/omnipkg-154%20Wins-brightgreen?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![pip](https://img.shields.io/badge/pip-154%20Failures-red?logo=pypi&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml) [![uv](https://img.shields.io/badge/uv-154%20Failures-red?logo=python&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)

*Multi-version installation tests run hourly. [Live results here.](https://github.com/1minds3t/omnipkg/actions/workflows/omnipkg_vs_the_world.yml)*

---
<!-- COMPARISON_STATS_END -->

## 💡 Why This Matters

**Data Science Reality**: Modern ML projects routinely need multiple TensorFlow versions, different NumPy versions, and various PyTorch builds. Traditional solutions lead to bloated storage, maintenance headaches, and deployment failures.

**Multi-Interpreter Reality**: Legacy codebases often require specific Python versions (e.g., Django on 3.8, modern ML on 3.11+). Traditional solutions force you to maintain separate environments and restart processes, killing productivity. `omnipkg` eliminates this friction entirely.

**Global Development**: Developers deserve tools that speak their language, whether debugging in Mandarin, documenting in Spanish, or troubleshooting in Hindi.

**`omnipkg` Solution**: One environment, one script, everything **just works**. Seamlessly switch between `torch==2.0.0` and `torch==2.7.1`, juggle `numpy` versions mid-script, and now, hot-swap from Python 3.12 to 3.10 instantly—all in your native language.

---

## 🧠 Revolutionary Core Features

### 1. Dynamic Package & Dependency Switching [![💥 Nuclear Test: NumPy+SciPy](https://img.shields.io/badge/💥_Nuclear_Test:NumPy+SciPy-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml)

Switch package versions mid-script using `omnipkgLoader`, without restarting or changing environments. `omnipkg` seamlessly juggles C-extension packages and even handles complex **nested dependency contexts**, a feat unmatched by other tools.

**Example: Nested `tensorflow` and `typing_extensions`:**
```python
# Switch to an older typing_extensions
with omnipkgLoader("typing_extensions==4.5.0", config=config):
    print(f"Outer context - Typing Extensions: {get_version('typing_extensions')}")
    # Switch to a specific tensorflow inside the first context
    with omnipkgLoader("tensorflow==2.13.0", config=config):
        print(f"Inner context - TensorFlow: {get_version('tensorflow')}")
        print(f"Inner context - Typing Extensions: {get_version('typing_extensions')}")
        # Model creation happens here, with a mix of versions
```

**Key CI Output (Nested Loaders):**
```bash
--- Nested Loader Test ---
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

**Example: Manual Swap & Verification:**
```bash
# See all managed Python versions
omnipkg info python

# Hot-swap to a different version
omnipkg swap python 3.11

# Your shell is now using Python 3.11, instantly.
python --version
```

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
```---

### 3. 🌍 Global Intelligence & AI-Driven Localization [![🤖 AI-Powered: 24 Languages](https://img.shields.io/badge/🤖_AI--Powered-24_Languages-brightgreen?logo=openai&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/language_test.yml)

`omnipkg` eliminates language barriers with AI localization supporting 24+ languages, making package management accessible to developers worldwide.

---

### 4. Lightweight Isolation & Downgrade Protection [![🔧 UV Multi-Version Test](https://img.shields.io/badge/🔧_UV_Multi--Version_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_install.yml)

`omnipkg` automatically isolates conflicting versions in lightweight "bubbles," preventing environment-breaking downgrades while sharing compatible dependencies to save disk space.

---

### 5. Python Library, Binary, & C-Extension Support [![💥 TensorFlow Hot-Swap](https://img.shields.io/badge/💥_TensorFlow_Hot_Swap-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test-tensorflow-switching.yml)

`omnipkg` seamlessly switches binary tools (e.g., `uv`) and complex C-extension packages (e.g., `tensorflow`, `numpy`, `scipy`) during runtime.

---
### 6. Deep Package Intelligence [![🔍 Package Discovery Demo](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/knowledge_base_check.yml)

`omnipkg` builds a knowledge base with 60+ metadata fields per package version, stored in Redis for instant analysis across different Python interpreters.

**Example: Viewing Package Info Across Interpreters:**
```bash
# Switch to Python 3.12 context
omnipkg swap python 3.12
omnipkg status
# 🌍 Main Environment: Active Packages: 58
# 📦 izolasyon Alanı (Bubbles): No isolated package versions found.

# Switch to Python 3.11 context
omnipkg swap python 3.11
omnipkg status
# 🌍 Main Environment: Active Packages: 373
# 📦 izolasyon Alanı (Bubbles): Isolated Package Versions (54 bubbles)
```

---

### 7. Instant Environment Recovery
[![🛡️ UV Revert Test](https://img.shields.io/badge/🛡️_UV_Revert_Test-passing-success)](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_revert.yml)

If an external tool (like `pip`) causes damage, `omnipkg revert` restores your environment to a "last known good" state in seconds.

---
### 🏗️ The Architecture: How Hot-Swapping Works

Solving interpreter hot-swapping required a complete architectural reimagining. The core challenges we solved:

*   **The State Problem**: Python interpreters maintain complex internal state. Our solution creates isolated execution contexts while maintaining a seamless user experience.
*   **The Control Plane Solution**: All sensitive operations execute through a dedicated Python 3.11 "control plane," ensuring reliability regardless of your active interpreter version.
*   **Native Adoption Breakthrough**: The biggest user friction point—getting "stuck" after switching away from your native Python—is eliminated by automatically managing your existing interpreter from day one.

---

## 🛠️ Get Started in 60 Seconds

### Step 1: Start Redis (Required) <img src="https://img.shields.io/badge/Redis-Required-%2523DC382D?logo=redis&logoColor=white" alt="Redis Required">
`omnipkg` uses Redis for fast metadata management. It **must be running**.

*   **Docker (Recommended)**:
    ```bash
    docker run -d -p 6379:6379 --name redis-omnipkg redis
    ```
*   Verify Redis: `redis-cli ping` (should return `PONG`)

### Step 2: Install `omnipkg`
```bash
pip install omnipkg
```

### Step 3: Run the Demos
See package version juggling in action:
```bash
omnipkg demo
```

### Step 4: Experience Python Hot-Swapping
```bash
# Let omnipkg manage your native Python automatically
omnipkg status
# 🎯 Your native Python is now managed!

# See available interpreters
omnipkg info python

# Install a new Python version if needed
omnipkg python adopt 3.10

# Hot-swap your entire shell context
omnipkg swap python 3.10
python --version  # Now Python 3.10.x
```

---

## 🔬 How It Works (Simplified Flow)

1.  **Adopt Interpreters**: On first run, `omnipkg` automatically adopts your native Python. Add more with `omnipkg python adopt <version>`.
2.  **Install Packages**: Use `omnipkg install uv==0.7.13 uv==0.7.14`.
3.  **Conflict Detection**: `omnipkg` spots version clashes and isolates them in bubbles.
4.  **Dynamic Package Switching**: Use `omnipkgLoader` to switch package versions mid-script.
5.  **Interpreter Hot-Swapping**: Switch your shell's active Python instantly with `omnipkg swap python <version>`.
6.  **Redis-Powered Speed**: A high-performance knowledge base enables instant lookups and environment analysis.
7.  **Atomic Snapshots**: Instant rollback with `omnipkg revert`.

---

## 🌟 Coming Soon

*   **Time Machine Technology for Legacy Packages**: Install ancient packages with historically accurate build tools and dependencies that are 100% proven to work in any environment.
*   **Bubble Validation**: Ensuring your bubbled packages are stored with functional dependencies by testing during installs.
*   **Enhanced CI Workflows**: Public CI demonstrating mid-script interpreter hot-swapping.

---

## 📚 Documentation

Learn more about `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation and setup.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): All `omnipkg` commands.
*   [**Python Hot-Swapping Guide**](docs/python_hot_swapping.md): Master multi-interpreter switching.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Master `omnipkgLoader`.
*   [**Advanced Management**](docs/advanced_management.md): Redis interaction and troubleshooting.
*   [**Future Roadmap**](docs/future_roadmap.md): Features being built today - for you!

---

## 📄 Licensing

`omnipkg` uses a dual-license model:

*   **AGPLv3**: For open-source and academic use ([View License](https://github.com/1minds3t/omnipkg/blob/main/LICENSE)).
*   **Commercial License**: For proprietary systems and enterprise deployment ([View Commercial License](https://github.com/1minds3t/omnipkg/blob/main/COMMERCIAL_LICENSE.md)).

Commercial inquiries: [omnipkg@proton.me](mailto:omnipkg@proton.me)

---

## 🤝 Contributing

This project thrives on community collaboration. Contributions, bug reports, and feature requests are incredibly welcome.

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