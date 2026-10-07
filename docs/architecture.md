# Architecture and behavior contract

## Component layout

```text
Static dashboard (same origin)
          │ JSON
          ▼
FastAPI routes ── Website validation ── SQLite repository
          │                              │
          ▼                              ├─ websites / current health
Monitoring service                       ├─ check history / transitions
  ├─ DNS (async resolver)                ├─ incidents
  ├─ HTTP (HTTPX)                        └─ webhook outbox
  ├─ TLS (stdlib ssl/socket)                          │
  ├─ latency (HTTP duration)                         ▼
  └─ policy engine ◄── persisted memory       Webhook dispatcher
          ▲
          │
Async scheduler ── enabled sites / individual intervals
```

The central rule is: **checks measure reality → policy interprets evidence → incident manager tracks events → dashboard/notifications communicate them.** The checks do not mutate incidents, and the policy does not perform I/O.

## Runtime boundaries

- `app/config.py`: single validated source of runtime defaults and thresholds.
- `app/models.py`: shared domain models, status enums, and UTC timestamp helpers.
- `app/validation.py`: URL normalization/validation; the only place that decides what a valid monitored target is.
- `app/checks.py`: independently isolated DNS, HTTP, TLS, and latency checks. DNS/HTTP/TLS run concurrently; a failure in one does not cancel sibling checks. The latency result is derived from the HTTP duration rather than a second request.
- `app/policy.py`: deterministic, explainable state machine over normalized evidence and persisted `PolicyMemory`.
- `app/database.py`: explicit SQLite schema and transactions. Each completed cycle writes observations, current state/counters, state transition, incident lifecycle, and notification event atomically.
- `app/monitoring.py`: one cycle and per-website lock shared by scheduled/manual checks.
- `app/scheduler.py`: in-process recurring schedule with per-website interval, bounded check concurrency, and per-site exception isolation.
- `app/notifications.py`: optional asynchronous webhook outbox delivery and retries.
- `app/backup.py` and `app/filesystem.py`: online, integrity-checked SQLite backup and owner-only POSIX permissions.
- `app/main.py`: API, UI serving, health endpoints, and application lifecycle.
- `app/static/`: no-build static dashboard.

SQLite uses foreign keys, WAL mode, a busy timeout, a schema version, cascading deletion of a website's own history, and owner-only database/backup file modes on POSIX (new parent directories are created with restrictive permissions). SQLite writes are brief and synchronous; this workload is intended for a modest self-hosted deployment. Do not run multiple application workers: the in-process schedule and locks are not a distributed lock or leader-election system.

## Health checks

| Check | Criticality | Result contract |
|---|---|---|
| DNS | Critical | Resolve hostname and record sorted addresses/duration; IP literals are recorded directly. Lookup failure/timeout is `FAIL`. |
| HTTP | Critical | Follow redirects; record final URL, status, duration. 2xx/3xx is `PASS`; 4xx/5xx, timeout, or request failure is `FAIL`. |
| TLS | Critical for HTTPS | Verify chain and hostname using the system trust store; record expiry. Invalid/expired/mismatched certificate is `FAIL`; expiry within configured warning window is `WARN`. For HTTP, return `UNKNOWN` with `value.not_applicable=true`; policy excludes it. |
| Latency | Non-critical | Use HTTP response duration: below warning threshold `PASS`, from warning threshold through failure threshold `WARN`, above failure threshold `FAIL`. |
| Content | Post-MVP | Not executed in this release. |

Any unhandled implementation exception is logged with traceback and normalized to `UNKNOWN` for that check; expected network failures have stable, user-facing messages and typed status. No raw response body is stored.

## State policy

Applicable critical results are DNS + HTTP + TLS for HTTPS, and DNS + HTTP for HTTP.

| Evidence/history | Next state | Counter behavior |
|---|---|---|
| Any critical `FAIL`, failure streak below threshold | `DEGRADED` / `CRITICAL_FAILURE_SUSPECTED` | Increment failures; reset clean successes. |
| Any critical `FAIL`, threshold reached | `DOWN` / `CRITICAL_FAILURE_THRESHOLD_EXCEEDED` | Increment failures; same incident remains open. |
| Failure while recovering an open/down episode | `DOWN` / `RECOVERY_INTERRUPTED` | Reopen/retain down state; the incident is not closed. |
| Critical `WARN` | `DEGRADED` | Break the clean recovery streak; warning is not counted as a failure. |
| Incomplete critical evidence and no decisive failure | `UNKNOWN` | Break consecutive failure/success streaks; preserve unresolved recovery/down context. |
| All applicable critical results `PASS`, recovery not pending | `HEALTHY`, or `DEGRADED` if non-critical issues exist | Clear failure/success streaks. |
| First complete pass after a critical failure/down episode | `DEGRADED` / `RECOVERY_IN_PROGRESS` | Increment recovery successes; retain episode context. |
| Required consecutive complete passes reached | Evidence-based `HEALTHY` or `DEGRADED` | Clear policy memory. |
| Latency `WARN`/`FAIL` or other non-critical warning/failure | At least `DEGRADED`; never `DOWN` alone | Critical counters do not increment. |

`UNKNOWN` means there was not enough trustworthy critical evidence to decide. It is not an implicit healthy state and does not resolve an incident. A single transient critical failure produces `DEGRADED`, not `DOWN`. The policy applies only actual configured check results; missing critical checks are treated as unknown.

Every evaluation contains a machine-readable `reason.code`, explanatory `message`, `failed_checks`, `warning_checks`, `unknown_checks`, and compact check `evidence`. A healthy evaluation has a reason too.

## Transitions and incidents

- Store a transition only when the overall state value changes; a reason update by itself does not create a new state-transition event.
- `DOWN` opens an incident if there is not already one open. Repeated `DOWN` updates `last_seen_at` without duplicate incidents or repeated alerts.
- `UNKNOWN`/`DEGRADED` does not close an incident. A confirmed `HEALTHY` state resolves the open incident and records `resolved_at` plus resolution reason.
- The policy's first recovery pass is represented as `DEGRADED` because `RECOVERING` is not in the public state enum.

## Webhook outbox

A state transition that is alert-worthy creates a unique event keyed by its transition ID in the same SQLite transaction. The event is `pending` only when a webhook URL is configured; otherwise its status is `disabled` and it is still inspectable. The dispatcher sends JSON with an `Idempotency-Key` header. 2xx is delivered; retryable network failures, 408/425/429, and 5xx use exponential backoff; other non-2xx responses are marked failed. Attempts are capped. A delivery may be repeated if the app dies after the receiver accepts it but before SQLite records success; receivers should honor the idempotency key.

Alert-worthy transitions include a transition to `DOWN`, a fresh transition from `HEALTHY`/`UNKNOWN` to `DEGRADED`, and confirmed recovery to `HEALTHY`. A first recovery pass from `UNKNOWN`/`DOWN` is not sent as a new degradation alert. Repeated cycles in the same state do not create new events.

## API / storage

The OpenAPI contract is served at `/docs`. Main API resources:

- `/api/websites`: website CRUD; URL duplicates are normalized and rejected. A target URL is immutable after the first check so stored history is never relabeled; add a new website to monitor a replacement target.
- `/api/dashboard`: site summary, health reason, latest check per check type.
- `/api/websites/{id}/checks` and `/transitions`: bounded read-only history.
- `/api/incidents`: open/resolved incident records.
- `/api/notifications`: delivery status without returning stored payloads.
- `/healthz`: process liveness; `/readyz`: SQLite readiness; `/api/health`: additional status/version/schema details.

All date-times persisted by the app are UTC ISO-8601 strings. The SQLite schema is at version 1 and created idempotently at startup. Automatic data retention and destructive migrations are intentionally absent; take backups and plan database growth.

## Failure isolation and concurrency

- A failed DNS/HTTP/TLS check does not skip its sibling checks or prevent the policy from evaluating evidence.
- A scheduled site's exception is logged and does not terminate the scheduler or another site's cycle.
- One asyncio lock per website prevents overlap between manual and scheduled cycles within the single process and serializes website mutations against an active cycle; conflicting API operations receive HTTP 409, scheduler overlap is skipped.
- A slow check is bounded by its configured timeout; global individual-check concurrency is bounded.
- Process restart loses only in-memory due-time bookkeeping; persistent last-check timestamps let the scheduler avoid unnecessary catch-up bursts. Enabled sites with no prior check run shortly after startup. All scheduling and mutation happens on the event loop thread, so the repository's per-operation SQLite connections never race inside one process.

## Repository automation and contracts

| Contract | Owner | Enforced by |
|---|---|---|
| Health-policy semantics and reason codes | `app/policy.py` + this document | `tests/test_policy.py`, review |
| SQLite schema and timestamp format | `app/database.py` (`SCHEMA_VERSION`) | `tests/test_database.py`, backup/restore rehearsal |
| Configuration defaults and validation | `app/config.py` + `README.md` + `.env.example` | `tests/test_config_and_validation.py` |
| Runtime settings surface | `Settings` fields (`WHM_*`) | startup validation, `SECURITY.md` |
| Version trio (`app/__init__.py`, `pyproject.toml`, newest `CHANGELOG.md` heading) | maintainers | `make verify` (`tools/check_repo_consistency.py`), `tests/test_repo_metadata.py` |
| Documentation inventory (project, GitHub, and agent files) | maintainers | `make verify` |
| Formatting and linting | Ruff configuration in `pyproject.toml` | `make check`, pre-commit |
| Test determinism (no live network, no sleeps) | `tests/` | review + CI on Python 3.11–3.13 |
| Dependency updates | Dependabot + dependency review | CI on pull requests |

Two HTTP clients exist on purpose and must not be conflated:
`httpx` is the **runtime** client used by `app/checks.py` and
`app/notifications.py` for outbound monitoring and webhook delivery, while
`httpx2` is a **development-only** dependency that Starlette's `TestClient`
prefers (it deprecates `httpx` for tests). Migrating runtime code to `httpx2` is a
deliberate, separately reviewed change because it would alter the TLS/trust-store
behavior of real checks.
