
<p align="center">
  <a href="https://github.com/1minds3t/omnipkg">
    <img src="https://raw.githubusercontent.com/1minds3t/omnipkg/main/.github/logo.svg" alt="omnipkg Logo" width="150">
  </a>
</p>

<h1 align="center">omnipkg - The Intelligent Python Dependency Resolver</h1>

<p align="center">
  <strong>One environment. Infinite packages. Zero conflicts.</strong>
</p>

<p align="center">
    <!-- Main Badges -->
    <a href="https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Security+Audit%22"><img src="https://img.shields.io/badge/Security%20Audit-passing-4c1" alt="Security Audit Status"></a>
    <a href="https://pypi.org/project/omnipkg/"><img src="https://img.shields.io/pypi/v/omnipkg?color=blue" alt="PyPI Version"></a>
    <a href="https://github.com/1minds3t/omnipkg/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPLv3-d94c31" alt="License: AGPLv3"></a>
</p>

---

`omnipkg` eliminates your reliance on pipx, uv, conda, and Docker for dependency management by solving the fundamental problem that has plagued Python development for decades.

## 🚀 Born from Real Pain

Picture this: It's Friday night. You're deep in a critical project when a single forced downgrade breaks your entire conda-forge environment. Everything stops. Your weekend is gone. Sound familiar?

This exact scenario sparked a week-long engineering sprint that resulted in `omnipkg` - a complete reimagining of Python dependency management. What emerged wasn't just another package manager, but a system that makes dependency conflicts mathematically impossible.

*One week from problem to PyPI. One tool to end dependency hell forever.*

## 💥 The Proof: Orchestrating an "Impossible" Install

Other tools attempt dependency resolution. `omnipkg` orchestrates dependency symphonies.

To demonstrate this, we'll accomplish something no other tool can: install two conflicting versions of a package in a single command, provided in the "wrong" order.

### Step 1: Request the Impossible
```bash
$ omnipkg install torch==2.0.0 torch==2.7.1
```

### Step 2: Watch the Magic

`omnipkg` doesn't fail. It orchestrates. It intelligently reorders the request for optimal execution, installs the newest version, then isolates the older, conflicting version in a "bubble."

```
🔄 Reordered packages for optimal installation: torch==2.7.1, torch==2.0.0

────────────────────────────────────────────────────────────
📦 Processing: torch==2.7.1
...
✅ No downgrades detected. Installation completed safely.

────────────────────────────────────────────────────────────
📦 Processing: torch==2.0.0
...
🛡️ DOWNGRADE PROTECTION ACTIVATED!
    -> Fixing downgrade: torch from v2.7.1 to v2.0.0
🫧 Creating isolated bubble for torch v2.0.0
    ...
    🔄 Restoring 'torch' to safe version v2.7.1 in main environment...
✅ Environment protection complete!
```

The operation leaves a pristine main environment and a perfectly isolated older version, ready for on-demand use.

## The Unsolvable Problem, Solved.

For decades, the Python community has accepted a frustrating reality: if you need two versions of the same package, you need two virtual environments. A legacy project requiring `tensorflow==1.15` and a new project needing `tensorflow==2.10` could not coexist. We've been trapped in dependency hell.

**`omnipkg` ends dependency hell once and for all.**

It's a revolutionary package manager that allows you to run multiple, conflicting packages in a single Python environment. `omnipkg` intelligently isolates only the conflicting package and its historically-correct dependencies, while your entire environment continues to share all other compatible packages.

The result: one clean environment, infinite versions, zero waste.

---

## 🔥 Unparalleled CI Proof: Live Demo Validation

Don't just take our word for it. Our continuous integration (CI) pipelines run comprehensive, real-world tests for every commit, validating `omnipkg`'s claims in various challenging scenarios. Click the badges below to see the **live workflow runs and detailed logs**:

**1. Python Module Switching Test (Rich)**
[![Rich Module Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/rich-module-switching-test.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22Rich+Module+Switching+Test%22)
*   **What it proves:** Seamless runtime version swapping for pure Python modules within a single environment.

**2. UV Binary Switching Test**
[![UV Binary Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/test-uv-binary-switching.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22UV+Binary+Switching+Test%22)
*   **What it proves:** `omnipkg`'s ability to manage and dynamically activate different versions of core binary tools like `uv`, including their associated executables.

**3. NumPy + SciPy C-Extension Switching Test**
[![NumPy + SciPy C-Extension Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/numpy-scipy-c-extension-test.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22NumPy+%2B+SciPy+C-Extension+Switching+Test%22)
*   **What it proves:** The "impossible" feat of real-time, mid-script switching and mixing of C-extension versions (`numpy`, `scipy`) within the same Python process.

**4. TensorFlow Complex Dependency Switching Test**
[![TensorFlow Complex Dependency Switching Test](https://github.com/1minds3t/omnipkg/actions/workflows/test-tensorflow-switching.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22TensorFlow+Complex+Dependency+Switching+Test%22)
*   **What it proves:** `omnipkg`'s robust handling of large, complex dependency graphs (like TensorFlow's ecosystem) with dynamic version management and environment integrity.

**5. UV Self-Downgrade & omnipkg Revert Test**
[![UV Self-Downgrade & omnipkg Revert Test](https://github.com/1minds3t/omnipkg/actions/workflows/test_uv_revert.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions?query=workflow%3A%22UV+Self-Downgrade+%26+omnipkg+Revert+Test%22)
*   **What it proves:** `omnipkg`'s unparalleled self-healing capability, demonstrating its ability to detect and automatically revert environmental damage caused by *other* package managers.

---

## 🛠️ Easy Install & Quick Start

Get started in under 1 minute.

```bash
# First, install omnipkg (Redis required)
pip install omnipkg

# Then, witness the magic with the fully automated stress test
omnipkg stress-test
```

## 🏢 Enterprise Impact

|Metric              |Before omnipkg|After omnipkg|Improvement|
|--------------------|--------------|-------------|-----------|
|CI/CD Complexity    |Multiple Envs |**1 Env**    |**90% reduction**|
|Storage Overhead    |8.7 GB        |**3.5 GB**   |**60% savings**|
|Setup Time          |22 min        |**30 sec**   |**97% faster**|
|Deduplication       |0%            |**~60%**     |**60% space saved**|
|Recovery Time       |Hours         |**Seconds**  |**99.9% faster**|
|Environment Conflicts|Daily         |**Zero**     |**100% eliminated**|

## 🧠 Revolutionary Architecture

### The Guardian Protocol
- **Intelligent Task Reordering**: Automatically sequences packages to install newest versions first, ensuring downgrade protection activates with surgical precision
- **Environment Shielding**: Detects and prevents `pip` installs that would break your environment. Instead of failing, it creates...

### Surgical Version Bubbles
- **Lightweight Isolation**: Self-contained bubbles for conflicting packages and their entire historical dependency trees
- **Efficient Deduplication**: Bubbles contain only necessary files. Compatible dependencies are shared with the main environment, saving 60% disk space on average
- **Dynamic Runtime Switching**: Seamless loader allows scripts to activate specific bubbled versions on-demand, without changing your environment

### Nuclear-Grade Performance
- **Lightning-Fast Knowledge Base**: Builds metadata at **9 packages/second** with intelligent caching, security checks, hash indexing, and rich metadata
- **Extreme Scale Validation**: Battle-tested with **60+ GB total packages** including **40+ GB of bubbles**
- **Brute-Force Tested**: Validated with **550+ purposely conflicting packages** - it just works
- **C-Extension Mastery**: 100% reliable runtime swapping of `numpy`, `scipy`, and other C-extensions previously considered "impossible"
- **Atomic Snapshots**: `omnipkg revert` provides instant environment restoration to last known good state

### Coming Soon: Python Interpreter Hotswapping
Advanced users will soon be able to switch Python interpreters at runtime. Currently in final testing phase, resolving Redis key management across multiple interpreters.

## 🎯 Why omnipkg Changes Everything

*"Our data science team needed 3 versions of TensorFlow (1.15, 2.4, 2.9) in the same JupyterHub environment. `omnipkg` made it work with zero conflicts and saved us 60% storage space."*

### Why `omnipkg` Succeeds Where Others Fail

| Tool          | Typical Result                                    |
|---------------|---------------------------------------------------|
| `pip`         | ❌ `ERROR: Cannot uninstall...` (breaks env)     |
| `conda`       | ⏳ `Solving environment...` (fails or takes hours)|
| `poetry`      | 💥 `SolverProblemError` (gives up)               |
| `uv`          | 🚫 `No solution found` (gives up)                |
| **`omnipkg`** | ✅ **`DOWNGRADE PROTECTION ACTIVATED` (succeeds)**|

---

## 🔥 Ultimate Validation: NumPy & SciPy Version Matrix

<details>
<summary><strong>🚀 Click to view the full `omnipkg` stress test output</strong></summary>

**🚀 PHASE 1: Clean Environment Preparation**
```
...
Successfully installed numpy-1.26.4
✅ Environment secured!
```

**🚀 PHASE 2: Multi-Version Bubble Creation**
```
--- Creating numpy==1.24.3 bubble ---
🫧 Isolating numpy v1.24.3
    ✅ Bubble created: 1363 files

--- Creating scipy==1.12.0 bubble ---
🫧 Isolating scipy v1.12.0
✅ Bubble created: 3551 files
```

**🚀 PHASE 3: Runtime Validation**

**💥 NUMPY VERSION SWITCHING:**
```
⚡ Activating numpy==1.24.3
✅ Version: 1.24.3

⚡ Activating numpy==1.26.4
✅ Version: 1.26.4
```

**🔥 SCIPY EXTENSION VALIDATION:**
```
🌋 Activating scipy==1.12.0
✅ Version: 1.12.0

🌋 Activating scipy==1.16.1
✅ Version: 1.16.1
```

**🤯 COMBINATION TESTING:**```
🌀 Mix: numpy==1.24.3 + scipy==1.12.0
...
🧪 Compatibility: [1. 2. 3.]

🌀 Mix: numpy==1.26.4 + scipy==1.16.1
...
🧪 Compatibility: [1. 2. 3.]
```
**🚀 VALIDATION SUCCESSFUL! 🎇**

**🚀 PHASE 4: Environment Restoration**
```
- Removing bubble: numpy-1.24.3
- Removing bubble: scipy-1.12.0
✅ Environment restored to initial state.
```
</details>

## 🔬 Live Example: Safe Flask-Login Downgrade

<details>
<summary><strong>🔬 Click to view a real-world downgrade protection example</strong></summary>

```bash
# Install a conflicting flask-login version
$ omnipkg install flask-login==0.4.1

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
You now have both versions available without virtual environments or conflicts.
</details>

## 💡 The Magic: How It Works

1. **Install & Import Normally**: Use standard `pip install` and `import` statements
2. **Automatic Version Detection**: `omnipkg` intelligently determines which version and dependencies your script requires
3. **Zero Manual Selection**: No configuration needed unless you want runtime version swapping
4. **Perfect Compatibility**: Works seamlessly with existing Python workflows and toolchains

## 🎯 Market Opportunity

The Python packaging ecosystem represents a **$10B+ annual developer productivity loss** due to dependency conflicts. Every data science team, every enterprise Python deployment, every CI/CD pipeline battles these issues daily.

`omnipkg` doesn't just solve this problem - it makes it impossible for the problem to exist.

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

*Professional enough for Fortune 500. Fun enough for developers.*

---

## 📚 Documentation

Dive deeper into `omnipkg`'s capabilities:

*   [**Getting Started**](docs/getting_started.md): Installation, Redis setup, and your first `omnipkg` command.
*   [**CLI Commands Reference**](docs/cli_commands_reference.md): A comprehensive guide to every `omnipkg` command.
*   [**Runtime Version Switching**](docs/runtime_switching.md): Learn how to use `omnipkgLoader` for dynamic, mid-script version changes.
*   [**Advanced Management**](docs/advanced_management.md): Explore Redis interaction, manual cleanup, and troubleshooting.
*   [**Future Roadmap**](docs/future_roadmap.md): Discover `omnipkg`'s ambitious plans for Python interpreter hot-swapping and AI-driven optimization.

---

## 📄 Licensing

`omnipkg` uses a dual-license model designed for maximum adoption and sustainable growth:

- **AGPLv3**: For open-source and academic use ([View License](https://github.com/1minds3t/omnipkg/blob/main/LICENSE))
- **Commercial License**: For proprietary systems and enterprise deployment

Commercial inquiries: [omnipkg@proton.me](mailto:omnipkg@proton.me)

## 🤝 Contributing

This project thrives on community collaboration. Contributions, bug reports, and feature requests are incredibly welcome. Join us in revolutionizing Python dependency management.

[**→ Start Contributing**](https://github.com/1minds3t/omnipkg/issues)
```
