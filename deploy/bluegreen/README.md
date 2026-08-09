# Safety-first release contour

This directory documents the production release path. CI and systemd use this
blue/green contour exclusively. A new host starts blue from a clean database;
there is no compatibility runtime or alternate traffic target.

## Runtime topology

| Project | Owns | Lifecycle |
| --- | --- | --- |
| `fb_agent_infra` | PostgreSQL, Redis, WAL spool and durable volumes | Never switched with an app release |
| `fb_agent_blue` | API, web, TMA and workers | Alternates with green |
| `fb_agent_green` | API, web, TMA and workers | Alternates with blue |
| `fb_agent_vision` | Unified Vision + KasmVNC desktop runtime | Released independently |
| `fb_agent_desktop` | browser-agent beside the independent Vision/Kasm stack | Released independently |
| `fb_agent_monitoring` | Prometheus, Loki, Tempo, Alertmanager, Grafana, blackbox | Preferably off-host |

There are no fixed application container names. Caddy continues to own public
TLS and switches between loopback port sets:

- blue: API `18100`, web `18080`, TMA `18081`;
- green: API `28100`, web `28080`, TMA `28081`;

All application and data-plane images must be `image@sha256`. CI uploads
`release-images-<git-sha>` after resolving the registry manifests. Tags are not
accepted by the deployment scripts.

## Environment commit model

`/opt/fb-agent/shared/.env` is desired input for the next release. Running
services never consume that mutable file directly:

- `active-app.env` is the committed API/web/worker runtime and app rollback
  snapshot;
- `active-desktop-state` is one atomic symlink to a mode-`0700` immutable
  desktop state containing `app.env`, `release-images.env`, a fingerprint and
  a `release` pointer. A sealed root-owned verifier outside every release
  validates the selected source manifest and read-only tree before systemd may
  execute the runtime wrapper, healer or any other release-owned script;
- `desktop-readiness/active.env` is the independently committed Kasm readiness
  credential pointer. It is switched only after direct candidate
  authentication succeeds and is then re-probed through the active API;
- Caddy API credentials change in the application traffic switch, while the
  desktop service credential changes only after the desktop health gate.

The desktop fingerprint includes digest-pinned images, the immutable source
manifest and the complete desktop environment, so code-only control-plane
changes create a new state and move the systemd pointer. Before any desktop
commit mutation, `desktop-transaction.env` is fsynced. The atomic
`active-desktop-state` pointer selects the transaction direction; reconciliation
then converges readiness credentials, desktop-scoped Caddy credentials and
systemd units before the journal is removed.

## Fresh bootstrap

The safety-first schema is a clean database boundary, not an in-place
migration. The release migrator accepts an empty target or a target already
stamped `0001_safety_first_baseline`, and rejects every other non-empty or
historical target.

1. Download `release-images.env` from the CI artifact to
   `/opt/fb-agent/shared/release-images.env`.
2. Create `/opt/fb-agent/shared/pgbackrest.env` from
   `deploy/backup/pgbackrest.env.example`, mode `0600`. The S3-compatible bucket
   must be encrypted, versioned, off-host and protected with Object Lock where
   available.
3. Provision `/opt/fb-agent/shared/alloy-agent.env` (mode `0600`) with private
   HTTPS ingest endpoints and a root-only
   `/opt/fb-agent/shared/desktop-profile-seed` (mode `0700`). The seed must be
   independently prepared; its root and every entry must be owned by
   `root:root`. Symlinks, special files and group/world-writable entries are
   rejected. It must include a root-owned mode-`0600` marker
   `.fb-agent-vision-profile-v1` with exact content
   `fb-agent-vision-profile-v1`.
4. Validate without mutation:

   ```bash
   ./scripts/platform-bootstrap.sh \
     --release-env /opt/fb-agent/shared/release-images.env \
     --app-env /opt/fb-agent/shared/.env \
     --backup-env /opt/fb-agent/shared/pgbackrest.env \
     --dry-run
   ```

5. Start durable infra with the newly allocated
   `fb_agent_safety_first_pgdata` PostgreSQL volume and prove WAL archive
   access. If `POSTGRES_VOLUME` is explicitly overridden, bootstrap accepts it
   only after a read-only guard proves an empty database or the complete
   `0001_safety_first_baseline`; it never falls back to `fb_agent_pgdata`:

   ```bash
   ./scripts/platform-bootstrap.sh \
     --release-env /opt/fb-agent/shared/release-images.env \
     --app-env /opt/fb-agent/shared/.env \
     --backup-env /opt/fb-agent/shared/pgbackrest.env
   ```

6. Prove the target contains no user relations and no historical
   `alembic_version`. The next step runs the advisory-locked migrator; after it
   succeeds, verify that its sole revision is `0001_safety_first_baseline`. If
   business data is outside this deployment path and requires a separately
   reviewed migration project.

7. Prepare the first color without traffic or workers:

   ```bash
   ./scripts/bluegreen-deploy.sh \
     --color blue \
     --release-env /opt/fb-agent/shared/release-images.env \
     --app-env /opt/fb-agent/shared/.env \
     --backup-env /opt/fb-agent/shared/pgbackrest.env
   ```

8. Inspect its local health and then repeat with `--activate`. Activation runs
   the advisory-locked migrator, validates `/healthz`, `/readyz` and OpenAPI,
   arms one durable 180-second cutover deadline, switches Caddy, starts and
   verifies the target money workers by exact container/release identity and
   requires fresh complete worker readiness. Any rollback consumes the
   remainder of that same deadline; it never starts a second 180-second budget.

Normal releases never downgrade PostgreSQL. If post-switch validation fails,
traffic and workers are returned only to the previous blue or green color and
the failed color is stopped. Database rollback is a separately reviewed DSN
switch, never an Alembic downgrade.

## Normal release

Choose the inactive color and run the same command with `--activate`. A release
to the already-active color is rejected, so no deployment can silently become
an in-place restart.

The first canary keeps `OBSERVER_CABINET_CONCURRENCY=1`. Raise it to `2` in the
application environment only after the 24-hour quota/CPU/SLO gate. Money tasks
are exclusively claimed by `autopause_worker`; ordinary `meta_api` explicitly
claims `interactive,bulk,background`.

All Vision/browser stop, restart and release paths share a renewable PostgreSQL
maintenance lease. Browser task claims serialize their snapshot boundary with
lease acquisition through the same advisory transaction lock. The Vision
update journal records the pre-mutation profile snapshot before Compose
`down`, and release/healer readiness accepts only a full Graph probe from the
exact canonical Vision profile—never another preferred session.
At boot, browser-agent cannot join `container:vision-webtop` until the
namespace container is healthy and exactly matches the committed image digest,
Compose identity, cluster, purpose and release labels. A fresh install enables
the desktop units early but starts them only after `active-desktop-state` is
durably committed.

## Backups and drills

The supported first-release path creates a full encrypted off-host backup,
forces and archives a post-backup WAL marker, restores that exact set in an
isolated temporary volume and validates the evidence before candidate start.
Only then does `server-platform-release.sh` enable and verify weekly full, daily
differential and monthly restore-drill timers. There is no separate adoption
command or implicit operator step. Later releases verify all three timers are
enabled and active.

The isolated drill never mounts the production volume. Its container, network
and volume are removed after the check. The standalone pgBackRest commands are
reserved for reviewed forensics and additional drills, not initial adoption.

## Acceptance gates

- `./scripts/validate-platform-configs.sh --containers` is green.
- Every release image resolves to a digest and no app Compose service has
  `container_name`.
- Candidate health, readiness and OpenAPI pass before Caddy changes.
- Candidate application-host Alloy and exporters pass local readiness and
  private ingest transport probes before Caddy or worker leases change.
- Desktop release requires exact live profile identity, a full successful Graph
  probe plus the API's authenticated Kasm readiness, not a TCP-open, preferred
  browser session or generic API health probe.
- Every endpoint on the canonical shared network has managed, cluster, purpose
  and release-scope labels; the Vision plane has purpose `vision` and no false
  application color.
- Caddy validate and reload both succeed; a failed reload restores its prior
  site file.
- Worker readiness first proves exact target release containers, then fresh
  heartbeats after the incumbent money workers stop.
- A failed local commit after `setWebhook` reasserts the webhook from the
  committed `APP_ENV_FILE` before the release journal is reconciled.
- A full backup, archive check and isolated restore have all succeeded.
- Rollback to the previous color is exercised before enabling automated use.
