# CLAUDE.md

Claude Code and other Claude-based agents must follow **[AGENTS.md](AGENTS.md)** —
it is the canonical brief for this repository and contains the full command list,
non-negotiable rules, testing style, and documentation duties.

Claude-specific notes:

- The quality gate is `make check` (`ruff check`, `ruff format --check`,
  repository metadata verification, pytest with a 90% coverage floor). Run it
  after your edits and before summarizing; report the actual output.
- Prefer the repository's own tools over ad-hoc commands: `make test`,
  `make format`, `make verify`, `make run`, `make backup DEST=...`.
- Use plan mode for anything touching `app/policy.py`, `app/database.py`,
  `app/config.py` defaults, `pyproject.toml` gates, or `.github/workflows/`,
  because those files encode contracts other components depend on.
- Do not create commits, branches, or pull requests unless the human asks for
  them; this environment may track work through its own branch.
- Never paste secrets, webhook URLs, or database contents into a prompt, and
  never echo them into files, logs, or test fixtures.
