# Changelog

# Changelog

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.1] - 2026-01-07

### 🔥 Critical Fix
- **Fixed streaming output for interactive documentation console**
  - Changed `/execute` endpoint from buffered `subprocess.run()` to streaming `subprocess.Popen()`
  - Implemented Server-Sent Events (SSE) for real-time line-by-line output delivery
  - Prevents timeout/hanging issues during command execution in web-based docs
  - Essential for demo functionality and user experience in interactive console

### Added
- **Interactive Documentation Structure**
  - New CLI Commands section with detailed command reference
  - Platform Support section documenting 70+ CI builds
  - Interactive Demos section with 4 live examples:
    - C Extension Version Switching
    - Rich Module Styling Demonstration
    - TensorFlow Dependency Management
    - UV Binary Hot-Swapping
  - All docs include YAML front matter with metadata
  - Embedded terminal with real-time command execution

- **Bridge API Improvements**
  - Added health check telemetry logging to SQLite database
  - Cloudflare Worker telemetry forwarding (fire-and-forget pattern)
  - DEV_MODE now allows missing Origin header for curl/testing
  - Expanded CORS origins to support local development:
    - `http://localhost:5000` (MkDocs dev server)
    - `http://127.0.0.1:8085` (Bridge local testing)
    - `https://omnipkg.workers.dev` (Cloudflare Worker)

- **Enhanced MkDocs Configuration**
  - Fixed `site_url` for Cloudflare Pages deployment
  - Added modern navigation features:
    - `navigation.instant` - SPA-like feel
    - `navigation.tracking` - URL updates on scroll
    - `navigation.sections` - Better sidebar grouping
    - `navigation.expand` - Auto-expand details
    - `navigation.indexes` - Clickable section headers
    - `toc.follow` - TOC highlights during scroll
  - New plugins: mkdocs-awesome-pages, mkdocs-macros, mkdocs-minify, mkdocs-redirects
  - Added `meta` extension for YAML front matter parsing
  - Mermaid diagram support via pymdownx.superfences

### Changed
- **execute_omnipkg_command()** now yields output as generator instead of returning complete string
- **/execute** endpoint returns SSE stream instead of JSON response
- Updated package description to include interactive documentation links
- Documentation URLs changed from readthedocs.io to omnipkg.pages.dev
- Improved error handling with streaming context in bridge API
- Updated aiohttp dependency constraint to `>=3.13.3`

### Fixed
- Interactive docs console no longer hangs waiting for command completion
- Real-time output now displays progressively instead of all-at-once
- CORS validation logic improved for production vs development modes
- Streaming responses properly handle errors and timeouts

### Documentation
- Added 15 new markdown files with interactive content
- Reorganized docs into logical sections with `.pages.yml` navigation
- All documentation pages include builder metadata headers
- Comprehensive platform support matrix with live CI links
- Step-by-step demo walkthroughs with copy-paste commands

### Package Metadata
- Version bump: 2.1.0 → 2.1.1
- Updated conda recipes with new PyPI SHA256 hash
- Added `flask` and `flask-cors` to dependencies
- Updated package URLs to point to new documentation site

---

## [v2.1.0] - 2026-01-05

### 🚀 Release v2.1.0 — Executable Documentation & Hybrid Local Cloud

**OmniPkg is no longer just a package manager. It is now an execution platform.**

This release introduces **Executable Documentation**: a secure hybrid architecture that allows users to run real OmniPkg commands directly from the documentation website — with execution happening on their own machine, not in the cloud.

Static docs are dead. Your environment is now the runtime.

### ✨ New Features

#### Executable Documentation
Documentation pages now include live “Run” buttons that execute the exact command being shown and stream real output back to the browser.
*   No copy/paste
*   No terminal switching
*   No “works on my machine”

#### OmniPkg Web Bridge
A new local service that securely connects your browser to your machine:
*   Runs as a local Flask service (`omnipkg web start`)
*   Executes commands in a constrained subprocess
*   Streams stdout/stderr live
*   Enforces strict CORS + allowlisting
*   Requires no open ports

#### Hybrid Cloud–Local Architecture
OmniPkg now spans three layers — without centralizing compute:
1.  **Cloudflare Pages:** Static docs, UI, WASM, zero trust
2.  **Cloudflare Worker:** Edge proxy + routing
3.  **Local Bridge:** Actual execution on your machine

#### Tailscale Remote Execution
If the user is on the same Tailnet, commands can be executed from any device (Phone → Browser → Edge → Tailscale → Local machine). Fully end-to-end encrypted via WireGuard.

#### Privacy-First Telemetry (Local-Only)
Telemetry has been redesigned from the ground up:
*   Stored locally in `~/.omnipkg/telemetry.db`
*   Tracks command names and UI interactions only
*   No IP addresses, no environment data, no cloud persistence.

### 🧰 New CLI Commands

*   `omnipkg web start`: Start the local web bridge
*   `omnipkg web stop`: Stop it cleanly
*   `omnipkg web status`: Health, PID, uptime, URL
*   `omnipkg web logs -f`: Follow live execution + telemetry

### 🔒 Security Model
*   Strict CORS enforcement
*   Command allowlisting
*   No arbitrary shell execution
*   Local-only execution by default
*   Tailscale required for remote access

### 📊 Stats
*   Files changed: 25
*   Insertions: 2,131
*   Deletions: 333

## [2.0.0] - 2025-12-08 - "The Singularity"

**The "Hypervisor" Update.**
This release marks a fundamental paradigm shift from "Package Loader" to "Distributed Runtime Architecture." OmniPkg 2.0 introduces a persistent daemon kernel, universal GPU IPC, and hardware-level isolation, effectively functioning as an Operating System for Python environments.

### 🚀 Major Architectural Breakthroughs
*   **Universal GPU IPC (Pure Python/ctypes):**
    *   Implemented a custom, framework-agnostic CUDA IPC protocol (`UniversalGpuIpc`) using raw `ctypes`.
    *   **Performance:** Achieved **~1.5ms latency** for tensor handoffs, beating PyTorch's native IPC by ~30% and Hybrid SHM by **800%**.
    *   Enables true zero-copy data transfer between isolated processes without relying on framework-specific hooks.
*   **Persistent Worker Daemon ("The Kernel"):**
    *   Replaced ad-hoc subprocess spawning with a persistent, self-healing worker pool (`WorkerPoolDaemon`).
    *   Reduces environment context switching time from **~2000ms** (process spawn) to **~60ms** (warm activation).
    *   Implements an "Elastic Lung" architecture: Workers morph into required environments on-demand and purge themselves back to a clean slate.
*   **Selective Hardware Virtualization (CUDA Hotswapping):**
    *   Implemented dynamic `LD_LIBRARY_PATH` injection at the worker level.
    *   The daemon now scans active bubbles to inject the **exact** CUDA runtime libraries required by the specific framework version (e.g., loading CUDA 11 libs for TF 2.13 while the host runs CUDA 12).
    *   **Result:** Successfully ran TensorFlow 2.12 (CPU), TF 2.13 (CPU), and TF 2.20 (GPU) **simultaneously** in a single orchestration flow without crashing.

### ⚡ Core Enhancements
*   **Fail-Safe Cloaking:** Added `_force_restore_owned_cloaks()` to guarantee filesystem restoration even during catastrophic process failures or OOM events.
*   **Global Shutdown Silencer:** Implemented an `atexit` hook that synchronizes CUDA contexts and silences the C++ "driver shutting down" noise, ensuring clean exit codes for CI/CD.
*   **Composite Bubble Injection:** The loader now automatically constructs "Meta-Bubbles" at runtime, merging the requested package bubble with its binary dependencies (NVIDIA libs, Triton) on the fly.

### 🐛 Critical Fixes
*   **PyTorch 1.13+ Compatibility:** Patched the worker daemon to handle `TypedStorage` serialization changes in newer PyTorch versions, preventing crashes during native IPC.
*   **Deadlock Prevention:** Implemented `ThreadPoolExecutor` in the daemon manager to allow recursive worker calls (Worker A calling Worker B) without deadlocking the socket.
*   **Lazy Loading:** Made `psutil` and `torch` imports lazy within the daemon to prevent "poisoning" the process with default environment versions before isolation takes effect.

### 📊 Benchmarks (vs v1.x)
*   **IPC Speed:** 14ms (v1 Hybrid) → **1.5ms** (v2 Universal).
*   **Context Switch:** 2.5s (v1 Cold) → **0.06s** (v2 Warm).
*   **Concurrency:** Validated "Framework Battle Royale" (NumPy 1.x + 2.x + Torch + TF running concurrently).

---

## [1.6.0]

# omnipkg v1.6.0: The Quantum Lock & Concurrency Release

After a monumentally productive weekend and over **70 developer commits**, this release transforms omnipkg from a powerful tool into a battle-hardened, production-grade orchestrator. This isn't just an update; it's a foundational rewrite of the core engine, focused on eliminating race conditions, conquering state corruption, and achieving true, safe concurrency on all platforms, including Windows.

With over **6,000 lines of code changed**, this release introduces an entirely new level of intelligence, compatibility, and resilience to the system.

## 🚀 New Features & Major Architectural Victories

### True, Multi-Platform Concurrency: The "Impossible" Achieved

Omnipkg now fully supports simultaneous, parallel operations without corrupting its own state. The "Quantum Multiverse" is no longer just a concept—it's a reality. Our Windows CI now proves that **three concurrent threads can simultaneously swap to different Python versions, install different package versions, and operate in the same environment without a single failure.**

This was made possible by a ground-up re-architecture of state management:
- **Atomic Registry Operations:** All writes to the interpreter `registry.json` are now protected by file locks and atomic move operations.
- **The "Admin vs. Worker" Firewall:** A critical safety rule has been implemented. The "native" interpreter is a protected "admin" context, and "worker" contexts (e.g., a thread on Python 3.9) are forbidden from modifying the native environment, solving the primary source of self-syncing bugs and race conditions.

### Intelligent, Trustworthy Self-Healing

The "zombie state"—where an interpreter exists on disk but is unknown to the registry—has been eradicated. The core commands are now self-aware and capable of healing the system.
- **Smart `swap` Command:** Now features a multi-tiered fallback that will **automatically trigger a full filesystem rescan** to find and register "zombie" interpreters before proceeding.
- **Hardened `adopt` & `remove`:** These commands are now fully transactional, performing a final rescan to verify the ground truth before reporting success. The system will never lie to you again.

### Full Python 3.7+ Compatibility & Next-Gen Resolution

- **Legacy Project Support:** Omnipkg now fully supports managing projects running on Python 3.7. The entire dependency chain has been updated, and omnipkg can now download and manage standalone Python 3.7 interpreters.
- **Smarter Dependency Resolution:** Omnipkg now intelligently calculates the correct intersection of version requirements (e.g., `numpy<2.0` vs. `numpy>=1.26`).

### Platform-Aware Intelligence & User Experience

- **Platform-Aware Wheel Selection:** Omnipkg now inspects all available package files, parsing wheel tags to select the best binary wheel for your specific OS, CPU architecture, and Python version.
  > *Pip may still be a reckless time traveler, but with omnipkg, it's now carrying the right passport.*
- **"Return to Origin" Install Guarantee:** The `install` command now automatically returns you to your original Python context after a "Quantum Healing" event.
- **Blazing Fast Startup (204x Faster Self-Heal):** The startup self-heal check has been optimized with a multi-tiered caching strategy, reducing its execution time from **138ms down to a mere 0.677ms** on a cache hit.

## 📝 Important Notes & Known Issues

- **Native Interpreter Sync:** To ensure maximum safety and respect for the user's environment, the self-healing mechanism will sync all *managed* interpreters automatically, but it will **not** automatically upgrade the *native* `omipkg` installation. This is intentional. To upgrade the native installation, please use the explicit `omnipkg upgrade omnipkg` command, or `pip install -e .` for developers.
- **Python 3.7 Self-Heal:** While Python 3.7 is now fully supported for adoption, installation, and swapping, the self-healing logic for identifying it as a "native" interpreter is still under development. This is a known issue that will be resolved in an upcoming patch release.

## 🔮 What's Next: Activating "Quantum Installation"

The individual pieces of our next great leap are already here. This release doesn't just promise future features; it ships the proven, foundational technology for them.

The `install` command's **"Quantum Healing"** engine can already perform fully autonomous, cross-interpreter installations—detecting Python version incompatibilities, adopting the correct Python version, installing the package, and seamlessly returning to the original context. The `run` command's **auto-healing loader** can already activate version "bubbles" at runtime.

The next major step is to **integrate these two proven technologies.**

In a near-future release, the `omnipkg run` loader will be wired directly into the Quantum Healing engine. When a script `import`s a package that requires a completely different version of Python, the loader won't just fail—it will trigger the full, cross-dimensional installation workflow that `omnipkg install` uses today.

The architecture is built. The engine is battle-tested. The final integration is the next logical step. The multiverse is not just expanding; it's becoming fully interactive.

## [1.5.8] - 2025-10-26

### 🌟 Major New Features

#### 🌌 Quantum Healing for Python Versions
The most groundbreaking feature in `omnipkg` history: **automatic Python version conflict resolution**. When a package is incompatible with your current Python version, `omnipkg` now:
- Automatically detects the version incompatibility
- Finds a compatible Python version
- Adopts or downloads the required interpreter
- Switches the environment context seamlessly
- Retries the installation—all in a single command with **zero user intervention**

No more cryptic "requires Python <3.11" errors. Just install and go.

#### 🤖 AI Import Healer
A revolutionary pre-script utility that automatically detects and removes AI-generated "hallucinated" placeholder imports like `from your_file import ...`. This prevents an entire class of frustrating runtime errors caused by AI code assistants suggesting non-existent modules.

#### ⚡️ Ultra-Fast Preflight Checks
Installation is now **dramatically faster** for already-satisfied packages:
- Sub-millisecond satisfaction checks before initializing the full dependency resolver
- Runs with already-installed packages are now nearly instantaneous
- Massive performance improvement for CI/CD pipelines and repeated installs

### ✨ New Features & Enhancements

- **Flask Port Finder & Auto-Healing**: New advanced demo utility that finds open ports for Flask applications and automatically heals missing dependencies during test runs
- **Comprehensive `upgrade` Command**: Fully implemented `omnipkg upgrade` for both self-upgrades and upgrading any managed package
- **Enhanced `run` Command**: 
  - Added `--verbose` flag for detailed execution logging
  - Clearer AI-facing status messages for success, test failures, and healing attempts
  - Better integration with automated workflows
- **Concurrency Optimization**: Test suite now runs concurrent tests in under 500ms by eliminating unnecessary subprocess calls

### 🐛 Bug Fixes

- **Critical Windows Socket Fix**: Resolved socket handling issues in the `run` command on Windows platforms
- **First-Time Setup**: Fixed `AttributeError` that could occur during initial environment setup
- **Uninstall Reliability**: Fixed edge cases where the `uninstall` command could fail
- **Self-Upgrade Logic**: Improved to work correctly for both standard and editable developer installs
- **Dependency Resolver**: Added fallbacks and better error handling for PyPI queries
- **Path Integrity**: Fixed path handling to preserve native Python environment integrity during context swaps
- **Loader TypeError**: Resolved loader issues and prevented recursive `omnipkg` calls within bubbles

### 🔧 CI/CD & Development Experience

#### 🚀 Massive CI Expansion
Added **10+ new GitHub Actions workflows** for comprehensive automated testing:
- Package upgrade testing across multiple scenarios
- Cross-interpreter installation tests (Quantum Healing validation)
- `omnipkg` self-upgrade verification
- Flask port finder and auto-healing demos
- Windows concurrency stress tests
- Automatic Docker image builds and pushes to Docker Hub and GHCR on release
- **Parallel Python Priming on Windows**: Environments now prime in parallel, dramatically speeding up CI runs

#### 🤖 Automation Improvements
- **Auto-Update `requirements.txt`**: CI automatically updates `requirements.txt` via `pip-compile` when `pyproject.toml` changes
- **Enhanced Test Suite**: Complete refactor for better robustness, debugging capabilities, and performance
- **Better Error Reporting**: More actionable error messages and clearer failure indicators

### 🏗️ Architecture & Refactoring

- **Core Installation Overhaul**: Completely redesigned installation logic for better performance and reliability
- **Unified Run/Loader Logic**: Synced and refactored from the `developer-port` branch for consistency
- **Security Scanning**: Improved with `pip-audit` fallback for better vulnerability detection
- **Code Organization**: Improved project structure, documentation, and file organization
- **Cleaned Up Repo**: Removed obsolete files and consolidated commit identities

### 📊 Statistics

- **100+ commits** merged since v1.5.7
- **28 files changed**
- **5,096 insertions**, 1,340 deletions (net +3,756 lines)
- **10+ new CI/CD workflows**
- Test suite performance improved by **>90%** for concurrent operations

### 🎯 Breaking Changes

None! This release maintains full backward compatibility with v1.5.7.

### 📝 Notes

This is the largest single release in `omnipkg` history, representing months of development across performance, reliability, and developer experience. The Quantum Healing feature alone represents a paradigm shift in how Python package managers handle version conflicts.

Special thanks to everyone who tested the development branches and provided feedback on the new features.

---

## [1.3.0] - 2025-09-06

### Added
- **`omnipkg run` Command:** A powerful new way to execute scripts. Features automatic detection of runtime `AssertionError`s for package versions and "auto-heals" the script by re-running it inside a temporary bubble.
- **Automatic Python Provisioning:** Scripts can now ensure required Python interpreters are available, with `omnipkg` automatically running `python adopt` if a version is missing.
- **Performance Timers:** The `multiverse_analysis` test script now instruments and reports on the speed of dimension swaps and package preparation.

### Changed
- **Major Performance Boost:** The knowledge base sync and package satisfaction checks are now dramatically faster, using single subprocess calls to validate the entire environment, reducing checks from many seconds to milliseconds.
- **Quieter Logging:** The bubble creation process is now significantly less verbose during large, multi-dependency installations, providing clean, high-level summaries instead.
- **CLI Refactoring:** Command logic for `run` has been moved to the new `omnipkg/commands/` directory for better structure.

### Fixed
- **Critical Context Bug:** The knowledge base is now always updated by the correct Python interpreter context, especially after a `swap` or during scripted installs, ensuring data for different Python versions is stored correctly.

## v.1.2.1

omnipkg v1.2.1: The Phoenix Release — True Multi-Interpreter Freedom

omnipkg v1.2.1: The Phoenix Release 🚀
This is the release we've been fighting for.

In a previous version (v1.0.8), we introduced a groundbreaking but ultimately unstable feature: Python interpreter hot-swapping. The immense complexity of managing multiple live contexts led to critical bugs, forcing a difficult but necessary rollback. We promised to return to this challenge once the architecture was right.

Today, the architecture is right. Version 1.2.1 delivers on that promise, rising from the ashes of that challenge.

This release introduces a completely re-imagined and bulletproof architecture for multi-interpreter management. It solves the core problems of state, context, and user experience that make this feature so difficult. The impossible is now a stable, intuitive reality.

🔥 Your Environment, Your Rules. Finally.
omnipkg now provides a seamless and robust experience for managing and switching between multiple Python versions within a single environment, starting from the very first command.

1. Zero-Friction First Run: Native Python is Now a First-Class Citizen
The single biggest point of friction for new users has been eliminated. On its very first run, omnipkg now automatically adopts the user's native Python interpreter, making it a fully managed and swappable version from the moment you start.

Start in Python 3.12? omnipkg recognizes it, registers it, and you can always omnipkg swap python 3.12 right back to it.
No more getting "stuck" after a version switch.
No more being forced to re-download a Python version you already have.
2. The Python 3.11 "Control Plane": A Guarantee of Stability
Behind the scenes, omnipkg establishes a managed Python 3.11 environment to act as its "Control Plane." This is our guarantee of stability. All sensitive operations, especially the creation of package bubbles, are now executed within this known-good context.

Solves Real-World Problems: This fixes critical failures where a user on a newer Python (e.g., 3.12) couldn't create bubbles for packages that only supported older versions (e.g., tensorflow==2.13.0).
Predictable & Reliable: Bubble creation is now 100% reliable, regardless of your shell's active Python version.
3. Smart, Safe Architecture
omnipkg runs in your active context, as you'd expect.
Tools that require a specific context (like our test suite) now explicitly and safely request it, making operations transparent and reliable.
What This Means
The journey to this release was a battle against one of the hardest problems in environment management. By solving it, we have created a tool that is not only more powerful but fundamentally more stable and intuitive. You can now step into any Python environment and omnipkg will instantly augment it with the power of multi-version support, without ever getting in your way.

This is the foundation for the future. Thank you for pushing the boundaries with us.

Upgrade now:

pip install -U omnipkg

## v1.1.0
2025-8-21
Localization support for 24 additional languages.

## v1.0.13 - 2025-08-17
### Features
- **Pip in Jail Easter Egg**: Added fun status messages like "Pip is in jail, crying silently. 😭🔒" to `omnipkg status` for a delightful user experience.
- **AGPL License**: Adopted GNU Affero General Public License v3 or later for full open-source compliance.
- **Commercial License Option**: Added `COMMERCIAL_LICENSE.md` for proprietary use cases, with contact at omnipkg@proton.me.
- **Improved License Handling**: Updated `THIRD_PARTY_NOTICES.txt` to list only direct dependencies, with license texts in `licenses/`.

### Bug Fixes
- Reduced deduplication to properly handle binaries, as well as ensuring python modules are kept safe. 

### Improvements
- Added AGPL notice to `omnipkg/__init__.py` with dynamic version and dependency loading.
- Enhanced `generate_licenses.py` to preserve existing license files and moved it to `scripts/`.
- Removed `examples/testflask.py` and `requirements.txt` for a leaner package.
- Updated `MANIFEST.in` to include only necessary files and exclude `examples/`, `scripts/`, and `tests/`.

### Notes
- Direct dependencies: `redis==6.4.0`, `packaging==25.0`, `requests==2.32.4`, `python-magic==0.4.27`, `aiohttp==3.12.15`, `tqdm==4.67.1`.
- Transitive dependency licenses available in `licenses/` for transparency.

## v1.0.9 - 2025-08-11
### Notes
- Restored stable foundation of v1.0.7.
- Removed experimental features from v1.0.8 for maximum stability.
- Recommended for production use.

## [1.3.0] - 2025-09-06

### Added
- **`omnipkg run` Command:** A powerful new way to execute scripts. Features automatic detection of runtime `AssertionError`s for package versions and "auto-heals" the script by re-running it inside a temporary bubble.
- **Automatic Python Provisioning:** Scripts can now ensure required Python interpreters are available, with `omnipkg` automatically running `python adopt` if a version is missing.
- **Performance Timers:** The `multiverse_analysis` test script now instruments and reports on the speed of dimension swaps and package preparation.

### Changed
- **Major Performance Boost:** The knowledge base sync and package satisfaction checks are now dramatically faster, using single subprocess calls to validate the entire environment, reducing checks from many seconds to milliseconds.
- **Quieter Logging:** The bubble creation process is now significantly less verbose during large, multi-dependency installations, providing clean, high-level summaries instead.
- **CLI Refactoring:** Command logic for `run` has been moved to the new `omnipkg/commands/` directory for better structure.

### Fixed
- **Critical Context Bug:** The knowledge base is now always updated by the correct Python interpreter context, especially after a `swap` or during scripted installs, ensuring data for different Python versions is stored correctly.

## v.1.2.1

omnipkg v1.2.1: The Phoenix Release — True Multi-Interpreter Freedom

omnipkg v1.2.1: The Phoenix Release 🚀
This is the release we've been fighting for.

In a previous version (v1.0.8), we introduced a groundbreaking but ultimately unstable feature: Python interpreter hot-swapping. The immense complexity of managing multiple live contexts led to critical bugs, forcing a difficult but necessary rollback. We promised to return to this challenge once the architecture was right.

Today, the architecture is right. Version 1.2.1 delivers on that promise, rising from the ashes of that challenge.

This release introduces a completely re-imagined and bulletproof architecture for multi-interpreter management. It solves the core problems of state, context, and user experience that make this feature so difficult. The impossible is now a stable, intuitive reality.

🔥 Your Environment, Your Rules. Finally.
omnipkg now provides a seamless and robust experience for managing and switching between multiple Python versions within a single environment, starting from the very first command.

1. Zero-Friction First Run: Native Python is Now a First-Class Citizen
The single biggest point of friction for new users has been eliminated. On its very first run, omnipkg now automatically adopts the user's native Python interpreter, making it a fully managed and swappable version from the moment you start.

Start in Python 3.12? omnipkg recognizes it, registers it, and you can always omnipkg swap python 3.12 right back to it.
No more getting "stuck" after a version switch.
No more being forced to re-download a Python version you already have.
2. The Python 3.11 "Control Plane": A Guarantee of Stability
Behind the scenes, omnipkg establishes a managed Python 3.11 environment to act as its "Control Plane." This is our guarantee of stability. All sensitive operations, especially the creation of package bubbles, are now executed within this known-good context.

Solves Real-World Problems: This fixes critical failures where a user on a newer Python (e.g., 3.12) couldn't create bubbles for packages that only supported older versions (e.g., tensorflow==2.13.0).
Predictable & Reliable: Bubble creation is now 100% reliable, regardless of your shell's active Python version.
3. Smart, Safe Architecture
omnipkg runs in your active context, as you'd expect.
Tools that require a specific context (like our test suite) now explicitly and safely request it, making operations transparent and reliable.
What This Means
The journey to this release was a battle against one of the hardest problems in environment management. By solving it, we have created a tool that is not only more powerful but fundamentally more stable and intuitive. You can now step into any Python environment and omnipkg will instantly augment it with the power of multi-version support, without ever getting in your way.

This is the foundation for the future. Thank you for pushing the boundaries with us.

Upgrade now:

pip install -U omnipkg

## v1.1.0
2025-8-21
Localization support for 24 additional languages.

## v1.0.13 - 2025-08-17
### Features
- **Pip in Jail Easter Egg**: Added fun status messages like “Pip is in jail, crying silently. 😭🔒” to `omnipkg status` for a delightful user experience.
- **AGPL License**: Adopted GNU Affero General Public License v3 or later for full open-source compliance.
- **Commercial License Option**: Added `COMMERCIAL_LICENSE.md` for proprietary use cases, with contact at omnipkg@proton.me.
- **Improved License Handling**: Updated `THIRD_PARTY_NOTICES.txt` to list only direct dependencies, with license texts in `licenses/`.

### Bug Fixes
- Reduced deduplication to properly handle binaries, as well as ensuring python modules are kept safe. 

### Improvements
- Added AGPL notice to `omnipkg/__init__.py` with dynamic version and dependency loading.
- Enhanced `generate_licenses.py` to preserve existing license files and moved it to `scripts/`.
- Removed `examples/testflask.py` and `requirements.txt` for a leaner package.
- Updated `MANIFEST.in` to include only necessary files and exclude `examples/`, `scripts/`, and `tests/`.

### Notes
- Direct dependencies: `redis==6.4.0`, `packaging==25.0`, `requests==2.32.4`, `python-magic==0.4.27`, `aiohttp==3.12.15`, `tqdm==4.67.1`.
- Transitive dependency licenses available in `licenses/` for transparency.

## v1.0.9 - 2025-08-11
### Notes
- Restored stable foundation of v1.0.7.
- Removed experimental features from v1.0.8 for maximum stability.
- Recommended for production use.
