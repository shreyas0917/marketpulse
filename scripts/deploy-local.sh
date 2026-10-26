#!/usr/bin/env bash
# Build the service image, load it into the kind cluster and deploy the chart.
#
# The image tag comes from deploy/helm/marketpulse/values-kind.yaml (the value in Git), so
# Helm and Argo CD deploy the same image. Bump that tag when the code changes.
#   - Before Argo CD is installed (M4): helm upgrade --install directly.
#   - Once Argo CD is installed (M5): apply deploy/argocd/ and let Argo CD sync from GitHub.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLUSTER=marketpulse
NAMESPACE=marketpulse
CHART="$ROOT/deploy/helm/marketpulse"
log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

kubectl config use-context "kind-$CLUSTER" >/dev/null
TAG="$(awk '/^image:/{f=1} f && /tag:/{print $2; exit}' "$CHART/values-kind.yaml" | tr -d '"')"
[[ -n "$TAG" ]] || { echo "no image.tag in values-kind.yaml" >&2; exit 1; }

log "building marketpulse:$TAG"
docker build -q -t "marketpulse:$TAG" "$ROOT" >/dev/null
log "loading marketpulse:$TAG into kind"
kind load docker-image "marketpulse:$TAG" --name "$CLUSTER" >/dev/null
# Best effort: pre-load Redis so the nodes do not need registry access at deploy time. Loading a
# digest-pinned multi-platform image can fail with Docker Desktop's containerd store; the nodes
# then pull it themselves.
REDIS_IMAGE="$(awk '/^redis:/{f=1} f && /repository:/{r=$2} f && /tag:/{print r":"$2; exit}' "$CHART/values.yaml")"
if ! kind load docker-image "$REDIS_IMAGE" --name "$CLUSTER" >/dev/null 2>&1; then
  log "could not pre-load $REDIS_IMAGE; nodes will pull it"
fi

if kubectl get namespace argocd >/dev/null 2>&1; then
  log "Argo CD present: applying deploy/argocd/ (sync is from GitHub main)"
  kubectl apply -f "$ROOT/deploy/argocd/" >/dev/null
  kubectl -n argocd annotate application marketpulse argocd.argoproj.io/refresh=normal --overwrite >/dev/null
  for _ in $(seq 1 60); do
    status="$(kubectl -n argocd get application marketpulse -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null || true)"
    [[ "$status" == "Synced/Healthy" ]] && break
    sleep 5
  done
  log "application marketpulse: ${status:-unknown}"
else
  log "helm upgrade --install marketpulse"
  helm upgrade --install marketpulse "$CHART" -n "$NAMESPACE" --create-namespace \
    -f "$CHART/values-kind.yaml" "$@" --wait --timeout 5m >/dev/null
fi
# Same tag, new build: restart so pods pick up the freshly loaded image.
if [[ "${RESTART:-0}" == "1" ]]; then
  kubectl -n "$NAMESPACE" rollout restart deploy >/dev/null
fi
kubectl -n "$NAMESPACE" get pods -o wide
log "deployed marketpulse:$TAG"
