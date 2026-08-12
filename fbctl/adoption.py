"""Small, dependency-free adoption-bundle preflight checks for bootstrap."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fbctl.errors import FbctlError
from fbctl.files import PrivateFileSnapshot, snapshot_private_file

MAX_ADOPTION_BUNDLE_BYTES = 8 * 1024 * 1024
_OWNER_CONTRACT_ERROR = "adoption bundle owner contract is invalid"
_RECIPIENT_FIELDS = frozenset({"chat_id", "telegram_user_id", "username", "display_name", "role"})


@dataclass(frozen=True)
class VerifiedAdoptionBundle:
    payload: bytes
    owner_telegram_user_id: str
    snapshot: PrivateFileSnapshot


def verify_adoption_bundle(
    path: Path,
    *,
    required_uid: int | None = None,
    directory_fd: int | None = None,
) -> VerifiedAdoptionBundle:
    """Return the exact private bundle bytes and its manifest-hashed owner."""

    try:
        snapshot = snapshot_private_file(
            path,
            label="adoption bundle",
            maximum=MAX_ADOPTION_BUNDLE_BYTES,
            required_uid=required_uid,
            directory_fd=directory_fd,
        )
        assert snapshot is not None
        document = json.loads(snapshot.payload.decode("utf-8"))
        owner = _verified_owner(document)
    except (
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise FbctlError(_OWNER_CONTRACT_ERROR) from exc
    except FbctlError as exc:
        raise FbctlError(_OWNER_CONTRACT_ERROR) from exc
    return VerifiedAdoptionBundle(snapshot.payload, str(owner), snapshot)


def verify_adoption_bundle_owner(
    path: Path,
    *,
    owner_telegram_user_id: str,
) -> bytes:
    """Return the exact bytes whose manifest-hashed owner contract was verified.

    The full adoption importer remains authoritative for every other section.
    This narrow preflight intentionally exposes one constant failure so neither
    bundle identities nor values become bootstrap diagnostics.
    """

    try:
        verified = verify_adoption_bundle(
            path, required_uid=0 if os.geteuid() == 0 else os.getuid()
        )
        payload = verified.payload
        document = json.loads(payload.decode("utf-8"))
        _verify_document(document, owner_telegram_user_id=owner_telegram_user_id)
    except (
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise FbctlError(_OWNER_CONTRACT_ERROR) from exc
    return payload


def _verify_document(document: Any, *, owner_telegram_user_id: str) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != "adoption-bundle/v1":
        raise ValueError
    if not isinstance(owner_telegram_user_id, str) or not re.fullmatch(
        r"[1-9][0-9]*", owner_telegram_user_id
    ):
        raise ValueError
    expected_owner_id = int(owner_telegram_user_id)
    if expected_owner_id <= 0:
        raise ValueError

    sections = document.get("sections")
    counts = document.get("entity_counts")
    hashes = document.get("section_sha256")
    if (
        not isinstance(sections, dict)
        or not isinstance(counts, dict)
        or not isinstance(hashes, dict)
    ):
        raise ValueError
    recipients = sections.get("recipients")
    recipient_count = counts.get("recipients")
    if (
        not isinstance(recipients, list)
        or isinstance(recipient_count, bool)
        or not isinstance(recipient_count, int)
        or recipient_count != len(recipients)
    ):
        raise ValueError
    expected_hash = hashes.get("recipients")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError
    actual_hash = hashlib.sha256(_canonical_json(recipients).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise ValueError

    user_ids: set[int] = set()
    chat_ids: set[int] = set()
    owners: list[int] = []
    previous_user_id: int | None = None
    for recipient in recipients:
        if not isinstance(recipient, dict) or set(recipient) != _RECIPIENT_FIELDS:
            raise ValueError
        chat_id = recipient["chat_id"]
        telegram_user_id = recipient["telegram_user_id"]
        if (
            isinstance(chat_id, bool)
            or isinstance(telegram_user_id, bool)
            or not isinstance(chat_id, int)
            or not isinstance(telegram_user_id, int)
            or chat_id <= 0
            or telegram_user_id <= 0
            or chat_id != telegram_user_id
            or chat_id in chat_ids
            or telegram_user_id in user_ids
        ):
            raise ValueError
        if previous_user_id is not None and telegram_user_id <= previous_user_id:
            raise ValueError
        if recipient["role"] not in {"owner", "recipient"}:
            raise ValueError
        if not _bounded_nullable_string(
            recipient["username"], maximum=64
        ) or not _bounded_nullable_string(recipient["display_name"], maximum=128):
            raise ValueError
        chat_ids.add(chat_id)
        user_ids.add(telegram_user_id)
        previous_user_id = telegram_user_id
        if recipient["role"] == "owner":
            owners.append(telegram_user_id)
    if len(owners) != 1 or owners[0] != expected_owner_id:
        raise ValueError


def _verified_owner(document: Any) -> int:
    """Validate the recipients contract and return its single owner."""

    if not isinstance(document, dict) or document.get("schema_version") != "adoption-bundle/v1":
        raise ValueError
    sections = document.get("sections")
    if not isinstance(sections, dict) or not isinstance(sections.get("recipients"), list):
        raise ValueError
    candidates = [
        recipient.get("telegram_user_id")
        for recipient in sections["recipients"]
        if isinstance(recipient, dict) and recipient.get("role") == "owner"
    ]
    if (
        len(candidates) != 1
        or isinstance(candidates[0], bool)
        or not isinstance(candidates[0], int)
    ):
        raise ValueError
    owner = candidates[0]
    _verify_document(document, owner_telegram_user_id=str(owner))
    return owner


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _bounded_nullable_string(value: Any, *, maximum: int) -> bool:
    return value is None or isinstance(value, str) and len(value) <= maximum
