"""Small, dependency-free adoption-bundle preflight checks for bootstrap."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from fbctl.errors import FbctlError

MAX_ADOPTION_BUNDLE_BYTES = 8 * 1024 * 1024
_OWNER_CONTRACT_ERROR = "adoption bundle owner contract is invalid"
_RECIPIENT_FIELDS = frozenset({"chat_id", "telegram_user_id", "username", "display_name", "role"})


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
        payload = _read_private_bundle(path)
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


def _read_private_bundle(path: Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_ADOPTION_BUNDLE_BYTES
        ):
            raise ValueError
        chunks: list[bytes] = []
        remaining = MAX_ADOPTION_BUNDLE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_ADOPTION_BUNDLE_BYTES:
            raise ValueError
        return payload
    finally:
        os.close(descriptor)


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


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _bounded_nullable_string(value: Any, *, maximum: int) -> bool:
    return value is None or isinstance(value, str) and len(value) <= maximum
