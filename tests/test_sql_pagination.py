"""Unit tests for deterministic OFFSET pagination and the last-query cache."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.sql_pagination import (  # noqa: E402
    apply_offset,
    extract_limit,
    extract_offset,
    next_page_sql,
)
from routers.query_cache import CachedQuery, LastQueryCache  # noqa: E402
from prompts.prompts_templates import format_response_to_user_prompt  # noqa: E402

BASE_SQL = (
    "SELECT wo_id, wo_status, wo_created_date FROM work_orders "
    "WHERE facm_code IN ('F1') ORDER BY wo_created_date DESC LIMIT 50"
)


class TestExtractLimitOffset:
    def test_reads_limit(self):
        assert extract_limit(BASE_SQL) == 50

    def test_missing_limit_is_none(self):
        assert extract_limit("SELECT COUNT(*) FROM work_orders") is None

    def test_missing_offset_is_zero(self):
        assert extract_offset(BASE_SQL) == 0

    def test_reads_offset(self):
        assert extract_offset(f"{BASE_SQL} OFFSET 100") == 100

    def test_case_insensitive(self):
        assert extract_limit("select a from t limit 25") == 25
        assert extract_offset("select a from t limit 25 offset 75") == 75

    def test_outer_clause_wins_over_subquery(self):
        # The inner LIMIT 10 must not shadow the outer LIMIT 50 that actually
        # controls the page the user sees.
        sql = "SELECT a FROM (SELECT a FROM t ORDER BY a LIMIT 10) s LIMIT 50"
        assert extract_limit(sql) == 50


class TestApplyOffset:
    def test_appends_when_absent(self):
        assert apply_offset(BASE_SQL, 50) == f"{BASE_SQL} OFFSET 50"

    def test_replaces_when_present(self):
        assert apply_offset(f"{BASE_SQL} OFFSET 50", 100) == f"{BASE_SQL} OFFSET 100"

    def test_replacing_does_not_duplicate_the_clause(self):
        result = apply_offset(f"{BASE_SQL} OFFSET 50", 100)
        assert result.upper().count("OFFSET") == 1

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            apply_offset(BASE_SQL, -1)


class TestNextPageSql:
    def test_first_follow_up_advances_by_page_size(self):
        sql, offset, page_size = next_page_sql(BASE_SQL, current_offset=0, page_size=50)
        assert offset == 50
        assert page_size == 50
        assert sql.endswith("OFFSET 50")

    def test_successive_pages_keep_advancing(self):
        sql, offset, _ = next_page_sql(BASE_SQL, 0, 50)
        sql, offset, _ = next_page_sql(sql, offset, 50)
        assert offset == 100
        assert sql.endswith("OFFSET 100")
        assert sql.upper().count("OFFSET") == 1

    def test_falls_back_to_the_querys_own_limit(self):
        _, offset, page_size = next_page_sql(BASE_SQL, 0, page_size=None)
        assert page_size == 50
        assert offset == 50

    def test_unpaginatable_aggregate_returns_none(self):
        assert next_page_sql("SELECT COUNT(*) FROM work_orders", 0, None) is None

    def test_empty_sql_returns_none(self):
        assert next_page_sql("", 0, 50) is None


class TestLastQueryCache:
    def _entry(self, sql=BASE_SQL, offset=0):
        return CachedQuery(
            sql_template=sql,
            offset=offset,
            page_size=50,
            table_schema="schema",
            context_for_user_response="examples",
            original_user_input="list breakdown work orders",
            created_at=time.time(),
        )

    def test_round_trip(self):
        cache = LastQueryCache()
        cache.put("tenant_a", "1278", self._entry())
        assert cache.get("tenant_a", "1278").sql_template == BASE_SQL

    def test_miss_returns_none(self):
        assert LastQueryCache().get("tenant_a", "1278") is None

    def test_same_user_id_in_different_tenants_is_isolated(self):
        # The bug this guards: user ids are unique per client database, so a
        # user_id-only key let one tenant page another tenant's result set.
        cache = LastQueryCache()
        cache.put("tenant_a", "1278", self._entry(sql="SELECT a FROM ta LIMIT 50"))
        cache.put("tenant_b", "1278", self._entry(sql="SELECT b FROM tb LIMIT 50"))

        assert cache.get("tenant_a", "1278").sql_template == "SELECT a FROM ta LIMIT 50"
        assert cache.get("tenant_b", "1278").sql_template == "SELECT b FROM tb LIMIT 50"

    def test_entry_expires_after_ttl(self):
        cache = LastQueryCache(ttl_seconds=0)
        cache.put("tenant_a", "1278", self._entry())
        assert cache.get("tenant_a", "1278") is None

    def test_evicts_oldest_when_full(self):
        cache = LastQueryCache(max_entries=2)
        cache.put("t", "1", self._entry())
        cache.put("t", "2", self._entry())
        cache.put("t", "3", self._entry())

        assert cache.get("t", "1") is None
        assert cache.get("t", "2") is not None
        assert cache.get("t", "3") is not None

    def test_read_refreshes_lru_position(self):
        cache = LastQueryCache(max_entries=2)
        cache.put("t", "1", self._entry())
        cache.put("t", "2", self._entry())
        cache.get("t", "1")  # "1" is now the most recently used
        cache.put("t", "3", self._entry())

        assert cache.get("t", "1") is not None
        assert cache.get("t", "2") is None

    def test_key_separator_cannot_be_forged(self):
        cache = LastQueryCache()
        cache.put("tenant", "a:1", self._entry(sql="SELECT 1 LIMIT 50"))
        assert cache.get("tenant:a", "1") is None


class TestResponsePromptPaginationContext:
    def test_normal_answer_has_no_continuation_section(self):
        prompt = format_response_to_user_prompt(
            user_input="list breakdown work orders",
            table_rows="wo_id  wo_status",
        )
        assert "Continuation Context" not in prompt
        assert "Continuation Instructions" not in prompt

    def test_follow_up_carries_the_original_question(self):
        # The whole point: "more" says nothing, so the original question and the
        # record range have to reach the model some other way.
        prompt = format_response_to_user_prompt(
            user_input="more",
            table_rows="wo_id  wo_status",
            pagination_context=(
                'The user\'s original question was: "list breakdown work orders". '
                "The fetched data below is records 51-100 of 3412 matching records in total."
            ),
        )
        assert "Continuation Context" in prompt
        assert "list breakdown work orders" in prompt
        assert "records 51-100 of 3412" in prompt

    def test_continuation_instructions_appear_with_the_context(self):
        prompt = format_response_to_user_prompt(
            user_input="next 50",
            table_rows="rows",
            pagination_context="records 51-100 of 3412",
        )
        assert "Continuation Instructions" in prompt
        assert "Answer the ORIGINAL question" in prompt
        assert "never estimate them" in prompt
