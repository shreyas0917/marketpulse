"""Probe quote endpoints at a fixed interval and record every response to CSV.

Usage: uv run python scripts/lib/probe.py --out FILE --duration S [--interval S]
         --target NAME=URL[@HOST] ... --symbols A,B
Each row: utc timestamp, target, symbol, HTTP status, error code, stale, age_seconds, source.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime


def fetch(url: str, host: str | None) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(url)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError) as exc:
        return 0, {"error": type(exc).__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--target", action="append", required=True, help="name=url[@host]")
    ap.add_argument("--symbols", default="RELIANCE,TCS")
    args = ap.parse_args()
    targets = []
    for spec in args.target:
        name, _, rest = spec.partition("=")
        url, _, host = rest.partition("@")
        targets.append((name, url.rstrip("/"), host or None))
    symbols = args.symbols.split(",")
    end = time.monotonic() + args.duration
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "target", "symbol", "status", "error", "stale", "age_seconds", "source"])
        while time.monotonic() < end:
            for name, url, host in targets:
                for sym in symbols:
                    status, body = fetch(f"{url}/v1/quotes/{sym}", host)
                    w.writerow(
                        [
                            datetime.now(UTC).isoformat(timespec="milliseconds"),
                            name,
                            sym,
                            status,
                            body.get("error", ""),
                            body.get("stale", ""),
                            body.get("age_seconds", ""),
                            body.get("source", ""),
                        ]
                    )
            fh.flush()
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
