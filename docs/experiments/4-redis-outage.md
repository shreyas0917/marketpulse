# Experiment 4: stop Redis (quote store and queue)

| | |
|---|---|
| Run date | 2026-10-26, 09:21:17 → 09:31:30 UTC (Redis down 09:22:22 → 09:25:01, **156 s**) |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, API Rollout stable `9884c6f64` (2 replicas), `STALE_QUOTE_MAX_AGE_SECONDS=30` |
| Script | `make drill N=4` → `scripts/drills/4-redis-outage.sh` (pauses Argo CD auto-sync for the duration, restores it on exit) |
| Load during run | k6 steady 30 req/s for 10 min through the ingress; an extra probe every 0.5 s for RELIANCE/TCS/INFY recording status, `stale`, `age_seconds`, `source` |

## Hypothesis

When Redis disappears: `quote_store_up` drops and `QuoteStoreDown` fires; API pods keep serving
cached quotes with `stale: true` for at most 30 s, then answer `503 QUOTE_UNAVAILABLE`; availability
and freshness burn; the pods **stay Ready** (readiness ignores Redis); a pod restarted during the
outage has an empty cache and returns 503 immediately; everything recovers when Redis returns.

## Setup

Rollout Healthy, 2 API pods Ready, no faults. Argo CD auto-sync paused so self-heal does not undo
the scale-to-zero (re-enabled by the cleanup trap).

## Steps

1. Start load; after 60 s start the 0.5 s probe through the ingress.
2. `kubectl scale deploy/marketpulse-redis --replicas=0`.
3. 5 s later delete one API pod; when its replacement is Ready, probe it directly for 10 s
   (cold-cache check).
4. Wait for `QuoteStoreDown`; hold the outage to 150 s; snapshot the alerts.
5. Scale Redis back to 1; wait for `QuoteStoreDown` to resolve; let the load finish.
6. Derive the degraded-mode phases from the probe CSV; export Prometheus ranges and alerts.

## Expected

Stale `200`s only within 30 s of the stop; `503` from then on; cold pod 503 from its first request;
2 Ready pods throughout; `QuoteStoreDown` after its 1-minute `for`; availability pages; fresh
quotes within seconds of restore.

## Observed

### Degraded-mode phases (ingress probe, 1,518 samples)

| Phase | Measured |
|---|---|
| Stale `200` (`stale: true`, `source: local_cache`) served | **99 responses**, last one **29.9 s** after the stop; max stale `age_seconds` **29.79 s** (never above the 30 s cap) |
| First `503 QUOTE_UNAVAILABLE` | **15.1 s** after the stop — from the cold replacement pod (Ready at 13 s) while the other pod still served stale 200s: the documented *per-pod cache* limitation, two pods answering differently for the same symbol |
| Every response `503` | from **30.0 s** after the stop until restore |
| First fresh `200` (`stale: false`) after Redis returned | **4.6 s** |

### Cold-cache limitation

The pod restarted 6 s into the outage became Ready 13 s in and answered **65 of 65** direct probes
with `503 QUOTE_UNAVAILABLE`: an empty cache cannot serve a stale quote, exactly as documented in
[SLOs](../slos.md#degraded-mode-and-its-limitations).

### Pods and alerts

| Measure | Value |
|---|---|
| Ready API pods during / at end of outage | **2 / 2** — nothing left the load balancer |
| `QuoteStoreDown` (warning, `for: 1m`) | `pending` 14 s after stop; **fired at +67 s** (`startsAt` 09:23:29); resolved **16 s** after Redis was Ready |
| `AvailabilityFastBurn` 6h/30m (page) | **fired at +42 s** (09:23:04) |
| `AvailabilityFastBurn` 1h/5m (page) | fired at +52 s; cleared 09:29:56, ~5 min after restore (5 m window clean) |
| Peak `sli:availability:error_ratio_rate5m` | **0.66** (09:25:06) |
| Peak `sli:freshness:error_ratio_rate5m` | **0.29** (09:23:06) |
| `FreshnessFastBurn` | **did not fire**: the 5 m ratio exceeded 14.4 % but the 1 h ratio never did, because stale serving is capped at 30 s. The multi-window pair behaved as designed; a 30-second freshness incident is not a page. |
| `IngestionStalled` | did not fire: the queue lives in the same Redis, so the producer could not enqueue and no backlog aged |
| `TargetDown` | `pending` for one evaluation during the pod restart, never fired |
| Ticket-level `*SlowBurn` (3d/6h) | already firing before the drill from the day's earlier host-level disruptions; not attributable to this run |
| k6 | 18,101 requests; **4,122** failed `not 5xx` checks (≈ 22.8 %, all `503` during the outage); p99 60 ms (503s are fast) |
| **Time to detect** | 42 s (page), 67 s (`QuoteStoreDown`) |
| **Time to recover** | Redis Ready 3 s after the scale-up; fresh quotes 4.6 s later; `QuoteStoreDown` resolved +16 s; availability page cleared ~5 min later (window) |

## Evidence

[`evidence/drill4-probe-ingress.csv`](evidence/drill4-probe-ingress.csv) (every probe response),
[`evidence/drill4-probe-cold-pod.csv`](evidence/drill4-probe-cold-pod.csv),
[`evidence/drill4-phases.json`](evidence/drill4-phases.json),
[`evidence/drill4-timeline.json`](evidence/drill4-timeline.json),
[`evidence/drill4.json`](evidence/drill4.json) (`quote_store_up`, responses by source, requests by
status, both SLIs, Ready pods, and the full `ALERTS` series),
`drill4-storedown-firing.json`, `drill4-storedown-resolved.json`,
`drill4-AvailabilityFastBurn-firing.json`, [`evidence/drill4-k6-summary.json`](evidence/drill4-k6-summary.json).

## Follow-ups

- Degraded mode did what it promises (30 s of stale answers, never older) and nothing more; for a
  store outage longer than 30 s the user outcome is a clean `503` with `Retry-After`. Raising
  `STALE_QUOTE_MAX_AGE_SECONDS` trades freshness for availability and should be a product decision.
- The per-pod split (one pod 200-stale, one pod 503 at 15 s) is confusing for clients; a shared
  cache tier or a Redis replica would remove it, at the cost of the simplicity this design chose.
- See the [postmortem](../incidents/2026-10-26-redis-outage.md).
