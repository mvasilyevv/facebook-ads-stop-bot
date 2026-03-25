# -*- coding: utf-8 -*-
"""Observer Worker: основной цикл — refresh → scroll → parse → evaluate → notify.

Единственный worker, который взаимодействует с Playwright.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.db import get_session_factory
from core.domain import AlertStage, AlertState
from core.models import AdSnapshot, Offer
from core.observer.service import AlertCandidate, build_metrics_json, evaluate_row
from core.observer.state_machine import resolve_transition
from core.scanner.models import ScannedAdRow
from core.scanner.parser import refresh_table
from core.telegram.client import TelegramBotClient
from core.telegram.renderer import TelegramAlertItem, render_alert_message

logger = logging.getLogger(__name__)


async def load_offers_from_db() -> dict:
    """Загружает активные офферы с правилами из БД.

    Returns:
        dict[offer_code_lower -> {"offer": Offer, "rule_config": OfferRuleConfig}]
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Offer).where(Offer.is_active.is_(True)).options(selectinload(Offer.rule_config))
        )
        offers = result.scalars().all()

        offers_map = {}
        for offer in offers:
            if offer.rule_config:
                offers_map[offer.code.casefold()] = {
                    "offer": offer,
                    "rule_config": offer.rule_config,
                }
        logger.info("Загружено %s активных офферов из БД", len(offers_map))
        return offers_map


def resolve_offer_code(
    ad_name: str,
    campaign_name: str,
    offers: dict,
) -> str | None:
    """Сопоставляет объявление с оффером по вхождению кода в название.

    Оффер содержит часть названия объявления/кампании.
    Например, оффер "DRC_CR2" → объявление "DRC_CR2_CR002".
    """
    # Проверяем ad_name и campaign_name
    text_lower = f"{campaign_name} {ad_name}".casefold()
    best_match: str | None = None
    best_len = 0

    for code in offers:
        if code in text_lower and len(code) > best_len:
            best_match = code
            best_len = len(code)

    return best_match


async def save_snapshot_to_db(
    row: ScannedAdRow,
    evaluation,
    alert_state: AlertState,
    token: str | None,
    offer_code: str | None,
    offers: dict,
) -> None:
    """Upsert снэпшота объявления и создание AlertEvent при необходимости."""
    factory = get_session_factory()
    async with factory() as session:
        # Ищем существующий снэпшот по fb_ad_id
        result = await session.execute(
            select(AdSnapshot).where(AdSnapshot.fb_ad_id == row.fb_ad_id)
        )
        snapshot = result.scalar_one_or_none()

        # Определяем offer_id
        offer_id = None
        if offer_code and offer_code in offers:
            offer_id = offers[offer_code]["offer"].id

        now = datetime.now(UTC)

        if snapshot is None:
            # Создаём новый снэпшот
            snapshot = AdSnapshot(
                fb_ad_id=row.fb_ad_id,
                campaign_name=row.campaign_name,
                adset_name=row.adset_name,
                ad_name=row.ad_name,
                delivery_status=row.delivery_status,
                offer_id=offer_id,
                resolved_offer_code=offer_code,
                spend=row.spend,
                clicks=row.clicks,
                cpc=row.cpc,
                leads=row.leads,
                cost_per_lead=row.cost_per_lead,
                registrations=row.registrations,
                cost_per_registration=row.cost_per_registration,
                deposits=row.deposits,
                alert_state=alert_state,
                current_stage=evaluation.stage,
                warning_rule_codes=evaluation.warning_rule_codes,
                stop_rule_codes=evaluation.stop_rule_codes,
                open_state_token=token,
                last_observed_at=now,
            )
            session.add(snapshot)
        else:
            # Обновляем метрики
            snapshot.campaign_name = row.campaign_name
            snapshot.adset_name = row.adset_name
            snapshot.ad_name = row.ad_name
            snapshot.delivery_status = row.delivery_status
            snapshot.offer_id = offer_id
            snapshot.resolved_offer_code = offer_code
            snapshot.spend = row.spend
            snapshot.clicks = row.clicks
            snapshot.cpc = row.cpc
            snapshot.leads = row.leads
            snapshot.cost_per_lead = row.cost_per_lead
            snapshot.registrations = row.registrations
            snapshot.cost_per_registration = row.cost_per_registration
            snapshot.deposits = row.deposits
            snapshot.alert_state = alert_state
            snapshot.current_stage = evaluation.stage
            snapshot.warning_rule_codes = evaluation.warning_rule_codes
            snapshot.stop_rule_codes = evaluation.stop_rule_codes
            snapshot.open_state_token = token
            snapshot.last_observed_at = now

        await session.commit()


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

    # Счётчик циклов для периодической перезагрузки офферов
    cycle_count = 0
    RELOAD_OFFERS_EVERY = 10  # Перечитываем офферы каждые 10 циклов

    while True:
        try:
            # Перезагружаем офферы каждые N циклов
            if cycle_count % RELOAD_OFFERS_EVERY == 0:
                try:
                    offers = await load_offers_from_db()
                except Exception:
                    logger.warning(
                        "Не удалось обновить офферы из БД, используем предыдущие",
                        exc_info=True,
                    )
            cycle_count += 1

            # 1. Обновляем таблицу (кнопка «Обновить» или reload)
            logger.info("Observer: обновление таблицы")
            refreshed = await refresh_table(page)
            if not refreshed:
                # Если кнопка не найдена — перезагружаем страницу
                await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # 2. Плавный скролл + парсинг
            rows = await _scroll_and_parse(page, parse_fn)
            logger.info("Observer: получено %s объявлений", len(rows))

            # 3. Оценка правил и сбор алертов
            alerts_to_send: list[AlertCandidate] = []
            for row in rows:
                # Матчинг оффера по названию
                offer_code = resolve_offer_code(row.ad_name, row.campaign_name, offers)
                offer_data = offers.get(offer_code) if offer_code else None

                evaluation = evaluate_row(
                    row=row,
                    offer_cpa=(Decimal(offer_data["offer"].cpa_amount) if offer_data else None),
                    rule_config=(offer_data.get("rule_config") if offer_data else None),
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

                # Сохраняем снэпшот в БД
                try:
                    await save_snapshot_to_db(
                        row, evaluation, next_state, token, offer_code, offers
                    )
                except Exception:
                    logger.warning(
                        "Не удалось сохранить снэпшот %s",
                        row.fb_ad_id,
                        exc_info=True,
                    )

                # Собираем алерты для отправки
                if should_emit and evaluation.stage is not None:
                    codes = (
                        evaluation.stop_rule_codes
                        if evaluation.stage == AlertStage.STOP
                        else evaluation.warning_rule_codes
                    )
                    alerts_to_send.append(
                        AlertCandidate(
                            snapshot_id=token or uuid.uuid4().hex,
                            fb_ad_id=row.fb_ad_id,
                            ad_name=row.ad_name,
                            offer_code=offer_code,
                            stage=evaluation.stage,
                            matched_rule_codes=codes,
                            metrics_json=build_metrics_json(row),
                        )
                    )

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
    max_scroll_passes = 50  # Защита от бесконечного цикла
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
                    AlertState.STOP_SENT if a.stage == AlertStage.STOP else AlertState.WARNING_SENT
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
            logger.info(
                "Отправлено TG-сообщение: %s алертов, стадия %s",
                len(items),
                stage,
            )
        except Exception:
            logger.exception("Не удалось отправить TG-сообщение")
