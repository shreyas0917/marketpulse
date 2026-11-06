# Runbook: error budget low

**Alerts:** `AvailabilityErrorBudgetLow`, `LatencyErrorBudgetLow`, `FreshnessErrorBudgetLow`,
`IngestionLagErrorBudgetLow` (severity `ticket`):
`slo:<sli>:error_budget_remaining_30d < 0.25` for 15 minutes.

<a id="budget-low"></a>
## What it means

Less than 25 % of the SLO's 30-day error budget is left. This is not an outage page: the burn-rate
alerts (`<Sli>FastBurn`, `<Sli>SlowBurn`) cover active incidents. This ticket says the **month**
has gone badly enough that the [error-budget policy](../slos.md#error-budget-policy) now applies:

| Budget remaining | Policy |
|---|---|
| < 25 % | Freeze feature releases; reliability fixes only. The top error source gets a named owner. |
| ≤ 0 % | Full freeze until the budget recovers above 25 %. Postmortem for every incident that used > 10 %. |

How it is computed: `1 - error_ratio_30d / (1 - target)`, where `error_ratio_30d` is
`1 - sum_over_time(good_rate5m[30d]) / sum_over_time(total_rate5m[30d])`. Each 5-minute rate
sample counts in proportion to its traffic, so a busy bad hour weighs more than a quiet one.
It goes below 0 when the budget is overspent.

**On a local stack the window is much shorter than 30 days.** Prometheus keeps 7 days on
Docker Compose and 2 days on kind, and a freshly started stack has only minutes of history. A
single drill can therefore "exhaust" the budget. That is expected: the number is the
compliance over the history Prometheus has. Read it next to the stack's age.

## Triage

1. Which SLI, and since when? Grafana **MarketPulse / SLO overview**: the *30-day compliance*
   and *30-day budget remaining* panels, and the burn-rate graph for the same SLI.
2. Was it one incident or a slow leak? A step down in budget remaining points to an incident
   (look for its postmortem in [incidents/](../incidents/)); a steady slope points to a
   chronic error source.
3. Find the top error source for that SLI:
   - availability: `topk(5, sum by (status) (rate(http_requests_total{route="/v1/quotes/{symbol}", status=~"5.."}[1d])))`
   - latency: `histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{route="/v1/quotes/{symbol}"}[1d])))`
   - freshness: share of `stale: true` responses; `quote_store_up` history
   - ingestion lag: `queue_oldest_event_age_seconds`, `events_processed_total{result="dead_lettered"}`

## Act

- Apply the policy above: tell the team that feature releases are frozen for this service and
  link this ticket. The canary pipeline keeps running for reliability fixes.
- Give the top error source a named owner and a ticket.
- Make sure every incident that used more than 10 % of the budget has a postmortem.

## Close the ticket

The alert resolves on its own once `error_budget_remaining_30d` is back above 0.25, which happens
as bad periods age out of the window. Lift the freeze then, not before.
