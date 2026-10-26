"""Generate the Grafana dashboards in observability/grafana/dashboards/.

Dashboards are written as code here and committed as JSON so Grafana can
provision them from files. Regenerate with `make dashboards`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
Q = 'route="/v1/quotes/{symbol}"'

Panel = dict[str, Any]


class Layout:
    """Places panels left to right on a 24-column grid."""

    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.row_h = 0
        self.next_id = 1

    def place(self, panel: Panel, w: int, h: int) -> Panel:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel["id"] = self.next_id
        self.next_id += 1
        self.x += w
        self.row_h = max(self.row_h, h)
        return panel

    def row(self, title: str) -> Panel:
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel = {"type": "row", "title": title, "collapsed": False, "panels": []}
        placed = self.place(panel, 24, 1)
        self.x, self.y, self.row_h = 0, self.y + 1, 0
        return placed


def target(expr: str, legend: str = "", ref: str = "A") -> dict[str, Any]:
    return {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto", "refId": ref}


def timeseries(
    title: str,
    exprs: list[tuple[str, str]],
    unit: str = "short",
    description: str = "",
    thresholds: list[tuple[float, str]] | None = None,
    min_: float | None = 0,
    max_: float | None = None,
) -> Panel:
    defaults: dict[str, Any] = {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10}}
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    if thresholds:
        defaults["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}]
            + [{"color": c, "value": v} for v, c in thresholds],
        }
        defaults["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [target(e, leg, chr(65 + i)) for i, (e, leg) in enumerate(exprs)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }


def stat(
    title: str,
    expr: str,
    unit: str = "short",
    thresholds: list[tuple[float, str]] | None = None,
    description: str = "",
    decimals: int | None = None,
    base_color: str = "green",
) -> Panel:
    steps = [{"color": base_color, "value": None}]
    steps += [{"color": c, "value": v} for v, c in (thresholds or [])]
    defaults: dict[str, Any] = {
        "unit": unit,
        "thresholds": {"mode": "absolute", "steps": steps},
        "color": {"mode": "thresholds"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [target(expr, title)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background"},
    }


ANNOTATIONS = [
    {
        "name": "Deploys / restarts",
        "datasource": DS,
        "enable": True,
        "iconColor": "blue",
        "expr": "changes(process_start_time_seconds[1m]) > 0",
        "titleFormat": "Process (re)started: {{job}} {{instance}}",
        "step": "30s",
    },
    {
        "name": "Fault injection",
        "datasource": DS,
        "enable": True,
        "iconColor": "orange",
        "expr": "max by (job, type) (fault_injection_active) == 1",
        "titleFormat": "Fault active: {{type}}",
        "step": "30s",
    },
    {
        "name": "Incidents (paging alerts)",
        "datasource": DS,
        "enable": True,
        "iconColor": "red",
        "expr": 'ALERTS{alertstate="firing", severity="page"}',
        "titleFormat": "{{alertname}}",
        "step": "30s",
    },
]


def dashboard(uid: str, title: str, panels: list[Panel], tags: list[str]) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["marketpulse", *tags],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                *ANNOTATIONS,
            ]
        },
        "templating": {"list": []},
        "panels": panels,
    }


def quantile(q: float, metric: str, selector: str = "") -> str:
    sel = f"{{{selector}}}" if selector else ""
    return f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket{sel}[5m])))"


def api_dashboard() -> dict[str, Any]:
    lay = Layout()
    p: list[Panel] = [lay.row("Traffic and errors")]
    p.append(
        lay.place(stat("Request rate", f"sum(rate(http_requests_total{{{Q}}}[5m]))", "reqps"), 6, 4)
    )
    p.append(
        lay.place(
            stat(
                "5xx ratio (5m)",
                f'(sum(rate(http_requests_total{{{Q}, status=~"5.."}}[5m])) or vector(0)) '
                f"/ sum(rate(http_requests_total{{{Q}}}[5m]))",
                "percentunit",
                [(0.001, "orange"), (0.01, "red")],
                decimals=3,
            ),
            6,
            4,
        )
    )
    p.append(
        lay.place(
            stat(
                "Quote store up",
                "min(quote_store_up)",
                "none",
                base_color="red",
                thresholds=[(1, "green")],
                description="0 when the last Redis call from any API pod failed",
            ),
            6,
            4,
        )
    )
    p.append(
        lay.place(
            stat(
                "Faults active",
                'sum(fault_injection_active{job=~".*api.*"})',
                "none",
                [(1, "orange")],
            ),
            6,
            4,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Requests by status",
                [(f"sum by (status) (rate(http_requests_total{{{Q}}}[1m]))", "{{status}}")],
                "reqps",
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Error ratio (5xx / all)",
                [
                    (
                        f'(sum(rate(http_requests_total{{{Q}, status=~"5.."}}[1m])) or vector(0)) '
                        f"/ sum(rate(http_requests_total{{{Q}}}[1m]))",
                        "5xx ratio",
                    )
                ],
                "percentunit",
                thresholds=[(0.001, "red")],
            ),
            12,
            8,
        )
    )
    p.append(lay.row("Latency and freshness"))
    p.append(
        lay.place(
            timeseries(
                "Quote latency percentiles",
                [
                    (quantile(0.5, "http_request_duration_seconds", Q), "p50"),
                    (quantile(0.9, "http_request_duration_seconds", Q), "p90"),
                    (quantile(0.99, "http_request_duration_seconds", Q), "p99"),
                ],
                "s",
                thresholds=[(0.25, "red")],
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Quote age percentiles",
                [
                    (quantile(0.5, "quote_age_seconds"), "p50"),
                    (quantile(0.99, "quote_age_seconds"), "p99"),
                ],
                "s",
                thresholds=[(2, "red")],
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Responses by source (stale-response rate)",
                [("sum by (source) (rate(quote_responses_total[1m]))", "{{source}}")],
                "reqps",
                description="local_cache = stale quote served in degraded mode; "
                "unavailable = 503 QUOTE_UNAVAILABLE",
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Quote store up by pod", [("quote_store_up", "{{instance}}")], "none", max_=1
            ),
            12,
            8,
        )
    )
    return dashboard("marketpulse-api", "MarketPulse / API", p, ["api"])


def ingestion_dashboard() -> dict[str, Any]:
    lay = Layout()
    p: list[Panel] = [lay.row("Throughput")]
    p.append(
        lay.place(
            stat("Events/s processed", 'sum(rate(events_processed_total{result="ok"}[5m]))', "ops"),
            6,
            4,
        )
    )
    p.append(
        lay.place(
            stat("Queue depth", "max(queue_depth)", "short", [(1000, "orange"), (10000, "red")]),
            6,
            4,
        )
    )
    p.append(
        lay.place(
            stat(
                "Oldest queued event age",
                "max(queue_oldest_event_age_seconds)",
                "s",
                [(2, "orange"), (10, "red")],
            ),
            6,
            4,
        )
    )
    p.append(lay.place(stat("DLQ depth", "max(dlq_depth)", "short", [(1, "red")]), 6, 4))
    p.append(
        lay.place(
            timeseries(
                "Events by result",
                [
                    ("sum by (result) (rate(events_processed_total[1m]))", "{{result}}"),
                    ("sum(rate(producer_events_published_total[1m]))", "published"),
                ],
                "ops",
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Retries and dead-letters",
                [
                    ('sum(rate(events_processed_total{result="retried"}[1m]))', "retried"),
                    (
                        'sum(rate(events_processed_total{result="dead_lettered"}[1m]))',
                        "dead_lettered",
                    ),
                ],
                "ops",
            ),
            12,
            8,
        )
    )
    p.append(lay.row("Lag and backlog"))
    p.append(
        lay.place(
            timeseries(
                "Ingestion lag percentiles",
                [
                    (quantile(0.5, "ingestion_lag_seconds"), "p50"),
                    (quantile(0.9, "ingestion_lag_seconds"), "p90"),
                    (quantile(0.99, "ingestion_lag_seconds"), "p99"),
                ],
                "s",
                thresholds=[(1, "red")],
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Queue depth and DLQ depth",
                [("max(queue_depth)", "queue"), ("max(dlq_depth)", "dlq")],
                "short",
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Oldest queued event age",
                [("max(queue_oldest_event_age_seconds)", "oldest")],
                "s",
                thresholds=[(10, "red")],
            ),
            12,
            8,
        )
    )
    p.append(
        lay.place(
            timeseries(
                "Producer target rate and burst",
                [
                    ("sum(producer_target_rate)", "target events/s"),
                    ("max(producer_burst_active)", "burst active"),
                ],
                "short",
            ),
            12,
            8,
        )
    )
    return dashboard("marketpulse-ingestion", "MarketPulse / Ingestion", p, ["ingestion"])


DASHBOARDS = {
    "api.json": api_dashboard,
    "ingestion.json": ingestion_dashboard,
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in DASHBOARDS.items():
        (OUT / name).write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
