from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

import pytest

from app.__main__ import main as cli_main
from app.common.config import Settings
from app.common.http import ServiceHttpServer
from app.common.logging import JsonFormatter, request_id_var
from app.common.metrics import QueueMetrics, QueueMetricsPoller, new_registry
from app.common.queue import RedisStreamQueue, make_queue
from app.common.queue.sqs import SqsQueue
from app.producer.main import Producer
from tests.unit.conftest import event, make_settings, sample


def test_redis_queue_stats_and_ack(queue: RedisStreamQueue) -> None:
    assert queue.stats().depth == 0
    assert queue.stats().oldest_age_seconds == 0.0
    queue.publish([event("AAA"), event("BBB")])
    time.sleep(0.05)
    stats = queue.stats()
    assert stats.depth == 2
    assert stats.oldest_age_seconds is not None
    assert stats.oldest_age_seconds > 0
    messages = queue.receive(10, 0)
    assert [m.attempts for m in messages] == [1, 1]
    for m in messages:
        queue.ack(m)
    assert queue.stats().depth == 0


def test_redis_queue_redelivery_counts_attempts(queue: RedisStreamQueue) -> None:
    queue.publish([event("AAA")])
    first = queue.receive(10, 0)[0]
    queue.retry(first)
    second = queue.receive(10, 0)
    assert len(second) == 1
    assert second[0].id == first.id
    assert second[0].attempts == 2


def test_redis_queue_respects_min_idle(sync_redis: object) -> None:
    q = RedisStreamQueue(sync_redis, consumer="c", min_idle_ms=60_000)  # type: ignore[arg-type]
    q.publish([event("AAA")])
    assert len(q.receive(10, 0)) == 1
    assert q.receive(10, 0) == []  # still pending, not idle long enough to reclaim


def test_ensure_group_idempotent(queue: RedisStreamQueue) -> None:
    queue.ensure_group()
    other = RedisStreamQueue(queue._client, consumer="other")
    other.ensure_group()  # BUSYGROUP is swallowed


def test_queue_metrics_poller(queue: RedisStreamQueue) -> None:
    registry = new_registry()
    metrics = QueueMetrics(registry)
    queue.publish([event("AAA")])
    poller = QueueMetricsPoller(metrics, queue, interval=0.01)
    poller.start()
    time.sleep(0.1)
    poller.stop()
    poller.join(1)
    assert sample(registry, "queue_depth") == 1
    assert sample(registry, "dlq_depth") == 0


def test_make_queue_selects_backend() -> None:
    assert isinstance(make_queue(Settings(queue_backend="redis")), RedisStreamQueue)
    sqs = make_queue(
        Settings(queue_backend="sqs", sqs_queue_url="u", sqs_dlq_url="d", aws_region="ap-south-1")
    )
    assert isinstance(sqs, SqsQueue)


def test_producer_tick_rate_and_burst(queue: RedisStreamQueue) -> None:
    producer = Producer(make_settings(producer_rate=10), queue, seed=1)
    assert producer.tick(1.0) == 10
    assert producer.tick(0.05) == 0  # fractional events carry over
    assert producer.tick(0.05) == 1
    producer.start_burst(multiplier=10, duration=60)
    assert sample(producer.registry, "producer_burst_active") == 0
    assert producer.tick(1.0) == 100
    assert sample(producer.registry, "producer_burst_active") == 1
    assert sample(producer.registry, "producer_target_rate") == 100
    assert sample(producer.registry, "producer_events_published_total") == 111
    with pytest.raises(ValueError, match="multiplier"):
        producer.start_burst(multiplier=0.5)


def test_producer_burst_on_start_and_poison(queue: RedisStreamQueue) -> None:
    producer = Producer(make_settings(producer_rate=1, producer_burst_on_start=True), queue)
    assert producer.current_rate() == 10
    assert producer.publish_poison(3) == {"poison_published": 3}
    assert queue.stats().depth == 3
    with pytest.raises(ValueError, match="count"):
        producer.publish_poison(0)


def test_producer_events_are_valid(queue: RedisStreamQueue) -> None:
    producer = Producer(make_settings(), queue, seed=7)
    for _ in range(50):
        e = producer.next_event()
        assert e.symbol in {"AAA", "BBB", "CCC"}
        assert e.price > 0


def test_producer_run_loop(queue: RedisStreamQueue) -> None:
    producer = Producer(make_settings(producer_rate=100), queue)
    import threading

    t = threading.Thread(target=producer.run)
    t.start()
    time.sleep(0.35)
    producer.stop()
    t.join(2)
    assert sample(producer.registry, "producer_events_published_total") > 0


def test_service_http_server_endpoints() -> None:
    registry = new_registry()
    live = {"ok": True}

    def echo(params: dict[str, str]) -> dict[str, object]:
        if "bad" in params:
            raise ValueError("bad param")
        return {"got": params}

    server = ServiceHttpServer(
        0, registry, lambda: live["ok"], post_handlers={"/echo": echo}, host="127.0.0.1"
    )
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        assert b"python_info" in urllib.request.urlopen(f"{base}/metrics").read()
        assert urllib.request.urlopen(f"{base}/health/ready").status == 200
        assert urllib.request.urlopen(f"{base}/health/live").status == 200
        live["ok"] = False
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/health/live")
        assert err.value.code == 503
        req = urllib.request.Request(f"{base}/echo?x=1", method="POST")
        assert json.loads(urllib.request.urlopen(req).read()) == {"got": {"x": "1"}}
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(f"{base}/echo?bad=1", method="POST"))
        assert err.value.code == 400
        for method in ("GET", "POST"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(urllib.request.Request(f"{base}/nope", method=method))
            assert err.value.code == 404
    finally:
        server.stop()


def test_json_formatter_fields() -> None:
    formatter = JsonFormatter("api")
    record = logging.makeLogRecord(
        {"msg": "quote_served", "levelname": "INFO", "levelno": 20, "symbol": "AAA"}
    )
    token = request_id_var.set("rid-1")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)
    assert payload["service"] == "api"
    assert payload["event"] == "quote_served"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "rid-1"
    assert payload["symbol"] == "AAA"
    assert "timestamp" in payload


def test_settings_symbol_list() -> None:
    s = Settings(symbols=" aaa, bbb ,,ccc ")
    assert s.symbol_list == ["AAA", "BBB", "CCC"]
    assert s.redis_timeout_seconds == 0.05
    assert s.producer_symbol_list == ["AAA", "BBB", "CCC"]
    assert Settings(symbols="A,B", producer_symbols="b").producer_symbol_list == ["B"]


def test_cli_usage() -> None:
    assert cli_main([]) == 2
    assert cli_main(["nope"]) == 2
