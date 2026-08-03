"""
Process-level cache of query embeddings.

Every request embeds the user's question before retrieval, which is a real
Bedrock round trip on the critical path. Maintenance chatbots see heavy
repetition - "PM compliance this month", "open breakdowns" - and an embedding is
a pure function of (model, text), so a repeat question can reuse the vector
instead of paying for it again.

Bounded LRU rather than TTL: the value never becomes stale for a given model and
text, so entries are only evicted for memory. The model id is part of the key so
that changing EMBEDDING_MODEL_ID cannot serve vectors from the old model.
"""

import re
import threading
from collections import OrderedDict
from typing import List, Optional, Tuple

from config import get_logger
from config import EMBEDDING_CACHE_MAX_ENTRIES

logging = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """
    Key form of a query. Collapses whitespace only - case and punctuation are
    left alone because they carry meaning the embedding model responds to.
    """
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


class EmbeddingCache:
    """Thread-safe bounded LRU of text -> embedding vector."""

    def __init__(self, max_entries: int = EMBEDDING_CACHE_MAX_ENTRIES) -> None:
        self._entries: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, model_id: str, text: str) -> Optional[List[float]]:
        key = (model_id, normalise(text))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, model_id: str, text: str, embedding: List[float]) -> None:
        if not embedding:
            return
        key = (model_id, normalise(text))
        with self._lock:
            self._entries[key] = embedding
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

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


embedding_cache = EmbeddingCache()
