#!/usr/bin/env bash
# M3 live checks against the compose stack (`make up` first):
#   1. steady load + FAULT_API_ERROR_RATE=0.2 -> AvailabilityFastBurn fires; clearing resolves it
#   2. stopping the worker fires IngestionStalled; starting it resolves it
# Timestamps are printed and saved with Prometheus/Alertmanager evidence under
# docs/experiments/evidence/.
set -euo pipefail

cd "$(dirname "$0")/.."
EVIDENCE=docs/experiments/evidence
ALERTS=(uv run python scripts/lib/alerts.py)
RATE="${RATE:-50}"
mkdir -p "$EVIDENCE"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

k6_pid=""
cleanup() {
  log "cleanup: clearing faults, starting worker, stopping load"
  docker compose up -d --wait api worker >/dev/null 2>&1 || true
  if [[ -n "$k6_pid" ]]; then kill "$k6_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "start; steady load at ${RATE} req/s"
k6 run -q -e RATE="$RATE" -e DURATION=90m load-tests/steady.js >/dev/null 2>&1 &
k6_pid=$!
sleep 120

log "check 1: enabling FAULT_API_ERROR_RATE=0.2"
FAULT_API_ERROR_RATE=0.2 docker compose up -d --wait api >/dev/null
FAULT_ON=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "fault on at $FAULT_ON"
"${ALERTS[@]}" wait --alert AvailabilityFastBurn --state firing --timeout 900 | tee "$EVIDENCE/m3-availability-firing.json"
sleep 60  # let it burn a little longer, as a real incident would
docker compose up -d --wait api >/dev/null
FAULT_OFF=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "fault cleared at $FAULT_OFF"
"${ALERTS[@]}" wait --alert AvailabilityFastBurn --state resolved --timeout 3000 | tee "$EVIDENCE/m3-availability-resolved.json"

log "check 2: stopping worker"
docker compose stop worker >/dev/null
WORKER_STOP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "worker stopped at $WORKER_STOP"
"${ALERTS[@]}" wait --alert IngestionStalled --state firing --timeout 600 | tee "$EVIDENCE/m3-stall-firing.json"
docker compose start worker >/dev/null
WORKER_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "worker started at $WORKER_START"
"${ALERTS[@]}" wait --alert IngestionStalled --state resolved --timeout 600 | tee "$EVIDENCE/m3-stall-resolved.json"

END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
"${ALERTS[@]}" export --out "$EVIDENCE/m3-live-check.json" --start "$START" --end "$END" --step 15s \
  --expr 'sli:availability:error_ratio_rate5m' --expr 'sli:availability:error_ratio_rate1h' \
  --expr 'sli:availability:error_ratio_rate30m' --expr 'sli:availability:error_ratio_rate6h' \
  --expr 'max(queue_oldest_event_age_seconds)' --expr 'max by (type) (fault_injection_active)'
cat > "$EVIDENCE/m3-timeline.json" <<EOF
{"start": "$START", "fault_on": "$FAULT_ON", "fault_off": "$FAULT_OFF",
 "worker_stop": "$WORKER_STOP", "worker_start": "$WORKER_START", "end": "$END", "load_rps": $RATE}
EOF
log "done; evidence in $EVIDENCE"
