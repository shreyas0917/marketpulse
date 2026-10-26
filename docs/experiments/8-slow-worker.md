# Experiment 8: slow worker (injected processing latency)

| | |
|---|---|
| Run date | 2026-10-26, 09:44:17 → 09:59:36 UTC (fault 09:44:32 → 09:59:36, **15 min**) |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, 2 worker replicas (CPU HPA 2–4), producer 20 events/s, `FAULT_WORKER_EXTRA_LATENCY_MS=500` |
| Script | `make drill N=8` → `scripts/drills/8-slow-worker.sh` (pauses Argo CD auto-sync, sets the env on the worker Deployment, restores by re-enabling auto-sync so self-heal reverts it) |
| Load during run | normal producer traffic only; no k6 |

## Hypothesis

With 500 ms added to every event, the two workers fall far behind 20 events/s; ingestion lag
exceeds 1 s and **`IngestionLagFastBurn` pages within 5 minutes**; the backlog drains after the
fault is removed.

## Setup

Lag p99 49 ms, queue empty, 2 workers, no faults, no ingestion alerts except the pre-existing
ticket-level `IngestionLagSlowBurn` from the day's earlier disruptions.

## Steps

1. `kubectl set env deploy/marketpulse-worker FAULT_WORKER_EXTRA_LATENCY_MS=500`; wait for the rollout.
2. Wait up to 15 min for `IngestionLagFastBurn`.
3. (Script as written: record `IngestionStalled`, hold the fault, restore, measure the drain.)
4. Export Prometheus ranges and the alert timeline.

## Observed

**The hypothesis was wrong, and the run shows why.**

| Measure | Value |
|---|---|
| Events written per second during the fault | **1.99** (2 workers × 2 events/s) against 20/s arriving |
| Queue depth | 0 → **16,279** at the end of the fault |
| Oldest queued event age | peak **814 s** |
| Ingestion lag p99 (1 m) | pinned at **30 s** — the histogram's largest bucket; the real p99 was higher |
| `sli:ingestion_lag:error_ratio_rate5m` | **1.00** from a few minutes in (every event written was late) |
| `…rate30m` / `…rate1h` / `…rate6h` at +12 min | **0.034 / 0.024 / 0.161** |
| `IngestionLagFastBurn` during the fault | **did not fire** in 15 min (1h/5m needs the 1 h ratio > 0.144; 6h/30m needs the 30 m ratio > 0.06) |
| `IngestionStalled` (gap alert) | pending 09:44:50, **firing 09:46:50 — 2 min 18 s** after the fault started; resolved ≈ 10:00:00 |
| Worker HPA during the fault | stayed at **2** — a worker sleeping 500 ms per event uses little CPU |
| Restore (auto-sync re-enabled → self-heal reverted the env) | 09:59:36 |
| Backlog drained (≤ 20) | **10:00:10**, 34 s after restore |
| `IngestionLagFastBurn` after restore | **fired 10:00:10 → 10:05:40** (6h/30m pair: 30 m ratio peaked at 0.144, 6 h at 0.19) |
| Worker HPA after restore | scaled to 4 during the drain burst (CPU finally rose) |
| **Time to detect** | 2 min 18 s via `IngestionStalled`; the burn-rate page never came while the fault was active |
| **Time to recover** | 34 s from restore to an empty queue |

Why the burn alert stayed quiet: burn rate is computed from the *rate of bad events*. A throttled
worker writes almost nothing (2 events/s instead of 20), so even though 100 % of what it writes is
late, the long windows are dominated by the hour's earlier good events and never cross 14.4 % (1 h)
or 6 % (30 m). The budget was not being burned quickly — it was being *deferred* into a growing
backlog. The moment the fault was removed, 16,000 late events were written in 30 seconds and the
deferred burn landed all at once, which is when the page fired.

This is precisely the gap the spec's "gaps a ratio SLI can't see" table anticipates:
"worker stalled → total ≈ 0 → no burn-rate alert"; `IngestionStalled` on
`queue_oldest_event_age_seconds` exists for it and did its job in 2 min 18 s, inside the 5-minute
target. The spec's expected outcome for this drill ("ingestion-lag burn-rate alert fires within
target") is therefore recorded as **not met by the burn alert, met by the gap alert**.

### Run note

The script's 15-minute wait for `IngestionLagFastBurn` returned non-zero and, under `set -e`, the
drill exited at that point (exit code 1) before its own stall check, restore timing and export. The
cleanup trap still re-enabled Argo CD auto-sync, and self-heal reverted the worker env within
seconds, so the system was restored. The numbers above come from the same run: the per-window ratios
were captured live during the fault (`drill8-live-windows.json`) and the ranges and alert timeline
were exported from Prometheus immediately afterwards (`drill8.json`). The script was then fixed to
record a non-firing alert as a result and continue (see `scripts/drills/8-slow-worker.sh`).

## Evidence

[`evidence/drill8.json`](evidence/drill8.json) (lag p99, queue depth, oldest age, all four
ingestion-lag windows, write rate, worker HPA replicas, freshness SLI, full `ALERTS` series
09:43–10:06), [`evidence/drill8-live-windows.json`](evidence/drill8-live-windows.json),
[`evidence/drill8-lagburn-firing.json`](evidence/drill8-lagburn-firing.json) (the timed-out wait).

## Follow-ups

- **Scale workers on queue depth, not CPU** (the spec's KEDA extension): CPU never moved while the
  backlog grew to 16 k.
- Keep `IngestionStalled` as a page: it is the only alert that fired while the fault was active.
  Consider lowering its 10 s threshold for market hours.
- Extend `ingestion_lag_seconds` buckets above 30 s (e.g. 60, 300) so a backlog's true p99 is
  measurable; the SLO boundary at 1 s is unaffected.
- A burn-rate alert on *deferred* budget (e.g. `queue_depth × expected lag`) would page earlier
  than the drain burst; the stall alert is the pragmatic version of that.
