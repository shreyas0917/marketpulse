"""Every `le` in an SLO query must be a real bucket boundary, written as Prometheus stores it.

Prometheus 3 normalises classic-histogram `le` labels to float form ("2.0", "0.25"), which is also
what prometheus_client exposes. A selector like le="2" silently matches nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from prometheus_client.utils import floatToGoString

from app.api.metrics import HTTP_DURATION_BUCKETS, QUOTE_AGE_BUCKETS
from app.worker.metrics import INGESTION_LAG_BUCKETS

ROOT = Path(__file__).resolve().parents[2]
BUCKETS = {
    "http_request_duration_seconds_bucket": HTTP_DURATION_BUCKETS,
    "quote_age_seconds_bucket": QUOTE_AGE_BUCKETS,
    "ingestion_lag_seconds_bucket": INGESTION_LAG_BUCKETS,
}
SLO_BOUNDARIES = {
    "http_request_duration_seconds_bucket": "0.25",
    "quote_age_seconds_bucket": "2.0",
    "ingestion_lag_seconds_bucket": "1.0",
}
SOURCES = [
    *sorted((ROOT / "observability/prometheus/rules").glob("*.yml")),
    *sorted((ROOT / "observability/grafana/dashboards").glob("*.json")),
    ROOT / "deploy/helm/marketpulse/templates/analysis.yaml",
]
# A label matcher block may itself contain the route template's {symbol}.
SELECTOR = re.compile(r"(\w+_bucket)\{(?:[^{}]|\{\w+\})*?le=\\?\"([^\"\\]+)\\?\"")


def selectors() -> list[tuple[str, str, str]]:
    found = []
    for path in SOURCES:
        if not path.exists():
            continue
        for metric, le in SELECTOR.findall(path.read_text(encoding="utf-8")):
            if metric in BUCKETS:
                found.append((path.name, metric, le))
    return found


def test_queries_reference_slo_buckets() -> None:
    metrics = {m for _, m, _ in selectors()}
    assert metrics == set(BUCKETS)


@pytest.mark.parametrize(("source", "metric", "le"), selectors())
def test_le_is_a_bucket_boundary(source: str, metric: str, le: str) -> None:
    assert le == SLO_BOUNDARIES[metric], f"{source}: {metric} uses le={le!r}"
    assert le in {floatToGoString(b) for b in BUCKETS[metric]}
