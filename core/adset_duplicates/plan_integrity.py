"""Canonical integrity contract for irreversible ad-set duplication plans."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from core.meta_api.identity import require_ad_account_id

DUPLICATE_ADSET_STRUCTURE_KIND = "duplicate_adset_structure"
_SHA256_BYTES = 32
_SHA256_HEX_CHARS = _SHA256_BYTES * 2


def canonical_duplicate_execution_payload(
    *,
    mutation_kind: str,
    target_id: str,
    params: dict[str, Any],
    ad_account_id: object,
) -> dict[str, Any]:
    """Return the only payload shape covered by the duplication plan digest."""
    if mutation_kind != DUPLICATE_ADSET_STRUCTURE_KIND:
        raise ValueError("unexpected duplicate mutation_kind")
    if isinstance(target_id, bool) or not isinstance(target_id, (str, int)):
        raise ValueError("target_id must be a numeric id")
    canonical_target_id = str(target_id).strip()
    if not canonical_target_id.isdigit():
        raise ValueError("target_id must be a numeric id")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    unsigned_params = {key: value for key, value in params.items() if key != "plan_digest"}
    return {
        "mutation_kind": DUPLICATE_ADSET_STRUCTURE_KIND,
        "target_id": canonical_target_id,
        "params": unsigned_params,
        "ad_account_id": require_ad_account_id(ad_account_id),
    }


def duplicate_execution_plan_digest(
    *,
    mutation_kind: str,
    target_id: str,
    params: dict[str, Any],
    ad_account_id: object,
) -> bytes:
    """SHA-256 of the exact canonical execution payload."""
    payload = canonical_duplicate_execution_payload(
        mutation_kind=mutation_kind,
        target_id=target_id,
        params=params,
        ad_account_id=ad_account_id,
    )
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).digest()


def duplicate_execution_plan_digest_matches(
    *,
    mutation_kind: str,
    target_id: str,
    params: dict[str, Any],
    ad_account_id: object,
    plan_digest: object,
) -> bool:
    """Verify canonical lowercase SHA-256 hex using a fixed-size comparison."""
    expected = duplicate_execution_plan_digest(
        mutation_kind=mutation_kind,
        target_id=target_id,
        params=params,
        ad_account_id=ad_account_id,
    )
    well_formed = isinstance(plan_digest, str) and len(plan_digest) == _SHA256_HEX_CHARS
    try:
        decoded = bytes.fromhex(plan_digest) if isinstance(plan_digest, str) else b""
    except ValueError:
        decoded = b""
    well_formed = bool(
        well_formed
        and len(decoded) == _SHA256_BYTES
        and isinstance(plan_digest, str)
        and plan_digest == decoded.hex()
    )
    candidate = decoded if len(decoded) == _SHA256_BYTES else bytes(_SHA256_BYTES)
    matches = secrets.compare_digest(expected, candidate)
    return well_formed and matches


__all__ = [
    "DUPLICATE_ADSET_STRUCTURE_KIND",
    "canonical_duplicate_execution_payload",
    "duplicate_execution_plan_digest",
    "duplicate_execution_plan_digest_matches",
]
