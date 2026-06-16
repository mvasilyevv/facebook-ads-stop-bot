# -*- coding: utf-8 -*-
"""Детерминированный детектор AI-паттернов в тексте (de-AI).

Зачем не отдавать это LLM: модели плохо ловят СВОЙ стиль (слепое пятно) — они сами
так пишут. Регекс-каталог штампов работает стабильно, быстро и бесплатно, независимо
от настроения модели. Используется как объективное дополнение к LLM-вердиктам по
тексту листинга / отзывов гемблинг-PWA.

`detect_ai_tells(text) → AiTellReport` (score 0-100 + список сработавших тэллов с
примерами). Высокий score = «пахнет нейронкой», нужно переписать живее.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Каталог тэллов: (имя, regex, вес). Подобрано под EN маркетинг/PWA-копи и отзывы.
# Вес ~ насколько сильно конструкция выдаёт LLM. flags=I|M везде.
_TELLS: tuple[tuple[str, str, int], ...] = (
    ("not_just_its", r"\bnot just (?:a|an|another)\b[^.!?]{0,60}?\bit'?s\b", 14),
    ("isnt_just", r"\b(?:it'?s|this is)n'?t just\b", 12),
    ("whether_youre", r"\bwhether you'?re\b", 9),
    ("rhetorical_or", r"\bare you [^.?!]{1,45}? or [^.?!]{1,45}?\?", 9),
    ("what_youre_made_of", r"\bwhat you'?re made of\b", 9),
    ("dont_just_be_the_one", r"\bdon'?t just\b[^.!?]{0,50}\bbe the one\b", 10),
    ("the_thrill_of", r"\bthe thrill of\b", 6),
    ("nerves_of_steel", r"\bnerves of steel\b", 6),
    ("it_only_takes", r"\bit only takes\b", 6),
    ("in_seconds", r"\bin seconds\b", 4),
    ("say_goodbye", r"\bsay goodbye to\b", 6),
    ("look_no_further", r"\blook no further\b", 8),
    ("clock_is_ticking", r"\bthe clock is ticking\b", 7),
    (
        "ai_verbs",
        r"\b(?:elevate|unleash|embark|dive in|supercharge|seamless|effortless|game-?changer)\b",
        5,
    ),
    ("superlative_100", r"\b100% (?:real|secure|legit|safe|seamless)\b", 5),
    ("hash_one", r"#1\b|\bnumber one\b", 4),
    # Перфектный листикл с Title-Case ярлыком: "1) Lightning-Fast Instant Payouts:"
    ("titlecase_benefit_label", r"(?m)^\s*\d+\)\s+(?:[A-Z][a-z]+[ &-]*){2,}:", 8),
    # Отзывы: штампы соц-пруфа
    ("review_money_maker", r"\bmoney[- ]?maker\b", 5),
    ("review_highly_recommend", r"\bhighly recommend(?:ed)?\b", 4),
    ("review_literally_instant", r"\bliterally instant\b", 5),
    ("review_payouts_real", r"\bpayouts are (?:100% )?real\b", 5),
)


@dataclass(slots=True, frozen=True)
class TellHit:
    """Один сработавший паттерн."""

    name: str
    weight: int
    count: int
    examples: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class AiTellReport:
    """Сводка по AI-паттернам текста."""

    score: int  # 0-100, выше = больше похоже на нейронку
    word_count: int
    em_dash_per_100w: float
    emoji_per_100w: float
    hits: tuple[TellHit, ...] = field(default_factory=tuple)

    @property
    def top(self) -> tuple[str, ...]:
        """Имена тэллов по убыванию вклада (вес×count)."""
        return tuple(
            h.name for h in sorted(self.hits, key=lambda h: h.weight * h.count, reverse=True)
        )


# Основные эмодзи-планы + флаги + ⭐/✅/❤. НЕ берём диапазон 2600-27BF целиком —
# туда попадают markdown-звёзды рейтинга ★ (U+2605), это давало ложные срабатывания.
_EMOJI = re.compile("[\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff⭐✅❌❤]")


def detect_ai_tells(text: str) -> AiTellReport:
    """Прогнать текст по каталогу AI-тэллов → score + детали.

    score = нормированная сумма (вес×count) по тэллам + плотностные надбавки за
    em-dash и эмодзи (типовые LLM/хайп-маркеры), приведённая к 0-100.
    """
    if not text or not text.strip():
        return AiTellReport(score=0, word_count=0, em_dash_per_100w=0.0, emoji_per_100w=0.0)

    words = max(1, len(re.findall(r"\b\w+\b", text)))
    hits: list[TellHit] = []
    weighted = 0
    for name, pattern, weight in _TELLS:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if not matches:
            continue
        count = len(matches)
        examples = tuple(
            (m if isinstance(m, str) else " ".join(x for x in m if x))[:50] for m in matches[:3]
        )
        hits.append(TellHit(name=name, weight=weight, count=count, examples=examples))
        weighted += weight * count

    em_dash = text.count("—") + text.count(" - ")
    em_dash_density = em_dash / words * 100
    emoji = len(_EMOJI.findall(text))
    emoji_density = emoji / words * 100
    # плотностные надбавки (em-dash > 1.5/100w и эмодзи > 3/100w — типичные маркеры)
    density_bonus = max(0.0, em_dash_density - 1.5) * 4 + max(0.0, emoji_density - 3.0) * 2

    score = int(min(100, weighted * 2.2 + density_bonus))
    return AiTellReport(
        score=score,
        word_count=words,
        em_dash_per_100w=round(em_dash_density, 2),
        emoji_per_100w=round(emoji_density, 2),
        hits=tuple(hits),
    )
