"""Queue backends selected by QUEUE_BACKEND."""

from __future__ import annotations

import socket

import redis

from app.common.config import Settings
from app.common.queue.base import Message, QueueBackend, QueueStats
from app.common.queue.redis_streams import RedisStreamQueue
from app.common.queue.sqs import SqsQueue

__all__ = ["Message", "QueueBackend", "QueueStats", "RedisStreamQueue", "SqsQueue", "make_queue"]


def make_queue(settings: Settings, consumer: str | None = None) -> QueueBackend:
    if settings.queue_backend == "sqs":
        import boto3

        client = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            endpoint_url=settings.sqs_endpoint_url or None,
        )
        return SqsQueue(
            client,
            settings.sqs_queue_url,
            settings.sqs_dlq_url,
            backoff_base_seconds=settings.retry_backoff_base_seconds,
        )
    client_r = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=2)
    return RedisStreamQueue(
        client_r,
        stream=settings.queue_stream,
        dlq_stream=settings.queue_dlq_stream,
        group=settings.queue_group,
        consumer=consumer or socket.gethostname(),
        min_idle_ms=settings.retry_min_idle_ms,
    )
