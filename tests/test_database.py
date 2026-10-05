from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from app.backup import backup_database
from app.database import (
    DuplicateWebsite,
    Repository,
    RepositoryError,
    WebsiteNotFound,
    WebsiteTargetChangeRequiresReplacement,
)
from app.models import CheckResult, CheckStatus, HealthStatus, utc_now
from app.policy import HealthPolicy


def check_set(http_status: CheckStatus) -> list[CheckResult]:
    return [
        CheckResult(
            "dns",
            CheckStatus.PASS,
            utc_now(),
            duration_ms=2,
            value={"addresses": ["192.0.2.1"]},
            message="DNS ok",
        ),
        CheckResult(
            "http",
            http_status,
            utc_now(),
            duration_ms=15,
            value={"status_code": 503 if http_status is CheckStatus.FAIL else 200},
            message="HTTP result",
            error="HTTP 503" if http_status is CheckStatus.FAIL else None,
        ),
        CheckResult(
            "tls",
            CheckStatus.PASS,
            utc_now(),
            duration_ms=7,
            value={"days_remaining": 80},
            message="TLS valid",
        ),
        CheckResult(
            "latency",
            CheckStatus.PASS,
            utc_now(),
            duration_ms=15,
            value={"duration_ms": 15},
            message="Latency ok",
        ),
    ]


def create_site(repository: Repository, url: str = "https://example.test/"):
    return repository.create_website(
        name="Example",
        url=url,
        enabled=True,
        interval_seconds=60,
        timeout_seconds=10,
    )


def test_schema_creation_is_idempotent_versioned_and_private(repository, settings) -> None:
    repository.initialize()
    assert repository.schema_version() == 1
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(settings.database_path).st_mode) == 0o600


def test_new_database_parent_directories_are_owner_only_on_posix(tmp_path) -> None:
    database_path = tmp_path / "private" / "nested" / "state.db"
    repository = Repository(str(database_path))
    repository.initialize()
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "private" / "nested").stat().st_mode) == 0o700
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def test_website_crud_persistence_and_url_uniqueness(repository, settings) -> None:
    website = create_site(repository)
    assert repository.get_website(website.id) == website
    with pytest.raises(DuplicateWebsite):
        create_site(repository)
    updated = repository.update_website(website.id, {"enabled": False, "interval_seconds": 120})
    assert updated is not None
    assert not updated.enabled
    assert updated.interval_seconds == 120
    reopened = Repository(settings.database_path)
    assert reopened.get_website(website.id) == updated
    assert reopened.list_websites(enabled_only=True) == []
    assert reopened.delete_website(website.id)
    assert not reopened.delete_website(website.id)
    assert reopened.get_website(website.id) is None


def test_cycle_persistence_incident_lifecycle_and_transition_dedup(repository, settings) -> None:
    website = create_site(repository)
    policy = HealthPolicy(settings)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    memory = repository.get_policy_memory(website.id)

    first = policy.evaluate(check_set(CheckStatus.FAIL), memory, url=website.url)
    repository.record_cycle(
        website.id,
        check_set(CheckStatus.FAIL),
        first,
        now=start,
        notifications_enabled=True,
    )
    assert first.state is HealthStatus.DEGRADED
    assert repository.get_incidents(website_id=website.id, status="open") == []

    second_checks = check_set(CheckStatus.FAIL)
    second = policy.evaluate(
        second_checks, repository.get_policy_memory(website.id), url=website.url
    )
    repository.record_cycle(
        website.id,
        second_checks,
        second,
        now=start + timedelta(seconds=60),
        notifications_enabled=True,
    )
    assert second.state is HealthStatus.DOWN
    active = repository.get_incidents(website_id=website.id, status="open")
    assert len(active) == 1
    incident_id = active[0]["id"]
    assert active[0]["started_at"] == "2026-01-01T00:01:00.000+00:00"

    repeat_checks = check_set(CheckStatus.FAIL)
    repeat = policy.evaluate(
        repeat_checks, repository.get_policy_memory(website.id), url=website.url
    )
    repository.record_cycle(
        website.id,
        repeat_checks,
        repeat,
        now=start + timedelta(seconds=120),
        notifications_enabled=True,
    )
    assert repeat.state is HealthStatus.DOWN
    assert len(repository.get_incidents(website_id=website.id, status="open")) == 1

    recovery_one_checks = check_set(CheckStatus.PASS)
    recovery_one = policy.evaluate(
        recovery_one_checks, repository.get_policy_memory(website.id), url=website.url
    )
    repository.record_cycle(
        website.id,
        recovery_one_checks,
        recovery_one,
        now=start + timedelta(seconds=180),
        notifications_enabled=True,
    )
    assert recovery_one.state is HealthStatus.DEGRADED
    assert repository.get_incidents(website_id=website.id, status="open")[0]["id"] == incident_id

    recovery_two_checks = check_set(CheckStatus.PASS)
    recovery_two = policy.evaluate(
        recovery_two_checks, repository.get_policy_memory(website.id), url=website.url
    )
    repository.record_cycle(
        website.id,
        recovery_two_checks,
        recovery_two,
        now=start + timedelta(seconds=240),
        notifications_enabled=True,
    )
    assert recovery_two.state is HealthStatus.HEALTHY
    resolved = repository.get_incidents(website_id=website.id, status="resolved")
    assert len(resolved) == 1
    assert resolved[0]["id"] == incident_id
    assert resolved[0]["resolved_at"] == "2026-01-01T00:04:00.000+00:00"
    assert len(repository.get_checks(website.id)) == 20
    assert len(repository.get_transitions(website.id)) == 4
    assert len(repository.get_notification_events()) == 3
    assert repository.get_health(website.id)["state"] == "HEALTHY"


def test_unknown_observation_never_resolves_an_open_incident(repository, settings) -> None:
    website = create_site(repository)
    policy = HealthPolicy(settings)
    failing = check_set(CheckStatus.FAIL)
    for _ in range(2):
        evaluation = policy.evaluate(
            failing, repository.get_policy_memory(website.id), url=website.url
        )
        repository.record_cycle(website.id, failing, evaluation, notifications_enabled=False)
    assert repository.get_incidents(website_id=website.id, status="open")

    unknown = [
        CheckResult(name, CheckStatus.UNKNOWN, utc_now(), message="No trustworthy evidence")
        for name in ("dns", "http", "tls", "latency")
    ]
    evaluation = policy.evaluate(unknown, repository.get_policy_memory(website.id), url=website.url)
    repository.record_cycle(website.id, unknown, evaluation, notifications_enabled=False)
    assert evaluation.state is HealthStatus.UNKNOWN
    assert len(repository.get_incidents(website_id=website.id, status="open")) == 1

    for _ in range(2):
        checks = check_set(CheckStatus.PASS)
        evaluation = policy.evaluate(
            checks, repository.get_policy_memory(website.id), url=website.url
        )
        repository.record_cycle(website.id, checks, evaluation, notifications_enabled=False)
    assert repository.get_incidents(website_id=website.id, status="open") == []
    assert len(repository.get_incidents(website_id=website.id, status="resolved")) == 1


def test_target_url_cannot_relabel_persisted_history(repository, settings) -> None:
    website = create_site(repository)
    checks = check_set(CheckStatus.PASS)
    evaluation = HealthPolicy(settings).evaluate(checks, url=website.url)
    repository.record_cycle(website.id, checks, evaluation, notifications_enabled=False)
    with pytest.raises(WebsiteTargetChangeRequiresReplacement, match="preserve history provenance"):
        repository.update_website(website.id, {"url": "https://new-target.test/"})
    assert repository.get_website(website.id).url == website.url


def test_transition_notifications_exclude_repeated_and_mid_recovery_states() -> None:
    assert not Repository.should_notify(
        HealthStatus.HEALTHY, HealthStatus.HEALTHY, {"code": "ALL_CRITICAL_CHECKS_PASS"}
    )
    assert not Repository.should_notify(
        HealthStatus.UNKNOWN, HealthStatus.DEGRADED, {"code": "RECOVERY_IN_PROGRESS"}
    )
    assert Repository.should_notify(
        HealthStatus.UNKNOWN, HealthStatus.DEGRADED, {"code": "CRITICAL_FAILURE_SUSPECTED"}
    )
    assert Repository.should_notify(
        HealthStatus.DEGRADED, HealthStatus.HEALTHY, {"code": "RECOVERY_CONFIRMED"}
    )


def test_disabled_notification_events_are_retained_without_delivery(repository, settings) -> None:
    website = create_site(repository)
    evaluation = HealthPolicy(settings).evaluate(check_set(CheckStatus.FAIL), url=website.url)
    repository.record_cycle(
        website.id,
        check_set(CheckStatus.FAIL),
        evaluation,
        notifications_enabled=False,
    )
    events = repository.get_notification_events()
    assert len(events) == 1
    assert events[0]["status"] == "disabled"


def test_online_backup_restores_all_application_tables(repository, settings, tmp_path) -> None:
    website = create_site(repository)
    destination = backup_database(settings.database_path, tmp_path / "backup" / "health.db")
    restored = Repository(str(destination))
    restored.initialize()
    assert restored.get_website(website.id) == website
    assert restored.schema_version() == repository.schema_version()
    with pytest.raises(FileExistsError):
        backup_database(settings.database_path, destination)
    with pytest.raises(ValueError, match="must differ"):
        backup_database(settings.database_path, settings.database_path)


def test_repository_handles_missing_rows_unsupported_updates_and_future_schema(
    repository, settings
) -> None:
    website = create_site(repository)
    with pytest.raises(ValueError, match="Unsupported"):
        repository.update_website(website.id, {"injected_column": "x"})
    with pytest.raises(RepositoryError, match="Could not update website"):
        repository.update_website(website.id, {"interval_seconds": 1})
    duplicate = create_site(repository, "https://another.test/")
    with pytest.raises(DuplicateWebsite):
        repository.update_website(duplicate.id, {"url": website.url})
    with pytest.raises(WebsiteNotFound):
        repository.record_cycle(
            999,
            [],
            HealthPolicy(settings).evaluate([], url="https://missing.test/"),
            notifications_enabled=False,
        )
    repository.finish_notification_attempt(999, success=True)

    assert repository.delete_website(website.id)
    assert repository.get_policy_memory(website.id).current_state is HealthStatus.UNKNOWN
    assert repository.get_health(website.id) is None
    assert repository.get_checks(website.id) == []
    with repository._connection() as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(RepositoryError, match="newer than supported"):
        repository.initialize()
