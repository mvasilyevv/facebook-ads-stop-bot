# -*- coding: utf-8 -*-
"""bulk_status_change — пакетное PAUSE/ACTIVE до 50 объектов одним вызовом.

Graph API Batch endpoint: POST /v22.0/ (root) с параметром
    batch=[{"method":"POST","relative_url":"{id}?status=PAUSED"}, ...]

Все sub-requests параллельно с одним токеном за один HTTP-запрос. Каждый
sub-request возвращает свой `code` (HTTP status) и `body` (JSON).

Контракт params (поддерживаются обе формы для совместимости):
1. Полная: {"object_ids": [...], "status": "PAUSED"|"ACTIVE", "object_type": "ad"|"adset"|"campaign"}
2. Сокращённая (от drafts/): {"ad_ids": [...], "action": "pause"|"activate"}

Пример (полная):
    MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="23847001",  # любой из object_ids
        params={
            "object_ids": ["23847001", "23847002", "23847003"],
            "status": "PAUSED",
            "object_type": "ad",
        },
        ad_account_id="act_123",
    )

Пример (от drafts):
    MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:3",
        params={"ad_ids": ["23847001", "23847002"], "action": "pause"},
        ad_account_id="act_123",
    )

Ограничения Graph Batch API:
- Максимум 50 sub-requests за один вызов.
- Каждый sub-request имеет таймаут как и обычный API call.
- Failures отдельных sub-requests не валят весь batch.

ВНИМАНИЕ — корреляция object_type ↔ object_ids НЕ проверяется!
    object_type указывается caller'ом и попадает только в audit + extra поля,
    но handler НЕ выполняет pre-flight `GET /<id>?fields=id` чтобы убедиться,
    что id'шки реально принадлежат типу. Если caller передал
    object_type='campaign' со списком ad-id, Meta попытается выключить
    кампании по этим id (а это уже совершенно другие сущности) — последствия
    необратимы.

    Caller обязан гарантировать корреляцию. Для AI-tools (например,
    request_bulk_pause) это значит сначала SELECT из локальной БД
    (`fb_ads`/`fb_campaigns`) или явный list_ads/list_campaigns, и только
    потом — bulk_status_change.

    Полный pre-flight check через Graph API — отдельная фича.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MetaApiError,
    MutationValidationError,
    TemporaryError,
    classify_graph_error,
)
from core.meta_api.mutations._batch_helpers import (
    MAX_BATCH_ENTRIES,
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

_ALLOWED_OBJECT_TYPES = frozenset({"ad", "adset", "campaign"})

# Маппинг сокращённого action из drafts → Graph API status.
_ACTION_TO_STATUS = {
    "pause": "PAUSED",
    "activate": "ACTIVE",
    "paused": "PAUSED",
    "active": "ACTIVE",
}


class BulkStatusChangeHandler:
    mutation_kind: ClassVar[str] = "bulk_status_change"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        params = payload.params or {}
        object_ids, status, object_type = self._extract_params(params)

        # Логируем явно — критично для аудита: видим связку type↔count↔status,
        # помогает отследить ошибки caller'а (несовпадение type с id'шками).
        logger.info(
            "bulk_status_change: type=%s count=%d status=%s first_id=%s",
            object_type,
            len(object_ids),
            status,
            object_ids[0] if object_ids else "?",
        )

        # Каждый sub-request: POST к {id}?status=PAUSED|ACTIVE. M6: используем общий
        # _batch_helpers (build_batch_payload + parse_batch_response) вместо ручного
        # json.dumps и копии парсера — единый формат batch со всеми мутациями.
        entries = [
            make_batch_entry(method="POST", relative_url=f"{oid}?status={status}")
            for oid in object_ids
        ]

        graph_response = await client.execute_graph_call(
            ad_account_id=payload.ad_account_id,
            method="POST",
            endpoint="/",
            query_params={"batch": build_batch_payload(entries)},
        )

        # Привязываем object_id к каждому sub-result по индексу (Batch сохраняет порядок).
        parsed = parse_batch_response(graph_response, expected_count=len(object_ids))
        sub_results = [
            {
                "id": object_ids[r["index"]] if r["index"] < len(object_ids) else None,
                "success": r["success"],
                "code": r["code"],
                **({"error": r["error"]} if r.get("error") else {}),
            }
            for r in parsed
        ]

        # M-1 (аудит 2026-07-12): полный отказ батча с ТРАНЗИЕНТНЫМИ саб-ошибками
        # (rate-limit, null-таймаут, сеть) раньше сворачивался в result-dict →
        # is_mutation_success=False → mark_failed БЕЗ retry: автостарт кабинета /
        # bulk-pause тихо не доисполнялись. Статус-изменение идемпотентно (повторный
        # PAUSED/ACTIVE безопасен) → бросаем Temporary, воркер сделает requeue с
        # backoff. Частичный успех и permanent-провалы идут прежним путём
        # (succeeded>0 → success + DM; все permanent → mark_failed + DM).
        # Классифицируем по parsed (там есть body с Graph-кодами; sub_results
        # body не несут — они уходят в task_queue.result компактными).
        failed_parsed = [r for r in parsed if not r["success"]]
        if failed_parsed and len(failed_parsed) == len(parsed):
            classified = [self._classify_sub_failure(r) for r in failed_parsed]
            if all(isinstance(exc, TemporaryError) for exc in classified):
                logger.warning(
                    "bulk_status_change: все %d саб-реквестов упали транзиентно "
                    "(rate-limit/timeout) — пробрасываем retry вместо mark_failed",
                    len(failed_parsed),
                )
                raise classified[0]

        return success_result(
            graph_response=graph_response,
            modified_ids=[r["id"] for r in sub_results if r["success"]],
            extra={
                "object_type": object_type,
                "status_applied": status,
                "batch_size": len(object_ids),
                "succeeded": sum(1 for r in sub_results if r["success"]),
                "failed": sum(1 for r in sub_results if not r["success"]),
                "sub_results": sub_results,
            },
        )

    @staticmethod
    def _classify_sub_failure(sub: dict[str, Any]) -> MetaApiError:
        """Классифицировать один провалившийся sub-result по Graph-кодам из body.

        null-саб (timeout) и body без error-структуры → code=None → Temporary
        (могла быть сеть) — согласовано с classify_graph_error.
        """
        body = sub.get("body")
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        subcode = err.get("error_subcode") if isinstance(err, dict) else None
        message = (err.get("message") if isinstance(err, dict) else None) or str(
            sub.get("error") or "batch sub-request failed"
        )
        return classify_graph_error(code, subcode, message)

    @staticmethod
    def _extract_params(params: dict[str, Any]) -> tuple[list[str], str, str]:
        """Достать object_ids/status/object_type из payload.params.

        Поддерживает обе формы: полную (object_ids+status+object_type) и
        сокращённую (ad_ids+action). Бросает MutationValidationError на bad input.
        """
        # Полная форма
        if "object_ids" in params or "status" in params:
            raw_ids = params.get("object_ids") or []
            status_raw = params.get("status")
            object_type = (params.get("object_type") or "ad").lower()
            if not isinstance(raw_ids, list) or not raw_ids:
                raise MutationValidationError("object_ids: ожидается непустой список ID")
            if not isinstance(status_raw, str):
                raise MutationValidationError(f"status: ожидается строка, получено {status_raw!r}")
            status = status_raw.strip().upper()
            if status not in ("PAUSED", "ACTIVE"):
                raise MutationValidationError(
                    f"status: допустимо PAUSED или ACTIVE, получено {status_raw!r}"
                )
        # Сокращённая (от drafts)
        elif "ad_ids" in params or "action" in params:
            raw_ids = params.get("ad_ids") or []
            action = str(params.get("action") or "").lower().strip()
            if not isinstance(raw_ids, list) or not raw_ids:
                raise MutationValidationError("ad_ids: ожидается непустой список ID")
            if action not in _ACTION_TO_STATUS:
                raise MutationValidationError(
                    f"action: допустимо pause/activate, получено {params.get('action')!r}"
                )
            status = _ACTION_TO_STATUS[action]
            object_type = "ad"
        else:
            raise MutationValidationError(
                "bulk_status_change: params должен содержать "
                "(object_ids+status+object_type) или (ad_ids+action)"
            )

        if object_type not in _ALLOWED_OBJECT_TYPES:
            raise MutationValidationError(
                f"object_type: допустимо {sorted(_ALLOWED_OBJECT_TYPES)}, получено {object_type!r}"
            )

        object_ids: list[str] = []
        for raw in raw_ids:
            sid = str(raw).strip()
            require_numeric_id(sid, "object_id")
            object_ids.append(sid)

        if len(object_ids) > MAX_BATCH_ENTRIES:
            raise MutationValidationError(
                f"bulk_status_change: слишком много объектов "
                f"({len(object_ids)} > {MAX_BATCH_ENTRIES})"
            )

        # Дедуп с сохранением порядка
        seen: set[str] = set()
        deduped: list[str] = []
        for sid in object_ids:
            if sid not in seen:
                seen.add(sid)
                deduped.append(sid)

        return deduped, status, object_type
