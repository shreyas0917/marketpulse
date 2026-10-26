"""Metric definitions shared by more than one service.

Each service builds its own CollectorRegistry so a process only exports the
metrics it owns, and tests get isolated registries.
"""

from __future__ import annotations

import logging
import threading

from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    CollectorRegistry,
    Gauge,
)

from app.common.queue.base import QueueBackend

log = logging.getLogger(__name__)


def new_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    for collector in (PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR):
        registry.register(collector)
    return registry


class QueueMetrics:
    """queue_depth, dlq_depth and queue_oldest_event_age_seconds gauges."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.queue_depth = Gauge(
            "queue_depth", "Messages waiting or in flight in the main queue", registry=registry
        )
        self.dlq_depth = Gauge("dlq_depth", "Messages in the dead-letter queue", registry=registry)
        self.oldest_age = Gauge(
            "queue_oldest_event_age_seconds",
            "Age of the oldest message still in the main queue (0 when empty)",
            registry=registry,
        )

    def refresh(self, queue: QueueBackend) -> None:
        stats = queue.stats()
        self.queue_depth.set(stats.depth)
        self.dlq_depth.set(stats.dlq_depth)
        if stats.oldest_age_seconds is not None:
            self.oldest_age.set(stats.oldest_age_seconds)


class QueueMetricsPoller(threading.Thread):
    """Background thread that refreshes QueueMetrics every `interval` seconds."""

    def __init__(self, metrics: QueueMetrics, queue: QueueBackend, interval: float) -> None:
        super().__init__(name="queue-metrics", daemon=True)
        self._metrics = metrics
        self._queue = queue
        self._interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._metrics.refresh(self._queue)
            except Exception:
                log.warning("queue_metrics_refresh_failed", exc_info=True)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
