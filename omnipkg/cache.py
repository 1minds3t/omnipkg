import sys
import threading
import sqlite3
import json
from pathlib import Path

# --- Self-contained safe_print for standalone utility use ---
_builtin_print = print
def safe_print(*args, **kwargs):
    try:
        _builtin_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            encoding = sys.stdout.encoding or 'utf-8'
            safe_args = [str(arg).encode(encoding, 'replace').decode(encoding) for arg in args]
            _builtin_print(*safe_args, **kwargs)
        except Exception:
            _builtin_print("[omnipkg: A message could not be displayed due to an encoding error.]")

class CacheClient:
    """An abstract base class for cache clients."""
    def hgetall(self, key): raise NotImplementedError
    def hset(self, key, field, value, mapping=None): raise NotImplementedError
    def smembers(self, key): raise NotImplementedError
    def sadd(self, key, *values): raise NotImplementedError
    def srem(self, key, value): raise NotImplementedError
    def get(self, key): raise NotImplementedError
    def set(self, key, value, ex=None): raise NotImplementedError
    def exists(self, key): raise NotImplementedError
    def delete(self, *keys): raise NotImplementedError
    def unlink(self, *keys): self.delete(*keys)
    def keys(self, pattern): raise NotImplementedError
    def pipeline(self): raise NotImplementedError
    def ping(self): raise NotImplementedError
    def hget(self, key, field): raise NotImplementedError
    def hdel(self, key, *fields): raise NotImplementedError
    def scard(self, key): raise NotImplementedError
    def scan_iter(self, match='*', count=None): raise NotImplementedError
    def sscan_iter(self, name, match='*', count=None): raise NotImplementedError
    def hkeys(self, name: str): raise NotImplementedError


class SQLiteCacheClient(CacheClient):
    """A SQLite-based cache client that emulates Redis commands."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._initialize_schema()

    def _initialize_schema(self):
        with self.conn:
            self.conn.execute(
                'CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)'
            )
            self.conn.execute(
                'CREATE TABLE IF NOT EXISTS hash_store (key TEXT, field TEXT, value TEXT, PRIMARY KEY (key, field))'
            )
            self.conn.execute(
                'CREATE TABLE IF NOT EXISTS set_store (key TEXT, member TEXT, PRIMARY KEY (key, member))'
            )

    def hgetall(self, name: str):
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT field, value FROM hash_store WHERE key = ?', (name,))
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            cursor.close()

    def hset(self, key, field=None, value=None, mapping=None):
        if mapping is not None:
            if not isinstance(mapping, dict):
                raise TypeError("The 'mapping' argument must be a dictionary.")
            data_to_insert = [(key, str(k), str(v)) for k, v in mapping.items()]
            with self.conn:
                self.conn.executemany('INSERT OR REPLACE INTO hash_store (key, field, value) VALUES (?, ?, ?)', data_to_insert)
        elif field is not None:
            with self.conn:
                self.conn.execute('INSERT OR REPLACE INTO hash_store (key, field, value) VALUES (?, ?, ?)', (key, str(field), str(value)))
        else:
            raise ValueError('hset requires either a field/value pair or a mapping')

    def smembers(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT member FROM set_store WHERE key = ?', (key,))
        return {row[0] for row in cur.fetchall()}

    def sadd(self, name: str, *values):
        if not values:
            return 0
        cursor = self.conn.cursor()
        try:
            data_to_insert = [(name, value) for value in values]
            cursor.executemany('INSERT OR IGNORE INTO set_store (key, member) VALUES (?, ?)', data_to_insert)
            self.conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()

    def srem(self, key, value):
        with self.conn:
            self.conn.execute('DELETE FROM set_store WHERE key = ? AND member = ?', (key, value))

    def get(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM kv_store WHERE key = ?', (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def set(self, key, value, ex=None): # Added ex for TTL compatibility
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)', (key, value))

    def exists(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT 1 FROM kv_store WHERE key = ? UNION ALL SELECT 1 FROM hash_store WHERE key = ? UNION ALL SELECT 1 FROM set_store WHERE key = ? LIMIT 1', (key, key, key))
        return cur.fetchone() is not None

    def delete(self, *keys):
        with self.conn:
            for key in keys:
                self.conn.execute('DELETE FROM kv_store WHERE key = ?', (key,))
                self.conn.execute('DELETE FROM hash_store WHERE key = ?', (key,))
                self.conn.execute('DELETE FROM set_store WHERE key = ?', (key,))
                
    def keys(self, pattern):
        sql_pattern = pattern.replace('*', '%')
        cur = self.conn.cursor()
        cur.execute('SELECT DISTINCT key FROM kv_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM hash_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM set_store WHERE key LIKE ?', (sql_pattern, sql_pattern, sql_pattern))
        return [row[0] for row in cur.fetchall()]

    def ping(self):
        try:
            self.conn.cursor()
            return True
        except (sqlite3.ProgrammingError, sqlite3.InterfaceError):
            return False

    def hget(self, key, field):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM hash_store WHERE key = ? AND field = ?', (key, field))
        row = cur.fetchone()
        return row[0] if row else None

    def hdel(self, key, *fields):
        with self.conn:
            for field in fields:
                self.conn.execute('DELETE FROM hash_store WHERE key = ? AND field = ?', (key, field))

    def scard(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT COUNT(member) FROM set_store WHERE key = ?', (key,))
        return cur.fetchone()[0]

    def scan_iter(self, match='*', count=None):
        sql_pattern = match.replace('*', '%')
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT DISTINCT key FROM kv_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM hash_store WHERE key LIKE ? UNION SELECT DISTINCT key FROM set_store WHERE key LIKE ?', (sql_pattern, sql_pattern, sql_pattern))
            for row in cursor.fetchall():
                yield row[0]
        finally:
            cursor.close()

    def sscan_iter(self, name, match='*', count=None):
        sql_pattern = match.replace('*', '%')
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT member FROM set_store WHERE key = ? AND member LIKE ?', (name, sql_pattern))
            for row in cursor.fetchall():
                yield row[0]
        finally:
            cursor.close()

    def hkeys(self, name: str):
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT field FROM hash_store WHERE key = ?', (name,))
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            
    # --- START: THE CRITICAL PIPELINE FIX ---
    def pipeline(self):
        """Returns a new, dedicated pipeline object for each call."""
        return SQLitePipeline(self)
    # --- END: THE CRITICAL PIPELINE FIX ---


class SQLitePipeline:
    """
    A stateful pipeline for the SQLiteCacheClient that collects commands
    and executes them in a batch, returning results just like redis-py.
    """
    def __init__(self, client: SQLiteCacheClient):
        self.client = client
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.commands = []

    def execute(self):
        """Executes all queued commands and returns a list of their results."""
        if not self.commands:
            return []
        
        results = []
        for command_func, args, kwargs in self.commands:
            try:
                result = command_func(*args, **kwargs)
                results.append(result)
            except Exception as e:
                results.append(e)
        
        self.commands = []
        return results

    # --- Add all methods that can be pipelined ---
    # They don't execute immediately; they just add the command to the queue.
    def hgetall(self, key):
        self.commands.append((self.client.hgetall, [key], {}))
        return self
    
    def hset(self, key, field=None, value=None, mapping=None):
        self.commands.append((self.client.hset, [], {'key': key, 'field': field, 'value': value, 'mapping': mapping}))
        return self
    
    def delete(self, *keys):
        self.commands.append((self.client.delete, keys, {}))
        return self
    
    def srem(self, key, value):
        self.commands.append((self.client.srem, [key, value], {}))
        return self
    
    def hdel(self, key, *fields):
        self.commands.append((self.client.hdel, [key] + list(fields), {}))
        return self
    
    def sadd(self, name: str, *values):
        # Pass `name` and the tuple of `values` to the client's sadd method
        self.commands.append((self.client.sadd, [name] + list(values), {}))
        return self

    def set(self, key, value, ex=None):
        # The 'ex' argument for TTL is needed for redis-py compatibility
        self.commands.append((self.client.set, [key, value], {'ex': ex}))
        return self

    # Add other methods here as needed to expand pipeline functionality.