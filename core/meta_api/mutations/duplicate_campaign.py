# -*- coding: utf-8 -*-
"""duplicate_campaign — клонировать кампанию (deep или плоский копией).

Graph API: POST /v22.0/{campaign_id}/copies
    ?deep_copy=true|false
    &status_option=PAUSED|ACTIVE_TO_INHERITED

Маппинг params.status_after_clone:
    "PAUSED"  → status_option=PAUSED (клон в PAUSED, рекомендуемо)
    "ACTIVE"  → status_option=ACTIVE_TO_INHERITED (клон наследует исходный статус)

Если задан new_name — используем Batch API: один POST к / с двумя sub-запросами:
  entry[0] "copy":   POST /{campaign_id}/copies  (возвращает copied_campaign_id)
  entry[1] "rename": POST /{result=copy:$.copied_campaign_id}?name=...
Batch НЕ транзакционен. Контракт провалов приведён к create_campaign (MID-4, аудит 02.07):
  - copy упал (ничего не создано) → возвращаем success=False с пустыми modified_ids;
    осиротевших объектов нет, чистить нечего. Worker метит failed без retry (R3).
  - copy ok, rename упал → копия РЕАЛЬНО создана и осиротела в Meta → бросаем
    DuplicateCampaignPartialError(created_ids={"campaign": copied_id}). Worker логирует
    осиротевший id и метит failed без retry — как CreateCampaignPartialError. Так оператор
    получает явный сигнал «проверь Meta вручную» с конкретным id для переименования/чистки.
Если new_name отсутствует или пустой — обычный одиночный /copies без batch.

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
from core.meta_api.errors import MutationValidationError
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    jsonpath_ref,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.mutations.base import require_numeric_id, success_result
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)


class DuplicateCampaignPartialError(Exception):
    """Batch copy прошёл, но rename упал: копия создана и осиротела в Meta (MID-4).

    Зеркалит контракт CreateCampaignPartialError — worker маршрутизирует её в
    mark_failed без retry (retry создал бы вторую копию) и логирует created_ids как
    осиротевшие объекты для ручной проверки/переименования.

    Атрибут `created_ids` — dict {"campaign": copied_campaign_id} (реально созданная копия).
    Атрибут `failed_steps` — список шагов с ошибкой (здесь всегда rename).
    """

    def __init__(
        self,
        message: str,
        *,
        created_ids: dict[str, str],
        failed_steps: list[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.created_ids = created_ids
        self.failed_steps = failed_steps

    def __repr__(self) -> str:
        return (
            f"DuplicateCampaignPartialError("
            f"created_ids={self.created_ids!r}, "
            f"failed_steps={self.failed_steps!r}, "
            f"message={str(self)!r})"
        )


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
            raise MutationValidationError(f"deep_copy: ожидается bool, получено {deep_copy!r}")

        status_after = self._resolve_status_option(params.get("status_after_clone", "PAUSED"))
        new_name = self._validate_new_name(params.get("new_name"))

        if new_name:
            return await self._execute_with_batch(
                client=client,
                src_campaign_id=src_campaign_id,
                deep_copy=deep_copy,
                status_after=status_after,
                new_name=new_name,
                ad_account_id=payload.ad_account_id,
            )
        return await self._execute_simple(
            client=client,
            src_campaign_id=src_campaign_id,
            deep_copy=deep_copy,
            status_after=status_after,
            ad_account_id=payload.ad_account_id,
        )

    async def _execute_simple(
        self,
        *,
        client: MetaApiClient,
        src_campaign_id: str,
        deep_copy: bool,
        status_after: str,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Обычный /copies без batch — используется когда new_name не задан."""
        copies_response = await client.execute_graph_call(
            ad_account_id=ad_account_id,
            method="POST",
            endpoint=f"/{src_campaign_id}/copies",
            query_params={
                "deep_copy": "true" if deep_copy else "false",
                "status_option": status_after,
            },
        )
        copied_id = self._extract_copied_id(copies_response)
        return success_result(
            graph_response=copies_response,
            modified_ids=[copied_id] if copied_id else [],
            extra={"source_campaign_id": src_campaign_id},
        )

    async def _execute_with_batch(
        self,
        *,
        client: MetaApiClient,
        src_campaign_id: str,
        deep_copy: bool,
        status_after: str,
        new_name: str,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Batch API: copy + rename за один HTTP-запрос.

        Batch НЕ транзакционен (MID-4, контракт как у create_campaign):
        - copy упал (ничего не создано) → success=False, modified_ids пуст;
        - copy ok + rename упал → копия осиротела → бросаем DuplicateCampaignPartialError
          с created_ids={"campaign": copied_id}, worker метит failed без retry.
        """
        copy_entry = make_batch_entry(
            method="POST",
            relative_url=f"{src_campaign_id}/copies",
            body_params={
                "deep_copy": deep_copy,
                "status_option": status_after,
            },
            name="copy",
        )
        rename_entry = make_batch_entry(
            method="POST",
            relative_url=jsonpath_ref("copy", "$.copied_campaign_id"),
            body_params={"name": new_name},
            name="rename",
        )

        batch_json = build_batch_payload([copy_entry, rename_entry])
        batch_response = await client.execute_graph_call(
            ad_account_id=ad_account_id,
            method="POST",
            endpoint="/",
            query_params={"batch": batch_json},
        )

        sub_results = parse_batch_response(batch_response, expected_count=2)
        copy_sub = sub_results[0] if len(sub_results) > 0 else {}
        rename_sub = sub_results[1] if len(sub_results) > 1 else {}

        # Вытаскиваем copied_campaign_id из тела copy sub-response.
        copied_id: str | None = None
        if copy_sub.get("success") and isinstance(copy_sub.get("body"), dict):
            copied_id = self._extract_copied_id(copy_sub["body"])

        if not copy_sub.get("success") or not copied_id:
            # Копия не создана.
            copy_error = copy_sub.get("error") or f"http {copy_sub.get('code', '?')}"
            logger.error(
                "duplicate_campaign batch: copy упал: %s (campaign_id=%s)",
                copy_error,
                src_campaign_id,
            )
            return {
                "success": False,
                "graph_response": batch_response,
                "modified_ids": [],
                "source_campaign_id": src_campaign_id,
                "last_error": f"copy не выполнен: {copy_error}",
            }

        modified_ids = [copied_id]

        if rename_sub.get("success"):
            logger.info(
                "duplicate_campaign batch: copy=%s rename=%r — всё ок",
                copied_id,
                new_name,
            )
            return success_result(
                graph_response=batch_response,
                modified_ids=modified_ids,
                extra={
                    "source_campaign_id": src_campaign_id,
                    "new_name": new_name,
                },
            )

        # Rename упал, но копия создана и осиротела в Meta. Бросаем partial-error
        # (контракт как у create_campaign): worker метит failed без retry + логирует
        # осиротевший id для ручной проверки. Retry создал бы вторую копию.
        rename_error = rename_sub.get("error") or f"http {rename_sub.get('code', '?')}"
        last_error = f"копия создана (id={copied_id}), но переименование не удалось: {rename_error}"
        logger.warning(
            "duplicate_campaign batch: copy=%s ок, rename упал: %s",
            copied_id,
            rename_error,
        )
        raise DuplicateCampaignPartialError(
            last_error,
            created_ids={"campaign": copied_id},
            failed_steps=[{"step": "rename", "error": rename_error}],
        )

    @staticmethod
    def _resolve_status_option(value: Any) -> str:
        """status_after_clone (PAUSED|ACTIVE) → Graph status_option."""
        if not isinstance(value, str):
            raise MutationValidationError(
                f"status_after_clone: ожидается строка, получено {value!r}"
            )
        normalized = value.strip().upper()
        if normalized == "PAUSED":
            return "PAUSED"
        if normalized == "ACTIVE":
            return "ACTIVE_TO_INHERITED"
        raise MutationValidationError(
            f"status_after_clone: допустимо PAUSED или ACTIVE, получено {value!r}"
        )

    @staticmethod
    def _validate_new_name(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise MutationValidationError(
                f"new_name: ожидается строка, получено {type(value).__name__}"
            )
        name = value.strip()
        if not name:
            return None
        if len(name) > 400:
            raise MutationValidationError(f"new_name: слишком длинное ({len(name)} > 400 символов)")
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
