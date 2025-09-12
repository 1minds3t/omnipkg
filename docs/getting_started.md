
# Getting Started with omnipkg

This guide will walk you through installing omnipkg and performing the initial setup.

### 1. Installation

omnipkg is designed for easy installation across multiple platforms. Choose the method that best fits your workflow.

#### 🏠 Official Conda-Forge (NEW!)
This is now one of the easiest and most reliable ways to install `omnipkg`.
```bash
# Using conda
conda install -c conda-forge omnipkg

# Or with mamba for a faster installation
mamba install -c conda-forge omnipkg
```

#### 📦 PyPI (Recommended Standard)
```bash
pip install omnipkg
```

#### 🍺 Homebrew (macOS/Linux)
```bash
# First, add the custom tap
brew tap 1minds3t/omnipkg

# Then, install omnipkg
brew install omnipkg
```

#### 🐋 Docker
```bash
# Pull the latest official image
docker pull 1minds3t/omnipkg:latest
```

### 2. Prerequisites: None! (SQLite by Default)

**`omnipkg` works out of the box with zero setup.** It uses a built-in SQLite database by default to store its knowledge base, making your first run completely frictionless.

#### Optional: Enhanced Performance with Redis
For users who need maximum performance in large or complex environments, `omnipkg` can automatically detect and use a Redis server if one is available. Redis provides lightning-fast, in-memory lookups. If you want to use this optional feature, you can install Redis as follows:

**How to Install and Start Redis:**

The installation process varies depending on your operating system:

*   **Linux (Ubuntu/Debian-based):**
    ```bash
    sudo apt-get update && sudo apt-get install redis-server
    sudo systemctl enable --now redis-server
    ```
*   **macOS (Homebrew):**
    ```bash
    brew install redis && brew services start redis
    ```
*   **Windows (via WSL2 or Docker):**
    The recommended approach is to use WSL2 and follow the Linux instructions. Alternatively, you can run Redis in a Docker container:
    ```bash
    docker run --name some-redis -p 6379:6379 -d redis
    ```

**Verify Redis is Running (if installed):**
```bash
redis-cli ping
```
You should see a `PONG` response. If you get an error, ensure the Redis server process is running.

### 3. First-Time omnipkg Setup

After `omnipkg` is installed, simply execute any command for the first time (e.g., `omnipkg status`).

`omnipkg` will detect that its configuration file (`~/.config/omnipkg/config.json`) does not exist and will guide you through a brief, interactive setup. It will ask you for details like:

*   The path where it should store package "bubbles" (a default is recommended).
*   It will then check for a Redis server (defaulting to `localhost:6379`), but will **seamlessly fall back to SQLite** if one is not found.

Once configured, omnipkg will save these settings and proceed with your command.

### 4. Quick Start Example

To immediately experience omnipkg's power, try the interactive demo:

```bash
omnipkg demo
```
This command will present a menu allowing you to explore different scenarios, including Python module, binary, C-extension, and complex dependency (TensorFlow) switching tests.
