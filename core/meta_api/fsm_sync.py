# -*- coding: utf-8 -*-
"""FSM-синхронизация ad_alert_state после успешной Marketing API mutation.

Когда toggle-действие исполняется через Marketing API (а не DOM toggle_executor),
именно meta_api_worker обязан привести ad_alert_state к реальному состоянию
объявления — иначе FSM застревает (напр. в 'stop_sent'), хотя объявление уже
на паузе. Маппинг:
- pause_ad   / bulk pause    → reset_alert_state_after_disable_succeeded (→ 'disabled')
- activate_ad / bulk activate → reset_alert_state_after_enable_succeeded  (→ 'normal')

Best-effort и идемпотентно: reset-функции сами проверяют допустимость перехода
(WHERE alert_state IN (...)), а ошибки не роняют succeeded-контракт задачи —
следующий observer-цикл всё равно увидит реальное состояние.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import MetaMutationPayload
from core.observer.writers import (
    reset_alert_state_after_disable_succeeded,
    reset_alert_state_after_enable_succeeded,
)

logger = logging.getLogger(__name__)

# Сокращённая форма bulk (drafts/autostart): action → семантика toggle.
_BULK_ACTION_DISABLE = frozenset({"pause", "paused"})
_BULK_ACTION_ENABLE = frozenset({"activate", "active"})
# Полная форма bulk: status → семантика toggle.
_BULK_STATUS_DISABLE = frozenset({"PAUSED"})
_BULK_STATUS_ENABLE = frozenset({"ACTIVE"})

# Выключающие действия bulk (обе формы) — для асимметричного стоп-гейта meta_api_worker.
_DEACTIVATING_BULK_ACTIONS = frozenset({"pause", "paused", "disable", "disabled"})


def is_deactivating_bulk(params: dict) -> bool:
    """True если bulk_status_change ВЫКЛЮЧАЕТ открут (pause) — в любой из форм.

    Используется асимметричным стоп-гейтом: выключающий bulk разрешён даже на паузе
    сканирования. Покрывает сокращённую (action=pause) и полную (status=PAUSED) формы —
    единый контракт с _resolve_bulk_ad_toggle. Раньше гейт смотрел только `action`,
    из-за чего полная форма {object_ids, status:PAUSED} ошибочно считалась активирующей
    и откладывалась на паузе (хотя это именно выключение, его надо пропускать).
    """
    action = str(params.get("action") or "").lower().strip()
    if action:
        return action in _DEACTIVATING_BULK_ACTIONS
    status = str(params.get("status") or "").upper().strip()
    return status in _BULK_STATUS_DISABLE


async def sync_fsm_after_mutation(
    engine: AsyncEngine,
    payload: MetaMutationPayload,
) -> None:
    """Привести ad_alert_state к результату успешной mutation. Best-effort.

    Вызывается meta_api_worker'ом ТОЛЬКО после mark_task_succeeded(applied=True).
    Для mutation_kind, не меняющих статус объявления — no-op.
    """
    kind = payload.mutation_kind
    try:
        if kind == "pause_ad":
            await reset_alert_state_after_disable_succeeded(engine, fb_ad_id=payload.target_id)
        elif kind == "activate_ad":
            await reset_alert_state_after_enable_succeeded(engine, fb_ad_id=payload.target_id)
        elif kind == "bulk_status_change":
            await _sync_bulk(engine, payload.params or {})
        # campaign/adset/budget/create/audience/creative — ad_alert_state не трогают
    except Exception:
        logger.warning(
            "sync_fsm_after_mutation: FSM-sync для kind=%s target=%s упал (некритично)",
            kind,
            payload.target_id,
            exc_info=True,
        )


async def _sync_bulk(engine: AsyncEngine, params: dict) -> None:
    """FSM-sync для bulk_status_change. Только ad-level toggle трогает ad_alert_state."""
    ad_ids, is_enable = _resolve_bulk_ad_toggle(params)
    if not ad_ids:
        return
    reset = (
        reset_alert_state_after_enable_succeeded
        if is_enable
        else reset_alert_state_after_disable_succeeded
    )
    for fb_ad_id in ad_ids:
        await reset(engine, fb_ad_id=fb_ad_id)


def _resolve_bulk_ad_toggle(params: dict) -> tuple[list[str], bool]:
    """Извлечь (ad_ids, is_enable) из bulk params. ([], _) если не ad-level toggle.

    Поддерживает обе формы (как BulkStatusChangeHandler):
    - сокращённая: {ad_ids, action: pause|activate} — всегда object_type=ad.
    - полная: {object_ids, status: PAUSED|ACTIVE, object_type} — только object_type=ad
      (campaign/adset не имеют ad_alert_state).
    """
    # Сокращённая форма (drafts/autostart) — всегда ad-level.
    if "ad_ids" in params or "action" in params:
        action = str(params.get("action") or "").lower().strip()
        ids = [str(x).strip() for x in (params.get("ad_ids") or []) if str(x).strip()]
        if action in _BULK_ACTION_ENABLE:
            return ids, True
        if action in _BULK_ACTION_DISABLE:
            return ids, False
        return [], False
    # Полная форма — синхронизируем только object_type='ad'.
    object_type = str(params.get("object_type") or "ad").lower()
    if object_type != "ad":
        return [], False
    status = str(params.get("status") or "").upper().strip()
    ids = [str(x).strip() for x in (params.get("object_ids") or []) if str(x).strip()]
    if status in _BULK_STATUS_ENABLE:
        return ids, True
    if status in _BULK_STATUS_DISABLE:
        return ids, False
    return [], False


__all__ = ["is_deactivating_bulk", "sync_fsm_after_mutation"]
