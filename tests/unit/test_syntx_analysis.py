# -*- coding: utf-8 -*-
"""Unit: мульти-модельный анализ — сборка промпта + парсинг JSON-ответа."""

from __future__ import annotations

from core.syntx.analysis import (
    DEFAULT_ANALYSIS_POOL,
    AnalysisResult,
    build_analysis_prompt,
    build_text_analysis_prompt,
    parse_analysis_json,
)


# build_text_analysis_prompt: gambling-freedom + EVERGREEN-правило + контент + JSON.
def test_build_text_analysis_prompt() -> None:
    p = build_text_analysis_prompt(
        kind="listing description",
        offer="Chicken Road",
        geo="Ghana",
        content="Make your deposit today and take our Generous Deposit Bonuses!",
    )
    assert "GAMBLING FREEDOM" in p
    assert "EVERGREEN" in p
    assert "Generous Deposit Bonuses" in p
    assert '"verdict"' in p


# build_analysis_prompt вшивает gambling-freedom, гео, роль, листинг и JSON-контракт.
def test_build_analysis_prompt_contains_key_parts() -> None:
    p = build_analysis_prompt(
        offer="Chicken Road",
        geo="Ghana",
        image_role="app icon",
        listing_text="Place your bet, cash out before the crash.",
        archetype="InOut",
    )
    assert "GAMBLING FREEDOM" in p
    assert "Ghana" in p
    assert "app icon" in p
    assert "Chicken Road" in p
    assert "Place your bet" in p
    assert '"verdict"' in p and '"fix_instructions"' in p


# Листинг обрезается до listing_limit (не раздуваем промпт).
def test_build_analysis_prompt_truncates_listing() -> None:
    p = build_analysis_prompt(
        offer="X", geo="GH", image_role="icon", listing_text="A" * 5000, listing_limit=100
    )
    assert "A" * 100 in p
    assert "A" * 101 not in p


# parse_analysis_json: чистый JSON.
def test_parse_plain_json() -> None:
    out = parse_analysis_json('{"verdict":"keep","score":9}')
    assert out["verdict"] == "keep" and out["score"] == 9


# parse_analysis_json: JSON в ```json-обёртке (модель добавила fence вопреки инструкции).
def test_parse_fenced_json() -> None:
    raw = 'Here is my analysis:\n```json\n{"verdict":"minor_fix","score":7}\n```\nDone.'
    out = parse_analysis_json(raw)
    assert out["verdict"] == "minor_fix" and out["score"] == 7


# parse_analysis_json: JSON с прозой вокруг (берём первый сбалансированный блок).
def test_parse_json_with_prose() -> None:
    raw = 'Sure! {"verdict":"regenerate","score":3,"issues":[]} hope this helps'
    out = parse_analysis_json(raw)
    assert out["verdict"] == "regenerate"


# parse_analysis_json: мусор → пустой dict, без исключения.
def test_parse_garbage() -> None:
    assert parse_analysis_json("no json here") == {}
    assert parse_analysis_json("") == {}


# Дефолтный пул — 3 разные лаборатории (OpenAI/Google/xAI).
def test_default_pool_three_labs() -> None:
    ai_names = {ai for ai, _, _ in DEFAULT_ANALYSIS_POOL}
    assert ai_names == {"chatgpt", "gemini", "grok"}
    assert len(DEFAULT_ANALYSIS_POOL) == 3


# AnalysisResult.verdict/score читаются из parsed; error даёт verdict='error'.
def test_analysis_result_props() -> None:
    ok = AnalysisResult("GPT", "chatgpt", "gpt-5.5", parsed={"verdict": "keep", "score": 8})
    assert ok.verdict == "keep" and ok.score == 8
    bad = AnalysisResult("GPT", "chatgpt", "gpt-5.5", error="boom")
    assert bad.verdict == "error"
