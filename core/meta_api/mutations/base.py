# -*- coding: utf-8 -*-
"""Базовый контракт для Meta API mutation handlers.

Каждый handler — отдельный класс, реализующий Protocol MutationHandler.
Handler знает один mutation_kind и валидирует свой набор params.

Общие соглашения:
- target_id передаётся из MetaMutationPayload.target_id (ad_id/adset_id/campaign_id).
- Все числовые значения для Graph API сериализуются как строки (требование Marketing API).
- Доменные ошибки (TokenInvalid, RateLimited, NotFound, ...) пробрасываются как есть —
  worker маршрутизирует их в retry vs final fail (см. apps/meta_api_worker/main.py).
- Валидация payload бросает MutationValidationError (subclass ValueError);
  worker маршрутизирует её в permanent mark_failed (retry бесполезен).
  Голый ValueError НЕ должен появляться в handler'ах — он будет трактоваться как
  unexpected баг и уйдёт в requeue.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    AmbiguousResultError,
    MutationValidationError,  # noqa: F401 — экспортируем для handlers
    PermanentError,
)
from core.meta_api.schemas import MetaMutationPayload


@runtime_checkable
class MutationHandler(Protocol):
    """Контракт одного handler'а.

    Реализация должна:
    1. Иметь class-level атрибут mutation_kind: str (ключ из MUTATION_KINDS).
    2. В execute(...) — валидировать payload.params, бросать MutationValidationError на bad input.
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

MutationEvidence = Literal["confirmed", "rejected", "unknown"]


def classify_meta_mutation_evidence(response: Any) -> MutationEvidence:
    """Classify the only acknowledgement that proves a Meta mutation.

    This pure classifier is shared by single-call and Graph Batch mutations so
    an HTTP/gRPC success can never be promoted to a confirmed business outcome
    without the literal JSON boolean ``{"success": true}``.
    """
    if not isinstance(response, dict):
        return "unknown"
    acknowledged = response.get("success")
    if acknowledged is True:
        return "confirmed"
    if acknowledged is False:
        return "rejected"
    return "unknown"


def require_numeric_id(value: str, field_name: str) -> str:
    """Проверить что строка — числовой ID Graph API (только цифры).

    Marketing API ID — целые числа в виде строк (например, "23847238472384").
    """
    if not value or not isinstance(value, str):
        raise MutationValidationError(
            f"{field_name}: ожидается строка с числовым ID, получено {value!r}"
        )
    if not value.isdigit():
        raise MutationValidationError(f"{field_name}: ожидается только из цифр, получено {value!r}")
    return value


def require_status(value: Any, *, field_name: str = "status") -> str:
    """Нормализовать status к Graph API формату: PAUSED или ACTIVE."""
    if not isinstance(value, str):
        raise MutationValidationError(
            f"{field_name}: ожидается строка, получено {type(value).__name__}"
        )
    normalized = value.strip().upper()
    if normalized not in ("PAUSED", "ACTIVE"):
        raise MutationValidationError(
            f"{field_name}: допустимо PAUSED или ACTIVE, получено {value!r}"
        )
    return normalized


def require_meta_success_ack(
    graph_response: Any,
    *,
    endpoint: str,
) -> dict[str, Any]:
    """Require Meta's exact boolean acknowledgement for a Graph mutation.

    A transport-level HTTP 2xx is not proof that a money command was applied.
    Meta acknowledges these accepted writes with the literal JSON
    boolean ``{"success": true}``; additional response fields remain valid.

    ``success=false`` is an explicit rejection and is therefore terminal. A
    missing, non-boolean or structurally invalid acknowledgement is ambiguous:
    the write may have committed even though the response cannot prove it. The
    worker routes ``AmbiguousResultError`` through kind-specific reconciliation
    or terminal ``UNKNOWN`` instead of blindly sending the money action again.
    """
    evidence = classify_meta_mutation_evidence(graph_response)
    if evidence == "confirmed":
        return graph_response
    if evidence == "rejected":
        raise PermanentError(
            "Meta explicitly rejected mutation (success=false)",
            endpoint=endpoint,
        )
    if not isinstance(graph_response, dict):
        raise AmbiguousResultError(
            "Meta mutation returned a non-object acknowledgement",
            endpoint=endpoint,
        )
    raise AmbiguousResultError(
        "Meta mutation returned no exact success=true acknowledgement",
        endpoint=endpoint,
    )


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
