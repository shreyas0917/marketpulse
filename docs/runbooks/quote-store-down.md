# Runbook: quote store (Redis) down

**Alert:** `QuoteStoreDown` (severity `warning`): `min(quote_store_up) == 0` for 1 minute.

## What it means

At least one API pod's last call to Redis failed or took longer than 50 ms. The API is in
**degraded mode**:

1. a pod with a cached quote no older than 30 s (`STALE_QUOTE_MAX_AGE_SECONDS`) returns it with
   `stale: true` and `source: local_cache`;
2. otherwise it returns `503 {"error": "QUOTE_UNAVAILABLE"}` with `Retry-After`.

Readiness deliberately ignores Redis, so pods stay in the load balancer and keep giving these
controlled answers instead of load-balancer errors.

## Degraded-mode limitations (read before acting)

- The cache is **per pod**, not shared: two pods can return different stale prices, or one 200 and
  another 503, for the same symbol at the same moment.
- It is **lost on restart**. A pod started during the outage has an empty cache and returns 503 for
  every quote. **Do not restart API pods to "fix" a Redis outage**, because that makes it worse.
- It only holds symbols that pod served recently. Rarely requested symbols will 503.
- **After 30 s of outage every pod returns 503.** Degraded mode buys time; it does not replace Redis.
- `quote_store_up` only updates when a quote is requested. With no traffic it keeps its last value.

## Impact timeline

| Time since Redis failed | User sees | SLIs |
|---|---|---|
| 0–30 s | mostly `200 stale:true` (cached symbols), some `503` | freshness burns (stale > 2 s); availability burns for 503s |
| > 30 s | `503 QUOTE_UNAVAILABLE` for everything | availability burns fast; `AvailabilityFastBurn` pages |

The worker cannot write either, so `IngestionStalled` will follow if the queue lives in the same
Redis (the local setup).

## Triage

1. Is Redis running?
   - compose: `docker compose ps redis`, `docker compose exec redis redis-cli ping`
   - kind: `kubectl -n marketpulse get pods -l app.kubernetes.io/component=redis`,
     `kubectl -n marketpulse exec deploy/marketpulse-redis -- redis-cli ping`
2. Is it slow rather than down? `redis-cli --latency` (calls over 50 ms count as failures).
3. On kind: is a NetworkPolicy change blocking the API? Test from an API pod:
   `kubectl -n marketpulse exec deploy/marketpulse-worker -- python -c "import redis; print(redis.Redis(host='marketpulse-redis').ping())"`

## Mitigate

- Restart Redis: compose `docker compose start redis` (or `up -d redis`); kind
  `kubectl -n marketpulse rollout restart deploy/marketpulse-redis`.
- Redis holds only the latest quotes (no persistence in dev). Prices refill within a second of the
  producer's next events, so a cold Redis is fine.

## Verify recovery

`min(quote_store_up) == 1`, `rate(quote_responses_total{source="store"}[1m])` back to normal,
`source="unavailable"` flat. Then check that freshness and availability burn alerts resolve.
