from __future__ import annotations

import pytest

from app.config import ConfigurationError, Settings
from app.validation import UrlValidationError, normalize_website_url


def test_settings_load_central_defaults_and_environment_overrides() -> None:
    settings = Settings.from_env(
        {
            "WHM_INTERVAL_SECONDS": "90",
            "WHM_FAILURE_THRESHOLD": "3",
            "WHM_LOG_LEVEL": "debug",
            "WHM_WEBHOOK_URL": " https://hooks.example.test/notify ",
        }
    )
    assert settings.interval_seconds == 90
    assert settings.failure_threshold == 3
    assert settings.recovery_threshold == 2
    assert settings.log_level == "DEBUG"
    assert settings.webhook_url == "https://hooks.example.test/notify"
    assert settings.timeout_seconds == 10


def test_settings_reject_invalid_policy_thresholds() -> None:
    with pytest.raises(ConfigurationError, match="must exceed"):
        Settings(latency_warning_ms=3000, latency_failure_ms=1000)
    with pytest.raises(ConfigurationError, match="WHM_FAILURE_THRESHOLD"):
        Settings(failure_threshold=0)
    with pytest.raises(ConfigurationError, match="WHM_PORT"):
        Settings(port=65536)


def test_settings_reject_invalid_environment_values_and_webhook_urls() -> None:
    with pytest.raises(ConfigurationError, match="WHM_INTERVAL_SECONDS must be an integer"):
        Settings.from_env({"WHM_INTERVAL_SECONDS": "soon"})
    with pytest.raises(ConfigurationError, match="WHM_SCHEDULER_POLL_SECONDS must be a number"):
        Settings.from_env({"WHM_SCHEDULER_POLL_SECONDS": "fast"})
    with pytest.raises(ConfigurationError, match="absolute HTTP or HTTPS"):
        Settings(webhook_url="file:///tmp/notify")
    with pytest.raises(ConfigurationError, match="credentials"):
        Settings(webhook_url="https://user:secret@hooks.example.test/notify")
    with pytest.raises(ConfigurationError, match="malformed"):
        Settings(webhook_url="https://[broken/notify")


def test_settings_reject_unsafe_ranges_and_accept_blank_optional_webhook() -> None:
    with pytest.raises(ConfigurationError, match="WHM_TIMEOUT_SECONDS"):
        Settings(timeout_seconds=0)
    with pytest.raises(ConfigurationError, match="WHM_SCHEDULER_POLL_SECONDS"):
        Settings(scheduler_poll_seconds=0.01)
    assert Settings.from_env({"WHM_WEBHOOK_URL": "  "}).webhook_url is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.COM:443#section", "https://example.com/"),
        ("http://example.com:80/a?x=1#part", "http://example.com/a?x=1"),
        ("https://bücher.example/path", "https://xn--bcher-kva.example/path"),
        ("https://[2001:db8::1]:443/health", "https://[2001:db8::1]/health"),
        ("http://localhost", "http://localhost/"),
    ],
)
def test_url_normalization(value: str, expected: str) -> None:
    assert normalize_website_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ftp://example.com",
        "//example.com/path",
        "https:///path",
        "https://user:secret@example.com",
        "https://example.com:invalid",
        "https://example.com:70000",
        "https://example.com/has space",
        "https://[broken-ipv6/",
    ],
)
def test_url_rejects_unsupported_or_ambiguous_input(value: str) -> None:
    with pytest.raises(UrlValidationError):
        normalize_website_url(value)
