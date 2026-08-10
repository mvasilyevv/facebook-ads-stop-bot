# -*- coding: utf-8 -*-
"""Transactional FSM projection after a confirmed Marketing API mutation.

Когда toggle-действие исполняется через Marketing API (а не DOM toggle_executor),
именно meta_api_worker обязан привести ad_alert_state к реальному состоянию
объявления — иначе FSM застревает (напр. в 'stop_sent'), хотя объявление уже
на паузе. Маппинг:
- pause_ad   / bulk pause    → reset_alert_state_after_disable_succeeded (→ 'disabled')
- activate_ad / bulk activate → 'normal'

The projection is committed in the same transaction as terminal task,
incident and notification state.  Projection errors therefore fail the whole
terminal transition instead of publishing a false confirmed result.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.meta_api.schemas import MetaMutationPayload

_BULK_ACTION_DISABLE = frozenset({"pause", "paused"})
_BULK_ACTION_ENABLE = frozenset({"activate", "active"})

_DEACTIVATING_BULK_ACTIONS = frozenset({"pause", "paused"})


def is_deactivating_bulk(params: dict) -> bool:
    """Return whether the canonical ad bulk action stops spend."""
    action = str(params.get("action") or "").lower().strip()
    return action in _DEACTIVATING_BULK_ACTIONS


async def sync_fsm_after_mutation_in_transaction(
    conn: AsyncConnection,
    payload: MetaMutationPayload,
    result: dict | None = None,
) -> None:
    """Project status and terminal task state in one transaction.

    Errors deliberately propagate so the task, incident/outbox transition and
    FSM projection commit or roll back together.  Activation is fail-closed:
    only an older ``disabled`` state may be cleared; a newer warning/stop/claim
    generation always wins over a late activation result.
    """
    kind = payload.mutation_kind
    if kind == "pause_ad":
        await _reset_disabled_in_transaction(conn, str(payload.target_id))
        return
    if kind == "activate_ad":
        await _reset_enabled_in_transaction(conn, str(payload.target_id))
        return
    if kind != "bulk_status_change":
        return

    ad_ids, is_enable = _resolve_bulk_ad_toggle(payload.params or {})
    if not ad_ids:
        return
    if not isinstance(result, dict) or not isinstance(result.get("modified_ids"), list):
        raise ValueError("bulk_status_change result contract violated: modified_ids is required")
    applied = {str(value).strip() for value in result["modified_ids"]}
    for fb_ad_id in (ad_id for ad_id in ad_ids if ad_id in applied):
        if is_enable:
            await _reset_enabled_in_transaction(conn, fb_ad_id)
        else:
            await _reset_disabled_in_transaction(conn, fb_ad_id)


async def _reset_disabled_in_transaction(
    conn: AsyncConnection,
    fb_ad_id: str,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE ad_alert_state
            SET alert_state = 'disabled',
                last_transition_at = NOW(),
                updated_at = NOW()
            WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fbid)
              AND alert_state IN ('warning_sent', 'stop_sent', 'claimed')
            """
        ),
        {"fbid": fb_ad_id},
    )


async def _reset_enabled_in_transaction(
    conn: AsyncConnection,
    fb_ad_id: str,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE ad_alert_state
            SET alert_state = 'normal',
                current_stage = NULL,
                open_state_token = NULL,
                warning_rule_codes = '[]'::jsonb,
                stop_rule_codes = '[]'::jsonb,
                snoozed_until = NULL,
                last_transition_at = NOW(),
                updated_at = NOW()
            WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fbid)
              AND alert_state = 'disabled'
            """
        ),
        {"fbid": fb_ad_id},
    )


def _resolve_bulk_ad_toggle(params: dict) -> tuple[list[str], bool]:
    """Extract the canonical ``{ad_ids, action}`` ad toggle."""
    action = str(params.get("action") or "").lower().strip()
    ids = [str(x).strip() for x in (params.get("ad_ids") or []) if str(x).strip()]
    if action in _BULK_ACTION_ENABLE:
        return ids, True
    if action in _BULK_ACTION_DISABLE:
        return ids, False
    return [], False


__all__ = [
    "is_deactivating_bulk",
    "sync_fsm_after_mutation_in_transaction",
]
