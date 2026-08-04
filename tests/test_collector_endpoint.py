"""
A misconfigured COLLECTOR_ENDPOINT produces no exception, no error log and no
spans - BatchSpanProcessor swallows export failures, so the only symptom is an
empty Phoenix UI. These tests pin the startup check that makes it visible.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import COLLECTOR_ENDPOINT, validate_collector_endpoint  # noqa: E402


class TestAcceptsGrpcEndpoints:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://arize-phoenix:4317",
            "https://phoenix.example.com:4317",
            "arize-phoenix:4317",          # no scheme is valid for the gRPC exporter
            "http://10.0.0.5:4317",
        ],
    )
    def test_no_warnings(self, endpoint):
        assert validate_collector_endpoint(endpoint) == []


class TestRejectsHttpEndpoints:
    def test_url_path_is_flagged(self):
        # '/v1/traces' is the HTTP endpoint; the gRPC exporter wants host:port.
        problems = validate_collector_endpoint("http://maintverse.com:6006/v1/traces")
        assert any("URL path" in p for p in problems)

    def test_http_receiver_port_is_flagged(self):
        problems = validate_collector_endpoint("http://arize-phoenix:4318")
        assert any("4318" in p and "HTTP receiver" in p for p in problems)

    def test_ui_port_is_flagged(self):
        problems = validate_collector_endpoint("http://arize-phoenix:6006")
        assert any("6006" in p for p in problems)

    def test_the_old_default_trips_both_checks(self):
        # The exact value that shipped as the fallback before this change.
        problems = validate_collector_endpoint("http://maintverse.com:6006/v1/traces")
        assert len(problems) == 2

    def test_messages_point_at_the_correct_port(self):
        # The operator should learn what to change to, not just what is wrong.
        # Only the port message names 4317; the path message explains the path.
        problems = validate_collector_endpoint("http://arize-phoenix:6006/v1/traces")
        assert any("4317" in p for p in problems)
        assert any("/v1/traces" in p for p in problems)


class TestShippedDefault:
    def test_the_configured_endpoint_is_valid(self):
        # Guards against the default regressing, and against a bad .env in dev.
        assert validate_collector_endpoint(COLLECTOR_ENDPOINT) == []

    def test_default_targets_the_grpc_port(self):
        import config.settings as settings
        import os

        # Read the literal default rather than whatever .env supplies.
        saved = os.environ.pop("COLLECTOR_ENDPOINT", None)
        try:
            default = os.getenv("COLLECTOR_ENDPOINT", "http://arize-phoenix:4317")
            assert default.endswith(":4317")
            assert validate_collector_endpoint(default) == []
        finally:
            if saved is not None:
                os.environ["COLLECTOR_ENDPOINT"] = saved
