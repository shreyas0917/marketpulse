#!/usr/bin/env bash
# M4 acceptance checks on kind. Usage: scripts/m4-checks.sh {security|netpol|hpa|pod-delete|all}
# Results are printed with UTC timestamps; nothing is changed permanently.
set -euo pipefail

cd "$(dirname "$0")/.."
NS=marketpulse
REDIS_IMAGE="redis:8.10.2-alpine@sha256:0bf6577d7774e592b29d093e3ac9390eb91e13d72c21e506afce736de5e0eeb0"
BASE_URL=http://127.0.0.1:18080
HOST_HEADER=marketpulse.localtest.me
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "FAIL: $*"; exit 1; }

check_security() {
  log "security: every container non-root with requests and limits"
  local out
  out=$(kubectl -n "$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{" runAsNonRoot="}{.spec.securityContext.runAsNonRoot}{" uid="}{.spec.securityContext.runAsUser}{range .spec.containers[*]}{" ro="}{.securityContext.readOnlyRootFilesystem}{" req="}{.resources.requests}{" lim="}{.resources.limits}{end}{"\n"}{end}')
  echo "$out"
  if echo "$out" | grep -E 'runAsNonRoot=( |$)|runAsNonRoot=false|uid=0 |req= |lim= ' >/dev/null; then
    fail "a pod is missing non-root or resources"
  fi
  log "PASS security"
}

# run_redis_probe NAME LABELS: one-off pod that tries `redis-cli ping`; prints output and exit code.
run_redis_probe() {
  kubectl -n "$NS" run "$1" --rm -i --restart=Never --quiet --pod-running-timeout=5m     --image="$REDIS_IMAGE" --labels="$2" --command --     sh -c 'timeout 5 redis-cli -h marketpulse-redis ping; echo "exit=$?"' 2>&1 || true
}

check_netpol() {
  log "netpol: positive control - same probe pod labelled as the worker must reach Redis"
  local out
  out=$(run_redis_probe np-control "app.kubernetes.io/name=marketpulse,app.kubernetes.io/component=worker")
  echo "  control: $(echo "$out" | tr '
' ' ')"
  echo "$out" | grep -q PONG || fail "control pod could not reach Redis (test would be meaningless)"
  log "netpol: pod outside the allowed set must NOT reach Redis"
  out=$(run_redis_probe np-intruder "app=np-intruder")
  echo "  intruder: $(echo "$out" | tr '
' ' ')"
  echo "$out" | grep -q PONG && fail "intruder pod reached Redis"
  echo "$out" | grep -qE 'exit=(124|143|1)' || fail "intruder pod did not run the probe"
  log "PASS intruder blocked"
  for comp in api worker; do
    local pod
    pod=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=$comp -o jsonpath='{.items[0].metadata.name}')
    if kubectl -n "$NS" exec "$pod" -- python -c         "import redis; print(redis.Redis(host='marketpulse-redis', socket_timeout=3).ping())" | grep -q True; then
      log "PASS $comp ($pod) reaches Redis"
    else
      fail "$comp cannot reach Redis"
    fi
  done
}

hpa_replicas() { kubectl -n "$NS" get hpa marketpulse-api -o jsonpath='{.status.currentReplicas}/{.status.desiredReplicas}'; }

check_hpa() {
  local min
  min=$(kubectl -n "$NS" get hpa marketpulse-api -o jsonpath='{.spec.minReplicas}')
  # Start from a quiet baseline so the scale-up time is not left over from earlier load.
  for _ in $(seq 1 60); do
    [[ "$(kubectl -n "$NS" get hpa marketpulse-api -o jsonpath='{.status.desiredReplicas}')" == "$min" ]] && break
    sleep 5
  done
  local t_load
  t_load=$(date -u +%s)
  log "hpa: start $(hpa_replicas) (min $min); starting load ${HPA_RATE:-400} req/s for ${HPA_DURATION:-4m}"
  k6 run -q -e BASE_URL="$BASE_URL" -e HOST_HEADER="$HOST_HEADER" -e RATE="${HPA_RATE:-400}" \
    -e DURATION="${HPA_DURATION:-4m}" load-tests/steady.js >/dev/null 2>&1 &
  local k6_pid=$! up_at="" max=0 cur
  while kill -0 "$k6_pid" 2>/dev/null; do
    cur=$(kubectl -n "$NS" get hpa marketpulse-api -o jsonpath='{.status.desiredReplicas}')
    if (( cur > max )); then max=$cur; log "hpa desired=$cur $(kubectl -n "$NS" top pods -l app.kubernetes.io/component=api --no-headers 2>/dev/null | awk '{printf "%s=%s ", $1, $2}')"; fi
    if [[ -z "$up_at" ]] && (( cur > min )); then up_at=$(date -u +%Y-%m-%dT%H:%M:%SZ); log "scaled up at $up_at ($(( $(date -u +%s) - t_load ))s after load start)"; fi
    sleep 10
  done
  wait "$k6_pid" || true
  [[ -n "$up_at" ]] || fail "HPA did not scale up"
  local stop_at down_at=""
  stop_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  log "load stopped at $stop_at (peak desired=$max); waiting for scale-down"
  for _ in $(seq 1 60); do
    cur=$(kubectl -n "$NS" get hpa marketpulse-api -o jsonpath='{.status.currentReplicas}')
    if (( cur <= min )); then down_at=$(date -u +%Y-%m-%dT%H:%M:%SZ); break; fi
    sleep 10
  done
  [[ -n "$down_at" ]] || fail "HPA did not scale back down within 10 minutes"
  log "PASS hpa: up at $up_at, peak $max, load stop $stop_at, back to $min at $down_at ($(( $(date -u -d "$down_at" +%s) - $(date -u -d "$stop_at" +%s) ))s after load stop)"
}

check_pod_delete() {
  local n
  n=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api --field-selector=status.phase=Running -o name | wc -l)
  (( n >= 2 )) || fail "need >= 2 API replicas, have $n"
  log "pod-delete: $n API replicas; 60 s of load at 30 req/s with strict checks"
  k6 run -q -e BASE_URL="$BASE_URL" -e HOST_HEADER="$HOST_HEADER" -e RATE=30 -e DURATION=60s \
    -e STRICT=1 --summary-export "${SUMMARY:-/dev/null}" load-tests/steady.js &
  local k6_pid=$!
  sleep 30
  local victim
  victim=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
  log "deleting $victim"
  kubectl -n "$NS" delete pod "$victim" --wait=false >/dev/null
  if wait "$k6_pid"; then
    log "PASS pod-delete: no failed requests while $victim was replaced"
  else
    fail "k6 saw failed requests during pod deletion"
  fi
}

case "${1:-all}" in
  security) check_security ;;
  netpol) check_netpol ;;
  hpa) check_hpa ;;
  pod-delete) check_pod_delete ;;
  all) check_security; check_netpol; check_pod_delete; check_hpa ;;
  *) echo "usage: $0 {security|netpol|hpa|pod-delete|all}" >&2; exit 2 ;;
esac
