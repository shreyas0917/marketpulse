#!/usr/bin/env bash
# GitOps canary demos on kind (M5 and drill 3). Each scenario commits a change to the API pod
# template in deploy/helm/marketpulse/values-kind.yaml, pushes it to main, and lets Argo CD +
# Argo Rollouts take it from there. Every state transition is timestamped.
#
#   scripts/canary.sh good        harmless env change under load -> all steps pass, promoted
#   scripts/canary.sh bad         FAULT_API_ERROR_RATE=0.2 under load -> analysis fails, aborted,
#                                 traffic back to stable; then `git revert` + push
#   scripts/canary.sh no-traffic  harmless change with no load -> no data -> aborted; then revert
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
NS=marketpulse
VALUES=deploy/helm/marketpulse/values-kind.yaml
EVIDENCE=docs/experiments/evidence
MODE="${1:?usage: canary.sh good|bad|no-traffic}"
NAME="canary-$MODE"
TIMELINE="$EVIDENCE/$NAME-timeline.jsonl"
mkdir -p "$EVIDENCE"
: > "$TIMELINE"
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s [canary %s] %s\n' "$(now)" "$MODE" "$*"; }
mark() { log "$1"; printf '{"ts": "%s", "epoch": %s, "event": "%s"}\n' "$(now)" "$(date -u +%s)" "$1" >> "$TIMELINE"; }

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "run from main" >&2; exit 1; }
git diff --quiet -- "$VALUES" || { echo "$VALUES has local changes" >&2; exit 1; }
git pull -q --ff-only origin main

# set_api_env KEY VALUE: rewrite the api.env block of values-kind.yaml.
set_api_env() {
  python - "$VALUES" "$1" "$2" <<'EOF'
import re, sys
path, key, value = sys.argv[1:]
text = open(path, encoding="utf-8").read()
# Match `api:` then its `  env:` child, tolerating comment/blank lines between them,
# and capture the existing `    KEY: value` entries that follow.
m = re.search(
    r"^(?P<head>api:\n(?:[ \t]*#.*\n|[ \t]*\n)*  env:\n)(?P<body>(?:    [^#\n].*\n)*)",
    text, re.M,
)
assert m, "values-kind.yaml needs an 'api:' block with an 'env:' child"
lines = [l for l in m.group("body").splitlines() if not l.strip().startswith(f"{key}:")]
lines.append(f'    {key}: "{value}"')
new = m.group("head") + "\n".join(lines) + "\n"
open(path, "w", encoding="utf-8", newline="\n").write(text[:m.start()] + new + text[m.end():])
EOF
}

push_main() {
  if command -v gitleaks >/dev/null 2>&1; then gitleaks git . --no-banner --exit-code 1 >/dev/null; fi
  git push -q origin main
}

refresh() { kubectl -n argocd annotate application marketpulse argocd.argoproj.io/refresh=normal --overwrite >/dev/null; }

rollout_state() {
  kubectl -n "$NS" get rollout marketpulse-api -o jsonpath='{.status.phase}|{.status.currentStepIndex}|{.status.canary.weights.canary.weight}|{.status.abort}|{.status.stableRS}|{.status.currentPodHash}'
}
latest_analysis() {
  kubectl -n "$NS" get analysisrun --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{range .items[*]}{.metadata.name}|{.status.phase}{"\n"}{end}' 2>/dev/null | tail -1
}
canary_ingress_weight() {
  kubectl -n "$NS" get ingress marketpulse-api-marketpulse-api-canary \
    -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/canary-weight}' 2>/dev/null || echo "none"
}

start_load() {
  k6 run -q -e BASE_URL=http://127.0.0.1:18080 -e HOST_HEADER=marketpulse.localtest.me -e RATE=30 \
    -e DURATION=60m --summary-export "$EVIDENCE/$NAME-k6-summary.json" load-tests/steady.js >/dev/null 2>&1 &
  pids+=($!)
  log "k6 steady load 30 req/s started"
}

before="$(rollout_state)"
stable_before="$(cut -d'|' -f5 <<< "$before")"
log "rollout before: $before"
analysis_before="$(latest_analysis)"

case "$MODE" in
  good)
    start_load; sleep 60
    set_api_env RELEASE_NOTE "canary-good-$(date -u +%Y%m%dT%H%M%SZ)"
    git commit -q -m "release(api): harmless RELEASE_NOTE change (canary demo: good)" -- "$VALUES" ;;
  bad)
    start_load; sleep 60
    set_api_env FAULT_API_ERROR_RATE "0.2"
    git commit -q -m "release(api): enable FAULT_API_ERROR_RATE=0.2 (canary demo: bad version)" -- "$VALUES" ;;
  no-traffic)
    if pgrep -f "k6 run" >/dev/null 2>&1; then echo "stop all k6 runs first" >&2; exit 1; fi
    set_api_env RELEASE_NOTE "canary-no-traffic-$(date -u +%Y%m%dT%H%M%SZ)"
    git commit -q -m "release(api): harmless RELEASE_NOTE change with no traffic (canary demo)" -- "$VALUES" ;;
  *) echo "unknown mode $MODE" >&2; exit 2 ;;
esac
CHANGE_SHA="$(git rev-parse HEAD)"
push_main
mark "pushed $CHANGE_SHA to main"
refresh

last="" last_an="" last_w="" outcome=""
t_failed="" t_stable=""
for _ in $(seq 1 2400); do
  st="$(rollout_state)"; an="$(latest_analysis)"; w="$(canary_ingress_weight)"
  [[ "$st" != "$last" ]] && mark "rollout phase|step|weight|abort|stableRS|currentHash = $st" && last="$st"
  if [[ "$an" != "$last_an" && "$an" != "$analysis_before" ]]; then mark "analysisrun $an"; last_an="$an"; fi
  [[ "$w" != "$last_w" ]] && mark "canary ingress weight = $w" && last_w="$w"
  IFS="|" read -r phase _step weight abort stable current <<< "$st"
  if [[ -z "$t_failed" && "$an" == *"|Failed" && "$an" != "$analysis_before" ]]; then
    t_failed=$(date -u +%s%3N); mark "analysis failed"
  fi
  if [[ -n "$t_failed" && -z "$t_stable" && "$abort" == "true" && ( "$w" == "0" || "$w" == "none" ) && "${weight:-0}" == "0" ]]; then
    t_stable=$(date -u +%s%3N); mark "100% stable (canary weight 0)"
    outcome="aborted"; break
  fi
  if [[ "$MODE" == "good" && "$phase" == "Healthy" && "$stable" == "$current" && "$stable" != "$stable_before" ]]; then
    outcome="promoted"; mark "promoted: new stable $stable"; break
  fi
  sleep 1
done
log "outcome: ${outcome:-timeout}"
if [[ -n "$t_failed" && -n "$t_stable" ]]; then
  log "analysis failure -> 100% stable: $(( t_stable - t_failed )) ms"
fi
kubectl -n "$NS" get rollout marketpulse-api -o yaml > "$EVIDENCE/$NAME-rollout.yaml"
run="$(cut -d'|' -f1 <<< "$(latest_analysis)")"
[[ -n "$run" ]] && kubectl -n "$NS" get analysisrun "$run" -o yaml > "$EVIDENCE/$NAME-analysisrun.yaml"
kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}' \
  | tee "$EVIDENCE/$NAME-argocd-status.txt"
cat > "$EVIDENCE/$NAME-result.json" <<EOF
{"mode": "$MODE", "commit": "$CHANGE_SHA", "outcome": "${outcome:-timeout}",
 "analysis_failed_epoch_ms": ${t_failed:-null}, "stable_epoch_ms": ${t_stable:-null},
 "failure_to_stable_ms": $( [[ -n "$t_failed" && -n "$t_stable" ]] && echo $(( t_stable - t_failed )) || echo null )}
EOF

if [[ "$MODE" != "good" ]]; then
  log "GitOps follow-up: reverting $CHANGE_SHA so Git matches what runs"
  git revert --no-edit "$CHANGE_SHA" >/dev/null
  push_main
  mark "pushed revert $(git rev-parse HEAD)"
  refresh
  for _ in $(seq 1 180); do
    st="$(rollout_state)"
    [[ "$(cut -d'|' -f1 <<< "$st")" == "Healthy" ]] && break
    sleep 2
  done
  mark "rollout after revert: $(rollout_state)"
  kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}/{.status.health.status}{"\n"}'
fi
[[ "$outcome" == "aborted" && "$MODE" != "good" ]] || [[ "$outcome" == "promoted" && "$MODE" == "good" ]]
