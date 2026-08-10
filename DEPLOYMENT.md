# Production deployment

The supported production path is the safety-first platform described in
[`deploy/bluegreen/README.md`](deploy/bluegreen/README.md). The former
monolithic Compose, host Vision/Xvfb, local `pg_dump`, long-polling Telegram
and Helm/K3s release paths are not supported launchers.

## Release entrypoint

CI builds every image once, resolves it to an immutable digest and calls:

```bash
./scripts/deploy-platform-server.sh \
  --host deploy@app-host.example \
  --release-env release-images.env
```

The remote entrypoint is `scripts/server-platform-release.sh`. It serializes
deployment and reconciliation with the shared deploy lock, releases the
desktop independently, proves the pre-migration backup/PITR gate, prepares the
inactive application colour and changes traffic only after health and contract
checks pass.

Do not build images on the server and do not invoke Compose files directly for
a production release. A release is uploaded into a private staging directory,
verified against `.fb-agent-source-manifest.json`, and published with one
same-filesystem rename. Reusing a `RELEASE_ID` is read-only and succeeds only
when both the source manifest and image manifest are byte-identical.

## First platform adoption

First adoption is an explicit maintenance operation. The squashed Alembic
baseline is fresh-install-only: it never upgrades a database stamped with a
historical revision. Follow the checklist in `deploy/bluegreen/README.md`;
keep the incumbent database and its backups untouched while a separate empty
target database is created, baselined and validated. Switching the runtime DSN
is a distinct, human-approved cutover step. No release script drops, stamps or
converts the incumbent database.

The target infra defaults to the dedicated
`fb_agent_safety_first_pgdata` volume. There is no legacy volume fallback. An
explicit `POSTGRES_VOLUME` override is accepted only after bootstrap proves
that the database is empty or claims the complete
`0001_safety_first_baseline`; the release migrator then runs `alembic check`
and rejects any extra legacy schema or ORM drift before activation.

Before the first release, provision these root-only prerequisites:

- `/opt/fb-agent/shared/alloy-agent.env` (mode `0600`) with reachable private
  HTTPS Prometheus, Loki and Tempo ingest URLs;
- `/opt/fb-agent/shared/desktop-profile-seed` (mode `0700`) containing the
  independently prepared Vision browser profile. The directory and every
  entry must be owned by `root:root`; symlinks, special files and
  group/world-writable entries are rejected. It must include a root-owned
  mode-`0600`
  `.fb-agent-vision-profile-v1` file whose exact content is
  `fb-agent-vision-profile-v1`.

The release never snapshots an incumbent desktop to invent this seed. It
validates and hashes the seed before database/application activation, copies
it through an atomic staging directory only into an absent fresh profile, and
refuses an unmanaged pre-existing config. A root-owned bootstrap marker inside
the staged profile makes a power-loss retry resumable without treating an
unknown profile as managed or deleting a profile that may have changed.

Every later Vision mutation persists its pre-change runtime contract and then,
while the old containers are only stopped, an exact profile snapshot. The
`snapshot_ready` journal is fsynced before the destructive Compose `down`, so a
power loss resumes in either the candidate or previous direction without
inventing profile state.

## Normal operations

```bash
# Validate Compose, telemetry and release contracts.
./scripts/validate-platform-configs.sh --containers

# Inspect the committed active colour.
python3 scripts/release-state.py get \
  --state-root /opt/fb-agent/shared/release-state \
  --source active --field color

# Operate the committed application and desktop lifecycles independently.
sudo /opt/fb-agent/current/scripts/platform-compose.sh status
sudo /opt/fb-agent/shared/active-desktop-state/release/scripts/platform-desktop-compose.sh status

# Run a reviewed full backup and isolated restore drill.
sudo /opt/fb-agent/current/scripts/pgbackrest-admin.sh \
  --release-env /opt/fb-agent/shared/active-release-images.env \
  --app-env /opt/fb-agent/shared/active-app.env \
  --backup-env /opt/fb-agent/shared/pgbackrest.env full
sudo /opt/fb-agent/current/scripts/pgbackrest-restore-drill.sh \
  --release-env /opt/fb-agent/shared/active-release-images.env \
  --app-env /opt/fb-agent/shared/active-app.env \
  --backup-env /opt/fb-agent/shared/pgbackrest.env
```

Rollback switches Caddy and singleton worker leases to the previous colour.
It never downgrades PostgreSQL. Backup replicas are not counted as backups.

Desktop/Vision stop and restart paths acquire one renewable PostgreSQL
maintenance lease. Browser-backed claims take the matching transaction-level
advisory lock before reading the gate, closing the pre-INSERT snapshot race;
maintenance then requires scanning disabled and zero running browser tasks.

## Production gates

- Candidate API health, readiness and OpenAPI contracts pass before traffic.
- Candidate Alloy, node-exporter and cAdvisor are running, Alloy reports ready,
  and all private ingest HTTPS transports respond before application cutover.
- Desktop cutover requires the exact PostgreSQL Vision profile, a concrete live
  browser session, a successful full Graph probe, a compatible versioned
  browser-agent contract and
  `/desktop-readyz` proving configured credentials, an anonymous `401`
  challenge and an authenticated `200`.
- Panel and new desktop connections authorize against the active PostgreSQL
  owner roster on every forward-auth. Public panel WebSockets and Kasm streams
  are forcibly bounded to one minute; acceptance must prove that reconnects
  transparently preserve panel state and Kasm input, clipboard and the active
  Ads Manager tab, and that a revoked owner cannot reconnect after the current
  stream closes.
- Desktop state, readiness credentials, Caddy credentials and systemd units
  are reconciled from `desktop-transaction.env`. The atomic active-state
  pointer is the durable commit point; the healer completes either direction
  after semantic readiness, including after reboot.
- Root-owned systemd launchers verify the immutable source manifest and sealed
  release tree before executing app or desktop scripts. The desktop boot gate
  additionally requires the exact committed healthy Vision container identity;
  a merely running same-name container is rejected.
- Full backup, archived post-backup WAL marker and isolated PITR pass before a
  migration-capable release.
- The first accepted backup/restore evidence automatically enables and verifies
  weekly full, daily differential and monthly restore-drill timers. Every later
  release fails closed if a required timer is disabled or inactive.
- Images are digest-pinned; the VPS never performs a production build.
- Caddy switch produces zero deployment 5xx and rollback completes in at most
  three minutes.
- Off-host monitoring and backup remain independent of the application host.
- The migrator sees either an empty database or exactly
  `0001_safety_first_baseline`; every historical/unversioned non-empty target
  fails before DDL.
- Automatic database failover is not enabled until the SLO, restore and chaos
  prerequisites in the implementation plan are met.
