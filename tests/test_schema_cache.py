"""
Tests for the table-schema cache and the batched schema fetch that replaced the
per-table query loop in fetch_context.

The database is stubbed: a fake pool records every query it is asked to run, so
the tests can assert on how many round trips a given call actually costs.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import db_queries  # noqa: E402
from database.schema_cache import TableSchemaCache  # noqa: E402


class FakeConnection:
    def __init__(self, recorder, columns):
        self._recorder = recorder
        self._columns = columns

    async def fetch(self, query, *args):
        self._recorder.append((query, args))
        schema, wanted = args
        return [c for c in self._columns if c["table_name"] in wanted]


class FakePool:
    """Minimal async-context-manager pool that records queries."""

    def __init__(self, columns):
        self.queries = []
        self._columns = columns

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return FakeConnection(pool.queries, pool._columns)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


COLUMNS = [
    {"table_name": "work_orders", "column_name": "wo_id", "data_type": "integer"},
    {"table_name": "work_orders", "column_name": "wo_status", "data_type": "text"},
    {"table_name": "assets", "column_name": "asset_id", "data_type": "integer"},
]


@pytest.fixture
def clean_cache(monkeypatch):
    cache = TableSchemaCache(ttl_seconds=3600)
    monkeypatch.setattr(db_queries, "table_schema_cache", cache)
    return cache


class TestBatchedFetch:
    def test_many_tables_cost_one_query(self, clean_cache):
        # This is the whole point: the old code issued one query per table.
        pool = FakePool(COLUMNS)
        result = asyncio.run(
            db_queries.fetch_table_schemas("tenant_a", ["work_orders", "assets"], pool)
        )
        assert len(pool.queries) == 1
        assert set(result) == {"work_orders", "assets"}

    def test_uses_any_rather_than_one_query_per_table(self, clean_cache):
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders", "assets"], pool))
        query, args = pool.queries[0]
        assert "ANY($2)" in query
        assert args[1] == ["work_orders", "assets"]

    def test_columns_are_ordered_by_declaration(self, clean_cache):
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders"], pool))
        assert "ORDER BY table_name, ordinal_position" in pool.queries[0][0]

    def test_formats_each_table_separately(self, clean_cache):
        pool = FakePool(COLUMNS)
        result = asyncio.run(
            db_queries.fetch_table_schemas("tenant_a", ["work_orders", "assets"], pool)
        )
        assert "wo_id: integer" in result["work_orders"]
        assert "wo_status: text" in result["work_orders"]
        assert "wo_id" not in result["assets"]

    def test_empty_input_costs_nothing(self, clean_cache):
        pool = FakePool(COLUMNS)
        assert asyncio.run(db_queries.fetch_table_schemas("tenant_a", [], pool)) == {}
        assert pool.queries == []

    def test_unknown_table_yields_empty_schema(self, clean_cache):
        pool = FakePool(COLUMNS)
        result = asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["nope"], pool))
        assert result["nope"] == ""


class TestCaching:
    def test_second_call_costs_zero_queries(self, clean_cache):
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders"], pool))
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders"], pool))
        assert len(pool.queries) == 1

    def test_only_missing_tables_are_fetched(self, clean_cache):
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders"], pool))
        asyncio.run(
            db_queries.fetch_table_schemas("tenant_a", ["work_orders", "assets"], pool)
        )
        assert len(pool.queries) == 2
        # The second query asks only for the table it did not already have.
        assert pool.queries[1][1][1] == ["assets"]

    def test_tenants_do_not_share_cached_schemas(self, clean_cache):
        # The same table name can have a different shape in each tenant database.
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["work_orders"], pool))
        asyncio.run(db_queries.fetch_table_schemas("tenant_b", ["work_orders"], pool))
        assert len(pool.queries) == 2

    def test_negative_result_is_cached_too(self, clean_cache):
        pool = FakePool(COLUMNS)
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["nope"], pool))
        asyncio.run(db_queries.fetch_table_schemas("tenant_a", ["nope"], pool))
        assert len(pool.queries) == 1


class TestCacheSemantics:
    def test_expired_entry_is_dropped(self):
        cache = TableSchemaCache(ttl_seconds=0)
        cache.put("db", "t", "schema")
        assert cache.get("db", "t") is None

    def test_invalidate_one_table(self):
        cache = TableSchemaCache()
        cache.put("db", "a", "A")
        cache.put("db", "b", "B")
        cache.invalidate("db", "a")
        assert cache.get("db", "a") is None
        assert cache.get("db", "b") == "B"

    def test_invalidate_whole_database(self):
        cache = TableSchemaCache()
        cache.put("db1", "a", "A")
        cache.put("db2", "a", "A")
        cache.invalidate("db1")
        assert cache.get("db1", "a") is None
        assert cache.get("db2", "a") == "A"

    def test_stats_track_hits_and_misses(self):
        cache = TableSchemaCache()
        cache.get("db", "missing")
        cache.put("db", "t", "S")
        cache.get("db", "t")
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
