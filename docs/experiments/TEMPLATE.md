# Experiment <n>: <name>

| | |
|---|---|
| Run date | YYYY-MM-DD, start → end (UTC) |
| Environment | kind (1 control plane + 2 workers), image tag, commit |
| Script | `make drill N=<n>` → `scripts/drills/<n>-<name>.sh` |
| Load during run | k6 rate and duration |

## Hypothesis

What we expect the system to do, stated so the run can prove it wrong.

## Setup

State before the run (replicas, faults off, alerts quiet).

## Steps

1. …

## Expected

## Observed

What actually happened, with numbers.

| Measure | Value |
|---|---|
| Time to detect | trigger → alert firing (or "no alert expected") |
| Time to recover | trigger → SLI back to normal |
| User-visible errors | count / ratio from k6 and Prometheus |

## Evidence

Files in [`evidence/`](evidence/): Prometheus range queries and Alertmanager alerts as JSON, k6
summary.

## Follow-ups
