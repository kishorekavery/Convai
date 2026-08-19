"""
Per-user cache of the last query executed.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from config import get_logger

logging = get_logger(__name__)

# Bound the cache limit and TTL
MAX_CACHED_QUERIES = 1000
CACHE_TTL_SECONDS = 30 * 60


@dataclass
class CachedQuery:
    """The state needed to build and answer the next page of a result set."""

    sql_template: str
    offset: int
    page_size: int
    table_schema: str
    context_for_user_response: str
    original_user_input: str
    created_at: float


class LastQueryCache:
    """Bounded, TTL-expiring, thread-safe LRU of the last query per user."""

    def __init__(
        self,
        max_entries: int = MAX_CACHED_QUERIES,
        ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> None:
        self._entries: "OrderedDict[str, CachedQuery]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(database_name: str, user_id: str) -> str:
        return f"{database_name}\x00{user_id}"

    def put(self, database_name: str, user_id: str, entry: CachedQuery) -> None:
        key = self._key(database_name, user_id)
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                logging.info(
                    "Query cache full (%s entries); evicted the oldest entry.",
                    self._max_entries,
                )

    def get(self, database_name: str, user_id: str) -> Optional[CachedQuery]:
        key = self._key(database_name, user_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None

            if time.time() - entry.created_at > self._ttl_seconds:
                del self._entries[key]
                logging.info(
                    "Cached query for user %s in %s expired after %s seconds.",
                    user_id,
                    database_name,
                    self._ttl_seconds,
                )
                return None

            self._entries.move_to_end(key)
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# Process-wide instance used by the inference router.
last_query_cache = LastQueryCache()
