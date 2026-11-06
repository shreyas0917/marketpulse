#!/usr/bin/env bash
# Drill 8: slow worker (FAULT_WORKER_EXTRA_LATENCY_MS=500).
# Expected: the backlog grows, ingestion lag exceeds 1 s and IngestionLagFastBurn pages within
# the target of 5 minutes. Restore: re-enable Argo CD auto-sync and wait for self-heal to put the
# worker env back to the value Git declares (FAULT_* are declared in the chart, F13). Only what is
# observed is logged: if Argo CD has not reverted the env within REVERT_TIMEOUT seconds, the drill
# restores it with its own cleanup and records that instead (F14).
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 8 "slow worker"
port_forwards
pause_autosync

NAME=drill8
LATENCY_MS="${LATENCY_MS:-500}"
FAULT_SECONDS="${FAULT_SECONDS:-300}"
REVERT_TIMEOUT="${REVERT_TIMEOUT:-300}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-900}"
# The drill's own cleanup: set the env back to the neutral value the chart declares (values.yaml
# faults:), so the result matches Git whether or not Argo CD reverted it first.
restore_env() { kubectl -n "$NS" set env deploy/marketpulse-worker FAULT_WORKER_EXTRA_LATENCY_MS=0 >/dev/null; }
on_exit "restore_env 2>/dev/null || true"
# Current value of the fault env on the worker pod template ("" when the name is absent).
worker_fault() {
  kubectl -n "$NS" get deploy marketpulse-worker \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="FAULT_WORKER_EXTRA_LATENCY_MS")].value}'
}

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
log "restoring: re-enabling Argo CD auto-sync and waiting up to ${REVERT_TIMEOUT}s for self-heal"
kubectl -n argocd patch application marketpulse --type merge \
  -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}' >/dev/null
kubectl -n argocd annotate application marketpulse argocd.argoproj.io/refresh=normal --overwrite >/dev/null
reverted_by="" T_REVERT="" saw_out_of_sync=false
while (( $(epoch) - T1 < REVERT_TIMEOUT )); do
  [[ "$(kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}')" == "OutOfSync" ]] \
    && saw_out_of_sync=true
  v="$(worker_fault)"
  if [[ -z "$v" || "$v" == "0" ]]; then reverted_by=argocd; T_REVERT=$(epoch); break; fi
  sleep 2
done
if [[ "$reverted_by" == "argocd" ]]; then
  log "Argo CD self-heal reverted the worker env after $(( T_REVERT - T1 ))s (OutOfSync seen: $saw_out_of_sync)"
else
  log "Argo CD did NOT revert the worker env within ${REVERT_TIMEOUT}s (value still '$(worker_fault)', OutOfSync seen: $saw_out_of_sync); restoring with the drill cleanup"
  restore_env
  reverted_by=drill_cleanup
  T_REVERT=$(epoch)
fi
if kubectl -n "$NS" rollout status deploy/marketpulse-worker --timeout=180s >/dev/null; then
  log "worker rollout complete $(( $(epoch) - T1 ))s after restore started"
else
  log "worker rollout NOT complete within 180 s"
fi
drained=""
while (( $(epoch) - T1 < DRAIN_TIMEOUT )); do
  d=$(prom_value 'max(queue_depth)')
  if [[ "$d" != "none" ]] && (( $(printf '%.0f' "$d") <= 20 )); then drained=$(epoch); break; fi
  sleep 5
done
if [[ -n "$drained" ]]; then
  log "backlog drained (queue depth <= 20) $(( drained - T1 ))s after restore started"
else
  log "backlog NOT drained within ${DRAIN_TIMEOUT}s of restore (queue depth now $(prom_value 'max(queue_depth)'))"
fi
resolved=true
"${ALERTS[@]}" wait --alert IngestionLagFastBurn --state resolved --timeout 1800 | tee "$EVIDENCE/$NAME-lagburn-resolved.json" || resolved=false
"${ALERTS[@]}" wait --alert IngestionStalled --state resolved --timeout 900 | tee "$EVIDENCE/$NAME-stalled-resolved.json" || resolved=false
T_RESOLVED=$(epoch)
$resolved || log "an ingestion alert did NOT resolve within its timeout"

# Seconds since restore started, or null for anything that was not observed.
since_restore() { if [[ -n "$1" ]]; then echo "$(( $1 - T1 ))"; else echo null; fi; }
argocd_revert=null
[[ "$reverted_by" == "argocd" ]] && argocd_revert=$(( T_REVERT - T1 ))
resolved_at=""
$resolved && resolved_at=$T_RESOLVED
cat > "$EVIDENCE/$NAME-result.json" <<EOF
{"latency_ms": $LATENCY_MS, "seconds_to_lag_burn_alert": ${T_DETECT:+$(( T_DETECT - T0 ))}${T_DETECT:-null}, "fault_seconds": $(( T1 - T0 )),
 "peak_queue_depth": "$peak_depth", "reverted_by": "$reverted_by", "argocd_out_of_sync_seen": $saw_out_of_sync,
 "seconds_argocd_revert": $argocd_revert, "seconds_to_restore": $(( T_REVERT - T1 )),
 "seconds_to_drain_after_restore": $(since_restore "$drained"),
 "seconds_to_alert_resolved_after_restore": $(since_restore "$resolved_at")}
EOF
export_evidence "$NAME" \
  --expr 'histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[1m])))' \
  --expr 'max(queue_depth)' --expr 'max(queue_oldest_event_age_seconds)' \
  --expr 'sli:ingestion_lag:error_ratio_rate5m' --expr 'sli:ingestion_lag:error_ratio_rate1h' \
  --expr 'sli:freshness:error_ratio_rate5m'
