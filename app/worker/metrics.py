"""Ingestion worker metrics. Names, labels and buckets follow spec section 6."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

INGESTION_LAG_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
RESULTS = ("ok", "retried", "dead_lettered")


class WorkerMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.processed = Counter(
            "events_processed_total",
            "Events handled by the worker, by result (ok, retried, dead_lettered)",
            ["result"],
            registry=registry,
        )
        for result in RESULTS:
            self.processed.labels(result=result)
        self.lag = Histogram(
            "ingestion_lag_seconds",
            "Write time minus event timestamp for every successfully written event",
            buckets=INGESTION_LAG_BUCKETS,
            registry=registry,
        )
