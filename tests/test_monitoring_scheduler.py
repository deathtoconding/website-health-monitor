from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.database import Repository
from app.monitoring import CheckAlreadyRunning, MonitorService
from app.scheduler import MonitoringScheduler


class Clock:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


class RecordingMonitor:
    def __init__(self, failures: set[int] | None = None) -> None:
        self.called: list[int] = []
        self.failures = failures or set()

    async def run_cycle(self, website_id: int, *, skip_if_running: bool = False):
        self.called.append(website_id)
        if website_id in self.failures:
            raise RuntimeError("isolated cycle failure")


def create_site(repository: Repository, name: str, interval: int = 5, enabled: bool = True):
    return repository.create_website(
        name=name,
        url=f"https://{name.lower()}.test/",
        enabled=enabled,
        interval_seconds=interval,
        timeout_seconds=10,
    )


async def test_scheduler_respects_per_site_intervals_and_disabled_sites(
    repository, settings
) -> None:
    fast = create_site(repository, "Fast", interval=5)
    slow = create_site(repository, "Slow", interval=10)
    paused = create_site(repository, "Paused", interval=5, enabled=False)
    clock = Clock()
    monitor = RecordingMonitor()
    scheduler = MonitoringScheduler(
        repository, monitor, settings, monotonic=clock.monotonic, wall_clock=clock.wall
    )

    await scheduler.run_due_once()
    await scheduler.wait_for_idle()
    assert sorted(monitor.called) == sorted([fast.id, slow.id])

    await scheduler.run_due_once()
    await scheduler.wait_for_idle()
    assert len(monitor.called) == 2

    clock.advance(5)
    await scheduler.run_due_once()
    await scheduler.wait_for_idle()
    assert monitor.called.count(fast.id) == 2
    assert monitor.called.count(slow.id) == 1
    assert paused.id not in monitor.called

    clock.advance(5)
    await scheduler.run_due_once()
    await scheduler.wait_for_idle()
    assert monitor.called.count(fast.id) == 3
    assert monitor.called.count(slow.id) == 2


async def test_scheduler_isolates_a_failing_site(repository, settings, caplog) -> None:
    first = create_site(repository, "Fails")
    second = create_site(repository, "Works")
    clock = Clock()
    monitor = RecordingMonitor({first.id})
    scheduler = MonitoringScheduler(
        repository, monitor, settings, monotonic=clock.monotonic, wall_clock=clock.wall
    )
    await scheduler.run_due_once()
    await scheduler.wait_for_idle()
    assert set(monitor.called) == {first.id, second.id}
    assert "Scheduled monitoring cycle failed" in caplog.text


async def test_scheduler_background_loop_logs_and_survives_poll_error(settings, caplog) -> None:
    class BrokenRepository:
        def list_websites(self):
            raise RuntimeError("SQLite is temporarily unavailable")

    scheduler = MonitoringScheduler(BrokenRepository(), RecordingMonitor(), settings)
    task = asyncio.create_task(scheduler.run())
    # Let one failed polling iteration reach its retry sleep, then stop cleanly.
    while "Scheduler polling iteration failed" not in caplog.text:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Monitoring scheduler stopped" in caplog.text


class BlockingChecks:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_all(self, website):
        self.started.set()
        await self.release.wait()
        return []


async def test_per_website_lock_rejects_overlapping_manual_cycle(repository, settings) -> None:
    site = create_site(repository, "Locked")
    runner = BlockingChecks()
    monitor = MonitorService(repository, runner, settings)
    first_task = asyncio.create_task(monitor.run_cycle(site.id))
    await runner.started.wait()
    with pytest.raises(CheckAlreadyRunning):
        await monitor.run_cycle(site.id)
    runner.release.set()
    # Empty check evidence is a valid UNKNOWN cycle and is persisted.
    result = await first_task
    assert result is not None
    assert result["state"] == "UNKNOWN"


async def test_site_update_and_delete_are_serialized_with_active_cycle(
    repository, settings
) -> None:
    site = create_site(repository, "ConfigLock")
    runner = BlockingChecks()
    monitor = MonitorService(repository, runner, settings)
    cycle = asyncio.create_task(monitor.run_cycle(site.id))
    await runner.started.wait()
    with pytest.raises(CheckAlreadyRunning):
        await monitor.update_website(site.id, {"url": "https://changed.test/"})
    with pytest.raises(CheckAlreadyRunning):
        await monitor.delete_website(site.id)
    assert repository.get_website(site.id).url == site.url
    runner.release.set()
    await cycle
    assert await monitor.delete_website(site.id)
