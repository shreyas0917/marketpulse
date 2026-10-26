#!/usr/bin/env bash
# Drill 1: kill API pods (force, no grace period) under steady load, one at a time.
# Expected: pods are replaced; no SLO breach with >= 2 replicas.
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 1 "kill API pods"
port_forwards

NAME=drill1
RATE=30
start_load "$RATE" 4m "$NAME"
sleep 60

for round in 1 2; do
  victim=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
  ready_before=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api --no-headers | grep -c '1/1')
  t_kill=$(epoch)
  log "round $round: force-killing $victim (ready API pods: $ready_before)"
  kubectl -n "$NS" delete pod "$victim" --grace-period=0 --force >/dev/null 2>&1
  # Wait until the ready count is back to where it was.
  for _ in $(seq 1 120); do
    ready=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api --no-headers | grep -c '1/1' || true)
    (( ready >= ready_before )) && break
    sleep 1
  done
  log "round $round: replacement ready after $(( $(epoch) - t_kill ))s"
  echo "{\"round\": $round, \"victim\": \"$victim\", \"killed_at\": $t_kill, \"replaced_after_s\": $(( $(epoch) - t_kill ))}" \
    >> "$EVIDENCE/$NAME-rounds.jsonl"
  sleep 45
done

wait_load
log "k6: $(k6_failures "$NAME")"
log "availability error ratio 5m: $(prom_value 'sli:availability:error_ratio_rate5m')"
log "5xx in drill window: $(prom_value 'sum(increase(http_requests_total{route="/v1/quotes/{symbol}", status=~"5.."}[5m])) or vector(0)')"
log "AvailabilityFastBurn firing: $(prom_value 'count(ALERTS{alertname="AvailabilityFastBurn", alertstate="firing"}) or vector(0)')"
export_evidence "$NAME" \
  --expr 'sli:availability:error_ratio_rate5m' \
  --expr 'sum by (status) (rate(http_requests_total{route="/v1/quotes/{symbol}"}[1m]))' \
  --expr 'count(up{job="api"} == 1)' \
  --expr 'sum(kube_pod_status_ready{namespace="marketpulse", condition="true", pod=~"marketpulse-api.*"})'
