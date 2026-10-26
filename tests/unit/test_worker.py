from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import fakeredis
import pytest
from moto import mock_aws

from app.common.queue import RedisStreamQueue, SqsQueue
from app.common.store import QuoteWriter, WriteResult
from app.worker.main import Worker
from tests.unit.conftest import event, make_settings, sample


def make_worker(queue: Any, writer: QuoteWriter, **overrides: object) -> Worker:
    return Worker(make_settings(**overrides), queue, writer)


def drain(worker: Worker, rounds: int = 10) -> None:
    for _ in range(rounds):
        worker.run_once()


def test_valid_event_written_and_acked(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    queue.publish([event("AAA", 101.0)])
    assert worker.run_once() == 1
    quote = writer.get("AAA")
    assert quote is not None
    assert quote.price == 101.0
    assert sample(worker.registry, "events_processed_total", {"result": "ok"}) == 1
    assert sample(worker.registry, "ingestion_lag_seconds_count") == 1
    assert queue.stats().depth == 0


def test_duplicate_event_id_processed_once(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    first = event("AAA", 100.0, event_id="dup-1")
    dup = first.model_copy(update={"price": 999.0, "event_ts": first.event_ts + 1})
    assert writer.write(first) is WriteResult.WRITTEN
    queue.publish([dup])
    worker.run_once()
    quote = writer.get("AAA")
    assert quote is not None
    assert quote.price == 100.0  # the duplicate was not applied
    assert sample(worker.registry, "ingestion_lag_seconds_count") == 0
    assert sample(worker.registry, "events_processed_total", {"result": "ok"}) == 1


def test_older_event_does_not_overwrite_newer(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    newer = event("AAA", 200.0, age=1)
    older = event("AAA", 150.0, age=5)
    queue.publish([newer, older])
    worker.run_once()
    quote = writer.get("AAA")
    assert quote is not None
    assert quote.price == 200.0
    assert quote.event_id == newer.event_id
    assert writer.write(event("AAA", 300.0, age=10)) is WriteResult.OLDER


def test_unknown_symbol_is_retried(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    queue.publish([event("ZZZ")])
    worker.run_once()
    assert sample(worker.registry, "events_processed_total", {"result": "retried"}) == 1
    assert queue.stats().depth == 1


def test_poison_dead_lettered_after_five_attempts_redis(
    queue: RedisStreamQueue, writer: QuoteWriter, sync_redis: fakeredis.FakeRedis
) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    queue.publish_raw(["{not json"])
    drain(worker)
    reg = worker.registry
    assert sample(reg, "events_processed_total", {"result": "retried"}) == 4
    assert sample(reg, "events_processed_total", {"result": "dead_lettered"}) == 1
    stats = queue.stats()
    assert stats.depth == 0
    assert stats.dlq_depth == 1
    entry = sync_redis.xrange("events-dlq")[0][1]
    assert entry[b"attempts"] == b"5"
    assert b"JSONDecodeError" in entry[b"reason"]
    assert entry[b"data"] == b"{not json"


@pytest.fixture
def sqs_queue() -> Iterator[SqsQueue]:
    with mock_aws():
        client = boto3.client("sqs", region_name="ap-south-1")
        main = client.create_queue(QueueName="marketpulse-dev-events")["QueueUrl"]
        dlq = client.create_queue(QueueName="marketpulse-dev-events-dlq")["QueueUrl"]
        yield SqsQueue(client, main, dlq, backoff_base_seconds=0)


def test_poison_dead_lettered_after_five_attempts_sqs(
    sqs_queue: SqsQueue, writer: QuoteWriter
) -> None:
    worker = make_worker(sqs_queue, writer, worker_block_ms=0, queue_backend="sqs")
    sqs_queue.publish_raw(["{not json"])
    drain(worker)
    reg = worker.registry
    assert sample(reg, "events_processed_total", {"result": "retried"}) == 4
    assert sample(reg, "events_processed_total", {"result": "dead_lettered"}) == 1
    stats = sqs_queue.stats()
    assert stats.depth == 0
    assert stats.dlq_depth == 1
    assert stats.oldest_age_seconds is None
    client = sqs_queue._client
    msgs = client.receive_message(
        QueueUrl=sqs_queue.dlq_url, MessageAttributeNames=["All"], MaxNumberOfMessages=1
    )["Messages"]
    assert msgs[0]["Body"] == "{not json"
    attrs = msgs[0]["MessageAttributes"]
    assert attrs["attempts"]["StringValue"] == "5"
    assert "JSONDecodeError" in attrs["failure_reason"]["StringValue"]


def test_sqs_valid_event_written(sqs_queue: SqsQueue, writer: QuoteWriter) -> None:
    worker = make_worker(sqs_queue, writer, worker_block_ms=0)
    sqs_queue.publish([event("BBB", 42.0) for _ in range(12)])  # spans two send batches
    drain(worker, 3)
    assert sample(worker.registry, "events_processed_total", {"result": "ok"}) == 12
    assert sqs_queue.stats().depth == 0


def test_sqs_retry_backoff_grows(sqs_queue: SqsQueue) -> None:
    calls: list[int] = []

    class Spy:
        def change_message_visibility(self, **kw: Any) -> None:
            calls.append(kw["VisibilityTimeout"])

    from app.common.queue.base import Message

    spy_queue = SqsQueue(Spy(), "u", "d", backoff_base_seconds=2)  # type: ignore[arg-type]
    for attempts in (1, 2, 3, 20):
        spy_queue.retry(Message(id="m", body="", attempts=attempts, receipt="r"))
    assert calls == [2, 4, 8, 900]


def test_fault_worker_fail_rate(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0, fault_worker_fail_rate=1.0)
    assert sample(worker.registry, "fault_injection_active", {"type": "worker_fail_rate"}) == 1
    assert sample(worker.registry, "fault_injection_active", {"type": "worker_extra_latency"}) == 0
    queue.publish([event("AAA")])
    worker.run_once()
    assert sample(worker.registry, "events_processed_total", {"result": "retried"}) == 1
    assert writer.get("AAA") is None


def test_fault_worker_extra_latency(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0, fault_worker_extra_latency_ms=1100)
    assert sample(worker.registry, "fault_injection_active", {"type": "worker_extra_latency"}) == 1
    queue.publish([event("AAA")])
    worker.run_once()
    lag_fast = sample(worker.registry, "ingestion_lag_seconds_bucket", {"le": "1.0"})
    assert sample(worker.registry, "ingestion_lag_seconds_count") == 1
    assert lag_fast == 0  # the injected delay pushed the event past the 1 s SLO boundary


def test_worker_metrics_exposed_with_buckets(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    from prometheus_client import generate_latest

    worker = make_worker(queue, writer, worker_block_ms=0)
    worker.queue_metrics.refresh(queue)
    text = generate_latest(worker.registry).decode()
    for name in (
        "events_processed_total",
        "ingestion_lag_seconds_bucket",
        "queue_depth",
        "dlq_depth",
        "queue_oldest_event_age_seconds",
        "fault_injection_active",
    ):
        assert f"\n{name}" in text, name
    assert 'ingestion_lag_seconds_bucket{le="1.0"}' in text
    for result in ("ok", "retried", "dead_lettered"):
        assert f'events_processed_total{{result="{result}"}}' in text


def test_run_loop_stops_and_heartbeat(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0, worker_heartbeat_timeout_seconds=5)
    calls = {"n": 0}
    original = worker.run_once

    def once() -> int:
        calls["n"] += 1
        if calls["n"] >= 3:
            worker.stop()
        return original()

    worker.run_once = once  # type: ignore[method-assign]
    worker.run()
    assert calls["n"] == 3
    assert worker.is_live()
    worker.last_heartbeat -= 10
    assert not worker.is_live()


def test_run_loop_survives_queue_errors(
    queue: RedisStreamQueue, writer: QuoteWriter, server: fakeredis.FakeServer
) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    server.connected = False
    calls = {"n": 0}
    original = worker.run_once

    def once() -> int:
        calls["n"] += 1
        worker.stop()
        return original()

    worker.run_once = once  # type: ignore[method-assign]
    worker.last_heartbeat -= 100
    worker.run()
    assert calls["n"] == 1
    assert worker.is_live()  # a Redis outage must not fail liveness


def test_heartbeat_advances_per_message(queue: RedisStreamQueue, writer: QuoteWriter) -> None:
    worker = make_worker(queue, writer, worker_block_ms=0)
    beats: list[float] = []
    original = worker.handle

    def handle(message):  # type: ignore[no-untyped-def]
        beats.append(worker.last_heartbeat)
        return original(message)

    worker.handle = handle  # type: ignore[method-assign]
    queue.publish([event("AAA"), event("BBB"), event("CCC")])
    worker.run_once()
    assert len(beats) == 3
    assert beats == sorted(beats)
    assert worker.last_heartbeat >= beats[-1]
