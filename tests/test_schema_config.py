"""
Schema, database and table names now come from the environment.

They cannot be bind parameters, so they are interpolated into SQL - which makes
an unvalidated value an injection vector. These tests pin both halves: the
resolution rules, and that a malformed value stops the app at import rather than
reaching the database.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.sql_safety import validate_identifier  # noqa: E402


def _resolve(**env):
    """Import config in a subprocess with `env` set, and report what it resolved."""
    code = (
        "from config import (KNOWLEDGEBASE_DATABASE_NAME, KNOWLEDGEBASE_SCHEMA_NAME, "
        "KNOWLEDGEBASE_TABLE, DATA_SCHEMA, USER_DETAILS_SCHEMA); "
        "print(KNOWLEDGEBASE_DATABASE_NAME, KNOWLEDGEBASE_SCHEMA_NAME, "
        "KNOWLEDGEBASE_TABLE, DATA_SCHEMA, USER_DETAILS_SCHEMA)"
    )
    import os

    child = dict(os.environ)
    child.update({k: str(v) for k, v in env.items()})
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=child, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


class TestTableResolution:
    def test_bare_name_is_qualified_with_the_schema(self):
        _, _, table, _, _ = _resolve(
            KNOWLEDGEBASE_SCHEMA_NAME="ai", KNOWLEDGEBASE_TABLE="examples"
        )
        assert table == "ai.examples"

    def test_qualified_name_is_used_as_is(self):
        # Existing .env files and the documented rollback
        # (KNOWLEDGEBASE_TABLE=ai.knowledge_base_examples) must keep working.
        _, _, table, _, _ = _resolve(
            KNOWLEDGEBASE_SCHEMA_NAME="other",
            KNOWLEDGEBASE_TABLE="ai.knowledge_base_examples",
        )
        assert table == "ai.knowledge_base_examples"

    def test_custom_schema_flows_through(self):
        _, schema, table, _, _ = _resolve(
            KNOWLEDGEBASE_SCHEMA_NAME="kb", KNOWLEDGEBASE_TABLE="examples"
        )
        assert (schema, table) == ("kb", "kb.examples")

    def test_all_five_are_configurable(self):
        db, schema, table, data, users = _resolve(
            KNOWLEDGEBASE_DATABASE_NAME="kbdb",
            KNOWLEDGEBASE_SCHEMA_NAME="kb",
            KNOWLEDGEBASE_TABLE="examples",
            DATA_SCHEMA="analytics",
            USER_DETAILS_SCHEMA="app",
        )
        assert [db, schema, table, data, users] == [
            "kbdb",
            "kb",
            "kb.examples",
            "analytics",
            "app",
        ]


class TestIdentifierValidation:
    """These values are interpolated into SQL, so they must be identifiers."""

    @pytest.mark.parametrize(
        "value",
        [
            "public; DROP TABLE x --",
            "pub lic",
            "ai.tbl; DELETE FROM y",
            "schema'name",
            "",
            "1starts_with_digit",
        ],
    )
    def test_malformed_values_are_rejected(self, value):
        with pytest.raises(ValueError):
            validate_identifier(value, label="schema")

    @pytest.mark.parametrize(
        "value", ["ai", "public", "ai.knowledge_base_examples", "kb_2", "_private"]
    )
    def test_legitimate_identifiers_pass(self, value):
        assert validate_identifier(value, label="schema") == value

    def test_a_malformed_value_stops_the_import(self):
        # Fail at startup, not on the first request - and certainly not by
        # reaching the database.
        import os

        child = dict(os.environ)
        child["DATA_SCHEMA"] = "public; DROP TABLE x --"
        out = subprocess.run(
            [sys.executable, "-c", "import database.db_queries"],
            cwd=ROOT,
            env=child,
            capture_output=True,
            text=True,
        )
        assert out.returncode != 0
        assert "Invalid data schema" in out.stderr


class TestContextLimits:
    """
    Few-shot sizing is the main cost/quality dial: the defaults cost ~1,785
    tokens per request. Making it configurable is what allows the trade-off to
    be measured with evals/ instead of argued about.
    """

    def _limits(self, **env):
        code = (
            "from config import KB_CONTEXT_LIMIT, CONTEXT_LIMIT, NUMBER_OF_CHAT_EXCHANGES; "
            "print(KB_CONTEXT_LIMIT, CONTEXT_LIMIT, NUMBER_OF_CHAT_EXCHANGES)"
        )
        import os

        child = dict(os.environ)
        child.update({k: str(v) for k, v in env.items()})
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=child, capture_output=True, text=True
        )
        assert out.returncode == 0, out.stderr
        return [int(x) for x in out.stdout.split()]

    def test_all_three_are_configurable(self):
        assert self._limits(
            KB_CONTEXT_LIMIT=5, CONTEXT_LIMIT=3, NUMBER_OF_CHAT_EXCHANGES=2
        ) == [5, 3, 2]

    def test_the_documented_tuning_target(self):
        # The 5/3 split from remaining_points: ~53% less few-shot per request.
        kb, ctx, _ = self._limits(KB_CONTEXT_LIMIT=5, CONTEXT_LIMIT=3)
        assert (kb, ctx) == (5, 3)

    @pytest.mark.parametrize("value", ["0", "-4"])
    def test_floored_at_one(self, value):
        # Zero examples would send an empty ##Examples:## block and a LIMIT 0
        # retrieval query, so the floor is a guard rather than politeness.
        kb, ctx, turns = self._limits(
            KB_CONTEXT_LIMIT=value, CONTEXT_LIMIT=value, NUMBER_OF_CHAT_EXCHANGES=value
        )
        assert (kb, ctx, turns) == (1, 1, 1)

    def test_context_limit_is_wired_to_the_response_examples(self):
        # It replaced a dead `if n <= 10` guard; if this import ever disappears
        # the cap silently stops applying.
        source = (ROOT / "database" / "db_queries.py").read_text()
        assert "if n <= CONTEXT_LIMIT:" in source

    def test_chat_exchanges_is_wired_to_the_classifier(self):
        source = (ROOT / "routers" / "llm_inference.py").read_text()
        assert "n=NUMBER_OF_CHAT_EXCHANGES" in source
