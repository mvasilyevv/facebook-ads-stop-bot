# -*- coding: utf-8 -*-
"""TG-команды массового вкл/выкл объявлений: /pause /resume <offer> (owner-scoped).

Резолвит активные объявления СВОИХ кампаний по offer-коду, создаёт DRAFT
bulk_status_change (Marketing API) и присылает превью с ✅ / ❌. Реальное
исполнение — meta_api_worker после подтверждения (dr_ok → draft_confirm.py).
Ad-level: выключаются/включаются объявления, кампания/адсет не трогаются.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.bulk import MAX_BULK, resolve_owner_ad_ids
from core.meta_api.queue import create_draft_task
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_observer_config
from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

logger = logging.getLogger(__name__)

# command → (mutation action, человекочитаемый label)
_ACTIONS: dict[str, tuple[str, str]] = {
    "pause": ("pause", "⏸ массовая ПАУЗА"),
    "resume": ("activate", "▶️ массовое ВКЛЮЧЕНИЕ"),
}


async def handle_bulk_toggle(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    username: str | None,
    command: str,
    args_text: str,
) -> None:
    """/pause <offer> | /resume <offer> — массовое вкл/выкл объявлений оффера (draft-first)."""
    action_label = _ACTIONS.get(command)
    if action_label is None:
        return
    action, label = action_label

    offer = (args_text or "").strip()
    if not offer:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                f"Использование: {fmt.code(f'/{command} <offer>')}\n"
                f"Пример: {fmt.code(f'/{command} GH_CR2')} — "
                f"{fmt.esc(label.lower())} всех объявлений оффера."
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Owner-scoping: только мои кампании (теги из observer_config).
    config = await load_observer_config(engine)
    owner_tag = (config or {}).get("owner_campaign_tag")

    ad_ids, total = await resolve_owner_ad_ids(engine, offer_code=offer, owner_tag=owner_tag)
    if not ad_ids:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                f"По офферу {fmt.code(offer)} активных объявлений в твоих кампаниях "
                "не нашлось.\n(owner-scoping: проверяются только кампании с твоим тегом)"
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id=f"bulk:{len(ad_ids)}",
        params={
            "ad_ids": sorted(ad_ids),
            "action": action,
            "resolved_from_offer": offer,
        },
        ad_account_id=None,
    )
    task_id = await create_draft_task(
        engine,
        payload=payload,
        requested_by=f"tg:{username or chat_id}",
        created_by_chat_id=chat_id,
    )
    if task_id is None:
        await send_text(
            client,
            chat_id=chat_id,
            text="Не удалось создать черновик (коллизия idempotency_key?). Повтори.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Превью с кнопками ✅ / ❌ — draft-callback (dr_ok/dr_cancel).
    from core.telegram.handlers.draft_confirm import draft_inline_keyboard

    preview = ", ".join(ad_ids[:5]) + (" …" if len(ad_ids) > 5 else "")
    truncated = (
        f"\n⚠️ Найдено {total}, в задачу взяты первые {MAX_BULK}." if total > len(ad_ids) else ""
    )
    await client.send_message(
        chat_id=str(chat_id),
        text=(
            f"📝 {fmt.b(f'Черновик #{task_id}')} · {fmt.esc(label)}\n"
            f"Оффер {fmt.code(offer)} → {fmt.b(len(ad_ids))} объявлений.{fmt.esc(truncated)}\n"
            f"{fmt.code(preview)}\n\n"
            "Подтверди ✅ / ❌."
        ),
        message_thread_id=thread_id,
        reply_markup=draft_inline_keyboard(task_id),
        parse_mode="HTML",
    )


__all__ = ["handle_bulk_toggle"]
