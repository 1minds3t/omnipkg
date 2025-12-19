"""
Omnipkg Metadata Client - Fetches enriched package metadata from GitHub
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests


class MetadataCache:
    """Client for fetching package metadata from omnipkg-metadata repo"""

    # GitHub raw content URL
    REPO_BASE = "https://raw.githubusercontent.com/1minds3t/omnipkg-metadata/main"

    # Cache TTL: 24 hours
    CACHE_TTL_HOURS = 24

    def __init__(self, cache_db_path: str):
        self.cache_db = Path(cache_db_path)
        self._init_cache_db()

    def _init_cache_db(self):
        """Initialize SQLite cache for GitHub metadata"""
        with sqlite3.connect(self.cache_db) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS github_metadata_cache (
                    package TEXT PRIMARY KEY,
                    metadata TEXT,
                    compat_data TEXT,
                    last_fetched TIMESTAMP,
                    cache_hit_count INTEGER DEFAULT 0
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_last_fetched 
                ON github_metadata_cache(last_fetched)
            """
            )

    def _is_cache_valid(self, last_fetched: Optional[str]) -> bool:
        """Check if cache entry is still valid"""
        if not last_fetched:
            return False

        fetched_time = datetime.fromisoformat(last_fetched)
        now = datetime.now(timezone.utc)
        age = now - fetched_time

        return age < timedelta(hours=self.CACHE_TTL_HOURS)

    def _fetch_from_github(self, package: str) -> tuple[Optional[Dict], Optional[Dict]]:
        """Fetch package metadata and compatibility data from GitHub"""
        metadata = None
        compat_data = None

        try:
            # Fetch metadata
            meta_url = f"{self.REPO_BASE}/metadata/{package}.json"
            resp = requests.get(meta_url, timeout=5)
            if resp.status_code == 200:
                metadata = resp.json()
                print(f"✓ Fetched metadata for {package} from GitHub")
            elif resp.status_code == 404:
                print(f"⚠ Package {package} not in GitHub metadata repo")
            else:
                print(f"✗ Failed to fetch metadata: HTTP {resp.status_code}")

        except Exception as e:
            print(f"✗ Error fetching metadata: {e}")

        try:
            # Fetch compatibility data
            compat_url = f"{self.REPO_BASE}/compat/{package}.json"
            resp = requests.get(compat_url, timeout=5)
            if resp.status_code == 200:
                compat_data = resp.json()
                print(f"✓ Fetched compatibility data for {package}")

        except Exception:
            # Compatibility data is optional
            pass

        return metadata, compat_data

    def get_package_info(self, package: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get package metadata with smart caching

        Returns:
            {
                "metadata": {...},  # PyPI metadata from GitHub
                "compat": {...},    # Compatibility test results
                "source": "cache" | "github" | "none",
                "cache_age_hours": float
            }
        """
        result = {
            "metadata": None,
            "compat": None,
            "source": "none",
            "cache_age_hours": None,
        }

        # Check cache first
        if not force_refresh:
            with sqlite3.connect(self.cache_db) as conn:
                row = conn.execute(
                    "SELECT metadata, compat_data, last_fetched FROM github_metadata_cache WHERE package = ?",
                    (package,),
                ).fetchone()

                if row:
                    metadata_str, compat_str, last_fetched = row

                    if self._is_cache_valid(last_fetched):
                        # Cache hit!
                        result["metadata"] = json.loads(metadata_str) if metadata_str else None
                        result["compat"] = json.loads(compat_str) if compat_str else None
                        result["source"] = "cache"

                        fetched_time = datetime.fromisoformat(last_fetched)
                        age = datetime.now(timezone.utc) - fetched_time
                        result["cache_age_hours"] = age.total_seconds() / 3600

                        # Increment hit counter
                        conn.execute(
                            "UPDATE github_metadata_cache SET cache_hit_count = cache_hit_count + 1 WHERE package = ?",
                            (package,),
                        )

                        print(f"✓ Cache hit for {package} (age: {result['cache_age_hours']:.1f}h)")
                        return result

        # Cache miss or expired - fetch from GitHub
        print(f"⟳ Fetching {package} from GitHub (cache expired or forced refresh)")
        metadata, compat_data = self._fetch_from_github(package)

        if metadata or compat_data:
            # Update cache
            with sqlite3.connect(self.cache_db) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO github_metadata_cache 
                    (package, metadata, compat_data, last_fetched, cache_hit_count)
                    VALUES (?, ?, ?, ?, COALESCE((SELECT cache_hit_count FROM github_metadata_cache WHERE package = ?), 0))
                """,
                    (
                        package,
                        json.dumps(metadata) if metadata else None,
                        json.dumps(compat_data) if compat_data else None,
                        datetime.now(timezone.utc).isoformat(),
                        package,
                    ),
                )

            result["metadata"] = metadata
            result["compat"] = compat_data
            result["source"] = "github"
            result["cache_age_hours"] = 0

        return result

    def check_compatibility(
        self, package: str, python_version: str, platform: str
    ) -> Optional[Dict]:
        """
        Check if package has known compatibility issues

        Args:
            package: Package name
            python_version: e.g. "3.11"
            platform: e.g. "linux-x64", "macos-x64"

        Returns:
            Latest test result for this platform/version, or None
        """
        info = self.get_package_info(package)

        if not info["compat"] or "test_results" not in info["compat"]:
            return None

        # Find matching test results
        for result in reversed(info["compat"]["test_results"]):
            if (
                result.get("python_version") == python_version
                and result.get("platform") == platform
            ):
                return result

        return None

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with sqlite3.connect(self.cache_db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM github_metadata_cache").fetchone()[0]

            expired = conn.execute(
                """
                SELECT COUNT(*) FROM github_metadata_cache 
                WHERE datetime(last_fetched) < datetime('now', '-24 hours')
            """
            ).fetchone()[0]

            total_hits = (
                conn.execute("SELECT SUM(cache_hit_count) FROM github_metadata_cache").fetchone()[0]
                or 0
            )

            top_packages = conn.execute(
                """
                SELECT package, cache_hit_count 
                FROM github_metadata_cache 
                ORDER BY cache_hit_count DESC 
                LIMIT 10
            """
            ).fetchall()

        return {
            "total_cached_packages": total,
            "expired_entries": expired,
            "total_cache_hits": total_hits,
            "top_packages": [{"package": pkg, "hits": hits} for pkg, hits in top_packages],
        }


# Example usage in omnipkg
if __name__ == "__main__":
    # Initialize cache
    cache = MetadataCache("~/.config/omnipkg/github_metadata.sqlite")

    # Get package info (will fetch from GitHub first time, then cache)
    info = cache.get_package_info("torch")

    if info["metadata"]:
        print(f"\nPackage: {info['metadata']['name']}")
        print(f"Latest Version: {info['metadata']['version']}")
        print(f"Summary: {info['metadata']['summary']}")
        print(f"Source: {info['source']}")

    # Check compatibility for current platform
    compat = cache.check_compatibility("torch", "3.11", "linux-x64")
    if compat:
        print("\nCompatibility for Python 3.11 on Linux:")
        print(f"  Install Success: {compat['install_success']}")
        print(f"  Import Success: {compat['import_success']}")
        print(f"  Install Time: {compat['install_time_seconds']}s")
        if compat["errors"]:
            print(f"  ⚠ Known Issues: {compat['errors']}")

    # Get stats
    stats = cache.get_cache_stats()
    print("\nCache Stats:")
    print(f"  Total Packages: {stats['total_cached_packages']}")
    print(f"  Cache Hits: {stats['total_cache_hits']}")
    print(f"  Expired Entries: {stats['expired_entries']}")
