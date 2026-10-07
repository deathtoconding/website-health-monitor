# AI-assisted development ("vibe coding") guide

This repository is designed to be edited by humans *and* by AI assistants without
losing its contracts. The rules live in [AGENTS.md](../AGENTS.md); this document
explains how to actually work that way: the loop, the guardrails, ready-to-use
prompts, and the review checklist for AI-produced changes.

## 1. What is configured, and why

| File | Tool | Purpose |
|---|---|---|
| [`AGENTS.md`](../AGENTS.md) | Any agent | Canonical project brief: map, commands, non-negotiable rules, testing and documentation duties. |
| [`CLAUDE.md`](../CLAUDE.md) | Claude Code | Pointer to `AGENTS.md` plus Claude-specific notes. |
| [`GEMINI.md`](../GEMINI.md) | Gemini CLI | Pointer to `AGENTS.md` plus Gemini-specific notes. |
| [`.github/copilot-instructions.md`](../.github/copilot-instructions.md) | GitHub Copilot | Always-on repo instructions for chat, completions, and the coding agent. |
| [`.cursor/rules/website-health-monitor.mdc`](../.cursor/rules/website-health-monitor.mdc) | Cursor | Always-applied rule pack with glob scoping. |
| [`.windsurf/rules/website-health-monitor.md`](../.windsurf/rules/website-health-monitor.md) | Windsurf | Always-on condensed rules. |
| [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) | Any editor | Deterministic local hygiene: Ruff, whitespace/EOF, YAML/TOML, large files, private keys, spell check. |
| [`.editorconfig`](../.editorconfig) | Any editor | Whitespace/line-ending defaults so generated files match house style. |
| [`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json) | Codespaces / VS Code | Reproducible Python 3.12 environment with Ruff, pytest, pre-commit, and Copilot preinstalled. |
| [`.vscode/settings.json`](../.vscode/settings.json), [`.vscode/extensions.json`](../.vscode/extensions.json) | VS Code / Copilot | Format-on-save with Ruff, pytest integration, and Conventional Commit instructions for generated commit messages. |
| [`tools/check_repo_consistency.py`](../tools/check_repo_consistency.py) | Any agent | Fails when versions or required documentation drift; runs in `make check`. |

Why so much duplication? Each tool only reads its own file. `AGENTS.md` is the
single source of truth; the rest are small mirrors. When a rule changes, change
`AGENTS.md` first, then the mirror.

## 2. The loop that keeps AI edits safe

1. **Orient.** Tell the assistant to read `AGENTS.md` (or let it happen
   automatically) and to restate the task, the files it expects to touch, and how
   it will verify the change. Correct the plan *before* it writes code.
2. **Scope one story.** One behavior change per session. Mixing "fix DNS reason
   code" with "restyle the dashboard" makes review and rollback harder.
3. **Edit, then test.** Ask for deterministic tests in the same change. Reject
   tests that sleep, use the public network, or assert on timing.
4. **Run the gate yourself.** `make check` is the contract. Assistant claims are
   not evidence; the command output is.
5. **Review the diff like anyone else's code.** Use the checklist in section 5.
6. **Update documentation.** Behavior, configuration, operations, security, and
   changelog entries are part of "done" (see `AGENTS.md` section 7).
7. **Commit with a Conventional Commit title** and let CI confirm it on the
   pull request.

## 3. Prompt recipes

### New or changed health behavior

> Read `AGENTS.md` and `docs/architecture.md`. Change the policy so `<behavior>`.
> Update `app/policy.py`, add tests in `tests/test_policy.py` covering the
> threshold boundary and the `UNKNOWN` path, update the state-policy table in
> `docs/architecture.md`, and add a `CHANGELOG.md` entry. Run `make check` and
> paste the result.

### New API field or route

> Read `AGENTS.md`. Add `<field/route>` to `app/main.py` and
> `app/api_models.py`. Keep the OpenAPI schema accurate, add API tests in
> `tests/test_api.py` (including a validation-error case), update the API table in
> `README.md`, and run `make check`.

### Operator-facing change

> Read `AGENTS.md`. Update `<setting>` in `app/config.py`, `.env.example`, and
> the configuration table in `README.md`. Add a validation test for an invalid
> value in `tests/test_config_and_validation.py`, and mention the operational
> consequence in `docs/runbook.md`. Run `make check`.

### Bug fix with evidence

> Read `AGENTS.md`. Here is the failing behavior and the observed output:
> `<paste>`. Find the root cause, explain it in one paragraph, fix it, add a
> regression test that fails before the fix, and run `make check`.

### Refactor only

> Read `AGENTS.md`. Refactor `<module>` for readability without changing behavior
> or public contracts. Keep the test suite unchanged and green; report the
> `make check` result.

### Review my change

> Read `AGENTS.md` and review `git diff main...HEAD` as a maintainer: contract
> violations, security posture, missing tests, missing docs, and simplifications.
> Rank findings by severity and cite file/line.

## 4. Guardrails to repeat to any assistant

- Do not weaken lint rules, the 90% coverage floor, or CI workflows to pass a
  change; fix the cause.
- Do not add authentication, cloud services, Kubernetes, Redis, queues, ORMs,
  container requirements, telemetry, or a frontend build step. The MVP boundary
  is a product decision, not an oversight.
- Do not change the default loopback bind, and do not imply that public exposure
  is safe — there is no authentication.
- Do not log, store, or commit secrets, webhook URLs, credentials, HTTP response
  bodies, database contents, or real monitored URLs used privately.
- Do not touch `app/policy.py` semantics, the SQLite schema, or
  `pyproject.toml` gates without stating the contract being changed.
- Do not invent commands, environment variables, or files; the real ones are in
  `README.md`, `AGENTS.md`, and `Makefile`.
- Do not claim verification you did not perform. "Not verified" is an acceptable
  and useful answer.

## 5. Review checklist for AI-produced changes

- [ ] `make check` is green and the output was actually shown.
- [ ] The diff touches only what the task requires; no drive-by refactors or
      reformatting of unrelated files.
- [ ] Tests fail without the fix and pass with it; negative paths are covered.
- [ ] No new runtime dependency, or a written justification.
- [ ] Health-state semantics and reason codes still match
      [docs/architecture.md](architecture.md).
- [ ] Security posture unchanged: loopback default, no secrets in code, logs,
      docs, tests, or fixtures.
- [ ] Documentation and `CHANGELOG.md` updated; the version trio matches
      (`make verify`).
- [ ] Error messages are stable, non-secret, and actionable.
- [ ] Anything unverifiable (real-network behavior, platform-specific paths) is
      called out explicitly.

## 6. Running the app from an AI sandbox

Remote sandboxes, Codespaces, and preview environments usually need the server to
listen on all interfaces so the platform's proxy can reach it:

```bash
WHM_HOST=0.0.0.0 WHM_PORT=8000 WHM_DATABASE_PATH=./data/website-health-monitor.db python -m app
```

This is a **session-only override for an ephemeral, access-controlled sandbox**.
Never commit it, never add it to `.env.example`, and never use it on a host whose
port is reachable by other machines — the application has no authentication, and
anyone who can reach the API can add, change, or delete monitored websites. On a
normal machine, use `make run` and keep the documented loopback default.

## 7. What AI assistants are good at here — and not

**Good fits:** adding tests for existing behavior, extending the dashboard, adding
validation with clear rules, refactors inside one module, documentation and
changelog updates, review comments, reproducing a bug from a log.

**Poor fits:** changing the health-policy contract without a written decision,
schema migrations, anything touching the trust boundary or authentication,
performance work without measurements, and multi-module redesigns the maintainer
has not agreed to.

If a task lands in the second group, ask the maintainer first — the review cost is
much higher than the writing cost.
