"""PoC v3 — Ad Library через browser-agent gRPC с целевым подбором keywords.

Запуск:
    PYTHONPATH=. .venv/bin/python scripts/ad_library_poc.py

Что изменилось:
- Multi-query поиск: для каждого GEO прогоняем 3-5 локализованных keyword'ов
- Дедупликация по ad_archive_id между запросами одного GEO
- Heuristic-классификатор отсеивает не-gambling выдачу (DramaBox, Lovely Books и т.п.)
- Считаем чистоту: сколько из всех ads — реальный gambling

Тест-кейс: 6 GEO (KE/CD/MZ/GH/TR/IT) × локализованные gambling keywords.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from clients.python_grpc.ad_library_client import AdLibraryClient
from scripts.ad_library_keywords import GEO_KEYWORDS, is_gambling_ad

TIMEOUT_MS = 30_000  # На каждый query в batch'е — на успешных ~5-15s
LOG_LEVEL = logging.INFO


@dataclass
class CaseResult:
    """Итог одного GEO."""

    country: str
    description: str
    queries_used: list[str] = field(default_factory=list)
    total_raw_ads: int = 0
    unique_ads: int = 0
    gambling_ads: int = 0
    elapsed_seconds: float = 0.0
    ads_with_text: int = 0
    ads_with_media: int = 0
    ads_with_video: int = 0
    ads_long_running: int = 0
    ads_very_long_running: int = 0
    top_gambling_pages: list[tuple[str, int]] = field(default_factory=list)
    sample_long_runner: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


GEO_DESCRIPTIONS = {
    "KE": "Кения",
    # Остальные GEO отключены — валидируем LLM-фильтрацию на одном GEO + одном keyword.
}


def _get_creative_text(ad: dict[str, Any]) -> str:
    """Извлечь текст рекламы из raw Ad Library dict."""
    snapshot = ad.get("snapshot") or {}
    parts: list[str] = []
    for key in ("body", "creative_body", "title", "link_description", "caption"):
        val = snapshot.get(key) or ad.get(key)
        if isinstance(val, dict):
            val = val.get("text") or val.get("markup", {}).get("__html") or ""
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    # Также текст в additional bodies (карусели)
    bodies = snapshot.get("bodies") or []
    if isinstance(bodies, list):
        for b in bodies:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
    return " | ".join(parts)


def _has_media(ad: dict[str, Any]) -> tuple[bool, bool]:
    snapshot = ad.get("snapshot") or {}
    has_image = bool(
        snapshot.get("images") or snapshot.get("creative_image_url") or snapshot.get("image_url")
    )
    videos = snapshot.get("videos") or []
    has_video = bool(videos) or bool(snapshot.get("video_hd_url") or snapshot.get("video_sd_url"))
    return (has_image or has_video, has_video)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str):
            if value.isdigit():
                return datetime.fromtimestamp(float(value), tz=UTC)
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    return None


def _days_running(ad: dict[str, Any]) -> int:
    start_raw = (
        ad.get("start_date")
        or ad.get("startDate")
        or ad.get("ad_delivery_start_time")
        or ad.get("delivery_start_time")
    )
    end_raw = (
        ad.get("end_date")
        or ad.get("endDate")
        or ad.get("ad_delivery_stop_time")
        or ad.get("delivery_stop_time")
    )
    start = _parse_ts(start_raw)
    if start is None:
        return 0
    end = _parse_ts(end_raw) or datetime.now(UTC)
    return max(0, (end - start).days)


def _get_page_name(ad: dict[str, Any]) -> str:
    page = ad.get("page_name") or ad.get("pageName")
    if page:
        return str(page)
    snapshot = ad.get("snapshot") or {}
    return str(snapshot.get("page_name") or "(нет page)")


async def run_geo(client: AdLibraryClient, country: str, description: str) -> CaseResult:
    """Прогон всех keyword'ов для одного GEO через batch RPC (одна страница, DOM input)."""
    print(f"\n{'=' * 70}")
    print(f"  {country} | {description}")
    print(f"{'=' * 70}")

    keywords = GEO_KEYWORDS.get(country, [])
    if not keywords:
        print(f"  ❌ Нет keywords для {country}")
        return CaseResult(country=country, description=description)

    start_ts = time.monotonic()
    errors: list[str] = []

    try:
        batch = await client.search_ads_batch(
            country=country,
            queries=keywords,
            active_status="active",
            per_query_timeout_ms=TIMEOUT_MS,
        )
    except Exception as exc:
        errors.append(f"batch: {type(exc).__name__}: {exc}")
        print(f"  ❌ Batch failed: {exc}")
        return CaseResult(country=country, description=description, errors=errors)

    seen_ids: set[str] = set()
    unique_ads: list[dict[str, Any]] = []
    queries_used: list[str] = []

    for qr in batch.results:
        new_count = 0
        for ad in qr.ads:
            ad_id = str(ad.get("ad_archive_id") or ad.get("adArchiveID") or ad.get("id") or "")
            if ad_id and ad_id not in seen_ids:
                seen_ids.add(ad_id)
                unique_ads.append(ad)
                new_count += 1
        if qr.error_type:
            errors.append(f"{qr.query}: {qr.error_type}: {qr.error_message}")
            print(f"  → query='{qr.query}': ❌ {qr.error_type}: {qr.error_message}")
        else:
            queries_used.append(qr.query)
            print(
                f"  → query='{qr.query}': +{new_count} новых "
                f"(всего {qr.ad_count}, {qr.duration_ms}ms)"
            )

    elapsed = time.monotonic() - start_ts

    # Классификация: gambling vs мусор
    gambling_ads: list[dict[str, Any]] = []
    for ad in unique_ads:
        page = _get_page_name(ad)
        text = _get_creative_text(ad)
        is_gambling, _ = is_gambling_ad(page_name=page, creative_text=text)
        if is_gambling:
            gambling_ads.append(ad)

    # Статистика по gambling-only
    ads_with_text = sum(1 for a in gambling_ads if _get_creative_text(a))
    media_flags = [_has_media(a) for a in gambling_ads]
    ads_with_media = sum(1 for has_any, _ in media_flags if has_any)
    ads_with_video = sum(1 for _, has_video in media_flags if has_video)
    days_list = [_days_running(a) for a in gambling_ads]
    ads_long_running = sum(1 for d in days_list if d >= 14)
    ads_very_long_running = sum(1 for d in days_list if d >= 30)

    page_counter: Counter[str] = Counter()
    for ad in gambling_ads:
        page_counter[_get_page_name(ad)] += 1

    sample: dict[str, Any] | None = None
    if days_list:
        sample_idx = max(range(len(days_list)), key=lambda i: days_list[i])
        if days_list[sample_idx] >= 7:
            sample = gambling_ads[sample_idx]

    purity = (len(gambling_ads) / len(unique_ads) * 100) if unique_ads else 0.0
    print(
        f"\n  ИТОГ {country}: {len(unique_ads)} уникальных, "
        f"{len(gambling_ads)} gambling ({purity:.0f}% чистота), "
        f"{elapsed:.0f}s"
    )
    if gambling_ads:
        print(
            f"  С текстом: {ads_with_text} | С медиа: {ads_with_media} | С видео: {ads_with_video}"
        )
        print(f"  Долгожителей: ≥14d = {ads_long_running}, ≥30d = {ads_very_long_running}")
        if page_counter:
            print("  Топ gambling-страниц:")
            for name, cnt in page_counter.most_common(5):
                print(f"    {cnt}x  {name[:55]}")
        if sample:
            sample_days = _days_running(sample)
            sample_page = _get_page_name(sample)
            sample_text = _get_creative_text(sample)
            print(f"\n  📌 Долгожитель ({sample_days} дней, '{sample_page[:50]}'):")
            preview = sample_text[:180] + ("..." if len(sample_text) > 180 else "")
            print(f"     {preview or '(нет текста)'}")

    return CaseResult(
        country=country,
        description=description,
        queries_used=queries_used,
        total_raw_ads=len(unique_ads),  # после межзапросной дедупликации
        unique_ads=len(unique_ads),
        gambling_ads=len(gambling_ads),
        elapsed_seconds=elapsed,
        ads_with_text=ads_with_text,
        ads_with_media=ads_with_media,
        ads_with_video=ads_with_video,
        ads_long_running=ads_long_running,
        ads_very_long_running=ads_very_long_running,
        top_gambling_pages=page_counter.most_common(5),
        sample_long_runner=sample,
        errors=errors,
    )


def print_summary(results: list[CaseResult]) -> None:
    print(f"\n\n{'=' * 96}")
    print("  СВОДКА — gambling ads after filter")
    print(f"{'=' * 96}")
    print(
        f"  {'Гео':<4} {'Страна':<14} {'Кол.запр':<10} {'Уник':<6} {'Gambling':<10} "
        f"{'Чистота':<9} {'≥14д':<6} {'Сек':<6}"
    )
    print(f"  {'-' * 90}")
    for r in results:
        purity = (r.gambling_ads / r.unique_ads * 100) if r.unique_ads else 0.0
        print(
            f"  {r.country:<4} {r.description:<14} "
            f"{len(r.queries_used):<10} {r.unique_ads:<6} {r.gambling_ads:<10} "
            f"{purity:<9.0f} {r.ads_long_running:<6} {r.elapsed_seconds:<6.0f}"
        )

    total_unique = sum(r.unique_ads for r in results)
    total_gambling = sum(r.gambling_ads for r in results)
    total_long = sum(r.ads_long_running for r in results)
    overall_purity = (total_gambling / total_unique * 100) if total_unique else 0.0
    successful_cases = sum(1 for r in results if r.gambling_ads > 0)

    print(f"  {'-' * 90}")
    print(
        f"  ИТОГО: {total_unique} уникальных, {total_gambling} gambling "
        f"({overall_purity:.0f}% чистота), {total_long} долгожителей, "
        f"{successful_cases}/{len(results)} GEO с gambling-выдачей"
    )

    if overall_purity >= 60 and successful_cases >= 4:
        print("\n  ✅ PoC v3 прошёл — gambling-выдача чистая.")
        print("     → готовы к полной интеграции core/ad_library/")
    elif total_gambling >= 20:
        print("\n  ⚠️  Чистота низкая или мало GEO работают.")
        print("     → пересмотреть keywords для проблемных GEO")
    else:
        print("\n  ❌ Слишком мало gambling-выдачи.")


async def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    print(f"\n{'=' * 72}")
    print("  Ad Library PoC v3 — multi-query + gambling filter")
    print(f"  Время старта: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  GEO: {list(GEO_DESCRIPTIONS.keys())}")
    print(f"{'=' * 72}")

    async with AdLibraryClient(grpc_host="localhost", grpc_port=50051) as client:
        try:
            health = await client.check_health()
            print(f"\n  Health-check: {health.detail}")
            if not health.healthy:
                print("  ❌ Ad Library канал нездоров.")
                return
        except Exception as exc:
            print(f"\n  ❌ Не удалось подключиться: {exc}")
            return

        results: list[CaseResult] = []
        for country, description in GEO_DESCRIPTIONS.items():
            try:
                result = await run_geo(client, country, description)
            except Exception as exc:
                print(f"  💥 Критическая ошибка для {country}: {exc}")
                result = CaseResult(
                    country=country,
                    description=description,
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            results.append(result)

        print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
