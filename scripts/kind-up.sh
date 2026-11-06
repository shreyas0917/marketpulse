#!/usr/bin/env bash
# Create the local kind platform: 3-node cluster, Calico (enforces NetworkPolicies),
# metrics-server, ingress-nginx and kube-prometheus-stack. Idempotent: re-running upgrades
# the add-ons in place. Delete with scripts/kind-down.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND_DIR="$ROOT/deploy/kind"
CLUSTER=marketpulse
case "$(docker info --format '{{.Architecture}}')" in
  aarch64|arm64) NODE_ARCH=arm64 ;;
  *) NODE_ARCH=amd64 ;;
esac
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

# load_image IMAGE: copy one image from the host's Docker into every kind node, downloading it once.
# `kind load docker-image` cannot be used: it imports with --all-platforms, and Docker Desktop's
# containerd store only holds the host platform's layers of a multi-platform image ("content
# digest not found"). Instead the image is saved once under a plain tag and imported into each
# node for the node's platform only; for a digest-pinned reference (tag@sha256:...) the digest name
# is added so the kubelet resolves the exact reference the chart uses.
load_image() {
  local img="$1" repo digest tag tar node
  repo="${img%@*}"; repo="${repo%:*}"
  digest=""; [[ "$img" == *@sha256:* ]] && digest="${img##*@}"
  tag="${img%@*}"; [[ "${tag##*/}" == *:* ]] || tag="$repo:preload"
  docker pull -q "$img" >/dev/null 2>&1 || return 1
  [[ "$tag" == "$img" ]] || docker tag "$img" "$tag" >/dev/null 2>&1 || return 1
  tar="$(mktemp)"
  docker save "$tag" -o "$tar" || { rm -f "$tar"; return 1; }
  for node in $(kind get nodes --name "$CLUSTER"); do
    (
      docker exec -i "$node" ctr -n k8s.io images import --platform "linux/$NODE_ARCH" \
        --digests --snapshotter overlayfs - < "$tar" >/dev/null 2>&1 \
        && { [[ -z "$digest" ]] || docker exec "$node" ctr -n k8s.io images tag --force \
          "$tag" "$repo@$digest" >/dev/null 2>&1; } \
        && docker exec "$node" crictl inspecti "$img" >/dev/null 2>&1
    ) &
  done
  local failed=0 pid
  for pid in $(jobs -p); do wait "$pid" || failed=$((failed + 1)); done
  rm -f "$tar"
  (( failed == 0 ))
}

# preload_chart_images RELEASE CHART VERSION NAMESPACE VALUES
# Pre-pull every image a chart renders so the Helm --wait budget is not spent on cold image pulls
# (the ingress-nginx pull alone exceeded 5 minutes on a fresh machine). Best effort: an image that
# cannot be preloaded is simply pulled by the kubelet during the install.
preload_chart_images() {
  local images img
  images=$(helm template "$1" "$2" --version "$3" -n "$4" -f "$5" 2>/dev/null \
    | grep -oE '(image: *"?[^" ]+"?|--prometheus-config-reloader=[^" ]+)' \
    | sed -E 's/^image: *"?//; s/"$//; s/^--prometheus-config-reloader=//' | sort -u)
  for img in $images; do
    if load_image "$img"; then log "  preloaded $img"; else log "  could not preload $img (kubelet will pull it)"; fi
  done
}

log "installing metrics-server $METRICS_SERVER_CHART_VERSION"
helm upgrade --install metrics-server metrics-server/metrics-server \
  --version "$METRICS_SERVER_CHART_VERSION" -n kube-system \
  -f "$KIND_DIR/metrics-server-values.yaml" --wait --timeout 5m >/dev/null

log "preloading ingress-nginx images"
preload_chart_images ingress-nginx ingress-nginx/ingress-nginx "$INGRESS_NGINX_CHART_VERSION" \
  ingress-nginx "$KIND_DIR/ingress-nginx-values.yaml"
log "installing ingress-nginx $INGRESS_NGINX_CHART_VERSION"
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --version "$INGRESS_NGINX_CHART_VERSION" -n ingress-nginx --create-namespace \
  -f "$KIND_DIR/ingress-nginx-values.yaml" --wait --timeout 10m >/dev/null

log "preloading kube-prometheus-stack images"
preload_chart_images kps prometheus-community/kube-prometheus-stack \
  "$KUBE_PROMETHEUS_STACK_CHART_VERSION" monitoring "$KIND_DIR/kube-prometheus-stack-values.yaml"
log "installing kube-prometheus-stack $KUBE_PROMETHEUS_STACK_CHART_VERSION"
helm upgrade --install kps prometheus-community/kube-prometheus-stack \
  --version "$KUBE_PROMETHEUS_STACK_CHART_VERSION" -n monitoring --create-namespace \
  -f "$KIND_DIR/kube-prometheus-stack-values.yaml" --wait --timeout 10m >/dev/null

kubectl get nodes -o wide
log "platform ready. Next: make deploy-local"
