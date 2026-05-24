# -*- coding: utf-8 -*-
"""Планировщик ежедневного digest-а в Telegram.

Раз в сутки в указанное локальное время отправляет daily digest в основной
chat_id. Дата последней отправки персистится в TelegramSettings, чтобы рестарт
поллера в окне `digest_hour` не вызывал повторную рассылку.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

# Интервал проверки планировщика (секунды)
_CHECK_INTERVAL_SECONDS = 60


async def _load_last_sent_date() -> date | None:
    """Читает digest_last_sent_date из TelegramSettings.

    Возвращает None при отсутствии записи / нераспарсиваемом значении /
    ошибке БД (логируется, не падает).
    """
    try:
        from sqlalchemy import select

        from core.db import get_session_factory
        from core.models import TelegramSettings

        factory = get_session_factory()
        async with factory() as session:
            row = await session.execute(select(TelegramSettings.digest_last_sent_date).limit(1))
            raw = row.scalar_one_or_none()
        if not raw:
            return None
        return date.fromisoformat(raw)
    except Exception:
        logger.exception("Не удалось прочитать digest_last_sent_date из TelegramSettings")
        return None


async def _save_last_sent_date(target: date) -> None:
    """Сохраняет digest_last_sent_date в TelegramSettings (без падения наружу)."""
    try:
        from sqlalchemy import update

        from core.db import get_session_factory
        from core.models import TelegramSettings

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(TelegramSettings).values(digest_last_sent_date=target.isoformat())
            )
            await session.commit()
    except Exception:
        logger.exception("Не удалось сохранить digest_last_sent_date в TelegramSettings")


async def run_digest_scheduler(
    client,
    chat_id: str,
    *,
    tz: str = "Europe/Moscow",
    hour: int = 9,
    check_interval: int = _CHECK_INTERVAL_SECONDS,
) -> None:
    """Цикл планировщика daily digest.

    Каждые check_interval секунд проверяет локальное время.
    Если текущий час == hour и за сегодня ещё не отправляли — шлёт digest.
    Дата последней отправки берётся из TelegramSettings.digest_last_sent_date
    при первом проходе, что защищает от повторной отправки после рестарта.
    """
    try:
        import zoneinfo

        local_tz = zoneinfo.ZoneInfo(tz)
    except Exception:
        from datetime import timezone

        local_tz = timezone.utc
        logger.warning("Не удалось загрузить TZ '%s', используем UTC", tz)

    last_sent_date: date | None = await _load_last_sent_date()
    if last_sent_date is not None:
        logger.info("Digest-планировщик восстановил last_sent_date=%s из БД", last_sent_date)

    logger.info(
        "Digest-планировщик запущен: отправка в %02d:00 (%s), chat_id=%s",
        hour,
        tz,
        chat_id,
    )

    while True:
        try:
            await asyncio.sleep(check_interval)

            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(local_tz)
            today = now_local.date()

            # Отправляем только если нужный час и ещё не слали сегодня
            if now_local.hour == hour and last_sent_date != today:
                logger.info("Отправляю daily digest за %s", today)
                sent_ok = await _send_digest(client, chat_id=chat_id, now=now_utc, tz=tz)
                if sent_ok:
                    last_sent_date = today
                    await _save_last_sent_date(today)
                else:
                    logger.warning("Digest не отправлен — попробую через %d сек", check_interval)

        except asyncio.CancelledError:
            logger.info("Digest-планировщик остановлен")
            raise
        except Exception:
            logger.exception("Ошибка в digest-планировщике")
            # Продолжаем работу — следующая итерация через check_interval


async def _send_digest(client, *, chat_id: str, now: datetime, tz: str) -> bool:
    """Загружает данные из БД, формирует и отправляет сообщение.

    Returns:
        True при успешной отправке, False при ошибке.
    """
    try:
        from core.db import get_session_factory
        from core.telegram.digest import render_digest_message
        from core.telegram.digest_queries import get_digest_data

        factory = get_session_factory()
        async with factory() as session:
            data = await get_digest_data(session, now=now, tz_name=tz)

        text = render_digest_message(data, tz=tz)
        await client.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        logger.info("Daily digest отправлен в чат %s", chat_id)
        return True

    except Exception:
        logger.exception("Ошибка при отправке daily digest")
        return False
