# -*- coding: utf-8 -*-
"""Реестр Meta API mutation handlers + dispatcher.

Архитектура: каждая mutation — отдельный класс-handler с одним методом execute().
Все handlers зарегистрированы в MUTATION_HANDLERS по ключу mutation_kind. Worker
вызывает dispatch_mutation(client, payload), которая делегирует нужному handler.

Подход: универсальный ExecuteGraphCall, никаких новых RPC в proto/v1/meta_api.proto.
Все mutations — это POST к graph.facebook.com/v22.0/{object_id} с разными params.

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
from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignHandler
from core.meta_api.mutations.pause_ad import PauseAdHandler
from core.meta_api.mutations.pause_campaign import PauseCampaignHandler
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
    "bulk_status_change": BulkStatusChangeHandler(),
    "create_campaign": CreateCampaignHandler(),
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
