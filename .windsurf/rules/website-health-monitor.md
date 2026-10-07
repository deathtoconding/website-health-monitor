---
trigger: always_on
description: Website Health Monitor project rules (Python/FastAPI/SQLite, single process, no auth)
---

# Website Health Monitor — Windsurf rules

[AGENTS.md](../../AGENTS.md) is the canonical brief for this repository. This
file is the condensed, always-on version.

- **Gate:** `make check` (ruff check, ruff format --check, metadata verify, pytest
  with a 90% coverage floor). Never weaken it to make a change pass.
- **Boundary:** no authentication, cloud services, Kubernetes, Redis, ORMs,
  container requirements, or frontend build steps. One process only.
- **Security:** loopback default only (`WHM_HOST=127.0.0.1`); never log secrets,
  webhook URLs, credentials, or HTTP response bodies.
- **Policy:** `app/policy.py` stays deterministic and I/O-free; every evaluation
  keeps `reason.code`, messages, and check evidence; `UNKNOWN` never counts as a
  pass or a recovery.
- **Checks:** DNS/HTTP/TLS failures are isolated; latency is derived from the HTTP
  duration; TLS for plain HTTP is `not_applicable`.
- **Data:** UTC ISO-8601 timestamps; monitored URL is immutable once history
  exists; SQLite schema changes need a version bump and a rollback note.
- **Versioning:** `app/__init__.py`, `pyproject.toml`, and the newest
  `CHANGELOG.md` heading move together (`make verify` enforces it).
- **Docs:** update `docs/architecture.md`, `docs/runbook.md`, `SECURITY.md`,
  `README.md`, or `.env.example` whenever your change affects them.
- **Tests:** deterministic fakes, `httpx.MockTransport`, injected clocks — never
  the public network, never sleeps.
