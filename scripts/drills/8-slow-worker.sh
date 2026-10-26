#!/usr/bin/env bash
# Drill 8: slow worker (FAULT_WORKER_EXTRA_LATENCY_MS=500).
# Expected: the backlog grows, ingestion lag exceeds 1 s and IngestionLagFastBurn pages within
# the target of 5 minutes. Restoring is done by re-enabling Argo CD auto-sync, which reverts the
# out-of-band env change (self-heal); the backlog then drains.
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 8 "slow worker"
port_forwards
pause_autosync

NAME=drill8
LATENCY_MS="${LATENCY_MS:-500}"
FAULT_SECONDS="${FAULT_SECONDS:-300}"
on_exit "kubectl -n $NS set env deploy/marketpulse-worker FAULT_WORKER_EXTRA_LATENCY_MS- >/dev/null 2>&1 || true"

log "baseline lag p99: $(prom_value 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))')"
kubectl -n "$NS" set env deploy/marketpulse-worker "FAULT_WORKER_EXTRA_LATENCY_MS=$LATENCY_MS" >/dev/null
kubectl -n "$NS" rollout status deploy/marketpulse-worker --timeout=180s >/dev/null
T0=$(epoch)
log "slow workers running at $(now) (${LATENCY_MS} ms per event)"
# A burn alert that does not fire is a result, not a failure of the drill: record it and go on.
if "${ALERTS[@]}" wait --alert IngestionLagFastBurn --state firing --timeout 900 | tee "$EVIDENCE/$NAME-lagburn-firing.json"; then
  T_DETECT=$(epoch)
  log "IngestionLagFastBurn firing $(( T_DETECT - T0 ))s after the fault (target <= 300 s)"
else
  T_DETECT=""
  log "IngestionLagFastBurn did NOT fire within 900 s (burn is rate-weighted; see IngestionStalled)"
fi
"${ALERTS[@]}" wait --alert IngestionStalled --state firing --timeout 300 | tee "$EVIDENCE/$NAME-stalled-firing.json" || true
remaining=$(( FAULT_SECONDS - ($(epoch) - T0) ))
(( remaining > 0 )) && sleep "$remaining"
peak_depth=$(prom_value 'max(max_over_time(queue_depth[10m]))')
log "peak queue depth: $peak_depth; lag p99 now: $(prom_value 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))')"

T1=$(epoch)
log "restoring: re-enabling Argo CD auto-sync (self-heal reverts the env change)"
kubectl -n argocd patch application marketpulse --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}' >/dev/null
kubectl -n argocd annotate application marketpulse argocd.argoproj.io/refresh=normal --overwrite >/dev/null
for _ in $(seq 1 120); do
  kubectl -n "$NS" get deploy marketpulse-worker -o jsonpath='{.spec.template.spec.containers[0].env[*].name}' \
    | grep -q FAULT_WORKER_EXTRA_LATENCY_MS || break
  sleep 2
done
T_REVERT=$(epoch)
log "Argo CD reverted the worker env after $(( T_REVERT - T1 ))s"
kubectl -n "$NS" rollout status deploy/marketpulse-worker --timeout=180s >/dev/null
drained=""
for _ in $(seq 1 120); do
  d=$(prom_value 'max(queue_depth)')
  if [[ "$d" != "none" ]] && (( $(printf '%.0f' "$d") <= 20 )); then drained=$(epoch); break; fi
  sleep 5
done
log "backlog drained $(( ${drained:-$(epoch)} - T1 ))s after restore started"
"${ALERTS[@]}" wait --alert IngestionLagFastBurn --state resolved --timeout 1800 | tee "$EVIDENCE/$NAME-lagburn-resolved.json" || true
"${ALERTS[@]}" wait --alert IngestionStalled --state resolved --timeout 900 | tee "$EVIDENCE/$NAME-stalled-resolved.json" || true
T_RESOLVED=$(epoch)
cat > "$EVIDENCE/$NAME-result.json" <<EOF
{"latency_ms": $LATENCY_MS, "seconds_to_lag_burn_alert": ${T_DETECT:+$(( T_DETECT - T0 ))}${T_DETECT:-null}, "fault_seconds": $(( T1 - T0 )),
 "peak_queue_depth": "$peak_depth", "seconds_argocd_revert": $(( T_REVERT - T1 )),
 "seconds_to_drain_after_restore": $(( ${drained:-$T_RESOLVED} - T1 )), "seconds_to_alert_resolved_after_restore": $(( T_RESOLVED - T1 ))}
EOF
export_evidence "$NAME" \
  --expr 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))' \
  --expr 'max(queue_depth)' --expr 'max(queue_oldest_event_age_seconds)' \
  --expr 'sli:ingestion_lag:error_ratio_rate5m' --expr 'sli:ingestion_lag:error_ratio_rate1h' \
  --expr 'sli:freshness:error_ratio_rate5m'
