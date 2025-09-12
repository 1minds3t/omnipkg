# Runtime Package Version Switching with omnipkgLoader

One of omnipkg's most revolutionary features is the ability to dynamically switch between different **package versions** within the same Python script, without requiring separate virtual environments or process restarts. This is achieved using the `omnipkgLoader` context manager.

> **Looking for Python Interpreter Switching?**
> This guide covers switching *packages* (like `numpy` or `requests`) inside a script. To learn how to switch the entire *Python interpreter* (e.g., from Python 3.9 to 3.11) for your shell or project, see the [**Python Hot-Swapping Guide**](./python_hot_swapping.md).

### How omnipkgLoader Works

The `omnipkgLoader` context manager temporarily activates a specific package version from its isolated "bubble." When your code enters the `with` block, omnipkg performs a series of operations (module cleaning, path manipulation) to ensure the requested version is loaded. When the block is exited, your environment is seamlessly restored to its original state.

### Using omnipkgLoader in Your Code

Using the loader is simple and powerful.

1.  **Install Multiple Versions**: First, ensure you have the desired versions installed. omnipkg will automatically bubble the conflicting one.
    ```bash
    # This will install 13.7.1 in main and bubble 13.5.3
    omnipkg install rich==13.7.1 rich==13.5.3
    ```

2.  **Use the Context Manager**: In your Python script, import and use `omnipkgLoader`.

```python
# example_version_switching.py

import sys
import importlib
from importlib.metadata import version as get_version
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

# It's best practice to initialize ConfigManager once
config_manager = ConfigManager()
omnipkg_config = config_manager.config

# --- Verify initial state ---
initial_rich_version = get_version('rich')
print(f"Initial 'rich' version in main environment: {initial_rich_version}")

# --- Activate an older version from a bubble ---
print("\n--- Entering context: Activating 'rich==13.5.3' bubble ---")
with omnipkgLoader("rich==13.5.3", config=omnipkg_config):
    # Inside this 'with' block, 'rich' 13.5.3 is active
    import rich
    from rich.console import Console

    active_version_in_bubble = rich.__version__
    print(f"  Active 'rich' version INSIDE bubble: {active_version_in_bubble}")
    Console().print(f"[red]This text is from Rich {active_version_in_bubble}[/red]")

# --- Environment is restored automatically ---
print("\n--- Exiting context: Environment restored ---")

# You must reload the module to see the change reflected
if 'rich' in sys.modules:
    importlib.reload(sys.modules['rich'])

import rich
print(f"Active 'rich' version AFTER bubble: {rich.__version__}")
```

### Key Considerations

*   **Automatic Cleanup**: No manual cleanup is necessary. The environment is automatically restored upon exiting the `with` block.
*   **Module Reloading**: If a module was imported *before* the `with` block, you **must** use `importlib.reload()` after the block to force Python to recognize the restored main version.
*   **Nested Contexts**: `omnipkgLoader` supports nesting, allowing you to create complex, temporary environments with multiple specific bubbled packages active at once.
```

---
