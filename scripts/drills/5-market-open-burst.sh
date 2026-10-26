#!/usr/bin/env bash
# Drill 5: market-open burst (09:15 IST simulation). Quote traffic and the producer's event rate
# both jump 10x for 5 minutes. Expected: the API HPA scales out; ingestion lag spikes and the
# backlog drains after the burst. Measures peak lag, peak queue depth and drain time.
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 5 "market-open burst"
port_forwards

NAME=drill5
BASE_RATE="${BASE_RATE:-30}"
PRE=1m; BURST=5m; POST=4m
SAMPLES="$EVIDENCE/$NAME-samples.csv"
echo "ts,api_hpa_current,api_hpa_desired,worker_hpa_current,worker_hpa_desired,queue_depth,lag_p99_1m,producer_rate" > "$SAMPLES"

k6 run -q -e BASE_URL="$BASE_URL" -e HOST_HEADER="$HOST_HEADER" -e PRODUCER_URL="$PRODUCER" \
  -e BASE_RATE="$BASE_RATE" -e MULTIPLIER=10 -e PRE="$PRE" -e BURST="$BURST" -e POST="$POST" \
  --summary-export "$EVIDENCE/$NAME-k6-summary.json" "$ROOT/load-tests/burst.js" >/dev/null 2>&1 &
LOAD_PID=$!
_pids+=("$LOAD_PID")
T0=$(epoch)
log "burst load started: base ${BASE_RATE} req/s, 10x from +60s for 300s"

hpa() { kubectl -n "$NS" get hpa "marketpulse-$1" -o jsonpath='{.status.currentReplicas},{.status.desiredReplicas}'; }
while kill -0 "$LOAD_PID" 2>/dev/null; do
  printf '%s,%s,%s,%s,%s,%s\n' "$(now)" "$(hpa api)" "$(hpa worker)" \
    "$(prom_value 'max(queue_depth)')" \
    "$(prom_value 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))')" \
    "$(prom_value 'sum(producer_target_rate)')" >> "$SAMPLES"
  sleep 10
done
log "k6: $(k6_failures "$NAME")"

uv run --project "$ROOT" python - "$SAMPLES" "$T0" <<'EOF' | tee "$EVIDENCE/$NAME-result.json"
import csv, json, sys
from datetime import datetime
rows = list(csv.DictReader(open(sys.argv[1])))
t0 = float(sys.argv[2])
def f(v):
    try: return float(v)
    except ValueError: return None
for r in rows:
    r["t"] = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp() - t0
burst_end = 60 + 10 + 300
base = [f(r["queue_depth"]) for r in rows if r["t"] < 60 and f(r["queue_depth"]) is not None]
baseline = max(base) if base else 0
peak_q = max((f(r["queue_depth"]) or 0 for r in rows), default=0)
lags = [f(r["lag_p99_1m"]) for r in rows if f(r["lag_p99_1m"]) is not None]
drained = [r["t"] - burst_end for r in rows
           if r["t"] >= burst_end and (f(r["queue_depth"]) or 0) <= max(baseline * 2, 5)]
print(json.dumps({
    "api_replicas_max": max(int(r["api_hpa_current"] or 0) for r in rows),
    "api_replicas_start": int(rows[0]["api_hpa_current"] or 0),
    "worker_replicas_max": max(int(r["worker_hpa_current"] or 0) for r in rows),
    "queue_depth_baseline": baseline,
    "queue_depth_peak": peak_q,
    "ingestion_lag_p99_peak_s": max(lags) if lags else None,
    "queue_drain_seconds_after_burst": round(min(drained), 0) if drained else None,
}, indent=1))
EOF
export_evidence "$NAME" \
  --expr 'kube_horizontalpodautoscaler_status_current_replicas{namespace="marketpulse"}' \
  --expr 'kube_horizontalpodautoscaler_status_desired_replicas{namespace="marketpulse"}' \
  --expr 'max(queue_depth)' --expr 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))' \
  --expr 'sum(rate(http_requests_total{route="/v1/quotes/{symbol}"}[1m]))' \
  --expr 'histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{route="/v1/quotes/{symbol}"}[1m])))' \
  --expr 'sli:ingestion_lag:error_ratio_rate5m' --expr 'sli:latency:error_ratio_rate5m'
