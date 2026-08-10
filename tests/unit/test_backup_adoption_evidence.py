from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup-adoption-evidence.py"


def _run(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["python3", str(SCRIPT), *(str(value) for value in args)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _evidence_pair(tmp_path: Path) -> tuple[Path, Path]:
    info = tmp_path / "info.json"
    info.write_text(
        json.dumps(
            [
                {
                    "name": "fb-agent",
                    "backup": [
                        {
                            "label": "20260719-120005F",
                            "type": "full",
                            "timestamp": {
                                "start": int(
                                    datetime(2026, 7, 19, 12, 0, 5, tzinfo=UTC).timestamp()
                                ),
                                "stop": int(
                                    datetime(2026, 7, 19, 12, 0, 15, tzinfo=UTC).timestamp()
                                ),
                            },
                            "archive": {
                                "start": "000000010000000000000001",
                                "stop": "000000010000000000000002",
                            },
                            "info": {"size": 1234, "repository": {"size": 567}},
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    full = tmp_path / "adoption-full.json"
    restore = tmp_path / "adoption-restore.json"
    config = tmp_path / "pgbackrest.conf"
    backup_env = tmp_path / "pgbackrest.env"
    backup_env.write_text("# local repository needs no credentials\n", encoding="utf-8")
    config.write_text(
        "[global]\nrepo1-type=posix\nrepo1-retention-full-type=time\nrepo1-retention-full=35\n",
        encoding="utf-8",
    )
    _run(
        "write-full",
        "--output",
        full,
        "--info",
        info,
        "--config",
        config,
        "--backup-env",
        backup_env,
        "--release-id",
        "release-1",
        "--started-at",
        "2026-07-19T12:00:00Z",
        "--completed-at",
        "2026-07-19T12:00:20Z",
    )
    _run(
        "write-restore",
        "--output",
        restore,
        "--release-id",
        "release-1",
        "--backup-set",
        "20260719-120005F",
        "--target-time",
        "2026-07-19T12:00:45Z",
        "--started-at",
        "2026-07-19T12:01:00Z",
        "--completed-at",
        "2026-07-19T12:02:00Z",
        "--revision",
        "0001_safety_first_baseline",
        "--volume",
        "fb_agent_restore_drill_20260719T120100Z",
        "--network",
        "fb_agent_restore_drill_20260719T120100Z",
        "--container",
        "fb-agent-restore-drill-20260719T120100Z",
        "--pg-is-in-recovery",
        "false",
        "--recovery-target-setting",
        "2026-07-19 12:00:45+00",
        "--production-volume",
        "fb_agent_safety_first_pgdata",
        "--mount",
        "fb_agent_restore_drill_20260719T120100Z|volume|/var/lib/postgresql/data",
        "--mount",
        "|bind|/etc/pgbackrest/pgbackrest.conf",
        "--marker-key",
        "restore_drill:unit",
        "--marker-token",
        "unit-marker-token",
        "--marker-observed",
        "true",
    )
    return full, restore


def test_full_and_exact_isolated_restore_form_immutable_evidence_pair(tmp_path: Path) -> None:
    full, restore = _evidence_pair(tmp_path)

    result = _run(
        "validate-pair",
        "--full",
        full,
        "--restore",
        restore,
        "--expected-release-id",
        "release-1",
        "--require-pitr-marker",
    )

    assert result.returncode == 0
    for path in (full, restore, Path(f"{full}.sha256"), Path(f"{restore}.sha256")):
        assert path.stat().st_mode & 0o777 == 0o600
    assert _run("evidence-full-label", "--full", full).stdout.strip() == "20260719-120005F"


def test_evidence_tampering_and_wrong_restore_set_fail_closed(tmp_path: Path) -> None:
    full, restore = _evidence_pair(tmp_path)
    restore_document = json.loads(restore.read_text())
    restore_document["backup_set"] = "20260718-120005F"
    restore.write_text(json.dumps(restore_document), encoding="utf-8")
    restore.chmod(0o600)

    result = _run("validate-pair", "--full", full, "--restore", restore, check=False)

    assert result.returncode != 0
    assert "checksum does not match" in result.stderr


def test_backup_timers_are_below_the_full_plus_restore_evidence_gate() -> None:
    installer = (ROOT / "scripts" / "install-platform-units.sh").read_text()
    validator = installer.index("backup-adoption-evidence.py")
    enable = installer.index("systemctl enable --now")
    restore = (ROOT / "scripts" / "pgbackrest-restore-drill.sh").read_text()
    service = (ROOT / "deploy/systemd/fb-agent-restore-drill.service").read_text()

    assert "--expected-release-id" in installer[:validator]
    assert "--max-age-seconds 14400" in installer
    assert "--require-pitr-marker" in installer
    assert validator < enable
    assert '--set="$BACKUP_SET"' in restore
    assert "write-restore" in restore
    assert "--evidence-dir" in service
    assert "latest-recoverable --info" in restore
    assert "--latest-pitr" in service
    assert "--type=time" in restore
    assert "--prove-post-backup-wal" in service
    assert "pg_switch_wal" in restore
    assert "last_archived_wal" in restore
    assert "restored database does not contain the post-backup WAL marker" in restore


def test_evidence_records_effective_repository_config_not_a_constant(tmp_path: Path) -> None:
    full, _ = _evidence_pair(tmp_path)
    document = json.loads(full.read_text(encoding="utf-8"))

    assert document["repository"]["type"] == "posix"
    assert document["repository"]["retention_full"] == 35
    assert len(document["repository"]["config_sha256"]) == 64


def test_repository_policy_env_override_is_observed_and_rejected(tmp_path: Path) -> None:
    full, _ = _evidence_pair(tmp_path)
    # Reuse the generated info/config but force an unsafe effective override.
    info = tmp_path / "info.json"
    config = tmp_path / "pgbackrest.conf"
    backup_env = tmp_path / "unsafe.env"
    backup_env.write_text("PGBACKREST_REPO1_TYPE=s3\n", encoding="utf-8")
    output = tmp_path / "unsafe-full.json"

    result = _run(
        "write-full",
        "--output",
        output,
        "--info",
        info,
        "--config",
        config,
        "--backup-env",
        backup_env,
        "--release-id",
        "release-1",
        "--started-at",
        "2026-07-19T12:00:00Z",
        "--completed-at",
        "2026-07-19T12:00:20Z",
        check=False,
    )

    assert full.exists()
    assert result.returncode != 0
    assert "not the accepted local policy" in result.stderr


def test_latest_recoverable_selects_diff_and_emits_wal_pitr_time(tmp_path: Path) -> None:
    info = tmp_path / "info.json"
    full_stop = int(datetime(2026, 7, 19, 12, 0, 15, tzinfo=UTC).timestamp())
    diff_stop = int(datetime(2026, 7, 20, 12, 0, 30, tzinfo=UTC).timestamp())
    info.write_text(
        json.dumps(
            [
                {
                    "name": "fb-agent",
                    "backup": [
                        {
                            "label": "20260719-120005F",
                            "type": "full",
                            "timestamp": {"start": full_stop - 10, "stop": full_stop},
                            "archive": {
                                "start": "000000010000000000000001",
                                "stop": "000000010000000000000002",
                            },
                        },
                        {
                            "label": "20260719-120005F_20260720-120010D",
                            "type": "diff",
                            "timestamp": {"start": diff_stop - 20, "stop": diff_stop},
                            "archive": {
                                "start": "000000010000000000000003",
                                "stop": "000000010000000000000004",
                            },
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = _run("latest-recoverable", "--info", info)

    assert result.stdout.strip() == ("20260719-120005F_20260720-120010D\t2026-07-20T12:00:30Z")


def test_latest_recoverable_rejects_backup_without_archived_wal(tmp_path: Path) -> None:
    info = tmp_path / "info.json"
    info.write_text(
        json.dumps(
            [
                {
                    "name": "fb-agent",
                    "backup": [
                        {
                            "label": "20260719-120005F",
                            "type": "full",
                            "timestamp": {"start": 1, "stop": 2},
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = _run("latest-recoverable", "--info", info, check=False)

    assert result.returncode != 0
    assert "no completed backup with a WAL chain" in result.stderr
