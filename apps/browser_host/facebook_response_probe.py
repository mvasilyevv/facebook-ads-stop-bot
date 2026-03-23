from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.scanner import ScannedAdRow

_NUMERIC_ID_PATTERN = re.compile(r"\b\d{8,20}\b")
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(slots=True, frozen=True)
class ResponsePayloadEntry:
    """Одна сохраненная response-нагрузка Ads Manager для диагностики."""

    url: str
    is_relevant: bool
    payload: Any


class FacebookResponseProbeService:
    """Временный диагностический сервис для сохранения сводки по response Ads Manager."""

    def __init__(self, *, enabled: bool, output_dir: str) -> None:
        self._enabled = enabled
        self._output_dir = Path(output_dir)

    async def write_incomplete_scope_report(
        self,
        *,
        profile_id: str,
        browser_host_name: str,
        page_url: str,
        page_title: str | None,
        expected_rows_count: int | None,
        response_entries: list[ResponsePayloadEntry],
        parsed_rows: list[ScannedAdRow],
    ) -> str | None:
        if not self._enabled:
            return None

        report = {
            "captured_at": datetime.now(tz=UTC).isoformat(),
            "profile_id": profile_id,
            "browser_host_name": browser_host_name,
            "page_url": page_url,
            "page_title": page_title,
            "expected_rows_count": expected_rows_count,
            "captured_response_count": len(response_entries),
            "parsed_row_count": len(parsed_rows),
            "parsed_fb_ad_ids": [row.fb_ad_id for row in parsed_rows],
            "responses": [self._summarize_entry(entry) for entry in response_entries],
        }

        await asyncio.to_thread(self._output_dir.mkdir, parents=True, exist_ok=True)
        file_stem = self._build_file_stem(
            browser_host_name=browser_host_name,
            profile_id=profile_id,
        )
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        latest_path = self._output_dir / f"{file_stem}__latest.json"
        history_path = self._output_dir / f"{file_stem}__{timestamp}.json"
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        await asyncio.to_thread(history_path.write_text, encoded, encoding="utf-8")
        await asyncio.to_thread(latest_path.write_text, encoded, encoding="utf-8")
        return str(latest_path)

    def _summarize_entry(self, entry: ResponsePayloadEntry) -> dict[str, Any]:
        stats = _PayloadStats()
        self._walk_value(entry.payload, stats)
        return {
            "url": entry.url,
            "is_relevant": entry.is_relevant,
            "dict_total": stats.dict_total,
            "list_total": stats.list_total,
            "max_list_size": stats.max_list_size,
            "row_list_total": stats.row_list_total,
            "ad_object_total": stats.ad_object_total,
            "unique_ad_ids": sorted(stats.unique_ad_ids),
            "sample_ad_ids": sorted(stats.unique_ad_ids)[:10],
            "top_level_keys": sorted(entry.payload.keys())[:20]
            if isinstance(entry.payload, dict)
            else [],
            "payload_excerpt": self._build_payload_excerpt(entry.payload),
        }

    def _walk_value(self, value: Any, stats: "_PayloadStats") -> None:
        if isinstance(value, dict):
            stats.dict_total += 1
            ad_id = self._extract_ad_id(value)
            if ad_id is not None:
                stats.ad_object_total += 1
                stats.unique_ad_ids.add(ad_id)
            rows_value = value.get("rows")
            if isinstance(rows_value, list):
                stats.row_list_total += len(rows_value)
            for nested in value.values():
                self._walk_value(nested, stats)
            return

        if isinstance(value, list):
            stats.list_total += 1
            stats.max_list_size = max(stats.max_list_size, len(value))
            for nested in value:
                self._walk_value(nested, stats)

    @staticmethod
    def _extract_ad_id(value: dict[str, Any]) -> str | None:
        for key in ("fb_ad_id", "ad_id", "id", "node_id"):
            raw_value = value.get(key)
            if raw_value is None:
                continue
            match = _NUMERIC_ID_PATTERN.search(str(raw_value))
            if match is not None:
                return match.group(0)
        for key in ("dimension_values", "atomic_values", "values"):
            nested_value = value.get(key)
            identifier = FacebookResponseProbeService._extract_ad_id_from_nested_value(nested_value)
            if identifier is not None:
                return identifier
        pair_key = (
            value.get("name") or value.get("field") or value.get("column") or value.get("key")
        )
        if isinstance(pair_key, str) and pair_key.strip().casefold() in {"ad_id", "fb_ad_id", "id"}:
            match = _NUMERIC_ID_PATTERN.search(str(value.get("value", "")))
            if match is not None:
                return match.group(0)
        return None

    @staticmethod
    def _extract_ad_id_from_nested_value(value: Any) -> str | None:
        if isinstance(value, dict):
            return FacebookResponseProbeService._extract_ad_id(value)
        if isinstance(value, list):
            for item in value:
                identifier = FacebookResponseProbeService._extract_ad_id_from_nested_value(item)
                if identifier is not None:
                    return identifier
        return None

    @staticmethod
    def _build_file_stem(*, browser_host_name: str, profile_id: str) -> str:
        safe_host = _SAFE_NAME_PATTERN.sub("-", browser_host_name).strip("-") or "unknown-host"
        safe_profile = _SAFE_NAME_PATTERN.sub("-", profile_id).strip("-") or "unknown-profile"
        return f"{safe_host}__{safe_profile}"

    def _build_payload_excerpt(self, value: Any, *, depth: int = 0) -> Any:
        if depth >= 2:
            if isinstance(value, dict):
                return {"type": "dict", "keys": sorted(value.keys())[:10]}
            if isinstance(value, list):
                return {"type": "list", "size": len(value)}
            return value

        if isinstance(value, dict):
            excerpt: dict[str, Any] = {}
            for key in list(value.keys())[:10]:
                excerpt[str(key)] = self._build_payload_excerpt(value[key], depth=depth + 1)
            return excerpt

        if isinstance(value, list):
            return [self._build_payload_excerpt(item, depth=depth + 1) for item in value[:3]]

        return value


@dataclass(slots=True)
class _PayloadStats:
    """Промежуточная статистика одного response payload."""

    dict_total: int = 0
    list_total: int = 0
    max_list_size: int = 0
    row_list_total: int = 0
    ad_object_total: int = 0
    unique_ad_ids: set[str] = field(default_factory=set)
