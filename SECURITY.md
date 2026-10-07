# Security notes

## Supported trust boundary

This MVP is an operator-controlled, **single-user local application**. It has no authentication or authorization. Its default bind address is `127.0.0.1`; keep it there unless you deliberately add a protected access layer and firewall policy. A reverse proxy by itself is not authentication.

Any client that can reach the API can add, change, pause, or delete monitored websites and delete their history. The dashboard can also trigger outbound requests to configured targets. Exposing it to a LAN or the public internet is not safe as shipped.

## Outbound-request behavior

Monitoring arbitrary websites is the product's purpose, so the app deliberately does not block private, loopback, link-local, or internal IP addresses. This permits local/self-hosted and internal checks but means an exposed unauthenticated API can be abused to make outbound requests to the monitor host's reachable network. Use only trusted operators and targets. Add a reviewed allowlist/egress policy before a multi-user or internet-accessible deployment.

The app follows HTTP redirects for monitored URLs and validates TLS certificates/hostnames. Operators should avoid putting secrets in query strings: configured URLs are stored and returned in the unauthenticated local API/dashboard. URLs containing username/password are rejected. Webhook destinations are operator configuration and are not accepted from API users.

## Data and secrets

- `WHM_WEBHOOK_URL` may contain a secret token. Store it in a protected service environment file; never commit it, paste it into issue trackers, or enable verbose HTTP wire logs.
- Webhook URLs are not returned by `/api/health`; delivery logs identify event IDs/statuses rather than logging the full destination.
- SQLite stores monitored URLs, check evidence, transitions, incidents, and notification payloads. On POSIX, the app sets the database and backups to mode `0600` and creates missing parent directories with mode `0700`; still restrict WAL/SHM, backup directories, and environment-file permissions.
- The dashboard has no third-party JavaScript, fonts, analytics, or CDN dependency.
- Error responses do not return check response bodies. URL query values may still be part of configured URLs; do not use them for credentials.

## Operational hardening

- Bind to loopback; if remote access is unavoidable, use a trusted VPN/private network plus authentication and request filtering.
- Run under a dedicated non-root OS user; use restrictive file permissions and the sample systemd hardening directives.
- Restrict outbound network access at the host/firewall when the target set is known.
- Keep Python dependencies and the host patched; review dependency updates before deployment.
- Back up SQLite with `whm-backup` and protect/rotate backups.

## Repository security settings

These settings live in GitHub (Settings → Code security and analysis, and
Settings → Branches) and cannot be enforced by files in the repository. They are
listed here so an operator can verify or recreate them; the sandbox token used to
prepare this release has read-only access to repository settings, so enabling
them is a maintainer action.

| Setting | Why it matters |
|---|---|
| **Dependency graph** + **Dependabot alerts** | Required for the `Dependency review` workflow and for advisory alerts on `pyproject.toml` dependencies. |
| **Dependabot security updates** | Opens pull requests for vulnerable pinned/ranged dependencies. Complements `.github/dependabot.yml`, which handles routine version updates. |
| **Secret scanning** + **Push protection** | Blocks accidental commits of `WHM_WEBHOOK_URL` tokens or credentials. |
| **Private vulnerability reporting** | Gives reporters the private channel referenced in [Reporting a vulnerability](#reporting-a-vulnerability). |
| **Code scanning (CodeQL)** | Optional: adds static analysis on pull requests. |
| **Branch protection on `main`** | Require the `CI` checks, require a pull request + review, and disallow force-pushes and deletions. |
| **Restrict who can push / tag** | Only the maintainer should be able to move `main` or publish release tags. |
| **Squash-only merges, delete branch on merge** | Keeps `main` linear and reviewable, matching the Conventional Commit policy. |

`.github/CODEOWNERS` already marks the contract files (policy, database, config,
CI, security docs) for mandatory maintainer review once branch protection requires
code-owner review.

## Reporting a vulnerability

Do not report secrets or exploit steps in public issues. Use the repository's private security reporting channel if one is configured; otherwise contact the project maintainer privately with a concise reproduction and impact. No private contact endpoint is assumed by this repository.
