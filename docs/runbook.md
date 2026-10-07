# Operations runbook

## 1. Start and verify

### Interactive/local operation

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
# Optional: export WHM_... variables before starting.
python -m app
```

By default, the UI/API listens only on `127.0.0.1:8000`. Visit `http://127.0.0.1:8000` and verify:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8000/api/health
```

`/healthz` proves the process can answer; `/readyz` proves SQLite is reachable. Neither proves that monitored targets or the optional webhook are healthy. Dashboard API docs: `/docs`.

### Rebuilding from source

There is no build step: installing the package and starting `python -m app` is the
whole deployment. When you build a revision yourself, run `make check` first — it
is the same gate CI runs (lint, formatting, repository consistency, and the tests
with the 90% coverage floor). `make verify` alone checks the version trio and the
documentation inventory if you want a fast sanity check.

### Environment file

`.env.example` is documentation only; the process does not auto-load it. To use a shell file, copy it to a protected location and source it explicitly, or configure `EnvironmentFile=` in systemd. Do not commit real webhook URLs/tokens.

### Optional systemd deployment

The example unit at `deploy/website-health-monitor.service` uses a single process, loopback binding, a dedicated service account, systemd-managed state directory, restart-on-failure, and journald output. Install the app/virtualenv under `/opt/website-health-monitor`, create the `whm` user/group, install the unit, and set `/etc/website-health-monitor.env` with restrictive permissions (`root:whm`, mode `0640`). Example:

```bash
sudo install -m 0644 deploy/website-health-monitor.service /etc/systemd/system/website-health-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now website-health-monitor
sudo systemctl status website-health-monitor
journalctl -u website-health-monitor -f
```

The unit sets `WHM_DATABASE_PATH=/var/lib/website-health-monitor/health.db`; keep backups outside that live state directory.

## 2. Normal operation

- New enabled sites are checked shortly after startup; each site then uses its own interval. Disabled sites are persisted and skipped by the scheduler.
- A manual “Check now” executes one cycle even if the site is paused; it cannot overlap another cycle for that site.
- Watch the UTC log for `Monitoring cycle completed`, `Website health transition`, scheduler failures, and webhook delivery/retry messages.
- Use dashboard Details or `GET /api/websites/{id}` to inspect the assigned reason, individual checks, recent transitions, and open incident.
- State policy is fail/recovery threshold based. A single critical failure is `DEGRADED`; the default second consecutive failure confirms `DOWN`. Two complete critical passes are required to confirm recovery.
- A high latency check is non-critical. It can keep the overall state `DEGRADED` but cannot alone open an incident.
- A target URL becomes immutable after its first stored check to keep history/incident provenance correct. Add a new website for a replacement URL.
- Deleting a website cascades its checks, transitions, incidents, and notification events. Export/back up first if this history must be kept.

## 3. Logs and alert delivery

Logs go to stdout/stderr with UTC timestamp, level, and logger name. Uvicorn access logs may also include request paths; avoid putting credentials in URLs. Do not increase HTTP client wire logging in production.

If `WHM_WEBHOOK_URL` is absent, alert-worthy state transitions are recorded with status `disabled`; they are not queued for future delivery. After a webhook is configured, new transitions produce `pending` events. Existing disabled events are not replayed. Inspect `GET /api/notifications` for delivery status. Delivery attempts are bounded, use exponential backoff, and include an idempotency key. A persistent `failed` event requires operator review; a receiver outage does not stop monitoring checks.

## 4. Database, backup, restore, and growth

Default database: `./data/website-health-monitor.db`. Set `WHM_DATABASE_PATH` to an absolute writable path for a service deployment. On POSIX, the application creates new parent directories restrictively and sets the database and completed backup file to owner-only mode (`0600`). SQLite WAL may create `-wal` and `-shm` files while running: **do not copy only the live `.db` file with a raw file-copy command**.

Create an online-consistent backup through SQLite's backup API:

```bash
.venv/bin/whm-backup ./backups/health-monitor-$(date -u +%Y%m%dT%H%M%SZ).db
# or: python -m app.backup ./backups/health-monitor-YYYYMMDD.db
```

The command refuses to overwrite an existing backup and checks `PRAGMA integrity_check`. Store backups on a different disk or protected location if possible, restrict permissions, and set an operator-owned retention schedule. The application does not create backups or prune history automatically.

**Restore procedure:**

1. Stop the app/service and preserve the current database plus any relevant WAL/SHM files for rollback.
2. Check the backup exists and is readable (the backup command already verified integrity; you can also use `sqlite3 backup.db 'PRAGMA integrity_check;'`).
3. Copy the backup to a new path on the database filesystem, set ownership/permissions for the service user, and point `WHM_DATABASE_PATH` at that path. Do not overlay a live SQLite file.
4. Start the app; check `/readyz`, `/api/health`, website count, current states, and active incidents.
5. Keep the old database until recovery is verified; record the restore time and any lost observations since the backup.

Every monitoring cycle stores four check results per website in the current MVP. At a 60-second interval that is about 5,760 check rows per website per day, plus state/incident metadata. There is no automatic retention setting; monitor free disk space, back up, and plan manual archival before a busy/long-lived installation fills its filesystem. Do not delete rows with ad-hoc SQL unless the app is stopped and you understand the incident/transition history relationships.

## 5. Incident response / troubleshooting

### A site is `DEGRADED`

1. Inspect the reason code and evidence; determine whether this is one suspected critical failure, a critical TLS warning, or non-critical latency.
2. Compare against the configured threshold and recent checks. A single failure is deliberately not an incident.
3. Validate the target from the host using the same DNS/network path. Do not resolve by lowering failure thresholds without understanding the effect.

### A site is `DOWN`

1. Review the open incident start/reason/evidence and newest check results.
2. Check DNS resolution from the monitor host, outbound routing/firewall, HTTP status/redirect chain, and TLS chain/hostname as indicated.
3. Confirm with an independent check or the site owner before taking action. The monitor's result is evidence from one network location, not global reachability.
4. Keep the incident open until the configured clean critical recovery streak returns the overall state to `HEALTHY`; an `UNKNOWN` result is not recovery.

### A site is `UNKNOWN`

An applicable critical check was missing or returned unknown, and there was no decisive critical failure. Check application logs and the individual check result. The system intentionally does not count unknown evidence as either a failure or a pass and will not resolve an incident from it.

### Common symptoms

| Symptom | Checks |
|---|---|
| DNS fails but HTTP passes | Resolver configuration, host-specific DNS path, transient cache differences, and whether the target is an IP literal. The policy still treats DNS as critical. |
| HTTP returns 4xx | Confirm the target URL/route, required authentication, and expected response. URL credentials are rejected; custom headers/content assertions are post-MVP. |
| TLS fails | Verify system CA trust, certificate chain, hostname/SNI, expiry, and host clock. TLS checks use the host trust store. |
| Dashboard unavailable | Check process/service status and logs; `/healthz`; binding/port/firewall; static file deployment. |
| `/readyz` fails | Verify `WHM_DATABASE_PATH`, parent directory, service user ownership, disk space, SQLite sidecars, and read-only filesystem. Restore from backup if integrity is compromised. |
| Notification pending | Confirm webhook URL and outbound access, dispatcher logs, response status, and event attempt count. A permanent 4xx or exhausted retries is marked `failed`. |
| Monitoring stopped | Check service logs and CPU/disk/resource pressure. Scheduler errors are isolated and logged; restart the process after correcting the root cause. |

## 6. Operational objectives and SRE limits

These are local operational checks, not an uptime SLA:

- **Process:** `/healthz` should return 200 while the API process is serving.
- **Storage readiness:** `/readyz` should return 200 and `GET /api/health` should report `database: ok`.
- **Monitoring freshness:** under normal load, each enabled site's `last_checked_at` should advance within roughly `interval_seconds + timeout_seconds + scheduler_poll_seconds + 5 seconds`. Check saturation, slow checks, and host load can extend this; the application does not expose a Prometheus metric or page on stale data.
- **Incident response:** use the latest check evidence and validate from an independent vantage point; do not treat one local monitor as global availability evidence.
- **Recovery objective:** enabled websites and unresolved incidents survive process restart; check outcomes created while the app was stopped are not backfilled.
- **Backup objective:** choose an operator-owned cadence based on how much history loss is acceptable. Until a backup is made, the app has no independent copy or automatic recovery point.

- **Single vantage:** no multi-region quorum, external uptime guarantee, or self-monitoring of the monitor process is claimed.
- **Single process:** multiple Uvicorn workers would create duplicate schedulers and bypass the in-process per-site lock.
- The app has no authentication, authorization, user roles, or CSRF defense appropriate for public exposure. Keep it on loopback or place it behind separately designed access controls.
- Database backup/restore, filesystem capacity, operating-system patches, and webhook secret rotation are operator responsibilities.
- A process crash can cause a webhook delivery retry. The outbox/idempotency key limits duplicate effects but exactly-once delivery cannot be guaranteed over HTTP.
