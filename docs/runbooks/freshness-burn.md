# Runbook: freshness error-budget burn

**Alerts:** `FreshnessFastBurn` (page; 1h/5m at 14.4×, 6h/30m at 6×), `FreshnessSlowBurn` (ticket; 3d/6h at 1×)
**SLO:** 99 % of returned quotes are under 2 s old (budget 1 %). See [SLOs](../slos.md).

## What it means

Quotes are being returned, but too many are older than 2 s (response time − event timestamp).
Stale quotes served from the per-pod cache in degraded mode always count as bad here.

## Impact

Consumers get prices that no longer reflect the market. For market data this is often worse than
an error, because nothing tells the client it is wrong (except `stale: true` and `age_seconds`).

## Triage

Freshness has two upstream causes. Check which one is happening:

1. **Store problem (degraded mode):** `min(quote_store_up) == 0` and
   `rate(quote_responses_total{source="local_cache"}[5m]) > 0` → follow
   [quote-store-down](quote-store-down.md).
2. **Ingestion problem:** quotes in Redis are old because events arrive late or not at all:
   - lag: `histogram_quantile(0.99, sum by (le) (rate(ingestion_lag_seconds_bucket[5m])))`
   - backlog: `max(queue_oldest_event_age_seconds)`, `max(queue_depth)`
   - see [ingestion-lag-burn](ingestion-lag-burn.md) / [ingestion-stalled](ingestion-stalled.md)
3. **Producer problem:** `sum(rate(producer_events_published_total[5m]))` near zero means no new
   prices are being published at all. Check the producer: compose `docker compose logs producer`;
   kind `kubectl -n marketpulse logs deploy/marketpulse-producer`.

## Mitigate

Fix the upstream cause above. There is no safe API-side mitigation, because serving older data is
exactly what the SLO prevents.

## Verify recovery

`sli:freshness:error_ratio_rate5m < 0.144` and `quote_responses_total{source="local_cache"}` no
longer increasing.

<a id="fast-burn"></a>
## Fast burn (page)

Usually a Redis outage or a stalled worker. Check both first.

<a id="slow-burn"></a>
## Slow burn (ticket)

Usually a chronically slow ingestion path or clock skew between producer and worker hosts.
Compare `ingestion_lag_seconds` p50 over days; check NTP on nodes.
