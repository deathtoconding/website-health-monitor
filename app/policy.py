"""Deterministic and explainable website-health state machine."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.models import (
    CheckResult,
    CheckStatus,
    HealthEvaluation,
    HealthStatus,
    PolicyMemory,
)


class HealthPolicy:
    """Interpret check evidence without performing network or persistence work."""

    def __init__(self, settings: Settings) -> None:
        self.failure_threshold = settings.failure_threshold
        self.recovery_threshold = settings.recovery_threshold

    def evaluate(
        self,
        check_results: Iterable[CheckResult],
        history: PolicyMemory | None = None,
        *,
        url: str,
    ) -> HealthEvaluation:
        previous = history or PolicyMemory()
        by_name = {result.check_name: result for result in check_results}
        applicable_names = ["dns", "http"]
        if urlsplit(url).scheme == "https":
            applicable_names.append("tls")
        critical = [by_name.get(name) for name in applicable_names]
        evidence = [result.to_dict() for result in by_name.values()]
        all_failed = [
            result.check_name for result in by_name.values() if result.status is CheckStatus.FAIL
        ]
        all_warnings = [
            result.check_name for result in by_name.values() if result.status is CheckStatus.WARN
        ]
        failed = [
            result.check_name
            for result in critical
            if result is not None and result.status is CheckStatus.FAIL
        ]
        unknown = [
            name
            for name, result in zip(applicable_names, critical, strict=True)
            if result is None or result.status is CheckStatus.UNKNOWN
        ]
        critical_warnings = [
            result.check_name
            for result in critical
            if result is not None and result.status is CheckStatus.WARN
        ]
        noncritical_problems = [
            result.check_name
            for name, result in by_name.items()
            if name not in applicable_names
            and result.status in {CheckStatus.WARN, CheckStatus.FAIL}
        ]
        all_critical_pass = all(
            result is not None and result.status is CheckStatus.PASS for result in critical
        )

        if failed:
            failure_count = previous.consecutive_failures + 1
            interrupted_down_recovery = previous.recovery_from_down
            if interrupted_down_recovery:
                state = HealthStatus.DOWN
                reason_code = "RECOVERY_INTERRUPTED"
                failure_count = max(failure_count, self.failure_threshold)
            elif failure_count >= self.failure_threshold:
                state = HealthStatus.DOWN
                reason_code = "CRITICAL_FAILURE_THRESHOLD_EXCEEDED"
            else:
                state = HealthStatus.DEGRADED
                reason_code = "CRITICAL_FAILURE_SUSPECTED"
            memory = PolicyMemory(
                current_state=state,
                consecutive_failures=failure_count,
                consecutive_successes=0,
                recovery_pending=False,
                recovery_from_down=False,
            )
            return self._result(
                state,
                reason_code,
                "One or more critical checks failed.",
                memory,
                evidence,
                failed_checks=all_failed,
                critical_failed_checks=failed,
                warning_checks=all_warnings,
                unknown_checks=unknown,
                failure_count=failure_count,
                failure_threshold=self.failure_threshold,
            )

        if unknown:
            carry_down_context = (
                previous.recovery_from_down or previous.current_state is HealthStatus.DOWN
            )
            # An unknown cycle is not consecutive evidence: clear both streak counters,
            # but preserve any unresolved failure/recovery context for the next decision.
            recovery_context = (
                previous.recovery_pending
                or previous.consecutive_failures > 0
                or previous.recovery_from_down
                or previous.current_state is HealthStatus.DOWN
            )
            memory = PolicyMemory(
                current_state=HealthStatus.UNKNOWN,
                consecutive_failures=0,
                consecutive_successes=0,
                recovery_pending=recovery_context,
                recovery_from_down=carry_down_context,
            )
            return self._result(
                HealthStatus.UNKNOWN,
                "NO_TRUSTWORTHY_CRITICAL_EVIDENCE",
                "Critical checks did not provide enough trustworthy evidence to classify health.",
                memory,
                evidence,
                failed_checks=all_failed,
                warning_checks=all_warnings,
                unknown_checks=unknown,
            )

        if critical_warnings:
            down_context = (
                previous.recovery_from_down or previous.current_state is HealthStatus.DOWN
            )
            recovery_context = (
                previous.recovery_pending or previous.consecutive_failures > 0 or down_context
            )
            reason_code = "RECOVERY_NOT_CONFIRMED" if recovery_context else "CRITICAL_CHECK_WARNING"
            message = (
                "Recovery is not confirmed because a critical check is warning."
                if recovery_context
                else "One or more critical checks returned a warning."
            )
            memory = PolicyMemory(
                current_state=HealthStatus.DEGRADED,
                consecutive_failures=0,
                consecutive_successes=0,
                recovery_pending=recovery_context,
                recovery_from_down=down_context,
            )
            return self._result(
                HealthStatus.DEGRADED,
                reason_code,
                message,
                memory,
                evidence,
                failed_checks=all_failed,
                warning_checks=all_warnings,
                unknown_checks=unknown,
            )

        in_recovery = (
            previous.consecutive_failures > 0
            or previous.consecutive_successes > 0
            or previous.recovery_pending
            or previous.recovery_from_down
            or previous.current_state is HealthStatus.DOWN
        )
        if all_critical_pass and in_recovery:
            recovery_count = previous.consecutive_successes + 1
            if recovery_count < self.recovery_threshold:
                memory = PolicyMemory(
                    current_state=HealthStatus.DEGRADED,
                    consecutive_failures=0,
                    consecutive_successes=recovery_count,
                    recovery_pending=True,
                    recovery_from_down=(
                        previous.recovery_from_down or previous.current_state is HealthStatus.DOWN
                    ),
                )
                return self._result(
                    HealthStatus.DEGRADED,
                    "RECOVERY_IN_PROGRESS",
                    "Critical checks passed; another consecutive pass is required to confirm recovery.",
                    memory,
                    evidence,
                    recovery_count=recovery_count,
                    recovery_threshold=self.recovery_threshold,
                    failed_checks=all_failed,
                    warning_checks=all_warnings,
                    unknown_checks=unknown,
                )
            final_state = HealthStatus.DEGRADED if noncritical_problems else HealthStatus.HEALTHY
            code = "NON_CRITICAL_DEGRADATION" if noncritical_problems else "RECOVERY_CONFIRMED"
            message = (
                "Critical checks recovered, but non-critical checks need attention."
                if noncritical_problems
                else "Recovery is confirmed after consecutive successful critical checks."
            )
            memory = PolicyMemory(current_state=final_state)
            return self._result(
                final_state,
                code,
                message,
                memory,
                evidence,
                failed_checks=all_failed,
                warning_checks=all_warnings,
                unknown_checks=unknown,
            )

        if noncritical_problems:
            memory = PolicyMemory(current_state=HealthStatus.DEGRADED)
            return self._result(
                HealthStatus.DEGRADED,
                "NON_CRITICAL_DEGRADATION",
                "Critical checks pass, but non-critical checks need attention.",
                memory,
                evidence,
                failed_checks=all_failed,
                warning_checks=all_warnings,
                unknown_checks=unknown,
            )

        memory = PolicyMemory(current_state=HealthStatus.HEALTHY)
        return self._result(
            HealthStatus.HEALTHY,
            "ALL_CRITICAL_CHECKS_PASS",
            "All applicable critical checks pass.",
            memory,
            evidence,
        )

    @staticmethod
    def _result(
        state: HealthStatus,
        code: str,
        message: str,
        memory: PolicyMemory,
        evidence: list[dict[str, Any]],
        **details: Any,
    ) -> HealthEvaluation:
        reason: dict[str, Any] = {
            "code": code,
            "message": message,
            "failed_checks": details.pop("failed_checks", []),
            "warning_checks": details.pop("warning_checks", []),
            "unknown_checks": details.pop("unknown_checks", []),
            "evidence": evidence,
        }
        reason.update(details)
        return HealthEvaluation(state=state, reason=reason, memory=memory)
