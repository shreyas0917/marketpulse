"""SQS queue backend (QUEUE_BACKEND=sqs).

Only exercised against moto in this build; it never contacts real AWS here.

- Retry tracking: ApproximateReceiveCount.
- Retry backoff: ChangeMessageVisibility with a timeout that doubles per attempt.
- Ack: DeleteMessage. Dead-letter: SendMessage to the DLQ with failure-reason
  attributes, then DeleteMessage on the original.
- queue_oldest_event_age_seconds is not available from the SQS API (it is a
  CloudWatch metric), so stats() reports None for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.common.models import MarketEvent
from app.common.queue.base import Message, QueueStats

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

MAX_VISIBILITY_SECONDS = 900
SQS_BATCH = 10


class SqsQueue:
    def __init__(
        self,
        client: SQSClient,
        queue_url: str,
        dlq_url: str,
        backoff_base_seconds: float = 2.0,
    ) -> None:
        self._client = client
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.backoff_base_seconds = backoff_base_seconds

    def publish(self, events: Sequence[MarketEvent]) -> None:
        self.publish_raw([e.to_json() for e in events])

    def publish_raw(self, bodies: Sequence[str]) -> None:
        for start in range(0, len(bodies), SQS_BATCH):
            chunk = bodies[start : start + SQS_BATCH]
            self._client.send_message_batch(
                QueueUrl=self.queue_url,
                Entries=[{"Id": str(i), "MessageBody": b} for i, b in enumerate(chunk)],
            )

    def receive(self, max_messages: int, wait_ms: int) -> list[Message]:
        resp = self._client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max(1, min(max_messages, SQS_BATCH)),
            WaitTimeSeconds=min(20, wait_ms // 1000),
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages = []
        for m in resp.get("Messages", []):
            attempts = int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            messages.append(
                Message(
                    id=m["MessageId"],
                    body=m["Body"],
                    attempts=attempts,
                    receipt=m["ReceiptHandle"],
                )
            )
        return messages

    def ack(self, message: Message) -> None:
        self._client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message.receipt)

    def retry(self, message: Message) -> None:
        timeout = self.backoff_base_seconds * (2 ** max(0, message.attempts - 1))
        self._client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt,
            VisibilityTimeout=int(min(MAX_VISIBILITY_SECONDS, timeout)),
        )

    def dead_letter(self, message: Message, reason: str) -> None:
        self._client.send_message(
            QueueUrl=self.dlq_url,
            MessageBody=message.body,
            MessageAttributes={
                "failure_reason": {"DataType": "String", "StringValue": reason[:500] or "unknown"},
                "attempts": {"DataType": "Number", "StringValue": str(message.attempts)},
                "original_message_id": {"DataType": "String", "StringValue": message.id},
            },
        )
        self.ack(message)

    def _approx_count(self, url: str) -> int:
        attrs: Any = self._client.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
        )
        return int(attrs["Attributes"]["ApproximateNumberOfMessages"])

    def stats(self) -> QueueStats:
        return QueueStats(
            depth=self._approx_count(self.queue_url),
            dlq_depth=self._approx_count(self.dlq_url),
            oldest_age_seconds=None,
        )
