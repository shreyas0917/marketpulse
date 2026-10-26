"""Ingestion worker: consumes market events, validates them and writes the latest quote.

Delivery is at-least-once and possibly out of order, so writes deduplicate on
event_id and only replace a quote with a newer one (see app.common.store).
Failed events are retried with backoff and dead-lettered after
WORKER_MAX_ATTEMPTS deliveries.
"""

from __future__ import annotations

import logging
import random
import signal
import threading
import time
from types import FrameType

import redis
from prometheus_client import CollectorRegistry

from app.common.config import Settings
from app.common.faults import WORKER_FAULTS, Faults, register_faults
from app.common.http import ServiceHttpServer
from app.common.metrics import QueueMetrics, QueueMetricsPoller, new_registry
from app.common.models import MarketEvent
from app.common.queue import Message, QueueBackend, make_queue
from app.common.store import QuoteWriter, WriteResult
from app.worker.metrics import WorkerMetrics

log = logging.getLogger(__name__)


class InjectedFailure(RuntimeError):
    """Raised by the FAULT_WORKER_FAIL_RATE hook."""


class Worker:
    def __init__(
        self,
        settings: Settings,
        queue: QueueBackend,
        writer: QuoteWriter,
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.writer = writer
        self.registry = registry or new_registry()
        self.metrics = WorkerMetrics(self.registry)
        self.queue_metrics = QueueMetrics(self.registry)
        self.faults = Faults.from_settings(settings)
        register_faults(self.faults, self.registry, WORKER_FAULTS)
        self.symbols = set(settings.symbol_list)
        self.last_heartbeat = time.monotonic()
        self._stop = threading.Event()

    def process(self, message: Message) -> WriteResult:
        """Validate and write one event. Raises on any failure."""
        if self.faults.worker_extra_latency_ms:
            time.sleep(self.faults.worker_extra_latency_ms / 1000.0)
        if self.faults.worker_fail_rate and random.random() < self.faults.worker_fail_rate:
            raise InjectedFailure("injected worker failure")
        event = MarketEvent.from_json(message.body)
        if event.symbol not in self.symbols:
            raise ValueError(f"unknown symbol {event.symbol}")
        result = self.writer.write(event)
        if result is WriteResult.WRITTEN:
            self.metrics.lag.observe(max(0.0, time.time() - event.event_ts))
        elif result is WriteResult.DUPLICATE:
            log.debug("event_duplicate", extra={"event_id": event.event_id})
        return result

    def handle(self, message: Message) -> str:
        try:
            self.process(message)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            if message.attempts >= self.settings.worker_max_attempts:
                self.queue.dead_letter(message, reason)
                self.metrics.processed.labels(result="dead_lettered").inc()
                log.warning(
                    "event_dead_lettered",
                    extra={
                        "message_id": message.id,
                        "attempts": message.attempts,
                        "reason": reason,
                    },
                )
                return "dead_lettered"
            self.queue.retry(message)
            self.metrics.processed.labels(result="retried").inc()
            log.info(
                "event_retry_scheduled",
                extra={"message_id": message.id, "attempts": message.attempts, "reason": reason},
            )
            return "retried"
        self.queue.ack(message)
        self.metrics.processed.labels(result="ok").inc()
        return "ok"

    def run_once(self) -> int:
        messages = self.queue.receive(
            self.settings.worker_batch_size, self.settings.worker_block_ms
        )
        self.last_heartbeat = time.monotonic()
        for message in messages:
            self.handle(message)
            self.last_heartbeat = time.monotonic()
        return len(messages)

    def is_live(self) -> bool:
        age = time.monotonic() - self.last_heartbeat
        return age < self.settings.worker_heartbeat_timeout_seconds

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("worker_started")
        while not self._stop.is_set():
            try:
                self.run_once()
            except (redis.RedisError, OSError):
                # A dependency outage is not a hung process: keep liveness passing so Kubernetes
                # does not restart workers that will recover on their own when Redis returns.
                log.warning("worker_queue_error", exc_info=True)
                self.last_heartbeat = time.monotonic()
                self._stop.wait(1.0)
        log.info("worker_stopped")


def main(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    queue = make_queue(settings)
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=2)
    worker = Worker(settings, queue, QuoteWriter(client, settings.dedup_ttl_seconds))
    poller = QueueMetricsPoller(
        worker.queue_metrics, queue, settings.queue_metrics_interval_seconds
    )
    server = ServiceHttpServer(settings.worker_port, worker.registry, worker.is_live)

    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        log.info("worker_signal", extra={"signal": signum})
        worker.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    server.start()
    poller.start()
    try:
        worker.run()
    finally:
        poller.stop()
        server.stop()
