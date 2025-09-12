
# Advanced omnipkg Management

This section covers more advanced topics related to omnipkg's internal workings, manual interventions (use with caution!), and future capabilities.

### Knowledge Base Interaction

omnipkg relies on a high-performance database to store its "knowledge graph" – a comprehensive record of package metadata, file hashes, and environment snapshots. It uses SQLite by default and automatically upgrades to Redis if available.

#### Interacting with Redis (If Used)
You can interact with the knowledge base directly using `redis-cli`:
```bash
# Connect to Redis
redis-cli

# Get all recorded versions for a package
SMEMBERS "omnipkg:pkg:requests:installed_versions"

# Get detailed metadata for a specific version
HGETALL "omnipkg:pkg:numpy:1.24.3"
```
**CAUTION**: Manually flushing with `FLUSHDB` will delete all data. Only do this if you are using a dedicated Redis database for omnipkg. Follow up with `omnipkg rebuild-kb`.

#### Interacting with SQLite (Default)
The SQLite database is a single file located in your omnipkg configuration directory.
*   **Location**: `~/.config/omnipkg/omnipkg.db`
*   **Tools**: You can inspect it using any SQLite database browser or the `sqlite3` command-line tool.

```bash
# Example: Connect to the SQLite database
sqlite3 ~/.config/omnipkg/omnipkg.db

# Example: List all tables
.tables

# Example: Query for a package (SQL knowledge required)
SELECT * FROM packages WHERE name = 'numpy';
```
**CAUTION**: Manually altering the SQLite database can corrupt your omnipkg environment. It is safer to use `omnipkg rebuild-kb` to fix issues.

### Manual Cleanup and Intervention

While omnipkg is designed to be self-healing, there might be rare cases where manual intervention is desired. Always prefer using commands like `omnipkg uninstall` or `omnipkg prune` first.

#### Deleting Bubbles Manually
omnipkg stores its isolated package "bubbles" in a dedicated directory (configured during setup). You can manually delete these directories if needed:
```bash
# Example: Delete the numpy-1.24.3 bubble
rm -rf /path/to/your/.omnipkg_versions/numpy-1.24.3
```
**IMPORTANT**: After manually deleting bubble directories, you **must** run `omnipkg rebuild-kb` to resynchronize the knowledge base. Failure to do so will result in an inconsistent state.

#### Adding Missing Dependencies Manually (Advanced & Not Recommended)
The `omnipkg install` and `omnipkg run` commands are designed to handle dependency resolution automatically. Manual installation is strongly discouraged.

However, in extreme debugging scenarios, you could:
1.  Install a package into a custom, isolated directory.
2.  Carefully move that installed package into omnipkg's `.omnipkg_versions` directory, following the `package_name-version` naming convention.
3.  Run `omnipkg rebuild-kb --force` to make omnipkg discover and register this new "bubble."

This is a highly advanced operation. It is almost always better to report an issue and let omnipkg handle the complexities.
```
