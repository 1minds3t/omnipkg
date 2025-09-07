# Changelog

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