"""Quote API metrics. Names, labels and buckets follow spec section 6."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

HTTP_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)
QUOTE_AGE_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)


class ApiMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.requests = Counter(
            "http_requests_total",
            "HTTP requests by route template, method and status",
            ["route", "method", "status"],
            registry=registry,
        )
        self.duration = Histogram(
            "http_request_duration_seconds",
            "Time from request receipt to response sent",
            ["route"],
            buckets=HTTP_DURATION_BUCKETS,
            registry=registry,
        )
        self.quote_age = Histogram(
            "quote_age_seconds",
            "Age of each returned quote (response time minus event timestamp)",
            buckets=QUOTE_AGE_BUCKETS,
            registry=registry,
        )
        self.store_up = Gauge(
            "quote_store_up", "1 if the last quote-store call succeeded, else 0", registry=registry
        )
        self.responses = Counter(
            "quote_responses_total",
            "Quote responses by source (store, local_cache, unavailable)",
            ["source"],
            registry=registry,
        )
        for source in ("store", "local_cache", "unavailable"):
            self.responses.labels(source=source)
        self.store_up.set(1)
