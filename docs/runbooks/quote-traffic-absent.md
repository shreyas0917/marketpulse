# Runbook: no quote traffic

**Alert:** `QuoteTrafficAbsent` (severity `ticket`): no `/v1/quotes/{symbol}` requests for 5 minutes.

## What it means

The availability, latency and freshness SLIs are ratios. With zero requests they have no data, so
no burn-rate alert can fire. This alert says "we are blind", not "we are healthy".

In a local stack with no load running this alert is **expected**. Start traffic with `make load`.

## Triage

1. Is the API up and scraped? `up{job=~".*api.*"}`
2. Can a request get through end to end?
   - compose: `curl -s localhost:18000/v1/quotes/RELIANCE`
   - kind: `curl -s -H 'Host: marketpulse.localtest.me' localhost:18080/v1/quotes/RELIANCE`
3. On kind: is the Ingress / ingress-nginx healthy?
   `kubectl -n ingress-nginx get pods`, `kubectl -n marketpulse get ingress`
4. Are clients sending traffic tagged as warm-up (`X-Traffic-Class: warmup`)? Those requests are
   excluded on purpose: `sum(rate(http_requests_total{route="warmup"}[5m]))`.

## Mitigate

Restore the path that is broken (ingress, service, pods). If traffic is genuinely absent, silence
the alert for the expected quiet period in Alertmanager.
