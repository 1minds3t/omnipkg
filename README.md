# omnipkg - The Intelligent Python Package Manager
> One environment. Infinite versions. Zero conflicts.

<p align="center">
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg.svg" alt="PyPI version">
  </a>
  <a href="https://www.gnu.org/licenses/agpl-3.0">
    <img src="https://img.shields.io/badge/License-AGPLv3-red.svg" alt="License: AGPLv3">
  </a>
  <a href="https://github.com/1minds3t/omnipkg/actions/workflows/security_audit.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/1minds3t/omnipkg/security_audit.yml?branch=main" alt="Security Audit">
  </a>
</p>

---

omnipkg lets you install any version of any package — no breakage, no downgrades, no workarounds, or needing Conda, Docker, or `pipx`. **Dependency hell? Eliminated.**

## Installation & Quick Start

```bash
pip install omnipkg
```

See the magic in action. This command runs a fully automated demo showcasing downgrade protection and version isolation:

```bash
omnipkg demo
```

---

### 🔬 Demo Output: See omnipkg in Action
```
# The demo first shows what happens with standard pip...
💀 pip install flask-login==0.4.1
...
💥 PIP RESULT:
   ❌ Uninstalled flask-login 0.6.3
   ❌ Downgraded Flask and Werkzeug
   ❌ Environment compromised

# Then, omnipkg's solution...
🧠 omnipkg install flask-login==0.4.1
🫧 Creating protective bubble for v0.4.1...
✅ Installation successful!
🎯 Both versions coexist peacefully!
```

---

### 🔥 Nuclear Stress Test: Can Your Package Manager Survive This
Witness omnipkg handling complex scenarios with the built-in stress test. This real-world example demonstrates seamless activation of incompatible C-extension libraries:

```bash
omnipkg stress-test
```

<details>
<summary><strong>View full stress test output</strong></summary>

```
# Creating bubbles for conflicting versions...
--- Creating bubble for numpy==1.24.3 ---
✅ Bubble created: 1363 files copied
--- Creating bubble for scipy==1.12.0 ---
✅ Bubble created: 3551 files copied

# Executing version juggling...
💥 NUMPY VERSION SWITCHING:

⚡ Activating numpy==1.24.3
   ✅ Version: 1.24.3
   🔢 Array sum: 6

⚡ Activating numpy==1.26.4
   ✅ Version: 1.26.4
   🔢 Array sum: 6

🔥 SCIPY C-EXTENSION TEST:

🌋 Activating scipy==1.12.0
   ✅ Version: 1.12.0
   ♻️ Sparse matrix: 3 non-zeros

🌋 Activating scipy==1.16.1
   ✅ Version: 1.16.1
   ♻️ Sparse matrix: 3 non-zeros

🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇
```
</details>

---

## 🚀 Core Features

-   🛡️ **Downgrade Protection**: Isolates conflicting versions into protected "bubbles"
-   💾 **Intelligent Deduplication**: Saves up to 60% disk space on bubbled packages
-   ⚡ **Redis-Backed Knowledge Base**: Lightning-fast package version lookups
-   🔀 **Runtime Version Switching**: Activate any version on-the-fly
-   🧪 **Battle-Tested**: Handles massive environments (520+ packages, 15GB+) reliably

---

## How It Works

When a conflict is detected:
1.  **Intercepts** the installation request
2.  **Isolates** conflicting dependencies in a deduplicated "bubble"
3.  **Preserves** your main environment integrity
4.  **Enables** runtime version switching

<details>
<summary><strong>Real-World Example: Downgrading PyTorch</strong></summary>

```bash
$ omnipkg install torch==2.7.0
🛡️  DOWNGRADE PROTECTION ACTIVATED!
🫧 Creating isolated bubble for torch v2.7.0
✅ Dependencies resolved via PyPI API
📊 Space efficiency: 16.5% saved
🔄 Restored torch v2.7.1 in main environment
✅ Environment protected!
```
</details>

---

## Why omnipkg Succeeds Where Others Fail

| Tool          | Result                                |
|---------------|---------------------------------------|
| `pip`         | ❌ `Cannot uninstall...`              |
| `conda`       | ⏳ `Solving environment...` (hours)   |
| `poetry`      | 💥 `SolverProblemError`               |
| `uv`          | 🚫 `No solution found`                |
| **`omnipkg`** | ✅ **`DOWNGRADE PROTECTION ACTIVATED`** |

---

## 📜 Licensing

`omnipkg` uses a dual-license model:

- **AGPLv3**: For open-source and academic use ([View License](https://www.gnu.org/licenses/agpl-3.0))
- **Commercial License**: For proprietary systems and organizations

**Commercial inquiries:** [omnipkg@proton.me](mailto:omnipkg@proton.me)

---

```bash
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

> *Professional enough for enterprises, fun enough for developers*
```