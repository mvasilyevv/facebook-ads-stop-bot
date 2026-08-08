"""Fail-closed identity checks for rows produced by the Meta scanner."""

from __future__ import annotations

from collections.abc import Iterable

from core.scanner.models import ScannedAdRow


def _has_required_text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_required_status(value: str) -> bool:
    return _has_required_text(value) and value.strip().upper() != "UNKNOWN"


def _is_canonical_meta_id(value: str) -> bool:
    return isinstance(value, str) and bool(value) and value.isascii() and value.isdigit()


def find_incomplete_scan_row_ids(rows: Iterable[ScannedAdRow]) -> list[str]:
    """Return diagnostic markers for rows unsafe for catalog/FSM processing."""
    partial: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        incomplete = (
            not _is_canonical_meta_id(row.fb_ad_id)
            or not _is_canonical_meta_id(row.campaign_id)
            or not _is_canonical_meta_id(row.adset_id)
            or not _has_required_text(row.campaign_name)
            or not _has_required_text(row.adset_name)
            or not _has_required_text(row.ad_name)
            or not _has_required_status(row.delivery_status)
        )
        if not incomplete:
            continue
        marker = (
            row.fb_ad_id.strip()
            if isinstance(row.fb_ad_id, str) and row.fb_ad_id.strip()
            else f"missing_fb_ad_id:row_{index}"
        )
        if marker not in seen:
            seen.add(marker)
            partial.append(marker)
    return partial


__all__ = ["find_incomplete_scan_row_ids"]
