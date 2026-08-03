"""
Process-level cache of formatted table schemas.

Schemas change on migration, not per request, but the retrieval step re-read
them from ``information_schema.columns`` on every call. That view is a join over
pg_attribute/pg_class/pg_namespace/pg_type with a per-row privilege check, and
the database is remote, so those reads sat on the critical path of every
request before SQL generation could begin.

Entries are keyed by (database_name, table_name) because each tenant has its own
database and the same table name can have a different shape in each.

A negative result (table not found) is cached too - it is the correct answer
until the next migration, and caching it stops a mistyped or renamed table from
being looked up again on every request. The TTL bounds how long any staleness,
positive or negative, can persist.
"""

import threading
import time
from typing import Dict, Optional, Tuple

from config import get_logger
from config import SCHEMA_CACHE_TTL_SECONDS

logging = get_logger(__name__)


class TableSchemaCache:
    """Thread-safe TTL cache of formatted schema text."""

    def __init__(self, ttl_seconds: int = SCHEMA_CACHE_TTL_SECONDS) -> None:
        # key -> (formatted_schema, stored_at)
        self._entries: Dict[Tuple[str, str], Tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(database_name: str, table_name: str) -> Tuple[str, str]:
        return (database_name, table_name)

    def get(self, database_name: str, table_name: str) -> Optional[str]:
        """Return the cached schema text, or None when absent or expired."""
        key = self._key(database_name, table_name)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None

            formatted, stored_at = entry
            if time.time() - stored_at > self._ttl_seconds:
                del self._entries[key]
                self.misses += 1
                return None

            self.hits += 1
            return formatted

    def put(self, database_name: str, table_name: str, formatted_schema: str) -> None:
        with self._lock:
            self._entries[self._key(database_name, table_name)] = (
                formatted_schema,
                time.time(),
            )

    def invalidate(self, database_name: str, table_name: Optional[str] = None) -> None:
        """
        Drop cached schemas. Call after a migration; without a table_name the
        whole database is dropped.
        """
        with self._lock:
            if table_name is not None:
                self._entries.pop(self._key(database_name, table_name), None)
                return
            for key in [k for k in self._entries if k[0] == database_name]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
            }


# Shared by the retrieval path; one instance per worker process.
table_schema_cache = TableSchemaCache()
