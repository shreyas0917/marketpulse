"""Smoke tests against a running stack (docker compose or kind).

Endpoints come from environment variables so the same tests run against both:

- SMOKE_API_URL       (default http://localhost:18000)
- SMOKE_WORKER_URL    (default http://localhost:19100; empty to skip)
- SMOKE_PRODUCER_URL  (default http://localhost:19200; empty to skip)
- SMOKE_HOST_HEADER   (optional Host header, e.g. for an ingress)
- SMOKE_SYMBOL        (default SMOKETEST: a served symbol the producer never publishes)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

import pytest

API = os.environ.get("SMOKE_API_URL", "http://localhost:18000").rstrip("/")
WORKER = os.environ.get("SMOKE_WORKER_URL", "http://localhost:19100").rstrip("/")
PRODUCER = os.environ.get("SMOKE_PRODUCER_URL", "http://localhost:19200").rstrip("/")
HOST = os.environ.get("SMOKE_HOST_HEADER", "")
SYMBOL = os.environ.get("SMOKE_SYMBOL", "SMOKETEST")


def call(url: str, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if HOST:
        req.add_header("Host", HOST)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_api_health(path: str) -> None:
    status, _ = call(API + path)
    assert status == 200


@pytest.mark.parametrize("base", [WORKER, PRODUCER])
@pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/metrics"])
def test_background_services(base: str, path: str) -> None:
    if not base:
        pytest.skip("service URL not configured")
    status, _ = call(base + path)
    assert status == 200


def test_api_metrics_reachable() -> None:
    status, text = call(API + "/metrics")
    assert status == 200
    assert "http_requests_total" in text


def test_produced_event_appears_as_quote_within_5s() -> None:
    event_id = f"smoke-{uuid.uuid4().hex}"
    price = 1000 + (uuid.uuid4().int % 100000) / 100
    status, body = call(
        API + "/v1/events", "POST", {"event_id": event_id, "symbol": SYMBOL, "price": price}
    )
    assert status == 202, body
    deadline = time.monotonic() + 5
    last: tuple[int, str] = (0, "")
    while time.monotonic() < deadline:
        last = call(f"{API}/v1/quotes/{SYMBOL}")
        if last[0] == 200 and json.loads(last[1])["event_id"] == event_id:
            quote = json.loads(last[1])
            assert quote["price"] == price
            assert quote["stale"] is False
            return
        time.sleep(0.2)
    pytest.fail(f"event {event_id} not served within 5 s; last response {last}")
