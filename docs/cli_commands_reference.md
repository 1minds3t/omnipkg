# omnipkg CLI Commands Reference

This document provides a comprehensive overview of all omnipkg command-line interface (CLI) commands and their usage.

### Global Options

*   `--lang <CODE>`: Overrides the display language for the current command (e.g., `es`, `ja`).
*   `-v, --version`: Displays the current version of omnipkg.

### Package Management Commands

#### `omnipkg install <package_spec>...`
Intelligently installs packages, automatically resolving conflicts by creating isolated "bubbles".
*   **Arguments**:
    *   `<package_spec>`: One or more packages (e.g., `requests`, `numpy==1.26.4`).
    *   `-r, --requirement <FILE>`: Install from a requirements file.

#### `omnipkg install-with-deps <package_spec> --dependency <dep_spec>...`
Installs a package with explicitly defined dependency versions.
*   **Arguments**:
    *   `<package_spec>`: The main package to install (e.g., `"tensorflow==2.13.0"`).
    *   `--dependency <dep_spec>`: A specific dependency version (e.g., `"numpy==1.24.3"`).

#### `omnipkg uninstall <package_name>...`
Intelligently removes packages, prompting to remove active, bubbled, or all versions.
*   **Arguments**:
    *   `<package_name>`: One or more packages to uninstall.
    *   `--yes, -y`: Skips confirmation prompts.

#### `omnipkg list [filter]`
Displays all managed packages, distinguishing between active and bubbled versions.
*   **Arguments**:
    *   `[filter]`: An optional string to filter package names. Use `python` to list interpreters.

#### `omnipkg info <package_spec>`
Provides a detailed dashboard for a specific package or for the Python environment itself.
*   **Arguments**:
    *   `<package_spec>`: The package to inspect (e.g., `requests`, `uv==0.7.14`). Use `python` to see interpreter details.

#### `omnipkg prune <package_name>`
Cleans up old, bubbled versions of a specific package.
*   **Arguments**:
    *   `<package_name>`: The package whose bubbles you want to prune.
    *   `--keep-latest <N>`: Keep the N most recent bubbled versions.
    *   `--yes, -y`: Skips confirmation.

### Environment & Interpreter Management

#### `omnipkg python <subcommand>`
The primary command for managing Python interpreters.
*   **Subcommands**:
    *   `adopt <version>`: Adds an existing system Python interpreter to omnipkg's management (e.g., `omnipkg python adopt 3.9`).
    *   `switch <version>`: Switches the active Python interpreter for the environment. Alias for `omnipkg swap python <version>`.
    *   `remove <version>`: Removes a managed Python interpreter.
    *   `rescan`: Forces a re-scan of managed interpreters to repair the registry.

#### `omnipkg swap python [version]`
A user-friendly command to hot-swap the active Python interpreter.
*   **Usage**:
    *   `omnipkg swap python 3.10`: Switches directly to Python 3.10.
    *   `omnipkg swap python`: Opens an interactive picker to choose from available versions.

#### `omnipkg run <script> [args...]`
Runs a script with auto-healing capabilities. If the script fails due to a dependency conflict (e.g., NumPy 2.0 incompatibility), omnipkg will attempt to fix it by using a compatible bubbled version and re-run the script.
*   **Arguments**:
    *   `<script> [args...]`: The Python script to run and any of its arguments.

#### `omnipkg revert`
Restores your environment to the last known good state, undoing changes made by external tools like `pip` or `uv`.
*   **Arguments**:
    *   `--yes, -y`: Skips confirmation.

#### `omnipkg status`
Provides a high-level health dashboard of your omnipkg-managed environment, including active Python version, package counts, and bubble stats.

### Demos & Diagnostics

#### `omnipkg demo`
An interactive showcase of omnipkg's core version-switching capabilities for modules, binaries, and C-extensions.

#### `omnipkg stress-test`
A heavy-duty demonstration of omnipkg's resilience with complex packages like NumPy and SciPy, including multiverse analysis across different Python interpreters.

### Configuration & Maintenance

#### `omnipkg config <subcommand>`
Views or edits the omnipkg configuration.
*   **Subcommands**:
    *   `view`: Displays the current configuration.
    *   `set <key> <value>`: Sets a configuration value (e.g., `omnipkg config set language ja`).
    *   `reset <key>`: Resets a configuration key to its default.

#### `omnipkg rebuild-kb`
Refreshes omnipkg's internal Redis/SQLite knowledge base.
*   **Arguments**:
    *   `--force, -f`: Forces a complete rebuild, ignoring any cache.

#### `omnipkg reset`
Deletes omnipkg's entire knowledge base (but not package files). Requires a `rebuild-kb` afterwards.
*   **Arguments**:
    *   `--yes, -y`: Skips confirmation.

#### `omnipkg reset-config`
Deletes the local configuration file to trigger the first-time setup again.
*   **Arguments**:
    *   `--yes, -y`: Skips confirmation.
```

---
