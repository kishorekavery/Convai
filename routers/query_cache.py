"""
Per-user cache of the last query executed, used to serve follow-up pagination
("next 50", "show more") without re-running the retrieval + SQL-generation chain.

Keyed by (database_name, user_id). The tenant is part of the key because user
ids are only unique within a client database - two tenants both having a user
1278 must never be able to page each other's results.

Scope: this cache lives in the worker process. Each gunicorn worker keeps its
own copy, so a follow-up served by a different worker than the original query
will miss and the user is asked to restate their question. Entries also expire,
so a "next page" long after the fact re-runs the full chain rather than paging a
stale result. Moving this to Redis or a table would make follow-ups reliable
across workers.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from config import get_logger

logging = get_logger(__name__)

# Bound the cache so a long-running worker cannot accumulate one entry per user
# indefinitely, and expire entries so "more" only ever pages a recent result.
MAX_CACHED_QUERIES = 1000
CACHE_TTL_SECONDS = 30 * 60


@dataclass
class CachedQuery:
    """The state needed to build and answer the next page of a result set."""

    # SQL with the <facilitycode> placeholder still in it. The placeholder is
    # re-substituted from the *current* request on every page, so a user whose
    # facility access changed between pages cannot keep reading the old scope.
    sql_template: str
    # OFFSET that produced the page the user has already seen.
    offset: int
    # Rows per page, taken from the query's own LIMIT.
    page_size: int
    # Carried forward so the follow-up prompt keeps the schema and the
    # response-formatting examples that the original retrieval fetched.
    table_schema: str
    context_for_user_response: str
    # The question that produced page 1. A follow-up's own text ("show me more")
    # says nothing about what the rows are, so the final-response model needs
    # the original question to describe them.
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
        # NUL separator so a database name containing the separator character
        # cannot be crafted to collide with another tenant's key.
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
