# -*- coding: utf-8 -*-
"""Регрессия CRIT #1: encode_batch_body НЕ url-кодирует JSONPath refs.

Meta Batch API распознаёт `{result=name:$.path}` только если ref пришёл в raw
виде. Если он закодирован как `%7Bresult%3D...%7D`, Meta теряет связь между
batch entries и возвращает error 100 ("create_campaign физически не работал").

Покрываем три сценария:
1. plain string value со скаляром-ref
2. dict-value с вложенным ref (через json.dumps)
3. нормальные form-разделители (&, +, space) кодируются как положено
"""

from __future__ import annotations

from urllib.parse import parse_qs

from core.meta_api.mutations._batch_helpers import encode_batch_body


# Scalar string-ref остаётся как есть — без %7B/%7D/%3D/%3A/%24.
def test_encode_batch_body_keeps_scalar_ref_unencoded() -> None:
    body = encode_batch_body({"campaign_id": "{result=campaign:$.id}"})
    assert body == "campaign_id={result=campaign:$.id}"
    # Дополнительная страховка — никаких percent-encoded скобок/двоеточий.
    assert "%7B" not in body
    assert "%7D" not in body
    assert "%3D" not in body
    assert "%3A" not in body
    assert "%24" not in body


# Ref внутри JSON-dict тоже сохраняется (Meta парсит refs из raw body).
def test_encode_batch_body_keeps_nested_ref_in_json_dict() -> None:
    body = encode_batch_body(
        {"creative": {"creative_id": "{result=creative:$.id}", "image_hash": "abc"}}
    )
    parsed = parse_qs(body)
    raw_creative = parsed["creative"][0]
    # Подстрока с ref должна быть в raw value, без percent-encoding.
    assert "{result=creative:$.id}" in raw_creative
    assert "%7Bresult" not in body
    # Полная JSON-структура должна валидно парситься обратно.
    import json

    obj = json.loads(raw_creative)
    assert obj == {"creative_id": "{result=creative:$.id}", "image_hash": "abc"}


# Form-разделители обычных значений всё же кодируются (& → %26, space → +).
def test_encode_batch_body_encodes_form_separators_in_normal_values() -> None:
    body = encode_batch_body({"name": "Test & Co", "amount": "100"})
    parsed = parse_qs(body)
    assert parsed["name"] == ["Test & Co"]
    assert parsed["amount"] == ["100"]
    # & внутри value должен быть %26, иначе будет восприниматься как разделитель.
    assert "%26" in body
    # space должен стать +.
    assert "Test+%26+Co" in body


# Литерал процента и + кодируются (%2B, %25) — иначе сломается обратный decode.
def test_encode_batch_body_encodes_percent_and_plus() -> None:
    body = encode_batch_body({"raw": "50%+bonus"})
    parsed = parse_qs(body)
    assert parsed["raw"] == ["50%+bonus"]
    assert "%25" in body  # литерал %
    assert "%2B" in body  # литерал +


# Не-ASCII (UTF-8) кодируется побайтно.
def test_encode_batch_body_encodes_unicode_bytes() -> None:
    body = encode_batch_body({"text": "тест"})
    parsed = parse_qs(body)
    assert parsed["text"] == ["тест"]
    assert "%D1%82" in body  # 'т' в UTF-8 — 0xD1 0x82


# Ref внутри JSON-list тоже не ломается.
def test_encode_batch_body_keeps_ref_in_json_list() -> None:
    body = encode_batch_body({"items": [{"id": "{result=x:$.id}"}, {"id": "raw_id"}]})
    parsed = parse_qs(body)
    raw_items = parsed["items"][0]
    assert "{result=x:$.id}" in raw_items
    assert "%7B" not in raw_items
