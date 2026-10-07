<!--
Thanks for contributing. Keep the PR to one logical change.
Titles use Conventional Commits, e.g. "fix: keep DNS failures critical".
-->

## Summary

<!-- What changes, and why? One or two sentences a reviewer can act on. -->

## Type of change

- [ ] Bug fix (`fix:`)
- [ ] New behavior (`feat:`)
- [ ] Documentation only (`docs:`)
- [ ] Tests only (`test:`)
- [ ] Refactor with no behavior change (`refactor:`)
- [ ] CI, packaging, or tooling (`ci:`, `build:`, `chore:`)

## Related work

<!-- Story/traceability ID (for example WHM-503) or "none". -->

## Verification

<!-- Paste the real command output; do not describe it from memory. -->

- [ ] `make check` passes (ruff check, ruff format --check, metadata verify, pytest ≥90% coverage)
- [ ] New/updated tests fail without this change and pass with it
- [ ] No test requires the public network, credentials, or sleeps

```
$ make check
```

## Documentation

- [ ] `docs/architecture.md` updated for behavior or contract changes
- [ ] `docs/runbook.md` / `SECURITY.md` updated for operational or trust-boundary changes
- [ ] `README.md` and `.env.example` updated for configuration or API changes
- [ ] `CHANGELOG.md` updated and the version trio (`app/__init__.py`, `pyproject.toml`, changelog) matches
- [ ] Not applicable — no user-visible or contract change

## Risk and security

- [ ] No authentication, exposure, or deployment-model change is introduced
- [ ] No secret, webhook URL, credential, database content, or response body is logged, stored, or committed
- [ ] Destructive behavior (schema, retention, deletion) is called out above
- [ ] Anything not verified is stated explicitly

## Notes for the reviewer

<!-- Trade-offs, follow-up work, or "I need a decision on X". -->
