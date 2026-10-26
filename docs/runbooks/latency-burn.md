# Runbook: latency error-budget burn

**Alerts:** `LatencyFastBurn` (page; 1h/5m at 14.4×, 6h/30m at 6×), `LatencySlowBurn` (ticket; 3d/6h at 1×)
**SLO:** 99 % of quote requests answered in under 250 ms (budget 1 %). See [SLOs](../slos.md).

## What it means

More than the allowed share of `/v1/quotes/{symbol}` requests take longer than 250 ms, measured
from request receipt to response sent (all statuses count, so a slow `503` is slow too).

## Impact

Consumers see slow prices, and clients with short timeouts see errors.

## Triage

1. **MarketPulse / API** dashboard → *Quote latency percentiles*. Is p99 above 250 ms on every pod or
   on one?
   `histogram_quantile(0.99, sum by (le, pod) (rate(http_request_duration_seconds_bucket{route="/v1/quotes/{symbol}"}[5m])))`
2. Fault injected? `fault_injection_active{type="api_extra_latency"} == 1`
3. CPU saturation? **MarketPulse / Platform** → CPU vs limit, HPA desired vs current replicas.
4. Redis slow? Redis calls time out after 50 ms (`REDIS_TIMEOUT_MS`), so a slow Redis shows as
   `quote_store_up == 0` and stale/503 responses rather than as latency.

## Diagnose and mitigate

| Cause | Mitigation |
|---|---|
| `FAULT_API_EXTRA_LATENCY_MS` set | Remove it (compose: restart `api` without it; kind: revert in Git). |
| CPU throttling / too few replicas | Check HPA: `kubectl -n marketpulse get hpa`. Raise `maxReplicas` or CPU limit in values, via Git. |
| Bad release | Abort canary, revert commit: [canary-abort](canary-abort.md). |
| Load spike (market open) | Expected short burst. Confirm HPA is scaling: `kubectl -n marketpulse get hpa -w`. |

## Verify recovery

`sli:latency:error_ratio_rate5m < 0.144`; p99 back under 250 ms.

<a id="fast-burn"></a>
## Fast burn (page)

Act now: follow Triage, then mitigate the cause you find.

<a id="slow-burn"></a>
## Slow burn (ticket)

Look for a gradual regression: compare p99 per release and per pod over the last few days.
