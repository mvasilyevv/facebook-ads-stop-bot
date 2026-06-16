# -*- coding: utf-8 -*-
"""Unit: детерминированный детектор AI-паттернов в тексте (de-AI)."""

from __future__ import annotations

from core.syntx.ai_tells import detect_ai_tells

# Кусок из реального AI-style PWA-листинга (с типичными штампами).
_AI_HEAVY = (
    "Chicken Road is not just another game; it's Ghana's #1 destination. "
    "Whether you're relaxing or chasing a thrill, feel the thrill of the chase. "
    "Have you got the nerves of steel to wait — or will you play it safe? "
    "Don't just hear about other guys winning — be the one cashing out. "
    "Say goodbye to lag. The clock is ticking."
)
_HUMAN = (
    "I played for ten minutes on my MTN line in Accra. Lost the first two rounds, "
    "then cashed 240 cedis. Network was fine. Withdrawal hit MoMo same evening."
)


# AI-насыщенный текст → высокий score + узнаваемые тэллы.
def test_detect_high_on_ai_text() -> None:
    rep = detect_ai_tells(_AI_HEAVY)
    assert rep.score >= 50
    names = {h.name for h in rep.hits}
    assert "not_just_its" in names
    assert "whether_youre" in names
    assert "nerves_of_steel" in names


# Живой человеческий текст → низкий score.
def test_detect_low_on_human_text() -> None:
    rep = detect_ai_tells(_HUMAN)
    assert rep.score < 20


# Пустой текст → 0, без падения.
def test_detect_empty() -> None:
    rep = detect_ai_tells("")
    assert rep.score == 0 and rep.word_count == 0


# top отдаёт тэллы по убыванию вклада.
def test_top_ordering() -> None:
    rep = detect_ai_tells(_AI_HEAVY)
    assert rep.top  # непусто
    assert rep.top[0] in {h.name for h in rep.hits}


# em-dash и эмодзи плотность считаются и поднимают score.
def test_density_signals() -> None:
    rep = detect_ai_tells("Win big — cash out — instant — 🔥🔥🔥 play now 💸💸💸")
    assert rep.em_dash_per_100w > 0
    assert rep.emoji_per_100w > 0
