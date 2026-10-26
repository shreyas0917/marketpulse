#!/usr/bin/env bash
# Run tests/smoke against kind: the API through the ingress (127.0.0.1:18080, Host header),
# worker and producer through port-forwards.
set -euo pipefail

cd "$(dirname "$0")/.."
NS=marketpulse
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

kubectl -n "$NS" port-forward deploy/marketpulse-worker 29100:9100 >/dev/null 2>&1 &
pids+=($!)
kubectl -n "$NS" port-forward deploy/marketpulse-producer 29200:9200 >/dev/null 2>&1 &
pids+=($!)
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null localhost:29100/health/live && curl -fsS -o /dev/null localhost:29200/health/live; then
    break
  fi
  sleep 1
done

SMOKE_API_URL=http://127.0.0.1:18080 \
SMOKE_HOST_HEADER=marketpulse.localtest.me \
SMOKE_WORKER_URL=http://localhost:29100 \
SMOKE_PRODUCER_URL=http://localhost:29200 \
  uv run pytest tests/smoke -p no:cacheprovider "$@"
