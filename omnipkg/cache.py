try:
    from .common_utils import safe_print
except ImportError:
    from omnipkg.common_utils import safe_print
import sqlite3
import json
from pathlib import Path

class CacheClient:
    """An abstract base class for cache clients."""

    def hgetall(self, key):
        raise NotImplementedError

    def hset(self, key, field, value):
        raise NotImplementedError

    def smembers(self, key):
        raise NotImplementedError

    def sadd(self, key, value):
        raise NotImplementedError

    def srem(self, key, value):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError

    def delete(self, *keys):
        raise NotImplementedError

    def unlink(self, *keys):
        self.delete(*keys)

    def keys(self, pattern):
        raise NotImplementedError

    def pipeline(self):
        raise NotImplementedError

    def ping(self):
        raise NotImplementedError

class SQLiteCacheClient(CacheClient):
    """A SQLite-based cache client that emulates Redis commands."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._initialize_schema()

    def _initialize_schema(self):
        with self.conn:
            self.conn.execute('\n                CREATE TABLE IF NOT EXISTS kv_store (\n                    key TEXT PRIMARY KEY,\n                    value TEXT\n                )\n            ')
            self.conn.execute('\n                CREATE TABLE IF NOT EXISTS hash_store (\n                    key TEXT,\n                    field TEXT,\n                    value TEXT,\n                    PRIMARY KEY (key, field)\n                )\n            ')
            self.conn.execute('\n                CREATE TABLE IF NOT EXISTS set_store (\n                    key TEXT,\n                    member TEXT,\n                    PRIMARY KEY (key, member)\n                )\n            ')

    def hgetall(self, name: str):
        """
        Emulates Redis's HGETALL command for SQLite.
        Returns a dictionary of the hash stored at 'name'.
        """
        cursor = self.conn.cursor()
        data = {}
        try:
            cursor.execute('SELECT field, value FROM hash_store WHERE key = ?', (name,))
            rows = cursor.fetchall()
            data = {row[0]: row[1] for row in rows}
        finally:
            cursor.close()
        return data

    def hset(self, key, field=None, value=None, mapping=None):
        """
        Emulates Redis HSET.
        FIXED: Now supports the 'mapping' keyword argument for batch updates,
        making it compatible with the redis-py client's API.
        """
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
        """
        Emulates Redis's SADD command for SQLite, now correctly handling
        multiple values at once and using the CORRECT SCHEMA.
        """
        if not values:
            return 0
        cursor = self.conn.cursor()
        added_count = 0
        try:
            data_to_insert = [(name, value) for value in values]
            cursor.executemany('INSERT OR IGNORE INTO set_store (key, member) VALUES (?, ?)', data_to_insert)
            added_count = cursor.rowcount
            self.conn.commit()
        except self.conn.Error as e:
            safe_print(_('   ⚠️  [SQLiteCache] Error in sadd: {}').format(e))
            self.conn.rollback()
        finally:
            cursor.close()
        return added_count

    def srem(self, key, value):
        with self.conn:
            self.conn.execute('DELETE FROM set_store WHERE key = ? AND member = ?', (key, value))

    def get(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM kv_store WHERE key = ?', (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def set(self, key, value):
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

    def pipeline(self):
        """Returns itself to be used in a 'with' statement."""
        return self

    def __enter__(self):
        """Called when entering a 'with' block. Returns the pipeline object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Called when exiting a 'with' block. We don't need to do anything special here."""
        pass

    def execute(self):
        """A no-op to maintain compatibility with the redis-py pipeline API."""
        pass

    def ping(self):
        try:
            self.conn.cursor()
            return True
        except sqlite3.ProgrammingError:
            return False

    def hget(self, key, field):
        cur = self.conn.cursor()
        cur.execute('SELECT value FROM hash_store WHERE key = ? AND field = ?', (key, field))
        row = cur.fetchone()
        return row[0] if row else None

    def hdel(self, key, field):
        with self.conn:
            self.conn.execute('DELETE FROM hash_store WHERE key = ? AND field = ?', (key, field))

    def scard(self, key):
        cur = self.conn.cursor()
        cur.execute('SELECT COUNT(member) FROM set_store WHERE key = ?', (key,))
        return cur.fetchone()[0]

    def scan_iter(self, match='*', count=None):
        """
        A generator that emulates Redis's SCAN_ITER command for SQLite.
        This is crucial for making the SQLite cache a true drop-in replacement.
        """
        sql_pattern = match.replace('*', '%')
        cursor = self.conn.cursor()
        try:
            cursor.execute('\n                SELECT DISTINCT key FROM kv_store WHERE key LIKE ?\n                UNION\n                SELECT DISTINCT key FROM hash_store WHERE key LIKE ?\n                UNION\n                SELECT DISTINCT key FROM set_store WHERE key LIKE ?\n            ', (sql_pattern, sql_pattern, sql_pattern))
            keys = cursor.fetchall()
            for row in keys:
                yield row[0]
        finally:
            cursor.close()

    def sscan_iter(self, name, match='*', count=None):
        """
        A generator that emulates Redis's SSCAN_ITER command for SQLite.
        This iterates over members of a set stored at 'name'.
        """
        sql_pattern = match.replace('*', '%')
        cursor = self.conn.cursor()
        try:
            cursor.execute('SELECT member FROM set_store WHERE key = ? AND member LIKE ?', (name, sql_pattern))
            members = cursor.fetchall()
            for row in members:
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
            cursor.execute('SELECT field FROM hash_store WHERE key = ?', (name,))
            rows = cursor.fetchall()
            keys = [row[0] for row in rows]
        finally:
            cursor.close()
        return keys