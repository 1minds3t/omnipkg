# omnipkg/cache.py

import sqlite3
import json
from pathlib import Path

class CacheClient:
    """An abstract base class for cache clients."""
    def hgetall(self, key): raise NotImplementedError
    def hset(self, key, field, value): raise NotImplementedError
    def smembers(self, key): raise NotImplementedError
    def sadd(self, key, value): raise NotImplementedError
    def srem(self, key, value): raise NotImplementedError
    def get(self, key): raise NotImplementedError
    def set(self, key, value): raise NotImplementedError
    def exists(self, key): raise NotImplementedError
    def delete(self, *keys): raise NotImplementedError
    def unlink(self, *keys): self.delete(*keys) # Alias for delete
    def keys(self, pattern): raise NotImplementedError
    def pipeline(self): raise NotImplementedError
    def ping(self): raise NotImplementedError

class SQLiteCacheClient(CacheClient):
    """A SQLite-based cache client that emulates Redis commands."""
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Use a higher timeout to prevent database locked errors during concurrent access
        self.conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._initialize_schema()

    def _initialize_schema(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS hash_store (
                    key TEXT,
                    field TEXT,
                    value TEXT,
                    PRIMARY KEY (key, field)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS set_store (
                    key TEXT,
                    member TEXT,
                    PRIMARY KEY (key, member)
                )
            """)

    def hgetall(self, name: str):
        """
        Emulates Redis's HGETALL command for SQLite.
        Returns a dictionary of the hash stored at 'name'.
        """
        cursor = self.conn.cursor()
        data = {}
        try:
            cursor.execute("SELECT field, value FROM hash_store WHERE key = ?", (name,))
            rows = cursor.fetchall()
            data = {row[0]: row[1] for row in rows}
        finally:
            cursor.close()
        return data

    # In omnipkg/cache.py

    # ------------------ START OF REPLACEMENT ------------------
    # DELETE your old hset method and REPLACE it with this one.

    def hset(self, key, field=None, value=None, mapping=None):
        """
        Emulates Redis HSET.
        FIXED: Now supports the 'mapping' keyword argument for batch updates,
        making it compatible with the redis-py client's API.
        """
        if mapping is not None:
            # This handles the batch update case: hset(key, mapping={...})
            if not isinstance(mapping, dict):
                raise TypeError("The 'mapping' argument must be a dictionary.")
            
            # Use fast executemany for bulk inserts
            data_to_insert = [(key, str(k), str(v)) for k, v in mapping.items()]
            with self.conn:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO hash_store (key, field, value) VALUES (?, ?, ?)",
                    data_to_insert
                )
        elif field is not None:
            # This handles the original single update case: hset(key, field, value)
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO hash_store (key, field, value) VALUES (?, ?, ?)",
                    (key, str(field), str(value))
                )
        else:
            # Raise an error if called improperly
            raise ValueError("hset requires either a field/value pair or a mapping")

    # ------------------- END OF REPLACEMENT -------------------

    def smembers(self, key):
        cur = self.conn.cursor()
        cur.execute("SELECT member FROM set_store WHERE key = ?", (key,))
        return {row[0] for row in cur.fetchall()}

    def sadd(self, name: str, *values):
        """
        Emulates Redis's SADD command for SQLite, now correctly handling
        multiple values at once. This is critical for compatibility with
        the application's bulk-add operations.
        """
        if not values:
            return 0  # No values to add
        
        cursor = self.conn.cursor()
        added_count = 0
        try:
            # Prepare the data for a bulk insert.
            # We use INSERT OR IGNORE to automatically handle duplicates,
            # which is the core behavior of a set.
            data_to_insert = [(name, value) for value in values]
            
            # Use executemany for a fast, bulk insert operation.
            cursor.executemany("INSERT OR IGNORE INTO set_store (key, value) VALUES (?, ?)", data_to_insert)
            
            # The number of rows changed is the number of new items added.
            added_count = cursor.rowcount
            self.conn.commit()
        except self.conn.Error as e:
            print(f"   ⚠️  [SQLiteCache] Error in sadd: {e}")
            self.conn.rollback()
        finally:
            cursor.close()
        
        return added_count

    def srem(self, key, value):
        with self.conn:
            self.conn.execute("DELETE FROM set_store WHERE key = ? AND member = ?", (key, value))
    
    def get(self, key):
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def set(self, key, value):
         with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (key, value))

    def exists(self, key):
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM kv_store WHERE key = ? UNION ALL SELECT 1 FROM hash_store WHERE key = ? UNION ALL SELECT 1 FROM set_store WHERE key = ? LIMIT 1", (key, key, key))
        return cur.fetchone() is not None

    def delete(self, *keys):
        with self.conn:
            for key in keys:
                self.conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
                self.conn.execute("DELETE FROM hash_store WHERE key = ?", (key,))
                self.conn.execute("DELETE FROM set_store WHERE key = ?", (key,))

    def keys(self, pattern):
        # Basic wildcard matching for SQLite
        sql_pattern = pattern.replace('*', '%')
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT key FROM kv_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM hash_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM set_store WHERE key LIKE ?", (sql_pattern, sql_pattern, sql_pattern))
        return [row[0] for row in cur.fetchall()]

    def pipeline(self):
        """Returns itself to be used in a 'with' statement."""
        # This is the key insight: the pipeline object is just the client itself.
        return self

    # --- THE CRITICAL FIX START ---
    # These two methods make the object a valid context manager.
    def __enter__(self):
        """Called when entering a 'with' block. Returns the pipeline object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Called when exiting a 'with' block. We don't need to do anything special here."""
        pass
    # --- THE CRITICAL FIX END ---

    def execute(self):
        """A no-op to maintain compatibility with the redis-py pipeline API."""
        # In a real transaction, this would commit the changes.
        # Since we commit on every statement, this does nothing.
        pass

    def ping(self):
        try:
            self.conn.cursor()
            return True
        except sqlite3.ProgrammingError:
            return False

    def hget(self, key, field):
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM hash_store WHERE key = ? AND field = ?", (key, field))
        row = cur.fetchone()
        return row[0] if row else None

    def hdel(self, key, field):
        with self.conn:
            self.conn.execute("DELETE FROM hash_store WHERE key = ? AND field = ?", (key, field))

    def scard(self, key):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(member) FROM set_store WHERE key = ?", (key,))
        return cur.fetchone()[0]
    
    # In omnipkg/cache.py, inside the SQLiteCacheClient class...

    def scan_iter(self, match='*', count=None):
        """
        A generator that emulates Redis's SCAN_ITER command for SQLite.
        This is crucial for making the SQLite cache a true drop-in replacement.
        """
        # The `match` pattern in Redis uses '*' as a wildcard.
        # The SQL `LIKE` operator uses '%' as a wildcard.
        sql_pattern = match.replace('*', '%')
        
        cursor = self.conn.cursor()
        try:
            # We are selecting the 'key' column from the 'kv_store' table
            # where the key matches the pattern.
            cursor.execute("SELECT key FROM kv_store WHERE key LIKE ?", (sql_pattern,))
            
            # Fetch all matching keys at once. For this use case, it's efficient enough.
            keys = cursor.fetchall()
            
            # The generator yields one key at a time, just like scan_iter.
            for row in keys:
                yield row[0]
        finally:
            cursor.close()

    def hkeys(self, name: str):
        """
        Emulates Redis's HKEYS command for SQLite.
        Returns all the field names in the hash stored at 'name'.
        """
        cursor = self.conn.cursor()
        keys = []
        try:
            # The key for a hash in our schema is the 'name' parameter.
            # The 'field' column holds the keys of the hash.
            cursor.execute("SELECT field FROM hash_store WHERE key = ?", (name,))
            rows = cursor.fetchall()
            # fetchall() returns a list of tuples, e.g., [('key1',), ('key2',)]
            # We need to flatten this into a simple list of strings.
            keys = [row[0] for row in rows]
        finally:
            cursor.close()
        return keys