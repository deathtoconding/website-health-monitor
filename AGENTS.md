# AGENTS.md — instructions for AI assistants and automated contributors

This file is the **canonical, tool-agnostic brief** for any assistant, agent, or
coding model that edits this repository. `CLAUDE.md`, `GEMINI.md`,
`.github/copilot-instructions.md`, `.cursor/rules/`, and `.windsurf/rules/`
mirror or point here; if they ever disagree, **this file wins**.

Human contributors should read [CONTRIBUTING.md](CONTRIBUTING.md). Operators
should read [README.md](README.md) and [docs/runbook.md](docs/runbook.md).

## 1. What this project is

A free, self-hosted website health monitor. It periodically runs DNS, HTTP, TLS,
and response-time checks against configured websites, turns the evidence into an
explainable `HEALTHY` / `DEGRADED` / `DOWN` / `UNKNOWN` state, persists history,
transitions and incidents in SQLite, serves a static dashboard plus JSON API from
one origin, and can deliver state-transition webhooks.

**Runtime:** Python 3.11+, FastAPI, `httpx` (outbound checks and webhooks),
`uvicorn`, standard-library `sqlite3`, standard-library DNS/TLS, and a
no-build static dashboard (HTML/CSS/JS). **One process only.**

## 2. Repository map

| Path | Responsibility |
|---|---|
| `app/config.py` | Single validated source of runtime settings (`WHM_*` environment variables). |
| `app/models.py` | Domain models, `CheckStatus`, `HealthStatus`, UTC helpers. |
| `app/checks.py` | Isolated DNS/HTTP/TLS checks and latency derivation; exceptions normalized to `UNKNOWN`. |
| `app/policy.py` | Deterministic, I/O-free health state machine over check evidence and persisted memory. |
| `app/database.py` | Explicit SQLite schema, transactions, repository methods, schema version. |
| `app/monitoring.py` | One monitoring cycle plus the per-website lock shared by manual and scheduled runs. |
| `app/scheduler.py` | In-process recurring schedule, per-site intervals, failure isolation. |
| `app/notifications.py` | Durable webhook outbox with bounded retries and idempotency keys. |
| `app/main.py` | FastAPI app factory, routes, UI serving, liveness/readiness, lifecycle. |
| `app/validation.py` | URL normalization/validation (absolute HTTP(S), no credentials). |
| `app/backup.py` | `whm-backup` online SQLite backup with integrity check. |
| `app/filesystem.py` | Owner-only permissions for database and backup paths. |
| `app/static/` | Dashboard assets (`index.html`, `app.css`, `app.js`); no build step. |
| `tests/` | Deterministic pytest suite; no public network, no credentials, no sleeps. |
| `tools/` | Repository maintenance scripts (standard library only). |
| `docs/` | Architecture contract, runbook, project plan, release checklist, AI workflow. |
| `deploy/` | Optional hardened systemd unit for a single-process deployment. |

## 3. Commands

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]'
make check      # ruff check + ruff format --check + metadata verify + pytest (90% coverage)
make test       # pytest only
make format     # apply Ruff formatting
make verify     # versions, metadata, and documentation inventory
make run        # start locally on 127.0.0.1:8000
make backup DEST=backups/health.db
```

`make check` is the definition of "done" for automated edits. Never report a
task as complete when it is red.

## 4. Non-negotiable rules

1. **Never weaken a gate.** Do not edit `[tool.pytest.ini_options]`,
   `[tool.ruff*]`, `pyproject.toml` dependency bounds, or
   `.github/workflows/*` to make a failing change pass. Fix the code, tests, or
   documentation instead. If a gate genuinely must change, explain why in the
   pull request and keep the coverage floor at 90% or higher.
2. **Do not expand scope.** No authentication framework, cloud service,
   Kubernetes, Redis, message queue, ORM, Docker requirement, frontend build
   step, telemetry, or paid integration. Post-MVP items are tracked in
   [docs/project-plan.md](docs/project-plan.md).
3. **Preserve the security boundary.** No authentication exists. Never change
   the default `WHM_HOST=127.0.0.1`, never bind `0.0.0.0` in code, docs, or
   examples, and never suggest exposing the app publicly without a separately
   designed access layer. Never log credentials, webhook URLs, or database
   contents, and never store HTTP response bodies.
4. **Stay single-process.** In-process scheduler, locks, and dispatcher are the
   supported model. Do not introduce multi-worker or multi-host assumptions.
5. **Keep the policy pure and explainable.** `app/policy.py` performs no I/O and
   every evaluation keeps its machine-readable `reason.code`, messages, and
   check evidence. `UNKNOWN` is never treated as a pass or a recovery.
6. **Checks must stay isolated.** A failure or exception in one check may not
   skip, cancel, or corrupt its siblings.
7. **Respect data contracts.** All stored timestamps are UTC ISO-8601. A website
   URL is immutable once it has check history. Cascading deletes are intentional;
   flag destructive changes before making them.
8. **No new runtime dependency** without a short justification in the pull
   request; prefer the standard library and existing dependencies.
9. **Never commit secrets or runtime state.** `.env*` (except the example),
   `data/`, `*.db`, and backups stay untracked. Tests must not require the public
   network or real credentials.
10. **Never rewrite published history.** Do not force-push, rebase, or amend
    commits that are already on `main`; change history only on your own branch.

## 5. How to change code

- Match the surrounding style: `from __future__ import annotations`, `snake_case`,
  type hints on public functions, module loggers via `logging.getLogger(__name__)`,
  docstrings that explain *why*.
- Keep functions small and side-effect free where practical; put I/O at the edges
  (`checks`, `database`, `notifications`, `main`) and decisions in `policy`.
- Error messages are user-facing strings: stable, non-secret, and actionable.
- Prefer dataclasses/enums already in `app/models.py` over raw strings and dicts.
- If you change behavior, change the matching section of
  [docs/architecture.md](docs/architecture.md) in the same commit.

## 6. How to test

- Add tests under `tests/`, following the existing file split
  (`test_api.py`, `test_checks.py`, `test_policy.py`, `test_database.py`,
  `test_monitoring_scheduler.py`, `test_notifications_and_e2e.py`, ...).
- Use fake check runners, fake clocks, `httpx.MockTransport`, injected resolvers,
  and injected TLS probes. No sleeping, no live DNS, no real certificates, no
  timing assumptions.
- Cover the negative path: invalid input, duplicate URL, unknown evidence,
  threshold boundaries, interrupted recovery, webhook retry exhaustion, and
  scheduler isolation.
- Keep the 90% application line-coverage floor satisfied; add tests rather than
  excluding code from coverage.

## 7. Documentation duties

Update documentation in the **same** change as the behavior:

| Change | Documents to update |
|---|---|
| Runtime/health-policy behavior | `docs/architecture.md` |
| Configuration or defaults | `README.md` table + `.env.example` + `app/config.py` validation |
| API routes/schemas | `README.md` API table (OpenAPI is generated) |
| Operations, backup, troubleshooting | `docs/runbook.md` |
| Security posture, secrets, exposure | `SECURITY.md` |
| Scope, sprints, decisions, risks | `docs/project-plan.md` |
| User-visible differences | `CHANGELOG.md` and the version trio (see below) |
| Release procedure | `docs/release-checklist.md` |
| Agent workflow | `AGENTS.md` + tool mirrors |

### Version trio

`app/__init__.py`, `[project].version` in `pyproject.toml`, and the newest
`## [x.y.z]` heading in `CHANGELOG.md` must always match. `make verify` (and
`tests/test_repo_metadata.py`) fails otherwise. Semver: patch = fixes and docs,
minor = new behavior, major = breaking contract change.

## 8. Pull requests from an assistant

1. Run `make check` and paste the real result.
2. Summarize: what changed, why, user-visible impact, risk, and any follow-up.
3. Call out anything you could not verify (for example, real-network behavior or
   a platform-specific path) instead of implying it was tested.
4. Use Conventional Commit titles; the maintainer merges with a squash commit.
5. Do not open a pull request that changes security posture, deployment model, or
   data retention without explicitly asking the maintainer first.
