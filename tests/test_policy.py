from __future__ import annotations

import pytest

from app.models import CheckResult, CheckStatus, HealthStatus, PolicyMemory, utc_now
from app.policy import HealthPolicy


def result(name: str, status: CheckStatus, message: str | None = None) -> CheckResult:
    return CheckResult(
        check_name=name,
        status=status,
        timestamp=utc_now(),
        duration_ms=25,
        value={"sample": name},
        message=message or f"{name} is {status.value.lower()}",
        error=f"{name} failed" if status is CheckStatus.FAIL else None,
    )


def good_https() -> list[CheckResult]:
    return [
        result("dns", CheckStatus.PASS),
        result("http", CheckStatus.PASS),
        result("tls", CheckStatus.PASS),
        result("latency", CheckStatus.PASS),
    ]


def test_all_critical_checks_passing_is_healthy(settings) -> None:
    evaluation = HealthPolicy(settings).evaluate(good_https(), url="https://example.test/")
    assert evaluation.state is HealthStatus.HEALTHY
    assert evaluation.reason["code"] == "ALL_CRITICAL_CHECKS_PASS"
    assert evaluation.reason["failed_checks"] == []
    assert len(evaluation.reason["evidence"]) == 4


@pytest.mark.parametrize("status", [CheckStatus.WARN, CheckStatus.FAIL])
def test_latency_warning_or_failure_is_degraded_not_down(status, settings) -> None:
    checks = good_https()
    checks[-1] = result("latency", status)
    evaluation = HealthPolicy(settings).evaluate(checks, url="https://example.test/")
    assert evaluation.state is HealthStatus.DEGRADED
    assert evaluation.memory.consecutive_failures == 0
    if status is CheckStatus.WARN:
        assert evaluation.reason["warning_checks"] == ["latency"]
        assert evaluation.reason["failed_checks"] == []
    else:
        assert evaluation.reason["failed_checks"] == ["latency"]


def test_critical_tls_warning_degrades_without_counting_as_failure(settings) -> None:
    checks = good_https()
    checks[2] = result("tls", CheckStatus.WARN)
    checks[-1] = result("latency", CheckStatus.FAIL)
    evaluation = HealthPolicy(settings).evaluate(checks, url="https://example.test/")
    assert evaluation.state is HealthStatus.DEGRADED
    assert evaluation.reason["code"] == "CRITICAL_CHECK_WARNING"
    assert evaluation.memory.consecutive_failures == 0
    assert evaluation.reason["failed_checks"] == ["latency"]
    assert evaluation.reason["warning_checks"] == ["tls"]


def test_failure_threshold_moves_suspect_to_down(settings) -> None:
    policy = HealthPolicy(settings)
    checks = good_https()
    checks[1] = result("http", CheckStatus.FAIL, "HTTP request returned 503")
    first = policy.evaluate(checks, PolicyMemory(), url="https://example.test/")
    second = policy.evaluate(checks, first.memory, url="https://example.test/")
    assert first.state is HealthStatus.DEGRADED
    assert first.reason["code"] == "CRITICAL_FAILURE_SUSPECTED"
    assert first.reason["failed_checks"] == ["http"]
    assert first.memory.consecutive_failures == 1
    assert second.state is HealthStatus.DOWN
    assert second.reason["code"] == "CRITICAL_FAILURE_THRESHOLD_EXCEEDED"
    assert second.memory.consecutive_failures == 2


def test_dns_and_http_are_both_critical(settings) -> None:
    checks = good_https()
    checks[0] = result("dns", CheckStatus.FAIL)
    first = HealthPolicy(settings).evaluate(checks, url="https://example.test/")
    second = HealthPolicy(settings).evaluate(checks, first.memory, url="https://example.test/")
    assert first.state is HealthStatus.DEGRADED
    assert second.state is HealthStatus.DOWN
    assert "dns" in second.reason["failed_checks"]


def test_recovery_requires_two_consecutive_clean_cycles(settings) -> None:
    policy = HealthPolicy(settings)
    down = PolicyMemory(
        current_state=HealthStatus.DOWN,
        consecutive_failures=2,
    )
    first = policy.evaluate(good_https(), down, url="https://example.test/")
    second = policy.evaluate(good_https(), first.memory, url="https://example.test/")
    assert first.state is HealthStatus.DEGRADED
    assert first.reason["code"] == "RECOVERY_IN_PROGRESS"
    assert first.memory.consecutive_successes == 1
    assert first.memory.recovery_from_down
    assert second.state is HealthStatus.HEALTHY
    assert second.reason["code"] == "RECOVERY_CONFIRMED"
    assert second.memory.consecutive_successes == 0
    assert not second.memory.recovery_pending


def test_failure_during_recovery_of_down_episode_returns_to_down(settings) -> None:
    policy = HealthPolicy(settings)
    recovering = PolicyMemory(
        current_state=HealthStatus.DEGRADED,
        consecutive_successes=1,
        recovery_pending=True,
        recovery_from_down=True,
    )
    checks = good_https()
    checks[1] = result("http", CheckStatus.FAIL)
    evaluation = policy.evaluate(checks, recovering, url="https://example.test/")
    assert evaluation.state is HealthStatus.DOWN
    assert evaluation.reason["code"] == "RECOVERY_INTERRUPTED"


def test_unknown_evidence_does_not_count_as_failure_or_recovery(settings) -> None:
    policy = HealthPolicy(settings)
    down = PolicyMemory(current_state=HealthStatus.DOWN, consecutive_failures=2)
    unknown = [
        result("dns", CheckStatus.UNKNOWN),
        result("http", CheckStatus.UNKNOWN),
        result("tls", CheckStatus.UNKNOWN),
    ]
    evaluation = policy.evaluate(unknown, down, url="https://example.test/")
    assert evaluation.state is HealthStatus.UNKNOWN
    assert evaluation.reason["code"] == "NO_TRUSTWORTHY_CRITICAL_EVIDENCE"
    assert evaluation.memory.consecutive_failures == 0
    assert evaluation.memory.consecutive_successes == 0
    assert evaluation.memory.recovery_pending
    assert evaluation.memory.recovery_from_down

    one_pass = policy.evaluate(good_https(), evaluation.memory, url="https://example.test/")
    assert one_pass.state is HealthStatus.DEGRADED
    assert one_pass.reason["code"] == "RECOVERY_IN_PROGRESS"
    assert one_pass.memory.consecutive_successes == 1


def test_unknown_breaks_consecutive_failure_streak_without_resolving_suspect(settings) -> None:
    policy = HealthPolicy(settings)
    failed = good_https()
    failed[1] = result("http", CheckStatus.FAIL)
    first_failure = policy.evaluate(failed, PolicyMemory(), url="https://example.test/")
    unknown = [
        result("dns", CheckStatus.UNKNOWN),
        result("http", CheckStatus.UNKNOWN),
        result("tls", CheckStatus.UNKNOWN),
    ]
    uncertain = policy.evaluate(unknown, first_failure.memory, url="https://example.test/")
    next_failure = policy.evaluate(failed, uncertain.memory, url="https://example.test/")
    confirmed = policy.evaluate(failed, next_failure.memory, url="https://example.test/")
    assert uncertain.state is HealthStatus.UNKNOWN
    assert uncertain.memory.consecutive_failures == 0
    assert uncertain.memory.recovery_pending
    assert next_failure.state is HealthStatus.DEGRADED
    assert next_failure.memory.consecutive_failures == 1
    assert confirmed.state is HealthStatus.DOWN


def test_missing_critical_result_yields_unknown(settings) -> None:
    checks = [result("http", CheckStatus.PASS), result("latency", CheckStatus.PASS)]
    evaluation = HealthPolicy(settings).evaluate(checks, url="https://example.test/")
    assert evaluation.state is HealthStatus.UNKNOWN
    assert evaluation.reason["unknown_checks"] == ["dns", "tls"]


def test_http_only_site_ignores_tls_not_applicable(settings) -> None:
    checks = [
        result("dns", CheckStatus.PASS),
        result("http", CheckStatus.PASS),
        result("tls", CheckStatus.UNKNOWN),
        result("latency", CheckStatus.PASS),
    ]
    evaluation = HealthPolicy(settings).evaluate(checks, url="http://example.test/")
    assert evaluation.state is HealthStatus.HEALTHY


def test_critical_warning_after_suspected_failure_still_requires_clean_recovery(settings) -> None:
    policy = HealthPolicy(settings)
    checks = good_https()
    checks[1] = result("http", CheckStatus.FAIL)
    suspected = policy.evaluate(checks, PolicyMemory(), url="https://example.test/")
    warning_checks = good_https()
    warning_checks[2] = result("tls", CheckStatus.WARN)
    warning = policy.evaluate(warning_checks, suspected.memory, url="https://example.test/")
    first_pass = policy.evaluate(good_https(), warning.memory, url="https://example.test/")
    second_pass = policy.evaluate(good_https(), first_pass.memory, url="https://example.test/")
    assert warning.state is HealthStatus.DEGRADED
    assert warning.memory.recovery_pending
    assert warning.memory.consecutive_successes == 0
    assert first_pass.reason["code"] == "RECOVERY_IN_PROGRESS"
    assert second_pass.state is HealthStatus.HEALTHY


def test_critical_warning_breaks_clean_recovery_streak(settings) -> None:
    policy = HealthPolicy(settings)
    prior = PolicyMemory(
        current_state=HealthStatus.DEGRADED,
        consecutive_successes=1,
        recovery_pending=True,
        recovery_from_down=True,
    )
    checks = good_https()
    checks[2] = result("tls", CheckStatus.WARN)
    warning = policy.evaluate(checks, prior, url="https://example.test/")
    assert warning.state is HealthStatus.DEGRADED
    assert warning.memory.consecutive_successes == 0
    assert warning.memory.recovery_pending
    next_pass = policy.evaluate(good_https(), warning.memory, url="https://example.test/")
    assert next_pass.state is HealthStatus.DEGRADED
    assert next_pass.memory.consecutive_successes == 1
