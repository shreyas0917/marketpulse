"""Fault-injection hooks (spec section 0.7).

Faults are read once at startup. Non-default values are logged at WARNING and
exported as fault_injection_active{type} so dashboards show when one is on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Gauge

from app.common.config import Settings

log = logging.getLogger(__name__)

API_FAULTS = ("api_error_rate", "api_extra_latency", "readiness_fail")
WORKER_FAULTS = ("worker_fail_rate", "worker_extra_latency")


@dataclass(frozen=True)
class Faults:
    api_error_rate: float = 0.0
    api_extra_latency_ms: int = 0
    worker_fail_rate: float = 0.0
    worker_extra_latency_ms: int = 0
    readiness_fail: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> Faults:
        return cls(
            api_error_rate=settings.fault_api_error_rate,
            api_extra_latency_ms=settings.fault_api_extra_latency_ms,
            worker_fail_rate=settings.fault_worker_fail_rate,
            worker_extra_latency_ms=settings.fault_worker_extra_latency_ms,
            readiness_fail=settings.fault_readiness_fail,
        )

    def active(self) -> dict[str, bool]:
        return {
            "api_error_rate": self.api_error_rate > 0,
            "api_extra_latency": self.api_extra_latency_ms > 0,
            "readiness_fail": self.readiness_fail,
            "worker_fail_rate": self.worker_fail_rate > 0,
            "worker_extra_latency": self.worker_extra_latency_ms > 0,
        }


def register_faults(faults: Faults, registry: CollectorRegistry, types: tuple[str, ...]) -> Gauge:
    """Create the fault_injection_active gauge for the given fault types and log active ones."""
    gauge = Gauge(
        "fault_injection_active",
        "1 when the named fault-injection hook is enabled",
        ["type"],
        registry=registry,
    )
    active = faults.active()
    for fault_type in types:
        gauge.labels(type=fault_type).set(1 if active[fault_type] else 0)
        if active[fault_type]:
            log.warning("fault_injection_enabled", extra={"fault_type": fault_type})
    return gauge
