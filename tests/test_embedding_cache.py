"""
Tests for the query-embedding cache.

Bedrock is stubbed, so these assert on how many times the model would actually
have been invoked.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.embedding_cache import EmbeddingCache, normalise  # noqa: E402

VECTOR = [0.1] * 8


class TestNormalise:
    def test_trims_and_collapses_whitespace(self):
        assert normalise("  how   many\nbreakdowns ") == "how many breakdowns"

    def test_preserves_case_and_punctuation(self):
        # Both carry meaning the embedding model responds to, so they are not
        # stripped - a cache hit must mean the same vector, not a similar one.
        assert normalise("PM Compliance?") == "PM Compliance?"

    def test_handles_empty(self):
        assert normalise("") == ""
        assert normalise(None) == ""


class TestCache:
    def test_round_trip(self):
        cache = EmbeddingCache()
        cache.put("titan", "how many breakdowns", VECTOR)
        assert cache.get("titan", "how many breakdowns") == VECTOR

    def test_miss_returns_none(self):
        assert EmbeddingCache().get("titan", "never asked") is None

    def test_whitespace_variants_share_an_entry(self):
        cache = EmbeddingCache()
        cache.put("titan", "how many breakdowns", VECTOR)
        assert cache.get("titan", "  how   many  breakdowns  ") == VECTOR

    def test_different_case_is_a_different_entry(self):
        cache = EmbeddingCache()
        cache.put("titan", "PM compliance", VECTOR)
        assert cache.get("titan", "pm compliance") is None

    def test_model_id_is_part_of_the_key(self):
        # Changing EMBEDDING_MODEL_ID must not serve vectors from the old model.
        cache = EmbeddingCache()
        cache.put("titan-v1", "q", VECTOR)
        assert cache.get("titan-v2", "q") is None

    def test_evicts_least_recently_used(self):
        cache = EmbeddingCache(max_entries=2)
        cache.put("titan", "a", VECTOR)
        cache.put("titan", "b", VECTOR)
        cache.put("titan", "c", VECTOR)
        assert cache.get("titan", "a") is None
        assert cache.get("titan", "c") == VECTOR

    def test_reading_refreshes_lru_position(self):
        cache = EmbeddingCache(max_entries=2)
        cache.put("titan", "a", VECTOR)
        cache.put("titan", "b", VECTOR)
        cache.get("titan", "a")
        cache.put("titan", "c", VECTOR)
        assert cache.get("titan", "a") == VECTOR
        assert cache.get("titan", "b") is None

    def test_empty_embedding_is_not_stored(self):
        cache = EmbeddingCache()
        cache.put("titan", "q", [])
        assert cache.get("titan", "q") is None

    def test_stats(self):
        cache = EmbeddingCache()
        cache.get("titan", "missing")
        cache.put("titan", "q", VECTOR)
        cache.get("titan", "q")
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["entries"] == 1


class TestModelIntegration:
    @pytest.fixture
    def model(self, monkeypatch):
        from models import embedding_model as em

        em.embedding_cache.clear()
        calls = []

        def fake_invoke(self, payload):
            calls.append(payload)
            return {"embedding": VECTOR, "inputTextTokenCount": 7}

        monkeypatch.setattr(em.TitanEmbeddingModel, "invoke_model", fake_invoke)
        monkeypatch.setattr(
            em.TitanEmbeddingModel, "__init__", lambda self: setattr(self, "model_id", "titan")
        )
        return em.TitanEmbeddingModel(), calls

    def test_repeat_question_skips_bedrock(self, model):
        m, calls = model
        m.generate_embedding("how many open breakdowns")
        m.generate_embedding("how many open breakdowns")
        assert len(calls) == 1

    def test_different_question_still_calls_bedrock(self, model):
        m, calls = model
        m.generate_embedding("question one")
        m.generate_embedding("question two")
        assert len(calls) == 2

    def test_cache_can_be_bypassed(self, model):
        m, calls = model
        m.generate_embedding("q")
        m.generate_embedding("q", use_cache=False)
        assert len(calls) == 2

    def test_returns_the_same_vector_either_way(self, model):
        m, _ = model
        assert m.generate_embedding("q") == m.generate_embedding("q")
