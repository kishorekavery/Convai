"""
Offline groundedness regression suite: for each golden-dataset case, generates
the real final-response answer through the app's own prompt builder + chat
model, then judges it against the exact data context it was given.

Makes real Bedrock calls (generation model + judge model) - excluded from the
default `pytest` run (see [tool.pytest.ini_options] in pyproject.toml).
Run with: pytest -m eval -k groundedness -v
"""

import pytest

from config import CHAT_MODEL_ID
from models import ChatModel
from prompts import format_response_to_user_prompt

from evals.dataset import load_cases
from evals.judge import GroundednessJudge

pytestmark = pytest.mark.eval

_CASES = load_cases(case_type="answer_groundedness")


@pytest.fixture(scope="module")
def chat_model():
    return ChatModel(model_id=CHAT_MODEL_ID)


@pytest.fixture(scope="module")
def judge():
    return GroundednessJudge()


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_answer_is_grounded(case, chat_model, judge):
    prompt = format_response_to_user_prompt(
        case["user_input"],
        case["context_for_user_response"],
        case["table_rows"],
        chat_history=case.get("chat_history", ""),
    )

    answer = chat_model.generate_response(prompt)
    assert answer and answer.strip(), f"{case['id']}: model returned an empty answer"

    verdict = judge.judge(case["user_input"], case["table_rows"], answer)

    expected_label = case["expect"]["label"]
    assert verdict["label"] == expected_label, (
        f"{case['id']}: expected label={expected_label!r}, got {verdict['label']!r}\n"
        f"Answer: {answer}\n"
        f"Unsupported claims: {verdict.get('unsupported_claims')}\n"
        f"Rationale: {verdict.get('rationale')}"
    )
