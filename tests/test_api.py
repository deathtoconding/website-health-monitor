from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import CheckResult, CheckStatus, utc_now


class ScriptedChecks:
    def __init__(self, outcomes: list[CheckStatus]) -> None:
        self.outcomes = list(outcomes)

    async def run_all(self, website):
        outcome = self.outcomes.pop(0) if self.outcomes else CheckStatus.PASS
        now = utc_now()
        code = 503 if outcome is CheckStatus.FAIL else 200
        return [
            CheckResult(
                "dns", CheckStatus.PASS, now, 3, {"addresses": ["192.0.2.3"]}, "DNS resolved"
            ),
            CheckResult(
                "http",
                outcome,
                now,
                42,
                {"status_code": code, "final_url": website.url, "redirect_count": 0},
                f"HTTP returned {code}",
                "HTTP returned 503" if outcome is CheckStatus.FAIL else None,
            ),
            CheckResult(
                "tls", CheckStatus.PASS, now, 8, {"days_remaining": 90}, "Certificate valid"
            ),
            CheckResult(
                "latency", CheckStatus.PASS, now, 42, {"duration_ms": 42}, "Response time 42 ms"
            ),
        ]


def test_api_full_incident_recovery_and_dashboard_flow(tmp_path) -> None:
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        webhook_url="https://hooks.example.test/notify",
    )
    fake_checks = ScriptedChecks(
        [CheckStatus.FAIL, CheckStatus.FAIL, CheckStatus.PASS, CheckStatus.PASS]
    )
    app = create_app(settings, check_runner=fake_checks, start_background=False)
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["database"] == "ok"

        created = client.post(
            "/api/websites",
            json={"name": "Example", "url": "HTTPS://Example.Test:443", "interval_seconds": 60},
        )
        assert created.status_code == 201
        website = created.json()
        assert website["url"] == "https://example.test/"
        assert website["health"]["state"] == "UNKNOWN"

        duplicate = client.post(
            "/api/websites", json={"name": "Duplicate", "url": "https://EXAMPLE.test:443/"}
        )
        assert duplicate.status_code == 409
        invalid = client.post("/api/websites", json={"name": "Bad", "url": "ftp://example.test"})
        assert invalid.status_code == 422

        first = client.post(f"/api/websites/{website['id']}/check")
        assert first.status_code == 200
        assert first.json()["state"] == "DEGRADED"
        target_change = client.patch(
            f"/api/websites/{website['id']}", json={"url": "https://replacement.test"}
        )
        assert target_change.status_code == 409
        assert "preserve history provenance" in target_change.json()["detail"]
        second = client.post(f"/api/websites/{website['id']}/check")
        assert second.json()["state"] == "DOWN"
        open_incidents = client.get("/api/incidents?status=open").json()
        assert len(open_incidents) == 1
        assert open_incidents[0]["website_id"] == website["id"]

        recovering = client.post(f"/api/websites/{website['id']}/check")
        assert recovering.json()["state"] == "DEGRADED"
        assert client.get("/api/incidents?status=open").json()[0]["id"] == open_incidents[0]["id"]
        healthy = client.post(f"/api/websites/{website['id']}/check")
        assert healthy.json()["state"] == "HEALTHY"
        resolved = client.get("/api/incidents?status=resolved").json()
        assert len(resolved) == 1
        assert resolved[0]["resolved_at"] is not None

        dashboard = client.get("/api/dashboard").json()["websites"]
        assert len(dashboard) == 1
        assert dashboard[0]["health"]["state"] == "HEALTHY"
        assert {check["check_name"] for check in dashboard[0]["latest_checks"]} == {
            "dns",
            "http",
            "latency",
            "tls",
        }
        assert len(client.get(f"/api/websites/{website['id']}/checks").json()) == 16
        assert len(client.get(f"/api/websites/{website['id']}/transitions").json()) == 4
        notifications = client.get("/api/notifications").json()
        assert len(notifications) == 3
        assert all(event["status"] == "pending" for event in notifications)

        details = client.get(f"/api/websites/{website['id']}")
        assert details.status_code == 200
        assert details.json()["active_incident"] is None

        paused = client.patch(f"/api/websites/{website['id']}", json={"enabled": False})
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False
        assert client.get("/api/websites?enabled_only=true").json() == []
        assert client.get("/").status_code == 200
        assert "Website Health Monitor" in client.get("/").text
        head = client.head("/")
        assert head.status_code == 200
        assert head.content == b""
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/health").json()["background_services"] is False

        deleted = client.delete(f"/api/websites/{website['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/websites/{website['id']}").status_code == 404


def test_api_patch_validation_and_missing_manual_check(tmp_path) -> None:
    app = create_app(
        Settings(
            database_path=str(tmp_path / "validation.db"),
            interval_seconds=90,
            timeout_seconds=7,
        ),
        check_runner=ScriptedChecks([]),
        start_background=False,
    )
    with TestClient(app) as client:
        created = client.post("/api/websites", json={"name": "Valid", "url": "https://valid.test"})
        site_id = created.json()["id"]
        assert created.json()["interval_seconds"] == 90
        assert created.json()["timeout_seconds"] == 7
        assert client.patch(f"/api/websites/{site_id}", json={}).status_code == 422
        assert client.patch(f"/api/websites/{site_id}", json={"url": None}).status_code == 422
        assert (
            client.patch(f"/api/websites/{site_id}", json={"interval_seconds": 1}).status_code
            == 422
        )
        assert client.post("/api/websites/999/check").status_code == 404
        assert client.get("/api/websites/999/checks").status_code == 404
        changed_target = client.patch(
            f"/api/websites/{site_id}", json={"url": "https://changed.valid.test/"}
        )
        assert changed_target.status_code == 200
        assert changed_target.json()["url"] == "https://changed.valid.test/"
        assert client.get("/api/health").json()["schema_version"] == 1


def test_default_http_clients_start_and_close_cleanly(tmp_path) -> None:
    app = create_app(
        Settings(database_path=str(tmp_path / "default-clients.db")),
        start_background=False,
    )
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200


def test_readiness_reports_database_failure(tmp_path, monkeypatch) -> None:
    from app.database import Repository

    repository = Repository(str(tmp_path / "unhealthy.db"))
    app = create_app(
        Settings(database_path=str(tmp_path / "unhealthy.db")),
        repository=repository,
        check_runner=ScriptedChecks([]),
        start_background=False,
    )
    with TestClient(app) as client:

        def broken_ping():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(repository, "ping", broken_ping)
        assert client.get("/readyz").status_code == 503
        health = client.get("/api/health").json()
        assert health["status"] == "degraded"
        assert health["database"] == "error"
        assert health["schema_version"] is None
