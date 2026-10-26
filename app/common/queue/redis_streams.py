"""Redis Streams queue backend (QUEUE_BACKEND=redis).

- Main queue: stream `events`, consumer group `workers`; DLQ: stream `events-dlq`.
- Retry tracking: delivery count from XPENDING.
- Retry backoff: failed messages stay pending and are reclaimed with XAUTOCLAIM
  once idle for `min_idle_ms`.
- Ack: XACK followed by XDEL, so XLEN of the main stream is the queue depth.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, cast

import redis

from app.common.models import MarketEvent
from app.common.queue.base import Message, QueueStats

DATA_FIELD = "data"


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _id_ms(stream_id: str) -> int:
    return int(stream_id.split("-", 1)[0])


class RedisStreamQueue:
    def __init__(
        self,
        client: redis.Redis,
        stream: str = "events",
        dlq_stream: str = "events-dlq",
        group: str = "workers",
        consumer: str = "worker",
        min_idle_ms: int = 2000,
    ) -> None:
        self._client = client
        self.stream = stream
        self.dlq_stream = dlq_stream
        self.group = group
        self.consumer = consumer
        self.min_idle_ms = min_idle_ms
        self._group_ready = False

    def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            self._client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    def publish(self, events: Sequence[MarketEvent]) -> None:
        self.publish_raw([e.to_json() for e in events])

    def publish_raw(self, bodies: Sequence[str]) -> None:
        self.ensure_group()
        pipe = self._client.pipeline(transaction=False)
        for body in bodies:
            pipe.xadd(self.stream, {DATA_FIELD: body})
        pipe.execute()

    def _delivery_counts(self, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        entries = self._client.xpending_range(
            self.stream,
            self.group,
            min=min(ids, key=_sort_key),
            max=max(ids, key=_sort_key),
            count=len(ids) * 4 + 10,
            consumername=self.consumer,
        )
        return {_s(e["message_id"]): int(e["times_delivered"]) for e in entries}

    def receive(self, max_messages: int, wait_ms: int) -> list[Message]:
        try:
            return self._receive(max_messages, wait_ms)
        except redis.ResponseError as exc:
            # Redis restarted without persistence: the stream and consumer group are gone.
            if "NOGROUP" not in str(exc):
                raise
            self._group_ready = False
            return self._receive(max_messages, wait_ms)

    def _receive(self, max_messages: int, wait_ms: int) -> list[Message]:
        self.ensure_group()
        messages: list[Message] = []

        # 1. Reclaim messages whose earlier delivery failed (or whose consumer died).
        claimed: list[Any] = self._client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=self.min_idle_ms,
            start_id="0-0",
            count=max_messages,
        )
        reclaimed = [(_s(i), f) for i, f in claimed[1] if f] if len(claimed) > 1 else []
        counts = self._delivery_counts([i for i, _ in reclaimed])
        for msg_id, fields in reclaimed:
            body = _s(fields.get(DATA_FIELD.encode(), fields.get(DATA_FIELD, b"")))
            messages.append(Message(id=msg_id, body=body, attempts=counts.get(msg_id, 2)))

        # 2. Read new messages.
        remaining = max_messages - len(messages)
        if remaining > 0:
            block = None if messages or wait_ms <= 0 else wait_ms
            resp = cast(
                list[Any],
                self._client.xreadgroup(
                    self.group,
                    self.consumer,
                    {self.stream: ">"},
                    count=remaining,
                    block=block,
                ),
            )
            for _stream, entries in resp or []:
                for msg_id, fields in entries:
                    body = _s(fields.get(DATA_FIELD.encode(), fields.get(DATA_FIELD, b"")))
                    messages.append(Message(id=_s(msg_id), body=body, attempts=1))
        return messages

    def ack(self, message: Message) -> None:
        pipe = self._client.pipeline(transaction=True)
        pipe.xack(self.stream, self.group, message.id)
        pipe.xdel(self.stream, message.id)
        pipe.execute()

    def retry(self, message: Message) -> None:
        # Leaving the entry pending is the retry: XAUTOCLAIM picks it up after min_idle_ms.
        return None

    def dead_letter(self, message: Message, reason: str) -> None:
        pipe = self._client.pipeline(transaction=True)
        pipe.xadd(
            self.dlq_stream,
            {
                DATA_FIELD: message.body,
                "reason": reason[:500],
                "attempts": str(message.attempts),
                "original_id": message.id,
            },
        )
        pipe.xack(self.stream, self.group, message.id)
        pipe.xdel(self.stream, message.id)
        pipe.execute()

    def stats(self) -> QueueStats:
        pipe = self._client.pipeline(transaction=False)
        pipe.xlen(self.stream)
        pipe.xlen(self.dlq_stream)
        pipe.xrange(self.stream, count=1)
        depth, dlq_depth, oldest = pipe.execute()
        age = 0.0
        if oldest:
            age = max(0.0, time.time() - _id_ms(_s(oldest[0][0])) / 1000.0)
        return QueueStats(depth=int(depth), dlq_depth=int(dlq_depth), oldest_age_seconds=age)


def _sort_key(stream_id: str) -> tuple[int, int]:
    ms, _, seq = stream_id.partition("-")
    return int(ms), int(seq or 0)
