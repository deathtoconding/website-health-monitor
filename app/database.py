"""SQLite persistence with explicit transactions and a small schema version."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from app.filesystem import ensure_private_file, ensure_private_parent
from app.models import (
    CheckResult,
    HealthEvaluation,
    HealthStatus,
    PolicyMemory,
    Website,
    parse_timestamp,
    timestamp_text,
    utc_now,
)

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS websites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 5 AND 86400),
    timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS health_states (
    website_id INTEGER PRIMARY KEY REFERENCES websites(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('HEALTHY', 'DEGRADED', 'DOWN', 'UNKNOWN')),
    reason_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    consecutive_successes INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_successes >= 0),
    recovery_pending INTEGER NOT NULL DEFAULT 0 CHECK(recovery_pending IN (0, 1)),
    recovery_from_down INTEGER NOT NULL DEFAULT 0 CHECK(recovery_from_down IN (0, 1))
);

CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PASS', 'WARN', 'FAIL', 'UNKNOWN')),
    duration_ms REAL,
    value_json TEXT,
    message TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_website_time
    ON check_results(website_id, timestamp DESC, id DESC);

CREATE TABLE IF NOT EXISTS health_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    from_state TEXT NOT NULL CHECK(from_state IN ('HEALTHY', 'DEGRADED', 'DOWN', 'UNKNOWN')),
    to_state TEXT NOT NULL CHECK(to_state IN ('HEALTHY', 'DEGRADED', 'DOWN', 'UNKNOWN')),
    occurred_at TEXT NOT NULL,
    reason_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_website_time
    ON health_transitions(website_id, occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    resolved_at TEXT,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
    reason_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    resolution_reason_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_website_status
    ON incidents(website_id, status, started_at DESC);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    transition_id INTEGER NOT NULL UNIQUE REFERENCES health_transitions(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'delivered', 'failed', 'disabled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_pending
    ON notification_events(status, next_attempt_at, id);
"""


class RepositoryError(RuntimeError):
    """Base class for persistence-layer errors."""


class WebsiteNotFound(RepositoryError):
    """Requested website does not exist."""


class DuplicateWebsite(RepositoryError):
    """A normalized website URL is already configured."""


class WebsiteTargetChangeRequiresReplacement(RepositoryError):
    """Prevent history for one URL from being silently attributed to another."""


class Repository:
    """Small SQLite repository; a single process is the supported runtime model."""

    def __init__(self, database_path: str) -> None:
        self.database_path = str(Path(database_path).expanduser())

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        path = Path(self.database_path)
        ensure_private_parent(path)
        ensure_private_file(path)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RepositoryError(
                    f"Database schema version {version} is newer than supported version "
                    f"{SCHEMA_VERSION}"
                )
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if os.name == "posix":
            path.chmod(0o600)
        logger.info("SQLite repository initialized at %s (schema=%s)", path, SCHEMA_VERSION)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load_json(value: str | None) -> Any:
        return json.loads(value) if value is not None else None

    @staticmethod
    def _website(row: sqlite3.Row | None) -> Website | None:
        if row is None:
            return None
        return Website(
            id=int(row["id"]),
            name=str(row["name"]),
            url=str(row["url"]),
            enabled=bool(row["enabled"]),
            interval_seconds=int(row["interval_seconds"]),
            timeout_seconds=int(row["timeout_seconds"]),
            created_at=parse_timestamp(str(row["created_at"])),  # type: ignore[arg-type]
            updated_at=parse_timestamp(str(row["updated_at"])),  # type: ignore[arg-type]
            last_checked_at=parse_timestamp(row["last_checked_at"]),
        )

    def create_website(
        self,
        *,
        name: str,
        url: str,
        enabled: bool,
        interval_seconds: int,
        timeout_seconds: int,
        now: datetime | None = None,
    ) -> Website:
        stamp = timestamp_text(now or utc_now())
        initial_reason = {
            "code": "NOT_CHECKED_YET",
            "message": "No monitoring result is available yet.",
            "failed_checks": [],
            "warning_checks": [],
            "unknown_checks": [],
            "evidence": [],
        }
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """INSERT INTO websites
                       (name, url, enabled, interval_seconds, timeout_seconds,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, url, int(enabled), interval_seconds, timeout_seconds, stamp, stamp),
                )
                website_id = int(cursor.lastrowid)
                connection.execute(
                    """INSERT INTO health_states
                       (website_id, state, reason_json, updated_at)
                       VALUES (?, 'UNKNOWN', ?, ?)""",
                    (website_id, self._json(initial_reason), stamp),
                )
                row = connection.execute(
                    "SELECT * FROM websites WHERE id = ?", (website_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            if "websites.url" in str(exc):
                raise DuplicateWebsite("A website with this normalized URL already exists") from exc
            raise RepositoryError(f"Could not create website: {exc}") from exc
        website = self._website(row)
        assert website is not None
        logger.info("Website configured: id=%s enabled=%s", website.id, website.enabled)
        return website

    def get_website(self, website_id: int) -> Website | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM websites WHERE id = ?", (website_id,)
            ).fetchone()
        return self._website(row)

    def list_websites(self, *, enabled_only: bool = False) -> list[Website]:
        query = "SELECT * FROM websites"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name COLLATE NOCASE, id"
        with self._connection() as connection:
            rows = connection.execute(query).fetchall()
        return [website for row in rows if (website := self._website(row)) is not None]

    def update_website(self, website_id: int, changes: dict[str, Any]) -> Website | None:
        allowed = {"name", "url", "enabled", "interval_seconds", "timeout_seconds"}
        if not changes:
            return self.get_website(website_id)
        if set(changes) - allowed:
            raise ValueError("Unsupported website update field")
        columns = list(changes)
        values = [int(changes[name]) if name == "enabled" else changes[name] for name in columns]
        assignments = [f"{name} = ?" for name in columns]
        assignments.append("updated_at = ?")
        values.extend([timestamp_text(utc_now()), website_id])
        try:
            with self._connection() as connection:
                current = connection.execute(
                    "SELECT url FROM websites WHERE id = ?", (website_id,)
                ).fetchone()
                if current is None:
                    return None
                requested_url = changes.get("url")
                if requested_url is not None and requested_url != current["url"]:
                    has_history = connection.execute(
                        "SELECT 1 FROM check_results WHERE website_id = ? LIMIT 1",
                        (website_id,),
                    ).fetchone()
                    if has_history is not None:
                        raise WebsiteTargetChangeRequiresReplacement(
                            "A website URL with check history cannot be changed; "
                            "add a new website to preserve history provenance"
                        )
                connection.execute(
                    f"UPDATE websites SET {', '.join(assignments)} WHERE id = ?", values
                )
                row = connection.execute(
                    "SELECT * FROM websites WHERE id = ?", (website_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            if "websites.url" in str(exc):
                raise DuplicateWebsite("A website with this normalized URL already exists") from exc
            raise RepositoryError(f"Could not update website: {exc}") from exc
        return self._website(row)

    def delete_website(self, website_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM websites WHERE id = ?", (website_id,))
        if cursor.rowcount:
            logger.info("Website and related history deleted: id=%s", website_id)
        return cursor.rowcount > 0

    def get_policy_memory(self, website_id: int) -> PolicyMemory:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM health_states WHERE website_id = ?", (website_id,)
            ).fetchone()
        if row is None:
            return PolicyMemory()
        return PolicyMemory(
            current_state=HealthStatus(str(row["state"])),
            consecutive_failures=int(row["consecutive_failures"]),
            consecutive_successes=int(row["consecutive_successes"]),
            recovery_pending=bool(row["recovery_pending"]),
            recovery_from_down=bool(row["recovery_from_down"]),
        )

    def get_health(self, website_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM health_states WHERE website_id = ?", (website_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "state": row["state"],
            "reason": self._load_json(row["reason_json"]),
            "updated_at": row["updated_at"],
            "consecutive_failures": row["consecutive_failures"],
            "consecutive_successes": row["consecutive_successes"],
            "recovery_pending": bool(row["recovery_pending"]),
        }

    def _check_result_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "website_id": int(row["website_id"]),
            "check_name": str(row["check_name"]),
            "status": str(row["status"]),
            "timestamp": str(row["timestamp"]),
            "duration_ms": row["duration_ms"],
            "value": self._load_json(row["value_json"]),
            "message": str(row["message"]),
            "error": row["error"],
        }

    def _latest_checks(
        self, connection: sqlite3.Connection, website_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT c.* FROM check_results AS c
               JOIN (SELECT check_name, MAX(id) AS latest_id
                     FROM check_results WHERE website_id = ? GROUP BY check_name) AS latest
                 ON latest.latest_id = c.id
               WHERE c.website_id = ? ORDER BY c.check_name""",
            (website_id, website_id),
        ).fetchall()
        return [self._check_result_dict(row) for row in rows]

    def list_dashboard(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM websites ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                website = self._website(row)
                assert website is not None
                health_row = connection.execute(
                    "SELECT * FROM health_states WHERE website_id = ?", (website.id,)
                ).fetchone()
                health = (
                    {
                        "state": health_row["state"],
                        "reason": self._load_json(health_row["reason_json"]),
                        "updated_at": health_row["updated_at"],
                    }
                    if health_row
                    else {"state": "UNKNOWN", "reason": None, "updated_at": None}
                )
                result.append(
                    {
                        **website.to_dict(),
                        "health": health,
                        "latest_checks": self._latest_checks(connection, website.id),
                    }
                )
        return result

    def get_checks(self, website_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM check_results WHERE website_id = ?
                   ORDER BY timestamp DESC, id DESC LIMIT ?""",
                (website_id, limit),
            ).fetchall()
        return [self._check_result_dict(row) for row in rows]

    def get_transitions(self, website_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM health_transitions WHERE website_id = ?
                   ORDER BY occurred_at DESC, id DESC LIMIT ?""",
                (website_id, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "website_id": int(row["website_id"]),
                "from_state": str(row["from_state"]),
                "to_state": str(row["to_state"]),
                "occurred_at": str(row["occurred_at"]),
                "reason": self._load_json(row["reason_json"]),
            }
            for row in rows
        ]

    def get_incidents(
        self, *, website_id: int | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if website_id is not None:
            clauses.append("i.website_id = ?")
            parameters.append(website_id)
        if status is not None:
            clauses.append("i.status = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""SELECT i.*, w.name AS website_name, w.url AS website_url
                    FROM incidents AS i JOIN websites AS w ON w.id = i.website_id
                    {where} ORDER BY i.started_at DESC, i.id DESC LIMIT ?""",
                parameters,
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "website_id": int(row["website_id"]),
                "website_name": str(row["website_name"]),
                "website_url": str(row["website_url"]),
                "started_at": str(row["started_at"]),
                "resolved_at": row["resolved_at"],
                "last_seen_at": str(row["last_seen_at"]),
                "status": str(row["status"]),
                "reason": self._load_json(row["reason_json"]),
                "evidence": self._load_json(row["evidence_json"]),
                "resolution_reason": self._load_json(row["resolution_reason_json"]),
            }
            for row in rows
        ]

    def get_notification_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT n.*, w.name AS website_name
                   FROM notification_events AS n JOIN websites AS w ON w.id = n.website_id
                   ORDER BY n.created_at DESC, n.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "website_id": int(row["website_id"]),
                "website_name": str(row["website_name"]),
                "event_key": str(row["event_key"]),
                "status": str(row["status"]),
                "attempt_count": int(row["attempt_count"]),
                "last_error": row["last_error"],
                "created_at": str(row["created_at"]),
                "delivered_at": row["delivered_at"],
            }
            for row in rows
        ]

    @staticmethod
    def should_notify(
        previous: HealthStatus, current: HealthStatus, reason: dict[str, Any]
    ) -> bool:
        if previous is current:
            return False
        if current is HealthStatus.DOWN:
            return True
        if current is HealthStatus.DEGRADED:
            return previous is HealthStatus.HEALTHY or (
                previous is HealthStatus.UNKNOWN
                and reason.get("code") not in {"RECOVERY_IN_PROGRESS", "RECOVERY_NOT_CONFIRMED"}
            )
        if current is HealthStatus.HEALTHY:
            return previous in {HealthStatus.DOWN, HealthStatus.DEGRADED, HealthStatus.UNKNOWN}
        return False

    def record_cycle(
        self,
        website_id: int,
        check_results: list[CheckResult],
        evaluation: HealthEvaluation,
        *,
        now: datetime | None = None,
        notifications_enabled: bool,
    ) -> dict[str, Any]:
        """Commit observations, state, transitions, incidents, and outbox atomically."""
        stamp_datetime = now or utc_now()
        stamp = timestamp_text(stamp_datetime)
        try:
            with self._connection() as connection:
                website_row = connection.execute(
                    "SELECT * FROM websites WHERE id = ?", (website_id,)
                ).fetchone()
                if website_row is None:
                    raise WebsiteNotFound(f"Website {website_id} does not exist")
                previous_row = connection.execute(
                    "SELECT state FROM health_states WHERE website_id = ?", (website_id,)
                ).fetchone()
                previous = (
                    HealthStatus(str(previous_row["state"]))
                    if previous_row
                    else HealthStatus.UNKNOWN
                )
                for result in check_results:
                    connection.execute(
                        """INSERT INTO check_results
                           (website_id, timestamp, check_name, status, duration_ms,
                            value_json, message, error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            website_id,
                            timestamp_text(result.timestamp),
                            result.check_name,
                            result.status.value,
                            result.duration_ms,
                            self._json(result.value) if result.value is not None else None,
                            result.message,
                            result.error,
                        ),
                    )
                memory = evaluation.memory
                connection.execute(
                    """INSERT INTO health_states
                       (website_id, state, reason_json, updated_at, consecutive_failures,
                        consecutive_successes, recovery_pending, recovery_from_down)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(website_id) DO UPDATE SET
                         state = excluded.state,
                         reason_json = excluded.reason_json,
                         updated_at = excluded.updated_at,
                         consecutive_failures = excluded.consecutive_failures,
                         consecutive_successes = excluded.consecutive_successes,
                         recovery_pending = excluded.recovery_pending,
                         recovery_from_down = excluded.recovery_from_down""",
                    (
                        website_id,
                        evaluation.state.value,
                        self._json(evaluation.reason),
                        stamp,
                        memory.consecutive_failures,
                        memory.consecutive_successes,
                        int(memory.recovery_pending),
                        int(memory.recovery_from_down),
                    ),
                )
                connection.execute(
                    "UPDATE websites SET last_checked_at = ? WHERE id = ?", (stamp, website_id)
                )

                transition_id: int | None = None
                if previous is not evaluation.state:
                    cursor = connection.execute(
                        """INSERT INTO health_transitions
                           (website_id, from_state, to_state, occurred_at, reason_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            website_id,
                            previous.value,
                            evaluation.state.value,
                            stamp,
                            self._json(evaluation.reason),
                        ),
                    )
                    transition_id = int(cursor.lastrowid)

                if evaluation.state is HealthStatus.DOWN:
                    open_incident = connection.execute(
                        "SELECT id FROM incidents WHERE website_id = ? AND status = 'open' "
                        "ORDER BY id DESC LIMIT 1",
                        (website_id,),
                    ).fetchone()
                    if open_incident is None:
                        connection.execute(
                            """INSERT INTO incidents
                               (website_id, started_at, last_seen_at, status, reason_json, evidence_json)
                               VALUES (?, ?, ?, 'open', ?, ?)""",
                            (
                                website_id,
                                stamp,
                                stamp,
                                self._json(evaluation.reason),
                                self._json(evaluation.reason.get("evidence", [])),
                            ),
                        )
                    else:
                        connection.execute(
                            "UPDATE incidents SET last_seen_at = ? WHERE id = ?",
                            (stamp, int(open_incident["id"])),
                        )
                elif evaluation.state is HealthStatus.HEALTHY:
                    open_incident = connection.execute(
                        "SELECT id FROM incidents WHERE website_id = ? AND status = 'open' "
                        "ORDER BY id DESC LIMIT 1",
                        (website_id,),
                    ).fetchone()
                    if open_incident is not None:
                        connection.execute(
                            """UPDATE incidents SET status = 'resolved', resolved_at = ?,
                               last_seen_at = ?, resolution_reason_json = ? WHERE id = ?""",
                            (
                                stamp,
                                stamp,
                                self._json(evaluation.reason),
                                int(open_incident["id"]),
                            ),
                        )

                notification_event_id: int | None = None
                if transition_id is not None and self.should_notify(
                    previous, evaluation.state, evaluation.reason
                ):
                    event_key = f"whm-transition-{transition_id}"
                    payload = {
                        "event": "website.health_transition",
                        "event_key": event_key,
                        "transition_id": transition_id,
                        "website": {
                            "id": website_id,
                            "name": str(website_row["name"]),
                            "url": str(website_row["url"]),
                        },
                        "previous_state": previous.value,
                        "new_state": evaluation.state.value,
                        "timestamp": stamp,
                        "reason": evaluation.reason,
                    }
                    cursor = connection.execute(
                        """INSERT INTO notification_events
                           (website_id, transition_id, event_key, payload_json, status,
                            next_attempt_at, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            website_id,
                            transition_id,
                            event_key,
                            self._json(payload),
                            "pending" if notifications_enabled else "disabled",
                            stamp,
                            stamp,
                        ),
                    )
                    notification_event_id = int(cursor.lastrowid)

                incident_row = connection.execute(
                    """SELECT id, started_at, status FROM incidents
                       WHERE website_id = ? ORDER BY id DESC LIMIT 1""",
                    (website_id,),
                ).fetchone()
                current_row = connection.execute(
                    "SELECT last_checked_at FROM websites WHERE id = ?", (website_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"Could not persist monitoring cycle: {exc}") from exc

        if previous is not evaluation.state:
            logger.info(
                "Website health transition: id=%s previous=%s current=%s reason=%s",
                website_id,
                previous.value,
                evaluation.state.value,
                evaluation.reason.get("code"),
            )
        return {
            "website_id": website_id,
            "checked_at": str(current_row["last_checked_at"]),
            "previous_state": previous.value,
            "state": evaluation.state.value,
            "reason": evaluation.reason,
            "checks": [result.to_dict() for result in check_results],
            "transition_id": transition_id,
            "notification_event_id": notification_event_id,
            "incident": (
                {
                    "id": int(incident_row["id"]),
                    "started_at": str(incident_row["started_at"]),
                    "status": str(incident_row["status"]),
                }
                if incident_row
                else None
            ),
        }

    def pending_notifications(
        self, *, now: datetime | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        stamp = timestamp_text(now or utc_now())
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM notification_events
                   WHERE status = 'pending' AND next_attempt_at <= ?
                   ORDER BY next_attempt_at, id LIMIT ?""",
                (stamp, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_key": str(row["event_key"]),
                "payload": self._load_json(row["payload_json"]),
                "attempt_count": int(row["attempt_count"]),
            }
            for row in rows
        ]

    def finish_notification_attempt(
        self,
        event_id: int,
        *,
        success: bool,
        error: str | None = None,
        retry_at: datetime | None = None,
        permanent_failure: bool = False,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> None:
        stamp = timestamp_text(now or utc_now())
        with self._connection() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM notification_events WHERE id = ?", (event_id,)
            ).fetchone()
            if row is None:
                return
            attempt_count = int(row["attempt_count"]) + 1
            if success:
                status = "delivered"
                delivered_at = stamp
                next_attempt_at = stamp
                last_error = None
            elif permanent_failure or attempt_count >= max_attempts:
                status = "failed"
                delivered_at = None
                next_attempt_at = stamp
                last_error = (error or "delivery failed")[:500]
            else:
                status = "pending"
                delivered_at = None
                next_attempt_at = timestamp_text(retry_at or utc_now())
                last_error = (error or "delivery failed")[:500]
            connection.execute(
                """UPDATE notification_events SET status = ?, attempt_count = ?,
                   next_attempt_at = ?, last_error = ?, delivered_at = ? WHERE id = ?""",
                (status, attempt_count, next_attempt_at, last_error, delivered_at, event_id),
            )

    def ping(self) -> None:
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])
