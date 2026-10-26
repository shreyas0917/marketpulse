# Runbook: ingestion stalled

**Alert:** `IngestionStalled` (severity `page`): `max(queue_oldest_event_age_seconds) > 10` for 2 minutes.

## What it means

The oldest message in the main queue has waited more than 10 s for 2 minutes: the worker is stopped,
crashed, hung, or far behind. The ingestion-lag SLI cannot see this (no events processed means no
ratio), which is why this separate alert exists. The gauge is exported by both the producer and
the worker, so it keeps working when the worker is gone.

## Impact

No new prices reach the store. Quotes age past 2 s and the freshness SLI burns within seconds.

## Triage

1. Is the worker running?
   - compose: `docker compose ps worker`
   - kind: `kubectl -n marketpulse get pods -l app.kubernetes.io/component=worker`
2. Is it scraping? `up{job=~".*worker.*"}`
3. Is it processing anything? `sum(rate(events_processed_total[1m]))`
4. Liveness: `/health/live` fails when the processing loop has not polled for 30 s
   (`WORKER_HEARTBEAT_TIMEOUT_SECONDS`), so Kubernetes restarts a hung worker automatically.

## Mitigate

- **Stopped:** compose `docker compose up -d worker`; kind `kubectl -n marketpulse rollout restart deploy/marketpulse-worker`.
- **Crash-looping:** read the last logs (`docker compose logs worker --tail 100` /
  `kubectl -n marketpulse logs deploy/marketpulse-worker --previous`). If a payload crashes the
  process, move the offending message to the DLQ by hand:
  `redis-cli XRANGE events - + COUNT 5`, inspect, then `XADD events-dlq * data '<body>' reason manual`
  and `XACK events workers <id>` + `XDEL events <id>`.
- **Behind:** scale out (see [ingestion-lag-burn](ingestion-lag-burn.md)).

## Verify recovery

`max(queue_oldest_event_age_seconds)` back under 1 s and `queue_depth` draining. Messages left
pending by a dead consumer are reclaimed with `XAUTOCLAIM` after `RETRY_MIN_IDLE_MS`.
