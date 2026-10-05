"""Durable webhook outbox delivery with bounded retries and deduplication."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import httpx

from app.config import Settings
from app.database import Repository
from app.models import utc_now

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.http_client = http_client

    async def run(self) -> None:
        logger.info(
            "Notification dispatcher started (configured=%s)", bool(self.settings.webhook_url)
        )
        while True:
            try:
                await self.dispatch_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification dispatch iteration failed; it will retry")
            await asyncio.sleep(self.settings.notification_poll_seconds)

    async def dispatch_due_once(self) -> int:
        webhook_url = self.settings.webhook_url
        if not webhook_url:
            return 0
        events = self.repository.pending_notifications()
        delivered_or_attempted = 0
        for event in events:
            try:
                response = await self.http_client.post(
                    webhook_url,
                    json=event["payload"],
                    headers={"Idempotency-Key": event["event_key"]},
                )
            except httpx.HTTPError as exc:
                await self._retry(event, f"{type(exc).__name__}: webhook request failed")
                delivered_or_attempted += 1
                continue

            if 200 <= response.status_code < 300:
                self.repository.finish_notification_attempt(
                    event["id"],
                    success=True,
                    max_attempts=self.settings.webhook_max_attempts,
                )
                logger.info(
                    "Webhook notification delivered: event_id=%s status=%s",
                    event["id"],
                    response.status_code,
                )
                delivered_or_attempted += 1
                continue

            retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
            error = f"Webhook returned HTTP {response.status_code}"
            if retryable:
                await self._retry(event, error)
            else:
                self.repository.finish_notification_attempt(
                    event["id"],
                    success=False,
                    error=error,
                    permanent_failure=True,
                    max_attempts=self.settings.webhook_max_attempts,
                )
                logger.error(
                    "Webhook notification permanently rejected: event_id=%s code=%s",
                    event["id"],
                    response.status_code,
                )
            delivered_or_attempted += 1
        return delivered_or_attempted

    async def _retry(self, event: dict[str, Any], error: str) -> None:
        next_attempt_number = int(event["attempt_count"]) + 1
        delay_seconds = min(2 ** min(next_attempt_number, 8), 300)
        attempt_will_exhaust = next_attempt_number >= self.settings.webhook_max_attempts
        self.repository.finish_notification_attempt(
            event["id"],
            success=False,
            error=error,
            retry_at=utc_now() + timedelta(seconds=delay_seconds),
            max_attempts=self.settings.webhook_max_attempts,
        )
        logger.warning(
            "Webhook notification attempt failed: event_id=%s attempt=%s exhausted=%s",
            event["id"],
            next_attempt_number,
            attempt_will_exhaust,
        )
