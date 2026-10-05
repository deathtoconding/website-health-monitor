"""In-process asyncio scheduler with per-site intervals and failure isolation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.database import Repository
from app.models import utc_now
from app.monitoring import MonitorService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScheduleEntry:
    interval_seconds: int
    next_due_monotonic: float


class MonitoringScheduler:
    def __init__(
        self,
        repository: Repository,
        monitor: MonitorService,
        settings: Settings,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.monitor = monitor
        self.settings = settings
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self._schedule: dict[int, ScheduleEntry] = {}
        self._cycle_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        logger.info("Monitoring scheduler started")
        try:
            while True:
                try:
                    await self.run_due_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Scheduler polling iteration failed; it will retry")
                await asyncio.sleep(self.settings.scheduler_poll_seconds)
        finally:
            pending = tuple(self._cycle_tasks)
            if pending:
                logger.info("Waiting for %d in-flight monitoring cycle(s) to finish", len(pending))
                await asyncio.gather(*pending, return_exceptions=True)
            logger.info("Monitoring scheduler stopped")

    async def run_due_once(self) -> None:
        now_mono = self.monotonic()
        now_wall = self.wall_clock().astimezone(UTC)
        sites = self.repository.list_websites()
        enabled_ids = {site.id for site in sites if site.enabled}
        self._schedule = {
            website_id: entry
            for website_id, entry in self._schedule.items()
            if website_id in enabled_ids
        }

        for website in sites:
            if not website.enabled:
                continue
            entry = self._schedule.get(website.id)
            if entry is None or entry.interval_seconds != website.interval_seconds:
                if website.last_checked_at is None:
                    delay = 0.0
                else:
                    due_at = website.last_checked_at + timedelta(seconds=website.interval_seconds)
                    delay = max(0.0, (due_at - now_wall).total_seconds())
                entry = ScheduleEntry(
                    interval_seconds=website.interval_seconds,
                    next_due_monotonic=now_mono + delay,
                )
                self._schedule[website.id] = entry
            if now_mono >= entry.next_due_monotonic:
                # Schedule from now rather than replaying a burst of missed intervals.
                entry.next_due_monotonic = now_mono + website.interval_seconds
                task = asyncio.create_task(self._run_site(website.id))
                self._cycle_tasks.add(task)
                task.add_done_callback(self._cycle_tasks.discard)

    async def _run_site(self, website_id: int) -> None:
        try:
            await self.monitor.run_cycle(website_id, skip_if_running=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled monitoring cycle failed: website_id=%s", website_id)

    async def wait_for_idle(self) -> None:
        """Wait for cycles currently launched by this scheduler (used by tests/shutdown)."""
        while self._cycle_tasks:
            await asyncio.gather(*tuple(self._cycle_tasks), return_exceptions=True)
