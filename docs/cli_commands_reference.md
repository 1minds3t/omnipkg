# omnipkg CLI Commands Reference

This document provides a comprehensive overview of all `omnipkg` command-line interface (CLI) commands and their usage.

## Global Options

*   `-v`, `--version`: Displays the current version of `omnipkg`.

## Commands

---

### `omnipkg install <package_spec> [package_spec...]`

**Description**: The primary command for installing packages. `omnipkg` intelligently manages versions, automatically resolving conflicts by creating isolated "bubbles" for conflicting versions while keeping your main environment stable.

**Arguments**:
*   `<package_spec>`: One or more package specifications (e.g., `"requests"`, `"numpy==1.26.4"`, `"django>=3.0,<4.0"`).
*   `-r`, `--requirement <FILE>`: Install packages listed in a `requirements.txt`-like file.

**Examples**:
```bash
# Install a specific version of a package
omnipkg install flask==2.3.0

# Install multiple packages
omnipkg install requests==2.32.4 tqdm

# Install multiple versions of the same package (omnipkg will bubble one)
omnipkg install uv==0.7.13 uv==0.7.14

# Install from a requirements file
omnipkg install -r my_project_requirements.txt
```

---

### `omnipkg install-with-deps <package_spec> [--dependency <dep_spec>...]`

**Description**: Installs a package while allowing you to specify exact versions for its direct or transitive dependencies. This is particularly useful for complex ecosystems like AI/ML where a specific combination of a main package and its underlying libraries (e.g., TensorFlow with a precise NumPy version) is required. `omnipkg` will ensure these versions are present, bubbling them if they conflict with your main environment.

**Arguments**:
*   `<package_spec>`: The main package to install (e.g., `"tensorflow==2.13.0"`).
*   `--dependency <dep_spec>`: One or more explicit dependency versions (e.g., `"numpy==1.24.3"`, `"typing-extensions==4.5.0"`). Can be used multiple times.

**Example**:
```bash
omnipkg install-with-deps tensorflow==2.13.0 --dependency numpy==1.24.3 --dependency typing-extensions==4.5.0
```

---

### `omnipkg list [filter]`

**Description**: Displays all packages `omnipkg` is aware of, distinguishing between versions active in your main Python environment and those stored in isolated "bubbles."

**Arguments**:
*   `[filter]`: An optional string to filter package names (e.g., `"flask"` will show `flask` and `flask-login`).

**Examples**:
```bash
# List all managed packages
omnipkg list

# List packages matching a filter
omnipkg list tensor
```

---

### `omnipkg status`

**Description**: Provides a high-level overview of your `omnipkg`-managed Python environment's health. It shows the active `site-packages` path, the number of active packages, the total size and count of isolated bubbles, and fun status messages about critical tools like `pip` and `uv`.

**Usage**:
```bash
omnipkg status
```

---

### `omnipkg info <package_name> [--version <version_spec>]`

**Description**: Provides a detailed interactive dashboard for a specific package. You can explore its metadata, dependencies, active status, and any bubbled versions.

**Arguments**:
*   `<package_name>`: The name of the package to inspect (e.g., `"requests"`, `"numpy"`).
*   `--version <version_spec>`: Optional. Specify a particular version to inspect directly (e.g., `"1.24.3"`). If omitted, `omnipkg` might offer interactive choices.

**Examples**:
```bash
omnipkg info flask-login
omnipkg info uv --version 0.7.14
```

---

### `omnipkg demo`

**Description**: An interactive showcase of `omnipkg`'s core version-switching capabilities. This is highly recommended for new users to see `omnipkg` in action on various package types.

**Usage**:
```bash
omnipkg demo
```
Follow the on-screen prompts to select a demo (Python module switching, binary switching, C-extension switching, complex dependency switching).

---

### `omnipkg stress-test`

**Description**: A heavy-duty demonstration of `omnipkg`'s resilience with large, complex scientific computing packages (NumPy, SciPy). It performs a series of challenging installations and runtime version swaps to prove `omnipkg`'s ability to handle previously "impossible" dependency scenarios.

**Usage**:
```bash
omnipkg stress-test
```
**(Note**: This demo currently requires Python 3.11. Future versions of `omnipkg` will enable cross-Python-version interpreter hot-swapping.)

---

### `omnipkg revert [--yes]`

**Description**: Your "undo" button for environment changes. `omnipkg` maintains "last known good" snapshots of your environment. This command restores your main environment to the state of the last snapshot, effectively reversing any unintended changes (e.g., downgrades caused by `pip` or `uv`).

**Arguments**:
*   `--yes`, `-y`: Skip the confirmation prompt.

**Examples**:
```bash
omnipkg revert
omnipkg revert --yes
```

---

### `omnipkg uninstall <package_name> [package_name...] [--yes]`

**Description**: Intelligently removes packages. By default, it will prompt you to select which versions (active, bubbled, or all non-protected) of a package to remove. It handles associated files and cleans up its knowledge base.

**Arguments**:
*   `<package_name>`: One or more package names to uninstall.
*   `--yes`, `-y`: Skip confirmation prompts for removal.

**Examples**:
```bash
omnipkg uninstall old-library
omnipkg uninstall torch --yes
```
**(Note**: `omnipkg`'s core dependencies and `omnipkg` itself are protected from accidental uninstallation from the active environment.)

---

### `omnipkg rebuild-kb [--force]`

**Description**: Refreshes `omnipkg`'s internal knowledge base. `omnipkg` stores rich metadata about your packages and their file hashes in Redis. Use this command if you suspect the knowledge base is out of sync or corrupted, or after manually altering installed packages (not recommended).

**Arguments**:
*   `--force`, `-f`: Ignore existing cache and force a complete rebuild of all package metadata and file hashes.

**Usage**:
```bash
omnipkg rebuild-kb
omnipkg rebuild-kb --force
```

---

### `omnipkg reset [--yes]`

**Description**: Deletes `omnipkg`'s entire Redis knowledge base. This will clear all package metadata, hash indexes, and bubble tracking data. It does NOT delete actual package files from your system. Use this for a complete clean slate if `omnipkg` is misbehaving severely, then follow up with `omnipkg rebuild-kb`.

**Arguments**:
*   `--yes`, `-y`: Skip the confirmation prompt.

**Usage**:
```bash
omnipkg reset
omnipkg reset --yes
```

---

### `omnipkg reset-config [--yes]`

**Description**: Deletes `omnipkg`'s local configuration file (`~/.config/omnipkg/config.json`). The next time `omnipkg` is executed, it will run through the first-time setup process again, allowing you to reconfigure paths or Redis connections.

**Arguments**:
*   `--yes`, `-y`: Skip the confirmation prompt.

**Usage**:
```bash
omnipkg reset-config
omnipkg reset-config --yes
```
