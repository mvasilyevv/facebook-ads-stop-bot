# Production cutover packet

This packet is prepared for the first safety-first release. It does not
authorize the switch. Execution starts only after the owner gives the exact
command `запускай` and all evidence cells below are green.

## Required evidence

- immutable release manifest from CI; every runtime image is an `@sha256`;
- clean `0001_safety_first_baseline` target and accepted adoption bundle;
- newly provisioned secrets and successful connectivity checks;
- off-host monitoring/Alertmanager/blackbox readiness;
- accepted full backup plus post-backup WAL and isolated PITR restore evidence;
- load, chaos, accessibility, browser and physical-device results;
- named release operator and rollback owner with access to the host, registry,
  DNS/Caddy, backup repository and Meta verification UI.

Any missing, expired or ambiguous item is a stop, not a waiver.

## Two-hour maintenance window

| Window    | Action                                                                                                                                                                             | Required proof                                                                        |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| T-30…T-15 | Freeze config changes and money commands. Record current Meta state, active color, release pointer and N-1 manifest.                                                               | Signed preflight record; N-1 images pull by digest.                                   |
| T-15…T-5  | Export, validate and dry-run `adoption-bundle/v1` using `docs/adoption-bundle-runbook.md`. Provision new secrets.                                                                  | Counts and semantic hashes match; connectivity is green; scanning remains disabled.   |
| T-5…T0    | Verify monitoring ingest, Alertmanager, blackbox, backup/WAL and restore artifacts. Announce maintenance.                                                                          | All required evidence current; rollback owner ready.                                  |
| T0…T20    | Merge the reviewed release commit to `main`. CI builds once, publishes immutable images and creates `release-images-<git-sha>`.                                                    | All source and artifact jobs green; manifest contains no tag-only references.         |
| T20…T40   | Let the deployment workflow stage the immutable release and run the advisory-locked migrator against the clean target. Import the approved bundle once.                            | Only baseline revision exists; import transaction and semantic re-projection succeed. |
| T40…T55   | Candidate starts without public traffic. Run health, readiness, OpenAPI, Alloy and desktop preflight checks.                                                                       | Candidate identity and every digest match the manifest.                               |
| T55…T58   | Automated blue/green activation acquires the browser maintenance fence, switches Caddy and hands over singleton leases.                                                            | Zero switch 5xx; the single durable cutover deadline remains within 180 seconds.      |
| T58…T70   | Verify owner login, global/cabinet snapshots, one scan, action lifecycle, Telegram webhook/card edit and unified desktop. Enable Observer only after cabinet mapping is confirmed. | No false-green state; no duplicate task/message; exact cabinet tabs confirmed.        |
| T70…T100  | Observe errors, queue age, snapshot age, browser/Meta latency, Telegram delivery and host resources.                                                                               | SLO dashboards remain green and no critical incident is open.                         |
| T100…T120 | Close maintenance, archive artifacts and record the release/backup fingerprints.                                                                                                   | Release packet complete; old stack and all old data remain retained and fenced.       |

## Abort and rollback

Before Caddy changes, abort by leaving the incumbent untouched and deleting
only the failed candidate. After activation starts, do not improvise manual
container changes: `server-platform-release.sh` and `bluegreen-deploy.sh`
restore the previous app/desktop pointers, Caddy target and worker ownership
under the same absolute 180-second deadline.

If the automated rollback cannot prove convergence, keep the maintenance fence
in place, stop the new runtime, reconcile the actual Meta status read-only and
escalate the critical release incident. Never downgrade the database. A full
return to the old stack uses its preserved database and immutable N-1 manifest;
it is an operational rollback, not a dual-read/runtime fallback.

Old production databases, volumes and backups are never deleted by this
runbook. Their destruction requires a separate explicit owner confirmation.
