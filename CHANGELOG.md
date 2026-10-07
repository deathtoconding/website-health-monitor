# Changelog

Notable project changes are recorded here. See the [release checklist](docs/release-checklist.md) for deployment-specific verification.

## [0.2.0] — 2026-10-07

Documentation, repository automation, and contributor-tooling release. No change to
the health-policy contract, the SQLite schema (still version 1), the API, or the
security posture.

### Added

- Complete contributor documentation: [CONTRIBUTING.md](CONTRIBUTING.md),
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [LICENSE](LICENSE) (MIT), and the
  [AI-assisted development guide](docs/ai-assisted-development.md).
- Agent guidance for the tools contributors actually use:
  [AGENTS.md](AGENTS.md) (canonical), `CLAUDE.md`, `GEMINI.md`,
  `.github/copilot-instructions.md`, `.cursor/rules/`, `.windsurf/rules/`.
- Editor and local-hygiene settings: `.editorconfig`, `.gitattributes`,
  `.pre-commit-config.yaml` (Ruff, whitespace/EOF, YAML/TOML, large files, private
  keys, codespell), `.devcontainer/devcontainer.json` for Codespaces/VS Code, and
  `.vscode/settings.json`/`extensions.json` (format-on-save with Ruff, pytest
  integration, Conventional Commit guidance for generated messages).
- GitHub configuration: pull-request and issue templates, `CODEOWNERS`,
  Dependabot updates for pip/actions/devcontainers, and a dependency-review
  workflow for pull requests.
- `tools/check_repo_consistency.py` plus `tests/test_repo_metadata.py` and the
  `make verify` target: fails when the package version, `pyproject.toml` version,
  newest changelog heading, or the required documentation inventory drift.
- Packaging metadata: license (SPDX), authors, keywords, classifiers, and project
  URLs; the wheel now ships the MIT license text.

### Changed

- `make check` now runs `ruff check`, `ruff format --check`, `make verify`, and the
  coverage-gated test suite; `make help`, `make wheel`, and `make clean` were added.
- CI reports the exact tool versions it used, supersedes in-flight runs for the same
  ref, and applies per-job timeouts, so a failure is diagnosable from the log alone.
- Development tooling versions are bounded deliberately (formatter, test runner,
  pre-commit) so a new style release is adopted by decision instead of silently
  reformatting the repository inside CI.
- README gained CI/license/Python/style badges, a contributing section, an
  AI-assistant section, and a license section.
- Internal readability refactors with no behavior change: the HTTP check message is
  built once, and the interrupted-recovery condition in the policy mirrors
  `recovery_from_down` directly.

### Notes

- `httpx2` is a development-only requirement: Starlette's `TestClient` prefers it
  and deprecates `httpx` for tests, while application runtime code continues to use
  `httpx` for outbound checks and webhook delivery.
- Upgrading from 0.1.0 needs no data migration: schema version 1 is unchanged and
  the database and configuration formats are compatible.

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

### Release requirements and known limitations (0.1.0)

- Requires Python 3.11 or newer; SQLite schema version 1 is initialized on startup.
- The application has no authentication or authorization. Keep it on loopback unless a separately secured access layer and network policy are in place.
- The scheduler and per-website locks are process-local; run one application process.
- The monitor can connect to arbitrary configured targets, including private and loopback addresses. Only trusted operators should be able to use its API.
- Automatic data retention, content assertions, response-time history charts, incident-history UI, and multi-region monitoring are not included in this MVP.
