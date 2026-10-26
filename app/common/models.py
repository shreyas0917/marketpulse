"""Market event and quote models."""

from __future__ import annotations

import json
import time
import uuid

from pydantic import BaseModel, Field, field_validator


class MarketEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=20)
    price: float = Field(gt=0)
    event_ts: float = Field(default_factory=time.time, gt=0)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> MarketEvent:
        return cls.model_validate(json.loads(raw))


class Quote(BaseModel):
    symbol: str
    price: float
    event_ts: float
    event_id: str
