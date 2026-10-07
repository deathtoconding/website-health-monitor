# Website Health Monitor

[![CI](https://github.com/deathtoconding/website-health-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/deathtoconding/website-health-monitor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

A free, self-hosted application for continuously checking websites and explaining whether they are `HEALTHY`, `DEGRADED`, `DOWN`, or `UNKNOWN`. It uses Python, FastAPI, HTTPX, standard-library DNS/TLS checks, SQLite, and a static HTML/CSS/JavaScript dashboard. No cloud account, paid service, Redis, container platform, or frontend build step is required.

## MVP capabilities

- Add, edit, enable/pause, run a manual check, and delete monitored websites.
- Periodically run DNS, HTTP, TLS (HTTPS only), and response-time checks.
- Persist every check, health state transition, incident, and notification event in SQLite.
- Apply configurable consecutive-failure/recovery thresholds and retain machine-readable reasons/evidence.
- Open one incident on confirmed `DOWN`; resolve it only after confirmed `HEALTHY` recovery.
- Serve a responsive dashboard and JSON API from the same origin.
- Optionally deliver state-transition webhooks with a durable outbox, idempotency key, and bounded retry policy.
- Expose liveness/readiness endpoints and a live-safe SQLite backup command.

**Intentionally post-MVP:** content assertions, response-time history charts, incident-history UI, and multi-region monitoring. The API stores check and incident history; there is no automatic retention/deletion policy yet.

## Requirements and quick start

- Python 3.11 or newer
- A host with outbound network access to the sites you want monitored

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m app
```

Open **http://127.0.0.1:8000**. The API reference is at **http://127.0.0.1:8000/docs**. The app creates `./data/website-health-monitor.db` on first start. The `data/` directory and SQLite files are ignored by Git.

The default server bind is `127.0.0.1` because the MVP has **no authentication**. Do not change it to `0.0.0.0` or expose it through a public reverse proxy unless you add appropriate access controls and network restrictions yourself. See [SECURITY.md](SECURITY.md).

## Configuration

Settings use the `WHM_` environment-variable prefix. They are validated on startup. Copy `.env.example` for reference; the application deliberately does not auto-load `.env` files. Export variables in your shell or configure the service manager's environment file.

| Variable | Default | Meaning |
|---|---:|---|
| `WHM_HOST` | `127.0.0.1` | Bind address; keep loopback unless access is separately secured. |
| `WHM_PORT` | `8000` | HTTP listen port. |
| `WHM_DATABASE_PATH` | `./data/website-health-monitor.db` | SQLite state file; parent directory is created. |
| `WHM_INTERVAL_SECONDS` | `60` | Default check interval for new websites (5–86400 seconds). |
| `WHM_TIMEOUT_SECONDS` | `10` | Default per-check timeout for new websites (1–120 seconds). |
| `WHM_FAILURE_THRESHOLD` | `2` | Consecutive critical failures before `DOWN` (1–20). |
| `WHM_RECOVERY_THRESHOLD` | `2` | Consecutive fully passing critical cycles before recovery (1–20). |
| `WHM_LATENCY_WARN_MS` | `1000` | At/above this response time, latency is `WARN`. |
| `WHM_LATENCY_FAIL_MS` | `3000` | Above this response time, latency is `FAIL`; latency is non-critical. |
| `WHM_TLS_WARNING_DAYS` | `30` | Certificate validity at or below this many days is `WARN`. |
| `WHM_SCHEDULER_POLL_SECONDS` | `1` | Scheduler polling cadence (0.1–60 seconds). |
| `WHM_MAX_CONCURRENT_CHECKS` | `20` | Maximum individual network checks in flight. |
| `WHM_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `WHM_WEBHOOK_URL` | unset | Optional HTTP(S) state-transition notification endpoint. |
| `WHM_WEBHOOK_TIMEOUT_SECONDS` | `5` | Webhook request timeout. |
| `WHM_WEBHOOK_MAX_ATTEMPTS` | `5` | Maximum webhook attempts before the event is marked failed. |
| `WHM_NOTIFICATION_POLL_SECONDS` | `2` | Outbox polling cadence. |

Each site can override its interval and timeout. Policy thresholds are centralized and shared by the checks, scheduler, API defaults, and documentation.

## How health is classified

- DNS and HTTP are critical. TLS is critical for HTTPS; TLS is `not_applicable` for HTTP and does not make an HTTP site unknown.
- Critical failure #1 → `DEGRADED` (suspected); the configured consecutive failure threshold → `DOWN`.
- Recovery requires consecutive cycles where every applicable critical check is `PASS`. The first success is `DEGRADED` with reason `RECOVERY_IN_PROGRESS`; the threshold success returns to the evidence-based health state.
- Missing/unknown critical evidence → `UNKNOWN`; it is neither a pass nor a recovery and cannot resolve an incident.
- TLS warnings and latency warnings/failures produce `DEGRADED`, never `DOWN` by themselves.
- HTTP 2xx/3xx responses pass; 4xx/5xx, connection failures, and timeouts fail. Redirects are followed and the final URL is recorded.
- Every result includes a reason code, message, affected checks, and diagnostic evidence. Full policy semantics are in [docs/architecture.md](docs/architecture.md).

## API quick reference

The dashboard uses this same-origin API. Interactive OpenAPI documentation is available at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dashboard` | Dashboard data, current health, latest checks. |
| `GET` / `POST` | `/api/websites` | List/create monitored websites. |
| `GET` / `PATCH` / `DELETE` | `/api/websites/{id}` | Read, update, or delete a website and its stored history. |
| `POST` | `/api/websites/{id}/check` | Run a monitoring cycle immediately. |
| `GET` | `/api/websites/{id}/checks` | Recent check results (`limit` 1–500). |
| `GET` | `/api/websites/{id}/transitions` | Recent health transitions. |
| `GET` | `/api/incidents` | Open/resolved incidents. |
| `GET` | `/api/notifications` | Recent webhook delivery state (payloads are not returned). |
| `GET` | `/healthz`, `/readyz`, `/api/health` | Liveness, DB readiness, and application details. |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/websites \
  -H 'Content-Type: application/json' \
  -d '{"name":"Example","url":"https://example.com","interval_seconds":60}'
```

Only absolute HTTP/HTTPS URLs are accepted. URLs are normalized before duplicate detection; fragments/default ports are removed, host casing is normalized, and URL credentials are rejected. A URL may be edited before the first check; after history exists, the target is immutable so old evidence cannot be relabeled. Add a new website to monitor a different URL.

## Quality checks

```bash
make check             # ruff check + ruff format --check + metadata verify + pytest (90% coverage)
make test              # pytest only
make format            # Apply Ruff formatting
make verify            # versions, packaging metadata, and documentation inventory
make hooks             # Install pre-commit hooks for this checkout
make run               # Start the local app (loopback only)
make backup DEST=backups/health-monitor-YYYYMMDD.db
```

Tests use mock DNS/TLS/HTTP transports; the suite does not require public network access, credentials, or real certificates. Pytest enforces a 90% application line-coverage floor, and `tools/check_repo_consistency.py` fails if the version trio (`app/__init__.py`, `pyproject.toml`, newest `CHANGELOG.md` heading) or the documentation inventory drifts. See [docs/release-checklist.md](docs/release-checklist.md) before producing a release.

GitHub runs the same gate on Python 3.11, 3.12, and 3.13 for every push and pull request, adds a dependency review on pull requests, and tracks updates with Dependabot. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## Contributing and AI-assisted development

Human contributors: start with [CONTRIBUTING.md](CONTRIBUTING.md) (setup, commit style, documentation duties, review expectations).

AI assistants and agent tools: [AGENTS.md](AGENTS.md) is the canonical brief, mirrored for [Claude Code](CLAUDE.md), [Gemini CLI](GEMINI.md), [GitHub Copilot](.github/copilot-instructions.md), [Cursor](.cursor/rules/website-health-monitor.mdc), and [Windsurf](.windsurf/rules/website-health-monitor.md). The workflow — guardrails, prompt recipes, and the review checklist for generated changes — is documented in [docs/ai-assisted-development.md](docs/ai-assisted-development.md).

The one rule that matters most: `make check` is the contract. No assistant may weaken lint rules, the coverage floor, or CI to make a change pass.

## Operations and project documentation

- [Changelog and release notes](CHANGELOG.md)
- [Architecture and health-policy contract](docs/architecture.md)
- [Runbook, backup, restore, and troubleshooting](docs/runbook.md)
- [Security boundary and hardening guidance](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md)
- [AI-assisted development guide](docs/ai-assisted-development.md) and [agent instructions](AGENTS.md)
- [Agile plan, story traceability, and implementation decisions](docs/project-plan.md)
- [Release checklist](docs/release-checklist.md)
- Optional Linux systemd unit: [`deploy/website-health-monitor.service`](deploy/website-health-monitor.service)

This MVP is designed for **one application process**. Its scheduler locks are in-process and do not coordinate multiple Uvicorn workers or multiple hosts; run with the default single worker.

## License

[MIT](LICENSE) © 2026 deathtoconding. The software is provided as-is: monitoring results are evidence from one network location, not an uptime guarantee.
