# -*- coding: utf-8 -*-
"""Scanner: запускает поиск в Meta Ad Library через browser-agent gRPC + сохраняет результаты в БД.

Использует AdLibraryClient (clients/python_grpc/ad_library_client.py) — он уже работает.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from clients.python_grpc.ad_library_client import (
    AdLibraryClient,
    AdLibraryError,
)
from core.ad_library.classifier import extract_ad_text, score_relevance_to_slot

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Результат одного scan-запуска."""

    scan_id: uuid.UUID
    slot: str
    country: str
    ads_count: int
    duration_ms: int
    status: str  # done / failed / running
    error: str | None = None


async def run_scan(
    engine: AsyncEngine,
    *,
    slot: str,
    country: str,
    triggered_by: str,
    search_type: str = "keyword_unordered",
    max_pages: int = 10,
    grpc_host: str = "localhost",
    grpc_port: int = 50051,
    session_id: str = "",
) -> ScanResult:
    """Один полный scan: GraphQL → нормализация → сохранение в БД.

    ПРАВИЛО: slot и country переданы дословно. Не подменять.
    """
    if not slot.strip():
        raise ValueError("slot не может быть пустым")
    if not country.strip() or len(country.strip()) != 2:
        raise ValueError(f"country должен быть ISO-2 (получил {country!r})")

    slot = slot.strip()
    country = country.strip().upper()
    started_at = datetime.now(timezone.utc)
    scan_id = uuid.uuid4()

    # 1. INSERT в ad_library_scan со статусом running
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_library_scan
                    (id, slot, country, search_type, max_pages, status, started_at, triggered_by)
                VALUES
                    (:id, :slot, :country, :st, :mp, 'running', :sa, :tb)
                """
            ),
            {
                "id": scan_id,
                "slot": slot,
                "country": country,
                "st": search_type,
                "mp": max_pages,
                "sa": started_at,
                "tb": triggered_by,
            },
        )

    error_message: str | None = None
    ads_count = 0
    raw_ads: list[dict[str, Any]] = []

    # 2. gRPC вызов
    client = AdLibraryClient(grpc_host=grpc_host, grpc_port=grpc_port)
    try:
        await client.start()
        result = await client.search_ads(
            country=country,
            query=slot,
            search_type=search_type,
            max_pages=max_pages,
            session_id=session_id,
            raise_on_error=False,
        )
        raw_ads = result.ads
        ads_count = result.ad_count
    except AdLibraryError as exc:
        error_message = f"Ad Library error: {exc} (type={exc.type}, code={exc.code})"
        logger.exception(error_message)
    except Exception as exc:
        error_message = f"gRPC error: {exc}"
        logger.exception(error_message)
    finally:
        try:
            await client.close()
        except Exception:
            pass

    finished_at = datetime.now(timezone.utc)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)

    # 3. Сохраняем результаты — даже если есть ошибка, status пересчитываем
    if error_message:
        status = "failed"
    else:
        status = "done"
        await _persist_ads(engine, scan_id=scan_id, slot=slot, country=country, raw_ads=raw_ads)

    # 4. Обновляем scan-запись финальным статусом
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_library_scan
                SET status = :st,
                    ads_count = :ac,
                    duration_ms = :dm,
                    finished_at = :fa,
                    error_message = :err
                WHERE id = :id
                """
            ),
            {
                "id": scan_id,
                "st": status,
                "ac": ads_count,
                "dm": duration_ms,
                "fa": finished_at,
                "err": error_message,
            },
        )

    return ScanResult(
        scan_id=scan_id,
        slot=slot,
        country=country,
        ads_count=ads_count,
        duration_ms=duration_ms,
        status=status,
        error=error_message,
    )


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_date(v: Any):
    """Meta отдаёт started_running_on либо как 'YYYY-MM-DD', либо как unix timestamp.

    Возвращает datetime.date или None. asyncpg требует именно date-объект
    при CAST AS DATE — строка ISO даёт DataError.
    """
    from datetime import date

    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).date()
        except (ValueError, OSError):
            return None
    return None


async def _persist_ads(
    engine: AsyncEngine,
    *,
    scan_id: uuid.UUID,
    slot: str,
    country: str,
    raw_ads: list[dict[str, Any]],
) -> None:
    """UPSERT каждого ad в ad_library_ad + INSERT snapshot в ad_library_snapshot."""
    if not raw_ads:
        return

    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        for rank, ad in enumerate(raw_ads, start=1):
            ad_archive_id = _safe_int(ad.get("ad_archive_id"))
            if ad_archive_id is None:
                continue

            snap = ad.get("snapshot") or {}
            page_id = _safe_int(ad.get("page_id") or snap.get("page_id"))
            page_name = (ad.get("page_name") or snap.get("page_name") or "")[:255]
            page_url = ad.get("page_profile_uri") or snap.get("link_url")
            ad_format = None
            if snap.get("videos"):
                ad_format = "video"
            elif snap.get("images"):
                ad_format = "image"
            elif snap.get("cards"):
                ad_format = "carousel"

            started_iso = _safe_date(
                ad.get("start_date") or ad.get("start_date_string") or snap.get("creation_time")
            )

            # Классификация — score relevance к slot + детект вертикали
            ad_text = extract_ad_text(ad)
            classification = score_relevance_to_slot(slot, ad_text, page_name=page_name)

            await conn.execute(
                text(
                    """
                    INSERT INTO ad_library_ad
                        (ad_archive_id, page_id, page_name, page_url, slot, country,
                         started_running_on, ad_format, classification_score, vertical,
                         first_seen_at, last_seen_at)
                    VALUES
                        (:aid, :pid, :pn, :pu, :slot, :country,
                         CAST(:sr AS DATE), :af, :cs, :vt,
                         :now, :now)
                    ON CONFLICT (ad_archive_id) DO UPDATE
                    SET last_seen_at = :now,
                        page_name = EXCLUDED.page_name,
                        page_url = EXCLUDED.page_url,
                        ad_format = COALESCE(EXCLUDED.ad_format, ad_library_ad.ad_format),
                        classification_score = EXCLUDED.classification_score,
                        vertical = COALESCE(EXCLUDED.vertical, ad_library_ad.vertical)
                    """
                ),
                {
                    "aid": ad_archive_id,
                    "pid": page_id or 0,
                    "pn": page_name,
                    "pu": page_url,
                    "slot": slot,
                    "country": country,
                    "sr": started_iso,
                    "af": ad_format,
                    "cs": classification.score,
                    "vt": classification.vertical,
                    "now": now,
                },
            )

            # INSERT snapshot
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_library_snapshot
                        (scan_id, ad_archive_id, scanned_at, is_active, position_rank, raw_json)
                    VALUES
                        (:sid, :aid, :now, TRUE, :rank, CAST(:raw AS JSONB))
                    """
                ),
                {
                    "sid": scan_id,
                    "aid": ad_archive_id,
                    "now": now,
                    "rank": rank,
                    "raw": json.dumps(ad, ensure_ascii=False),
                },
            )
