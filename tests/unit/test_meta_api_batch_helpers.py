# -*- coding: utf-8 -*-
"""Unit-тесты helpers для Graph API Batch.

Покрываем:
- encode_batch_body: list/dict сериализуются в JSON, bool → true/false.
- make_batch_entry: валидация relative_url (no leading /, no host), name validator.
- build_batch_payload: пустой/переполненный → ValueError.
- parse_batch_response: разные формы ответа, null элементы, не-2xx коды.
- jsonpath_ref: формат строки.
"""

from __future__ import annotations

import json

import pytest

from core.meta_api.mutations._batch_helpers import (
    MAX_BATCH_ENTRIES,
    build_batch_payload,
    encode_batch_body,
    jsonpath_ref,
    make_batch_entry,
    parse_batch_response,
    validate_relative_url,
)


# encode_batch_body: bool сериализуется как true/false.
def test_encode_batch_body_bool_to_lower() -> None:
    body = encode_batch_body({"flag": True, "other": False})
    parts = body.split("&")
    assert "flag=true" in parts
    assert "other=false" in parts


# encode_batch_body: list/dict → JSON.
def test_encode_batch_body_serializes_complex_types() -> None:
    body = encode_batch_body({"arr": [1, 2, 3], "obj": {"a": 1}})
    from urllib.parse import parse_qs

    parsed = parse_qs(body)
    assert json.loads(parsed["arr"][0]) == [1, 2, 3]
    assert json.loads(parsed["obj"][0]) == {"a": 1}


# encode_batch_body: None-значения пропускаются.
def test_encode_batch_body_skips_none() -> None:
    body = encode_batch_body({"a": 1, "b": None, "c": "ok"})
    assert "b=" not in body
    assert "a=1" in body and "c=ok" in body


# encode_batch_body: спецсимволы экранируются.
def test_encode_batch_body_escapes_special_chars() -> None:
    body = encode_batch_body({"name": "a b&c=d"})
    from urllib.parse import parse_qs

    parsed = parse_qs(body)
    assert parsed["name"] == ["a b&c=d"]


# make_batch_entry: minimal POST с body.
def test_make_batch_entry_minimal_post() -> None:
    entry = make_batch_entry(
        method="POST",
        relative_url="act_123/campaigns",
        body_params={"name": "X"},
        name="campaign",
    )
    assert entry["method"] == "POST"
    assert entry["relative_url"] == "act_123/campaigns"
    assert entry["name"] == "campaign"
    assert "name=X" in entry["body"]


# make_batch_entry: GET без body тоже валидно.
def test_make_batch_entry_get_no_body() -> None:
    entry = make_batch_entry(method="GET", relative_url="me")
    assert entry["method"] == "GET"
    assert "body" not in entry


# make_batch_entry: пустой method отвергается.
def test_make_batch_entry_rejects_empty_method() -> None:
    with pytest.raises(ValueError, match="method"):
        make_batch_entry(method="", relative_url="me")


# make_batch_entry: unsupported method отвергается.
def test_make_batch_entry_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="method"):
        make_batch_entry(method="PATCH", relative_url="me")


# make_batch_entry: пустой relative_url отвергается.
def test_make_batch_entry_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="relative_url"):
        make_batch_entry(method="POST", relative_url="")


# make_batch_entry: leading / отвергается.
def test_make_batch_entry_rejects_leading_slash() -> None:
    with pytest.raises(ValueError, match="не должен начинаться"):
        make_batch_entry(method="POST", relative_url="/campaigns")


# make_batch_entry: абсолютный URL отвергается.
def test_make_batch_entry_rejects_absolute_url() -> None:
    with pytest.raises(ValueError, match="относительным"):
        make_batch_entry(method="POST", relative_url="https://graph.facebook.com/me")


# make_batch_entry: name с недопустимыми символами отвергается.
def test_make_batch_entry_rejects_invalid_name() -> None:
    with pytest.raises(ValueError, match="batch entry name"):
        make_batch_entry(
            method="POST",
            relative_url="me",
            name="bad name with space",
        )


# make_batch_entry: omit_response_on_success прокидывается.
def test_make_batch_entry_omit_response_flag() -> None:
    entry = make_batch_entry(
        method="POST",
        relative_url="me",
        omit_response_on_success=True,
    )
    assert entry["omit_response_on_success"] is True


# build_batch_payload: пустой список → ValueError.
def test_build_batch_payload_rejects_empty() -> None:
    with pytest.raises(ValueError, match="пустой"):
        build_batch_payload([])


# build_batch_payload: > MAX_BATCH_ENTRIES → ValueError.
def test_build_batch_payload_rejects_too_many() -> None:
    entries = [
        make_batch_entry(method="POST", relative_url=f"act_X/{i}")
        for i in range(MAX_BATCH_ENTRIES + 1)
    ]
    with pytest.raises(ValueError, match="много"):
        build_batch_payload(entries)


# build_batch_payload: валидный список → корректный JSON.
def test_build_batch_payload_serializes_to_json_array() -> None:
    entries = [
        make_batch_entry(method="POST", relative_url="me/campaigns", name="campaign"),
        make_batch_entry(method="POST", relative_url="me/adsets", name="adset"),
    ]
    payload = build_batch_payload(entries)
    decoded = json.loads(payload)
    assert isinstance(decoded, list)
    assert len(decoded) == 2
    assert decoded[0]["name"] == "campaign"


# parse_batch_response: list-форма ответа → normalized list.
def test_parse_batch_response_list_form() -> None:
    raw = [
        {"code": 200, "body": json.dumps({"id": "111"})},
        {"code": 200, "body": json.dumps({"id": "222"})},
    ]
    results = parse_batch_response(raw)
    assert len(results) == 2
    assert all(r["success"] for r in results)
    assert results[0]["body"] == {"id": "111"}
    assert results[0]["index"] == 0
    assert results[1]["index"] == 1


# parse_batch_response: dict {data: [...]} тоже принимается.
def test_parse_batch_response_dict_data_form() -> None:
    raw = {"data": [{"code": 200, "body": '{"id":"333"}'}]}
    results = parse_batch_response(raw)
    assert len(results) == 1
    assert results[0]["body"] == {"id": "333"}


# parse_batch_response: null-элемент → success=False.
def test_parse_batch_response_null_item() -> None:
    raw = [
        {"code": 200, "body": '{"id":"111"}'},
        None,
    ]
    results = parse_batch_response(raw)
    assert results[1]["success"] is False
    assert "missing_sub_result" in results[1]["error"]


# parse_batch_response: 4xx с error.message → извлекаем сообщение.
def test_parse_batch_response_extracts_error_message() -> None:
    raw = [
        {
            "code": 400,
            "body": json.dumps({"error": {"message": "Bad request: missing name"}}),
        }
    ]
    results = parse_batch_response(raw)
    assert results[0]["success"] is False
    assert results[0]["code"] == 400
    assert "Bad request" in results[0]["error"]


# parse_batch_response: unexpected shape → all failed заглушка.
def test_parse_batch_response_unexpected_shape() -> None:
    results = parse_batch_response("not-a-list", expected_count=2)
    assert len(results) == 2
    assert all(not r["success"] for r in results)
    assert all("unexpected" in r["error"] for r in results)


# jsonpath_ref: формат строки.
def test_jsonpath_ref_format() -> None:
    assert jsonpath_ref("campaign") == "{result=campaign:$.id}"
    assert jsonpath_ref("adset", "$.id") == "{result=adset:$.id}"


# jsonpath_ref: невалидное name → ValueError.
def test_jsonpath_ref_validates_name() -> None:
    with pytest.raises(ValueError, match="jsonpath_ref"):
        jsonpath_ref("bad name!")


# validate_relative_url: путь с JSONPath не отвергается.
def test_validate_relative_url_allows_jsonpath() -> None:
    # Не бросает.
    validate_relative_url("{result=campaign:$.id}/copies")
    validate_relative_url("act_123/campaigns")


# validate_relative_url: невалидный тип → ValueError.
def test_validate_relative_url_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="должен быть str"):
        validate_relative_url(123)  # type: ignore[arg-type]
