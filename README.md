# omnipkg: The Intelligent Python Package Manager
One environment. Infinite versions. Zero conflicts.
<p align="center">
<a href="https://github.com/1minds3t/omnipkg/actions/workflows/test.yml">
<img src="https://img.shields.io/github/actions/workflow/status/1minds3t/omnipkg/test.yml?branch=main" alt="Build Status">
</a>
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
omnipkg lets you install any version of any package without breaking your environment, downgrading dependencies, or needing Conda, Docker, or pipx. Dependency hell? Obliterated.

Installation & The 30-Second Demo
Get started in seconds. After installing, run the interactive demo to see the magic for yourself.

Generated bash
pip install omnipkg
omnipkg demo

content_copy

download
Use code with caution.
Bash
The demo will guide you through a real-world dependency conflict, showcasing how omnipkg isolates the problem version instead of breaking your environment.

<details>
<summary><strong>🔬 Click to see what the demo shows you</strong></summary>
Generated bash
# The demo first shows you what happens when you use pip...
💀 You: pip install flask-login==0.4.1
...
💥 BOOM! Look what pip did:
   ❌ Uninstalled flask-login 0.6.3
   ❌ Downgraded Flask and Werkzeug
   ❌ Your modern project is now BROKEN

# Then, it shows you the omnipkg way...
🧠 Smart choice! Using omnipkg instead...
🫧 Creating a protective bubble for the old version...
$ omnipkg install flask-login==0.4.1
✅ omnipkg install successful!
🎯 BOTH versions now coexist peacefully!

content_copy

download
Use code with caution.
Bash
</details>
🔥 The Gauntlet: Surviving the Stress Test
Talk is cheap. Here’s what happens when omnipkg is pushed to its absolute limit with the built-in omnipkg stress-test command. This isn't a simulation; it's omnipkg seamlessly activating different, often incompatible, versions of C-extension-heavy libraries in the same Python process.

<details>
<summary><strong>🤯 Click to view the full stress test output.</strong></summary>
Generated bash
# Creating bubbles for older, conflicting versions...
--- Creating bubble for numpy==1.24.3 ---
✅ Bubble created: 1363 files copied, 0 deduplicated.
--- Creating bubble for scipy==1.12.0 ---
✅ Bubble created: 3551 files copied, 0 deduplicated.

# Executing the test...
💥 NUMPY VERSION JUGGLING:

⚡ Switching to numpy==1.24.3
🌀 omnipkg loader: Activating numpy==1.24.3...
 ✅ Activated bubble: /path/to/.omnipkg_versions/numpy-1.24.3
   ✅ Version: 1.24.3
   🔢 Array sum: 6

⚡ Switching to numpy==1.26.4
🌀 omnipkg loader: Activating numpy==1.26.4...
 🧹 Deactivated bubble: numpy-1.24.3
 ✅ Activated bubble: /path/to/.omnipkg_versions/numpy-1.26.4
   ✅ Version: 1.26.4
   🔢 Array sum: 6

🔥 SCIPY C-EXTENSION TEST:

🌋 Switching to scipy==1.12.0
🌀 omnipkg loader: Activating scipy==1.12.0...
 ✅ Activated bubble: /path/to/.omnipkg_versions/scipy-1.12.0
   ✅ Version: 1.12.0
   ♻️ Sparse matrix: 3 non-zeros

🌋 Switching to scipy==1.16.1
🌀 omnipkg loader: Activating scipy==1.16.1...
 🧹 Deactivated bubble: scipy-1.12.0
 ✅ System version already matches requested version (1.16.1). No bubble activation needed.
   ✅ Version: 1.16.1
   ♻️ Sparse matrix: 3 non-zeros

 🚨 OMNIPKG SURVIVED NUCLEAR TESTING! 🎇

content_copy

download
Use code with caution.
Bash
</details>
🚀 Core Features
🛡️ Downgrade Protection: Stops pip from nuking your environment by isolating conflicting versions into protected "bubbles."
💾 Intelligent Deduplication: Saves up to 60% disk space on bubbled packages while keeping native C extensions stable and separate.
🧠 Redis-Backed Knowledge Base: Lightning-fast lookups for all package versions, dependencies, and security info.
🔀 Runtime Version Switching: Activate any bubbled package version on the fly, even within the same script, using the built-in loader.
🧪 Battle-Tested: Proven to handle massive environments (520+ packages, 95+ bubbles, 15.4GB+) without flinching.
How It Works
When a downgrade is detected, omnipkg performs surgery:

Intercepts the request.
Installs the conflicting version and its entire dependency tree into a temporary location.
Creates a space-efficient, deduplicated "bubble" in .omnipkg_versions.
Restores the original package in your main environment, leaving it pristine.
The result: a perfectly stable global environment, with every version you've ever needed on standby.

Why Other Tools Fail
Tool	The Task: install old-conflicting-package	Result
pip	❌	ERROR: Cannot uninstall...
conda	⏳	Solving environment... (for hours)
poetry	💥	SolverProblemError
uv	🚫	No solution found for the request
omnipkg	✅	DOWNGRADE PROTECTION ACTIVATED!
📜 Licensing
omnipkg is available under a dual-license model.

Community Edition (AGPLv3): Perfect for individual developers, open-source projects, and academic use. If you use omnipkg in a project that is also open-source under a compatible license, you're good to go.
Commercial License: Required for use in closed-source commercial software or for any organization that cannot comply with the AGPLv3. This license allows you to integrate omnipkg without the obligation to open-source your own code.
→ To inquire about a commercial license, please contact: omnipkg@proton.me
