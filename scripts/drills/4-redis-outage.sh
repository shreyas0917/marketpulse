#!/usr/bin/env bash
# Drill 4: stop Redis under load.
# Expected: QuoteStoreDown fires; pods serve cached quotes (stale: true) for <= 30 s, then
# 503 QUOTE_UNAVAILABLE; availability and freshness burn; pods stay Ready (in the load balancer);
# full recovery after Redis returns. One API pod is restarted mid-outage: its cache is empty, so
# it answers 503 straight away (cold-cache limitation).
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 4 "stop Redis"
port_forwards
pause_autosync

NAME=drill4
OUTAGE_SECONDS="${OUTAGE_SECONDS:-150}"
on_exit "kubectl -n $NS scale deploy/marketpulse-redis --replicas=1 >/dev/null; kubectl -n $NS rollout status deploy/marketpulse-redis --timeout=120s >/dev/null && log 'redis restored'"

ready_api() { kubectl -n "$NS" get pods -l app.kubernetes.io/component=api --no-headers | grep -c '1/1' || true; }

start_load 30 10m "$NAME"
sleep 60
log "ready API pods before: $(ready_api)"

uv run --project "$ROOT" python "$ROOT/scripts/lib/probe.py" --out "$EVIDENCE/$NAME-probe-ingress.csv" \
  --duration $(( OUTAGE_SECONDS + 150 )) --interval 0.5 --symbols RELIANCE,TCS,INFY \
  --target "ingress=$BASE_URL@$HOST_HEADER" &
_pids+=($!)
sleep 5

T0=$(epoch)
log "stopping Redis (scale to 0) at $(now)"
kubectl -n "$NS" scale deploy/marketpulse-redis --replicas=0 >/dev/null
echo "{\"redis_stopped_at\": \"$(now)\"" > "$EVIDENCE/$NAME-timeline.json"

sleep 5
victim=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
log "restarting API pod $victim mid-outage (cold-cache check)"
kubectl -n "$NS" delete pod "$victim" --wait=false >/dev/null
fresh=""
for _ in $(seq 1 60); do
  fresh=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api \
    -o jsonpath='{range .items[*]}{.metadata.name} {.status.containerStatuses[0].ready} {.metadata.creationTimestamp}{"\n"}{end}' \
    | awk -v v="$victim" '$1 != v && $2 == "true"' | sort -k3 | tail -1 | cut -d' ' -f1)
  created=$(kubectl -n "$NS" get pod "$fresh" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || true)
  if [[ -n "$fresh" ]] && [[ "$(date -u -d "$created" +%s 2>/dev/null || echo 0)" -ge "$T0" ]]; then break; fi
  fresh=""
  sleep 1
done
if [[ -n "$fresh" ]]; then
  log "replacement pod $fresh ready $(( $(epoch) - T0 ))s into the outage; probing it directly"
  kubectl -n "$NS" port-forward "pod/$fresh" 28000:8000 >/dev/null 2>&1 &
  _pids+=($!)
  sleep 3
  uv run --project "$ROOT" python "$ROOT/scripts/lib/probe.py" --out "$EVIDENCE/$NAME-probe-cold-pod.csv" \
    --duration 10 --interval 0.5 --symbols RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK \
    --target "cold-pod=http://localhost:28000"
  log "cold pod responses: $(cut -d, -f4,5 "$EVIDENCE/$NAME-probe-cold-pod.csv" | tail -n +2 | sort | uniq -c | tr '\n' ' ')"
else
  log "replacement pod did not become ready in time"
fi

log "ready API pods during outage: $(ready_api) (readiness ignores Redis)"
"${ALERTS[@]}" wait --alert QuoteStoreDown --state firing --timeout 240 | tee "$EVIDENCE/$NAME-storedown-firing.json"
remaining=$(( OUTAGE_SECONDS - ($(epoch) - T0) ))
(( remaining > 0 )) && sleep "$remaining"
log "ready API pods at end of outage: $(ready_api)"
log "alerts firing: $(prom_value 'count by () (ALERTS{alertstate="firing", alertname=~"QuoteStoreDown|AvailabilityFastBurn|FreshnessFastBurn|IngestionStalled"})')"
for a in AvailabilityFastBurn FreshnessFastBurn; do
  "${ALERTS[@]}" wait --alert "$a" --state firing --timeout 5 > "$EVIDENCE/$NAME-$a-firing.json" || true
done

T1=$(epoch)
log "restoring Redis at $(now) after $(( T1 - T0 ))s"
kubectl -n "$NS" scale deploy/marketpulse-redis --replicas=1 >/dev/null
kubectl -n "$NS" rollout status deploy/marketpulse-redis --timeout=120s >/dev/null
log "redis ready $(( $(epoch) - T1 ))s after restore"
echo ", \"redis_restored_at\": \"$(now)\", \"outage_seconds\": $(( T1 - T0 ))}" >> "$EVIDENCE/$NAME-timeline.json"
"${ALERTS[@]}" wait --alert QuoteStoreDown --state resolved --timeout 300 | tee "$EVIDENCE/$NAME-storedown-resolved.json"

wait_load
log "k6: $(k6_failures "$NAME")"
uv run --project "$ROOT" python - "$EVIDENCE/$NAME-probe-ingress.csv" "$T0" "$T1" <<'EOF' | tee "$EVIDENCE/$NAME-phases.json"
import csv, json, sys
from datetime import datetime
rows = list(csv.DictReader(open(sys.argv[1])))
t0, t1 = float(sys.argv[2]), float(sys.argv[3])
def ts(r): return datetime.fromisoformat(r["ts"]).timestamp()
out = [r for r in rows if ts(r) >= t0]
stale = [ts(r) - t0 for r in out if r["status"] == "200" and r["stale"] == "True" and ts(r) < t1]
unavail = [ts(r) - t0 for r in out if r["status"] == "503" and ts(r) < t1]
fresh_after = [ts(r) - t1 for r in out if ts(r) >= t1 and r["status"] == "200" and r["stale"] == "False"]
print(json.dumps({
    "probe_rows": len(rows),
    "stale_200_count": len(stale),
    "stale_200_last_seen_s_after_stop": round(max(stale), 1) if stale else None,
    "max_stale_age_seconds": max((float(r["age_seconds"]) for r in out if r["stale"] == "True"), default=None),
    "first_503_s_after_stop": round(min(unavail), 1) if unavail else None,
    "all_503_from_s": round(min(u for u in unavail if u > (max(stale) if stale else 0)), 1) if unavail else None,
    "first_fresh_200_s_after_restore": round(min(fresh_after), 1) if fresh_after else None,
}, indent=1))
EOF
export_evidence "$NAME" \
  --expr 'min(quote_store_up)' \
  --expr 'sum by (source) (rate(quote_responses_total[1m]))' \
  --expr 'sum by (status) (rate(http_requests_total{route="/v1/quotes/{symbol}"}[1m]))' \
  --expr 'sli:availability:error_ratio_rate5m' --expr 'sli:freshness:error_ratio_rate5m' \
  --expr 'sum(kube_pod_status_ready{namespace="marketpulse", condition="true", pod=~"marketpulse-api.*"})'
