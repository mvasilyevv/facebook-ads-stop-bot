# -*- coding: utf-8 -*-
"""Реестр Meta API mutation handlers + dispatcher.

Архитектура: каждая mutation — отдельный класс-handler с одним методом execute().
Все handlers зарегистрированы в MUTATION_HANDLERS по ключу mutation_kind. Worker
вызывает dispatch_mutation(client, payload), которая делегирует нужному handler.

Подход: универсальный ExecuteGraphCall, никаких новых RPC в proto/v1/meta_api.proto.
Все mutations — это POST/DELETE к graph.facebook.com/v22.0/{object_id} с разными params.

Зарегистрированные kinds (должны совпадать с MUTATION_KINDS в schemas.py):
- pause_ad, activate_ad
- pause_campaign, activate_campaign
- set_adset_budget
- duplicate_campaign
- bulk_status_change
- create_campaign
- custom_audience   (create/update/delete Custom Audiences)
- set_ad_creative   (замена creative у существующего объявления)

См. META_INTEGRATION_PLAN.md, Этап 5.
"""

from __future__ import annotations

from typing import Any

from core.meta_api.client import MetaApiClient
from core.meta_api.mutations.activate_ad import ActivateAdHandler
from core.meta_api.mutations.activate_campaign import ActivateCampaignHandler
from core.meta_api.mutations.base import MutationHandler
from core.meta_api.mutations.bulk_status_change import BulkStatusChangeHandler
from core.meta_api.mutations.create_campaign import CreateCampaignHandler
from core.meta_api.mutations.custom_audience import CustomAudienceHandler
from core.meta_api.mutations.duplicate_adset_structure import DuplicateAdsetStructureHandler
from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignHandler
from core.meta_api.mutations.pause_ad import PauseAdHandler
from core.meta_api.mutations.pause_campaign import PauseCampaignHandler
from core.meta_api.mutations.set_ad_creative import SetAdCreativeHandler
from core.meta_api.mutations.set_adset_budget import SetAdsetBudgetHandler
from core.meta_api.schemas import MetaMutationPayload

# Реестр handlers по ключу mutation_kind.
# Должен покрывать MUTATION_KINDS из core.meta_api.schemas — есть тест на это.
MUTATION_HANDLERS: dict[str, MutationHandler] = {
    "pause_ad": PauseAdHandler(),
    "activate_ad": ActivateAdHandler(),
    "pause_campaign": PauseCampaignHandler(),
    "activate_campaign": ActivateCampaignHandler(),
    "set_adset_budget": SetAdsetBudgetHandler(),
    "duplicate_campaign": DuplicateCampaignHandler(),
    "duplicate_adset_structure": DuplicateAdsetStructureHandler(),
    "bulk_status_change": BulkStatusChangeHandler(),
    "create_campaign": CreateCampaignHandler(),
    "custom_audience": CustomAudienceHandler(),
    "set_ad_creative": SetAdCreativeHandler(),
}


async def dispatch_mutation(
    client: MetaApiClient,
    payload: MetaMutationPayload,
) -> dict[str, Any]:
    """Найти handler по payload.mutation_kind и исполнить.

    Бросает NotImplementedError если handler не зарегистрирован (worker → mark_failed).
    Доменные ошибки Meta пробрасываются как есть.
    """
    handler = MUTATION_HANDLERS.get(payload.mutation_kind)
    if handler is None:
        raise NotImplementedError(
            f"Handler для mutation_kind={payload.mutation_kind!r} не зарегистрирован"
        )
    return await handler.execute(client, payload)


__all__ = [
    "MUTATION_HANDLERS",
    "MutationHandler",
    "dispatch_mutation",
]
