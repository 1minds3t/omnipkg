# Changelog

# Changelog

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