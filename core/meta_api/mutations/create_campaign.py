# -*- coding: utf-8 -*-
"""create_campaign — упрощённое создание кампании (только Campaign object).

Graph API: POST /v22.0/{ad_account_id}/campaigns
    ?name=...
    &objective=OUTCOME_LEADS|OUTCOME_SALES|...
    &status=PAUSED|ACTIVE
    &special_ad_categories=["NONE"]
    &daily_budget=<cents>  (опционально, для CBO)
    &lifetime_budget=<cents>  (опционально, для CBO)

ОГРАНИЧЕНИЯ (упрощённая версия Этапа 5):
- Создаётся только Campaign object. AdSets и Ads не создаются —
  юзер должен добавить их вручную в Ads Manager либо через отдельные
  task_queue задачи (на Этапе 5+).
- Без креативов/image_hash/page_id. Это требует chunked upload
  (UploadImage/UploadVideo gRPC методы), которые также не реализованы
  в текущем подходе через универсальный ExecuteGraphCall.
- Без targeting/placements — это уровень AdSet, а AdSet здесь не создаём.

Пример payload:
    MetaMutationPayload(
        mutation_kind="create_campaign",
        target_id="new",  # placeholder, реальный ID появится после создания
        params={
            "name": "DRC_CR2 | MV | Tyver | 26.05",
            "objective": "OUTCOME_LEADS",
            "status_after_create": "PAUSED",
            "special_ad_categories": ["NONE"],
            "daily_budget": 5000,  # опционально, центы
        },
        ad_account_id="act_123456",
    )

Полная реализация (campaign + adset + ad через Batch API, с upload креативов)
запланирована на Этап 5+ — потребует расширения proto или отдельных handlers
(create_adset, create_ad, upload_image, upload_video). Сейчас MVP.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.base import success_result
from core.meta_api.schemas import MetaMutationPayload

_VALID_OBJECTIVES = frozenset(
    {
        "OUTCOME_LEADS",
        "OUTCOME_SALES",
        "OUTCOME_TRAFFIC",
        "OUTCOME_AWARENESS",
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_APP_PROMOTION",
    }
)

_VALID_SPECIAL_CATEGORIES = frozenset(
    {
        "NONE",
        "EMPLOYMENT",
        "HOUSING",
        "CREDIT",
        "ISSUES_ELECTIONS_POLITICS",
    }
)


class CreateCampaignHandler:
    mutation_kind: ClassVar[str] = "create_campaign"

    async def execute(
        self,
        client: MetaApiClient,
        payload: MetaMutationPayload,
    ) -> dict[str, Any]:
        ad_account_id = (payload.ad_account_id or "").strip()
        if not ad_account_id.startswith("act_"):
            raise ValueError(
                f"create_campaign: ad_account_id должен начинаться с 'act_', получено {ad_account_id!r}"
            )

        params = payload.params or {}
        name = self._validate_name(params.get("name"))
        objective = self._validate_objective(params.get("objective"))
        status = self._resolve_status(params.get("status_after_create", "PAUSED"))
        special_categories = self._validate_special_categories(
            params.get("special_ad_categories", ["NONE"])
        )

        graph_params: dict[str, str] = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": json.dumps(special_categories),
        }

        # Опциональный CBO бюджет на уровне кампании
        daily = params.get("daily_budget")
        lifetime = params.get("lifetime_budget")
        if daily is not None and lifetime is not None:
            raise ValueError(
                "create_campaign: укажи не больше одного из daily_budget/lifetime_budget"
            )
        if daily is not None:
            cents = self._validate_cents(daily, field_name="daily_budget")
            graph_params["daily_budget"] = str(cents)
        elif lifetime is not None:
            cents = self._validate_cents(lifetime, field_name="lifetime_budget")
            graph_params["lifetime_budget"] = str(cents)

        graph_response = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{ad_account_id}/campaigns",
            query_params=graph_params,
        )

        new_id = self._extract_new_id(graph_response)
        modified_ids = [new_id] if new_id else []
        return success_result(
            graph_response=graph_response,
            modified_ids=modified_ids,
            extra={
                "ad_account_id": ad_account_id,
                "campaign_name": name,
                "objective": objective,
                "status": status,
            },
        )

    @staticmethod
    def _validate_name(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"name: ожидается строка, получено {value!r}")
        name = value.strip()
        if len(name) < 3:
            raise ValueError("name: минимум 3 символа")
        if len(name) > 400:
            raise ValueError(f"name: слишком длинное ({len(name)} > 400)")
        return name

    @staticmethod
    def _validate_objective(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"objective: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized not in _VALID_OBJECTIVES:
            raise ValueError(
                f"objective: допустимо {sorted(_VALID_OBJECTIVES)}, получено {value!r}"
            )
        return normalized

    @staticmethod
    def _resolve_status(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError(f"status_after_create: ожидается строка, получено {value!r}")
        normalized = value.strip().upper()
        if normalized not in ("PAUSED", "ACTIVE"):
            raise ValueError(
                f"status_after_create: допустимо PAUSED или ACTIVE, получено {value!r}"
            )
        return normalized

    @staticmethod
    def _validate_special_categories(value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError("special_ad_categories: ожидается непустой список (минимум ['NONE'])")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"special_ad_categories: элементы должны быть строками, получен {item!r}"
                )
            up = item.strip().upper()
            if up not in _VALID_SPECIAL_CATEGORIES:
                raise ValueError(
                    f"special_ad_categories: недопустимое значение {item!r}, "
                    f"ожидается из {sorted(_VALID_SPECIAL_CATEGORIES)}"
                )
            normalized.append(up)
        return normalized

    @staticmethod
    def _validate_cents(value: Any, *, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name}: ожидается int центов, получено {value!r}")
        if value <= 0:
            raise ValueError(f"{field_name}: должен быть > 0, получено {value}")
        return value

    @staticmethod
    def _extract_new_id(response: dict[str, Any]) -> str | None:
        if not isinstance(response, dict):
            return None
        val = response.get("id")
        return str(val) if val else None
