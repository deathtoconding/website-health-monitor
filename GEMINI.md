# GEMINI.md

Gemini CLI and other Gemini-based agents must follow **[AGENTS.md](AGENTS.md)** —
it is the canonical brief for this repository (commands, rules, testing style,
documentation duties, and pull-request expectations).

Gemini-specific notes:

- Always run `make check` before claiming a task is complete and include the real
  output in your summary.
- Prefer `make test`, `make format`, `make verify`, `make run`, and
  `make backup DEST=...` over inventing new invocations.
- Treat `app/policy.py`, `app/database.py`, `app/config.py`, `pyproject.toml`, and
  `.github/workflows/` as contract files: explain any proposed change there
  before making it.
- Do not paste secrets, webhook URLs, or database contents into prompts or files.
