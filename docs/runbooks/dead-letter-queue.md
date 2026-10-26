# Runbook: dead-letter queue not empty

**Alert:** `DeadLetterQueueNotEmpty` (severity `ticket`): `max(dlq_depth) > 0` for 1 minute.

## What it means

Some events failed processing 5 times (`WORKER_MAX_ATTEMPTS`) and were moved to the DLQ
(Redis stream `events-dlq`; on AWS the SQS queue `marketpulse-dev-events-dlq`). They never became
quotes. Each DLQ entry carries `reason`, `attempts` and `original_id`.

On SQS there is a second layer: messages that crash the worker before it can dead-letter them reach
the DLQ through the redrive policy (`maxReceiveCount = 10`). Those are **not** counted in
`events_processed_total{result="dead_lettered"}`, which is why this alert watches the DLQ depth
directly.

## Triage

Look at the reasons:

- compose: `docker compose exec redis redis-cli XRANGE events-dlq - + COUNT 10`
- kind: `kubectl -n marketpulse exec deploy/marketpulse-redis -- redis-cli XRANGE events-dlq - + COUNT 10`

| Reason | Meaning |
|---|---|
| `JSONDecodeError` / `ValidationError` | malformed payload from a producer (poison message) |
| `ValueError: unknown symbol` | producer publishing symbols the system does not serve |
| `InjectedFailure` | `FAULT_WORKER_FAIL_RATE` was on |
| `RedisError` / timeouts | the store failed repeatedly; check [quote-store-down](quote-store-down.md) |

## Mitigate

1. Fix the cause (bad producer, config, fault left on).
2. Decide per message: drop it, or redrive it.
   - **Drop** malformed messages: `redis-cli XTRIM events-dlq MAXLEN 0` (all) or
     `redis-cli XDEL events-dlq <id>` (one).
   - **Redrive** valid ones after the fix: for each entry, `XADD events * data '<data field>'`, then
     `XDEL events-dlq <id>`. On AWS use `aws sqs start-message-move-task` (needs
     `sqs:StartMessageMoveTask`).
3. Old events are harmless to redrive: the worker deduplicates on `event_id` and never overwrites a
   newer quote with an older one.

## Verify recovery

`max(dlq_depth) == 0` and `rate(events_processed_total{result="dead_lettered"}[5m]) == 0`.
