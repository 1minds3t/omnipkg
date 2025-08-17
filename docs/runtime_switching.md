# Runtime Version Switching with omnipkgLoader

One of `omnipkg`'s most revolutionary features is the ability to dynamically switch between different package versions *within the same Python script or process*, without requiring separate virtual environments, Docker containers, or process restarts. This is achieved using the `omnipkgLoader` context manager.

## How omnipkgLoader Works

The `omnipkgLoader` context manager allows you to temporarily activate a specific package version from its isolated "bubble." When your code enters the `with` block, `omnipkg` performs a series of meticulous operations to ensure the requested version is loaded. When your code exits the `with` block, `omnipkg` seamlessly restores your environment to its original state.

### Key Operations:

*   **Aggressive Module Cleaning**: `omnipkg` removes any existing loaded modules related to the target package from `sys.modules` (Python's module cache). This ensures Python will attempt a fresh import of the desired version.
*   **Main Environment Cloaking**: To prevent interference, `omnipkg` temporarily "cloaks" (renames) the main environment's installation directories of the package (e.g., `flask_login/` becomes `flask_login.timestamp_omnipkg_cloaked/`).
*   **`sys.path` Manipulation**: The bubble's path is dynamically inserted at the very front of `sys.path`, giving it import priority.
*   **Environment Variable Adjustment**: If the bubble contains binaries, `omnipkg` temporarily adds the bubble's `bin` directory to your `PATH` environment variable.
*   **Automatic Restoration**: Upon exiting the `with` block, `omnipkg` reverses all these changes, restoring `sys.path`, uncloaking the main package, clearing module caches, and restoring environment variables, leaving your environment exactly as it was.

This intricate dance ensures that your code truly operates with the specific version you request within the `with` block, and cleanly reverts afterwards.

## Using `omnipkgLoader` in Your Code

The `omnipkgLoader` is designed for simplicity.

1.  **Import `omnipkgLoader`**:
    ```python
    from omnipkg.loader import omnipkgLoader
    from omnipkg.core import ConfigManager # Highly recommended for robust path discovery
    ```
2.  **Initialize `ConfigManager` (Optional but Recommended)**:
    It's best practice to initialize `ConfigManager` once at the start of your application and pass its `config` object to `omnipkgLoader`. This ensures `omnipkgLoader` accurately finds your bubble base path and Redis connection details. If not provided, it will attempt auto-detection.
    ```python
    config_manager = ConfigManager()
    omnipkg_config = config_manager.config
    ```
3.  **Use the `with` Statement**:
    Pass the package specification (e.g., `"my-package==1.2.3"`) and your `omnipkg_config` to the `omnipkgLoader`.

```python
# example_version_switching.py

import sys
import importlib
from importlib.metadata import version as get_version, PackageNotFoundError
from omnipkg.loader import omnipkgLoader
from omnipkg.core import ConfigManager

# --- Initialize omnipkg's configuration (do this once in your application) ---
config_manager = ConfigManager()
omnipkg_config = config_manager.config

# --- Verify initial state (e.g., rich installed in main environment) ---
try:
    initial_rich_version = get_version('rich')
    print(f"Initial 'rich' version in main environment: {initial_rich_version}")
except PackageNotFoundError:
    print("Warning: 'rich' not found in main environment. Please install it with `omnipkg install rich`.")
    initial_rich_version = None

# --- Scenario 1: Activate an older version from a bubble ---
# Make sure 'rich==13.5.3' is installed as a bubble: `omnipkg install rich==13.5.3`
print("\n--- Entering context: Activating 'rich==13.5.3' bubble ---")
try:
    with omnipkgLoader("rich==13.5.3", config=omnipkg_config):
        # Inside this 'with' block, 'rich' 13.5.3 is active
        import rich
        from rich.console import Console
        
        active_version_in_bubble = rich.__version__
        print(f"  Active 'rich' version INSIDE bubble: {active_version_in_bubble}")
        Console().print(f"[red]This text is from Rich {active_version_in_bubble}[/red]")

    # Outside the 'with' block, the environment is automatically restored
    print("\n--- Exiting context: Environment restored ---")
    # To see the change reflected immediately in the current Python scope,
    # you might need to force a reload if 'rich' was imported before the 'with' block.
    if 'rich' in sys.modules:
        importlib.reload(sys.modules['rich'])
    # Re-import to confirm the restored version
    import rich
    print(f"Active 'rich' version AFTER bubble: {rich.__version__}") # Should be the initial version again

except RuntimeError as e:
    print(f"\nError activating 'rich==13.5.3' bubble: {e}")
    print("Please ensure 'rich==13.5.3' is installed as a bubble via `omnipkg install rich==13.5.3`.")
except Exception as e:
    print(f"\nAn unexpected error occurred: {e}")
    import traceback
    traceback.print_exc()

# --- Scenario 2: Switch to another bubbled version or even back to main ---
# Make sure 'rich==13.4.2' is installed as a bubble: `omnipkg install rich==13.4.2`
print("\n--- Entering context: Activating 'rich==13.4.2' bubble ---")
try:
    with omnipkgLoader("rich==13.4.2", config=omnipkg_config):
        import rich
        from rich.console import Console
        active_version_in_second_bubble = rich.__version__
        print(f"  Active 'rich' version INSIDE second bubble: {active_version_in_second_bubble}")
        Console().print(f"[blue]This text is from Rich {active_version_in_second_bubble}[/blue]")

    print("\n--- Exiting context: Environment restored again ---")
    if 'rich' in sys.modules:
        importlib.reload(sys.modules['rich'])
    import rich
    print(f"Active 'rich' version FINALLY: {rich.__version__}") # Should be the initial version again

except RuntimeError as e:
    print(f"\nError activating 'rich==13.4.2' bubble: {e}")
    print("Please ensure 'rich==13.4.2' is installed as a bubble via `omnipkg install rich==13.4.2`.")
except Exception as e:
    print(f"\nAn unexpected error occurred during second demo: {e}")
    import traceback
    traceback.print_exc()

print("\n--- End of script ---")
print("omnipkg enables seamless multi-version usage in a single Python process!")
```

## Verification Methods

To confirm that `omnipkg` is doing its magic and the correct versions are active:

*   **Inside `with` block**: Use `package.__version__` or `importlib.metadata.version('package_name')` immediately after importing the package.
*   **Outside `with` block**: After the `with omnipkgLoader` block, always call `importlib.reload(sys.modules['your_package'])` if `your_package` was already imported *before* the `with` block. This forces Python to re-evaluate where to find the module, reflecting the restored `sys.path`. Then, check its version again.

The `omnipkg demo` command includes tests that show these verifications in action.

## Cleanup Necessary?

One of the greatest benefits of `omnipkgLoader` is that **no manual cleanup is necessary after exiting the `with` block**. The environment is automatically restored. You only need to ensure you've installed the necessary package versions as bubbles using `omnipkg install <package_spec>` beforehand.
