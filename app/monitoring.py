"""Orchestrate one complete website monitoring cycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.checks import CheckRunner
from app.config import Settings
from app.database import Repository, WebsiteNotFound
from app.models import Website
from app.policy import HealthPolicy

logger = logging.getLogger(__name__)


class CheckAlreadyRunning(RuntimeError):
    """A monitoring cycle for this website is already in progress."""


class MonitorService:
    def __init__(self, repository: Repository, checks: CheckRunner, settings: Settings) -> None:
        self.repository = repository
        self.checks = checks
        self.settings = settings
        self.policy = HealthPolicy(settings)
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, website_id: int) -> asyncio.Lock:
        return self._locks.setdefault(website_id, asyncio.Lock())

    async def update_website(self, website_id: int, changes: dict[str, Any]) -> Website | None:
        lock = self._lock_for(website_id)
        if lock.locked():
            raise CheckAlreadyRunning(f"A monitoring cycle for website {website_id} is in progress")
        async with lock:
            return self.repository.update_website(website_id, changes)

    async def delete_website(self, website_id: int) -> bool:
        lock = self._lock_for(website_id)
        if lock.locked():
            raise CheckAlreadyRunning(f"A monitoring cycle for website {website_id} is in progress")
        async with lock:
            return self.repository.delete_website(website_id)

    async def run_cycle(
        self,
        website_id: int,
        *,
        skip_if_running: bool = False,
    ) -> dict[str, Any] | None:
        lock = self._lock_for(website_id)
        if lock.locked():
            if skip_if_running:
                logger.debug("Skipped overlapping cycle for website id=%s", website_id)
                return None
            raise CheckAlreadyRunning(f"A monitoring cycle for website {website_id} is in progress")

        async with lock:
            website = self.repository.get_website(website_id)
            if website is None:
                raise WebsiteNotFound(f"Website {website_id} does not exist")
            check_results = await self.checks.run_all(website)
            memory = self.repository.get_policy_memory(website_id)
            evaluation = self.policy.evaluate(check_results, memory, url=website.url)
            result = self.repository.record_cycle(
                website_id,
                check_results,
                evaluation,
                notifications_enabled=bool(self.settings.webhook_url),
            )
            check_summary = ",".join(
                f"{result.check_name}:{result.status.value}" for result in check_results
            )
            log_method = (
                logger.warning
                if evaluation.state.value in {"DEGRADED", "DOWN", "UNKNOWN"}
                else logger.info
            )
            log_method(
                "Monitoring cycle completed: website_id=%s state=%s reason=%s "
                "failed_checks=%s warning_checks=%s checks=%s",
                website_id,
                evaluation.state.value,
                evaluation.reason.get("code"),
                evaluation.reason.get("failed_checks", []),
                evaluation.reason.get("warning_checks", []),
                check_summary,
            )
            return result
