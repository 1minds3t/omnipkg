---
title: Getting Started with omnipkg
doc_type: guide
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
---

# Getting Started with omnipkg

This guide will walk you through installing `omnipkg` and performing the initial setup.


# Getting Started with omnipkg

Welcome to **omnipkg** - the package manager that breaks the fundamental laws of Python environments.

## 🚀 Quick Install (30 Seconds)

=== "UV (Recommended)"
```bash
    uv pip install omnipkg && omnipkg demo
```

=== "pip"
```bash
    pip install omnipkg && omnipkg demo
```

=== "conda"
```bash
    conda install -c conda-forge omnipkg
    omnipkg demo
```

=== "Docker"
```bash
    docker pull 1minds3t/omnipkg:latest
    docker run -it 1minds3t/omnipkg:latest omnipkg demo
```

## ✨ See It In Action (10 Seconds)

Run the interactive demo to experience omnipkg's revolutionary features:
```bash
omnipkg demo
```

Choose from 33+ demos including:
- 🌀 **Multiverse Orchestration** - 3 Python versions, 1 script, <600ms
- ⚡ **Auto-Healing** - 7.76× faster than UV at fixing broken environments
- 🔥 **Hot-Swapping** - Switch NumPy versions mid-script in <1ms
- 💥 **Concurrent Execution** - Run Python 3.9/3.10/3.11 simultaneously

## 🎯 What Makes omnipkg Revolutionary?

### Before omnipkg:
```bash
# Traditional approach - FAILS
pip install torch==2.0.0
pip install torch==2.7.1
# ERROR: Cannot install torch==2.0.0 - conflicts with torch==2.7.1
```

### With omnipkg:
```bash
# omnipkg approach - WORKS
omnipkg install torch==2.0.0 torch==2.7.1
# ✅ torch==2.7.1 active
# 🫧 torch==2.0.0 bubbled (isolated)
```

Both versions coexist. Switch between them instantly:
```python
from omnipkg.loader import omnipkgLoader

# Use PyTorch 2.0
with omnipkgLoader("torch==2.0.0"):
    import torch
    print(torch.__version__)  # 2.0.0

# Use PyTorch 2.7.1
with omnipkgLoader("torch==2.7.1"):
    import torch
    print(torch.__version__)  # 2.7.1
```

## 📋 Prerequisites

### Required
- **Python 3.7 - 3.14** (any version works)
- **pip/uv** (for installation)

### Optional (Auto-Fallback to SQLite)
- **Redis** (for 10× faster metadata lookups)

omnipkg works perfectly with SQLite by default. Redis is optional for enhanced performance.

### Installing Redis (Optional)

=== "Ubuntu/Debian"
```bash
    sudo apt update && sudo apt install redis-server
    sudo systemctl start redis
```

=== "macOS"
```bash
    brew install redis
    brew services start redis
```

=== "Windows (WSL2)"
```bash
    # In WSL2
    sudo apt update && sudo apt install redis-server
    sudo service redis-server start
```

=== "Docker"
```bash
    docker run -d -p 6379:6379 --name redis redis
```

Verify Redis (optional):
```bash
redis-cli ping  # Should return "PONG"
```

## 🔧 First-Time Setup

omnipkg automatically configures itself on first run:
```bash
omnipkg status
```

You'll be prompted for:
1. **Bubble Storage Path** (where isolated versions live)
2. **Redis Connection** (optional - uses SQLite if unavailable)
3. **Language Preference** (24 languages supported)

All settings are saved to `~/.config/omnipkg/config.json`

## 🎓 Learn by Example

### Example 1: Multi-Version Installation
```bash
# Install multiple NumPy versions
omnipkg install numpy==1.24.3 numpy==1.26.4 numpy==2.0.0

# Check what's active
omnipkg list numpy
# ✅ numpy==2.0.0 (active)
# 🫧 numpy==1.26.4 (bubble)
# 🫧 numpy==1.24.3 (bubble)
```

### Example 2: Python Hot-Swapping
```bash
# Adopt Python interpreters
omnipkg python adopt 3.9
omnipkg python adopt 3.11

# Switch between them
omnipkg swap python 3.9
python --version  # Python 3.9.x

omnipkg swap python 3.11
python --version  # Python 3.11.x
```

### Example 3: Auto-Healing Scripts
```bash
# This script requires old NumPy but you have 2.0 installed
omnipkg run legacy_tensorflow_model.py

# omnipkg automatically:
# 1. Detects NumPy 2.0 incompatibility
# 2. Creates numpy==1.24.3 bubble
# 3. Re-runs script successfully
# ✅ Done in <1 second (7.76× faster than UV)
```

### Example 4: Environment Protection
```bash
# Disaster: pip/uv accidentally downgrades a package
pip install some-package
# (downgrades critical dependencies)

# No problem - revert instantly
omnipkg revert
# ✅ Environment restored to last known good state
```

## 🌐 Interactive Web Bridge

omnipkg includes a web interface for running commands from your browser:
```bash
omnipkg web start
```

Visit `http://localhost:5000` and run commands interactively with live output!

## 📖 Next Steps

| Learn About | Description |
|-------------|-------------|
| [CLI Commands](../cli-commands/overview.md) | Complete command reference |
| [Python Hot-Swapping](../advanced-features/python-hot-swapping.md) | Master multi-interpreter switching |
| [Runtime Switching](../advanced-features/runtime-switching.md) | Dynamic package version swapping |
| [Daemon System](../cli-commands/daemon-system.md) | Background worker for instant healing |
| [Demos](../demos/index.md) | 33+ interactive demonstrations |

## 💡 Pro Tips

1. **Start with demos**: `omnipkg demo` shows you what's possible
2. **Use `omnipkg run`**: Automatically heals script errors
3. **Enable Redis**: 10× faster metadata lookups (optional)
4. **Snapshot often**: `omnipkg status` creates auto-snapshots
5. **Check health**: `omnipkg doctor` diagnoses issues

## 🆘 Need Help?
```bash
# Global help
omnipkg --help

# Command help
omnipkg install --help

# Diagnose issues
omnipkg doctor

# Check environment
omnipkg status
```

## 🐛 Troubleshooting

### "Redis not found" warning
✅ **This is fine!** omnipkg automatically uses SQLite. Redis is optional for performance.

### Permission errors on Ubuntu 24.04+
```bash
# Ubuntu 24.04+ requires this flag
pip install --break-system-packages omnipkg

# Or use a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install omnipkg
```

### Package conflicts
```bash
omnipkg doctor  # Auto-diagnose
omnipkg heal    # Auto-repair
omnipkg revert  # Restore snapshot
```

---

**Ready to break free from dependency hell?**
```bash
omnipkg demo
```