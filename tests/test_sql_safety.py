"""
Unit tests for database.sql_safety.validate_sql.

This is the gate between LLM-generated SQL and the database, so it is tested in
both directions: legitimate read-only queries must pass, and everything that
writes, chains or escapes must be refused.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.sql_safety import validate_sql  # noqa: E402

FACILITY = "'FAC-001'"


class TestAcceptsReadOnlyQueries:
    def test_plain_select(self):
        sql = f"SELECT wo_id FROM work_orders WHERE facm_code IN ({FACILITY}) LIMIT 50"
        assert validate_sql(sql) == sql

    def test_strips_trailing_semicolon(self):
        assert validate_sql("SELECT a FROM t;") == "SELECT a FROM t"

    def test_strips_markdown_fences(self):
        assert validate_sql("```sql\nSELECT a FROM t\n```") == "SELECT a FROM t"

    def test_lowercase_select(self):
        assert validate_sql("select a from t") == "select a from t"

    def test_subquery(self):
        sql = "SELECT a FROM t WHERE id IN (SELECT id FROM u WHERE x > 1)"
        assert validate_sql(sql) == sql

    def test_aggregate_with_group_by_and_having(self):
        sql = (
            "SELECT status, COUNT(*) FROM work_orders "
            "GROUP BY status HAVING COUNT(*) > 1"
        )
        assert validate_sql(sql) == sql

    def test_join(self):
        sql = "SELECT a.x FROM t a LEFT JOIN u b ON a.id = b.id"
        assert validate_sql(sql) == sql


class TestAcceptsReadOnlyCTEs:
    """The regression this change was made for - see KB row #1996."""

    def test_simple_cte(self):
        sql = "WITH recent AS (SELECT id FROM work_orders LIMIT 10) SELECT * FROM recent"
        assert validate_sql(sql) == sql

    def test_lowercase_with(self):
        sql = "with x as (select 1 as n) select n from x"
        assert validate_sql(sql) == sql

    def test_multiple_ctes(self):
        sql = (
            "WITH a AS (SELECT id FROM t), b AS (SELECT id FROM u) "
            "SELECT a.id FROM a JOIN b ON a.id = b.id"
        )
        assert validate_sql(sql) == sql

    def test_cte_shaped_like_the_knowledge_base_example(self):
        sql = (
            "WITH target_wo AS ("
            "  SELECT workorder_equipment_code FROM ai.workordermasterdetails"
            f"  WHERE workorder_id = 'WO-0000001' AND workorder_facility_code IN ({FACILITY})"
            "), related_wos AS ("
            "  SELECT w.workorder_id FROM ai.workordermasterdetails w"
            "  JOIN target_wo t ON w.workorder_equipment_code = t.workorder_equipment_code"
            ") SELECT workorder_id FROM related_wos ORDER BY workorder_id DESC LIMIT 50"
        )
        assert validate_sql(sql) == sql

    def test_column_names_containing_keywords_are_not_rejected(self):
        # workorder_createdtime contains "create"; the \b guards must not fire.
        sql = (
            "WITH x AS (SELECT workorder_createdtime, updated_at, deleted_flag FROM t) "
            "SELECT * FROM x"
        )
        assert validate_sql(sql) == sql


class TestRejectsDataModifyingCTEs:
    """A leading WITH no longer proves the statement is read-only."""

    def test_delete_inside_cte(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")

    def test_insert_inside_cte(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x")

    def test_update_inside_cte(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM x")

    def test_update_only_variant_that_the_phrase_regex_misses(self):
        # "UPDATE ONLY t SET" does not match _DANGEROUS_KEYWORDS_RE's
        # UPDATE\s+\w+\s+SET, which is why the CTE path needs its own check.
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (UPDATE ONLY t SET a = 1 RETURNING *) SELECT * FROM x")

    def test_delete_after_the_cte(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (SELECT 1) DELETE FROM t")

    def test_drop_after_the_cte(self):
        with pytest.raises(ValueError, match="read-only"):
            validate_sql("WITH x AS (SELECT 1) DROP TABLE t")


class TestRejectsNonSelectStatements:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM work_orders",
            "DROP TABLE work_orders",
            "UPDATE work_orders SET status = 'x'",
            "INSERT INTO work_orders VALUES (1)",
            "TRUNCATE TABLE work_orders",
            "ALTER TABLE work_orders ADD COLUMN x int",
            "GRANT ALL ON work_orders TO public",
            "CREATE TABLE t (a int)",
        ],
    )
    def test_write_statements_are_refused(self, sql):
        with pytest.raises(ValueError):
            validate_sql(sql)


class TestRejectsChainedAndMalformed:
    def test_stacked_statements(self):
        with pytest.raises(ValueError, match="Multiple SQL statements"):
            validate_sql("SELECT a FROM t; DROP TABLE t")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            validate_sql("")

    def test_none(self):
        with pytest.raises(ValueError):
            validate_sql(None)


    def test_only_a_semicolon(self):
        with pytest.raises(ValueError):
            validate_sql(";")


class TestRejectsInjectionPatterns:
    def test_comment_then_write(self):
        with pytest.raises(ValueError):
            validate_sql("SELECT a FROM t -- DROP TABLE t")

    def test_into_outfile(self):
        with pytest.raises(ValueError):
            validate_sql("SELECT a FROM t INTO OUTFILE '/tmp/x'")

    def test_union_against_pg_catalog(self):
        with pytest.raises(ValueError):
            validate_sql("SELECT a FROM t UNION SELECT usename FROM pg_user")


class TestAllowedTables:
    def test_permits_a_listed_table(self):
        assert validate_sql("SELECT a FROM work_orders", allowed_tables={"work_orders"})

    def test_refuses_an_unlisted_table(self):
        with pytest.raises(ValueError, match="not in the allowed tables"):
            validate_sql("SELECT a FROM secrets", allowed_tables={"work_orders"})

    def test_strips_the_schema_prefix_before_comparing(self):
        assert validate_sql("SELECT a FROM ai.work_orders", allowed_tables={"work_orders"})
