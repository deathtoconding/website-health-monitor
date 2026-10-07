# Contributing to Website Health Monitor

Thanks for improving Website Health Monitor. This project is a small, self-hosted
Python application with an MVP boundary and a strong operational contract: follow
the rules below and your change will be easy to review and merge.

Working with an AI assistant (Copilot, Claude Code, Cursor, Cody, an agent
sandbox)? Read [AGENTS.md](AGENTS.md) first — it is the canonical instruction file
for automated contributors and is mirrored by the tool-specific files listed in
[Working with AI assistants](#working-with-ai-assistants).

## Ground rules

1. **Keep the MVP boundary.** No cloud service, Kubernetes, Redis, container
   platform, ORM, frontend build step, or paid integration. No authentication
   layer unless a story explicitly adds it together with its threat model.
2. **Protect the security posture.** The application is a single-user,
   loopback-bound tool without authentication. Never change the default bind
   address, never suggest public exposure, and never log credentials, webhook
   URLs, or target query strings.
3. **Stay single-process.** The scheduler, per-site locks, and dispatcher are
   in-process by design. Do not add multi-worker assumptions.
4. **Explain behavior with evidence.** Health states must stay deterministic and
   explainable: every evaluation carries a machine-readable reason code,
   messages, and check evidence.
5. **Keep data safe.** All persisted timestamps are UTC ISO-8601. SQLite schema
   changes require a schema-version bump and a documented forward/rollback story.
6. **Do not weaken quality gates.** Lint rules, the coverage floor, CI workflows,
   and security files are part of the product. Changing one needs a stated reason
   in the pull request.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
make hooks           # optional: install pre-commit hooks for this checkout
make run             # http://127.0.0.1:8000
```

Requirements: Python 3.11+ (CI covers 3.11, 3.12, and 3.13) and a host with
outbound access to the sites you monitor. Tests never touch the public network.

## Daily commands

| Command | Purpose |
|---|---|
| `make check` | The full gate: `ruff check`, `ruff format --check`, repository consistency, pytest with the 90% coverage floor. This is exactly what CI runs. |
| `make test` | Pytest only (with coverage gate). |
| `make format` | Apply Ruff formatting after your edits. |
| `make verify` | Version/metadata/documentation inventory consistency. |
| `make run` | Start the app with documented defaults. |
| `make backup DEST=backups/health.db` | Create and verify an online SQLite backup. |

## Change workflow

1. **Pick one story-sized change.** The plan in
   [docs/project-plan.md](docs/project-plan.md) uses `WHM-xxx` identifiers for
   traceability. Reference the identifier in your branch, commit, or pull
   request when one exists.
2. **Implement, then test.** Add or update deterministic tests in `tests/`.
   Fakes and `httpx.MockTransport` are preferred over real network calls; do not
   add sleeps, live DNS, or real certificates.
3. **Update documentation in the same change.** Behavior changes belong in
   [docs/architecture.md](docs/architecture.md); operator-facing changes in
   [docs/runbook.md](docs/runbook.md) and [SECURITY.md](SECURITY.md); notable
   user-visible changes in [CHANGELOG.md](CHANGELOG.md); configuration changes in
   the `README.md` table and [.env.example](.env.example).
4. **Bump versions together when releasing.** `app/__init__.py`,
   `pyproject.toml`, and the newest `CHANGELOG.md` heading must match —
   `make verify` fails if they drift.
5. **Run `make check`.** A pull request must be green before it is merged.
6. **Open a pull request** using the template and keep it squashed to one
   logical change per PR; the maintainer merges with a squash commit
   (Conventional Commit title, e.g. `fix: keep DNS failures critical`).

## Commit and pull-request style

- **Conventional Commits**: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`, `ci:`, `perf:`, `build:`.
- Imperative, present tense, lower case after the type: `fix: reject URL
  credentials before persistence`.
- Pull requests: describe the behavior change, the risk, and the exact
  verification commands you ran. Note anything intentionally *not* done.
- One review is enough for small changes; the release checklist in
  [docs/release-checklist.md](docs/release-checklist.md) covers releasable
  changes.

## Documentation map

| Document | Owns |
|---|---|
| [README.md](README.md) | Product overview, quick start, configuration, API reference. |
| [docs/architecture.md](docs/architecture.md) | Component boundaries and the health-policy contract. |
| [docs/runbook.md](docs/runbook.md) | Operations: start/verify, logs, backup/restore, troubleshooting. |
| [docs/project-plan.md](docs/project-plan.md) | Scope, decisions, sprints, definition of done, risks. |
| [docs/release-checklist.md](docs/release-checklist.md) | Pre-release verification steps. |
| [docs/ai-assisted-development.md](docs/ai-assisted-development.md) | How to drive AI assistants safely in this repository. |
| [SECURITY.md](SECURITY.md) | Trust boundary, data/secrets handling, hardening, reporting. |

## Working with AI assistants

AI-assisted ("vibe") coding is welcome here, and the repository ships the
configuration to make it safe:

- `AGENTS.md` — canonical, tool-agnostic agent instructions.
- `CLAUDE.md`, `GEMINI.md` — pointers for Claude Code and Gemini CLI.
- `.github/copilot-instructions.md` — GitHub Copilot guidance.
- `.cursor/rules/`, `.windsurf/rules/` — editor rule packs.
- `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes` — deterministic
  local hygiene so agent edits match house style.
- `.devcontainer/devcontainer.json` and `.vscode/` — reproducible Python 3.12
  environment with Ruff, pytest, pre-commit, and Copilot guidance preconfigured.

Rules for AI-assisted changes:

- Treat generated code as a **draft, not a decision**: review every diff and make
  sure you can defend the change in review.
- Never let an assistant weaken tests, coverage thresholds, lint rules, or CI to
  make a change pass. Fix the cause instead.
- Never paste secrets, webhook URLs, database contents, or customer URLs into a
  prompt or an issue.
- Run `make check` after the assistant stops editing, not before.
- If an assistant proposes a new dependency, ask what the standard library or
  existing dependency cannot do first.

## Maintainer setup (repository settings)

Files in the repository cannot turn on GitHub security features. A maintainer
should enable, once per repository:

1. **Settings → Code security and analysis:** Dependency graph, Dependabot
   alerts, Dependabot security updates, secret scanning with push protection, and
   private vulnerability reporting. Without the dependency graph, the
   `Dependency review` workflow reports that it is unsupported (it is
   deliberately non-blocking).
2. **Settings → General → Pull Requests:** allow squash merging only, and enable
   deleting the branch after merge.
3. **Settings → Branches:** protect `main` — require a pull request, require the
   `CI` status checks, require review from a code owner (see
   `.github/CODEOWNERS`), and disallow force-pushes and branch deletion.

The rationale for each setting is documented in
[SECURITY.md](SECURITY.md#repository-security-settings), and
[docs/release-checklist.md](docs/release-checklist.md) verifies them before a
release.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow
[SECURITY.md](SECURITY.md#reporting-a-vulnerability) and use GitHub's private
reporting channel when available.
