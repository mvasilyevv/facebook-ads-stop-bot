# -*- coding: utf-8 -*-
"""Helpers for the canonical independent Graph status batch."""

from __future__ import annotations

import json
from typing import Any, Literal

from core.meta_api.mutations.base import classify_meta_mutation_evidence

# Максимум sub-requests за один Batch API вызов.
MAX_BATCH_ENTRIES = 50


def make_batch_entry(
    *,
    method: str,
    relative_url: str,
) -> dict[str, Any]:
    """Build one independent Graph batch entry."""
    if not method or not isinstance(method, str):
        raise ValueError(f"method обязателен и должен быть str, получено {method!r}")
    method_up = method.upper()
    if method_up not in ("GET", "POST", "DELETE", "PUT"):
        raise ValueError(f"method: допустимо GET/POST/DELETE/PUT, получено {method!r}")
    if not relative_url:
        raise ValueError("relative_url не должен быть пустым")
    validate_relative_url(relative_url)

    return {
        "method": method_up,
        "relative_url": relative_url,
    }


def validate_relative_url(url: str) -> None:
    """Проверить relative_url batch entry.

    Запрещаем:
    - абсолютные URL (https://, http://)
    - ведущий /  (Meta хочет относительный путь от graph.facebook.com/vXX/)
    - cross-entry template syntax (canonical batch entries are independent)
    """
    if not isinstance(url, str):
        raise ValueError(f"relative_url должен быть str, получено {type(url).__name__}")
    if url.startswith(("http://", "https://", "//")):
        raise ValueError(f"relative_url должен быть относительным (без host), получено {url!r}")
    if url.startswith("/"):
        raise ValueError(f"relative_url не должен начинаться с /, получено {url!r}")
    if any(char in url for char in "{}$"):
        raise ValueError("relative_url must not contain cross-entry templates")


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
    success_evidence: Literal["transport", "mutation_ack"] = "transport",
) -> list[dict[str, Any]]:
    """Распарсить ответ Batch API в список нормализованных sub-results.

    Meta возвращает массив объектов: [{code, body, headers}, ...] в том же
    порядке, что и batch entries. Иногда элемент = null (timeout/skipped).

    ``transport`` подходит для read-only batch: HTTP 2xx достаточно для чтения.
    ``mutation_ack`` требует тот же exact ``success=true`` evidence contract,
    что и одиночная mutation. Возвращает
    list[{index, success, code, body, mutation_evidence?, error?}].
    Никогда не бросает.
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

    if expected_count is not None and len(items) > expected_count:
        return [
            {
                "index": index,
                "success": False,
                "code": 0,
                "body": None,
                "error": "unexpected_batch_response_count",
            }
            for index in range(expected_count)
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
                    "error": "null_response",
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

        transport_success = 200 <= code < 300
        mutation_evidence = (
            classify_meta_mutation_evidence(body_parsed)
            if success_evidence == "mutation_ack" and transport_success
            else None
        )
        success = transport_success and (
            success_evidence == "transport" or mutation_evidence == "confirmed"
        )
        entry: dict[str, Any] = {
            "index": idx,
            "success": success,
            "code": code,
            "body": body_parsed,
        }
        if mutation_evidence is not None:
            entry["mutation_evidence"] = mutation_evidence
        if not success:
            if transport_success and mutation_evidence == "rejected":
                entry["error"] = "mutation_rejected"
            elif transport_success and mutation_evidence == "unknown":
                entry["error"] = "ambiguous_mutation_ack"
            else:
                err_field = body_parsed.get("error") if isinstance(body_parsed, dict) else None
                if isinstance(err_field, dict):
                    entry["error"] = (
                        err_field.get("message") or err_field.get("type") or "graph_error"
                    )
                else:
                    entry["error"] = "non_2xx"
        results.append(entry)

    if expected_count is not None and len(results) < expected_count:
        results.extend(
            {
                "index": index,
                "success": False,
                "code": 0,
                "body": None,
                "error": "missing_response",
            }
            for index in range(len(results), expected_count)
        )
    return results


def classify_sub_failure(sub: dict[str, Any]):
    """Классифицировать провалившийся sub-result по Graph-кодам из body.

    Достаёт error.code/error_subcode из bulk_status_change response и прогоняет
    через classify_graph_error. null-саб (timeout) и body без error-структуры
    дают code=None → TemporaryError (могла быть сеть).
    """
    from core.meta_api.errors import classify_graph_error

    body = sub.get("body")
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    subcode = err.get("error_subcode") if isinstance(err, dict) else None
    message = (err.get("message") if isinstance(err, dict) else None) or str(
        sub.get("error") or "batch sub-request failed"
    )
    return classify_graph_error(code, subcode, message)
