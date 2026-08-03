"""
The AI-generated SQL path must run inside a read-only transaction with a
statement timeout.

Rationale: the SQL comes from a language model and sql_safety.py screens it with
regexes, which cannot reliably parse SQL. A dropped join condition produces a
cartesian product that LIMIT does not bound - the prompt mandates ORDER BY, so
Postgres must produce and sort every row before taking 50. Without a timeout the
connection is held until it completes, and DB_MAX_CONN such queries exhaust a
tenant's pool.

asyncpg is stubbed, so these assert on the statements actually issued.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from asyncpg import exceptions
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import AI_SQL_COUNT_TIMEOUT_MS, AI_SQL_STATEMENT_TIMEOUT_MS  # noqa: E402
from database import db_queries  # noqa: E402


class FakeTransaction:
    def __init__(self, recorder, readonly):
        self._recorder = recorder
        self.readonly = readonly

    async def __aenter__(self):
        self._recorder.append(("BEGIN", {"readonly": self.readonly}))
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, recorder, fetch_error=None, rows=None):
        self._recorder = recorder
        self._fetch_error = fetch_error
        self._rows = rows if rows is not None else []

    def transaction(self, **kwargs):
        return FakeTransaction(self._recorder, kwargs.get("readonly"))

    async def execute(self, statement, *args):
        self._recorder.append(("EXECUTE", statement))

    async def fetch(self, sql, *args):
        self._recorder.append(("FETCH", sql))
        if self._fetch_error:
            raise self._fetch_error
        return self._rows

    async def fetchval(self, sql, *args):
        self._recorder.append(("FETCHVAL", sql))
        if self._fetch_error:
            raise self._fetch_error
        return 3412


class FakePool:
    def __init__(self, fetch_error=None, rows=None):
        self.log = []
        self._fetch_error = fetch_error
        self._rows = rows

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return FakeConnection(pool.log, pool._fetch_error, pool._rows)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def statements(pool):
    return [s for kind, s in pool.log if kind == "EXECUTE"]


class TestReadOnlyTransaction:
    def test_query_runs_in_a_read_only_transaction(self):
        pool = FakePool(rows=[])
        asyncio.run(db_queries.execute_ai_generated_sql("SELECT 1", pool))
        begins = [meta for kind, meta in pool.log if kind == "BEGIN"]
        assert begins and begins[0]["readonly"] is True

    def test_a_write_rejected_by_postgres_becomes_403(self):
        # The regex validator missed it; the database caught it.
        pool = FakePool(fetch_error=exceptions.ReadOnlySQLTransactionError("read-only"))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(db_queries.execute_ai_generated_sql("DELETE FROM t", pool))
        assert exc.value.status_code == 403


class TestStatementTimeout:
    def test_timeout_is_set_before_the_query(self):
        pool = FakePool(rows=[])
        asyncio.run(db_queries.execute_ai_generated_sql("SELECT 1", pool))
        assert any(
            f"SET LOCAL statement_timeout = {AI_SQL_STATEMENT_TIMEOUT_MS}" in s
            for s in statements(pool)
        )

    def test_cancelled_query_becomes_504_not_500(self):
        # The user gets actionable guidance instead of a hung request.
        pool = FakePool(fetch_error=exceptions.QueryCanceledError("canceled"))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(db_queries.execute_ai_generated_sql("SELECT 1", pool))
        assert exc.value.status_code == 504
        assert "narrow" in exc.value.detail.lower()

    def test_count_query_gets_a_tighter_budget(self):
        # Its LIMIT is stripped, so it scans everything by design.
        pool = FakePool()
        asyncio.run(db_queries.execute_count_query("SELECT COUNT(*) FROM t", pool))
        assert any(
            f"SET LOCAL statement_timeout = {AI_SQL_COUNT_TIMEOUT_MS}" in s
            for s in statements(pool)
        )

    def test_count_budget_is_tighter_than_the_query_budget(self):
        assert AI_SQL_COUNT_TIMEOUT_MS < AI_SQL_STATEMENT_TIMEOUT_MS


class TestSearchPathIsTransactionScoped:
    def test_query_path_uses_set_local(self):
        # A plain SET persists on the pooled connection and leaks into whichever
        # query borrows it next.
        pool = FakePool(rows=[])
        asyncio.run(db_queries.execute_ai_generated_sql("SELECT 1", pool))
        search_path = [s for s in statements(pool) if "search_path" in s]
        assert search_path and all("SET LOCAL" in s for s in search_path)

    def test_count_path_uses_set_local(self):
        pool = FakePool()
        asyncio.run(db_queries.execute_count_query("SELECT COUNT(*) FROM t", pool))
        search_path = [s for s in statements(pool) if "search_path" in s]
        assert search_path and all("SET LOCAL" in s for s in search_path)

    def test_user_details_path_uses_set_local(self):
        pool = FakePool(rows=[])
        asyncio.run(db_queries.fetch_user_details("1278", pool))
        search_path = [s for s in statements(pool) if "search_path" in s]
        assert search_path and all("SET LOCAL" in s for s in search_path)

    def test_no_plain_set_search_path_remains_on_the_ai_path(self):
        pool = FakePool(rows=[])
        asyncio.run(db_queries.execute_ai_generated_sql("SELECT 1", pool))
        assert not any(
            s.strip().upper().startswith("SET SEARCH_PATH") for s in statements(pool)
        )
