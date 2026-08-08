# -*- coding: utf-8 -*-
"""bulk_status_change — пакетное PAUSE/ACTIVE до 50 объектов одним вызовом.

Graph API Batch endpoint: POST /v22.0/ (root) с параметром
    batch=[{"method":"POST","relative_url":"{id}?status=PAUSED"}, ...]

Все sub-requests параллельно с одним токеном за один HTTP-запрос. Каждый
sub-request возвращает свой `code` (HTTP status) и `body` (JSON).

Единственный контракт params: {"ad_ids": [...], "action": "pause"|"activate"}.

Пример:
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

Каждый id предварительно проходит owner/catalog guard как Meta ad id.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.errors import AmbiguousResultError, MutationValidationError, TemporaryError
from core.meta_api.mutations._batch_helpers import (
    MAX_BATCH_ENTRIES,
    build_batch_payload,
    classify_sub_failure,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

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
        object_ids, status = self._extract_params(params)

        # Логируем явно — критично для аудита: видим связку type↔count↔status,
        # помогает отследить ошибки caller'а (несовпадение type с id'шками).
        logger.info(
            "bulk_status_change: type=%s count=%d status=%s first_id=%s",
            "ad",
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
        parsed = parse_batch_response(
            graph_response,
            expected_count=len(object_ids),
            success_evidence="mutation_ack",
        )
        ambiguous_ids = [
            object_ids[row["index"]]
            for row in parsed
            if row.get("mutation_evidence") == "unknown" and row["index"] < len(object_ids)
        ]
        if ambiguous_ids:
            # Do not publish partial/confirmed from transport-only 2xx evidence.
            # The worker reads actual configured status for every target before
            # deciding whether any idempotent status write may be retried.
            raise AmbiguousResultError(
                "Meta batch mutation returned no exact success=true acknowledgement "
                f"for ids={','.join(ambiguous_ids)}",
                endpoint="/",
            )
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
        transport_failures = [row for row in failed_parsed if row.get("mutation_evidence") is None]
        if transport_failures and len(transport_failures) == len(parsed):
            classified = [classify_sub_failure(r) for r in transport_failures]
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
                "status_applied": status,
                "batch_size": len(object_ids),
                "succeeded": sum(1 for r in sub_results if r["success"]),
                "failed": sum(1 for r in sub_results if not r["success"]),
                "sub_results": sub_results,
            },
        )

    @staticmethod
    def _extract_params(params: dict[str, Any]) -> tuple[list[str], str]:
        """Validate and return canonical ad ids plus Graph status."""
        raw_ids = params.get("ad_ids") or []
        action = str(params.get("action") or "").lower().strip()
        if not isinstance(raw_ids, list) or not raw_ids:
            raise MutationValidationError("ad_ids: ожидается непустой список ID")
        if action not in _ACTION_TO_STATUS:
            raise MutationValidationError(
                f"action: допустимо pause/activate, получено {params.get('action')!r}"
            )
        status = _ACTION_TO_STATUS[action]

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

        return deduped, status
