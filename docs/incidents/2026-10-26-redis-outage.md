# Postmortem: quote store (Redis) outage

> **Status:** Draft — for owner review
> Blameless format: describe what the system and the process allowed to happen, not who did it.

| | |
|---|---|
| Date | 2026-10-26 |
| Environment | kind (1 control plane + 2 workers), 2 API replicas, single Redis (no persistence) used as both quote store and event queue |
| Duration (impact) | 09:22:37 → 09:25:06 UTC (first stale answer at +15 s, all-503 from +30 s, fresh quotes 4.6 s after restore); Redis itself was down 09:22:22 → 09:25:01 (156 s) |
| Severity | page (`AvailabilityFastBurn`) |
| SLOs affected | availability (peak 5 m error ratio 0.66), freshness (peak 0.29, no page) |
| Error budget consumed | ≈ **5 % of the 30-day availability budget** — 4,122 measured `503`s against a budget of ≈ 77,760 bad requests at the run's 30 req/s held for 30 days (the rate is the drill's, not a production figure) |
| Detection | `AvailabilityFastBurn` 6h/30m at +42 s; `QuoteStoreDown` at +67 s |
| Authors | project owner (drill run by the build) |

## Summary

Redis, the single source of truth for quotes and the local queue, was scaled to zero for 156 s.
For the first 30 s each API pod served the last price it had seen for each symbol, flagged
`stale: true`; a pod that was restarted during the outage had nothing cached and answered `503`
from its first request. From 30 s on every pod answered `503 QUOTE_UNAVAILABLE` with
`Retry-After`, as designed — no price older than 30 s was ever served. Both pods stayed Ready, so
the ingress kept routing to them and users received controlled errors rather than gateway
failures. Availability paged 42 s in. Fresh quotes resumed 4.6 s after Redis returned.

## Impact

- 4,122 of 18,101 requests during the 10-minute load window returned `503` (22.8 %); all of them
  fell inside the 156-second outage.
- 99 responses were served stale (≤ 29.8 s old) in the first 30 s.
- Ingestion stopped entirely (the queue is the same Redis), but with no backlog to age, the stall
  alert correctly stayed quiet; events published during the outage were lost at the producer
  (`event_publish_failed`), which is acceptable for a synthetic feed and would not be for a real one.

## Timeline (UTC)

| Time | Event |
|---|---|
| 09:22:22 | Redis scaled to 0 |
| 09:22:28 | One API pod deleted (cold-cache check) |
| 09:22:35 | Replacement pod Ready (13 s in) — empty cache |
| 09:22:36 | `QuoteStoreDown` pending (first failed store call) |
| 09:22:37 | First `503` seen at the ingress (+15.1 s): requests landing on the cold pod |
| 09:22:52 | Last stale `200` (+29.9 s); from here every pod answers `503` |
| 09:23:04 | **`AvailabilityFastBurn` (6h/30m) fires** — page, +42 s |
| 09:23:14 | `AvailabilityFastBurn` (1h/5m) fires, +52 s |
| 09:23:29 | `QuoteStoreDown` fires (+67 s, after its 1 m `for`) |
| 09:24:59 | Redis scaled back to 1 |
| 09:25:01 | Redis Ready |
| 09:25:06 | First fresh `200` (+4.6 s after Ready) — **impact ended** |
| 09:25:17 | `QuoteStoreDown` resolved |
| 09:29:56 | `AvailabilityFastBurn` 1h/5m cleared (5 m window clean); the 6h/30m pair cleared later with its window |

**Time to detect:** 42 s (page). **Time to recover:** 2 min 49 s from Redis stop to fresh quotes,
of which 2 min 37 s was the deliberate hold; **7.6 s** from "scale Redis up" to fresh quotes.

## Root cause

A single Redis instance with no replica, serving both the quote store and the queue. Losing it
removes both the source of truth and the ability to ingest new prices. Degraded mode is a
30-second buffer by design, not a mitigation for an outage of minutes.

## What went well

- The failure was contained to the controlled behaviour described in [SLOs](../slos.md): stale
  for ≤ 30 s, then `503` + `Retry-After`. The documented limitations (per-pod cache, cold cache
  after restart, 30 s ceiling) were all observed exactly as written.
- Readiness ignoring Redis kept both pods in rotation; there were no load-balancer `502`s.
- Paging was fast (42 s) and `QuoteStoreDown` pointed straight at the cause with its runbook.
- Recovery after restore was automatic and quick (4.6 s).

## What went wrong

- Nothing in the platform prevents the outage; it only shapes the failure. There is no Redis
  replica and no failover.
- For 15 s two pods gave different answers for the same symbol (one stale `200`, one `503`),
  which a client cannot distinguish from flapping.
- Freshness did not page. That is correct for a 30-second stale phase, but it means a freshness
  problem has to persist for many minutes before anyone is told; the `QuoteStoreDown` warning is
  the only early signal.

## Where we got lucky

- The outage was short and announced; events lost at the producer did not matter. A real feed would
  need a durable queue (the SQS backend on AWS) separate from the store.

## Action items

| Action | Type | Owner | Status |
|---|---|---|---|
| Separate the queue from the quote store (SQS on AWS is already designed; locally a second Redis would do) so ingestion survives a store outage | prevent | owner | proposed |
| Add a Redis replica with automatic failover (ElastiCache on AWS; Sentinel or a replica + readiness gate locally) | prevent | owner | proposed |
| Alert on `rate(quote_responses_total{source="local_cache"}[1m]) > 0` as an immediate "degraded mode entered" signal (today the first signal is 42–67 s in) | detect | owner | proposed |
| Decide the `STALE_QUOTE_MAX_AGE_SECONDS` trade-off with product: 30 s favours freshness; a longer window favours availability | mitigate | owner | proposed |
| Keep [quote-store-down runbook](../runbooks/quote-store-down.md) as is; it matched the run | mitigate | owner | done |

## Evidence

Everything under `docs/experiments/evidence/drill4-*` and `drill4.json`; the full write-up is
[`docs/experiments/4-redis-outage.md`](../experiments/4-redis-outage.md).
