# Experiment 6: poison messages in the queue

| | |
|---|---|
| Run date | 2026-10-26, 09:18:11 → 09:20:26 UTC |
| Environment | kind (1 control plane + 2 workers), image `marketpulse:0.1.0`, 2 worker replicas, producer at 20 events/s |
| Script | `make drill N=6` → `scripts/drills/6-poison-messages.sh` |
| Load during run | normal producer traffic (20 events/s); no k6 |

## Hypothesis

Malformed messages are retried a bounded number of times (`WORKER_MAX_ATTEMPTS=5`), then moved to
the dead-letter stream with a reason; the DLQ alert fires; the worker neither crashes nor stops
processing valid events.

## Setup

DLQ empty, worker restart counts recorded (1 and 1 — left over from an earlier host-level
overload, not from this drill), `DeadLetterQueueNotEmpty` not firing.

## Steps

1. `POST /poison?count=20` on the producer: 20 bodies that are not JSON, fail validation, or lack
   required fields.
2. Watch `XLEN events-dlq` until it has grown by 20.
3. Wait for `DeadLetterQueueNotEmpty` in Alertmanager.
4. Compare worker restart counts and the `events_processed_total{result="ok"}` delta.
5. Clear the DLQ (`XTRIM events-dlq MAXLEN 0`) and wait for the alert to resolve.

## Expected

20 messages in the DLQ after 5 attempts each; `events_processed_total{result="dead_lettered"}` +20
and `result="retried"` +80; alert fires after its 1-minute `for`; no worker restarts; good events
keep flowing.

## Observed

| Measure | Value |
|---|---|
| Poison published | 20 at 09:18:13 |
| All 20 in the DLQ | **9 s** later (09:18:22): 5 deliveries each, reclaimed with `XAUTOCLAIM` after `RETRY_MIN_IDLE_MS` |
| `events_processed_total` deltas (5 m increase) | retried ≈ **81**, dead_lettered ≈ **20** (rate-extrapolated counters; the exact expectation is 80 / 20) |
| `DeadLetterQueueNotEmpty` | `startsAt` 09:19:29 — **76 s** after publish (`for: 1m` + evaluation interval), severity `ticket` |
| Worker restarts before / after | 1 1 / **1 1** (unchanged) |
| Good events processed during the drill | **2,200** — the worker never stalled |
| DLQ cleared → alert resolved | 09:20:00 → 09:20:26 (**26 s**) |
| Time to detect | 76 s (alert), 9 s if "all dead-lettered" counts as detection |
| Time to recover | the service never degraded; DLQ cleanup took 26 s to resolve the alert |

Each DLQ entry carries `reason` (`JSONDecodeError …` / `ValidationError …`), `attempts: 5` and the
original stream id, which is what the [DLQ runbook](../runbooks/dead-letter-queue.md) needs to
decide between dropping and redriving.

## Evidence

[`evidence/drill6.json`](evidence/drill6.json) (events by result, `dlq_depth`, `queue_depth`,
ingestion-lag SLI), [`evidence/drill6-result.json`](evidence/drill6-result.json),
[`evidence/drill6-dlq-sample.txt`](evidence/drill6-dlq-sample.txt) (three raw DLQ entries),
[`evidence/drill6-dlq-alert-firing.json`](evidence/drill6-dlq-alert-firing.json),
[`evidence/drill6-dlq-alert-resolved.json`](evidence/drill6-dlq-alert-resolved.json).

## Follow-ups

- Dead-lettered events count as bad for the ingestion-lag SLI by design; 20 events against 2,200
  good ones (≈ 0.9 %) is just under the 1 % budget, so a steady trickle of poison would page.
  That is the intended behaviour: bad producer data is an ingestion failure.
- A redrive tool (`XADD events … ; XDEL events-dlq …` in a loop) would make the runbook's
  "redrive after the fix" step a one-liner.
