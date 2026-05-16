# -*- coding: utf-8 -*-
"""markdown_report делит селекторы на стабильные и нестабильные.

Цель: разработчик, глядя в отчёт recorder, видит, какие селекторы можно нести
в шаг (placeholder, aria-label, role+name, text=), а какие — только подсказка
(xpath, data-auto-logging-id, обфусцированные классы FB).
"""

from __future__ import annotations

from core.campaign_recorder.analyzer import UserAction
from core.campaign_recorder.markdown_report import _is_unstable, build_markdown


# Чёрный список — xpath, data-auto-logging-id, обфусцированные классы.
def test_is_unstable_flags_xpath_and_alids():
    assert _is_unstable("xpath=/html/body/div[1]")
    assert _is_unstable('[data-auto-logging-id="abc123"]')
    assert _is_unstable("div.x1abcdef.x9zxc1ws")


# Белый список — placeholder, aria-label, role+name, text=.
def test_is_unstable_keeps_human_selectors():
    assert not _is_unstable('[placeholder="Поиск местоположений"]')
    assert not _is_unstable('[aria-label="Заголовок"]')
    assert not _is_unstable('role=button[name="Создать"]')
    assert not _is_unstable('text="Сайт"')


# Markdown отделяет блоки «Стабильные» и «Нестабильные» в одном шаге.
def test_build_markdown_splits_selectors():
    action = UserAction(
        kind="click",
        selectors=(
            '[aria-label="Создать"]',
            'role=button[name="Создать"]',
            "xpath=/html/body/div",
            '[data-auto-logging-id="x"]',
        ),
        value=None,
        label="Создать",
        section=None,
        ts=1.0,
        raw_indices=(0,),
    )
    md = build_markdown({"offer_code": "X", "events": []}, [action])
    assert "Селекторы (стабильные):" in md
    assert "Селекторы (нестабильные" in md
    # Стабильные идут раньше нестабильных.
    assert md.index("Селекторы (стабильные)") < md.index("Селекторы (нестабильные")


# Если все селекторы стабильные — блок «Нестабильные» не выводится.
def test_no_unstable_block_when_all_stable():
    action = UserAction(
        kind="click",
        selectors=('[aria-label="X"]',),
        value=None,
        label="X",
        section=None,
        ts=0.0,
        raw_indices=(0,),
    )
    md = build_markdown({"offer_code": "Y", "events": []}, [action])
    assert "Селекторы (стабильные):" in md
    assert "Нестабильные" not in md
