# -*- coding: utf-8 -*-
"""Тесты генерации markdown-отчёта для UserAction."""

from core.campaign_recorder.analyzer import UserAction
from core.campaign_recorder.markdown_report import build_markdown


def test_build_markdown_contains_steps_and_selectors():
    """Отчёт должен содержать заголовок, шаги, селекторы и значения."""
    session = {
        "offer_code": "KE_CR2",
        "started_at": "2026-05-13T14:22:00",
        "events": [{"ts": 1.0}, {"ts": 135.0}],
        "path": "recordings/20260513_142200_KE_CR2.json",
    }
    actions = [
        UserAction(
            kind="click",
            selectors=(
                'role=button[name="Conversion Location"]',
                '[aria-label="Conversion Location"]',
            ),
            value=None,
            label="Conversion Location",
            section="Where do you want to drive traffic?",
            ts=1.0,
            raw_indices=(0,),
        ),
        UserAction(
            kind="fill",
            selectors=('[aria-label="Website URL"]',),
            value="https://example.com",
            label="Website URL",
            section=None,
            ts=10.0,
            raw_indices=(1,),
        ),
    ]
    md = build_markdown(session, actions)
    assert "KE_CR2" in md
    assert "2 действ" in md
    assert "## Шаг 1 — click" in md
    assert "## Шаг 2 — fill" in md
    assert "Conversion Location" in md
    assert 'role=button[name="Conversion Location"]' in md
    assert "https://example.com" in md
    assert "Where do you want to drive traffic?" in md
