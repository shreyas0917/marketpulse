#!/usr/bin/env bash
# Create the local kind platform: 3-node cluster, Calico (enforces NetworkPolicies),
# metrics-server, ingress-nginx and kube-prometheus-stack. Idempotent: re-running upgrades
# the add-ons in place. Delete with scripts/kind-down.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND_DIR="$ROOT/deploy/kind"
CLUSTER=marketpulse
# shellcheck source=deploy/kind/versions.env
source "$KIND_DIR/versions.env"
log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  log "cluster $CLUSTER already exists"
else
  log "creating cluster $CLUSTER"
  kind create cluster --config "$KIND_DIR/cluster.yaml" --wait 0s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

log "installing Calico $CALICO_VERSION"
kubectl apply --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml" >/dev/null
kubectl -n kube-system rollout status ds/calico-node --timeout=300s
kubectl -n kube-system rollout status deploy/calico-kube-controllers --timeout=300s
kubectl wait --for=condition=Ready nodes --all --timeout=300s

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

log "installing metrics-server $METRICS_SERVER_CHART_VERSION"
helm upgrade --install metrics-server metrics-server/metrics-server \
  --version "$METRICS_SERVER_CHART_VERSION" -n kube-system \
  -f "$KIND_DIR/metrics-server-values.yaml" --wait --timeout 5m >/dev/null

log "installing ingress-nginx $INGRESS_NGINX_CHART_VERSION"
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --version "$INGRESS_NGINX_CHART_VERSION" -n ingress-nginx --create-namespace \
  -f "$KIND_DIR/ingress-nginx-values.yaml" --wait --timeout 5m >/dev/null

log "installing kube-prometheus-stack $KUBE_PROMETHEUS_STACK_CHART_VERSION"
helm upgrade --install kps prometheus-community/kube-prometheus-stack \
  --version "$KUBE_PROMETHEUS_STACK_CHART_VERSION" -n monitoring --create-namespace \
  -f "$KIND_DIR/kube-prometheus-stack-values.yaml" --wait --timeout 10m >/dev/null

kubectl get nodes -o wide
log "platform ready. Next: make deploy-local"
