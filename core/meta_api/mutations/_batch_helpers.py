# -*- coding: utf-8 -*-
"""Helpers для Graph API Batch — построение payload и парсинг ответа.

Batch API в Marketing API позволяет одним HTTP-запросом сделать несколько
суб-запросов, причём сабжи могут ссылаться друг на друга через JSONPath:
    "{result=campaign:$.id}"  — где "campaign" — это значение поля "name"
    из batch entry, имеющего {"name":"campaign", ...}.

Контракт sub-request:
    {
        "method": "POST",
        "relative_url": "act_X/campaigns",
        "body": "name=...&objective=...",        # form-encoded, как query string
        "name": "campaign",                       # имя для cross-reference (опционально)
    }

Документация: https://developers.facebook.com/docs/graph-api/batch-requests
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus

# Максимум sub-requests за один Batch API вызов.
MAX_BATCH_ENTRIES = 50


def encode_batch_body(params: dict[str, Any]) -> str:
    """Закодировать params как form-encoded строку для batch entry.body.

    Значения-list/dict сериализуются в JSON, иначе передаются как строка.
    Используем quote_plus чтобы экранировать спецсимволы.
    """
    encoded_parts: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            value_str = json.dumps(value)
        elif isinstance(value, bool):
            value_str = "true" if value else "false"
        else:
            value_str = str(value)
        encoded_parts.append(f"{quote_plus(key)}={quote_plus(value_str)}")
    return "&".join(encoded_parts)


def make_batch_entry(
    *,
    method: str,
    relative_url: str,
    body_params: dict[str, Any] | None = None,
    name: str | None = None,
    omit_response_on_success: bool = False,
) -> dict[str, Any]:
    """Построить одну запись batch.

    Args:
        method: HTTP-метод ("POST", "GET", "DELETE").
        relative_url: путь без host и API version. Может содержать JSONPath-ссылки
            вроде "{result=campaign:$.id}/copies" — Meta их разрешит.
        body_params: параметры для form-encoded body (используется при POST).
        name: имя для cross-reference из других entries.
        omit_response_on_success: не возвращать body саб-ответа если 2xx
            (экономит размер ответа на больших batch).

    Returns:
        dict со структурой batch entry.
    """
    if not method or not isinstance(method, str):
        raise ValueError(f"method обязателен и должен быть str, получено {method!r}")
    method_up = method.upper()
    if method_up not in ("GET", "POST", "DELETE", "PUT"):
        raise ValueError(f"method: допустимо GET/POST/DELETE/PUT, получено {method!r}")
    if not relative_url:
        raise ValueError("relative_url не должен быть пустым")
    validate_relative_url(relative_url)

    entry: dict[str, Any] = {
        "method": method_up,
        "relative_url": relative_url,
    }
    if body_params:
        entry["body"] = encode_batch_body(body_params)
    if name:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"batch entry name: разрешены [A-Za-z0-9_-], получено {name!r}")
        entry["name"] = name
    if omit_response_on_success:
        entry["omit_response_on_success"] = True
    return entry


def validate_relative_url(url: str) -> None:
    """Проверить relative_url batch entry.

    Запрещаем:
    - абсолютные URL (https://, http://)
    - ведущий /  (Meta хочет относительный путь от graph.facebook.com/vXX/)
    """
    if not isinstance(url, str):
        raise ValueError(f"relative_url должен быть str, получено {type(url).__name__}")
    if url.startswith(("http://", "https://", "//")):
        raise ValueError(f"relative_url должен быть относительным (без host), получено {url!r}")
    if url.startswith("/"):
        raise ValueError(f"relative_url не должен начинаться с /, получено {url!r}")


def build_batch_payload(entries: list[dict[str, Any]]) -> str:
    """Сериализовать список batch entries в JSON-строку для query param 'batch'.

    Бросает ValueError если entries пуст или > MAX_BATCH_ENTRIES.
    """
    if not entries:
        raise ValueError("entries пустой — Batch API требует минимум один sub-request")
    if len(entries) > MAX_BATCH_ENTRIES:
        raise ValueError(f"entries слишком много ({len(entries)} > {MAX_BATCH_ENTRIES})")
    return json.dumps(entries)


def parse_batch_response(
    response: Any,
    *,
    expected_count: int | None = None,
) -> list[dict[str, Any]]:
    """Распарсить ответ Batch API в список нормализованных sub-results.

    Meta возвращает массив объектов: [{code, body, headers}, ...] в том же
    порядке, что и batch entries. Иногда элемент = null (timeout/skipped).

    Возвращает list[{index, success, code, body, error?}]. Никогда не бросает.
    """
    items: list[Any]
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict) and isinstance(response.get("data"), list):
        items = response["data"]
    else:
        # Неожиданная форма — отметим как все_failed.
        count = expected_count or 1
        return [
            {
                "index": i,
                "success": False,
                "code": 0,
                "body": None,
                "error": "unexpected_batch_response_shape",
            }
            for i in range(count)
        ]

    results: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            results.append(
                {
                    "index": idx,
                    "success": False,
                    "code": 0,
                    "body": None,
                    "error": "missing_sub_result",
                }
            )
            continue
        code = int(item.get("code") or 0)
        body_raw = item.get("body")
        body_parsed: Any = None
        if isinstance(body_raw, str) and body_raw:
            try:
                body_parsed = json.loads(body_raw)
            except json.JSONDecodeError:
                body_parsed = {"raw": body_raw}
        elif isinstance(body_raw, dict):
            body_parsed = body_raw

        success = 200 <= code < 300
        entry: dict[str, Any] = {
            "index": idx,
            "success": success,
            "code": code,
            "body": body_parsed,
        }
        if not success:
            err_field = body_parsed.get("error") if isinstance(body_parsed, dict) else None
            if isinstance(err_field, dict):
                entry["error"] = err_field.get("message") or err_field.get("type") or "graph_error"
            else:
                entry["error"] = "non_2xx"
        results.append(entry)
    return results


def jsonpath_ref(name: str, path: str = "$.id") -> str:
    """Сахар: построить JSONPath-ссылку на результат другого entry.

    Пример:
        jsonpath_ref("campaign", "$.id") → "{result=campaign:$.id}"
    """
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"jsonpath_ref name: разрешены [A-Za-z0-9_-], получено {name!r}")
    return f"{{result={name}:{path}}}"
