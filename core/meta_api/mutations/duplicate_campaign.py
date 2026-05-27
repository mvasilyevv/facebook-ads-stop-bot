# -*- coding: utf-8 -*-
"""duplicate_campaign — клонировать кампанию (deep или плоский копией).

Graph API: POST /v22.0/{campaign_id}/copies
    ?deep_copy=true|false
    &status_option=PAUSED|ACTIVE_TO_INHERITED

Маппинг params.status_after_clone:
    "PAUSED"  → status_option=PAUSED (клон в PAUSED, рекомендуемо)
    "ACTIVE"  → status_option=ACTIVE_TO_INHERITED (клон наследует исходный статус)

Если задан new_name — handler делает доп. вызов POST /{copied_id}?name=...
после успешного copies. Если rename падает — копия уже создана, поэтому handler
возвращает success с warning (вместо retry, чтобы не создать вторую копию).
Произвольное имя через сам copies endpoint Marketing API не позволяет —
только через rename_options с суффиксами, что нам неудобно.

Пример payload:
    MetaMutationPayload(
        mutation_kind="duplicate_campaign",
        target_id="23843000000",  # source campaign_id
        params={
            "deep_copy": True,
            "status_after_clone": "PAUSED",
            "new_name": "Copy of CR2 | 26.05",  # опционально
        },
        ad_account_id="act_123",
    )
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.errors import MetaApiError
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)


class DuplicateCampaignHandler:
    mutation_kind: ClassVar[str] = "duplicate_campaign"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        src_campaign_id = require_numeric_id(payload.target_id, "target_id (campaign_id)")
        params = payload.params or {}

        deep_copy = params.get("deep_copy", True)
        if not isinstance(deep_copy, bool):
            raise ValueError(f"deep_copy: ожидается bool, получено {deep_copy!r}")

        status_after = self._resolve_status_option(params.get("status_after_clone", "PAUSED"))
        new_name = self._validate_new_name(params.get("new_name"))

        # Шаг 1: copies
        copies_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{src_campaign_id}/copies",
            query_params={
                "deep_copy": "true" if deep_copy else "false",
                "status_option": status_after,
            },
        )

        copied_id = self._extract_copied_id(copies_response)
        modified_ids = [copied_id] if copied_id else []
        extra: dict[str, Any] = {"source_campaign_id": src_campaign_id}

        # Шаг 2 (опционально): rename. Если упадёт — копия уже создана,
        # возвращаем success с warning, чтобы worker не делал retry copies.
        if new_name and copied_id:
            try:
                rename_response = await client.execute_graph_call(
                    method="POST",
                    endpoint=f"/{copied_id}",
                    query_params={"name": new_name},
                )
                extra["rename_response"] = rename_response
                extra["new_name"] = new_name
            except MetaApiError as exc:
                logger.warning(
                    "duplicate_campaign: rename %s → %r упал: %s (копия создана, retry не нужен)",
                    copied_id,
                    new_name,
                    exc,
                )
                extra["rename_warning"] = str(exc)

        return success_result(
            graph_response=copies_response,
            modified_ids=modified_ids,
            extra=extra,
        )

    @staticmethod
    def _resolve_status_option(value: Any) -> str:
        """status_after_clone (PAUSED|ACTIVE) → Graph status_option."""
        if not isinstance(value, str):
            raise ValueError(f"status_after_clone: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized == "PAUSED":
            return "PAUSED"
        if normalized == "ACTIVE":
            return "ACTIVE_TO_INHERITED"
        raise ValueError(f"status_after_clone: допустимо PAUSED или ACTIVE, получено {value!r}")

    @staticmethod
    def _validate_new_name(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError(f"new_name: ожидается строка, получено {type(value).__name__}")
        name = value.strip()
        if not name:
            return None
        if len(name) > 400:
            raise ValueError(f"new_name: слишком длинное ({len(name)} > 400 символов)")
        return name

    @staticmethod
    def _extract_copied_id(response: dict[str, Any]) -> str | None:
        """Из ответа copies endpoint вытащить ID новой кампании.

        Meta возвращает: {"copied_campaign_id": "...", "ad_object_ids": [...]}.
        Поле может варьироваться между версиями API.
        """
        if not isinstance(response, dict):
            return None
        for key in ("copied_campaign_id", "id"):
            val = response.get(key)
            if isinstance(val, str) and val:
                return val
        return None
