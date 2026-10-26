# Runbook: scrape target down

**Alert:** `TargetDown` (severity `ticket`): a MarketPulse job (`api`, `worker`, `producer`) has
`up == 0` for 2 minutes.

## What it means

Prometheus cannot scrape the service. Either the process is down, or the network path or service
discovery is broken. While a target is down its SLI contribution is missing, so ratios can look
better than reality.

## Triage

1. Which target? Prometheus → *Status → Targets*, or `up == 0`.
2. Is the process running?
   - compose: `docker compose ps`
   - kind: `kubectl -n marketpulse get pods -o wide`
3. Does the endpoint answer? compose: `curl -s localhost:19100/metrics | head` (worker),
   `localhost:19200` (producer), `localhost:18000` (API).
   Kind: `kubectl -n marketpulse port-forward deploy/marketpulse-worker 9100` then curl.
4. On kind: check the ServiceMonitor still selects the Service
   (`kubectl -n marketpulse get servicemonitor`), and that NetworkPolicies allow ingress from the
   `monitoring` namespace.

## Mitigate

Restart the failed service, or fix the discovery/network config through Git (Argo CD will sync it).
