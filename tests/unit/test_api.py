from __future__ import annotations

import asyncio
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.common.models import Quote
from app.common.queue import RedisStreamQueue
from app.common.store import QuoteWriter
from tests.unit.conftest import build_client, event, make_settings, sample


def registry_of(client: TestClient):  # type: ignore[no-untyped-def]
    return client.app.state.registry  # type: ignore[attr-defined]


def test_quote_served_from_redis_refreshes_cache(client: TestClient, writer: QuoteWriter) -> None:
    writer.write(event("AAA", 123.45, age=0.5))
    resp = client.get("/v1/quotes/aaa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAA"
    assert body["price"] == 123.45
    assert body["stale"] is False
    assert body["source"] == "store"
    assert 0.4 < body["age_seconds"] < 5
    cache = client.app.state.cache  # type: ignore[attr-defined]
    assert len(cache) == 1
    reg = registry_of(client)
    assert sample(reg, "quote_responses_total", {"source": "store"}) == 1
    assert sample(reg, "quote_store_up") == 1
    assert sample(reg, "quote_age_seconds_count") == 1


def test_redis_down_with_fresh_cache_serves_stale(
    client: TestClient, writer: QuoteWriter, server: fakeredis.FakeServer
) -> None:
    writer.write(event("AAA", 50.0, age=1))
    assert client.get("/v1/quotes/AAA").status_code == 200
    server.connected = False
    resp = client.get("/v1/quotes/AAA")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stale"] is True
    assert body["source"] == "local_cache"
    assert body["price"] == 50.0
    reg = registry_of(client)
    assert sample(reg, "quote_store_up") == 0
    assert sample(reg, "quote_responses_total", {"source": "local_cache"}) == 1


def test_redis_down_without_cache_returns_503(
    client: TestClient, server: fakeredis.FakeServer
) -> None:
    server.connected = False
    resp = client.get("/v1/quotes/AAA")
    assert resp.status_code == 503
    assert resp.json()["error"] == "QUOTE_UNAVAILABLE"
    assert resp.headers["Retry-After"] == "5"
    reg = registry_of(client)
    assert sample(reg, "quote_responses_total", {"source": "unavailable"}) == 1
    assert sample(reg, "quote_age_seconds_count") == 0


def test_redis_down_with_cache_older_than_max_age_returns_503(
    client: TestClient, writer: QuoteWriter, server: fakeredis.FakeServer
) -> None:
    writer.write(event("AAA", 50.0, age=31))
    assert client.get("/v1/quotes/AAA").status_code == 200  # cached, but already 31 s old
    server.connected = False
    resp = client.get("/v1/quotes/AAA")
    assert resp.status_code == 503
    assert resp.json()["error"] == "QUOTE_UNAVAILABLE"
    assert "Retry-After" in resp.headers


def test_redis_timeout_is_treated_as_unavailable(queue: RedisStreamQueue) -> None:
    class SlowReader:
        async def get(self, symbol: str) -> Quote | None:
            await asyncio.sleep(0.5)
            return None

        async def close(self) -> None:
            return None

    app = create_app(make_settings(), reader=SlowReader(), queue=queue)  # type: ignore[arg-type]
    with TestClient(app) as c:
        resp = c.get("/v1/quotes/AAA")
    assert resp.status_code == 503
    assert resp.json()["error"] == "QUOTE_UNAVAILABLE"


def test_readiness_ignores_redis(client: TestClient, server: fakeredis.FakeServer) -> None:
    server.connected = False
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/live").status_code == 200


def test_unknown_symbol_and_missing_quote(client: TestClient) -> None:
    resp = client.get("/v1/quotes/ZZZ")
    assert resp.status_code == 404
    assert resp.json()["error"] == "UNKNOWN_SYMBOL"
    resp = client.get("/v1/quotes/BBB")
    assert resp.status_code == 404
    assert resp.json()["error"] == "NO_QUOTE"
    # 4xx are client errors and must still be labelled with the route template
    reg = registry_of(client)
    labels = {"route": "/v1/quotes/{symbol}", "method": "GET", "status": "404"}
    assert sample(reg, "http_requests_total", labels) == 2


def test_unmatched_path_uses_bounded_label(client: TestClient) -> None:
    resp = client.get("/no/such/path")
    assert resp.status_code == 404
    assert resp.json()["error"] == "NOT_FOUND"
    reg = registry_of(client)
    assert (
        sample(reg, "http_requests_total", {"route": "unmatched", "method": "GET", "status": "404"})
        == 1
    )


def test_post_event_enqueues(client: TestClient, queue: RedisStreamQueue) -> None:
    resp = client.post("/v1/events", json={"symbol": "aaa", "price": 10.5})
    assert resp.status_code == 202
    assert resp.json()["accepted"] is True
    messages = queue.receive(10, 0)
    assert len(messages) == 1
    assert json.loads(messages[0].body)["symbol"] == "AAA"


def test_post_event_validation(client: TestClient) -> None:
    resp = client.post("/v1/events", json={"symbol": "ZZZ", "price": 1})
    assert resp.status_code == 400
    assert resp.json()["error"] == "UNKNOWN_SYMBOL"
    resp = client.post("/v1/events", json={"symbol": "AAA", "price": -1})
    assert resp.status_code == 422
    assert resp.json()["error"] == "VALIDATION_ERROR"


def test_post_event_queue_down(client: TestClient, server: fakeredis.FakeServer) -> None:
    server.connected = False
    resp = client.post("/v1/events", json={"symbol": "AAA", "price": 1})
    assert resp.status_code == 503
    assert resp.json()["error"] == "QUEUE_UNAVAILABLE"


def test_warmup_traffic_excluded_from_sli(client: TestClient, writer: QuoteWriter) -> None:
    writer.write(event("AAA"))
    resp = client.get("/v1/quotes/AAA", headers={"X-Traffic-Class": "warmup"})
    assert resp.status_code == 200
    reg = registry_of(client)
    assert (
        sample(reg, "http_requests_total", {"route": "warmup", "method": "GET", "status": "200"})
        == 1
    )
    q = {"route": "/v1/quotes/{symbol}", "method": "GET", "status": "200"}
    assert sample(reg, "http_requests_total", q) == 0
    assert sample(reg, "quote_age_seconds_count") == 0


def test_request_id_is_echoed(client: TestClient) -> None:
    resp = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"
    assert client.get("/health/live").headers["x-request-id"]


def test_unhandled_exception_returns_json_500(queue: RedisStreamQueue) -> None:
    class BrokenReader:
        async def get(self, symbol: str) -> Quote | None:
            raise RuntimeError("boom")

        async def close(self) -> None:
            return None

    app = create_app(make_settings(), reader=BrokenReader(), queue=queue)  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/v1/quotes/AAA")
    assert resp.status_code == 500
    assert resp.json()["error"] == "INTERNAL"
    labels = {"route": "/v1/quotes/{symbol}", "method": "GET", "status": "500"}
    assert sample(app.state.registry, "http_requests_total", labels) == 1


REQUIRED_API_METRICS = (
    "http_requests_total",
    "http_request_duration_seconds_bucket",
    "quote_age_seconds_bucket",
    "quote_store_up",
    "quote_responses_total",
    "fault_injection_active",
)


def test_metrics_endpoint_exposes_required_metrics_and_buckets(
    client: TestClient, writer: QuoteWriter
) -> None:
    writer.write(event("AAA"))
    client.get("/v1/quotes/AAA")
    text = client.get("/metrics").text
    for name in REQUIRED_API_METRICS:
        assert f"\n{name}" in text, name
    assert 'http_request_duration_seconds_bucket{le="0.25",route="/v1/quotes/{symbol}"}' in text
    assert 'quote_age_seconds_bucket{le="2.0"}' in text


@pytest.mark.parametrize(
    ("overrides", "fault_type"),
    [
        ({"fault_api_error_rate": 1.0}, "api_error_rate"),
        ({"fault_api_extra_latency_ms": 120}, "api_extra_latency"),
        ({"fault_readiness_fail": True}, "readiness_fail"),
    ],
)
def test_api_fault_gauge(
    server: fakeredis.FakeServer,
    queue: RedisStreamQueue,
    overrides: dict[str, object],
    fault_type: str,
) -> None:
    with build_client(server, queue, **overrides) as c:
        reg = registry_of(c)
        assert sample(reg, "fault_injection_active", {"type": fault_type}) == 1
        others = {"api_error_rate", "api_extra_latency", "readiness_fail"} - {fault_type}
        for other in others:
            assert sample(reg, "fault_injection_active", {"type": other}) == 0


def test_fault_api_error_rate_returns_500(
    server: fakeredis.FakeServer, queue: RedisStreamQueue, writer: QuoteWriter
) -> None:
    writer.write(event("AAA"))
    with build_client(server, queue, fault_api_error_rate=1.0) as c:
        resp = c.get("/v1/quotes/AAA")
    assert resp.status_code == 500
    assert resp.json()["error"] == "FAULT_INJECTED"


def test_fault_api_extra_latency_delays_quotes(
    server: fakeredis.FakeServer, queue: RedisStreamQueue, writer: QuoteWriter
) -> None:
    writer.write(event("AAA"))
    with build_client(server, queue, fault_api_extra_latency_ms=300) as c:
        assert c.get("/v1/quotes/AAA").status_code == 200
        reg = registry_of(c)
    route = {"route": "/v1/quotes/{symbol}"}
    fast = sample(reg, "http_request_duration_seconds_bucket", {**route, "le": "0.25"})
    total = sample(reg, "http_request_duration_seconds_count", route)
    assert total == 1
    assert fast == 0


def test_fault_readiness_fail(server: fakeredis.FakeServer, queue: RedisStreamQueue) -> None:
    with build_client(server, queue, fault_readiness_fail=True) as c:
        resp = c.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["error"] == "NOT_READY"
        assert c.get("/health/live").status_code == 200
