
---

# omnipkg: The Intelligent Python Package Manager
> One environment. Infinite versions. Zero conflicts.

<p align="center">
  <a href="https://github.com/omnipkg/omnipkg/actions/workflows/test.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/omnipkg/omnipkg/test.yml?branch=main" alt="Build Status">
  </a>
  <a href="https://pypi.org/project/omnipkg/">
    <img src="https://img.shields.io/pypi/v/omnipkg.svg" alt="PyPI version">
  </a>
  <a href="https://www.gnu.org/licenses/agpl-3.0">
    <img src="https://img.shields.io/badge/License-AGPLv3-red.svg" alt="License: AGPLv3">
  </a>
  <a href="https://github.com/omnipkg/omnipkg/actions/workflows/security_audit.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/omnipkg/omnipkg/security_audit.yml?branch=main" alt="Security Audit">
  </a>
</p>

[![Security Audit](https://github.com/1minds3t/omnipkg/actions/workflows/security_audit.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/security_audit.yml)
[![Build Status](https://github.com/1minds3t/omnipkg/actions/workflows/publish.yml/badge.svg)](https://github.com/1minds3t/omnipkg/actions/workflows/publish.yml)
[![PyPI version](https://img.shields.io/pypi/v/omnipkg.svg)](https://pypi.org/project/omnipkg/)


---

`omnipkg` lets you install *any version* of *any package* without breaking your environment, downgrading dependencies, or needing Conda, Docker, or `pipx`. **Dependency hell? Obliterated.**

---
## See It to Believe It (30 Second Demo)

<!-- A GIF is the best format for this. It shows the tool in action without taking up a ton of space. -->
![omnipkg Demo GIF](https://user-images.githubusercontent.com/your-image-url-here.gif) 
*This shows `omnipkg install old-package`, downgrade protection activating, and `omnipkg status` confirming both versions coexist.*

---

## 🚀 Features

-   🛡️ **Downgrade Protection**: Stops `pip` from nuking your environment by isolating conflicting versions into protected "bubbles."
-   💾 **Intelligent Deduplication**: Saves up to 60% disk space on bubbled packages while keeping native C extensions stable.
-   🧠 **Redis-Backed Knowledge Base**: Lightning-fast lookups for all package versions, dependencies, and security info.
-   🔀 **Runtime Version Switching**: Activate any bubbled package version on the fly, even within the same script.
-   🧪 **Battle-Tested**: Proven to handle massive environments (520+ packages, 95+ bubbles, 15.4GB+) without flinching.

---

## How Is This Possible?

When a downgrade is detected, `omnipkg` performs surgery:
1.  **Intercepts** the request.
2.  **Installs** the conflicting version and its entire dependency tree into a temporary, isolated location.
3.  **Creates** a space-efficient, deduplicated "bubble" in `.omnipkg_versions`.
4.  **Restores** the original package in your main environment.

The result: a perfectly stable global environment, with every version you've ever needed on standby.

<details>
<summary><strong>🔬 Click for a Real-World Example: Downgrading PyTorch</strong></summary>

```bash
# User wants to install an older torch version
$ omnipkg install torch==2.7.0

# ... (omnipkg detects the downgrade) ...
🛡️  DOWNGRADE PROTECTION ACTIVATED!
-> Fixing downgrade: torch from v2.7.1 to v2.7.0
🫧 Creating isolated bubble for torch v2.7.0
✅ Success: Dependencies resolved via PyPI API.
🧹 Creating deduplicated bubble...
⚠️  Disabling deduplication for native package: torch
✅ Bubble created: 16241 files copied, 3211 deduplicated.
📊 Space efficiency: 16.5% saved.
🔄 Restoring ‘torch’ to safe version v2.7.1 in main environment…

✅ Environment protection complete!
```
</details>

---

## Why Other Tools Fail

| Tool          | The Task: `install old-conflicting-package` | Result                                |
|---------------|---------------------------------------------|---------------------------------------|
| `pip`         | ❌                                          | `ERROR: Cannot uninstall...`          |
| `conda`       | ⏳                                          | `Solving environment...` (for hours)  |
| `poetry`      | 💥                                          | `SolverProblemError`                  |
| `uv`          | 🚫                                          | `No solution found for the request`   |
| **`omnipkg`** | ✅                                          | **`DOWNGRADE PROTECTION ACTIVATED!`** |

---

## 📜 Licensing

`omnipkg` is available under a dual-license model to suit different needs.

-   **Community Edition (AGPLv3):** Perfect for individual developers, open-source projects, and academic use. If you use `omnipkg` in a project that is also open-source under a compatible license, you're good to go. The source code is available in this repository under the [GNU AGPLv3](LICENSE).

-   **Commercial License:** Required for use in closed-source commercial software, proprietary systems, or for any organization that cannot comply with the terms of the AGPLv3. This license allows you to integrate `omnipkg` without the obligation to open-source your own code.

    → **To inquire about a commercial license, please contact:** [**omnipkg@proton.me**](mailto:omnipkg@proton.me)