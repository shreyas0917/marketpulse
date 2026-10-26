#!/usr/bin/env bash
# Shared helpers for scripts/drills/*.sh (sourced, not executed).
# Every drill prints start/end timestamps and registers cleanup steps that run on exit,
# including Ctrl-C, so the system is always restored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=marketpulse
EVIDENCE="$ROOT/docs/experiments/evidence"
BASE_URL=http://127.0.0.1:18080
HOST_HEADER=marketpulse.localtest.me
PROM=http://localhost:29090
AM=http://localhost:29093
# shellcheck disable=SC2034  # used by the drills that source this file
PRODUCER=http://localhost:29200
ALERTS=(uv run --project "$ROOT" python "$ROOT/scripts/lib/alerts.py" --prometheus "$PROM" --alertmanager "$AM")
mkdir -p "$EVIDENCE"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
epoch() { date -u +%s; }
log() { printf '%s [drill %s] %s\n' "$(now)" "${DRILL:-?}" "$*"; }

_cleanups=()
_pids=()
on_exit() { _cleanups+=("$*"); }
_run_cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  log "cleanup (exit code $rc)"
  local i
  for (( i=${#_cleanups[@]}-1; i>=0; i-- )); do
    eval "${_cleanups[$i]}" || log "cleanup step failed: ${_cleanups[$i]}"
  done
  for p in "${_pids[@]}"; do kill "$p" 2>/dev/null || true; done
  log "END $(now)"
  exit "$rc"
}
trap _run_cleanup EXIT
trap 'exit 130' INT TERM

drill_start() {
  DRILL="$1"
  DRILL_START="$(now)"
  log "START $DRILL_START - $2"
  kubectl config use-context kind-marketpulse >/dev/null
}

# Port-forward Prometheus, Alertmanager and the producer for the duration of the drill.
port_forwards() {
  kubectl -n monitoring port-forward svc/kps-prometheus 29090:9090 >/dev/null 2>&1 &
  _pids+=($!)
  kubectl -n monitoring port-forward svc/kps-alertmanager 29093:9093 >/dev/null 2>&1 &
  _pids+=($!)
  kubectl -n "$NS" port-forward deploy/marketpulse-producer 29200:9200 >/dev/null 2>&1 &
  _pids+=($!)
  local _
  for _ in $(seq 1 30); do
    curl -fsS -o /dev/null "$PROM/-/ready" && curl -fsS -o /dev/null "$AM/-/ready" && return 0
    sleep 1
  done
  log "port-forwards not ready"; return 1
}

# start_load RATE DURATION NAME: k6 steady load in the background; summary saved as evidence.
start_load() {
  k6 run -q -e BASE_URL="$BASE_URL" -e HOST_HEADER="$HOST_HEADER" -e RATE="$1" -e DURATION="$2" \
    --summary-export "$EVIDENCE/$3-k6-summary.json" "$ROOT/load-tests/steady.js" >/dev/null 2>&1 &
  LOAD_PID=$!
  _pids+=("$LOAD_PID")
  log "k6 load started: $1 req/s for $2 (pid $LOAD_PID)"
}

wait_load() { wait "${LOAD_PID}" || true; log "k6 load finished"; }

# Argo CD self-heal would undo deliberate changes (scaling Redis to 0, setting env vars), so
# drills that change cluster state pause auto-sync and always re-enable it on exit.
pause_autosync() {
  kubectl -n argocd patch application marketpulse --type merge \
    -p '{"spec":{"syncPolicy":{"automated":null}}}' >/dev/null
  on_exit "kubectl -n argocd patch application marketpulse --type merge -p '{\"spec\":{\"syncPolicy\":{\"automated\":{\"prune\":true,\"selfHeal\":true}}}}' >/dev/null && log 'Argo CD auto-sync re-enabled'"
  log "Argo CD auto-sync paused"
}

prom_value() {
  "${ALERTS[@]}" query --expr "$1" | python -c "import json,sys; r=json.load(sys.stdin); print(r[0]['value'][1] if r else 'none')"
}

export_evidence() {
  local name="$1"; shift
  "${ALERTS[@]}" export --out "$EVIDENCE/$name.json" --start "$DRILL_START" --end "$(now)" --step 10s "$@"
}

k6_failures() {
  python - "$EVIDENCE/$1-k6-summary.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
m = d["metrics"]
reqs = m.get("http_reqs", {}).get("count", 0)
checks = m.get("checks", {})
print(f"requests={reqs} checks_passed={checks.get('passes', 0)} checks_failed={checks.get('fails', 0)} "
      f"p99_ms={m.get('http_req_duration', {}).get('p(99)', 0):.1f}")
EOF
}
