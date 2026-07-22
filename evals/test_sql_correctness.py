"""
Offline SQL-groundedness regression suite: for each golden-dataset case,
generates the real SQL through the app's own prompt builder + SQL model, then
deterministically checks every referenced table/column exists in the schema
context it was given (or that it correctly refused when it should have).

Makes real Bedrock calls - excluded from the default `pytest` run (see
[tool.pytest.ini_options] in pyproject.toml).
Run with: pytest -m eval -k sql_correctness -v
"""

import pytest

from config import SQL_MODEL_ID
from database import format_sql_query
from models import ChatModel
from prompts import format_sql_prompt

from evals.dataset import load_cases
from evals.sql_schema_adherence import check_schema_adherence

pytestmark = pytest.mark.eval

_CASES = load_cases(case_type="sql_generation")

# Mirrors the exact check in agents/sql_agent.py - the production code treats
# only these two literal strings as "model declined to answer".
_REJECTION_SQL_VARIANTS = {
    "SELECT 'User request cannot be fulfilled.';",
    "SELECT 'User request cannot be fulfilled.'",
}


@pytest.fixture(scope="module")
def sql_model():
    return ChatModel(model_id=SQL_MODEL_ID)


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_sql_generation(case, sql_model):
    prompt = format_sql_prompt(
        user_input=case["user_input"],
        user_details=case["user_details"],
        facm_code=case["facm_code"],
        table_schema=case["table_schema"],
        context_for_sql_generation=case["context_for_sql_generation"],
        chat_history=case.get("chat_history", ""),
    )

    raw_sql = sql_model.generate_response(prompt)
    assert raw_sql and raw_sql.strip(), f"{case['id']}: model returned no SQL"

    sql = format_sql_query(raw_sql, case["facm_code"])

    if case["expect"].get("rejected"):
        assert sql in _REJECTION_SQL_VARIANTS, (
            f"{case['id']}: expected the SQL-generation model to refuse "
            f"(schema/request mismatch), but it produced: {sql}"
        )
        return

    result = check_schema_adherence(sql, case["table_schema"])
    assert result["adherent"], (
        f"{case['id']}: generated SQL references unknown identifiers\n"
        f"SQL: {sql}\n"
        f"Unknown tables: {result['unknown_tables']}\n"
        f"Unknown columns: {result['unknown_columns']}"
    )
