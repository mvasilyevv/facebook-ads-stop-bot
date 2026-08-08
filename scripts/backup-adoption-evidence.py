#!/usr/bin/env python3
"""Write and validate immutable pgBackRest adoption/restore evidence."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

FULL_SCHEMA = "fb-agent-pgbackrest-full-evidence/v2"
RESTORE_SCHEMA = "fb-agent-pgbackrest-restore-evidence/v2"


class EvidenceError(ValueError):
    pass


def _die(message: str) -> NoReturn:
    raise EvidenceError(message)


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{field} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        _die(f"{field} must include a timezone")
    return parsed


def _read_json(path: Path) -> Any:
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            _die(f"evidence must be a regular file: {path}")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            _die(f"evidence must have mode 600: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read evidence JSON: {path}") from exc


def _latest_full(document: Any) -> dict[str, Any]:
    if not isinstance(document, list) or len(document) != 1:
        _die("pgBackRest info must contain exactly one stanza")
    stanza = document[0]
    if not isinstance(stanza, dict) or stanza.get("name") != "fb-agent":
        _die("pgBackRest info does not describe the fb-agent stanza")
    backups = stanza.get("backup")
    if not isinstance(backups, list):
        _die("pgBackRest info has no backup list")
    full = [item for item in backups if isinstance(item, dict) and item.get("type") == "full"]
    if not full:
        _die("pgBackRest repository has no full backup")

    def stop_time(item: dict[str, Any]) -> int:
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, dict) or not isinstance(timestamp.get("stop"), int):
            _die("full backup has no numeric stop timestamp")
        return int(timestamp["stop"])

    return max(full, key=stop_time)


def _latest_recoverable(document: Any) -> dict[str, Any]:
    """Select the newest completed full/differential/incremental WAL chain."""
    if not isinstance(document, list) or len(document) != 1:
        _die("pgBackRest info must contain exactly one stanza")
    stanza = document[0]
    if not isinstance(stanza, dict) or stanza.get("name") != "fb-agent":
        _die("pgBackRest info does not describe the fb-agent stanza")
    backups = stanza.get("backup")
    if not isinstance(backups, list):
        _die("pgBackRest info has no backup list")
    recoverable: list[dict[str, Any]] = []
    for item in backups:
        if not isinstance(item, dict) or item.get("type") not in {"full", "diff", "incr"}:
            continue
        timestamp = item.get("timestamp")
        archive = item.get("archive")
        if (
            isinstance(timestamp, dict)
            and isinstance(timestamp.get("stop"), int)
            and isinstance(archive, dict)
            and isinstance(archive.get("start"), str)
            and archive.get("start")
            and isinstance(archive.get("stop"), str)
            and archive.get("stop")
        ):
            recoverable.append(item)
    if not recoverable:
        _die("pgBackRest repository has no completed backup with a WAL chain")
    return max(recoverable, key=lambda item: int(item["timestamp"]["stop"]))


def _recoverable_by_label(document: Any, label: str) -> dict[str, Any]:
    latest = _latest_recoverable(document)
    stanza = document[0]
    for item in stanza["backup"]:
        if isinstance(item, dict) and item.get("label") == label:
            timestamp = item.get("timestamp")
            archive = item.get("archive")
            if (
                item.get("type") in {"full", "diff", "incr"}
                and isinstance(timestamp, dict)
                and isinstance(timestamp.get("stop"), int)
                and isinstance(archive, dict)
                and isinstance(archive.get("start"), str)
                and archive.get("start")
                and isinstance(archive.get("stop"), str)
                and archive.get("stop")
            ):
                return item
            _die(f"backup set {label} has no complete archived WAL chain")
    _die(f"backup set {label} is absent; newest recoverable is {latest['label']}")


def _repository_policy(config_path: Path, backup_env_path: Path) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with config_path.open(encoding="utf-8") as source:
            parser.read_file(source)
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        raise EvidenceError("cannot read effective pgBackRest config") from exc
    if not parser.has_section("global"):
        _die("pgBackRest config has no [global] section")
    section = parser["global"]
    environment: dict[str, str] = {}
    try:
        for raw_line in backup_env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            environment[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError) as exc:
        raise EvidenceError("cannot read effective pgBackRest environment") from exc

    def effective(option: str) -> str | None:
        env_key = f"PGBACKREST_{option.upper().replace('-', '_')}"
        return environment.get(env_key, section.get(option))

    try:
        retention = int(effective("repo1-retention-full") or "")
    except (KeyError, ValueError) as exc:
        raise EvidenceError("pgBackRest full retention is invalid") from exc
    return {
        "type": effective("repo1-type"),
        "cipher": effective("repo1-cipher-type"),
        "retention_full_type": effective("repo1-retention-full-type"),
        "retention_full": retention,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }


def _atomic_evidence(path: Path, document: dict[str, Any]) -> None:
    if not path.is_absolute() or ".." in path.parts:
        _die("evidence output must be an absolute path without '..'")
    parent = path.parent.resolve(strict=True)
    if path.parent != parent or stat.S_IMODE(parent.stat().st_mode) & 0o022:
        _die("evidence parent must be canonical and not group/world writable")
    checksum_path = path.with_name(f"{path.name}.sha256")
    for destination in (path, checksum_path):
        if destination.exists() or destination.is_symlink():
            _die(f"immutable evidence already exists: {destination}")
    rendered = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(rendered).hexdigest()
    temporary_paths: list[Path] = []
    try:
        for destination, content in (
            (path, rendered),
            (checksum_path, f"{digest}  {path.name}\n".encode()),
        ):
            descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
            temporary_path = Path(temporary)
            temporary_paths.append(temporary_path)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, destination)
            temporary_paths.remove(temporary_path)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)


def _validate_checksum(path: Path) -> None:
    checksum_path = path.with_name(f"{path.name}.sha256")
    try:
        file_stat = checksum_path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            _die("evidence checksum must be a regular file")
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            _die("evidence checksum must have mode 600")
        parts = checksum_path.read_text(encoding="ascii").strip().split()
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        raise EvidenceError("cannot read evidence checksum") from exc
    if len(parts) != 2 or parts[1] != path.name:
        _die("evidence checksum format is invalid")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if parts[0] != actual:
        _die("evidence checksum does not match")


def _validate_full(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema") != FULL_SCHEMA:
        _die("invalid full-backup evidence schema")
    if document.get("release_id") in (None, ""):
        _die("full-backup evidence has no release_id")
    started = _timestamp(str(document.get("started_at")), "full.started_at")
    completed = _timestamp(str(document.get("completed_at")), "full.completed_at")
    if completed < started:
        _die("full backup completed before it started")
    backup = document.get("backup")
    if not isinstance(backup, dict) or backup.get("type") != "full" or not backup.get("label"):
        _die("full-backup evidence has no full backup label")
    repository = document.get("repository")
    expected = {
        "type": "s3",
        "cipher": "aes-256-cbc",
        "retention_full_type": "time",
        "retention_full": 35,
    }
    if (
        not isinstance(repository, dict)
        or {key: repository.get(key) for key in expected} != expected
    ):
        _die("full-backup repository policy is not the accepted off-host policy")
    if not re.fullmatch(r"[0-9a-f]{64}", str(repository.get("config_sha256", ""))):
        _die("full-backup evidence has no effective config digest")
    backup_timestamps = backup.get("timestamp")
    if not isinstance(backup_timestamps, dict) or not all(
        isinstance(backup_timestamps.get(field), int) for field in ("start", "stop")
    ):
        _die("full-backup evidence has no numeric backup timestamps")
    backup_started = datetime.fromtimestamp(backup_timestamps["start"], tz=timezone.utc)
    backup_completed = datetime.fromtimestamp(backup_timestamps["stop"], tz=timezone.utc)
    if backup_started < started - timedelta(minutes=1) or backup_completed > completed + timedelta(
        minutes=1
    ):
        _die("full-backup label does not belong to this audited backup run")
    return document


def _validate_restore(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema") != RESTORE_SCHEMA:
        _die("invalid restore evidence schema")
    started = _timestamp(str(document.get("started_at")), "restore.started_at")
    completed = _timestamp(str(document.get("completed_at")), "restore.completed_at")
    if completed < started or document.get("pg_is_in_recovery") is not False:
        _die("restore did not reach a promoted, queryable database")
    revisions = document.get("alembic_revisions")
    if (
        not isinstance(revisions, list)
        or not revisions
        or not all(isinstance(value, str) and value for value in revisions)
    ):
        _die("restore evidence has no Alembic revision")
    isolation = document.get("isolation")
    if not isinstance(isolation, dict):
        _die("restore isolation evidence is missing")
    if isolation.get("production_volume_mounted") is not False:
        _die("restore evidence does not prove production-volume isolation")
    expected_prefixes = {
        "volume": "fb_agent_restore_drill_",
        "network": "fb_agent_restore_drill_",
        "container": "fb-agent-restore-drill-",
    }
    for field, prefix in expected_prefixes.items():
        if not isinstance(isolation.get(field), str) or not isolation[field].startswith(prefix):
            _die(f"restore isolation {field} is invalid")
    mounts = isolation.get("observed_mounts")
    if not isinstance(mounts, list) or not mounts:
        _die("restore evidence has no observed Docker mounts")
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("type"), str)
        and isinstance(item.get("destination"), str)
        for item in mounts
    ):
        _die("restore evidence Docker mount observation is invalid")
    if not any(
        item["name"] == isolation["volume"] and item["destination"] == "/var/lib/postgresql/data"
        for item in mounts
    ):
        _die("restore volume was not observed at PostgreSQL data directory")
    backup_set = document.get("backup_set")
    if not isinstance(backup_set, str) or not re.fullmatch(
        r"[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?", backup_set
    ):
        _die("restore evidence has no valid explicit backup set")
    target_time = document.get("target_time")
    if target_time is not None:
        _timestamp(str(target_time), "restore.target_time")
    marker = document.get("post_backup_marker")
    if marker is not None:
        if not isinstance(marker, dict):
            _die("restore post-backup marker evidence is invalid")
        if marker.get("observed") is not True:
            _die("post-backup WAL marker was not observed after PITR")
        if not all(isinstance(marker.get(key), str) and marker[key] for key in ("key", "token")):
            _die("post-backup WAL marker identity is missing")
    return document


def write_full(args: argparse.Namespace) -> None:
    info = json.loads(args.info.read_text(encoding="utf-8"))
    backup = _latest_full(info)
    document = {
        "schema": FULL_SCHEMA,
        "release_id": args.release_id,
        "started_at": args.started_at,
        "completed_at": args.completed_at,
        "repository": _repository_policy(args.config, args.backup_env),
        "backup": backup,
    }
    _validate_full(document)
    _atomic_evidence(args.output, document)


def latest_full_label(args: argparse.Namespace) -> None:
    info = json.loads(args.info.read_text(encoding="utf-8"))
    print(_latest_full(info)["label"])


def latest_recoverable(args: argparse.Namespace) -> None:
    info = json.loads(args.info.read_text(encoding="utf-8"))
    backup = _latest_recoverable(info)
    stopped_at = datetime.fromtimestamp(backup["timestamp"]["stop"], tz=timezone.utc)
    print(f"{backup['label']}\t{stopped_at.isoformat().replace('+00:00', 'Z')}")


def backup_details(args: argparse.Namespace) -> None:
    info = json.loads(args.info.read_text(encoding="utf-8"))
    backup = _recoverable_by_label(info, args.backup_set)
    stopped_at = datetime.fromtimestamp(backup["timestamp"]["stop"], tz=timezone.utc)
    print(stopped_at.isoformat().replace("+00:00", "Z"))


def evidence_full_label(args: argparse.Namespace) -> None:
    document = _validate_full(_read_json(args.full))
    _validate_checksum(args.full)
    print(document["backup"]["label"])


def write_restore(args: argparse.Namespace) -> None:
    mounts: list[dict[str, str]] = []
    for value in args.mount:
        try:
            name, mount_type, destination = value.split("|", 2)
        except ValueError as exc:
            raise EvidenceError("observed mount must be name|type|destination") from exc
        mounts.append({"name": name, "type": mount_type, "destination": destination})
    production_volume_mounted = any(item["name"] == args.production_volume for item in mounts)
    marker = None
    if args.marker_key or args.marker_token:
        if not args.marker_key or not args.marker_token:
            _die("post-backup marker key and token must be supplied together")
        marker = {
            "key": args.marker_key,
            "token": args.marker_token,
            "observed": args.marker_observed == "true",
        }
    document = {
        "schema": RESTORE_SCHEMA,
        "release_id": args.release_id,
        "backup_set": args.backup_set,
        "target_time": args.target_time,
        "started_at": args.started_at,
        "completed_at": args.completed_at,
        "pg_is_in_recovery": args.pg_is_in_recovery == "true",
        "recovery_target_setting": args.recovery_target_setting,
        "alembic_revisions": sorted(set(args.revision)),
        "post_backup_marker": marker,
        "isolation": {
            "volume": args.volume,
            "network": args.network,
            "container": args.container,
            "production_volume_mounted": production_volume_mounted,
            "production_volume": args.production_volume,
            "observed_mounts": mounts,
        },
    }
    _validate_restore(document)
    _atomic_evidence(args.output, document)


def validate_pair(args: argparse.Namespace) -> None:
    full = _validate_full(_read_json(args.full))
    restore = _validate_restore(_read_json(args.restore))
    _validate_checksum(args.full)
    _validate_checksum(args.restore)
    if restore["backup_set"] != full["backup"]["label"]:
        _die("restore drill did not use the accepted full backup set")
    full_completed = _timestamp(full["completed_at"], "full.completed_at")
    restore_started = _timestamp(restore["started_at"], "restore.started_at")
    if restore_started < full_completed:
        _die("restore drill started before the accepted full backup completed")
    if restore.get("release_id") != full.get("release_id"):
        _die("full and restore evidence belong to different releases")
    if args.expected_release_id and full.get("release_id") != args.expected_release_id:
        _die("backup evidence does not belong to the candidate release")
    if args.max_age_seconds is not None:
        age = datetime.now(timezone.utc) - full_completed
        if age < timedelta(seconds=-60) or age > timedelta(seconds=args.max_age_seconds):
            _die("full-backup evidence is stale for this migration attempt")
    if args.require_pitr_marker:
        target = restore.get("target_time")
        marker = restore.get("post_backup_marker")
        if target is None or not isinstance(marker, dict) or marker.get("observed") is not True:
            _die("migration gate requires observed post-backup WAL/PITR marker")
        backup_stop = datetime.fromtimestamp(full["backup"]["timestamp"]["stop"], tz=timezone.utc)
        if _timestamp(str(target), "restore.target_time") <= backup_stop:
            _die("PITR target does not prove WAL replay after the full backup")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    full = commands.add_parser("write-full")
    full.add_argument("--output", type=Path, required=True)
    full.add_argument("--info", type=Path, required=True)
    full.add_argument("--config", type=Path, required=True)
    full.add_argument("--backup-env", type=Path, required=True)
    full.add_argument("--release-id", required=True)
    full.add_argument("--started-at", required=True)
    full.add_argument("--completed-at", required=True)
    full.set_defaults(func=write_full)
    latest = commands.add_parser("latest-full-label")
    latest.add_argument("--info", type=Path, required=True)
    latest.set_defaults(func=latest_full_label)
    recoverable = commands.add_parser("latest-recoverable")
    recoverable.add_argument("--info", type=Path, required=True)
    recoverable.set_defaults(func=latest_recoverable)
    details = commands.add_parser("backup-details")
    details.add_argument("--info", type=Path, required=True)
    details.add_argument("--backup-set", required=True)
    details.set_defaults(func=backup_details)
    evidence_label = commands.add_parser("evidence-full-label")
    evidence_label.add_argument("--full", type=Path, required=True)
    evidence_label.set_defaults(func=evidence_full_label)
    restore = commands.add_parser("write-restore")
    restore.add_argument("--output", type=Path, required=True)
    restore.add_argument("--release-id", required=True)
    restore.add_argument("--backup-set", required=True)
    restore.add_argument("--target-time")
    restore.add_argument("--started-at", required=True)
    restore.add_argument("--completed-at", required=True)
    restore.add_argument("--revision", action="append", required=True)
    restore.add_argument("--volume", required=True)
    restore.add_argument("--network", required=True)
    restore.add_argument("--container", required=True)
    restore.add_argument("--pg-is-in-recovery", choices=("true", "false"), required=True)
    restore.add_argument("--recovery-target-setting")
    restore.add_argument("--production-volume", required=True)
    restore.add_argument("--mount", action="append", required=True)
    restore.add_argument("--marker-key")
    restore.add_argument("--marker-token")
    restore.add_argument("--marker-observed", choices=("true", "false"), default="false")
    restore.set_defaults(func=write_restore)
    validate = commands.add_parser("validate-pair")
    validate.add_argument("--full", type=Path, required=True)
    validate.add_argument("--restore", type=Path, required=True)
    validate.add_argument("--expected-release-id")
    validate.add_argument("--max-age-seconds", type=int)
    validate.add_argument("--require-pitr-marker", action="store_true")
    validate.set_defaults(func=validate_pair)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (EvidenceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
