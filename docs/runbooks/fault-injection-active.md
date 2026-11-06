# Runbook: fault injection active

<a id="left-on"></a>
## Fault switch left on

**Alert:** `FaultInjectionActive` (severity `ticket`): `max(fault_injection_active) > 0` for 30 minutes.

### What it means

A MarketPulse pod has one of its fault-injection switches turned on (`FAULT_API_ERROR_RATE`,
`FAULT_API_EXTRA_LATENCY_MS`, `FAULT_WORKER_FAIL_RATE`, `FAULT_WORKER_EXTRA_LATENCY_MS`,
`FAULT_READINESS_FAIL`; see [configuration](../configuration.md)). Every pod exports
`fault_injection_active{type}` as 1 while its switch is on.

Drills turn these on deliberately, but none holds a fault for more than 15 minutes. A switch that has
been on for 30 minutes was most likely left on by a drill that did not clean up, a manual
`kubectl set env`, or a values change pushed to Git and never reverted. The symptom alerts
(burn-rate, `IngestionStalled`) may be firing too; this alert names the cause.

Background: [postmortem 2026-11-06](../incidents/2026-11-06-fault-env-invisible-to-argocd.md), where
a fault stayed on for 30 minutes and nothing pointed at it.

### Triage

1. Find the switch and the pod: `fault_injection_active == 1` in Prometheus (the `type` label names
   the switch).
2. Is a drill running right now? Check for a `make drill` session or a recent `[drill N] START` log
   line. If one is running and within its planned time, leave it.
3. Where does the value come from?
   - kind: `kubectl -n marketpulse get deploy,rollout -o yaml | grep -A1 FAULT_` and compare with
     `deploy/helm/marketpulse/values*.yaml` on `main`. The chart declares every `FAULT_*` switch
     with a neutral value (DECISIONS D36), so a manual change shows as `OutOfSync` in Argo CD.
   - compose: `docker compose config | grep FAULT_` and your shell environment.

### Fix

- **Set by hand, Argo CD auto-sync on:** self-heal should already have reverted it within seconds.
  If it has not, check that auto-sync is enabled
  (`kubectl -n argocd get application marketpulse -o jsonpath='{.spec.syncPolicy.automated}'`). A
  drill pauses auto-sync and re-enables it on exit; re-enable it if a drill was killed hard.
- **Set in Git** (`api.env` in `values-kind.yaml`): `git revert` the commit and push; Argo CD syncs
  it and Argo Rollouts runs a canary. Never force-push.
- **compose:** recreate the service without the variable
  (`docker compose up -d --force-recreate <service>`).

The alert resolves as soon as no pod reports a fault (no `for` delay on resolve).
