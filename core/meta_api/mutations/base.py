# -*- coding: utf-8 -*-
"""Базовый контракт для Meta API mutation handlers.

Каждый handler — отдельный класс, реализующий Protocol MutationHandler.
Handler знает один mutation_kind и валидирует свой набор params.

Общие соглашения:
- target_id передаётся из MetaMutationPayload.target_id (ad_id/adset_id/campaign_id).
- Все числовые значения для Graph API сериализуются как строки (требование Marketing API).
- Доменные ошибки (TokenInvalid, RateLimited, NotFound, ...) пробрасываются как есть —
  worker маршрутизирует их в retry vs final fail (см. apps/meta_api_worker/main.py).
- Валидация payload бросает ValueError; worker превращает её в final mark_failed.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.meta_api.client import MetaApiClient
from core.meta_api.schemas import MetaMutationPayload


@runtime_checkable
class MutationHandler(Protocol):
    """Контракт одного handler'а.

    Реализация должна:
    1. Иметь class-level атрибут mutation_kind: str (ключ из MUTATION_KINDS).
    2. В execute(...) — валидировать payload.params, бросать ValueError на bad input.
    3. Вызвать client.execute_graph_call(...) с правильным method/endpoint/params.
    4. Вернуть dict вида {"success": True, "graph_response": ..., "modified_ids": [...]}.
    5. Доменные ошибки Meta пробрасывать as-is (они доходят до worker'а).
    """

    mutation_kind: str

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]: ...


# ====================== общие helpers ======================


def require_numeric_id(value: str, field_name: str) -> str:
    """Проверить что строка — числовой ID Graph API (только цифры).

    Marketing API ID — целые числа в виде строк (например, "23847238472384").
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"{field_name}: ожидается строка с числовым ID, получено {value!r}")
    if not value.isdigit():
        raise ValueError(f"{field_name}: ожидается только из цифр, получено {value!r}")
    return value


def require_status(value: Any, *, field_name: str = "status") -> str:
    """Нормализовать status к Graph API формату: PAUSED или ACTIVE."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name}: ожидается строка, получено {type(value).__name__}")
    normalized = value.strip().upper()
    if normalized not in ("PAUSED", "ACTIVE"):
        raise ValueError(f"{field_name}: допустимо PAUSED или ACTIVE, получено {value!r}")
    return normalized


def success_result(
    *,
    graph_response: dict[str, Any],
    modified_ids: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Стандартная success-форма для handler'ов.

    modified_ids — список изменённых сущностей (для bulk может быть >1).
    """
    out: dict[str, Any] = {
        "success": True,
        "graph_response": graph_response,
        "modified_ids": list(modified_ids),
    }
    if extra:
        out.update(extra)
    return out
