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

import json
import logging
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 50

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

        # Каждый sub-request: POST к {id} с status=PAUSED|ACTIVE.
        # relative_url требует form-encoded, поэтому status кладём как query.
        batch_items = [
            {
                "method": "POST",
                "relative_url": f"{oid}?status={status}",
            }
            for oid in object_ids
        ]

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint="/",
            query_params={"batch": json.dumps(batch_items)},
        )

        # Распарсить результаты sub-requests: какие успешно, какие нет.
        # Batch API возвращает массив объектов с полями code/body/headers.
        sub_results = self._parse_batch_response(graph_response, object_ids)

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
    def _extract_params(params: dict[str, Any]) -> tuple[list[str], str, str]:
        """Достать object_ids/status/object_type из payload.params.

        Поддерживает обе формы: полную (object_ids+status+object_type) и
        сокращённую (ad_ids+action). Бросает ValueError на bad input.
        """
        # Полная форма
        if "object_ids" in params or "status" in params:
            raw_ids = params.get("object_ids") or []
            status_raw = params.get("status")
            object_type = (params.get("object_type") or "ad").lower()
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("object_ids: ожидается непустой список ID")
            if not isinstance(status_raw, str):
                raise ValueError(f"status: ожидается строка, получено {status_raw!r}")
            status = status_raw.strip().upper()
            if status not in ("PAUSED", "ACTIVE"):
                raise ValueError(f"status: допустимо PAUSED или ACTIVE, получено {status_raw!r}")
        # Сокращённая (от drafts)
        elif "ad_ids" in params or "action" in params:
            raw_ids = params.get("ad_ids") or []
            action = str(params.get("action") or "").lower().strip()
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("ad_ids: ожидается непустой список ID")
            if action not in _ACTION_TO_STATUS:
                raise ValueError(
                    f"action: допустимо pause/activate, получено {params.get('action')!r}"
                )
            status = _ACTION_TO_STATUS[action]
            object_type = "ad"
        else:
            raise ValueError(
                "bulk_status_change: params должен содержать "
                "(object_ids+status+object_type) или (ad_ids+action)"
            )

        if object_type not in _ALLOWED_OBJECT_TYPES:
            raise ValueError(
                f"object_type: допустимо {sorted(_ALLOWED_OBJECT_TYPES)}, получено {object_type!r}"
            )

        object_ids: list[str] = []
        for raw in raw_ids:
            sid = str(raw).strip()
            require_numeric_id(sid, "object_id")
            object_ids.append(sid)

        if len(object_ids) > _MAX_BATCH_SIZE:
            raise ValueError(
                f"bulk_status_change: слишком много объектов "
                f"({len(object_ids)} > {_MAX_BATCH_SIZE})"
            )

        # Дедуп с сохранением порядка
        seen: set[str] = set()
        deduped: list[str] = []
        for sid in object_ids:
            if sid not in seen:
                seen.add(sid)
                deduped.append(sid)

        return deduped, status, object_type

    @staticmethod
    def _parse_batch_response(
        graph_response: Any,
        object_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Парсинг ответа Batch API в список {id, success, code, error?}.

        Meta возвращает массив объектов в том же порядке, что и batch items.
        Поля каждого элемента: code (HTTP status), body (JSON-строка), headers.
        Иногда элемент = null если был timeout/skipped.
        """
        # graph_response может быть list напрямую или dict с ключом 'data'.
        items: list[Any]
        if isinstance(graph_response, list):
            items = graph_response
        elif isinstance(graph_response, dict) and isinstance(graph_response.get("data"), list):
            items = graph_response["data"]
        else:
            # Unexpected shape — отчёт как «всё failed».
            return [
                {"id": oid, "success": False, "code": 0, "error": "unexpected_batch_response"}
                for oid in object_ids
            ]

        results: list[dict[str, Any]] = []
        for idx, oid in enumerate(object_ids):
            item = items[idx] if idx < len(items) else None
            if not isinstance(item, dict):
                results.append(
                    {"id": oid, "success": False, "code": 0, "error": "missing_sub_result"}
                )
                continue
            code = int(item.get("code") or 0)
            body_raw = item.get("body")
            success = 200 <= code < 300
            entry: dict[str, Any] = {"id": oid, "success": success, "code": code}
            if not success:
                # Извлечь error.message если есть.
                try:
                    body_json = json.loads(body_raw) if isinstance(body_raw, str) else {}
                    err = body_json.get("error") if isinstance(body_json, dict) else None
                    if isinstance(err, dict):
                        entry["error"] = err.get("message") or err.get("type") or "graph_error"
                    else:
                        entry["error"] = "non_2xx_no_error_block"
                except (json.JSONDecodeError, AttributeError):
                    entry["error"] = "non_2xx_unparseable"
            results.append(entry)
        return results
