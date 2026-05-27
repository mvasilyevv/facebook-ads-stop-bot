# -*- coding: utf-8 -*-
"""/record_plan, /stop_record, /plans и callback plan:<uuid>.

Telegram-интеграция creator workflow:
  /record_plan <name>  — публикует record_start в Redis → creator_recorder начинает CDP-запись
  /stop_record         — публикует record_stop → recorder останавливает и сохраняет план
  /plans               — список active creator_plans с inline-кнопками «Запустить»
  plan:<uuid>          — создаёт task_queue plan_run → creator_worker исполняет
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks.queue import create_task
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

if TYPE_CHECKING:  # pragma: no cover
    from core.pubsub import RedisPubSub

logger = logging.getLogger(__name__)

# Pubsub-каналы (должны совпадать с apps/creator_recorder/main.py)
CHANNEL_RECORD_START = "fb_agent:creator:record_start"
CHANNEL_RECORD_STOP = "fb_agent:creator:record_stop"

# Лимит списка планов в TG
_PLANS_LIMIT = 20


# ====================== /record_plan ======================


async def handle_record_plan(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    redis: RedisPubSub,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    args_text: str,
) -> None:
    """/record_plan <name> [| ad_account_id=act_...]  — начать CDP-запись плана.

    Поддерживает pipe-separated args:
      /record_plan my plan | ad_account_id=act_123
    Если имя пустое — отвечает ошибкой и не публикует.
    """
    # Парсим pipe-separated аргументы
    plan_name = args_text
    ad_account_id = ""
    if "|" in args_text:
        pipe_parts = args_text.split("|", 1)
        plan_name = pipe_parts[0].strip()
        extra = pipe_parts[1].strip()
        for kv in extra.split(","):
            kv = kv.strip()
            if kv.startswith("ad_account_id="):
                ad_account_id = kv.split("=", 1)[1].strip()
    else:
        plan_name = args_text.strip()

    if not plan_name:
        await send_text(
            client=client,
            chat_id=chat_id,
            text=(
                "⚠️ Укажи название плана.\n"
                "Использование: `/record_plan <название>`\n"
                "Пример: `/record_plan Кампания Бразилия июль`"
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    await redis.publish(
        CHANNEL_RECORD_START,
        {
            "plan_name": plan_name,
            "recipient_id": str(chat_id),
            "ad_account_id": ad_account_id,
        },
    )

    await send_text(
        client=client,
        chat_id=chat_id,
        text=(
            f"✅ Запись начата: *{plan_name}*\n\n"
            "Открой Ads Manager и выполни нужные шаги.\n"
            "Когда закончишь — нажми `/stop_record`."
        ),
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


# ====================== /stop_record ======================


async def handle_stop_record(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    redis: RedisPubSub,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
) -> None:
    """/stop_record — остановить CDP-запись. Recorder сохранит план и отправит confirmation."""
    await redis.publish(
        CHANNEL_RECORD_STOP,
        {"recipient_id": str(chat_id)},
    )

    await send_text(
        client=client,
        chat_id=chat_id,
        text=(
            "⏳ Запись останавливается…\n"
            "План будет сохранён в базе. Получишь уведомление когда готово.\n"
            "После этого запусти план через `/plans`."
        ),
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


# ====================== /plans ======================


async def handle_list_plans(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    thread_id: int | None,
) -> None:
    """/plans — показывает active creator_plans с кнопками «Запустить»."""

    plans = await _load_active_plans(engine)

    if not plans:
        await send_text(
            client=client,
            chat_id=chat_id,
            text=("Нет активных планов.\n\nЗапиши новый: `/record_plan <название>`"),
            message_thread_id=thread_id,
        )
        return

    # Собираем текст и inline-кнопки
    lines: list[str] = ["*Активные планы:*\n"]
    keyboard_rows: list[list[dict[str, str]]] = []

    for i, plan in enumerate(plans, start=1):
        plan_id = str(plan["id"])
        name = str(plan["name"])
        created_at = str(plan["created_at"])[:16].replace("T", " ")  # YYYY-MM-DD HH:MM
        lines.append(f"{i}. *{name}* — `{created_at}`")
        keyboard_rows.append([{"text": f"▶ {name}", "callback_data": f"plan:{plan_id}"}])

    text_body = "\n".join(lines)
    # Обрезаем если TG-лимит
    if len(text_body) > 3800:
        text_body = text_body[:3800] + "\n_(список обрезан)_"

    reply_markup = {"inline_keyboard": keyboard_rows}

    try:
        await client.send_message(
            chat_id=str(chat_id),
            text=text_body,
            message_thread_id=thread_id,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("handle_list_plans: send_message failed")


async def _load_active_plans(engine: AsyncEngine) -> list[dict[str, Any]]:
    """SELECT active creator_plans, последние _PLANS_LIMIT штук."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, created_at
                    FROM creator_plans
                    WHERE is_archived = false
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                {"lim": _PLANS_LIMIT},
            )
        ).fetchall()
    return [{"id": str(r[0]), "name": str(r[1]), "created_at": r[2]} for r in rows]


# ====================== callback plan:<uuid> ======================


async def handle_plan_run_callback(
    callback_query: dict[str, Any],
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
) -> None:
    """plan:<plan_id> — создать task_queue plan_run → creator_worker исполняет."""
    cq_id = str(callback_query.get("id", ""))
    data = str(callback_query.get("data") or "")
    from_user = callback_query.get("from") or {}
    user_id = int(from_user.get("id", 0))

    # plan:<uuid>
    parts = data.split(":", 1)
    if len(parts) < 2:
        await _answer_cq(client, cq_id, "Некорректный формат")
        return

    plan_id = parts[1].strip()

    # Валидируем: план существует и не архивирован
    plan = await _load_plan_for_callback(engine, plan_id)
    if plan is None:
        await _answer_cq(client, cq_id, "❌ План не найден")
        return
    if plan.get("is_archived"):
        await _answer_cq(client, cq_id, "❌ План архивирован — запустить нельзя")
        return

    # Idempotency на основе времени — позволяет запускать один план несколько раз
    idem_key = f"plan_run:{plan_id}:{int(time.time() // 60)}"  # окно 60 сек
    requested_by = f"user:{user_id}"

    try:
        task_id = await create_task(
            engine,
            task_type="plan_run",
            idempotency_key=idem_key,
            payload={"plan_id": plan_id},
            requested_by=requested_by,
        )
    except Exception:
        logger.exception("plan_run create_task failed (plan_id=%s)", plan_id)
        await _answer_cq(client, cq_id, "❌ Ошибка создания задачи")
        return

    if task_id:
        ack = f"🚀 Задача #{task_id} создана — creator_worker исполнит"
    else:
        ack = "⏳ Задача уже в очереди (60-секундное окно)"

    await _answer_cq(client, cq_id, ack)

    # Редактируем кнопки — убираем чтобы не запустили повторно в том же окне
    if task_id:
        msg = callback_query.get("message") or {}
        msg_id = msg.get("message_id")
        chat_id = (msg.get("chat") or {}).get("id")
        if msg_id and chat_id:
            try:
                await client.edit_message_reply_markup(
                    chat_id=str(chat_id),
                    message_id=int(msg_id),
                    reply_markup={"inline_keyboard": []},
                )
            except Exception:
                logger.debug("edit_message_reply_markup не сработал — не критично")


async def _load_plan_for_callback(engine: AsyncEngine, plan_id: str) -> dict[str, Any] | None:
    """Загрузить план по id для валидации. None если не найден."""
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT id, name, is_archived
                        FROM creator_plans
                        WHERE id = :pid
                        LIMIT 1
                        """
                    ),
                    {"pid": plan_id},
                )
            ).first()
    except Exception:
        logger.exception("_load_plan_for_callback failed")
        return None
    if not row:
        return None
    return {"id": str(row[0]), "name": str(row[1]), "is_archived": bool(row[2])}


async def _answer_cq(client: TelegramBotClient, cq_id: str, text_: str) -> None:
    """Безопасный ответ на callback query."""
    try:
        await client.answer_callback_query(cq_id, text=text_)
    except Exception:
        logger.debug("answer_callback_query failed: %s", text_)


__all__ = [
    "CHANNEL_RECORD_START",
    "CHANNEL_RECORD_STOP",
    "handle_record_plan",
    "handle_stop_record",
    "handle_list_plans",
    "handle_plan_run_callback",
]
