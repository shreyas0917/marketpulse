# Experiment 5: market-open burst (09:15 IST simulation)

| | |
|---|---|
| Run date | 2026-10-26, 09:33:01 → 09:43:30 UTC (burst 09:34:02 → 09:39:12, 5 min) |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, API Rollout 2 replicas (HPA 2–6 at 60 % CPU), worker 2 replicas (HPA 2–4 at 70 %), `REDIS_TIMEOUT_MS=50` |
| Script | `make drill N=5` → `scripts/drills/5-market-open-burst.sh` (`load-tests/burst.js` + producer `POST /burst`) |
| Load during run | quotes 30 req/s → **300 req/s** for 5 min → 30 req/s; producer 20 → **200 events/s** for the same 5 min |

## Hypothesis

A 10× burst on both the read path (quotes) and the write path (events) makes the API and worker
HPAs scale out; ingestion lag spikes while the workers catch up and the backlog drains quickly
after the burst; the HPAs scale back when it is over.

## Setup

Quiet baseline: queue depth 0, lag p99 ≈ 50 ms, 2 API and 2 worker replicas, no faults.

## Steps

1. k6 `ramping-arrival-rate`: 1 min at 30 req/s, ramp to 300 req/s in 10 s, hold 5 min, ramp down,
   4 min at 30 req/s. At +60 s k6 also calls the producer's `/burst?multiplier=10&duration=300`.
2. Every 10 s sample both HPAs, `queue_depth`, lag p99 (1 m) and the producer's target rate.
3. Export Prometheus ranges and the k6 summary; derive peak/drain figures from the samples.

## Expected

API HPA to 6, worker HPA to 4, lag over 1 s briefly, queue drains within seconds of the burst end,
HPAs back to minimum afterwards. Some latency SLO burn is expected at 10×; availability should hold.

## Observed

### Scaling and ingestion

| Measure | Value |
|---|---|
| API HPA | 2 → desired **4** at +37 s (09:34:39) → **6/6** by +89 s (09:35:31); back to 2 at 09:42:06 (**≈ 174 s** after the burst ended) |
| Worker HPA | 2 → **4/4** by +89 s; back to 2 at 09:40:32 (**≈ 80 s** after the burst ended) |
| Queue depth | baseline 0 → peak **347** events |
| Ingestion lag p99 (1 m) | baseline 0.05 s → peak **1.90 s** at 09:35:31 (the moment the 3rd/4th workers were starting), then 0.2–0.8 s for the rest of the burst |
| `sli:ingestion_lag:error_ratio_rate5m` | peak **0.061** (6.1 % of events over 1 s); `IngestionLagFastBurn` did **not** fire |
| Backlog drain after the burst | queue back to ≤ 5 within **7 s** |
| Producer | 200 events/s confirmed by `producer_target_rate` for the whole burst |

### The read path under 10×

| Measure | Value |
|---|---|
| k6 | **101,877** requests; p50 4.8 ms, **p99 1,336 ms**, max 3.56 s; 0 transport errors |
| Peak quote-request rate | 300.2 req/s |
| p99 quote latency (1 m, Prometheus) | peak **≈ 1.0 s** at 09:35:01 |
| `sli:latency:error_ratio_rate5m` | peak **0.26** — 26 % of requests slower than 250 ms |
| `LatencyFastBurn` 6h/30m (page) | **fired 09:35:01 → 09:37:01** |
| 5xx | **1,310 × `503 QUOTE_UNAVAILABLE`** (1.28 % of requests), no 500s |
| `quote_responses_total` over the burst | store **87,322** · local_cache **12,658** · unavailable **1,318** |
| `AvailabilityFastBurn` 1h/5m | fired 09:35:11 → 09:39:51 on the 503s (the 6h/30m pair was still firing from experiment 4's outage and is not attributable to this run) |
| `FreshnessFastBurn` 6h/30m | fired from 09:34:51: the 12.6 % of answers served from the local cache were older than 2 s |
| `QuoteStoreDown` | one 10-second blip (09:38:41–09:38:51): Redis calls timing out, not Redis down |

What happened on the read path: at 300 req/s on a shared 8-vCPU VM, Redis round-trips exceeded the
**50 ms call timeout** often enough that the API entered degraded mode — 12.6 % of responses were
stale local-cache serves (fresh enough to answer, old enough to burn freshness), and the four
**newly scaled pods started with empty caches**, so their timed-out calls became `503`s. The burst
therefore paged on latency and (briefly) availability, and degraded mode hid part of the slowdown
behind stale answers. Ingestion, by contrast, scaled cleanly and never paged.

### Time to detect / recover

| Measure | Value |
|---|---|
| HPA reaction | 37 s to first scale-up decision, 89 s to full scale |
| Latency page | 59 s after the burst began |
| Recovery | queue drained 7 s after the burst; `LatencyFastBurn` cleared 09:37:01 while the burst was still running (once 6 replicas were serving); HPAs back to minimum 80 s / 174 s after the burst |

## Evidence

[`evidence/drill5-samples.csv`](evidence/drill5-samples.csv) (10-second samples),
[`evidence/drill5-result.json`](evidence/drill5-result.json),
[`evidence/drill5.json`](evidence/drill5.json) (HPA current/desired, queue depth, lag p99, request
rate, p99 latency, ingestion-lag and latency SLIs, full `ALERTS` series),
[`evidence/drill5-k6-summary.json`](evidence/drill5-k6-summary.json).

## Follow-ups

- **Warm the cache on scale-out:** a new API pod could prefetch the configured symbols at startup so
  a slow Redis never turns into `503`s on fresh replicas.
- **Scale earlier:** 60 % CPU target with a 15 s metrics window took 37 s to react; the first minute
  of a market open is exactly when it matters. A lower target or a scheduled pre-scale before 09:15
  IST would cut the latency page.
- **Redis timeout vs. load:** 50 ms is right for freshness but tight for a shared host; the stale
  serves show the timeout firing on a Redis that was slow, not down. On AWS, a dedicated ElastiCache
  node changes this picture.
- The ticket-level slow-burn alerts were already firing before this run (earlier disruptions) and
  are not evidence from this experiment.
