# Postmortem: bad API release caught and rolled back by the canary

> **Status:** Draft — for owner review
> Blameless format: describe what the system and the process allowed to happen, not who did it.

| | |
|---|---|
| Date | 2026-10-26 |
| Environment | kind (1 control plane + 2 workers), Argo CD syncing `main`, Argo Rollouts canary with nginx traffic routing |
| Duration (impact) | 07:48:11 → 07:54:20 UTC (6 min 9 s of canary traffic receiving injected errors) |
| Severity | page-level signal on the canary slice; **no page fired** for the service (see "What went well") |
| SLOs affected | availability (canary slice only) |
| Error budget consumed | ≈ 0.3 % of the 30-day availability budget — **estimate** from configured rates (30 req/s, 10 % weight, 20 % fault) × measured duration; the measured timeline is in the evidence files |
| Detection | `AnalysisRun marketpulse-api-57cbbb97cc-2-2` failed; 59 s after analysis started |
| Authors | project owner (drill run by the build) |

## Summary

A release that returns `500` on 20 % of quote requests (`FAULT_API_ERROR_RATE=0.2`, standing in
for a real regression) was pushed to `main`. Argo CD synced it and Argo Rollouts started a canary at
10 % traffic. After the configured 5-minute pause, the first Prometheus analysis measured a canary
5xx ratio of ~0.20 against a 0.01 threshold and failed. Rollouts aborted automatically; the ingress
canary weight went back to 0 **3.265 s** after the failure, so 100 % of traffic returned to the
unchanged stable pods. The bad commit was then reverted in Git so the repository matched what was
running, and the rollout returned to `Healthy`.

## Impact

- Only the ~10 % of requests routed to canary pods saw errors, and only ~20 % of those: roughly 2 %
  of all quote requests during the 6-minute window returned `500 FAULT_INJECTED`.
- Stable pods (`56c4fb6458`) were never changed, so 90 % of traffic was unaffected throughout.
- No alert paged: the whole-service error ratio stayed under the fast-burn thresholds because the
  canary was small and short-lived. The canary analysis, not the SLO alert, was the detector — which
  is the intended layering.

## Timeline (UTC)

| Time | Event |
|---|---|
| 07:46:36 | Bad commit `10d5b1c` pushed to `main` |
| 07:47:40 | Argo CD synced; Rollout left `Healthy`, new pod template hash `57cbbb97cc` |
| 07:48:11 | Canary ingress weight set to 10 % — **first user-visible impact** on the canary slice |
| 07:48:17 | Rollout `Paused` for the 5-minute warm-up step |
| 07:53:17 | `AnalysisRun marketpulse-api-57cbbb97cc-2-2` started |
| 07:54:16 | Analysis **Failed** (first 1-minute sample already over `failureLimit`) |
| 07:54:19 | Rollout `Degraded`, `abort=true` |
| 07:54:20 | Canary ingress weight 0 — **impact ended** (3.265 s after failure) |
| 07:54:32 | Revert commit `c82c426` pushed to `main` |
| 07:55:14 | Rollout `Healthy` again, stable hash unchanged; Argo CD `Synced/Healthy` |

**Time to detect:** 6 min 5 s from first impact to analysis failure (5 min of that is the configured
pause). **Time to recover:** 6 min 9 s from first impact to 100 % stable traffic; **3.3 s** from
detection to recovery.

## Root cause

The release itself was the cause (a deliberate fault standing in for a real defect). The system
behaved as designed: the canary bounded the blast radius to 10 % of traffic, and the
Prometheus-backed analysis on `role="canary"` series made the fault visible and actionable without
human involvement.

## What went well

- Automatic abort and traffic return in seconds; no manual action needed to protect users.
- Blast radius bounded by the 10 % first step.
- Canary-only metrics (via the `role` pod label relabelled onto every series) kept the stable
  slice's numbers clean, so the comparison was unambiguous.
- Git was reconciled with a normal `revert` commit; no force-push, full history kept.

## What went wrong

- Detection waited for the full 5-minute pause before the first analysis. A release that fails on
  its first request still gets five minutes of 10 % exposure.
- Nothing paged. That is correct for this blast radius, but a release that *keeps* failing would
  rely entirely on the rollout controller; there is no independent alert on "rollout aborted".

## Where we got lucky

- Traffic was steady (30 req/s), so the analysis had samples. With little traffic the no-data rule
  would still have aborted — but as an *untested* release rather than a *detected bad* one, which
  looks the same in the dashboard and could hide a real problem.

## Action items

| Action | Type | Owner | Status |
|---|---|---|---|
| Add a short pre-analysis step (e.g. 60 s) before the 5-minute pause to catch catastrophic releases early | prevent | owner | proposed |
| Alert on `Rollout` phase `Degraded` / `abort=true` so an aborted release is visible outside the Argo UI | detect | owner | proposed |
| Distinguish "analysis failed on data" from "analysis failed on no data" in the runbook and dashboard | detect | owner | proposed |
| Keep `docs/runbooks/canary-abort.md` as the operator path; it was followed exactly here | mitigate | owner | done |

## Evidence

`docs/experiments/evidence/canary-bad-timeline.jsonl` (every state transition),
`canary-bad-result.json` (measured failure→stable 3265 ms), `canary-bad-analysisrun.yaml`,
`canary-bad-rollout.yaml`, `canary-bad-argocd-status.txt`. Experiment write-up:
[`docs/experiments/3-bad-deploy.md`](../experiments/3-bad-deploy.md).
