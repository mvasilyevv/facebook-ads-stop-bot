# Adoption bundle runbook

`adoption-bundle/v1` moves reviewed configuration from one legacy installation
to one clean safety-first baseline. It is a one-time migration tool, not a
runtime compatibility layer.

## Scope and safety boundary

The bundle contains only:

- canonical ad-account IDs, offers, offer memberships, and offer rules;
- non-secret observer settings (the importer always forces scanning off);
- the owner's presentation timezone as one identity-free setting when the
  selected source profile contains it;
- active Telegram DM recipients, with newly generated target UUIDs, and their
  notification preferences when the selected source profile supports them;
- retention policy and the safe Web App URL.

It never contains credentials, tokens, invitations, revoked recipients,
history, metrics, tasks, incidents, notification deliveries, leases, browser
sessions, or other runtime state. Export logs and the source fingerprint do not
contain a database name, DSN, host, or raw database metadata.
The fingerprint binds a one-way digest of the configured source endpoint to the
explicit profile and canonical semantic sections; the endpoint fields
themselves never enter the bundle.

The source schema is selected explicitly:

- `legacy-array-0036-no-preferences` is revision
  `0036_observer_30s_default`. That production-era schema has no recipient
  preference table, so the exported preference section is intentionally empty.
- `legacy-array-baseline-with-preferences` is the reviewed pre-normalization
  safety baseline and requires the recipient preference table. It is a separate
  profile; the exporter does not auto-detect or fall back between schemas.
- `legacy-array-baseline-with-display-preferences` additionally requires the
  owner display-preference table and exports only its IANA timezone, without a
  recipient UUID, Telegram user ID, or chat ID in that section.

Both legacy profiles require `offers.ad_account_ids` to be the reviewed ARRAY
column and reject normalized account tables. The importer accepts only the
current exact `0001_safety_first_baseline` schema and writes through the
normalized ad-account catalog.

## Procedure

Load database URLs from the secret manager into the dedicated process
environment. Never place a DSN on the command line:

```bash
export FB_AGENT_ADOPTION_SOURCE_DATABASE_URL='postgresql+asyncpg://...'
export FB_AGENT_ADOPTION_TARGET_DATABASE_URL='postgresql+asyncpg://...'
```

Export from the legacy source under a repeatable-read, read-only transaction:

```bash
.venv/bin/python scripts/adoption-bundle.py export \
  --source-profile legacy-array-0036-no-preferences \
  --output ./adoption-bundle.json
```

The output is created once with mode `0600`; an existing file or symlink is
refused. Record the printed source fingerprint separately, review section
counts and hashes, then validate the artifact:

```bash
.venv/bin/python scripts/adoption-bundle.py validate \
  --input ./adoption-bundle.json
```

Provision a new target from the exact baseline. It must contain no application
data and only the pristine baseline `retention_policy` system seed. Run the
exact import path with guaranteed rollback:

```bash
.venv/bin/python scripts/adoption-bundle.py dry-run \
  --input ./adoption-bundle.json
```

After the dry-run succeeds, perform the one real import with both explicit
confirmations:

```bash
.venv/bin/python scripts/adoption-bundle.py import \
  --input ./adoption-bundle.json \
  --source-fingerprint '<64-character fingerprint from export>' \
  --confirm 'IMPORT adoption-bundle/v1'
```

The importer validates before connecting, obtains a transaction-scoped advisory
lock, runs one serializable transaction, writes in foreign-key order, reprojects
the target inside that transaction, and compares every semantic count and hash.
Any error or mismatch rolls back the whole import. Dry-run always rolls back.

For the first production release, install the validated bundle as
`/opt/fb-agent/shared/adoption-bundle-v1.json` with mode `0600`. The immutable
release path verifies the manifest-hashed recipients section has exactly one owner
whose DM identity matches canonical `DESKTOP_OWNER_TELEGRAM_USER_ID`, then performs
the dry-run and import before any API or worker starts.
After a crash following commit, a retry verifies the same semantic hashes and
the absence of runtime data instead of attempting a second import.

## Operator checklist

- [ ] Source backup and rollback owner are confirmed.
- [ ] Explicit source profile and Alembic revision match the reviewed source.
- [ ] Bundle file is mode `0600`; counts and section hashes are recorded.
- [ ] Bundle contains only the allowlisted sections above.
- [ ] Target was provisioned from the exact current baseline and is otherwise fresh.
- [ ] `validate` and `dry-run` both succeed with the same fingerprint.
- [ ] Real import uses the exact exported fingerprint and confirmation phrase.
- [ ] Imported offer/account memberships, USD rules, owner recipient, optional
      owner display timezone, retention, and Web App URL are reviewed.
- [ ] Observer scanning remains disabled until an owner enables it manually.
- [ ] The bundle is removed through the approved secure-data disposal process.
