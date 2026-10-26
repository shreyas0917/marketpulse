# Experiment 3: deploy a bad version (canary auto-rollback)

| | |
|---|---|
| Run date | 2026-10-26, 07:46:36 → 07:55:14 UTC |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, API as an Argo Rollouts canary, Argo CD syncing `main` |
| Script | `make drill N=3` → `scripts/drills/3-bad-deploy.sh` (wraps `scripts/canary.sh bad`) |
| Load during run | k6 steady 30 req/s against the ingress |

## Hypothesis

Pushing a bad API version (`FAULT_API_ERROR_RATE=0.2`) to `main` makes Argo CD sync it and Argo
Rollouts start a canary. The canary analysis detects the 5xx rate, **aborts automatically**, and
traffic returns to 100% stable. Users on the stable version see no errors; Git is then reverted so
it matches what runs.

## Setup

Rollout Healthy, stable hash `56c4fb6458`, 2 replicas, no faults, Argo CD `Synced/Healthy`.

## Steps

1. Start steady load (30 req/s).
2. Commit `api.env.FAULT_API_ERROR_RATE=0.2` to `deploy/helm/marketpulse/values-kind.yaml`, push to `main`.
3. Argo CD syncs; Argo Rollouts runs the canary (`setWeight 10 → pause 5m → analysis`).
4. Observe the analysis, the abort, and the traffic weight.
5. `git revert` the bad commit and push; confirm the rollout returns to Healthy.

## Expected

Analysis fails (canary 5xx ratio ≈ 0.20 ≫ 0.01 threshold); Rollouts aborts; canary weight → 0;
rollout `Degraded`; after revert, rollout `Healthy` and app `Synced`.

## Observed

The canary reached 10% weight, paused 5 minutes, then the `api-canary-health` analysis ran and
**failed on its first measurement** (error ratio 0.20 > 0.01; `failureLimit: 1`). Rollouts aborted
and set the canary ingress weight back to 0.

| Measure | Value |
|---|---|
| Push → canary at 10% weight | 07:46:36 → 07:48:11 (~95 s, includes Argo CD sync + canary pod start) |
| Analysis started → failed | 07:53:17 → 07:54:16 (~59 s; first 1-minute sample already over budget) |
| **Analysis failure → 100% stable traffic** | **3.265 s** (`failure_to_stable_ms` in `canary-bad-result.json`) |
| Rollout state after abort | `Degraded`, canary weight 0, stable RS unchanged (`56c4fb6458`) |
| User-visible 5xx on stable traffic | none — the fault only affected the 10% routed to canary pods |
| Revert pushed → rollout Healthy again | 07:54:32 → 07:55:14 (~42 s) |

The stable ReplicaSet never changed hash, so the 90% of traffic on stable pods was unaffected
throughout. After the `git revert` commit, Argo CD synced the reverted template and the rollout
returned to `Healthy` / `Synced` without starting a new canary.

## Evidence

In [`evidence/`](evidence/): `canary-bad-timeline.jsonl` (every state transition with timestamps),
`canary-bad-result.json` (measured abort time), `canary-bad-rollout.yaml`,
`canary-bad-analysisrun.yaml` (the failed `AnalysisRun`), `canary-bad-argocd-status.txt`.
The run above was executed through `scripts/canary.sh bad`, which `make drill N=3` wraps. The
wrapper was then run as well (10:03:34 → 10:11:17 UTC, bad commit `bc1401c`, revert `94811bb`) with
the same outcome: analysis failed on its first sample and traffic was back to 100 % stable **247 ms**
after the failure. Its files are `drill3.json` (per-`role` request and 5xx rates across the window),
`drill3-timeline.jsonl`, `drill3-result.json`, `drill3-rollout.yaml`, `drill3-analysisrun.yaml`.

## Follow-ups

- The abort-to-stable time (3.3 s) is the traffic-weight flip; canary pods scale down after
  `abortScaleDownDelaySeconds` (30 s). Both are well inside any user-facing timeout.
- The 5-minute pause before the first analysis dominates detection time. It is deliberate (let the
  canary warm up), but a shorter first pause would catch a catastrophic release sooner; a
  pre-analysis at a short pause is a reasonable future addition.
