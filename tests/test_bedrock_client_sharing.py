"""
The Bedrock runtime client must be built once per process, not once per model
wrapper - a request instantiates four wrappers, and client construction is the
expensive part.

These tests stub boto3 so nothing contacts AWS.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import bedrock_client  # noqa: E402


@pytest.fixture
def counting_boto3(monkeypatch):
    """Replace boto3.client with a counter so construction can be observed."""
    calls = []

    class FakeClient:
        pass

    def fake_client(service, **kwargs):
        calls.append((service, kwargs))
        return FakeClient()

    monkeypatch.setattr(bedrock_client.boto3, "client", fake_client)
    bedrock_client.get_bedrock_runtime_client.cache_clear()
    yield calls
    bedrock_client.get_bedrock_runtime_client.cache_clear()


class TestClientIsShared:
    def test_built_once_however_many_calls(self, counting_boto3):
        for _ in range(10):
            bedrock_client.get_bedrock_runtime_client()
        assert len(counting_boto3) == 1

    def test_same_instance_returned(self, counting_boto3):
        assert (
            bedrock_client.get_bedrock_runtime_client()
            is bedrock_client.get_bedrock_runtime_client()
        )

    def test_four_wrappers_share_one_client(self, counting_boto3):
        # Mirrors one request: embedding + SQL + chat + classification.
        wrappers = [
            bedrock_client.BedrockClient("amazon.titan-embed-text-v2:0", "application/json", "*/*"),
            bedrock_client.BedrockClient("meta.llama3-3-70b", "application/json", "application/json"),
            bedrock_client.BedrockClient("meta.llama3-3-70b", "application/json", "application/json"),
            bedrock_client.BedrockClient("anthropic.claude", "application/json", "application/json"),
        ]
        assert len(counting_boto3) == 1
        assert len({id(w.client) for w in wrappers}) == 1

    def test_model_id_is_still_per_wrapper(self, counting_boto3):
        a = bedrock_client.BedrockClient("model-a", "application/json", "*/*")
        b = bedrock_client.BedrockClient("model-b", "application/json", "*/*")
        assert (a.model_id, b.model_id) == ("model-a", "model-b")
        assert a.client is b.client


class TestClientConfiguration:
    def test_targets_bedrock_runtime(self, counting_boto3):
        bedrock_client.get_bedrock_runtime_client()
        assert counting_boto3[0][0] == "bedrock-runtime"

    def test_timeouts_and_retries_are_set(self, counting_boto3):
        # Without these a hung call holds an executor thread indefinitely and a
        # ThrottlingException surfaces as a 500 rather than being retried.
        bedrock_client.get_bedrock_runtime_client()
        config = counting_boto3[0][1]["config"]
        assert config.connect_timeout > 0
        assert config.read_timeout > 0
        assert config.retries["mode"] == "adaptive"
        assert config.retries["max_attempts"] >= 1

    def test_import_does_not_construct_a_client(self, monkeypatch):
        # Importing must not require AWS credentials; the client is lazy.
        called = []
        monkeypatch.setattr(
            bedrock_client.boto3, "client", lambda *a, **k: called.append(1)
        )
        bedrock_client.get_bedrock_runtime_client.cache_clear()
        import importlib

        importlib.reload(bedrock_client)
        assert called == []
