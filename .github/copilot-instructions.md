# GitHub Copilot instructions

Repository-wide guidance for GitHub Copilot (chat, code completions, and coding
agent). The canonical brief is [AGENTS.md](../AGENTS.md); this file is the
condensed version Copilot reads automatically.

## Project

Self-hosted website health monitor: Python 3.11+, FastAPI, `httpx`, `uvicorn`,
standard-library `sqlite3`, standard-library DNS/TLS checks, and a no-build static
dashboard (`app/static/`). One application process; the scheduler, per-site locks,
and webhook dispatcher are in-process by design.

Health semantics matter more than features: `HEALTHY`, `DEGRADED`, `DOWN`, and
`UNKNOWN` must stay deterministic and explainable, and every evaluation carries a
machine-readable `reason.code`, messages, and check evidence.

## Commands

- `make check` — full quality gate (ruff check, ruff format --check, metadata
  verification, pytest with a 90% coverage floor).
- `make test`, `make format`, `make verify`, `make run`,
  `make backup DEST=backups/health.db`.

## Always

- Keep the module boundaries: `app/checks.py` (measurement), `app/policy.py`
  (decisions, no I/O), `app/database.py` (persistence), `app/monitoring.py`
  (one cycle + lock), `app/scheduler.py` (timing), `app/notifications.py`
  (webhook outbox), `app/main.py` (API/UI).
- Add or update deterministic tests in `tests/` for every behavior change; use
  fakes, injected clocks, and `httpx.MockTransport` instead of the public network.
- Update the matching docs: `docs/architecture.md` for behavior,
  `docs/runbook.md` for operations, `SECURITY.md` for the trust boundary,
  `README.md` and `.env.example` for configuration, `CHANGELOG.md` for
  user-visible changes.
- Keep the version trio in sync: `app/__init__.py`, `pyproject.toml`, and the
  newest `CHANGELOG.md` heading (`make verify` fails otherwise).
- Use Conventional Commit messages (`fix:`, `feat:`, `docs:`, `test:`, `ci:`).

## Never

- Never weaken lint rules, the coverage floor, or `.github/workflows/*` to make a
  change pass.
- Never introduce authentication, cloud services, Kubernetes, Redis, message
  queues, ORMs, container requirements, telemetry, or a frontend build step.
- Never change the default `WHM_HOST=127.0.0.1` bind or imply that public
  exposure is safe; this application has no authentication.
- Never log secrets, webhook URLs, credentials, or HTTP response bodies, and
  never commit `.env`, `data/`, `*.db`, or backups.
- Never rewrite history on `main`.
