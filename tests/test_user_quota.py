import asyncio
from unittest.mock import AsyncMock
from database import update_user_quota, UPDATE_USER_QUOTA_USAGE


def test_update_user_quota_executes_per_model():
    mock_conn = AsyncMock()
    mock_conn.execute.return_value = "UPDATE 1"
    user_id = 123
    token_usage = {
        "anthropic.claude-3-haiku": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "amazon.titan-embed-text-v1": {
            "prompt_tokens": 20,
            "completion_tokens": 0,
            "total_tokens": 20,
        },
    }

    asyncio.run(update_user_quota(mock_conn, user_id, token_usage))

    assert mock_conn.execute.call_count == 2

    # Check first model call
    mock_conn.execute.assert_any_call(
        UPDATE_USER_QUOTA_USAGE,
        123,
        150,
        "anthropic.claude-3-haiku",
        100,
        50,
    )

    # Check second model call
    mock_conn.execute.assert_any_call(
        UPDATE_USER_QUOTA_USAGE,
        123,
        20,
        "amazon.titan-embed-text-v1",
        20,
        0,
    )


def test_update_user_quota_inserts_when_no_row_exists():
    mock_conn = AsyncMock()
    mock_conn.execute.return_value = "UPDATE 0"
    user_id = 999
    token_usage = {
        "model-b": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }

    asyncio.run(update_user_quota(mock_conn, user_id, token_usage))

    # Should call UPDATE first, then INSERT when UPDATE returns "UPDATE 0"
    assert mock_conn.execute.call_count == 2


def test_update_user_quota_skips_zero_tokens():
    mock_conn = AsyncMock()
    token_usage = {
        "model-a": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }

    asyncio.run(update_user_quota(mock_conn, 456, token_usage))

    mock_conn.execute.assert_not_called()
