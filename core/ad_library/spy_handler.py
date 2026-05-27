# -*- coding: utf-8 -*-
"""Handler команды /spy <slot> <country>.

Используется TG-ботом и API endpoint'ом. Запускает run_pipeline в фоне,
шлёт ответ с топ-5 ads после завершения.

ПРАВИЛО: slot+country переданы дословно. Не подменяем.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.pipeline import run_pipeline

logger = logging.getLogger(__name__)


@dataclass
class SpyRequest:
    """Запрос на /spy команду."""

    slot: str
    country: str
    triggered_by: str
    max_pages: int = 10
    search_type: str = "keyword_unordered"


def parse_spy_args(args: str) -> SpyRequest | str:
    """Парсит аргументы /spy: <slot> <country>.

    Слот может быть multi-word: "/spy chicken road 2 KE"
    Country — последний токен, ISO-2.

    Returns: SpyRequest или str (текст ошибки).
    """
    args = args.strip()
    if not args:
        return (
            "Использование: /spy <слот> <страна>\n"
            "Пример: /spy chicken road 2 KE\n"
            "Страна — ISO-2 код (KE, CD, MZ, GH, TR, ...)"
        )

    tokens = args.split()
    if len(tokens) < 2:
        return "Нужно минимум 2 аргумента: слот и страна (ISO-2)"

    country = tokens[-1].strip().upper()
    if len(country) != 2 or not country.isalpha():
        return f"Последний аргумент должен быть ISO-2 кодом страны (получил {country!r})"

    slot = " ".join(tokens[:-1]).strip()
    if not slot:
        return "Не распознал слот"

    return SpyRequest(slot=slot, country=country, triggered_by="tg:unknown")


def format_short_summary(pipeline_result: Any) -> str:
    """Краткое summary для TG сообщения после завершения scan'а."""
    scan = pipeline_result.scan
    if scan.status == "failed":
        return f"❌ Ошибка scan'а: {scan.error}\nslot=`{scan.slot}` country=`{scan.country}`"

    if scan.ads_count == 0:
        return (
            f"🔍 Пусто.\n"
            f"slot=`{scan.slot}` country=`{scan.country}`\n"
            f"Ad Library не вернул ни одного объявления.\n"
            f"Возможные причины: новый/непопулярный слот, неправильная страна, "
            f"объявления есть но Meta их не отдала через GraphQL."
        )

    tier_counts = pipeline_result.tier_counts or {}
    s_count = tier_counts.get("S", 0)
    a_count = tier_counts.get("A", 0)
    b_count = tier_counts.get("B", 0)
    c_count = tier_counts.get("C", 0)

    top_winners = (pipeline_result.report or {}).get("top_winners_json") or []
    lines = [
        f"✅ Scan готов: slot=`{scan.slot}` country=`{scan.country}`",
        f"Всего: **{scan.ads_count}** ads, длительность {scan.duration_ms} ms",
        f"Tiers: S={s_count} A={a_count} B={b_count} C={c_count}",
        "",
        "**Топ-5 винеров:**",
    ]
    for i, w in enumerate(top_winners[:5], start=1):
        lines.append(
            f"{i}. [{w['tier']}] **{w['page_name']}** "
            f"(score={w['score']}, "
            f"running={w.get('started_running_on') or '?'}, "
            f"format={w.get('ad_format') or '?'})"
        )
    return "\n".join(lines)


async def execute_spy(
    engine: AsyncEngine,
    *,
    request: SpyRequest,
    media_root: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Выполняет полный pipeline и возвращает (short_summary, full_payload).

    Pipeline идёт inline (не в фоне) — caller сам решает делать ли это asyncio.create_task().
    """
    pipeline_result = await run_pipeline(
        engine,
        slot=request.slot,
        country=request.country,
        triggered_by=request.triggered_by,
        search_type=request.search_type,
        max_pages=request.max_pages,
        media_root=media_root or Path("./data/ad_library_media"),
    )

    summary = format_short_summary(pipeline_result)
    full = {
        "scan_id": str(pipeline_result.scan.scan_id),
        "ads_count": pipeline_result.scan.ads_count,
        "duration_ms": pipeline_result.scan.duration_ms,
        "status": pipeline_result.scan.status,
        "tier_counts": pipeline_result.tier_counts,
        "media_counts": pipeline_result.media_counts,
        "enriched": pipeline_result.enriched,
        "error": pipeline_result.error,
        "report": pipeline_result.report,
    }
    return summary, full
