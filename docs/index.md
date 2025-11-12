# Welcome to omnipkg

> The Ultimate Python Dependency Resolver. One environment. Infinite packages. Zero conflicts.

Tired of `pip` breaking your environment every time you install a package for an old project? **omnipkg** solves this by fundamentally changing how packages are managed.

Instead of overwriting packages, omnipkg isolates every version in a "bubble," allowing you to hot-swap between them at runtime in microseconds. This is the end of dependency hell.

---

## 🚀 Quick Start

Install multiple, conflicting versions of a package without breaking your environment:

```bash
# Install the latest version of Flask for your modern app
omnipkg install flask

# Now install an ancient version for a legacy script.
# omnipkg isolates it instead of overwriting the new one.
omnipkg install flask==0.12.5
```

Run a script with automatic dependency healing. If the wrong version is active, `omnipkg run` fixes it on the fly for that script's execution only:

```bash
# This script needs an old version, but omnipkg handles it automatically.
omnipkg run --requires "flask==0.12.5" legacy_app.py
```

---

## Key Features

*   **Peaceful Coexistence:** Install hundreds of versions of the same package. They are all available on demand without conflict.
*   **Runtime Hot-Swapping:** Your code can switch between package versions in microseconds without restarting the Python interpreter.
*   **The Time Machine:** Automatically resurrects and rebuilds ancient, broken packages from the past, including their historical dependencies.
*   **Automatic Healing:** The `omnipkg run` command detects version conflicts before your script even starts and temporarily activates the exact versions required.
*   **Full Environment Management:** Adopt and switch between multiple Python interpreters (`3.7` through `3.14`) with a single command.

---

## Dive Deeper

*   **[Getting Started](./getting_started.md):** Your first steps and core concepts.
*   **[CLI Commands Reference](./cli_commands_reference.md):** Detailed guide to every command.
*   **[Advanced Management](./advanced_management.md):** Master the Time Machine and version bubbling.
*   **[Python Hot-Swapping](./python_hot_swapping.md):** See live examples of runtime version switching.
*   **[LibResolver](./LIBRESOLVER.md):** Learn how omnipkg can even manage system-level library compatibility.

---

Find an issue or want to contribute? Check out the [GitHub Repository](https://github.com/1minds3t/omnipkg).
