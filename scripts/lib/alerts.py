"""Alertmanager / Prometheus helpers for live checks and drills.

  wait   --alert NAME --state firing|resolved [--timeout S]   block until the alert reaches state
  query  --expr PROMQL                                         print an instant query result
  export --out FILE --start ISO --end ISO --expr PROMQL...     save range queries + alerts as JSON

Prints UTC ISO timestamps so results can be copied into drill docs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def active_alerts(am: str, name: str) -> list[dict[str, Any]]:
    flt = urllib.parse.urlencode(
        {
            "filter": f'alertname="{name}"',
            "active": "true",
            "silenced": "false",
            "inhibited": "true",
        }
    )
    alerts: list[dict[str, Any]] = get_json(f"{am}/api/v2/alerts?{flt}")
    return [a for a in alerts if a.get("status", {}).get("state") == "active"]


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    started = now_iso()
    while time.monotonic() < deadline:
        try:
            alerts = active_alerts(args.alertmanager, args.alert)
        except OSError as exc:
            print(f"{now_iso()} alertmanager unreachable: {exc}", file=sys.stderr)
            alerts = None
        if alerts is not None:
            firing = bool(alerts)
            if (args.state == "firing") == firing:
                print(
                    json.dumps(
                        {
                            "alert": args.alert,
                            "state": args.state,
                            "observed_at": now_iso(),
                            "waited_since": started,
                            "starts_at": [a.get("startsAt") for a in alerts],
                            "labels": [a.get("labels") for a in alerts],
                        }
                    )
                )
                return 0
        time.sleep(args.interval)
    print(
        json.dumps(
            {
                "alert": args.alert,
                "state": args.state,
                "timeout": True,
                "waited_since": started,
                "gave_up_at": now_iso(),
            }
        )
    )
    return 1


def cmd_query(args: argparse.Namespace) -> int:
    q = urllib.parse.urlencode({"query": args.expr})
    print(json.dumps(get_json(f"{args.prometheus}/api/v1/query?{q}")["data"]["result"]))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    out: dict[str, Any] = {
        "exported_at": now_iso(),
        "start": args.start,
        "end": args.end,
        "step": args.step,
        "queries": {},
    }
    for expr in args.expr:
        q = urllib.parse.urlencode(
            {"query": expr, "start": args.start, "end": args.end, "step": args.step}
        )
        out["queries"][expr] = get_json(f"{args.prometheus}/api/v1/query_range?{q}")["data"]
    q = urllib.parse.urlencode(
        {"query": "ALERTS", "start": args.start, "end": args.end, "step": args.step}
    )
    out["alerts_series"] = get_json(f"{args.prometheus}/api/v1/query_range?{q}")["data"]
    out["alertmanager_active"] = get_json(f"{args.alertmanager}/api/v2/alerts")
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alertmanager", default="http://localhost:19093")
    ap.add_argument("--prometheus", default="http://localhost:19090")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait")
    w.add_argument("--alert", required=True)
    w.add_argument("--state", choices=["firing", "resolved"], required=True)
    w.add_argument("--timeout", type=float, default=900)
    w.add_argument("--interval", type=float, default=5)
    q = sub.add_parser("query")
    q.add_argument("--expr", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--start", required=True)
    e.add_argument("--end", required=True)
    e.add_argument("--step", default="15s")
    e.add_argument("--expr", action="append", default=[])
    args = ap.parse_args()
    return {"wait": cmd_wait, "query": cmd_query, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
