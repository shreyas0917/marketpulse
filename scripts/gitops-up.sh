#!/usr/bin/env bash
# Install Argo CD and Argo Rollouts on the kind cluster, then register the marketpulse
# Application (deploy/argocd/) which syncs the chart from GitHub main.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND_DIR="$ROOT/deploy/kind"
# shellcheck source=deploy/kind/versions.env
source "$KIND_DIR/versions.env"
log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

kubectl config use-context kind-marketpulse >/dev/null
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null

log "installing Argo Rollouts chart $ARGO_ROLLOUTS_CHART_VERSION"
helm upgrade --install argo-rollouts argo/argo-rollouts --version "$ARGO_ROLLOUTS_CHART_VERSION" \
  -n argo-rollouts --create-namespace -f "$KIND_DIR/argo-rollouts-values.yaml" --wait --timeout 5m >/dev/null

log "installing Argo CD chart $ARGO_CD_CHART_VERSION"
helm upgrade --install argocd argo/argo-cd --version "$ARGO_CD_CHART_VERSION" \
  -n argocd --create-namespace -f "$KIND_DIR/argo-cd-values.yaml" --wait --timeout 10m >/dev/null

# A Helm release created by `make deploy-local` before Argo CD existed is adopted by Argo CD;
# drop Helm's release record so the two tools do not both own the resources.
if helm -n marketpulse status marketpulse >/dev/null 2>&1; then
  log "handing marketpulse over from helm to Argo CD"
  kubectl -n marketpulse delete secret -l owner=helm,name=marketpulse >/dev/null
fi

log "registering Application marketpulse"
kubectl apply -f "$ROOT/deploy/argocd/" >/dev/null
for _ in $(seq 1 90); do
  status="$(kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null || true)"
  [[ "$status" == "Synced/Healthy" ]] && break
  sleep 5
done
kubectl -n argocd get application marketpulse
log "Argo CD UI: kubectl -n argocd port-forward svc/argocd-server 18443:80 (user admin, password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
