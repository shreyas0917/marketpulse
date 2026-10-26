from __future__ import annotations

import time
from collections.abc import Iterator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.api.main import create_app
from app.common.config import Settings
from app.common.metrics import new_registry
from app.common.models import MarketEvent
from app.common.queue import RedisStreamQueue
from app.common.store import QuoteReader, QuoteWriter

SYMBOLS = "AAA,BBB,CCC"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"symbols": SYMBOLS, "retry_min_idle_ms": 0}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def sync_redis(server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=server)


@pytest.fixture
def queue(sync_redis: fakeredis.FakeRedis) -> RedisStreamQueue:
    return RedisStreamQueue(sync_redis, consumer="test", min_idle_ms=0)


@pytest.fixture
def writer(sync_redis: fakeredis.FakeRedis) -> QuoteWriter:
    return QuoteWriter(sync_redis, dedup_ttl_seconds=60)


@pytest.fixture
def registry() -> CollectorRegistry:
    return new_registry()


def build_client(
    server: fakeredis.FakeServer, queue: RedisStreamQueue, **overrides: object
) -> TestClient:
    reader = QuoteReader(fakeredis.FakeAsyncRedis(server=server))
    app = create_app(make_settings(**overrides), reader=reader, queue=queue)
    return TestClient(app)


@pytest.fixture
def client(server: fakeredis.FakeServer, queue: RedisStreamQueue) -> Iterator[TestClient]:
    with build_client(server, queue) as c:
        yield c


def event(symbol: str = "AAA", price: float = 100.0, age: float = 0.0, **kw: object) -> MarketEvent:
    return MarketEvent(symbol=symbol, price=price, event_ts=time.time() - age, **kw)  # type: ignore[arg-type]


def sample(registry: CollectorRegistry, name: str, labels: dict[str, str] | None = None) -> float:
    value = registry.get_sample_value(name, labels or {})
    return 0.0 if value is None else value
