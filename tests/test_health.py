"""
Liveness and readiness must answer different questions.

The gap this closes: /health returned 200 unconditionally, so during the
hour-long database outage a load balancer would have kept routing traffic to an
instance that could not serve a single request.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


@pytest.fixture
def client():
    # Bypass lifespan: it flushes traces and closes pools, none of which these
    # tests need.
    return TestClient(main.app)


class TestLiveness:
    def test_health_is_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_health_does_not_touch_the_database(self, client, monkeypatch):
        # Docker's HEALTHCHECK uses this. Restarting the container because
        # Postgres is down would be wrong - the process is healthy.
        called = []

        async def boom(*a, **k):
            called.append(1)
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(main, "_check_knowledgebase", boom)
        assert client.get("/health").status_code == 200
        assert called == []


class TestReadiness:
    def test_ready_when_the_database_answers(self, client, monkeypatch):
        async def ok():
            return {"ok": True, "latency_ms": 4}

        monkeypatch.setattr(main, "_check_knowledgebase", ok)
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"
        assert r.json()["checks"]["knowledgebase_database"]["ok"] is True

    def test_503_when_the_database_is_unreachable(self, client, monkeypatch):
        # The whole point: the balancer must take this instance out of rotation.
        async def down():
            return {"ok": False, "error": "ConnectionRefusedError", "latency_ms": 12}

        monkeypatch.setattr(main, "_check_knowledgebase", down)
        r = client.get("/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not ready"

    def test_failure_reason_is_reported(self, client, monkeypatch):
        async def down():
            return {"ok": False, "error": "TimeoutError: nope", "latency_ms": 3000}

        monkeypatch.setattr(main, "_check_knowledgebase", down)
        body = client.get("/ready").json()
        assert "TimeoutError" in body["checks"]["knowledgebase_database"]["error"]


class TestKnowledgebaseProbe:
    def test_reports_ok_and_latency(self, monkeypatch):
        import database

        class FakePool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self):
                        class _Conn:
                            async def fetchval(self, *a):
                                return 1

                        return _Conn()

                    async def __aexit__(self, *e):
                        return False

                return _Ctx()

        async def fake_get_pool(name):
            return FakePool()

        monkeypatch.setattr(database, "get_pool", fake_get_pool)
        result = asyncio.run(main._check_knowledgebase())
        assert result["ok"] is True
        assert "latency_ms" in result

    def test_a_hang_is_bounded_by_the_timeout(self, monkeypatch):
        # A probe that hangs is as bad as one that fails: the balancer keeps
        # sending traffic while it waits.
        import config
        import database

        async def hanging_pool(name):
            await asyncio.sleep(30)

        monkeypatch.setattr(database, "get_pool", hanging_pool)
        monkeypatch.setattr(config, "READINESS_TIMEOUT_SECONDS", 0.2)

        async def timed():
            loop = asyncio.get_running_loop()
            start = loop.time()
            result = await main._check_knowledgebase()
            return result, loop.time() - start

        result, elapsed = asyncio.run(timed())
        assert result["ok"] is False
        assert "timed out" in result["error"]
        assert elapsed < 5

    def test_connection_failure_is_caught_not_raised(self, monkeypatch):
        import database

        async def refuses(name):
            raise ConnectionRefusedError("no route to host")

        monkeypatch.setattr(database, "get_pool", refuses)
        result = asyncio.run(main._check_knowledgebase())
        assert result["ok"] is False
        assert "ConnectionRefusedError" in result["error"]
