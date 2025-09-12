
# omnipkg Future Roadmap: The Path to a Universal Development Engine

omnipkg is not just a package manager; it's a foundational platform for highly dynamic and intelligent development environments. Having already delivered groundbreaking features like **Runtime Package Switching** and **Live Python Interpreter Hot-Swapping**, our roadmap is focused on solving the next set of "impossible" problems in software development.

We are moving beyond reactive problem-solving to create a proactive, self-healing, and language-agnostic development engine.

### 🚀 Key Areas of Development

#### 1. The "Time Machine": Perfect Legacy Package Resolution
*   **Status**: Initial script complete and functional.
*   **Goal**: To make any package, from any era, installable and usable today. The "Time Machine" will solve dependencies that are unsolvable with modern tools by:
    1.  **Historical Environment Recreation**: Intelligently detecting the build requirements of a legacy package (e.g., Python 3.7, a specific compiler, older setuptools).
    2.  **Automated Interpreter Management**: Automatically adopting and swapping to the correct Python interpreter needed to build the package from source.
    3.  **Modern Wheel Generation**: Once built, the package will be converted into a modern wheel, making it compatible and installable within newer Python environments, bridging the gap between legacy code and modern projects.

#### 2. Proactive, AI-Driven Runtime Healing & Environment Building
*   **Status**: Advanced `omnipkg run` command implemented.
*   **Goal**: To evolve the runtime healer from a reactive tool into a proactive environment builder. The future `omnipkg run` will:
    1.  **Auto-Adopt & Build**: When a script requires a legacy package not present, the healer will automatically invoke the **Time Machine**, adopt the necessary Python interpreter, build the package, and make it available to your main environment—all in a single, seamless operation.
    2.  **Pluggable Resolvers**: Allow users to choose their underlying dependency resolver (e.g., `uv`, `pip`, `mamba`, `pixi`) to align with their philosophy, whether it's maximum speed (`fast-compat`) or maximum compatibility (`max-compat`).

#### 3. True Concurrent Multiverse Execution
*   **Status**: Manual PoC successful; automated tests in development.
*   **Goal**: To achieve something unprecedented: running multiple Python interpreters **simultaneously in parallel** within a single script and a single environment.
    *   **Instant Cross-Version Testing**: This will revolutionize CI/CD and local testing. A command could execute a test suite across Python 3.9, 3.10, and 3.11 concurrently, returning results almost instantly. What currently takes minutes in separate jobs will take seconds in one.
    *   **Hardened CI/CD Integration**: We will focus heavily on hardening the interpreter swapping and concurrency logic to be flawlessly reliable across any OS and CI provider (GitHub Actions, GitLab CI, etc.).

#### 4. Hyper-Efficient Storage via Advanced Deduplication
*   **Status**: R&D phase; building on previous successes.
*   **Goal**: To dramatically reduce the disk-space footprint of complex environments by re-architecting deduplication with our new, robust runtime capabilities.
    1.  **C-Extension Symlinking**: With our enhanced understanding of bubble architecture and runtime path manipulation, we will revisit and perfect the symlinking of C-extensions. The goal is to achieve **~80% average disk savings** on heavy scientific computing and AI libraries.
    2.  **Cross-Interpreter Bubble Sharing**: Where possible, allow bubbles to be shared between different Python versions, further reducing duplication.
    3.  **Cross-Version Module Sharing (Experimental)**: Research the possibility of safely sharing individual compatible modules *between different versions of the same package*. This is the ultimate form of deduplication and could enable novel solutions to complex dependency graphs.

#### 5. Beyond Python: True Multi-Language Management
*   **Status**: Design phase.
*   **Goal**: To make `omnipkg` a truly "omni" package manager by extending its core principles of bubbling, healing, and dynamic loading to other ecosystems.
    *   **Unified Syntax**: `omnipkg install npm:react ruby:rails go:gin`
    *   **Cross-Language Resolver**: A smart resolver that can identify packages across different language ecosystems and manage them.
    *   **Universal Runtime Healing**: The `omnipkg run` command will be extended to understand and auto-heal errors in Node.js, Ruby, and other language scripts, providing the same seamless experience Python users enjoy.

We are not just building a package manager; we are building the future of dynamic, intelligent development environments. Stay tuned for these groundbreaking developments!
