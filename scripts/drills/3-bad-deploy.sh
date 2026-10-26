#!/usr/bin/env bash
# Drill 3: deploy a bad version (FAULT_API_ERROR_RATE=0.2) through GitOps under load.
# Expected: canary analysis fails, Argo Rollouts aborts automatically and traffic returns to
# stable; the bad commit is then reverted in Git. Same flow as the M5 check (scripts/canary.sh).
# shellcheck source=scripts/drills/lib.sh
source "$(dirname "$0")/lib.sh"
drill_start 3 "deploy bad version"
port_forwards
"$ROOT/scripts/canary.sh" bad | tee "$EVIDENCE/drill3-canary.log"
for f in timeline.jsonl result.json rollout.yaml analysisrun.yaml; do
  cp "$EVIDENCE/canary-bad-$f" "$EVIDENCE/drill3-$f" 2>/dev/null || true
done
export_evidence drill3 \
  --expr 'sum by (role) (rate(http_requests_total{route="/v1/quotes/{symbol}"}[30s]))' \
  --expr 'sum by (role) (rate(http_requests_total{route="/v1/quotes/{symbol}", status=~"5.."}[30s]))' \
  --expr 'sli:availability:error_ratio_rate5m'
