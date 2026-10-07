# MVP release checklist

## Scope and readiness

- [ ] Only approved 143-SP MVP scope is included; 18-SP post-MVP scope is deferred.
- [ ] Story/acceptance traceability is updated in `docs/project-plan.md` if scope changes.
- [ ] Release notes state Python version, configuration changes, schema version, and known limitations.
- [ ] No authentication, cloud platform, Redis, Kubernetes, content check, or paid integration is implied by the release.

## Quality gates

- [ ] Start from a clean Python 3.11+ environment and install `python -m pip install -e '.[dev]'`.
- [ ] `make check` passes (`ruff check .`, `ruff format --check .`, `make verify`, and the complete pytest suite with the 90% application line-coverage gate).
- [ ] `make verify` reports a consistent version trio (`app/__init__.py`, `pyproject.toml`, newest `CHANGELOG.md` heading) and a complete documentation inventory.
- [ ] CI is green on Python 3.11, 3.12, and 3.13 for the exact revision being released, including the wheel-asset and `pip check` steps.
- [ ] The dependency-review check on the release pull request reports no new high/critical advisories; Dependabot pull requests are either merged or explicitly deferred.
- [ ] Contributor-facing docs match reality: `README.md`, `CONTRIBUTING.md`, `AGENTS.md` (and its tool mirrors), `docs/architecture.md`, `docs/runbook.md`, `docs/ai-assisted-development.md`.
- [ ] `python -m compileall -q app tests` passes.
- [ ] A built wheel contains `app/static/index.html`, `app/static/app.css`, and `app/static/app.js`.
- [ ] Start `python -m app` with a temporary `WHM_DATABASE_PATH`; `/healthz` and `/readyz` return success; root dashboard and `/docs` load.
- [ ] Exercise add → immediate check → failure threshold → open incident → two-pass recovery → resolved incident using a controlled target/test environment.
- [ ] Confirm tests do not require public network access and no credentials are present in source, logs, test fixtures, or `.env.example`.

## Persistence / operations

- [ ] Confirm database path, owner, free disk space, and backup location.
- [ ] Create a live backup with `whm-backup`; verify integrity and perform a restore rehearsal on a separate path.
- [ ] Confirm one Uvicorn worker / one process is configured.
- [ ] If using webhooks, verify a test payload, idempotency handling, retries, and no secret exposure in logs or health endpoints.
- [ ] Confirm default bind remains loopback or document the independently secured remote-access boundary.
- [ ] Review `SECURITY.md`, runbook, and systemd unit for the actual deployment environment.
- [ ] Confirm restart recovery: websites and state/history remain; enabled websites resume; an open incident is not silently resolved.

## Release and rollback

- [ ] Tag/version the exact tested source revision using the repository's release process.
- [ ] Back up the database before upgrading.
- [ ] Schema is currently version 1 with idempotent initialization; if a future release changes schema, document forward/rollback compatibility before shipping.
- [ ] Roll back by stopping the service and restoring the pre-upgrade database/source as a matched pair; preserve the failed DB/logs for diagnosis.
- [ ] After deployment, check `/readyz`, dashboard counts, recent check timestamps, open incidents, and notification delivery status.
