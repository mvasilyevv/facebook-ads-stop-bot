# -*- coding: utf-8 -*-
"""Heuristic-классификатор ads по вертикалям + score relevance к запрашиваемому slot.

Простой keyword-based scorer. Может быть расширен AI-классификатором (enricher).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Базовые keyword'ы по вертикалям
_VERTICAL_KEYWORDS: dict[str, list[str]] = {
    "gambling": [
        "casino",
        "bet",
        "slot",
        "spin",
        "win",
        "jackpot",
        "bonus",
        "deposit",
        "withdraw",
        "play",
        "gambling",
        "wager",
        "aviator",
        "plinko",
        "chicken",
        "crash",
        "lucky",
        "betting",
        "wager",
    ],
    "nutra": [
        "weight loss",
        "diet",
        "slim",
        "fat burn",
        "keto",
        "supplement",
        "vitamin",
        "detox",
        "skin care",
    ],
    "finance": [
        "loan",
        "credit",
        "invest",
        "trading",
        "forex",
        "crypto",
        "bitcoin",
        "binance",
        "stock",
    ],
}


@dataclass
class ClassificationResult:
    """Результат классификации одного ad."""

    vertical: str | None
    score: float  # 0..1 — насколько ad соответствует запрашиваемому slot
    matched_terms: list[str]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def detect_vertical(page_name: str, ad_text: str = "") -> str | None:
    """Простой keyword-based detector вертикали.

    Returns None если ничего явно не подходит.
    """
    combined = _normalize(f"{page_name} {ad_text}")
    best_vertical = None
    best_hits = 0
    for vertical, kw_list in _VERTICAL_KEYWORDS.items():
        hits = sum(1 for kw in kw_list if kw in combined)
        if hits > best_hits:
            best_vertical = vertical
            best_hits = hits
    return best_vertical


def score_relevance_to_slot(slot: str, ad_text: str, page_name: str = "") -> ClassificationResult:
    """Оценивает насколько ad релевантен запрашиваемому slot.

    Возвращает score 0..1 — простой term-overlap метрика.
    Намеренно не использует fuzzy matching: пользователь сказал «не подменяй keyword» —
    точный match приоритетнее.
    """
    slot_terms = [t for t in _normalize(slot).split() if len(t) >= 2]
    if not slot_terms:
        return ClassificationResult(vertical=None, score=0.0, matched_terms=[])

    combined = _normalize(f"{page_name} {ad_text}")
    matched = [t for t in slot_terms if t in combined]
    score = len(matched) / max(1, len(slot_terms))
    vertical = detect_vertical(page_name, ad_text)
    return ClassificationResult(vertical=vertical, score=score, matched_terms=matched)


def extract_ad_text(raw_ad: dict) -> str:
    """Достаёт текстовое содержимое из raw GraphQL ad-структуры.

    Meta GraphQL имеет несколько мест где может лежать body text:
    snapshot.body, snapshot.title, snapshot.cards[].body, snapshot.link_description, ...
    Безопасно собираем всё что нашли.
    """
    parts: list[str] = []
    snap = raw_ad.get("snapshot") or {}

    for k in ("title", "body", "caption", "link_description", "page_name"):
        v = snap.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            # Meta иногда оборачивает text в {"text": "..."}
            inner = v.get("text")
            if isinstance(inner, str):
                parts.append(inner)

    for card in snap.get("cards") or []:
        if isinstance(card, dict):
            for k in ("title", "body", "link_description"):
                v = card.get(k)
                if isinstance(v, str):
                    parts.append(v)

    return " ".join(parts)
