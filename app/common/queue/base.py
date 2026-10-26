"""QueueBackend interface shared by the Redis Streams and SQS implementations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.common.models import MarketEvent


@dataclass(frozen=True)
class Message:
    """A delivered message. `attempts` counts deliveries including this one."""

    id: str
    body: str
    attempts: int
    receipt: str = ""


@dataclass(frozen=True)
class QueueStats:
    depth: int
    dlq_depth: int
    oldest_age_seconds: float | None


class QueueBackend(Protocol):
    def publish(self, events: Sequence[MarketEvent]) -> None: ...

    def publish_raw(self, bodies: Sequence[str]) -> None:
        """Publish raw bodies without validation (used to inject poison messages)."""
        ...

    def receive(self, max_messages: int, wait_ms: int) -> list[Message]: ...

    def ack(self, message: Message) -> None: ...

    def retry(self, message: Message) -> None:
        """Leave the message for redelivery after a backoff."""
        ...

    def dead_letter(self, message: Message, reason: str) -> None: ...

    def stats(self) -> QueueStats: ...
