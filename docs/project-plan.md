# Website Health Monitor — Implementation Plan and Decisions

**Status:** Approved implementation baseline

**Scope:** Supplied MVP (143 story points); supplied post-MVP scope (18 points) is deferred

**Project constraint:** Local/self-hosted, Python, SQLite, no authentication in MVP

## 1. Repository assessment

The repository started with only a placeholder `README.md`; there was no application, dependency manifest, test suite, deployment setup, or existing behavior to preserve. Implementation therefore starts with WHM-101 and proceeds in dependency order. This document is the working project-management record; story IDs below provide traceability to the supplied Jira plan.

## 2. Architecture decisions

| Area | Decision | Rationale / operational consequence |
|---|---|---|
| Runtime/API | Python 3.11+, FastAPI, Uvicorn | Small, documented async API; OpenAPI is available locally. |
| Persistence | SQLite via the standard-library `sqlite3` module | Keeps deployment self-contained; one connection per operation, foreign keys, WAL, busy timeout, transactional state updates, schema version. No ORM or migration service. |
| Network checks | HTTPX for HTTP; `asyncio.getaddrinfo` for DNS; standard-library `ssl`/`socket` for TLS | Follows the plan while keeping the check behavior testable through injected dependencies. |
| Scheduler | One asyncio background service, per-site due times, bounded concurrency, and per-site locks | No external scheduler or Redis. A manual check and scheduled check cannot overlap in the same process. Run a single application process; SQLite and in-process locks do not coordinate multiple workers. |
| UI | Static HTML, CSS, and JavaScript served by FastAPI | No Node toolchain or frontend build is needed. |
| Target identity | A website URL can be edited before the first stored check; after monitoring begins, changing the URL returns HTTP 409 and requires adding a new website | Prevents old checks/incidents from being falsely attributed to a different origin. Configuration-only edits (name, interval, timeout, enabled) remain available. |
| Notifications | Optional configured HTTP webhook with durable SQLite outbox, transition deduplication, retry/backoff, and idempotency key | No paid provider or email-server dependency. With no webhook configured, transitions remain recorded as disabled notifications; no network delivery is attempted. |
| Default exposure | Bind to `127.0.0.1` | There is no authentication. Binding to a public/LAN interface is an explicit operator action and is documented as unsafe without network controls. |
| Configuration | Validated `WHM_...` environment variables with one centralized settings object | Defaults and thresholds are not duplicated in check/policy code. A `.env.example` documents the supported names; secrets are not committed. |
| Operational state | Log to stdout/stderr; `/healthz` and `/readyz`; SQLite online backup command; optional systemd unit | Supports local operation and basic SRE workflows without a cloud platform. |

## 3. Health-policy contract

The evaluator receives normalized `PASS`, `WARN`, `FAIL`, and `UNKNOWN` check results plus persisted policy memory. It returns an overall state, machine-readable reason, and evidence. The contract is intentionally conservative and deterministic:

1. DNS and HTTP are critical for every configured website. TLS is critical for HTTPS; an HTTP site's TLS result is `UNKNOWN` with `not_applicable` evidence and is excluded from policy completeness.
2. Any critical `FAIL` increments the consecutive-failure counter and resets the success streak. Failure one is `DEGRADED`/suspected; the configured threshold (default two) is `DOWN`. Repeated failures do not create repeated incidents.
3. A critical `WARN` is evidence of degradation, not a critical failure. A clean recovery streak is broken by a warning.
4. A recovery success requires a complete cycle with every applicable critical check `PASS`. Two consecutive successes (default) complete recovery. The first is represented as `DEGRADED` with reason `RECOVERY_IN_PROGRESS`, because `RECOVERING` is not an allowed overall state. A failure during recovery from an open/down episode returns to `DOWN`; recovery counters are reset appropriately.
5. If the critical evidence is incomplete and there is no decisive critical failure, the state is `UNKNOWN`. An unknown cycle breaks consecutive failure/success streaks but preserves the unresolved failure/recovery context; it never resolves an incident or counts as a pass.
6. Critical warnings, latency warnings/failures, or other implemented non-critical problems yield `DEGRADED`; latency is never a cause of `DOWN`.
7. A new incident opens on `DOWN`, remains the same through repeated `DOWN`, and resolves only when policy confirms `HEALTHY`. This matches the explicit state-transition notifications in the supplied plan; a degraded/unknown observation does not falsely resolve an outage.
8. Every non-healthy result includes a reason code, message, affected check names, and serialized evidence. Health transitions are recorded only when the overall state changes.

## 4. MVP delivery map (scope unchanged)

| Sprint | Stories / scope | Acceptance outcome |
|---|---|---|
| 1 — Foundation & basic monitoring (30 SP) | WHM-101–103, 201–203, 301–302, 401 | Installable app, central configuration/logging, validated persisted websites, DNS/HTTP checks, normalized result model. |
| 2 — Health intelligence (30 SP) | WHM-402–403, 303–304, 501–504 | TLS/latency checks, explainable deterministic health state and failure/recovery policy. |
| 3 — Continuous monitoring & incidents (28 SP + 2 reserve) | WHM-505, 601–602, 702–703 | Policy coverage, isolated recurring checks, health transitions, single incident lifecycle. |
| 4 — Persistence, dashboard & notifications (29 SP + 1 reserve) | WHM-701, 801–802, 901–903, 603 | Durable check history, useful dashboard/details, optional deduplicated webhook alerts, no overlapping site checks. |
| 5 — Reliability & release (26 SP + 4 reserve) | WHM-604, 1001–1004 | Scheduler isolation, focused check/policy/incident tests, full local integration test, release-ready documentation. |

The story IDs are traceability markers, not Jira issue updates: no Jira integration or remote Jira project was supplied. Post-MVP WHM-305 (content), WHM-803/804 (response-time charts and incident-history UI; history APIs are in the MVP), and WHM-1005 (extra multi-site failure-isolation scenario) are not release requirements. Failure isolation itself is still tested where it protects core monitoring behavior.

### Implementation status

**MVP implementation complete in this repository.** Sprint 1–5 scope is implemented and mapped to the plan above. Verification currently includes focused check/policy/database/scheduler/API tests plus a full scheduler → checks → policy → SQLite → incident → webhook end-to-end test. GitHub Actions is configured to run the quality gate on Python 3.11, 3.12, and 3.13; pytest enforces at least 90% application line coverage. Final test/lint results are reported in the implementation handoff, not treated as a substitute for Jira issue updates.

## 5. Implementation sequence

1. **Foundation:** project metadata, settings, logging, API application/lifespan, SQLite schema/repository, URL validation and website CRUD (WHM-101–203).
2. **Checks/results:** DNS, HTTP, TLS, latency and normalized diagnostics (WHM-301–304, 401–403).
3. **Policy/history:** explainable policy state machine, persisted counters/transitions/incidents/check results (WHM-501–505, 701–703).
4. **Operations:** scheduler, per-site no-overlap guard, optional webhook outbox/retries, health/readiness endpoints (WHM-601–604, 901–903).
5. **Product surface:** dashboard overview, details, manual check, active/recent evidence, responsive/static UI (WHM-801–802).
6. **Verification/release:** focused unit/integration/E2E tests, lint, clean-start check, backup/restore and security/runbook/release docs (WHM-1001–1004).

## 6. Test and quality strategy

- **Pure unit tests:** URL normalization and invalid input; policy truth table, threshold boundaries, recovery, unknown evidence, and explainability.
- **Network-check tests:** injected DNS/TLS functions and HTTPX mock transport; no test depends on public DNS, internet access, real certificates, or sleeping for a timeout.
- **Persistence tests:** CRUD, duplicate canonical URL handling, disabled state, restart persistence, foreign-key cleanup, transactionally consistent transitions/incidents/outbox, backup integrity.
- **Service/API tests:** a deterministic fake check runner through website creation → manual monitoring cycle → stored checks/state → incident open/recovery → notification record; exercise validation and HTTP error paths.
- **Scheduler tests:** interval/due behavior, disabled sites, one in-flight cycle per website, and exception isolation across sites.
- **Quality gates:** `pytest` with a 90% application line-coverage floor, `ruff check .`, `ruff format --check .`, `compileall`, dependency consistency, and clean application startup. All network-bound behavior is mocked; no credentials or external service are required.

## 7. Agile workflow and readiness

Use the hierarchy **Epic → Story → Subtask** and move work through:

```text
BACKLOG → READY → IN PROGRESS → CODE REVIEW → TESTING → DONE
```

A story is **Ready** only when the behavior and testable acceptance criteria are clear, dependencies and required inputs/outputs are identified, architectural decisions do not block implementation, and its estimate/priority are recorded. Implement one story or small subtask at a time; finish its tests and acceptance checks before beginning dependent work.

Dependency chain:

```text
WHM-503 policy → WHM-702 transitions → WHM-703 incidents
                                      ↓
                  WHM-701 history → WHM-801/802 dashboard
                  WHM-901 transitions → WHM-902/903 notifications
```

The tables above preserve the supplied sprint budgets (no sprint exceeds 30 SP). Jira IDs are traceability markers in this repository; creating/updating remote Jira issues, committing, or tagging a release are separate delivery actions and are not claimed by this implementation handoff.

## 8. Definition of Done / release gates

A story/release is Done only when:

- [ ] Implementation satisfies its acceptance criteria.
- [ ] Appropriate automated tests are added and pass.
- [ ] Error handling and timestamped operational logging are in place; exceptions are not silently discarded.
- [ ] Configuration, API, and policy behavior are documented when changed.
- [ ] No known regression is introduced; relevant tests and lint/format gates pass.
- [ ] The application imports and starts from a clean environment.
- [ ] Runtime data/secrets are ignored by Git; sample configuration contains no secret.
- [ ] Operations docs cover loopback-only default, one-process constraint, webhook semantics, data location, online backup/restore, restart behavior, and smoke checks.
- [ ] MVP/post-MVP boundary remains explicit; no cloud, Kubernetes, Redis, authentication, content checking, or paid integration slips into scope.

## 9. Risk register

| Risk | Impact | Mitigation / remaining limit |
|---|---|---|
| Unauthenticated API exposed to a network | Arbitrary target changes, deletions, and outbound requests | Loopback default, security/runbook warning, operator-managed firewall/auth layer required before remote exposure. |
| One monitoring location or transient failure | False positive or blind spot | Consecutive failure/recovery thresholds, explicit `UNKNOWN`, explainable evidence; no multi-region quorum in MVP. |
| SQLite growth / host or disk failure | Lost monitoring history or stopped checks | Owner-only database/backup permissions, online backup command, restore rehearsal/runbook; no automated retention or backup scheduler yet. |
| Duplicate webhook delivery | A receiver may observe a retry after the app crashes between send and acknowledgement | Durable outbox, unique transition key, `Idempotency-Key`, bounded retries; exactly-once HTTP delivery is not claimed. |
| Multiple application processes | Duplicate schedules and overlapping cycles | Single-worker deployment is the supported model; distributed locking/leader election is post-MVP. |
| External site behavior or local resolver/CA differences | Checks may vary by vantage point/host configuration | Network integration is intentionally environment-specific; tests are deterministic fakes; document system DNS and trust-store dependency. |
