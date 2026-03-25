# -*- coding: utf-8 -*-
"""Observer Worker: основной цикл — reload → scroll → parse → evaluate → notify.

Единственный worker, который взаимодействует с Playwright.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from decimal import Decimal

import httpx

from core.domain import AlertStage, AlertState
from core.observer.service import AlertCandidate, build_metrics_json, evaluate_row
from core.observer.state_machine import resolve_transition
from core.scanner.models import ScannedAdRow
from core.telegram.client import TelegramBotClient
from core.telegram.renderer import TelegramAlertItem, render_alert_message

logger = logging.getLogger(__name__)


async def observer_loop(
    *,
    page,
    offers: dict,
    telegram_bot_token: str,
    telegram_chat_id: str,
    interval_seconds: int = 90,
    jitter_seconds: int = 10,
    warning_percent_of_stop: Decimal = Decimal("80"),
    parse_fn,
    on_snapshot_update=None,
) -> None:
    """Основной бесконечный цикл observer.

    Args:
        page: Playwright Page (уже открыта на Ads Manager)
        offers: dict[offer_code -> {offer, rule_config}]
        telegram_bot_token: токен TG-бота
        telegram_chat_id: ID чата для уведомлений
        interval_seconds: интервал между обновлениями
        jitter_seconds: случайный jitter
        warning_percent_of_stop: процент предупреждения от стопа
        parse_fn: функция парсинга DOM → list[ScannedAdRow]
        on_snapshot_update: callback для сохранения snapshot в БД
    """
    # Хранилище текущих состояний объявлений (в памяти, для FSM)
    ad_states: dict[str, tuple[AlertState, str | None]] = {}

    tg_client = None
    if telegram_bot_token and telegram_chat_id:
        tg_client = TelegramBotClient(telegram_bot_token)

    while True:
        try:
            # 1. Обновляем страницу
            logger.info("Observer: обновление страницы")
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # 2. Плавный скролл + парсинг
            rows = await _scroll_and_parse(page, parse_fn)
            logger.info("Observer: получено %s объявлений", len(rows))

            # 3. Оценка правил и сбор алертов
            alerts_to_send: list[AlertCandidate] = []
            for row in rows:
                # Ищем оффер
                offer_data = None
                if row.resolved_offer_code:
                    offer_data = offers.get(row.resolved_offer_code.casefold())

                evaluation = evaluate_row(
                    row=row,
                    offer_cpa=Decimal(offer_data["offer"].cpa_amount) if offer_data else None,
                    rule_config=offer_data.get("rule_config") if offer_data else None,
                    warning_percent_of_stop=warning_percent_of_stop,
                )

                # FSM-переход
                current_state, current_token = ad_states.get(
                    row.fb_ad_id, (AlertState.NORMAL, None)
                )
                next_state, token, should_emit = resolve_transition(
                    current_state=current_state,
                    current_token=current_token,
                    next_stage=evaluation.stage,
                )
                ad_states[row.fb_ad_id] = (next_state, token)

                # Callback для сохранения в БД
                if on_snapshot_update:
                    await on_snapshot_update(row, evaluation, next_state, token)

                # Собираем алерты для отправки
                if should_emit and evaluation.stage is not None:
                    codes = (
                        evaluation.stop_rule_codes
                        if evaluation.stage == AlertStage.STOP
                        else evaluation.warning_rule_codes
                    )
                    alerts_to_send.append(AlertCandidate(
                        snapshot_id=token or uuid.uuid4().hex,
                        fb_ad_id=row.fb_ad_id,
                        ad_name=row.ad_name,
                        offer_code=row.resolved_offer_code,
                        stage=evaluation.stage,
                        matched_rule_codes=codes,
                        metrics_json=build_metrics_json(row),
                    ))

            # 4. Отправка в Telegram
            if alerts_to_send and tg_client:
                await _send_alerts_to_telegram(tg_client, telegram_chat_id, alerts_to_send)

        except Exception:
            logger.exception("Observer: ошибка в цикле")

        # 5. Сон с jitter
        sleep_time = interval_seconds + random.randint(0, jitter_seconds)
        logger.info("Observer: следующий цикл через %s сек", sleep_time)
        await asyncio.sleep(sleep_time)


async def _scroll_and_parse(page, parse_fn) -> list[ScannedAdRow]:
    """Плавный скролл с рандомными паузами, имитирующий человека.

    Прокручивает таблицу Ads Manager, парсит видимые строки после
    каждого скролла, мерджит результаты. Останавливается когда
    скролл перестаёт давать новые строки.
    """
    all_rows: dict[str, ScannedAdRow] = {}
    max_scroll_passes = 50  # защита от бесконечного цикла
    prev_count = -1

    for pass_num in range(max_scroll_passes):
        # Парсим текущий view
        visible_rows = await parse_fn(page)
        for row in visible_rows:
            all_rows[row.fb_ad_id] = row

        # Если нет новых строк — скролл закончен
        if len(all_rows) == prev_count:
            logger.info(
                "Observer: скролл завершён, проход %s, всего строк %s",
                pass_num + 1,
                len(all_rows),
            )
            break
        prev_count = len(all_rows)

        # Плавный скролл вниз с рандомной паузой (имитация человека)
        scroll_amount = random.randint(300, 600)
        await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await asyncio.sleep(random.uniform(1.0, 3.0))

    return list(all_rows.values())


async def _send_alerts_to_telegram(
    client: TelegramBotClient,
    chat_id: str,
    alerts: list[AlertCandidate],
) -> None:
    """Группирует и отправляет алерты в Telegram."""
    # Группируем по стадии
    by_stage: dict[AlertStage, list[AlertCandidate]] = {}
    for alert in alerts:
        by_stage.setdefault(alert.stage, []).append(alert)

    for stage, group in by_stage.items():
        items = [
            TelegramAlertItem(
                snapshot_id=a.snapshot_id,
                fb_ad_id=a.fb_ad_id,
                ad_name=a.ad_name,
                offer_code=a.offer_code,
                stage=a.stage,
                alert_state=(
                    AlertState.STOP_SENT if a.stage == AlertStage.STOP
                    else AlertState.WARNING_SENT
                ),
                matched_rule_codes=a.matched_rule_codes,
                metrics_json=a.metrics_json,
            )
            for a in group
        ]
        message = render_alert_message(stage=stage, items=items)
        try:
            await client.send_message(
                chat_id=chat_id,
                text=message.text,
                reply_markup=message.reply_markup,
            )
            logger.info("Отправлено TG-сообщение: %s алертов, стадия %s", len(items), stage)
        except Exception:
            logger.exception("Не удалось отправить TG-сообщение")
