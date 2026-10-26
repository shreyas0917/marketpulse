"""Latest-quote store in Redis.

Writes are idempotent and ordered by event time: a single Lua script marks the
event_id as seen (SET NX with TTL) and only replaces the stored quote when the
event is newer than what is already there (last-write-wins by event time).
"""

from __future__ import annotations

import enum
from typing import Any, cast

import redis
import redis.asyncio as aioredis

from app.common.models import MarketEvent, Quote

QUOTE_KEY = "quote:{symbol}"
SEEN_KEY = "event:seen:{event_id}"

_WRITE_SCRIPT = """
local fresh = redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[4])
if not fresh then
  return 0
end
local current = redis.call('HGET', KEYS[1], 'event_ts')
if current and tonumber(current) >= tonumber(ARGV[2]) then
  return 2
end
redis.call('HSET', KEYS[1], 'price', ARGV[1], 'event_ts', ARGV[2], 'event_id', ARGV[3])
return 1
"""


class WriteResult(enum.Enum):
    DUPLICATE = 0
    WRITTEN = 1
    OLDER = 2


def quote_key(symbol: str) -> str:
    return QUOTE_KEY.format(symbol=symbol)


def _parse(symbol: str, raw: dict[Any, Any]) -> Quote | None:
    if not raw:
        return None
    data = {(k.decode() if isinstance(k, bytes) else k): v for k, v in raw.items()}
    data = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in data.items()}
    return Quote(
        symbol=symbol,
        price=float(data["price"]),
        event_ts=float(data["event_ts"]),
        event_id=str(data["event_id"]),
    )


class QuoteWriter:
    """Synchronous writer used by the ingestion worker."""

    def __init__(self, client: redis.Redis, dedup_ttl_seconds: int) -> None:
        self._client = client
        self._ttl = dedup_ttl_seconds
        self._script = client.register_script(_WRITE_SCRIPT)

    def write(self, event: MarketEvent) -> WriteResult:
        result = self._script(
            keys=[quote_key(event.symbol), SEEN_KEY.format(event_id=event.event_id)],
            args=[repr(event.price), repr(event.event_ts), event.event_id, self._ttl],
        )
        return WriteResult(int(cast(int, result)))

    def get(self, symbol: str) -> Quote | None:
        return _parse(symbol, cast(dict[Any, Any], self._client.hgetall(quote_key(symbol))))


class QuoteReader:
    """Async reader used by the quote API."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def get(self, symbol: str) -> Quote | None:
        raw = await self._client.hgetall(quote_key(symbol))
        return _parse(symbol, cast(dict[Any, Any], raw))

    async def close(self) -> None:
        await self._client.aclose()
