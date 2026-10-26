# Experiment 2: fail readiness on one pod

| | |
|---|---|
| Run date | 2026-10-26, 09:12:51 → 09:17:04 UTC |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, API Rollout stable `9884c6f64`, 2 replicas |
| Script | `make drill N=2` → `scripts/drills/2-fail-readiness.sh` |
| Load during run | k6 steady 30 req/s for 4 min through the ingress |

## Hypothesis

A pod whose readiness probe fails (`FAULT_READINESS_FAIL=true`) is never added to the Service
endpoints, receives no traffic, and users see no errors. Liveness still passes, so it is not
restarted (readiness ≠ liveness).

## Setup

A copy of a running API pod is started with the same labels (so the stable Service and the
PodMonitor select it) and `FAULT_READINESS_FAIL=true`. It is owned by a throwaway ConfigMap so the
Rollout's ReplicaSet neither adopts nor deletes it.

## Steps

1. Start 30 req/s steady load; wait 30 s.
2. Create `marketpulse-api-unready` from the live pod spec plus the fault.
3. After 2 min, record its readiness, restart count, endpoint membership, the quote requests it
   served (Prometheus, by `pod` label) and its `fault_injection_active` gauge.
4. Let the load finish and read the k6 summary.

## Expected

`ready=false`, `restarts=0`, not in any ready endpoint, 0 quote requests served,
`fault_injection_active{type="readiness_fail"} == 1`, 0 failed k6 checks.

## Observed

| Measure | Value |
|---|---|
| Pod status after 121 s | `Running`, **ready=false**, **restarts=0** |
| Ready endpoint entries for its IP | **0** |
| Quote requests served by the pod | **0** (`increase(http_requests_total{pod="marketpulse-api-unready", route="/v1/quotes/{symbol}"}[3m])`) |
| `fault_injection_active{type="readiness_fail"}` on the pod | **1** (the dashboard shows the fault) |
| k6 requests / failed checks | 7,301 / **0**; p99 12.8 ms |
| Time to detect | n/a — no alert is expected; the pod is simply not Ready |
| Time to recover | n/a — no impact; the pod was removed at cleanup |

The pod was scraped (its metrics carry the fault gauge) but never served a quote: readiness kept it
out of the load balancer while liveness kept it alive, exactly the split the design relies on
(readiness deliberately ignores Redis for the same reason, see [SLOs](../slos.md)).

## Evidence

[`evidence/drill2.json`](evidence/drill2.json) (requests by pod, SLI, fault gauge by pod,
`kube_pod_status_ready` for the pod), [`evidence/drill2-pod.json`](evidence/drill2-pod.json),
[`evidence/drill2-k6-summary.json`](evidence/drill2-k6-summary.json).

## Follow-ups

- A pod that is `Running` but never Ready is invisible to users and to the SLIs. The Platform
  dashboard's "Pods not ready" stat shows it; an alert on `kube_pod_status_ready{condition="false"}`
  held for several minutes would make a stuck rollout or a bad config visible without waiting for
  the Rollout's own `progressDeadlineSeconds`.
