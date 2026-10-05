"""Domain models shared by checks, policy, persistence, and the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_name: str
    status: CheckStatus
    timestamp: datetime
    duration_ms: float | None = None
    value: Any = None
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "status": self.status.value,
            "timestamp": timestamp_text(self.timestamp),
            "duration_ms": self.duration_ms,
            "value": self.value,
            "message": self.message,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Website:
    id: int
    name: str
    url: str
    enabled: bool
    interval_seconds: int
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "created_at": timestamp_text(self.created_at),
            "updated_at": timestamp_text(self.updated_at),
            "last_checked_at": timestamp_text(self.last_checked_at)
            if self.last_checked_at
            else None,
        }


@dataclass(frozen=True, slots=True)
class PolicyMemory:
    """Persisted state-machine memory; UNKNOWN never erases a recovery context."""

    current_state: HealthStatus = HealthStatus.UNKNOWN
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    recovery_pending: bool = False
    recovery_from_down: bool = False


@dataclass(frozen=True, slots=True)
class HealthEvaluation:
    state: HealthStatus
    reason: dict[str, Any]
    memory: PolicyMemory

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "consecutive_failures": self.memory.consecutive_failures,
            "consecutive_successes": self.memory.consecutive_successes,
            "recovery_pending": self.memory.recovery_pending,
        }
