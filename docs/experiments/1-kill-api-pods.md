# Experiment 1: kill API pods

| | |
|---|---|
| Run date | 2026-10-26, 09:07:59 → 09:12:15 UTC |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, API Rollout stable `9884c6f64`, 2 replicas |
| Script | `make drill N=1` → `scripts/drills/1-kill-api-pods.sh` |
| Load during run | k6 steady 30 req/s for 4 min through the ingress |

## Hypothesis

With 2 replicas behind the ingress, force-killing one API pod at a time (`--grace-period=0
--force`, i.e. SIGKILL with no drain) is replaced quickly and does **not** breach the availability
SLO.

## Setup

Rollout `Healthy`, 2 Ready API pods, no faults, no alerts firing.

## Steps

1. Start 30 req/s steady load; wait 60 s.
2. Force-kill one API pod; wait until the Ready count is back to 2.
3. Wait 45 s; force-kill the other pod; wait for Ready again.
4. Let the load finish; read the SLI, 5xx count, alert state and the k6 summary.

## Expected

Each pod replaced within seconds; `sli:availability:error_ratio_rate5m` stays ≈ 0; no
`AvailabilityFastBurn`; no 5xx.

## Observed

| Measure | Value |
|---|---|
| Replacement time, round 1 | **6 s** (pod `…-6lg82` killed 09:09:01, Ready count back to 2 at 09:09:07) |
| Replacement time, round 2 | **7 s** (pod `…-djrnt`, 09:09:52 → 09:09:59) |
| 5xx responses in the window | **0** (`http_requests_total{status=~"5.."}`) |
| `sli:availability:error_ratio_rate5m` | **0** |
| `AvailabilityFastBurn` | not firing |
| k6 requests | 7,277; p50 3.3 ms, p99 48.5 ms, **max 5,000.7 ms** (the k6 5 s timeout) |
| k6 check `not 5xx` | 7,177 passed, **0 failed** |
| k6 check `no transport error` | 7,126 passed, **51 failed** (0.70 % of requests) |
| Time to detect | n/a — nothing to detect from the SLI's point of view (see below) |
| Time to recover | 6–7 s per kill (pod replacement) |

**The SLO was not breached, but users were affected.** All 51 failed checks are transport-level
errors (connection reset or timeout), none are 5xx. SIGKILL with no grace period drops the
in-flight connections of the dying pod, and for a few hundred milliseconds the ingress may still
route new connections to it. Those requests never reach a healthy API process, so the API-side
`http_requests_total` never records them: the availability SLI measured a perfect window while
0.7 % of client requests failed. Compare the M4 check that deleted a pod *gracefully* (default
grace period, `preStop` 5 s sleep): 1,901 requests, 0 failures.

## Evidence

[`evidence/drill1.json`](evidence/drill1.json) (Prometheus range queries: SLI, requests by status,
API targets up, ready API pods), [`evidence/drill1-rounds.jsonl`](evidence/drill1-rounds.jsonl)
(kill and replacement timestamps), [`evidence/drill1-k6-summary.json`](evidence/drill1-k6-summary.json)
(per-check pass/fail counts).

## Follow-ups

- **Blind spot:** the availability SLI is measured at the API, so failures that never reach a
  healthy API process (resets at the edge) are invisible. Add an edge-side signal: ingress-nginx
  upstream 5xx/connection-error metrics or a blackbox/k6 probe SLI measured from the client side.
- Keep graceful termination as the normal path (`preStop` + grace period); an ungraceful kill is a
  node/OOM-level failure, and the drill shows its real cost (≈ 25 requests per kill at 30 req/s).
