# Configuration

Every service reads its configuration from environment variables only, parsed by
`pydantic-settings` in [`app/common/config.py`](../app/common/config.py). Names are case-insensitive.
All services share one settings model, so a variable that does not apply to a service is ignored.

## General

| Variable | Default | Used by | Description |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | all | Python log level. Logs are JSON lines on stdout. |
| `REDIS_URL` | `redis://localhost:6379/0` | all | Redis used as the quote store and, with `QUEUE_BACKEND=redis`, as the queue. |
| `SYMBOLS` | `RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,BHARTIARTL,ITC,LT,KOTAKBANK` | all | Comma-separated symbols the system serves. Unknown symbols get `404` (API) or are rejected by the worker. |

## Quote API

| Variable | Default | Description |
|---|---|---|
| `API_PORT` | `8000` | HTTP port. |
| `REDIS_TIMEOUT_MS` | `50` | Timeout for each quote-store call. A timeout counts as "Redis unavailable" and triggers degraded mode. |
| `STALE_QUOTE_MAX_AGE_SECONDS` | `30` | Oldest cached quote (by event timestamp) the API may serve while Redis is unavailable. |
| `RETRY_AFTER_SECONDS` | `5` | Value of the `Retry-After` header on `503 QUOTE_UNAVAILABLE`. |

## Queue

| Variable | Default | Description |
|---|---|---|
| `QUEUE_BACKEND` | `redis` | `redis` (Redis Streams) or `sqs`. |
| `QUEUE_STREAM` | `events` | Redis stream used as the main queue. |
| `QUEUE_DLQ_STREAM` | `events-dlq` | Redis stream used as the dead-letter queue. |
| `QUEUE_GROUP` | `workers` | Redis consumer group. |
| `SQS_QUEUE_URL` | _(empty)_ | Main SQS queue URL (`QUEUE_BACKEND=sqs`). |
| `SQS_DLQ_URL` | _(empty)_ | SQS dead-letter queue URL. |
| `SQS_ENDPOINT_URL` | _(empty)_ | Override the SQS endpoint (for local emulators). Empty uses the AWS default. |
| `AWS_REGION` | `ap-south-1` | Region for the SQS client. |
| `QUEUE_METRICS_INTERVAL_SECONDS` | `15` | How often the worker and producer refresh `queue_depth`, `dlq_depth` and `queue_oldest_event_age_seconds`. Compose and kind set `5`. |

## Ingestion worker

| Variable | Default | Description |
|---|---|---|
| `WORKER_PORT` | `9100` | Port for `/metrics`, `/health/live`, `/health/ready`. |
| `WORKER_MAX_ATTEMPTS` | `5` | Deliveries before a failing event is dead-lettered. |
| `WORKER_BATCH_SIZE` | `50` | Max messages read per poll. |
| `WORKER_BLOCK_MS` | `1000` | How long a poll blocks waiting for new messages. |
| `RETRY_MIN_IDLE_MS` | `2000` | Redis backend: a failed message is reclaimed with `XAUTOCLAIM` once it has been idle this long. |
| `RETRY_BACKOFF_BASE_SECONDS` | `2` | SQS backend: visibility timeout after attempt *n* is `base × 2^(n-1)`, capped at 900 s. |
| `DEDUP_TTL_SECONDS` | `3600` | How long a processed `event_id` is remembered for deduplication. |
| `WORKER_HEARTBEAT_TIMEOUT_SECONDS` | `30` | `/health/live` fails if the processing loop has not completed a poll for this long. |

## Producer

| Variable | Default | Description |
|---|---|---|
| `PRODUCER_PORT` | `9200` | Port for `/metrics`, health probes, `POST /burst` and `POST /poison`. |
| `PRODUCER_RATE` | `20` | Events per second across all symbols. |
| `PRODUCER_SYMBOLS` | _(empty)_ | Comma-separated subset of `SYMBOLS` to publish. Empty publishes every symbol. Compose and kind leave `SMOKETEST` out so smoke tests own that symbol. |
| `PRODUCER_BURST_MULTIPLIER` | `10` | Rate multiplier during market-open burst mode. |
| `PRODUCER_BURST_DURATION_SECONDS` | `300` | Length of a burst. |
| `PRODUCER_BURST_ON_START` | `false` | Start in burst mode. |

Producer admin endpoints (used by drills):

- `POST /burst?multiplier=10&duration=300` starts a burst (query parameters optional).
- `POST /poison?count=10` publishes `count` malformed messages.

## Fault injection (section 0.7 of the spec)

All default to off. Each is read at startup, logged at `WARNING` when set, and exported as
`fault_injection_active{type}`.

| Variable | Default | Service | `type` label | Effect |
|---|---|---|---|---|
| `FAULT_API_ERROR_RATE` | `0` | API | `api_error_rate` | Fraction (0–1) of quote requests that return `500 FAULT_INJECTED`. |
| `FAULT_API_EXTRA_LATENCY_MS` | `0` | API | `api_extra_latency` | Added delay per quote request. |
| `FAULT_READINESS_FAIL` | `false` | API | `readiness_fail` | `/health/ready` returns `503`. |
| `FAULT_WORKER_FAIL_RATE` | `0` | Worker | `worker_fail_rate` | Fraction of events that raise during processing. |
| `FAULT_WORKER_EXTRA_LATENCY_MS` | `0` | Worker | `worker_extra_latency` | Added delay per event. |

## Request headers

| Header | Effect |
|---|---|
| `X-Request-ID` | Echoed back and attached to log lines; generated if absent. |
| `X-Traffic-Class: warmup` | Request is recorded under `route="warmup"` and records no `quote_age_seconds`, so it is excluded from every SLI. |
