from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from app.config import Settings
from app.database import Repository
from app.models import CheckResult, CheckStatus, utc_now
from app.monitoring import MonitorService
from app.notifications import NotificationDispatcher
from app.policy import HealthPolicy
from app.scheduler import MonitoringScheduler


class ScriptedChecks:
    def __init__(self, outcomes: list[CheckStatus]):
        self.outcomes = list(outcomes)

    async def run_all(self, website):
        status = self.outcomes.pop(0) if self.outcomes else CheckStatus.PASS
        now = utc_now()
        code = 503 if status is CheckStatus.FAIL else 200
        return [
            CheckResult(
                "dns", CheckStatus.PASS, now, 2, {"addresses": ["192.0.2.8"]}, "DNS resolved"
            ),
            CheckResult(
                "http",
                status,
                now,
                38,
                {"status_code": code, "final_url": website.url, "redirect_count": 0},
                f"HTTP {code}",
                "HTTP 503" if status is CheckStatus.FAIL else None,
            ),
            CheckResult("tls", CheckStatus.PASS, now, 4, {"days_remaining": 60}, "TLS valid"),
            CheckResult("latency", CheckStatus.PASS, now, 38, {"duration_ms": 38}, "Latency good"),
        ]


class Clock:
    def __init__(self):
        self.monotonic_value = 50.0
        self.wall_value = datetime(2026, 3, 1, tzinfo=UTC)

    def monotonic(self):
        return self.monotonic_value

    def wall(self):
        return self.wall_value

    def advance(self, seconds: float):
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


def create_site(repository: Repository):
    return repository.create_website(
        name="E2E",
        url="https://e2e.test/",
        enabled=True,
        interval_seconds=5,
        timeout_seconds=10,
    )


async def test_end_to_end_scheduler_checks_incident_sqlite_and_webhook(tmp_path) -> None:
    webhook_payloads: list[dict] = []

    def webhook_handler(request: httpx.Request) -> httpx.Response:
        webhook_payloads.append(json.loads(request.content))
        assert request.headers["Idempotency-Key"].startswith("whm-transition-")
        return httpx.Response(204, request=request)

    settings = Settings(
        database_path=str(tmp_path / "e2e.db"),
        webhook_url="https://hooks.example.test/whm",
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    website = create_site(repository)
    checks = ScriptedChecks(
        [CheckStatus.FAIL, CheckStatus.FAIL, CheckStatus.PASS, CheckStatus.PASS]
    )
    monitor = MonitorService(repository, checks, settings)
    clock = Clock()
    scheduler = MonitoringScheduler(
        repository, monitor, settings, monotonic=clock.monotonic, wall_clock=clock.wall
    )
    webhook_client = httpx.AsyncClient(transport=httpx.MockTransport(webhook_handler))
    dispatcher = NotificationDispatcher(repository, settings, webhook_client)

    for index in range(4):
        await scheduler.run_due_once()
        await scheduler.wait_for_idle()
        if index < 3:
            clock.advance(5)

    assert repository.get_health(website.id)["state"] == "HEALTHY"
    incidents = repository.get_incidents(website_id=website.id, status="resolved")
    assert len(incidents) == 1
    assert incidents[0]["started_at"] < incidents[0]["resolved_at"]
    assert len(repository.get_checks(website.id)) == 16
    assert len(repository.get_transitions(website.id)) == 4
    assert await dispatcher.dispatch_due_once() == 3
    assert len(webhook_payloads) == 3
    assert {payload["event"] for payload in webhook_payloads} == {"website.health_transition"}
    assert {event["status"] for event in repository.get_notification_events()} == {"delivered"}
    await webhook_client.aclose()


async def test_webhook_retry_and_permanent_rejection(tmp_path) -> None:
    settings = Settings(
        database_path=str(tmp_path / "notify.db"), webhook_url="https://hooks.example.test/notify"
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    site = create_site(repository)
    evaluation = HealthPolicy(settings).evaluate(
        [
            CheckResult("dns", CheckStatus.PASS, utc_now(), message="DNS ok"),
            CheckResult("http", CheckStatus.FAIL, utc_now(), message="HTTP 503", error="HTTP 503"),
            CheckResult("tls", CheckStatus.PASS, utc_now(), message="TLS ok"),
            CheckResult("latency", CheckStatus.PASS, utc_now(), message="Latency ok"),
        ],
        url=site.url,
    )
    repository.record_cycle(site.id, [], evaluation, notifications_enabled=True)
    responses = [500, 400]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(responses.pop(0), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = NotificationDispatcher(repository, settings, client)
    assert await dispatcher.dispatch_due_once() == 1
    first_event = repository.get_notification_events()[0]
    assert first_event["status"] == "pending"
    assert first_event["attempt_count"] == 1
    # Make the retry immediately due without sleeping in the test.
    with repository._connection() as connection:
        connection.execute(
            "UPDATE notification_events SET next_attempt_at = ? WHERE id = ?",
            (utc_now().isoformat(), first_event["id"]),
        )
    assert await dispatcher.dispatch_due_once() == 1
    final_event = repository.get_notification_events()[0]
    assert final_event["status"] == "failed"
    assert final_event["attempt_count"] == 2
    await client.aclose()


async def test_webhook_transport_error_is_bounded_and_recorded(tmp_path) -> None:
    settings = Settings(
        database_path=str(tmp_path / "transport-error.db"),
        webhook_url="https://hooks.example.test/notify",
        webhook_max_attempts=1,
    )
    repository = Repository(settings.database_path)
    repository.initialize()
    site = create_site(repository)
    evaluation = HealthPolicy(settings).evaluate(
        [
            CheckResult("dns", CheckStatus.PASS, utc_now(), message="DNS ok"),
            CheckResult(
                "http", CheckStatus.FAIL, utc_now(), message="HTTP failure", error="offline"
            ),
            CheckResult("tls", CheckStatus.PASS, utc_now(), message="TLS ok"),
        ],
        url=site.url,
    )
    repository.record_cycle(site.id, [], evaluation, notifications_enabled=True)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(offline))
    dispatcher = NotificationDispatcher(repository, settings, client)
    assert await dispatcher.dispatch_due_once() == 1
    event = repository.get_notification_events()[0]
    assert event["status"] == "failed"
    assert event["attempt_count"] == 1
    assert "ConnectError" in event["last_error"]
    await client.aclose()


async def test_unconfigured_webhook_does_not_attempt_delivery(tmp_path) -> None:
    settings = Settings(database_path=str(tmp_path / "no-webhook.db"))
    repository = Repository(settings.database_path)
    repository.initialize()
    client = httpx.AsyncClient()
    dispatcher = NotificationDispatcher(repository, settings, client)
    assert await dispatcher.dispatch_due_once() == 0
    await client.aclose()
