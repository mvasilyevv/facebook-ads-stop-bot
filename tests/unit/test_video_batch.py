# -*- coding: utf-8 -*-
"""Тесты моста video_batch: parse_amount / build_props / select_creatives (без node и рендера).

video_batch.py — скрипт (не пакет), грузим по пути через importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from core.creatives.registry import Creative, Geo, Slot

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "video_batch_mod", REPO / "scripts" / "video_batch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _geo() -> Geo:
    return Geo(
        code="KE", name="Kenya", languages=("en", "sw"), payment={"min_deposit_local": "153 KES"}
    )


def _creative(
    code: str = "KE_CR2_CR013", fmt: str = "video", angle: str = "Proof Post Brian"
) -> Creative:
    return Creative(code=code, format=fmt, status="draft", verdict="testing", angle=angle)


# parse_amount: число + валюта
def test_parse_amount_number_currency():
    assert _load().parse_amount("153 KES") == ("153", "KES")


# parse_amount: валюта + число (порядок как в GH)
def test_parse_amount_currency_number():
    assert _load().parse_amount("GHS 10") == ("10", "GHS")


# parse_amount: одиночное значение и пустое
def test_parse_amount_single_and_empty():
    mod = _load()
    assert mod.parse_amount("1000") == ("1000", "")
    assert mod.parse_amount("") == ("", "")


# build_props собирает корректные пропсы из записи реестра
def test_build_props_from_registry():
    mod = _load()
    p = mod.build_props(_creative(), _geo(), cta_text="PLAY", cta_start_sec=3.0)
    assert p["code"] == "KE_CR2_CR013"
    assert p["geo"] == "KE"
    assert p["lang"] == "en"  # первый язык гео
    assert p["offer"] == {"amount": "153", "currency": "KES"}
    assert p["hook"]["text"] == "Proof Post Brian"  # болванка из angle
    assert p["cta"] == {"text": "PLAY", "startSec": 3.0}
    assert p["bg"]["type"] == "solid"  # render-batch заменит на --bg


# --hook перекрывает angle
def test_build_props_hook_override():
    mod = _load()
    p = mod.build_props(
        _creative(), _geo(), cta_text="GO", cta_start_sec=2, hook_text="Custom hook"
    )
    assert p["hook"]["text"] == "Custom hook"


# пустой offer, если в гео нет min_deposit_local
def test_build_props_no_payment():
    mod = _load()
    geo = Geo(code="GH", name="Ghana", languages=("en",), payment={})
    p = mod.build_props(_creative(code="GH_AVI_V1"), geo, cta_text="PLAY", cta_start_sec=3)
    assert p["offer"]["amount"] == "0" and p["offer"]["currency"] == ""
    assert p["lang"] == "en"


def _slot_with(*creatives) -> Slot:
    return Slot(
        code="CR2", geo="KE", offer_code="KE_CR2", name="CR2", mechanic="", creatives=creatives
    )


# select_creatives по --codes берёт указанные (любой формат)
def test_select_by_codes():
    mod = _load()
    slot = _slot_with(_creative("A", fmt="static"), _creative("B", fmt="video"))
    assert [c.code for c in mod.select_creatives(slot, ["A"])] == ["A"]


# select_creatives без codes берёт только format==video
def test_select_video_only():
    mod = _load()
    slot = _slot_with(_creative("A", fmt="static"), _creative("B", fmt="video"))
    assert [c.code for c in mod.select_creatives(slot, None)] == ["B"]
