"""Redis Streams queue backend (QUEUE_BACKEND=redis).

- Main queue: stream `events`, consumer group `workers`; DLQ: stream `events-dlq`.
- Retry tracking: delivery count from XPENDING.
- Retry backoff: a failed message stays pending. It is reclaimed with XCLAIM once it has been
  idle for `min_idle_ms * 2^(n-1)` (n = deliveries so far), capped at `max_backoff_ms`, so
  each retry waits twice as long as the one before (2 s, 4 s, 8 s, 16 s with the defaults).
- Ack: XACK followed by XDEL, so XLEN of the main stream is the queue depth.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, cast

import redis

from app.common.models import MarketEvent
from app.common.queue.base import Message, QueueStats

DATA_FIELD = "data"
log = logging.getLogger(__name__)


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
        max_attempts: int = 5,
        max_backoff_ms: int = 60_000,
    ) -> None:
        self._client = client
        self.stream = stream
        self.dlq_stream = dlq_stream
        self.group = group
        self.consumer = consumer
        self.min_idle_ms = min_idle_ms
        self.max_attempts = max_attempts
        self.max_backoff_ms = max_backoff_ms
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

    def backoff_ms(self, deliveries: int) -> int:
        """Idle time a message delivered `deliveries` times must reach before it is retried."""
        exponent = max(0, deliveries - 1)
        return int(min(self.min_idle_ms * (2**exponent), self.max_backoff_ms))

    def _reclaim(self, max_messages: int) -> list[Message]:
        """Claim pending messages whose backoff has elapsed, oldest first.

        XPENDING lists candidates with their delivery count and idle time; only those idle for
        at least backoff_ms(count) are claimed. XCLAIM repeats the idle check atomically, so two
        workers never both claim the same delivery, and it drops entries already deleted from
        the stream. If an entry's count is unreadable, it is treated as having used all but one
        attempt, so this delivery is its last: a missing count must never allow unbounded
        retries.
        """
        kwargs: dict[str, Any] = {"idle": self.min_idle_ms} if self.min_idle_ms > 0 else {}
        entries = self._client.xpending_range(
            self.stream, self.group, min="-", max="+", count=max_messages * 4, **kwargs
        )
        due: dict[str, int] = {}
        for entry in entries:
            msg_id = _s(entry["message_id"])
            try:
                delivered = int(entry["times_delivered"])
            except (KeyError, TypeError, ValueError):
                delivered = self.max_attempts - 1
                log.warning("delivery_count_unknown", extra={"message_id": msg_id})
            if int(entry.get("time_since_delivered", 0)) >= self.backoff_ms(delivered):
                due[msg_id] = delivered
            if len(due) >= max_messages:
                break
        messages: list[Message] = []
        # Claim per backoff bucket: XCLAIM takes one min-idle value for all ids in a call.
        by_wait: dict[int, list[str]] = {}
        for msg_id, delivered in due.items():
            by_wait.setdefault(self.backoff_ms(delivered), []).append(msg_id)
        for wait, ids in sorted(by_wait.items()):
            claimed: list[Any] = self._client.xclaim(
                self.stream,
                self.group,
                self.consumer,
                min_idle_time=wait,
                message_ids=list[Any](ids),
            )
            for raw_id, fields in claimed or []:
                if not fields:
                    continue
                msg_id = _s(raw_id)
                body = _s(fields.get(DATA_FIELD.encode(), fields.get(DATA_FIELD, b"")))
                # XCLAIM counts this delivery too.
                messages.append(Message(id=msg_id, body=body, attempts=due[msg_id] + 1))
        messages.sort(key=lambda m: _sort_key(m.id))
        return messages

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

        # 1. Reclaim messages whose earlier delivery failed (or whose consumer died) once their
        #    backoff has elapsed.
        messages.extend(self._reclaim(max_messages))

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
        # Leaving the entry pending is the retry: _reclaim picks it up once its backoff elapses.
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
