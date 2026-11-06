# Postmortem: injected fault left in place while Argo CD reported Synced/Healthy

> **Status:** Draft — for owner review
> Blameless format: describe what the system and the process allowed to happen, not who did it.

| | |
|---|---|
| Date | 2026-11-06 |
| Environment | kind (1 control plane + 2 workers, WSL capped at 8 GB), Argo CD auto-sync with self-heal, `ServerSideApply=true` + `ServerSideDiff=true` (D29); verification run 3, drill 8 |
| Duration (impact) | 15:30:54 → 16:01:29 UTC, **30 m 35 s** with the worker slowed. Of that, **15 m 29 s** (15:46:00 → 16:01:29) came after the drill had handed the restore to Argo CD self-heal, while the application reported `Synced/Healthy` |
| Severity | page (`IngestionStalled`, firing throughout) |
| SLOs affected | ingestion lag (every event late while the fault was on), freshness |
| Error budget consumed | Not measured as a budget fraction: the burn is deferred into the backlog and lands during the drain (see drill 8). Backlog peak **32,804** events against **16,235** at the planned end of the fault |
| Detection | Symptom: `IngestionStalled` at 15:33:14 (2 m 19.9 s). Cause (fault switch still on, Argo CD not reverting): **not detected by any alert**; found by the verifier at 16:01:14 |
| Authors | project owner (found during the independent verification) |

## Summary

Drill 8 slows the workers by setting `FAULT_WORKER_EXTRA_LATENCY_MS=500` with `kubectl set env`,
then restores the system by re-enabling Argo CD auto-sync and relying on self-heal to revert the
change. Self-heal never reverted it. Argo CD applies and diffs server-side, and with server-side
diff only fields that the manifest declares are compared. The fault variable was not in the chart,
so to Argo CD the hand-added env var was not drift: the worker Deployment stayed `Synced` and the
application `Synced/Healthy`. The drill script then logged "Argo CD reverted the worker env after
264s" and a drain time, neither of which had happened (F14). The verifier noticed the env still
present 15 minutes later and removed it with the drill's own cleanup command; the backlog drained
about 37 s later.

## Impact

- Worker throughput fell to ~2 events/s against 20/s arriving for 30 m 35 s instead of the planned
  15 min; the queue grew to **32,804** events (16,235 at the planned end of the fault).
- Every event written during the fault was later than the 1 s ingestion-lag SLO; quotes went stale
  for the symbols whose events were stuck in the backlog.
- The GitOps status, which an operator would use to confirm "the cluster matches Git", reported
  `Synced/Healthy` the whole time. The drift-protection claim in the docs (drill 8 write-up,
  PROGRESS, threat model) was wrong for any setting Git does not declare.
- Environment: a local kind cluster during a drill; no external users.

## Timeline (UTC)

| Time | Event |
|---|---|
| 15:30:37 | Drill 8 starts; Argo CD auto-sync paused |
| 15:30:54 | Trigger: `FAULT_WORKER_EXTRA_LATENCY_MS=500` set on the worker Deployment; rollout complete |
| 15:33:14 | `IngestionStalled` fires (2 m 19.9 s after the trigger); `IngestionLagFastBurn` stays silent (known gap 1) |
| 15:46:00 | Planned end of the fault: drill re-enables auto-sync with self-heal and waits for the env to disappear |
| 15:46:00 → 16:01:29 | Application `Synced/Healthy`; worker Deployment `Synced`; env still set; queue keeps growing |
| 15:50:24 | Drill logs "Argo CD reverted the worker env after 264s" — its wait loop timed out and logged success anyway (F14) |
| 16:01:14 | Verifier finds the env still present and the app `Synced/Healthy`; applies the drill's own cleanup command |
| 16:01:29 | Env removed, worker rollout complete (mitigation) |
| ~16:02:06 | Backlog drained (~37 s after the restore); `IngestionLagFastBurn` fires only during the drain, 16:01:56 → 16:02:16 |
| 16:02:17 | `IngestionStalled` resolved |

**Time to detect** (the cause, not the symptom): 30 m 20 s, by a person. **Time to recover:**
30 m 35 s from the trigger, 15 m 29 s from the planned restore.

## Root cause

Two things combined:

1. **Drift detection only covers declared fields.** Argo CD runs with `ServerSideApply=true` and
   `ServerSideDiff=true` (D29), chosen so API-server defaults do not show up as drift. With
   server-side diff, Argo CD dry-runs its own apply and compares the result. A field owned by
   another field manager that the manifest does not mention survives that apply unchanged, so it
   never shows as a difference. A changed *value* of a declared field (for example `replicas`) is
   still caught, which is why the M5 drift test passed and the gap went unnoticed.
2. **The restore step trusted a status instead of checking the effect.** Drill 8's restore loop
   waited for the env name to disappear, gave up after 120 polls (264 s), and logged a revert either way. The
   drain loop logged a drain time on timeout. The written record therefore showed a recovery.

## Why monitoring didn't see it

- The **symptom** was seen: `IngestionStalled` fired at 2 m 19.9 s and stayed firing until 16:02:17.
  But a drill is expected to page, and the page was already firing before the planned restore, so a
  continuing page did not stand out as "the restore failed".
- The **cause** had no signal. The app exports `fault_injection_active{type}` (1 while a fault switch
  is on), but no alert or dashboard panel turns it into a warning. Argo CD's own health (the obvious
  place to look for "is the cluster what Git says?") reported `Synced/Healthy`.
- The burn-rate alert for ingestion lag stayed silent while the fault was on (known gap 1: the SLI is
  rate-weighted and a throttled worker writes almost nothing), so the SLO page that would normally
  track "this is still burning" was absent.

## What went well

- `IngestionStalled`, the gap alert added for exactly this kind of stall, fired in 2 m 19.9 s.
- The drill's own cleanup command restored the system in 15 s once it was run.
- The independent verification checked the env directly instead of trusting the drill log.

## What went wrong

- Self-heal was documented as reverting `kubectl` changes in general; it only reverts declared fields.
- The drill reported a revert and a drain that did not happen.
- No alert on `fault_injection_active`, so a fault switch left on is only visible through its effects.

## Where we got lucky

- This happened on a local cluster during a verification run, not after a real incident where an
  operator had set a debug or fault variable by hand.
- The fault made a loud symptom. A quieter out-of-band change (an extra log level, a feature flag)
  would have stayed indefinitely with nothing paging.

## Fix

- **F13:** the chart now declares every `FAULT_*` variable with a neutral value in the api, worker
  and producer containers (`values.yaml` `faults:`), so a manual change is a changed value of a
  declared field: Argo CD reports `OutOfSync` and self-heal reverts it. Verified after the fix
  (2026-11-06, kind, app tracking the fix branch, no manual refresh):
  - `kubectl set env deploy/marketpulse-worker FAULT_WORKER_EXTRA_LATENCY_MS=500` at 17:44:57.383:
    Argo CD event `Synced -> OutOfSync` 17:44:57, automated sync succeeded, value back to `0` after
    **1.97 s**, app `Synced/Healthy` after 2.2 s.
  - Same on the producer with `FAULT_API_ERROR_RATE=0.5`: reverted after **2.24 s**.
  - Drill 8 re-run with the fixed script: self-heal reverted the fault **1 s** after auto-sync was
    re-enabled (`reverted_by: argocd`, `OutOfSync` seen), backlog of 12,690 drained 31 s later.
- **F14:** drill 8 records who restored the env (`argocd` or `drill_cleanup`), whether `OutOfSync`
  was seen, and `null` for anything it did not observe. Its own cleanup stays in place.
- Docs corrected: experiment 8, PROGRESS, threat model.

## Action items

| Action | Type (prevent / detect / mitigate) | Owner | Status |
|---|---|---|---|
| Declare every `FAULT_*` env var in the chart with neutral defaults | prevent | owner | Done (F13) |
| Drill 8 logs only an observed revert and drain | detect | owner | Done (F14) |
| Correct docs that said self-heal reverted drill 8 | prevent | owner | Done |
| Alert (ticket) when `max(fault_injection_active) == 1` for longer than a drill's planned duration, and add it to the overview dashboard | detect | owner | Open — alert change, needs owner approval |
| Decide for M8 (EKS) whether drift protection must also catch *undeclared* additions: options are an admission policy that blocks non-Argo writers to managed workloads, or client-side diff/`Replace=true` for selected kinds | prevent | owner | Open — design decision |
| Add a drill step that asserts the cluster matches Git after restore (env values, not just app status) | detect | owner | Open |

## Evidence

- Run 3 drill log: [`R3-E8-drill8.txt`](../verification/evidence/R3-E8-drill8.txt)
- Manual restore with Argo CD status at the time: [`drill8-verifier-restore.txt`](../verification/evidence/R3-E-drills/drill8-verifier-restore.txt)
- Alert timeline and Prometheus ranges: [`drill8.json`](../verification/evidence/R3-E-drills/drill8.json), [`drill8-stalled-firing.json`](../verification/evidence/R3-E-drills/drill8-stalled-firing.json)
- Verification findings F13 and F14: [`docs/VERIFICATION.md`](../VERIFICATION.md)
- Fix proof: [`R4-fixes/`](../verification/evidence/R4-fixes/)
