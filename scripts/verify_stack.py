"""Verify a running stack through its HTTP APIs (M2 acceptance checks).

1. Every Prometheus target reports up == 1.
2. Grafana lists the provisioned dashboards.
3. Every query on those dashboards returns data from Prometheus.

Usage: uv run python scripts/verify_stack.py [--prometheus URL] [--grafana URL] [--api URL]
       [--dashboards uid,uid] [--warm SECONDS]
Exits non-zero if any check fails. Writes nothing; prints one line per check.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any


def get(url: str, auth: str | None = None, host: str | None = None) -> Any:
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def prom_query(prom: str, expr: str) -> list[Any]:
    url = f"{prom}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    body = get(url)
    if body.get("status") != "success":
        raise RuntimeError(body)
    result: list[Any] = body["data"]["result"]
    return result


def warm(api: str, seconds: float, host: str | None) -> None:
    """Send a little quote traffic so latency and age histograms have samples."""
    end = time.monotonic() + seconds
    symbols = ["RELIANCE", "TCS", "INFY"]
    n = 0
    while time.monotonic() < end:
        req = urllib.request.Request(f"{api}/v1/quotes/{symbols[n % 3]}")
        if host:
            req.add_header("Host", host)
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:
            print(f"  warm-up request failed: {exc}")
        n += 1
        time.sleep(0.05)
    print(f"sent {n} warm-up quote requests")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prometheus", default="http://localhost:19090")
    ap.add_argument("--grafana", default="http://localhost:13000")
    ap.add_argument("--grafana-auth", default="", help="user:password; anonymous if empty")
    ap.add_argument("--api", default="http://localhost:18000")
    ap.add_argument("--api-host", default="")
    ap.add_argument("--dashboards", default="marketpulse-api,marketpulse-ingestion")
    ap.add_argument("--warm", type=float, default=30)
    ap.add_argument("--target-filter", default="", help="only check targets whose job matches")
    args = ap.parse_args()
    failures = 0
    auth = args.grafana_auth or None

    if args.warm > 0:
        warm(args.api, args.warm, args.api_host or None)

    targets = get(f"{args.prometheus}/api/v1/targets?state=active")["data"]["activeTargets"]
    for t in targets:
        job = t["labels"].get("job", "?")
        if args.target_filter and args.target_filter not in job:
            continue
        ok = t["health"] == "up"
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] target {job} {t['scrapeUrl']} health={t['health']}")
    up = prom_query(args.prometheus, "up == 0")
    if args.target_filter:
        up = [r for r in up if args.target_filter in r["metric"].get("job", "")]
    print(f"[{'PASS' if not up else 'FAIL'}] no series with up == 0 ({len(up)} found)")
    failures += bool(up)

    listed = {d["uid"]: d["title"] for d in get(f"{args.grafana}/api/search?type=dash-db", auth)}
    for uid in args.dashboards.split(","):
        ok = uid in listed
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] grafana lists dashboard {uid} ({listed.get(uid)})")
        if not ok:
            continue
        dash = get(f"{args.grafana}/api/dashboards/uid/{uid}", auth)["dashboard"]
        for panel in dash["panels"]:
            for tgt in panel.get("targets", []):
                expr = tgt["expr"].replace("$__range", "1h").replace("$__rate_interval", "1m")
                rows = prom_query(args.prometheus, expr)
                ok = len(rows) > 0
                failures += not ok
                print(f"[{'PASS' if ok else 'FAIL'}] {uid} / {panel['title']}: {len(rows)} series")
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
