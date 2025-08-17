# Getting Started with omnipkg

This guide will walk you through installing `omnipkg` and performing the initial setup.

## 1. Installation

`omnipkg` is available on PyPI. You can install it directly using `pip`:

```bash
pip install omnipkg
```

## 2. Prerequisites: Redis Server

`omnipkg` leverages a **Redis server** as its high-performance, in-memory knowledge base. This allows for lightning-fast metadata lookups, hash indexing, and managing the state of your multi-version environments.

**Before running `omnipkg` for the first time, you must have a Redis server up and running.**

### How to Install and Start Redis:

The installation process varies depending on your operating system:

**Linux (Ubuntu/Debian-based):**
```bash
sudo apt-get update
sudo apt-get install redis-server
sudo systemctl start redis-server
sudo systemctl enable redis-server # Optional: Start Redis automatically on boot
```
**Linux (CentOS/RHEL-based):**
```bash
sudo yum install redis
sudo systemctl start redis
sudo systemctl enable redis # Optional
```
**macOS (using Homebrew):**
```bash
brew install redis
brew services start redis # Starts Redis automatically on login
# Or, to start manually: redis-server```
**Windows:**
The official Redis project does not natively support Windows. You can use:
*   **Windows Subsystem for Linux (WSL 2)**: Install a Linux distribution (like Ubuntu) and follow the Linux instructions above. This is the recommended approach.
*   **Docker Desktop**: Run Redis in a Docker container.
    ```bash
    docker pull redis
    docker run --name some-redis -p 6379:6379 -d redis
    ```
*   **Scoop/Chocolatey (Community Ports)**: Be aware these are unofficial ports. Search for `scoop install redis` or `choco install redis-server`.

### Verify Redis is Running:

Once installed and started, you can verify Redis is operational:

```bash
redis-cli ping
```
You should see a `PONG` response. If you get an error, ensure the Redis server process is running.

## 3. First-Time omnipkg Setup

After `omnipkg` is installed and your Redis server is running, simply execute any `omnipkg` command for the first time (e.g., `omnipkg status` or `omnipkg install requests`).

`omnipkg` will detect that its configuration file (`~/.config/omnipkg/config.json`) does not exist and will guide you through a brief, interactive setup. It will ask you for details like:
*   The path where it should store package "bubbles" (defaults to a hidden directory in your `site-packages`).
*   The connection details for your Redis server (defaults to `localhost:6379`).

Once configured, `omnipkg` will save these settings and proceed with your command.

## 4. Quick Start Example

To immediately experience `omnipkg`'s power, try the interactive demo:

```bash
omnipkg demo
```
This command will present a menu allowing you to explore different scenarios, including Python module, binary, C-extension, and complex dependency (TensorFlow) switching tests.

Congratulations! You're now ready to harness the power of `omnipkg` and say goodbye to dependency hell.
