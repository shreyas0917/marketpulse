"""Synthetic market feed producer.

Publishes random-walk prices for a fixed set of symbols at PRODUCER_RATE events
per second. Burst mode multiplies the rate (default 10x for 5 minutes) to
simulate the 09:15 IST market open; it can start with the process
(PRODUCER_BURST_ON_START) or be triggered with `POST /burst`.

The producer also exports the queue gauges, so a stopped or stalled worker is
still visible through queue_oldest_event_age_seconds.
"""

from __future__ import annotations

import logging
import random
import signal
import threading
import time
from types import FrameType
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge

from app.common.config import Settings
from app.common.http import ServiceHttpServer
from app.common.metrics import QueueMetrics, QueueMetricsPoller, new_registry
from app.common.models import MarketEvent
from app.common.queue import QueueBackend, make_queue

log = logging.getLogger(__name__)

TICK_SECONDS = 0.1
POISON_BODIES = ("{not json", '{"symbol": "", "price": -1}', '{"event_id": "x"}')


class Producer:
    def __init__(
        self,
        settings: Settings,
        queue: QueueBackend,
        registry: CollectorRegistry | None = None,
        seed: int | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.registry = registry or new_registry()
        self.symbols = settings.producer_symbol_list
        self._rng = random.Random(seed)
        self.prices = {s: round(self._rng.uniform(100, 3000), 2) for s in self.symbols}
        self._lock = threading.Lock()
        self._burst_until = 0.0
        self._burst_multiplier = 1.0
        self._carry = 0.0
        self._stop = threading.Event()
        self.published = Counter(
            "producer_events_published_total",
            "Events published to the queue",
            registry=self.registry,
        )
        self.target_rate = Gauge(
            "producer_target_rate",
            "Events per second the producer is aiming for",
            registry=self.registry,
        )
        self.burst_active = Gauge(
            "producer_burst_active", "1 while market-open burst mode is on", registry=self.registry
        )
        self.queue_metrics = QueueMetrics(self.registry)
        if settings.producer_burst_on_start:
            self.start_burst()

    def start_burst(
        self, multiplier: float | None = None, duration: float | None = None
    ) -> dict[str, Any]:
        mult = multiplier or self.settings.producer_burst_multiplier
        dur = duration or self.settings.producer_burst_duration_seconds
        if mult < 1 or dur <= 0:
            raise ValueError("multiplier must be >= 1 and duration > 0")
        with self._lock:
            self._burst_multiplier = mult
            self._burst_until = time.monotonic() + dur
        log.warning("burst_started", extra={"multiplier": mult, "duration_seconds": dur})
        return {"burst": True, "multiplier": mult, "duration_seconds": dur}

    def current_rate(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            bursting = now < self._burst_until
            mult = self._burst_multiplier if bursting else 1.0
        self.burst_active.set(1 if bursting else 0)
        rate = self.settings.producer_rate * mult
        self.target_rate.set(rate)
        return rate

    def next_event(self) -> MarketEvent:
        symbol = self._rng.choice(self.symbols)
        price = max(0.01, self.prices[symbol] * (1 + self._rng.gauss(0, 0.0005)))
        self.prices[symbol] = round(price, 2)
        return MarketEvent(symbol=symbol, price=self.prices[symbol])

    def tick(self, dt: float) -> int:
        self._carry += self.current_rate() * dt
        count = int(self._carry)
        self._carry -= count
        if count:
            self.queue.publish([self.next_event() for _ in range(count)])
            self.published.inc(count)
        return count

    def publish_poison(self, count: int) -> dict[str, Any]:
        if not 1 <= count <= 1000:
            raise ValueError("count must be between 1 and 1000")
        bodies = [POISON_BODIES[i % len(POISON_BODIES)] for i in range(count)]
        self.queue.publish_raw(bodies)
        log.warning("poison_published", extra={"count": count})
        return {"poison_published": count}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("producer_started", extra={"rate": self.settings.producer_rate})
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                self.tick(now - last)
            except Exception:
                log.warning("producer_publish_failed", exc_info=True)
            last = now
            self._stop.wait(TICK_SECONDS)
        log.info("producer_stopped")


def main(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    queue = make_queue(settings, consumer="producer")
    producer = Producer(settings, queue)
    poller = QueueMetricsPoller(
        producer.queue_metrics, queue, settings.queue_metrics_interval_seconds
    )

    def burst(params: dict[str, str]) -> dict[str, Any]:
        mult = float(params["multiplier"]) if "multiplier" in params else None
        dur = float(params["duration"]) if "duration" in params else None
        return producer.start_burst(mult, dur)

    def poison(params: dict[str, str]) -> dict[str, Any]:
        return producer.publish_poison(int(params.get("count", "10")))

    server = ServiceHttpServer(
        settings.producer_port,
        producer.registry,
        lambda: True,
        post_handlers={"/burst": burst, "/poison": poison},
    )

    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        producer.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    server.start()
    poller.start()
    try:
        producer.run()
    finally:
        poller.stop()
        server.stop()
