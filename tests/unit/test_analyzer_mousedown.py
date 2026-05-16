# -*- coding: utf-8 -*-
"""Денойзер должен сохранять mousedown-only клики (FB-listbox).

FB закрывает выпадающий listbox на mousedown без последующего click. Если
анализатор требует click, такие действия теряются и в markdown-отчёт не
попадают. Тесты фиксируют новое поведение: mousedown с непустыми селекторами
становится `kind="click"`.
"""

from __future__ import annotations

from core.campaign_recorder.analyzer import denoise


def _ev(idx: int, kind: str, *, xpath: str = "/html/x", text: str = "", ts: float = 0.0) -> dict:
    return {
        "type": kind,
        "xpath": xpath,
        "ts": ts + idx * 0.01,
        "selector_candidates": [f"#opt-{idx}"] if text else [],
        "text": text,
        "aria_label": text or None,
        "label_text": None,
        "nearest_heading": None,
    }


# mousedown-only без click — должен превратиться в click-action.
def test_mousedown_without_click_becomes_action():
    events = [_ev(0, "mousedown", text="Кения")]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "click"
    assert actions[0].label == "Кения"


# pointerdown-only — то же самое поведение.
def test_pointerdown_without_click_becomes_action():
    events = [_ev(0, "pointerdown", text="Антарктика")]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "click"


# Связка mousedown+click по тому же xpath — один action, источник click.
def test_mousedown_then_click_collapsed():
    events = [
        _ev(0, "mousedown", text="Антарктика"),
        _ev(1, "click", text="Антарктика"),
    ]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "click"
    # raw_indices содержит оба события — это нормально (группа).
    assert 0 in actions[0].raw_indices and 1 in actions[0].raw_indices


# mousedown без селекторов и без текста — игнорируется (шум).
def test_empty_mousedown_ignored():
    events = [_ev(0, "mousedown")]  # text="" → selectors пустые
    actions = denoise(events)
    assert actions == []
