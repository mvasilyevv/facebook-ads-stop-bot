# -*- coding: utf-8 -*-
"""AI enricher per-ad: hook, cta, tone, claims.

MVP: использует существующий ai_assistant клиент. Сейчас — детерминированная заглушка,
которая работает без AI (для тестов и offline-режима). Реальная AI-обвязка добавляется
позже путём замены analyze_one_ad() на вызов claude/openai.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.classifier import extract_ad_text

logger = logging.getLogger(__name__)


# Эмодзи и фразы намекающие на типы hook'ов
_HOOK_PATTERNS = {
    "free_money_hook": [
        r"\bbonus\b",
        r"\bfree\b",
        r"\bwin\b",
        r"\$\d+",
        r"\d+%\s*off",
        r"\b(пода[рп]ок|бонус|бесплатн)",
    ],
    "fear_hook": [
        r"\bdon'?t miss\b",
        r"\blast chance\b",
        r"\bоставшийся\b",
        r"\bтолько сегодня\b",
    ],
    "social_proof_hook": [
        r"\b\d+\s*(million|thousand|million)\b",
        r"\bjoin\s+\d+\b",
        r"\busers?\b",
    ],
    "curiosity_hook": [
        r"\bsecret\b",
        r"\bsurprise\b",
        r"\bunbelievable\b",
        r"\byou won'?t believe\b",
    ],
}

_CTA_PATTERNS = [
    "play now",
    "download",
    "install",
    "join",
    "sign up",
    "register",
    "claim",
    "get bonus",
    "start playing",
    "играть",
    "скачать",
    "получить",
]

_TONE_KEYWORDS = {
    "aggressive": ["NOW", "INSTANT", "URGENT", "!!!"],
    "casual": ["hey", "hi", "let's"],
    "professional": ["proven", "trusted", "reliable", "official"],
    "exciting": ["amazing", "incredible", "wow"],
}


def analyze_one_ad(raw_ad: dict[str, Any]) -> dict[str, Any]:
    """Pure-функция — анализ одного ad. Возвращает структуру под AdLibraryAd.ai_summary."""
    text_content = extract_ad_text(raw_ad)
    low = text_content.lower()

    detected_hooks: list[str] = []
    for hook, patterns in _HOOK_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, low, re.IGNORECASE):
                detected_hooks.append(hook)
                break

    detected_ctas: list[str] = [c for c in _CTA_PATTERNS if c in low]

    tone_scores: dict[str, int] = {}
    for tone, words in _TONE_KEYWORDS.items():
        tone_scores[tone] = sum(1 for w in words if w.lower() in low)
    best_tone = (
        max(tone_scores, key=lambda k: tone_scores[k]) if any(tone_scores.values()) else None
    )

    # Claims: цифры с %, $, и т.д. — простой extractor
    claims: list[str] = re.findall(
        r"\$?\d+(?:\.\d+)?\s*(?:%|million|thousand|free|bonus)?", text_content[:500]
    )

    return {
        "hooks": detected_hooks,
        "ctas": detected_ctas,
        "tone": best_tone,
        "claims_sample": claims[:5],
        "text_length": len(text_content),
        "enricher_version": "heuristic_v1",
    }


async def enrich_scan(
    engine: AsyncEngine,
    *,
    scan_id: str,
) -> int:
    """Прогоняет enricher по всем ads scan'а, обновляет ad_library_ad.ai_summary.

    Returns: количество обновлённых ads.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT a.ad_archive_id, s.raw_json
                    FROM ad_library_snapshot s
                    JOIN ad_library_ad a ON a.ad_archive_id = s.ad_archive_id
                    WHERE s.scan_id = :sid
                    """
                ),
                {"sid": scan_id},
            )
        ).all()

    enriched = 0
    async with engine.begin() as conn:
        for ad_archive_id, raw_json in rows:
            try:
                summary = analyze_one_ad(raw_json or {})
            except Exception as exc:
                logger.warning("Enricher failed for ad %d: %s", ad_archive_id, exc)
                continue
            await conn.execute(
                text(
                    "UPDATE ad_library_ad SET ai_summary = CAST(:s AS JSONB) "
                    "WHERE ad_archive_id = :aid"
                ),
                {"aid": ad_archive_id, "s": json.dumps(summary)},
            )
            enriched += 1

    logger.info("Enriched %d ads for scan %s", enriched, scan_id)
    return enriched
