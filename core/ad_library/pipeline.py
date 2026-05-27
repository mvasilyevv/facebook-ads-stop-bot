# -*- coding: utf-8 -*-
"""End-to-end pipeline: scan → media → enrich → tier_ranker → report.

Используется TG /spy командой и API endpoint'ом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.enricher import enrich_scan
from core.ad_library.media import DEFAULT_MEDIA_ROOT, download_for_scan
from core.ad_library.report import build_report
from core.ad_library.scanner import ScanResult, run_scan
from core.ad_library.tier_ranker import rank_scan

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Полный результат pipeline."""

    scan: ScanResult
    media_counts: dict[str, int] = field(default_factory=dict)
    enriched: int = 0
    tier_counts: dict[str, int] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


async def run_pipeline(
    engine: AsyncEngine,
    *,
    slot: str,
    country: str,
    triggered_by: str,
    search_type: str = "keyword_unordered",
    max_pages: int = 10,
    skip_media: bool = False,
    media_root: Path = DEFAULT_MEDIA_ROOT,
    grpc_host: str = "localhost",
    grpc_port: int = 50051,
    session_id: str = "",
) -> PipelineResult:
    """End-to-end pipeline по одному запросу пользователя.

    ПРАВИЛО: slot+country переданы дословно. Не подменяем.
    """
    logger.info("Pipeline start: slot=%s country=%s triggered_by=%s", slot, country, triggered_by)

    # 1. Scan
    scan_result = await run_scan(
        engine,
        slot=slot,
        country=country,
        triggered_by=triggered_by,
        search_type=search_type,
        max_pages=max_pages,
        grpc_host=grpc_host,
        grpc_port=grpc_port,
        session_id=session_id,
    )

    if scan_result.status == "failed":
        logger.warning("Scan failed: %s", scan_result.error)
        return PipelineResult(scan=scan_result, error=scan_result.error)

    if scan_result.ads_count == 0:
        # Empty pool — честный empty result, никакого fallback на расширение запроса
        logger.info("Empty pool for slot=%s country=%s — пустой ответ", slot, country)
        return PipelineResult(scan=scan_result)

    pipeline = PipelineResult(scan=scan_result)
    scan_id_str = str(scan_result.scan_id)

    # 2. Media download (опционально — иногда хотим просто данные без файлов)
    if not skip_media:
        try:
            pipeline.media_counts = await download_for_scan(
                engine,
                scan_id=scan_id_str,
                country=country,
                media_root=media_root,
            )
        except Exception as exc:
            logger.exception("Media download failed: %s", exc)
            pipeline.media_counts = {"error": str(exc)}

    # 3. Enrich
    try:
        pipeline.enriched = await enrich_scan(engine, scan_id=scan_id_str)
    except Exception as exc:
        logger.exception("Enrich failed: %s", exc)

    # 4. Tier ranking
    try:
        pipeline.tier_counts = await rank_scan(engine, scan_id=scan_id_str)
    except Exception as exc:
        logger.exception("Tier ranking failed: %s", exc)

    # 5. Report
    try:
        pipeline.report = await build_report(engine, scan_id=scan_id_str)
    except Exception as exc:
        logger.exception("Report build failed: %s", exc)
        pipeline.error = f"report failed: {exc}"

    logger.info(
        "Pipeline done: scan=%s ads=%d tiers=%s",
        scan_id_str,
        scan_result.ads_count,
        pipeline.tier_counts,
    )
    return pipeline
