"""Centralized, validated runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when an environment setting is invalid."""


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _floating(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Application settings; all policy defaults are defined in one place."""

    database_path: str = "./data/website-health-monitor.db"
    host: str = "127.0.0.1"
    port: int = 8000
    interval_seconds: int = 60
    timeout_seconds: int = 10
    failure_threshold: int = 2
    recovery_threshold: int = 2
    latency_warning_ms: int = 1000
    latency_failure_ms: int = 3000
    tls_warning_days: int = 30
    scheduler_poll_seconds: float = 1.0
    max_concurrent_checks: int = 20
    log_level: str = "INFO"
    webhook_url: str | None = None
    webhook_timeout_seconds: int = 5
    webhook_max_attempts: int = 5
    notification_poll_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.database_path.strip():
            raise ConfigurationError("WHM_DATABASE_PATH must not be empty")
        if not self.host.strip():
            raise ConfigurationError("WHM_HOST must not be empty")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError("WHM_PORT must be between 1 and 65535")
        if not 5 <= self.interval_seconds <= 86400:
            raise ConfigurationError("WHM_INTERVAL_SECONDS must be between 5 and 86400")
        if not 1 <= self.timeout_seconds <= 120:
            raise ConfigurationError("WHM_TIMEOUT_SECONDS must be between 1 and 120")
        if not 1 <= self.failure_threshold <= 20:
            raise ConfigurationError("WHM_FAILURE_THRESHOLD must be between 1 and 20")
        if not 1 <= self.recovery_threshold <= 20:
            raise ConfigurationError("WHM_RECOVERY_THRESHOLD must be between 1 and 20")
        if self.latency_warning_ms <= 0:
            raise ConfigurationError("WHM_LATENCY_WARN_MS must be positive")
        if self.latency_failure_ms <= self.latency_warning_ms:
            raise ConfigurationError("WHM_LATENCY_FAIL_MS must exceed WHM_LATENCY_WARN_MS")
        if not 1 <= self.tls_warning_days <= 365:
            raise ConfigurationError("WHM_TLS_WARNING_DAYS must be between 1 and 365")
        if not 0.1 <= self.scheduler_poll_seconds <= 60:
            raise ConfigurationError("WHM_SCHEDULER_POLL_SECONDS must be between 0.1 and 60")
        if not 1 <= self.max_concurrent_checks <= 1000:
            raise ConfigurationError("WHM_MAX_CONCURRENT_CHECKS must be between 1 and 1000")
        if not 1 <= self.webhook_timeout_seconds <= 120:
            raise ConfigurationError("WHM_WEBHOOK_TIMEOUT_SECONDS must be between 1 and 120")
        if not 1 <= self.webhook_max_attempts <= 20:
            raise ConfigurationError("WHM_WEBHOOK_MAX_ATTEMPTS must be between 1 and 20")
        if not 0.1 <= self.notification_poll_seconds <= 60:
            raise ConfigurationError("WHM_NOTIFICATION_POLL_SECONDS must be between 0.1 and 60")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(
                "WHM_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
        if self.webhook_url:
            candidate = self.webhook_url.strip()
            object.__setattr__(self, "webhook_url", candidate)
            if any(character.isspace() or ord(character) < 32 for character in candidate):
                raise ConfigurationError("WHM_WEBHOOK_URL must not contain whitespace")
            try:
                parsed = urlsplit(candidate)
                port = parsed.port
            except ValueError as exc:
                raise ConfigurationError(
                    "WHM_WEBHOOK_URL is malformed or has an invalid port"
                ) from exc
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ConfigurationError("WHM_WEBHOOK_URL must be an absolute HTTP or HTTPS URL")
            if parsed.username or parsed.password:
                raise ConfigurationError("WHM_WEBHOOK_URL must not contain credentials")
            if port is not None and not 1 <= port <= 65535:
                raise ConfigurationError("WHM_WEBHOOK_URL has an invalid port")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Load and validate the documented WHM_ environment variables."""
        values = os.environ if env is None else env
        webhook_url = values.get("WHM_WEBHOOK_URL", "").strip() or None
        return cls(
            database_path=values.get("WHM_DATABASE_PATH", "./data/website-health-monitor.db"),
            host=values.get("WHM_HOST", "127.0.0.1"),
            port=_integer(values, "WHM_PORT", 8000),
            interval_seconds=_integer(values, "WHM_INTERVAL_SECONDS", 60),
            timeout_seconds=_integer(values, "WHM_TIMEOUT_SECONDS", 10),
            failure_threshold=_integer(values, "WHM_FAILURE_THRESHOLD", 2),
            recovery_threshold=_integer(values, "WHM_RECOVERY_THRESHOLD", 2),
            latency_warning_ms=_integer(values, "WHM_LATENCY_WARN_MS", 1000),
            latency_failure_ms=_integer(values, "WHM_LATENCY_FAIL_MS", 3000),
            tls_warning_days=_integer(values, "WHM_TLS_WARNING_DAYS", 30),
            scheduler_poll_seconds=_floating(values, "WHM_SCHEDULER_POLL_SECONDS", 1.0),
            max_concurrent_checks=_integer(values, "WHM_MAX_CONCURRENT_CHECKS", 20),
            log_level=values.get("WHM_LOG_LEVEL", "INFO").upper(),
            webhook_url=webhook_url,
            webhook_timeout_seconds=_integer(values, "WHM_WEBHOOK_TIMEOUT_SECONDS", 5),
            webhook_max_attempts=_integer(values, "WHM_WEBHOOK_MAX_ATTEMPTS", 5),
            notification_poll_seconds=_floating(values, "WHM_NOTIFICATION_POLL_SECONDS", 2.0),
        )
