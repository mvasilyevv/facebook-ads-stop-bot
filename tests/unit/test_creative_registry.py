# -*- coding: utf-8 -*-
"""Тесты Creative Registry + петли creative_report."""

from __future__ import annotations

from core.creatives.registry import (
    Creative,
    Geo,
    Hook,
    Registry,
    Slot,
    load_registry,
)
from scripts.creative_report import _build_report, _norm


# Реальный реестр (docs/creatives/*.yaml) грузится и проходит валидацию без ошибок
def test_real_registry_loads_and_validates() -> None:
    reg = load_registry()
    assert reg.hooks, "хуки должны загрузиться"
    assert "KE" in reg.geos
    assert reg.find_creative("KE_CR2_CR005") is not None
    assert reg.validate() == []


# Валидатор ловит ссылку креатива на несуществующий хук
def test_validate_catches_missing_hook_ref() -> None:
    reg = Registry(
        hooks={
            "real_hook": Hook(
                id="real_hook", level="visual", text="x", type="format", verdict="works"
            )
        },
        geos={
            "KE": Geo(
                code="KE",
                name="Kenya",
                slots={
                    "CR2": Slot(
                        code="CR2",
                        geo="KE",
                        offer_code="KE_CR2",
                        name="CR2",
                        mechanic="crash",
                        creatives=(
                            Creative(
                                code="KE_CR2_CRX",
                                format="static",
                                status="draft",
                                verdict="testing",
                                visual_hooks=("ghost_hook",),  # несуществующий
                            ),
                        ),
                    )
                },
            )
        },
    )
    errors = reg.validate()
    assert any("ghost_hook" in e for e in errors)


# Валидатор ловит дубль creative.code внутри слота
def test_validate_catches_duplicate_code() -> None:
    dup = Creative(code="KE_CR2_DUP", format="static", status="draft", verdict="testing")
    reg = Registry(
        hooks={},
        geos={
            "KE": Geo(
                code="KE",
                name="Kenya",
                slots={
                    "CR2": Slot(
                        code="CR2",
                        geo="KE",
                        offer_code="KE_CR2",
                        name="CR2",
                        mechanic="m",
                        creatives=(dup, dup),
                    )
                },
            )
        },
    )
    assert any("дубль" in e.lower() for e in reg.validate())


# Creative.all_hook_ids объединяет visual+text без дублей
def test_all_hook_ids_dedup() -> None:
    cr = Creative(
        code="C",
        format="static",
        status="live",
        verdict="winner",
        visual_hooks=("a", "b", "a"),
        text_hook="b",
    )
    assert cr.all_hook_ids() == ("a", "b")


# Нормализация схлопывает двойной URL-энкодинг макроса
def test_norm_collapses_double_encoding() -> None:
    assert _norm("Payment+Trust+%2F+M-Pesa") == _norm("Payment Trust / M-Pesa")
    assert _norm("M-Pesa%2Bangle") == _norm("M-Pesa+angle")


# Петля: один креатив с sub6 в двух кодировках → один угол, депозиты суммируются
def test_report_collapses_encoding_duplicates() -> None:
    reg = load_registry()
    deposits = [
        {
            "sub3": "KE_CR2_CR005",
            "sub6": "1 | Payment Trust / M-Pesa",
            "deposits": 2,
            "revenue": 16.0,
        },
        {
            "sub3": "KE_CR2_CR005",
            "sub6": "1 | Payment+Trust+%2F+M-Pesa",
            "deposits": 1,
            "revenue": 8.0,
        },
    ]
    report = _build_report(reg, deposits, 30)
    # Угол один (схлопнут), депозитов 3
    assert report.count("payment trust / m-pesa") == 1
    assert "| 3 |" in report  # суммарные депозиты угла/креатива


# Петля: депозиты креатива раскидываются по всем его хукам
def test_report_distributes_deposits_to_hooks() -> None:
    reg = load_registry()
    deposits = [
        {
            "sub3": "KE_CR2_CR005",
            "sub6": "1 | Payment Trust / M-Pesa",
            "deposits": 5,
            "revenue": 40.0,
        },
    ]
    report = _build_report(reg, deposits, 30)
    # CR005 несёт vis_mpesa_green_proof → хук должен получить те же 5 депозитов
    assert "vis_mpesa_green_proof" in report
    assert "vis_native_fb_post" in report


# Без депозитов отчёт уходит в библиотечный режим
def test_report_library_mode_when_no_deposits() -> None:
    reg = load_registry()
    report = _build_report(reg, [], 30)
    assert "Библиотека" in report
    assert "Хуков:" in report


# Депозит с code вне реестра помечается предупреждением
def test_report_flags_unknown_code() -> None:
    reg = load_registry()
    deposits = [{"sub3": "KE_CR2_NOPE", "sub6": "x", "deposits": 2, "revenue": 0.0}]
    report = _build_report(reg, deposits, 30)
    assert "вне реестра" in report
