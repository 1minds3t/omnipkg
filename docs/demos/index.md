---
title: Demos
doc_type: tutorial
status: stable
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

Yes, for the **Rich Test** (Demo 1), if you want to run it **directly** (non-interactive mode) in your docs code block, the command is:

```bash
omnipkg demo 1
```

*(In the previous Rich Test draft, I used the log output you gave me which was interactive `omnipkg demo --verbose` then selecting `1`. But for the "Usage" section of the docs, `omnipkg demo 1` is the correct instruction to give users.)*

Here is the **Index Overview** for `docs/demos/index.md` that catalogs all 11 demos found in your CLI.

**Paste this into:** `docs/demos/index.md`

```markdown
---
title: Demos
doc_type: tutorial
status: stable
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: demos
---

# Demos

Welcome to the **OmniPkg Live Demos** section.

OmniPkg includes a built-in suite of 11 interactive demos designed to showcase specific capabilities of the resolver, from basic module hot-swapping to "impossible" concurrent C-extension loading.

## How to Run

You can access the demo suite directly from the CLI.

**Interactive Menu:**
```bash
omnipkg demo
```

**Run a specific demo directly:**
```bash
# Syntax: omnipkg demo <ID>
omnipkg demo 1
```

## Demo Catalog

Below is the complete list of available demos included in the current build.

| ID | Name | What it Demonstrates |
| :--- | :--- | :--- |
| **1** | **Rich Module Switching** | **Pure Python Hot-Swapping.** Instantly switches between `rich` versions (v13.7.1, v13.5.3, etc.) within the same process. |
| **2** | **UV Binary Test** | **Binary Executable Management.** Swaps underlying binary versions of tools like `uv` without changing the global PATH. |
| **3** | **NumPy + SciPy Stress** | **C-Extension Swapping.** The "Holy Grail" test. Swaps incompatible `numpy`/`scipy` pairs mid-execution without crashing via memory isolation. |
| **4** | **TensorFlow Test** | **Complex Dependency Trees.** Handles massive packages with deep dependency graphs (TensorFlow, Keras, etc.). |
| **5** | **Multiverse Healing** | **Cross-Python Healing.** Detects missing dependencies mid-script and "heals" them by hopping between Python 3.8/3.10/3.11 contexts. |
| **6** | **Legacy Flask Test** | **Legacy Support.** Runs an ancient Flask app requiring Python 3.8 inside a modern Python 3.11 environment. |
| **7** | **Script Healing** | **`omnipkg run` Validation.** Demonstrates the auto-wrapping capability for standalone Python scripts. |
| **8** | **Quantum Warp** | **Concurrent Installations.** Installs packages into multiple Python versions simultaneously (Parallel processing test). |
| **9** | **Flask Port Finder** | **Runtime Auto-Healing.** A Flask app that crashes due to missing deps, gets healed by OmniPkg, and restarts automatically. |
| **10** | **CLI Healing** | **Shell Command Healing.** Intercepts failing shell commands and fixes the environment before re-running them. |
| **11** | **Chaos Theory** | **The Stress Test.** A torture test that creates race conditions, circular dependencies, and massive loads to verify stability. |

## Documentation Status

Each demo above has its own detailed documentation page covering:
1. The code behind the demo.
2. The specific OmniPkg features utilized.
3. Live execution logs.
```