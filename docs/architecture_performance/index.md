---
title: Architecture & Performance
doc_type: reference
status: stable
generated: true
created: '2026-02-25'
builder: gitship-docbuilder
builder_version: 2.1.0
section: architecture_performance
---

# Architecture & Performance

Omnipkg is designed to solve the "Linker Lock" problem—the inability to load multiple versions of C-extension libraries (like TensorFlow or NumPy) in a single Python process.

It achieves this through a distributed **Worker Daemon** architecture that outperforms traditional virtualization methods (Docker, Conda) for Python-specific workflows.

## Core Documentation

*   [**Deep Dive: The Daemon**](architecture_performance_deep_dive_the_daemon.md)  
    *   Understand the **Manager-Worker** architecture.
    *   Learn how **Universal CUDA IPC** achieves zero-copy data transfer.
    *   See the decision matrix for **Legacy Loader vs. Daemon**.

*   [**Benchmarking vs The World**](architecture_performance_benchmarking_vs_the_world.md)  
    *   See how omnipkg achieves **2ms execution latency**.
    *   Comparison against **Docker**, **Conda**, and **Venv**.
    *   Memory efficiency analysis (**1.9x vs Docker**).

## Key Concepts

### 1. Process Isolation > Virtualization
Instead of virtualizing the entire OS (Docker) or just the filesystem (Conda), omnipkg virtualizes the **Python Runtime**. This allows it to strip away overhead while maintaining the strict ABI isolation required for AI frameworks.

### 2. Intelligent Dispatch
The daemon automatically routes payloads based on data locality:
*   **GPU Data?** $\to$ Universal CUDA IPC (<5µs)
*   **Large CPU Data?** $\to$ Shared Memory Ring Buffer (~5ms)
*   **Config Data?** $\to$ Standard JSON Sockets (~10ms)