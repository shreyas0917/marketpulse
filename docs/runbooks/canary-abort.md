# Runbook: canary aborted / rollout degraded

**Signals:** Argo Rollouts aborts the `marketpulse-api` canary; the Rollout is `Degraded`; Argo CD
shows the `marketpulse` application as `Degraded`. Often preceded by `AvailabilityFastBurn` or
`LatencyFastBurn` while the canary held traffic.

## What happened

A new API pod template (new image tag or changed environment, e.g. `FAULT_API_ERROR_RATE`) reached
`main`. Argo CD synced it and Argo Rollouts started the canary:

```text
setWeight 10 → pause 5m → analysis → setWeight 50 → pause 5m → analysis → promote
```

The `api-canary-health` analysis queries Prometheus for **canary pods only** (`role="canary"`):

- `canary-error-ratio`: 5xx ratio must stay below 1 %;
- `canary-latency-good-ratio`: share of requests under 250 ms must stay at or above 99 %.

Each runs every 1 m, 5 times, failing after more than 1 bad measurement. **No data is a failure**,
so a canary that received no traffic is never promoted.

On failure Rollouts **aborts**: the canary Ingress weight goes back to 0 (100 % of traffic to
stable pods) and canary pods are scaled down after `abortScaleDownDelaySeconds` (30 s).

## Confirm

```bash
kubectl argo rollouts get rollout marketpulse-api -n marketpulse      # Status: Degraded, "RolloutAborted"
kubectl -n marketpulse get analysisrun                                # Failed run
kubectl -n marketpulse describe analysisrun <name> | grep -A5 "Metric"  # which metric failed, values
kubectl -n marketpulse get ingress                                    # canary ingress weight 0
kubectl -n argocd get application marketpulse                         # Synced / Degraded
```

Traffic is safe once the canary weight is 0: `sum by (role) (rate(http_requests_total{route="/v1/quotes/{symbol}"}[1m]))`
shows no `role="canary"` traffic.

## GitOps follow-up: make Git match what is running

The cluster is running the **stable** version, but Git still contains the bad change. Until Git is
fixed, Argo CD keeps showing the app as degraded, and the next sync would retry the bad canary.

1. Find the commit that introduced the bad change:
   `git log --oneline -5 -- deploy/helm/marketpulse/values-kind.yaml`
2. Revert it with a normal commit (**never force-push**):
   ```bash
   git revert --no-edit <bad-sha>
   git push origin main
   ```
3. Argo CD syncs the revert (within `timeout.reconciliation`, 60 s; or force a refresh:
   `kubectl -n argocd annotate application marketpulse argocd.argoproj.io/refresh=normal --overwrite`).
   The reverted pod template matches the stable ReplicaSet, so Rollouts goes back to `Healthy`
   without starting a new canary.
4. Confirm `kubectl argo rollouts get rollout marketpulse-api -n marketpulse` shows `Healthy` and the
   application shows `Synced` / `Healthy`.

## If the abort did not happen automatically

`kubectl argo rollouts abort marketpulse-api -n marketpulse` sets the weight to 0 immediately. Then
do the GitOps follow-up above.

## Record

The rollback time is measured from the analysis failure to 100 % stable traffic (canary weight 0).
See [experiment 3](../experiments/3-bad-deploy.md).
