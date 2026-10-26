# Postmortem: <short title>

> **Status:** Draft — for owner review
> Blameless format: describe what the system and the process allowed to happen, not who did it.

| | |
|---|---|
| Date | YYYY-MM-DD |
| Environment | compose / kind / EKS |
| Duration (impact) | start → end (UTC), total |
| Severity | page / ticket |
| SLOs affected | availability / latency / freshness / ingestion lag |
| Error budget consumed | % of 30-day budget, with the query used |
| Detection | alert name, time to detect |
| Authors | |

## Summary

Two or three sentences: what broke, who was affected, how it was fixed.

## Impact

What users saw (status codes, stale prices, latency), how many requests or events were affected,
with the Prometheus queries used to count them.

## Timeline (UTC)

| Time | Event |
|---|---|
| hh:mm:ss | Trigger |
| hh:mm:ss | First user-visible impact |
| hh:mm:ss | Alert fired (name) |
| hh:mm:ss | Mitigation started |
| hh:mm:ss | Impact ended |
| hh:mm:ss | Alert resolved |

**Time to detect:** trigger → alert firing. **Time to recover:** trigger → impact ended.

## Root cause

The technical cause, and the conditions that let it reach users.

## What went well

## What went wrong

## Where we got lucky

## Action items

| Action | Type (prevent / detect / mitigate) | Owner | Status |
|---|---|---|---|

## Evidence

Links to files in `docs/experiments/evidence/` (Prometheus range queries, Alertmanager alerts,
k6 summaries) and dashboard screenshots if available.
