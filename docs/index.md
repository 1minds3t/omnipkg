# omnipkg - The Ultimate Python Dependency Resolver

<div class="grid cards" markdown>

-   :rocket:{ .lg .middle } __Multi-Version Support__

    ---

    Run multiple versions of the same package simultaneously in one environment

-   :zap:{ .lg .middle } __Python Hot-Swapping__

    ---

    Switch Python interpreters mid-execution with zero downtime

-   :hospital:{ .lg .middle } __Auto-Healing__

    ---

    Automatically fix dependency conflicts in real-time

-   :infinity:{ .lg .middle } __Universal Execution__

    ---

    Handle any Python input - scripts, heredocs, pipes, inline code

</div>

## What is omnipkg?

omnipkg is not just another package manager. It's an intelligent, self-healing runtime orchestrator that breaks the fundamental laws of Python environments.

For 30 years, developers accepted that you couldn't run multiple Python versions in one script, or safely switch C-extensions like NumPy mid-execution. **omnipkg proves this is no longer true.**

### Quick Start
```bash
# Install omnipkg
pip install omnipkg

# Run the interactive demo
8pkg demo

# Install multiple versions
8pkg install torch==2.0.0 torch==2.7.1
```

### Why omnipkg?

Traditional package managers force you to choose: Docker overhead, slow venv switching, or dependency conflicts. omnipkg makes these problems irrelevant.

!!! success "Performance"
    - **5-7x faster** than UV for healing workflows
    - **Concurrent Python versions** in one environment
    - **Auto-healing** for broken dependencies
    - **24 languages** supported via AI localization

---

## Latest Release: v1.6.2

**Universal Runtime Healing** introduced! omnipkg can now accept Python code via any method and wrap them in an immortal, self-healing context.

[View Release Notes :octicons-arrow-right-24:](https://github.com/1minds3t/omnipkg/releases/tag/v1.6.2){ .md-button .md-button--primary }

---

## Next Steps

<div class="grid cards" markdown>

-   [Getting Started](getting_started.md)
-   [CLI Commands](cli_commands_reference.md)
-   [Python Hot-Swapping](python_hot_swapping.md)
-   [Runtime Switching](runtime_switching.md)

</div>
