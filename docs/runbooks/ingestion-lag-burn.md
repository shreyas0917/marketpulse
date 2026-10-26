# Runbook: ingestion-lag error-budget burn

**Alerts:** `IngestionLagFastBurn` (page; 1h/5m at 14.4×, 6h/30m at 6×), `IngestionLagSlowBurn` (ticket; 3d/6h at 1×)
**SLO:** 99 % of events are written within 1 s of their event timestamp (budget 1 %).
Dead-lettered events count as bad. See [SLOs](../slos.md).

## What it means

Events are taking longer than 1 s from publication to being written as the latest quote, or are
being dead-lettered.

## Triage

1. **MarketPulse / Ingestion** dashboard: *Ingestion lag percentiles*, *Queue depth*, *Retries and
   dead-letters*.
2. Dead-letters or slowness?
   - `sum(rate(events_processed_total{result="dead_lettered"}[5m]))` > 0 → see
     [dead-letter-queue](dead-letter-queue.md).
   - p99 lag high but no dead-letters → the worker is slow or under-provisioned.
3. Fault injected? `fault_injection_active{type=~"worker_.*"} == 1`
4. Backlog growing? `deriv(max(queue_depth)[5m:])` > 0 means producers outpace the worker.
5. Burst in progress? `max(producer_burst_active) == 1` (market-open simulation).

## Diagnose and mitigate

| Cause | Mitigation |
|---|---|
| `FAULT_WORKER_EXTRA_LATENCY_MS` / `FAULT_WORKER_FAIL_RATE` set | Remove it (compose: recreate `worker`; kind: revert in Git). |
| Not enough workers for the rate | Scale: compose `docker compose up -d --scale worker=3`; kind `kubectl -n marketpulse scale deploy/marketpulse-worker --replicas=3` (then make it permanent in Git values). |
| Redis slow | `docker compose exec redis redis-cli --latency` / `kubectl -n marketpulse exec deploy/marketpulse-redis -- redis-cli --latency` |
| Retry storm (many `retried`) | Check worker logs for the `reason` field: compose `docker compose logs worker \| grep event_retry_scheduled`. |

## Verify recovery

`sli:ingestion_lag:error_ratio_rate5m < 0.144`; `queue_depth` returns to near zero.

<a id="fast-burn"></a>
## Fast burn (page)

Act now: find out if it is slowness or dead-lettering (Triage step 2) and mitigate.

<a id="slow-burn"></a>
## Slow burn (ticket)

Look for steady small dead-letter counts (bad producer data) or a lag p99 that sits near 1 s.
