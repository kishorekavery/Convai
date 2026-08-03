"""
Unit tests for follow-up aware intent classification.

Covers the two pieces that can be tested without a live model: building the
conversation transcript the classifier sees, and parsing/validating what it
returns.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.intent_classification_agent import parse_classification_output  # noqa: E402
from dataprocessing.user_query_processing import (  # noqa: E402
    get_last_and_current_user_query,
    get_last_n_exchanges,
    get_last_n_user_queries,
    parse_chat_turns,
)
from prompts.prompts_templates import format_classification_prompt  # noqa: E402

HISTORY = (
    "user: What are the recent work orders for plant A, "
    "ai: Here are the 50 most recent work orders for plant A, "
    "user: what about last month"
)


class TestParseChatTurns:
    def test_preserves_roles_and_order(self):
        assert parse_chat_turns(HISTORY) == [
            ("User", "What are the recent work orders for plant A"),
            ("Assistant", "Here are the 50 most recent work orders for plant A"),
            ("User", "what about last month"),
        ]

    def test_empty_history(self):
        assert parse_chat_turns("") == []
        assert parse_chat_turns(None) == []

    def test_does_not_split_on_a_value_starting_with_user(self):
        # Guards the \b + colon requirement: "user23432343" is a value, not a role.
        turns = parse_chat_turns("user: who logged this, ai: user23432343")
        assert turns == [("User", "who logged this"), ("Assistant", "user23432343")]


class TestGetLastNExchanges:
    def test_renders_a_transcript_with_assistant_replies(self):
        # The whole point: the assistant's reply is what makes "that" resolvable.
        rendered = get_last_n_exchanges(HISTORY)
        assert "User: What are the recent work orders for plant A" in rendered
        assert "Assistant: Here are the 50 most recent work orders for plant A" in rendered
        assert "User: what about last month" in rendered

    def test_empty_history_is_empty_string(self):
        assert get_last_n_exchanges("") == ""

    def test_keeps_only_the_most_recent_turns(self):
        history = ", ".join(
            f"user: question {i}, ai: answer {i}" for i in range(10)
        )
        rendered = get_last_n_exchanges(history, n=2)

        assert "question 9" in rendered
        assert "question 8" in rendered
        # Old turns must be dropped, not kept - this is the bug that made the
        # previous helper return the *first* turns instead of the last.
        assert "question 0" not in rendered

    def test_long_assistant_replies_are_clipped(self):
        history = f"user: hi, ai: {'x' * 900}"
        rendered = get_last_n_exchanges(history, max_message_chars=100)
        assert "..." in rendered
        assert len(rendered) < 300


class TestGetLastNUserQueries:
    """
    These feed chat_history into format_sql_prompt and
    format_response_to_user_prompt, so returning the wrong end of the
    conversation silently degrades both.
    """

    LONG = ", ".join(f"user: question {i}, ai: answer {i}" for i in range(6))

    def test_returns_the_most_recent_queries(self):
        assert get_last_n_user_queries(self.LONG, n=3) == [
            "question 3",
            "question 4",
            "question 5",
        ]

    def test_does_not_return_the_oldest(self):
        # The original bug: [:n] handed back questions 0-2 forever.
        assert "question 0" not in get_last_n_user_queries(self.LONG, n=3)

    def test_chronological_order_is_preserved(self):
        result = get_last_n_user_queries(self.LONG, n=3)
        assert result == sorted(result, key=lambda q: int(q.split()[-1]))

    def test_returns_all_when_fewer_than_n(self):
        assert get_last_n_user_queries("user: only one", n=3) == ["only one"]

    def test_empty_history(self):
        assert get_last_n_user_queries("") == ""

    def test_combined_query_uses_the_latest_turn(self):
        combined = get_last_and_current_user_query(self.LONG, "and for plant B?")
        assert combined.startswith("question 5")
        assert "question 0" not in combined


class TestParseClassificationOutput:
    def _output(self, **overrides):
        payload = {
            "type": "sql",
            "message": "",
            "is_followup": False,
            "resolved_query": "How many breakdowns last week?",
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_parses_all_fields(self):
        result = parse_classification_output(self._output(), "How many breakdowns last week?")
        assert result["type"] == "sql"
        assert result["action"] == "call_sql_model"
        assert result["is_followup"] is False
        assert result["resolved_query"] == "How many breakdowns last week?"

    def test_resolved_followup(self):
        result = parse_classification_output(
            self._output(
                is_followup=True,
                resolved_query="What are the work orders for plant A from last month?",
            ),
            "what about last month?",
        )
        assert result["is_followup"] is True
        assert result["resolved_query"] == "What are the work orders for plant A from last month?"

    def test_strips_markdown_fences(self):
        fenced = f"```json\n{self._output()}\n```"
        assert parse_classification_output(fenced, "x")["type"] == "sql"

    def test_missing_resolved_query_falls_back_to_raw_input(self):
        # Older prompt versions and truncated outputs omit the field; downstream
        # reads it unconditionally, so it must never come back empty.
        raw = json.dumps({"type": "sql", "message": ""})
        result = parse_classification_output(raw, "how many breakdowns?")
        assert result["resolved_query"] == "how many breakdowns?"
        assert result["is_followup"] is False

    def test_blank_resolved_query_falls_back_to_raw_input(self):
        result = parse_classification_output(
            self._output(resolved_query="   "), "how many breakdowns?"
        )
        assert result["resolved_query"] == "how many breakdowns?"

    def test_non_string_resolved_query_falls_back(self):
        result = parse_classification_output(
            self._output(resolved_query=42), "how many breakdowns?"
        )
        assert result["resolved_query"] == "how many breakdowns?"

    def test_truthy_is_followup_is_coerced_to_bool(self):
        result = parse_classification_output(self._output(is_followup="true"), "x")
        assert result["is_followup"] is True

    @pytest.mark.parametrize(
        "classification_type,expected_action",
        [
            ("sql", "call_sql_model"),
            ("greeting", "return_greeting"),
            ("rejected", "return_rejection_response"),
            ("follow_up_pagination", "follow_up_pagination"),
        ],
    )
    def test_every_type_maps_to_an_action(self, classification_type, expected_action):
        result = parse_classification_output(
            self._output(type=classification_type), "x"
        )
        assert result["action"] == expected_action

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unexpected type"):
            parse_classification_output(self._output(type="nonsense"), "x")

    def test_empty_output_raises(self):
        with pytest.raises(ValueError):
            parse_classification_output("   ", "x")

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_classification_output("not json at all", "x")


class TestClassificationPrompt:
    def test_includes_the_transcript(self):
        prompt = format_classification_prompt(
            "what about last month?", get_last_n_exchanges(HISTORY)
        )
        assert "Assistant: Here are the 50 most recent work orders for plant A" in prompt
        assert "what about last month?" in prompt

    def test_marks_a_first_message_explicitly(self):
        # Without this the model sees a blank section and can invent a referent.
        prompt = format_classification_prompt("hi", "")
        assert "no previous turns" in prompt

    def test_declares_the_new_output_fields(self):
        prompt = format_classification_prompt("hi", "")
        assert '"is_followup"' in prompt
        assert '"resolved_query"' in prompt

    def test_teaches_the_topic_switch_case(self):
        prompt = format_classification_prompt("hi", "")
        assert "topic switch" in prompt.lower()
        assert "A short message is not automatically a follow-up." in prompt
