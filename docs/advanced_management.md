# Advanced omnipkg Management

This section covers more advanced topics related to `omnipkg`'s internal workings, manual interventions (use with caution!), and future capabilities.

## Redis Knowledge Base Interaction

`omnipkg` relies on a Redis instance to store its "knowledge graph" – a comprehensive database of package metadata, file hashes, installed versions (active and bubbled), and environment snapshots. This allows for fast lookups, intelligent decision-making, and robust recovery.

You can interact with this knowledge base directly using `redis-cli`:

*   **Connect to Redis**:
    ```bash
    redis-cli
    ```
    (Assumes Redis is running on `localhost:6379`. Adjust if your `omnipkg` config uses different settings.)

*   **Explore Package Information**:
    `omnipkg` uses a prefix (default: `omnipkg:pkg:`) for its keys.

    *   **Get all recorded versions for a package**:
        ```bash
        SMEMBERS "omnipkg:pkg:your-package-name:installed_versions"
        ```
        (Replace `your-package-name` with the canonical name, e.g., `requests`, `numpy`, `typing-extensions`).
    *   **Get detailed metadata for a specific version**:
        ```bash
        HGETALL "omnipkg:pkg:your-package-name:your-version"
        ```
        (e.g., `HGETALL "omnipkg:pkg:tensorflow:2.13.0"`).
    *   **Inspect active versions (less common to directly query)**:
        ```bash
        HGETALL "omnipkg:pkg:your-package-name"
        ```
        This might show the currently active version recorded by `omnipkg`.

*   **Manually Flushing the Knowledge Base (`FLUSHDB`)**:
    **CAUTION**: This command will delete *all* data in the currently selected Redis database. Only use it if you are sure there is no other critical data in that Redis instance, or if you are using a dedicated Redis database for `omnipkg`.
    ```bash
    redis-cli FLUSHDB
    ```
    After flushing, you will need to run `omnipkg rebuild-kb` to repopulate the knowledge base.

## Manual Cleanup and Intervention

While `omnipkg` is designed to be self-healing and manage cleanup automatically, there might be rare cases where manual intervention is desired or necessary.

### Deleting Bubbles Manually

`omnipkg` stores its isolated package "bubbles" in a dedicated directory (configured during first-time setup, typically `~/.config/omnipkg/.omnipkg_versions` or within your `site-packages` directory under `.omnipkg_versions`). Each bubble is a subdirectory named `package_name-version` (e.g., `numpy-1.24.3`).

You can manually delete these directories if needed:

```bash
# Example: Delete the numpy-1.24.3 bubble
rm -rf /path/to/your/.omnipkg_versions/numpy-1.24.3
```
**CAUTION**: Manually deleting bubble directories will remove the package files but will **not** update `omnipkg`'s internal Redis knowledge base. If you do this, you should follow up with `omnipkg rebuild-kb` to resynchronize `omnipkg`'s understanding of your environment.

### Adding Missing Dependencies / Versions Manually (Advanced & Not Recommended)

`omnipkg`'s `smart_install` is designed to handle complex dependency resolution and bubble creation automatically. Manual installation of packages outside of `omnipkg` (e.g., directly with `pip` or by copying files) is generally discouraged as it can lead to an inconsistent state that `omnipkg` needs to reconcile.

However, in extreme debugging scenarios or if `omnipkg` were to encounter an unforeseen issue with a very specific package:
1.  You could theoretically install a package into a custom, isolated directory.
2.  Then, carefully move or copy that installed package (including its `.dist-info` or `.egg-info` metadata) into `omnipkg`'s `.omnipkg_versions` directory, ensuring it follows the correct `package_name-version` naming convention for the directory itself.
3.  After this manual placement, run `omnipkg rebuild-kb` to force `omnipkg` to discover and register this new "bubble."

**This is an advanced operation and should only be attempted if you fully understand Python's package structure and `omnipkg`'s internal layout.** It's almost always better to report an issue and let `omnipkg`'s `smart_install` handle the complexities.

## Understanding `omnipkg`'s Limitations (and Future Solutions)

While `omnipkg` solves many long-standing dependency issues, it operates within the constraints of the Python ecosystem. Currently, a major area of active development is:

*   **Python Interpreter Hot-Swapping**: `omnipkg` currently manages packages within a *single Python interpreter version* (e.g., Python 3.11). While `omnipkg` is architected to allow dynamic switching between different Python interpreters (e.g., switching from Python 3.8 to 3.11 mid-script), this feature is still under development. This is why the `stress-test` specifically requires Python 3.11. When implemented, this will further extend `omnipkg`'s power, allowing environments that truly blend Python versions seamlessly.

*   **"Time Machine" for Legacy Packages**: Some extremely old or niche Python packages, especially those with C extensions, rely on very specific build environments or have outdated/incorrect metadata on PyPI. `pip` (and therefore `omnipkg` which leverages `pip` for initial installation) can struggle with these. `omnipkg` is developing a "time machine" script and enhanced build/wheel capabilities to support these truly legacy packages by intelligently finding and building them against historically compatible toolchains, going beyond what current package managers can do.

These aren't fundamental flaws of `omnipkg`'s core isolation strategy, but rather challenges inherent in the vast and evolving Python ecosystem that `omnipkg` is uniquely positioned to solve.
