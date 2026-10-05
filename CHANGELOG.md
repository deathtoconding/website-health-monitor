# Changelog

Notable project changes are recorded here. The first release is an MVP; see the [release checklist](docs/release-checklist.md) for deployment-specific verification.

## [0.1.0] — 2026-10-05

Initial MVP release.

### Added

- FastAPI dashboard and JSON API for adding, editing, pausing, checking, and deleting monitored websites.
- DNS, HTTP, HTTPS certificate, and response-time checks with explainable `HEALTHY`, `DEGRADED`, `DOWN`, and `UNKNOWN` states.
- SQLite persistence for check results, transitions, incidents, and notification delivery events.
- Consecutive-failure and recovery thresholds, a single incident lifecycle, and optional webhook notifications with retries and idempotency keys.
- Liveness/readiness endpoints, an online SQLite backup command, a systemd example, and an operational runbook.
- Unit, API, scheduler, and end-to-end tests; GitHub Actions quality checks for Python 3.11–3.13.
- `GET /` and `HEAD /` support for the dashboard entry point, including proxy/readiness compatibility.

### Release requirements and known limitations

- Requires Python 3.11 or newer; SQLite schema version 1 is initialized on startup.
- The application has no authentication or authorization. Keep it on loopback unless a separately secured access layer and network policy are in place.
- The scheduler and per-website locks are process-local; run one application process.
- The monitor can connect to arbitrary configured targets, including private and loopback addresses. Only trusted operators should be able to use its API.
- Automatic data retention, content assertions, response-time history charts, incident-history UI, and multi-region monitoring are not included in this MVP.
