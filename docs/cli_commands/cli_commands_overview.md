---
title: Overview
doc_type: reference
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: cli_commands
---

# Overview

## Overview

# CLI Commands Overview

`omnipkg` provides a comprehensive CLI for managing Python packages across versions and interpreters.

## Quick Reference

| Command | Description | Example |
|---------|-------------|---------|
| `install` | Install packages with conflict resolution | `omnipkg install torch==2.0.0 torch==2.7.1` |
| `list` | View all managed packages | `omnipkg list` |
| `info` | Interactive package explorer | `omnipkg info numpy` |
| `status` | Environment health dashboard | `omnipkg status` |
| `swap` | Switch Python versions or packages | `omnipkg swap python 3.9` |
| `revert` | Restore last known good state | `omnipkg revert` |
| `run` | Auto-healing script runner | `omnipkg run script.py` |
| `daemon` | Manage background worker | `omnipkg daemon start` |
| `web` | Start interactive web bridge | `omnipkg web start` |
| `demo` | Try interactive demos | `omnipkg demo` |

## Command Categories

### 📦 [Package Management](package-management.md)
Install, uninstall, and manage multiple package versions

### 🔧 [Environment Health](environment-health.md)
Monitor, repair, and restore your Python environment

### 🐍 [Python Management](python-management.md)
Hot-swap Python interpreters without restarting

### ⚡ [Advanced Features](advanced-features.md)
Daemon workers, web bridge, and auto-healing scripts

### 🎮 [Demos & Testing](demos-and-testing.md)
Interactive demonstrations of omnipkg's capabilities

## Getting Help
```bash
# Global help
omnipkg --help

# Command-specific help
omnipkg install --help
omnipkg daemon --help
```

## Interactive Web Bridge

Start the web bridge to run commands from your browser:
```bash
omnipkg web start
```

Then visit: `http://localhost:5000` to execute commands interactively!