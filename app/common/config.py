"""Environment-driven configuration shared by every service.

Every field maps to an upper-case environment variable of the same name and is
documented in docs/configuration.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYMBOLS = "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,BHARTIARTL,ITC,LT,KOTAKBANK"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    log_level: str = "INFO"

    # Store / Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_ms: int = Field(default=50, ge=1)
    symbols: str = DEFAULT_SYMBOLS

    # API
    api_port: int = 8000
    stale_quote_max_age_seconds: float = Field(default=30.0, gt=0)
    retry_after_seconds: int = Field(default=5, ge=1)

    # Queue
    queue_backend: Literal["redis", "sqs"] = "redis"
    queue_stream: str = "events"
    queue_dlq_stream: str = "events-dlq"
    queue_group: str = "workers"
    sqs_queue_url: str = ""
    sqs_dlq_url: str = ""
    sqs_endpoint_url: str = ""
    aws_region: str = "ap-south-1"
    queue_metrics_interval_seconds: float = Field(default=15.0, gt=0)

    # Worker
    worker_port: int = 9100
    worker_max_attempts: int = Field(default=5, ge=1)
    worker_batch_size: int = Field(default=50, ge=1)
    worker_block_ms: int = Field(default=1000, ge=0)
    retry_min_idle_ms: int = Field(default=2000, ge=0)
    retry_backoff_base_seconds: float = Field(default=2.0, ge=0)
    dedup_ttl_seconds: int = Field(default=3600, ge=1)
    worker_heartbeat_timeout_seconds: float = Field(default=30.0, gt=0)

    # Producer
    producer_port: int = 9200
    producer_rate: float = Field(default=20.0, ge=0)
    producer_symbols: str = ""
    producer_burst_multiplier: float = Field(default=10.0, ge=1)
    producer_burst_duration_seconds: float = Field(default=300.0, gt=0)
    producer_burst_on_start: bool = False

    # Fault injection (section 0.7). All off by default.
    fault_api_error_rate: float = Field(default=0.0, ge=0, le=1)
    fault_api_extra_latency_ms: int = Field(default=0, ge=0)
    fault_worker_fail_rate: float = Field(default=0.0, ge=0, le=1)
    fault_worker_extra_latency_ms: int = Field(default=0, ge=0)
    fault_readiness_fail: bool = False

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def producer_symbol_list(self) -> list[str]:
        """Symbols the producer publishes; defaults to every served symbol."""
        own = [s.strip().upper() for s in self.producer_symbols.split(",") if s.strip()]
        return own or self.symbol_list

    @property
    def redis_timeout_seconds(self) -> float:
        return self.redis_timeout_ms / 1000.0
