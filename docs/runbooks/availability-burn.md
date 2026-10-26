# Runbook: availability error-budget burn

**Alerts:** `AvailabilityFastBurn` (severity `page`, windows 1h/5m at 14.4× and 6h/30m at 6×),
`AvailabilitySlowBurn` (severity `ticket`, 3d/6h at 1×)
**SLO:** 99.9 % of `/v1/quotes/{symbol}` responses are not 5xx (budget 0.1 %). See [SLOs](../slos.md).

## What it means

The quote API is returning `5xx` responses fast enough to exhaust the 30-day budget early:
14.4× uses 2 % of the budget in an hour, 6× uses 5 % in 6 hours, 1× uses 10 % in 3 days.
`4xx` responses (unknown symbol, no quote yet) are client errors and never count.

## Impact

Consumers get errors instead of prices. `503 QUOTE_UNAVAILABLE` means the quote store is down and
the local cache had nothing fresh enough; `500 FAULT_INJECTED` means fault injection is on; `500
INTERNAL` is an unhandled bug.

## Triage (first 5 minutes)

1. Open **MarketPulse / API** in Grafana (compose: <http://localhost:13000>). Look at
   *Requests by status* and *Responses by source*.
2. Which error code? In Prometheus:
   `sum by (status) (rate(http_requests_total{route="/v1/quotes/{symbol}"}[5m]))`
3. Is a fault injected? `max by (type) (fault_injection_active) == 1`
4. Is Redis down? `min(quote_store_up)`. If 0, follow [quote-store-down](quote-store-down.md).
5. Did a release just happen? Check deploy annotations on the dashboard, or on kind:
   `kubectl argo rollouts get rollout marketpulse-api -n marketpulse`

## Diagnose

| Cause | Signal | Command |
|---|---|---|
| Bad release | errors only from `role="canary"` pods, or started at a deploy annotation | `kubectl argo rollouts get rollout marketpulse-api -n marketpulse` |
| Fault injection left on | `fault_injection_active{type="api_error_rate"} == 1` | compose: `docker compose exec api env \| grep FAULT_`; kind: `kubectl -n marketpulse get rollout marketpulse-api -o yaml \| grep -A1 FAULT_` |
| Redis outage | `quote_store_up == 0`, `quote_responses_total{source="unavailable"}` rising | see [quote-store-down](quote-store-down.md) |
| Bug | `500 INTERNAL`, `unhandled_exception` in logs | compose: `docker compose logs api --since 10m`; kind: `kubectl -n marketpulse logs -l app.kubernetes.io/component=api --since=10m` |

## Mitigate

- **Bad release:** abort the canary (`kubectl argo rollouts abort marketpulse-api -n marketpulse`)
  and revert the commit in Git. Full steps: [canary-abort](canary-abort.md).
- **Fault injection:** unset the variable. Compose: `docker compose up -d api` without
  `FAULT_API_ERROR_RATE`. Kind: revert the values change in Git and let Argo CD sync.
- **Redis:** restore Redis (see [quote-store-down](quote-store-down.md)).

## Verify recovery

`sli:availability:error_ratio_rate5m` drops below `0.0144`; the fast-burn alert resolves once the
5-minute window is clean. The slow-burn ticket may stay open for hours. That is expected, because the
budget has been spent.

## Follow-up

Record the incident with [the postmortem template](../incidents/TEMPLATE.md) if more than 2 % of
the monthly budget was used. Apply the [error-budget policy](../slos.md#error-budget-policy).

<a id="fast-burn"></a>
## Fast burn (page)

Act now: follow Triage and Mitigate above. A fast burn left alone exhausts the monthly budget in
about two days (14.4×) or five days (6×).

<a id="slow-burn"></a>
## Slow burn (ticket)

Not urgent, but the budget will run out before the window ends. Find the recurring error source
during working hours (`topk(5, sum by (status) (increase(http_requests_total{route="/v1/quotes/{symbol}", status=~"5.."}[1d])))`)
and schedule a fix.
